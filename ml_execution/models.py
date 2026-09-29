"""Independent fitted preprocessing/models. Libraries are imported only on execution."""
from pathlib import Path
from .contracts import NotReady

SEED = 20260926


def candidates(kind, engine="python", seed=SEED):
    if kind == "classification":
        return [
            {"model_name": "logistic_regression", "model_type": "classification", "parameters": {"max_iter": 300}, "random_seed": seed},
            {"model_name": "decision_tree", "model_type": "classification", "parameters": {"max_depth": 6}, "random_seed": seed},
            {"model_name": "random_forest", "model_type": "classification", "parameters": {"n_estimators": 80, "max_depth": 8}, "random_seed": seed},
        ]
    if kind == "regression":
        return [
            {"model_name": "linear_regression", "model_type": "regression", "parameters": {"regularization": 1.0}, "random_seed": None},
            {"model_name": "decision_tree", "model_type": "regression", "parameters": {"max_depth": 6}, "random_seed": seed},
            {"model_name": "random_forest", "model_type": "regression", "parameters": {"n_estimators": 80, "max_depth": 8}, "random_seed": seed},
        ]
    if kind == "clustering":
        return [{"model_name": f"kmeans_k{k}", "model_type": "clustering",
                 "parameters": {"k": k, "max_iter": 100}, "random_seed": seed} for k in (2, 3, 4)]
    raise ValueError(f"No model candidates for {kind}")


class PythonModel:
    def __init__(self, spec, kind):
        try:
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler
            from sklearn.linear_model import LogisticRegression, Ridge
            from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
            from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
            from sklearn.cluster import KMeans
        except ImportError as exc:
            raise NotReady("Python execution requires NumPy and scikit-learn; dependencies were not installed") from exc
        name, p, seed = spec["model_name"], spec["parameters"], spec["random_seed"]
        if kind == "clustering":
            estimator = KMeans(n_clusters=p["k"], max_iter=p["max_iter"], n_init=10, random_state=seed)
        elif name == "logistic_regression":
            estimator = LogisticRegression(max_iter=p["max_iter"], random_state=seed)
        elif name == "linear_regression":
            estimator = Ridge(alpha=p["regularization"])
        elif name == "decision_tree":
            cls = DecisionTreeClassifier if kind == "classification" else DecisionTreeRegressor
            estimator = cls(max_depth=p["max_depth"], random_state=seed)
        else:
            cls = RandomForestClassifier if kind == "classification" else RandomForestRegressor
            estimator = cls(n_estimators=p["n_estimators"], max_depth=p["max_depth"], random_state=seed, n_jobs=1)
        self.model = Pipeline([("scale", StandardScaler()), ("estimator", estimator)])
        self.kind = kind

    def fit(self, x, y):
        self.model.fit(x, y)
        return self

    def predict(self, x):
        return self.model.predict(x).tolist()

    def probabilities(self, x):
        return self.model.predict_proba(x).tolist()

    def scaled(self, x):
        return self.model.named_steps["scale"].transform(x).tolist()

    def parameters(self):
        # Primitive settings, including library defaults, are saved with library versions.
        return {key: value for key, value in self.model.get_params(deep=True).items()
                if value is None or isinstance(value, (str, int, float, bool))}

    def save(self, path):
        import pickle
        with open(path, "wb") as stream:
            pickle.dump(self.model, stream)


class SparkModel:
    """Spark fits its own scaler/model; it never consumes a Python fitted artifact.

    Input is the bounded certified task matrix, not raw production ingestion.
    """
    def __init__(self, spec, kind, spark):
        try:
            from pyspark.ml import Pipeline
            from pyspark.ml.feature import StandardScaler
            from pyspark.ml.classification import LogisticRegression, DecisionTreeClassifier, RandomForestClassifier
            from pyspark.ml.regression import LinearRegression, DecisionTreeRegressor, RandomForestRegressor
            from pyspark.ml.clustering import KMeans
        except ImportError as exc:
            raise NotReady("Spark execution requires PySpark and a configured Java/Spark runtime") from exc
        name, p, seed = spec["model_name"], spec["parameters"], spec["random_seed"]
        if kind == "clustering":
            estimator = KMeans(k=p["k"], maxIter=p["max_iter"], seed=seed, featuresCol="features")
        elif name == "logistic_regression":
            estimator = LogisticRegression(maxIter=p["max_iter"])
        elif name == "linear_regression":
            estimator = LinearRegression(regParam=p["regularization"], elasticNetParam=0.0)
        elif name == "decision_tree":
            cls = DecisionTreeClassifier if kind == "classification" else DecisionTreeRegressor
            estimator = cls(maxDepth=p["max_depth"], seed=seed)
        else:
            cls = RandomForestClassifier if kind == "classification" else RandomForestRegressor
            estimator = cls(numTrees=p["n_estimators"], maxDepth=p["max_depth"], seed=seed)
        self.pipeline = Pipeline(stages=[StandardScaler(inputCol="raw_features", outputCol="features",
                                                        withMean=True, withStd=True), estimator])
        self.spark, self.kind, self.model = spark, kind, None

    def frame(self, x, y=None):
        from pyspark.ml.linalg import Vectors
        return self.spark.createDataFrame([(i, Vectors.dense(v), float(y[i]) if y is not None else 0.0)
                                           for i, v in enumerate(x)], ["ordinal", "raw_features", "label"])

    def fit(self, x, y):
        self.model = self.pipeline.fit(self.frame(x, y))
        return self

    def predict(self, x):
        return [float(r.prediction) for r in self.model.transform(self.frame(x)).orderBy("ordinal").select("prediction").collect()]

    def probabilities(self, x):
        return [r.probability.toArray().tolist() for r in self.model.transform(self.frame(x)).orderBy("ordinal").select("probability").collect()]

    def scaled(self, x):
        return [r.features.toArray().tolist() for r in self.model.stages[0].transform(self.frame(x)).orderBy("ordinal").select("features").collect()]

    def parameters(self):
        return {f"stage{i}.{p.name}": v for i, stage in enumerate(self.model.stages)
                for p, v in stage.extractParamMap().items() if v is None or isinstance(v, (str, int, float, bool))}

    def save(self, path):
        self.model.write().save(Path(path).resolve().as_uri())
