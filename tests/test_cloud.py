import json

import pytest

from dp.cloud import list_keys, pull_state, push_results
from dp.handler import run_job
from dp.pipeline import PipelineError, run_pipeline


class FakeS3:
    """In-memory stand-in for boto3's S3 client. Lists two keys per page to test pagination."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.uploads = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]
        out = {"Contents": [{"Key": k} for k in page], "IsTruncated": start + 2 < len(keys)}
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(start + 2)
        return out

    def download_file(self, Bucket, Key, Filename):
        with open(Filename, "wb") as f:
            f.write(self.objects[Key])

    def upload_file(self, Filename, Bucket, Key):
        with open(Filename, "rb") as f:
            self.objects[Key] = f.read()
        self.uploads.append(Key)


def lake_with_history():
    return FakeS3({
        "_ingestion_log.jsonl": b"{}\n",
        "source/customers.csv": b"new customers",
        "source/_manifest.json": b"{}",
        "raw/customers/ingest_date=2026-11-10/customers_20261110T010000Z.csv": b"old",
        "raw/customers/ingest_date=2026-11-11/customers_20261111T010000Z.csv": b"latest",
        "raw/products/ingest_date=2026-11-10/products_20261110T010000Z.csv": b"products",
        "models/churn_model.joblib": b"model",
        "curated/rfm/rfm.parquet": b"not needed to run",
    })


def test_list_keys_follows_every_page():
    s3 = FakeS3({f"raw/x/{i}.csv": b"" for i in range(5)})
    assert len(list_keys(s3, "b", "raw/x/")) == 5


def test_pull_state_takes_only_what_one_run_needs(tmp_path):
    s3 = lake_with_history()
    pulled = pull_state(s3, "bucket", tmp_path)
    assert (tmp_path / "source" / "customers.csv").read_bytes() == b"new customers"
    assert not (tmp_path / "source" / "_manifest.json").exists()           # only CSV files
    latest = tmp_path / "lake/raw/customers/ingest_date=2026-11-11/customers_20261111T010000Z.csv"
    assert latest.read_bytes() == b"latest"
    assert not (tmp_path / "lake/raw/customers/ingest_date=2026-11-10").exists()  # older file is not needed
    assert (tmp_path / "lake/raw/products/ingest_date=2026-11-10/products_20261110T010000Z.csv").exists()
    assert (tmp_path / "lake/_ingestion_log.jsonl").exists()
    assert (tmp_path / "models/churn_model.joblib").read_bytes() == b"model"
    assert not (tmp_path / "lake/curated").exists()
    assert "curated/rfm/rfm.parquet" not in pulled


def test_pull_state_works_on_an_empty_bucket(tmp_path):
    assert pull_state(FakeS3(), "bucket", tmp_path) == []


def test_push_results_uploads_new_files_and_the_log_but_not_what_was_only_pulled(tmp_path):
    s3 = lake_with_history()
    pulled = pull_state(s3, "bucket", tmp_path)
    (tmp_path / "lake/clean/orders").mkdir(parents=True)
    (tmp_path / "lake/clean/orders/orders.parquet").write_bytes(b"clean")
    (tmp_path / "lake/_ingestion_log.jsonl").write_bytes(b"{}\n{}\n")
    s3.uploads.clear()
    uploaded = push_results(s3, "bucket", tmp_path, pulled)
    assert sorted(uploaded) == ["_ingestion_log.jsonl", "clean/orders/orders.parquet"]
    assert s3.objects["clean/orders/orders.parquet"] == b"clean"
    assert s3.objects["_ingestion_log.jsonl"] == b"{}\n{}\n"


def fake_steps(calls, ingest_status="ingested", validation="pass"):
    tables = ["customers", "products", "orders", "order_items", "payments"]
    quarantine = {t: 0.5 for t in tables}

    def step(name, result=None):
        def run(*args, **kwargs):
            calls.append(name)
            return result
        return run

    report = {"status": validation, "report_file": "reports/v.json",
              "tables": {t: {"quarantine_pct": 0.5, "status": validation} for t in tables}}
    return {
        "ingest": step("ingest", [{"table": t, "status": ingest_status} for t in tables]),
        "validate": step("validate", report),
        "clean": step("clean"),
        "curate": step("curate", {"revenue_kpis": 10}),
        "features": step("features", {"scoring_rows": 3}),
        "predict": step("predict", [1, 2, 3]),
    }


def test_pipeline_runs_every_step_in_order(tmp_path):
    calls = []
    summary = run_pipeline(tmp_path, "2026-10-31", "2026-11-11", steps=fake_steps(calls))
    assert calls == ["ingest", "validate", "clean", "curate", "features", "predict"]
    assert summary["status"] == "ok" and summary["scored_customers"] == 3
    assert summary["curated_rows"] == {"revenue_kpis": 10}


def test_validation_warnings_give_a_warn_status(tmp_path):
    summary = run_pipeline(tmp_path, "2026-10-31", steps=fake_steps([], validation="warn"))
    assert summary["status"] == "warn"


def test_a_failed_ingest_stops_everything(tmp_path):
    calls = []
    with pytest.raises(PipelineError) as e:
        run_pipeline(tmp_path, "2026-10-31", steps=fake_steps(calls, ingest_status="failed_schema"))
    assert "ingestion failed" in str(e.value) and calls == ["ingest"]


def test_too_many_bad_rows_stop_the_pipeline_before_clean(tmp_path):
    calls = []
    with pytest.raises(PipelineError) as e:
        run_pipeline(tmp_path, "2026-10-31", steps=fake_steps(calls, validation="fail"))
    assert "validation failed" in str(e.value) and calls == ["ingest", "validate"]


def test_a_batch_that_was_already_ingested_runs_nothing_else(tmp_path):
    calls = []
    summary = run_pipeline(tmp_path, "2026-10-31", steps=fake_steps(calls, ingest_status="skipped_duplicate"))
    assert summary["status"] == "no_new_data" and calls == ["ingest"]


def test_job_pulls_runs_and_pushes(tmp_path):
    s3 = lake_with_history()

    def pipeline(work, as_of, ingest_date, sql_dir):
        assert (work / "source" / "customers.csv").exists()   # state was pulled first
        (work / "lake/clean/customers").mkdir(parents=True)
        (work / "lake/clean/customers/customers.parquet").write_bytes(b"c")
        return {"status": "ok"}

    assert run_job(s3, "bucket", "2026-10-31", tmp_path / "w", pipeline) == {"status": "ok"}
    assert s3.objects["clean/customers/customers.parquet"] == b"c"


def test_job_still_pushes_the_reports_when_the_pipeline_fails(tmp_path):
    s3 = lake_with_history()

    def pipeline(work, as_of, ingest_date, sql_dir):
        (work / "lake/reports").mkdir(parents=True)
        (work / "lake/reports/validation.json").write_text(json.dumps({"status": "fail"}))
        raise PipelineError("validation failed")

    with pytest.raises(PipelineError):
        run_job(s3, "bucket", "2026-10-31", tmp_path / "w", pipeline)
    assert "reports/validation.json" in s3.objects  # the evidence is kept
