"""Build the churn dataset: one row per customer per monthly snapshot date.

Churn (see docs/kpi_definitions.md): a customer is churned at snapshot date t if they place
no non-cancelled order in the 90 days from t (t included, t + 90 days excluded).

No leakage: features for snapshot t use only orders placed BEFORE t. The label uses only
orders from t onwards. A snapshot is labelled only if its 90-day window ends inside the data.

Who is in a snapshot: customers who signed up on or before t and have at least one
non-cancelled order before t. (Customers whose row was quarantined are not included.)

Output (data/lake/ml/):
    churn_dataset.parquet   labelled rows, many snapshots (training, validation and test)
    churn_scoring.parquet   unlabelled rows at the end of the data (what we predict on)

Run: python3 -m dp.features --lake data/lake --as-of 2026-10-31
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

HORIZON_DAYS = 90
FIRST_SNAPSHOT = "2025-08-01"  # about 3 months after the first order, so customers have history

NUMERIC_FEATURES = [
    "recency_days", "tenure_days", "orders_total", "orders_last_30d", "orders_last_90d",
    "avg_gap_days", "spend_total", "spend_last_90d", "spend_per_order",
    "cancel_rate", "return_rate", "late_delivery_rate", "marketing_opt_in",
]
CATEGORICAL_FEATURES = ["city", "acquisition_channel"]


def load_orders(lake_dir, as_of):
    """Clean orders with the amount actually paid, cut off at the end of the as-of day."""
    lake = Path(lake_dir)
    orders = pd.read_parquet(lake / "clean" / "orders" / "orders.parquet")
    pay = pd.read_parquet(lake / "clean" / "payments" / "payments.parquet")
    paid = pay[pay["payment_status"] == "paid"].groupby("order_id")["amount"].sum()
    orders["paid_amount"] = orders["order_id"].map(paid).fillna(0.0)
    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    return orders[orders["order_ts"] < cutoff].reset_index(drop=True)


def build_features(orders, customers, snapshot):
    """Features as of `snapshot`, using only orders placed before it."""
    t = pd.Timestamp(snapshot)
    past = orders[orders["order_ts"] < t]

    delivered = past["status"].eq("delivered")
    late = delivered & (past["delivery_days"].astype("float64") > past["promised_days"].astype("float64"))
    flags = pd.DataFrame({
        "customer_id": past["customer_id"],
        "cancelled": past["status"].eq("cancelled"),
        "returned": past["status"].eq("returned"),
        "delivered": delivered,
        "late": late,
    })
    n_all = flags.groupby("customer_id").size()
    fl = flags.groupby("customer_id").sum()

    live = past[past["status"] != "cancelled"]
    g = live.groupby("customer_id")
    f = pd.DataFrame({
        "orders_total": g.size(),
        "first_ts": g["order_ts"].min(),
        "last_ts": g["order_ts"].max(),
        "spend_total": g["paid_amount"].sum(),
    })
    for days in (30, 90):
        recent = live[live["order_ts"] >= t - pd.Timedelta(days=days)]
        f[f"orders_last_{days}d"] = recent.groupby("customer_id").size().reindex(f.index).fillna(0).astype(int)
    recent90 = live[live["order_ts"] >= t - pd.Timedelta(days=90)]
    f["spend_last_90d"] = recent90.groupby("customer_id")["paid_amount"].sum().reindex(f.index).fillna(0.0)

    f["recency_days"] = (t - f["last_ts"]).dt.days
    f["avg_gap_days"] = ((f["last_ts"] - f["first_ts"]).dt.days / (f["orders_total"] - 1)).where(f["orders_total"] > 1)
    f["spend_per_order"] = f["spend_total"] / f["orders_total"]
    f["cancel_rate"] = fl["cancelled"].reindex(f.index) / n_all.reindex(f.index)
    kept = fl["delivered"].reindex(f.index) + fl["returned"].reindex(f.index)
    f["return_rate"] = (fl["returned"].reindex(f.index) / kept).where(kept > 0)
    d = fl["delivered"].reindex(f.index)
    f["late_delivery_rate"] = (fl["late"].reindex(f.index) / d).where(d > 0)

    cust = customers[pd.to_datetime(customers["signup_date"]) <= t].set_index("customer_id")
    f = f.join(cust[["signup_date", "city", "acquisition_channel", "marketing_opt_in"]], how="inner")
    f["tenure_days"] = (t - pd.to_datetime(f["signup_date"])).dt.days
    f["marketing_opt_in"] = f["marketing_opt_in"].astype("float64")

    out = f[NUMERIC_FEATURES + CATEGORICAL_FEATURES].reset_index().sort_values("customer_id")
    out.insert(0, "snapshot_date", t)
    return out.reset_index(drop=True)


def snapshot_frame(orders, customers, snapshot, horizon=HORIZON_DAYS, with_label=True):
    """Features for one snapshot, plus the label `churned` (1 = no order in the next `horizon` days)."""
    t = pd.Timestamp(snapshot)
    frame = build_features(orders, customers, t)
    if with_label:
        end = t + pd.Timedelta(days=horizon)
        ahead = orders[(orders["order_ts"] >= t) & (orders["order_ts"] < end)
                       & (orders["status"] != "cancelled")]
        frame["churned"] = (~frame["customer_id"].isin(ahead["customer_id"])).astype(int)
    return frame


def snapshot_dates(as_of, horizon=HORIZON_DAYS, first_snapshot=FIRST_SNAPSHOT):
    """Month starts whose whole label window lies inside the data."""
    cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    return [d for d in pd.date_range(first_snapshot, cutoff, freq="MS")
            if d + pd.Timedelta(days=horizon) <= cutoff]


def build_dataset(orders, customers, as_of, horizon=HORIZON_DAYS, first_snapshot=FIRST_SNAPSHOT):
    """Returns (labelled rows for all snapshots, unlabelled scoring rows at the end of the data)."""
    labelled = pd.concat([snapshot_frame(orders, customers, d, horizon)
                          for d in snapshot_dates(as_of, horizon, first_snapshot)], ignore_index=True)
    scoring = snapshot_frame(orders, customers, pd.Timestamp(as_of) + pd.Timedelta(days=1),
                             horizon, with_label=False)
    return labelled, scoring


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--as-of", default="2026-10-31", help="YYYY-MM-DD, end of the data period")
    ap.add_argument("--horizon", type=int, default=HORIZON_DAYS)
    args = ap.parse_args()
    lake = Path(args.lake)
    try:
        orders = load_orders(lake, args.as_of)
        customers = pd.read_parquet(lake / "clean" / "customers" / "customers.parquet")
    except FileNotFoundError as e:
        print(f"{e}\nRun dp.clean first.")
        sys.exit(1)

    labelled, scoring = build_dataset(orders, customers, args.as_of, args.horizon)
    out = lake / "ml"
    out.mkdir(parents=True, exist_ok=True)
    labelled.to_parquet(out / "churn_dataset.parquet", index=False)
    scoring.to_parquet(out / "churn_scoring.parquet", index=False)

    print(f"{'snapshot':<12}{'customers':>10}{'churned':>10}{'churn rate':>12}")
    for d, part in labelled.groupby("snapshot_date"):
        print(f"{d.date()!s:<12}{len(part):>10,}{int(part['churned'].sum()):>10,}{part['churned'].mean():>11.1%}")
    print(f"\nlabelled rows: {len(labelled):,} -> ml/churn_dataset.parquet")
    print(f"scoring rows : {len(scoring):,} (as of {scoring['snapshot_date'].iloc[0].date()}) -> ml/churn_scoring.parquet")


if __name__ == "__main__":
    main()
