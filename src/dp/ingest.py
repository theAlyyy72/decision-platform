"""Ingest source CSV files into the raw zone of the data lake.

Raw zone rules:
- files are copied byte for byte and never edited
- the folder layout mirrors S3: raw/<table>/ingest_date=YYYY-MM-DD/<table>_<batch_id>.csv
- every file is logged with a checksum, so running the same batch twice does nothing
- a file whose columns do not match the expected schema is rejected before anything is written

Run: python3 -m dp.ingest --source data/source --lake data/lake
"""
import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dp.schemas import EXPECTED_COLUMNS

LOG_NAME = "_ingestion_log.jsonl"


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path):
    with open(path, newline="") as f:
        return next(csv.reader(f), [])


def count_rows(path):
    with open(path, newline="") as f:
        return max(sum(1 for _ in csv.reader(f)) - 1, 0)  # minus the header


def already_ingested(log_path, table, checksum):
    if not log_path.exists():
        return False
    with open(log_path) as f:
        for line in f:
            rec = json.loads(line)
            if rec["table"] == table and rec.get("sha256") == checksum and rec["status"] == "ingested":
                return True
    return False


def write_log(log_path, record):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def ingest(source_dir, lake_dir, ingest_date, batch_id=None):
    """Ingest every expected table. Returns one result record per table."""
    source_dir, lake_dir = Path(source_dir), Path(lake_dir)
    batch_id = batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = lake_dir / LOG_NAME
    results = []

    for table, expected in EXPECTED_COLUMNS.items():
        src = source_dir / f"{table}.csv"
        rec = {"batch_id": batch_id, "table": table, "source_file": str(src),
               "ingest_date": ingest_date,
               "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

        if not src.exists():
            rec["status"] = "failed_missing_file"
        elif src.stat().st_size == 0:
            rec["status"] = "failed_empty_file"
        elif read_header(src) != expected:
            header = read_header(src)
            rec["status"] = "failed_schema"
            rec["missing_columns"] = [c for c in expected if c not in header]
            rec["unexpected_columns"] = [c for c in header if c not in expected]
        else:
            checksum = sha256_of(src)
            rec["sha256"] = checksum
            if already_ingested(log_path, table, checksum):
                rec["status"] = "skipped_duplicate"
            else:
                dest = (lake_dir / "raw" / table / f"ingest_date={ingest_date}"
                        / f"{table}_{batch_id}.csv")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)  # on AWS this becomes an S3 upload
                if sha256_of(dest) != checksum:
                    dest.unlink()
                    rec["status"] = "failed_copy_check"
                else:
                    rec.update(status="ingested", rows=count_rows(dest),
                               bytes=dest.stat().st_size, raw_path=str(dest))

        write_log(log_path, rec)
        results.append(rec)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/source")
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--ingest-date", default=date.today().isoformat())
    args = ap.parse_args()

    results = ingest(args.source, args.lake, args.ingest_date)
    for r in results:
        extra = f"{r['rows']:,} rows, {r['bytes'] / 1024:,.0f} KB" if r["status"] == "ingested" else ""
        print(f"{r['table']:<12} {r['status']:<20} {extra}")
    failed = [r for r in results if r["status"].startswith("failed")]
    if failed:
        print(f"\n{len(failed)} table(s) failed. See {Path(args.lake) / LOG_NAME}")
        sys.exit(1)


if __name__ == "__main__":
    main()