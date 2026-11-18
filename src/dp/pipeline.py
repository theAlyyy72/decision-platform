"""Run the whole pipeline once, in order, inside a work folder.

    ingest -> validate -> clean -> curate -> features -> predict

work_dir/source/   the new batch (CSV)
work_dir/lake/     the data lake (raw, validated, quarantine, clean, curated, ml, reports)
work_dir/models/   churn_model.joblib

Stops with PipelineError when ingestion fails or a table has too many bad rows (the
validation step decides that). Returns a summary dict; summary["status"] is
  ok           everything ran
  warn         everything ran, but validation had warnings (for example bad emails)
  no_new_data  every source file was already ingested, so nothing was run

The steps are passed in as functions, so the order and the stop rules can be tested
without any data. `default_steps()` wires in the real ones.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path


class PipelineError(Exception):
    pass


def default_steps():
    """The real step functions. Imported here so this module loads without pandas or duckdb."""
    import pandas as pd

    from dp.clean import clean
    from dp.curate import curate
    from dp.features import build_dataset, load_orders
    from dp.ingest import ingest
    from dp.predict import predict
    from dp.validate import validate

    def features(lake, as_of):
        lake = Path(lake)
        orders = load_orders(lake, as_of)
        customers = pd.read_parquet(lake / "clean" / "customers" / "customers.parquet")
        labelled, scoring = build_dataset(orders, customers, as_of)
        out = lake / "ml"
        out.mkdir(parents=True, exist_ok=True)
        labelled.to_parquet(out / "churn_dataset.parquet", index=False)
        scoring.to_parquet(out / "churn_scoring.parquet", index=False)
        return {"labelled_rows": len(labelled), "scoring_rows": len(scoring)}

    return {"ingest": ingest, "validate": validate, "clean": clean, "curate": curate,
            "features": features, "predict": predict}


def run_pipeline(work_dir, as_of, ingest_date=None, sql_dir="sql", steps=None):
    steps = steps or default_steps()
    work = Path(work_dir)
    source, lake, models = work / "source", work / "lake", work / "models"
    ingest_date = ingest_date or date.today().isoformat()
    summary = {"as_of": as_of, "ingest_date": ingest_date}

    results = steps["ingest"](source, lake, ingest_date)
    summary["ingest"] = {r["table"]: r["status"] for r in results}
    failed = {t: s for t, s in summary["ingest"].items() if s.startswith("failed")}
    if failed:
        raise PipelineError(f"ingestion failed: {failed}")
    if all(s == "skipped_duplicate" for s in summary["ingest"].values()):
        summary["status"] = "no_new_data"
        return summary

    report = steps["validate"](lake, as_of=as_of)
    summary["validation"] = {
        "status": report["status"],
        "quarantine_pct": {t: v["quarantine_pct"] for t, v in report["tables"].items()},
        "report_file": report.get("report_file"),
    }
    if report["status"] == "fail":
        bad = {t: v["quarantine_pct"] for t, v in report["tables"].items() if v["status"] == "fail"}
        raise PipelineError(f"validation failed, too many rows quarantined: {bad}")

    steps["clean"](lake)
    summary["curated_rows"] = steps["curate"](lake, sql_dir, as_of)
    summary["features"] = steps["features"](lake, as_of)
    scores = steps["predict"](lake, models)
    summary["scored_customers"] = len(scores)
    summary["status"] = "warn" if report["status"] == "warn" else "ok"
    return summary


def main():
    """Run the pipeline on a local work folder (a dry run of what the AWS job does).

    mkdir -p /tmp/dp-work && cp -R data/source /tmp/dp-work/source && cp -R data/models /tmp/dp-work/models
    python3 -m dp.pipeline --work /tmp/dp-work --as-of 2026-10-31
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--as-of", required=True, help="YYYY-MM-DD, end of the data period")
    ap.add_argument("--ingest-date", default=None)
    ap.add_argument("--sql", default="sql")
    args = ap.parse_args()
    try:
        summary = run_pipeline(args.work, args.as_of, args.ingest_date, args.sql)
    except PipelineError as e:
        print(f"PIPELINE STOPPED: {e}")
        sys.exit(1)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
