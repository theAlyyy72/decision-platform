"""The AWS Lambda entry point: pull state from S3, run the pipeline, push the results.

Environment: LAKE_BUCKET (required), AS_OF (optional, YYYY-MM-DD; an event can also carry "as_of").
Results are pushed even when the pipeline stops, so the validation report and the
quarantined rows are never lost. The error is raised afterwards, which makes the Lambda
run count as failed (that is what the alarm watches).
"""
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

from dp.cloud import pull_state, push_results
from dp.pipeline import run_pipeline

WORK_DIR = "/tmp/work"


def run_job(s3, bucket, as_of, work_dir=WORK_DIR, pipeline=run_pipeline, ingest_date=None, sql_dir="sql"):
    work = Path(work_dir)
    shutil.rmtree(work, ignore_errors=True)  # a warm Lambda may still hold files from the last run
    work.mkdir(parents=True)
    pulled = pull_state(s3, bucket, work)
    try:
        summary = pipeline(work, as_of, ingest_date, sql_dir)
    finally:
        push_results(s3, bucket, work, pulled)
    return summary


def lambda_handler(event, context):
    import boto3

    event = event or {}
    as_of = event.get("as_of") or os.environ.get("AS_OF") or (date.today() - timedelta(days=1)).isoformat()
    return run_job(boto3.client("s3"), os.environ["LAKE_BUCKET"], as_of,
                   sql_dir=os.environ.get("SQL_DIR", "sql"))
