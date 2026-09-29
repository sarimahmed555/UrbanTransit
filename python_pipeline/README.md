# Independent Python/Pandas pipeline foundation

This package is the independent Python path required by the SRS. It reads
immutable raw CSV/JSONL source tables in bounded Pandas chunks and maintains
its own staging, DQ, joins, features, split, and evidence contracts. It does
not import Spark code or consume Spark-cleaned data, fitted models,
predictions, or derived analytical outputs.

`PipelinePaths` represents the approved future production layout, while raw
discovery refuses to inspect `raw_data/production-v1` during preparation.
`iter_table()` is the only intended table reader: callers choose `chunksize`,
columns, and explicit dtypes. Future production execution must iterate and
reduce each chunk rather than concatenate all rows into RAM.

The code is **IMPLEMENTED IN CODE**, not **EXECUTED/VALIDATED ON PRODUCTION
DATA**. Production execution is blocked until the separately running physical
validation certifies the dataset.

The feature layer is in `python_pipeline.features` and is independent from
Spark. `build_feature_frames_from_tables()` creates task outputs from accepted
table DataFrames; the Pandas transformations are small-frame compatible but
the full certified-data runtime, bounded-memory execution strategy, output
materialization, and its evidence remain unverified. See
`documentation/FEATURE_ENGINEERING_CONTRACT.md` for source grains, output
labels, leakage rules, and split dates.
