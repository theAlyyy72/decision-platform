"""Build the dashboard page (dashboard/index.html) from the curated tables.

The page is one self-contained HTML file with the numbers embedded, so it opens anywhere
(double click, GitHub Pages). It holds no names or emails, only the curated tables.
Run after dp.curate and dp.predict:  python3 -m dp.dashboard
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SERIES = ["period_start", "orders", "net_revenue", "avg_order_value", "active_customers", "new_customers",
          "repeat_customers", "cancellation_rate", "return_rate", "on_time_delivery_rate", "gross_margin_pct"]


def rows(df, cols):
    df = df[cols].copy()
    for c in cols:
        if "date" in c or "month" in c or c == "period_start":
            df[c] = df[c].astype(str).str[:10]
    return json.loads(df.to_json(orient="records"))


def build_data(t, report, metrics, as_of):
    rev = t["revenue_kpis"].sort_values("period_start")
    series = {g: rows(rev[rev["grain"] == g].tail(n), SERIES) for g, n in (("day", 60), ("week", 26), ("month", 99))}
    cs = t["churn_scores"]
    risk_cols = ["customer_id", "churn_probability", "recency_days", "orders_total", "spend_total"]
    return {
        "as_of": as_of, "generated": datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "series": series,
        "categories": rows(t["top_categories"], ["category", "units_sold", "revenue", "revenue_share"]),
        "products": rows(t["top_products"].sort_values("revenue_rank").head(8),
                         ["product_name", "category", "units_sold", "revenue"]),
        "retention": rows(t["retention"], ["cohort_month", "cohort_size", "retention_30d", "retention_60d", "retention_90d"]),
        "customer_kpis": json.loads(t["customer_kpis"].iloc[0].to_json()),
        "rfm": rows(t["rfm"].groupby("segment").agg(customers=("customer_id", "size"), avg_spend=("monetary", "mean"))
                    .round(0).reset_index().sort_values("customers", ascending=False), ["segment", "customers", "avg_spend"]),
        "bands": rows(cs.groupby("risk_band").agg(customers=("customer_id", "size"), avg_prob=("churn_probability", "mean"),
                                                    avg_recency=("recency_days", "mean")).round(3).reset_index(),
                      ["risk_band", "customers", "avg_prob", "avg_recency"]),
        "top_risk": rows(cs.sort_values("risk_rank").head(10), risk_cols),
        "model": {"name": metrics["best_model"], "test": metrics["test"]},
        "quality": [{"table": k, "rows": v["rows_in"], "quarantined": v["quarantined"], "pct": v["quarantine_pct"],
                     "status": v["status"], "rules": [[r, c["rows"], c["severity"]] for r, c in v["rules"].items() if c["rows"]]}
                    for k, v in report["tables"].items()],
        "run": {"status": report["status"], "at": report["run_at"]},
    }


def render(data, template, out):
    page = Path(template).read_text().replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(page)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--models-dir", default="data/models")
    ap.add_argument("--as-of", default="2026-10-31")
    ap.add_argument("--template", default="dashboard/template.html")
    ap.add_argument("--out", default="dashboard/index.html")
    a = ap.parse_args()
    lake = Path(a.lake)
    t = {p.parent.name: pd.read_parquet(p) for p in (lake / "curated").glob("*/*.parquet")}
    report = json.loads(sorted((lake / "reports").glob("validation_*.json"))[-1].read_text())
    metrics = json.loads((Path(a.models_dir) / "metrics.json").read_text())
    render(build_data(t, report, metrics, a.as_of), a.template, a.out)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
