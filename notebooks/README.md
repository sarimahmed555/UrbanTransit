# Reproducible notebook evidence

- `01_eda_feature_engineering.ipynb`: current feature contracts, bounded TRAIN Parquet sample EDA, DQ evidence and leakage handoff review.
- `02_python_ml_evaluation.ipynb`: certified independent Python handoff validation, disabled-by-default ML runner, genuine result inspection and dual-pipeline readiness.

Status: **PREPARED — PENDING CERTIFIED RUNTIME EVIDENCE**. All stored outputs are empty.
The authoritative checkout is `/home/manal/Desktop/UrbanTransit-IQ`; change `REPO` only for a deliberate relocation.
Open with Jupyter and a Python 3 kernel. EDA reading additionally needs Pandas/PyArrow; plots need Matplotlib.
Later Python fitting needs NumPy/scikit-learn. Follow `documentation/INSTALLATION_AND_RUNTIME.md` for the environment.
No packages were installed, no training was run, and no raw/HDFS scan is required by these notebooks.

Keep all read/training switches off until the feature owner supplies completed paths.
Supply exact local processed artifacts; never point at an active run or the raw dataset.
The EDA prefix sample is not representative. The ML package is independently certified and bounded;
neither constitutes production-scale performance evidence. Do not truncate a certified package to fit limits.
After an approved execution, save genuine cell outputs and record hashes/run IDs in the evidence manifest.
