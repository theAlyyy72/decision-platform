"""Move the data lake between S3 and a local work folder.

The AWS job is stateless: every run starts with an empty work folder, pulls only what it
needs, runs the same pipeline code we run on the laptop, and pushes the results back.

S3 key                          <->  work folder
  source/<table>.csv                 source/<table>.csv         the new batch
  _ingestion_log.jsonl               lake/_ingestion_log.jsonl  so a repeated batch is skipped
  raw/<table>/.../<file>.csv         lake/raw/...               only the LATEST file of each table
  models/churn_model.joblib          models/churn_model.joblib  the trained model
  raw|validated|quarantine|clean|    lake/...                   everything the run produced
  curated|ml|reports/...

Only three boto3 S3 calls are used (list_objects_v2, download_file, upload_file), so the
tests can use a small fake instead of AWS.
"""
from pathlib import Path

from dp.schemas import EXPECTED_COLUMNS

LOG_KEY = "_ingestion_log.jsonl"
MODEL_KEY = "models/churn_model.joblib"


def list_keys(s3, bucket, prefix):
    """Every key under a prefix, following pagination."""
    keys, token = [], None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        keys += [o["Key"] for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            return keys
        token = page["NextContinuationToken"]


def download(s3, bucket, key, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(dest))


def pull_state(s3, bucket, work_dir):
    """Download what one run needs. Returns the S3 keys that were pulled."""
    work = Path(work_dir)
    lake = work / "lake"
    pulled = []

    def get(key, dest):
        download(s3, bucket, key, dest)
        pulled.append(key)

    if LOG_KEY in list_keys(s3, bucket, LOG_KEY):
        get(LOG_KEY, lake / LOG_KEY)
    for key in list_keys(s3, bucket, "source/"):
        if key.endswith(".csv"):
            get(key, work / key)
    for table in EXPECTED_COLUMNS:
        files = [k for k in list_keys(s3, bucket, f"raw/{table}/") if k.endswith(".csv")]
        if files:
            latest = max(files)  # ingest_date and batch id both sort in time order
            get(latest, lake / latest)
    if MODEL_KEY in list_keys(s3, bucket, MODEL_KEY):
        get(MODEL_KEY, work / MODEL_KEY)
    return pulled


def push_results(s3, bucket, work_dir, pulled=()):
    """Upload everything under lake/. Files that were only pulled (not changed) are skipped,
    except the ingestion log, which every run appends to. Returns the keys uploaded."""
    lake = Path(work_dir) / "lake"
    skip = set(pulled) - {LOG_KEY}
    uploaded = []
    for path in sorted(p for p in lake.rglob("*") if p.is_file()):
        key = path.relative_to(lake).as_posix()
        if key in skip:
            continue
        s3.upload_file(str(path), bucket, key)
        uploaded.append(key)
    return uploaded
