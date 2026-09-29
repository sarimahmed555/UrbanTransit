"""Independent Spark analytics entry point; it writes only analytics outputs."""

import argparse
import uuid

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from spark_jobs.output_safety import reserve_spark_output, versioned_spark_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    args.run_id = args.run_id or str(uuid.uuid4())
    output = versioned_spark_path(
        args.output,
        stage="spark_analytics",
        dataset_version=args.dataset_version,
        run_id=args.run_id,
    )
    spark = SparkSession.builder.appName("urbantransit-spark-analytics").getOrCreate()
    try:
        reserve_spark_output(
            spark,
            output,
            stage="spark_analytics",
            run_id=args.run_id,
            dataset_version=args.dataset_version,
        )
        features = spark.read.parquet(args.features)
        result = features.groupBy("service_date", "route_code").agg(
            F.countDistinct("trip_id").alias("trip_count"),
            F.avg("departure_delay_sec").alias("mean_departure_delay_sec"),
            F.max("onboard_departure").alias("peak_onboard"),
        )
        result.write.mode("errorifexists").partitionBy("service_date").parquet(output)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
