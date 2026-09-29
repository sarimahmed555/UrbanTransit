# Runtime evidence framework

This package defines the machine-readable evidence boundary for later
certified execution. It does not read production data, launch HDFS/Spark,
train models, or calculate analytics.

`EvidenceRecorder` creates a JSON bundle with stable categories for dataset
certification, HDFS ingestion, Spark and Python pipelines, ML, forecasting,
classification, clustering, occupancy/crowding, delay analytics,
Python-vs-Spark comparison, performance, and API/dashboard readiness.

Every category starts as `NOT_RUN`; missing evidence is never interpreted as a
pass. Records preserve timestamp, command, dataset version, input artifact,
output artifact, status, and category-specific fields. `evaluate_thresholds`
returns `NOT_RUN` when a runtime record is absent or not ready, and only
evaluates numeric thresholds when a ready record supplies the observed value.

The intended later flow is:

1. A certified runner creates an `EvidenceRecorder` with its command and
   artifact provenance.
2. The runner records each completed stage and writes the JSON bundle.
3. CI or a certification gate loads the bundle and calls the threshold
   helpers.
4. The backend/API artifact adapter exposes only bundles/results whose
   readiness has been explicitly established.
