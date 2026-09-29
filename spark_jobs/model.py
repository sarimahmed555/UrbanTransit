"""Independent Spark ML entry point; no Python/Pandas model or predictions are read."""

import argparse
import uuid

from pyspark.ml import Pipeline
from pyspark.ml.feature import OneHotEncoder, StringIndexer, VectorAssembler
from pyspark.ml.regression import RandomForestRegressor
from pyspark.sql import SparkSession
from spark_jobs.output_safety import reserve_spark_output, versioned_spark_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--split", choices=("train", "validation", "test"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    args.run_id = args.run_id or str(uuid.uuid4())
    output = versioned_spark_path(
        args.output,
        stage=f"spark_model_{args.split}",
        dataset_version=args.dataset_version,
        run_id=args.run_id,
    )
    spark = SparkSession.builder.appName("urbantransit-spark-model").getOrCreate()
    try:
        reserve_spark_output(
            spark,
            output,
            stage=f"spark_model_{args.split}",
            run_id=args.run_id,
            dataset_version=args.dataset_version,
        )
        frame = spark.read.parquet(f"{args.features}/split={args.split}")
        indexed = StringIndexer(inputCol="route_code", outputCol="route_index",
                                handleInvalid="keep")
        encoded = OneHotEncoder(inputCol="route_index", outputCol="route_vector")
        assembled = VectorAssembler(
            inputCols=["route_vector", "event_hour", "weekday"], outputCol="features",
            handleInvalid="keep"
        )
        estimator = RandomForestRegressor(
            featuresCol="features", labelCol="departure_delay_sec",
            predictionCol="spark_prediction", seed=20260924
        )
        Pipeline(stages=[indexed, encoded, assembled, estimator]).fit(frame).transform(frame).select(
            "trip_id", "service_date", "spark_prediction"
        ).write.mode("errorifexists").partitionBy("service_date").parquet(output)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
