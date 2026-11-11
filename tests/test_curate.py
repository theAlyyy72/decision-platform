from pathlib import Path

import pandas as pd
import pytest

from dp.clean import CLEANERS

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
AS_OF = "2026-10-31"


def strings(**cols):
    return pd.DataFrame(cols, dtype=str)


def typed(raw):
    return {t: CLEANERS[t](df) for t, df in raw.items()}


def tiny_clean():
    """Mixed statuses. As-of date is 2026-10-31, so O4 (2 Nov) must be ignored."""
    return typed({
        "customers": strings(
            customer_id=["C1", "C2", "C3", "C4"], full_name=["A"] * 4,
            email=["a@example.com", "b@example.com", "", "d@example.com"],
            city=["Pune", "Delhi", "Pune", "Pune"],
            signup_date=["2026-01-05", "2026-02-01", "2026-03-01", "2026-11-05"],
            acquisition_channel=["organic"] * 4, marketing_opt_in=["True"] * 4),
        "products": strings(
            product_id=["P1", "P2"], product_name=["Toy", "Book"], category=["Toys", "Books"],
            price=["100", "200"], unit_cost=["60", "120"], created_at=["2025-01-01"] * 2),
        "orders": strings(
            order_id=["O1", "O2", "O3", "O4"], customer_id=["C1", "C1", "C2", "C1"],
            order_ts=["2026-10-01 10:00:00", "2026-10-05 12:00:00",
                      "2026-10-03 09:00:00", "2026-11-02 10:00:00"],
            status=["delivered", "cancelled", "returned", "delivered"],
            promised_days=["3", "3", "5", "3"], delivery_days=["3.0", "", "6.0", "3.0"]),
        "order_items": strings(
            order_item_id=["OI1", "OI2", "OI3", "OI4"], order_id=["O1", "O2", "O3", "O4"],
            product_id=["P1", "P1", "P2", "P1"], quantity=["2", "1", "1", "1"],
            unit_price=["100", "100", "200", "100"], discount_pct=["10", "0", "0", "0"]),
        "payments": strings(
            payment_id=["PAY1", "PAY2", "PAY3", "PAY4"], order_id=["O1", "O2", "O3", "O4"],
            payment_method=["upi", "upi", "card", "upi"], amount=["180", "100", "200", "100"],
            payment_status=["paid", "failed", "refunded", "paid"],
            paid_at=["2026-10-01 10:05:00"] * 4),
    })


def repeat_clean():
    """Five customers who all buy one 100 rupee toy per order, to test retention, RFM and churn."""
    placed = [("C1", "2026-01-10"), ("C1", "2026-01-25"), ("C1", "2026-03-01"),
              ("C2", "2026-01-12"), ("C2", "2026-03-20"),
              ("C3", "2026-01-20"), ("C4", "2026-02-05"), ("C5", "2026-10-20")]
    n = len(placed)
    oid = [f"O{i}" for i in range(1, n + 1)]
    return typed({
        "customers": strings(
            customer_id=["C1", "C2", "C3", "C4", "C5"], full_name=["A"] * 5,
            email=["a@example.com"] * 5, city=["Pune"] * 5, signup_date=["2026-01-01"] * 5,
            acquisition_channel=["organic"] * 5, marketing_opt_in=["True"] * 5),
        "products": strings(
            product_id=["P1"], product_name=["Toy"], category=["Toys"], price=["100"],
            unit_cost=["60"], created_at=["2025-01-01"]),
        "orders": strings(
            order_id=oid, customer_id=[c for c, _ in placed],
            order_ts=[f"{d} 10:00:00" for _, d in placed], status=["delivered"] * n,
            promised_days=["3"] * n, delivery_days=["3.0"] * n),
        "order_items": strings(
            order_item_id=[f"OI{i}" for i in range(1, n + 1)], order_id=oid,
            product_id=["P1"] * n, quantity=["1"] * n, unit_price=["100"] * n,
            discount_pct=["0"] * n),
        "payments": strings(
            payment_id=[f"PAY{i}" for i in range(1, n + 1)], order_id=oid,
            payment_method=["upi"] * n, amount=["100"] * n, payment_status=["paid"] * n,
            paid_at=[f"{d} 10:05:00" for _, d in placed]),
    })


def build(tmp_path, frames):
    pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    from dp.curate import curate
    lake = tmp_path / "lake"
    for t, df in frames.items():
        p = lake / "clean" / t / f"{t}.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
    rows = curate(lake, SQL_DIR, AS_OF)
    return rows, lambda m: pd.read_parquet(lake / "curated" / m / f"{m}.parquet")


@pytest.fixture
def curated(tmp_path):
    return build(tmp_path, tiny_clean())


@pytest.fixture
def curated_repeat(tmp_path):
    return build(tmp_path, repeat_clean())


def test_one_file_per_model(curated):
    rows, _ = curated
    assert rows == {"customer_360": 3, "customer_kpis": 1, "retention": 1, "revenue_kpis": 6,
                    "rfm": 2, "top_categories": 1, "top_products": 2}


def period(df, grain):
    df = df[df["grain"] == grain].copy()
    df["period_start"] = df["period_start"].astype(str)
    return df.set_index("period_start")


def test_revenue_kpis_month(curated):
    _, read = curated
    r = period(read("revenue_kpis"), "month").loc["2026-10-01"]
    assert (r["orders"], r["delivered_orders"], r["cancelled_orders"], r["returned_orders"]) == (3, 1, 1, 1)
    assert r["active_customers"] == 2 and r["new_customers"] == 2 and r["repeat_customers"] == 0
    assert r["gmv"] == 480.0
    assert r["net_revenue"] == 180.0 and r["paid_orders"] == 1 and r["avg_order_value"] == 180.0
    assert r["gross_margin_pct"] == pytest.approx(0.3333, abs=1e-4)
    assert r["cancellation_rate"] == pytest.approx(0.3333, abs=1e-4)
    assert r["return_rate"] == 0.5
    assert r["on_time_delivery_rate"] == 1.0


def test_revenue_kpis_has_day_week_and_month_rows(curated):
    _, read = curated
    df = read("revenue_kpis")
    assert df["grain"].value_counts().to_dict() == {"day": 3, "week": 2, "month": 1}
    week = period(df, "week")
    # 1 Oct and 3 Oct fall in the week starting Monday 28 Sep; 5 Oct starts the next week
    assert week.loc["2026-09-28", "orders"] == 2 and week.loc["2026-09-28", "net_revenue"] == 180.0
    assert week.loc["2026-10-05", "orders"] == 1 and week.loc["2026-10-05", "active_customers"] == 0


def test_customer_360_rows(curated):
    _, read = curated
    c = read("customer_360").set_index("customer_id")
    assert c.loc["C1", "orders_total"] == 2 and c.loc["C1", "orders_cancelled"] == 1
    assert c.loc["C1", "net_revenue"] == 180.0
    assert c.loc["C1", "days_since_last_order"] == 30  # cancelled order on 5 Oct is not activity
    assert c.loc["C1", "lifecycle_status"] == "active"
    assert c.loc["C1", "favourite_category"] == "Toys"
    assert c.loc["C2", "net_revenue"] == 0.0 and c.loc["C2", "favourite_category"] == "Books"
    assert c.loc["C3", "lifecycle_status"] == "never_ordered"
    assert pd.isna(c.loc["C3", "days_since_last_order"])
    assert not c.loc["C3", "has_email"]
    assert "email" not in c.columns and "full_name" not in c.columns


def test_top_products_and_categories(curated):
    _, read = curated
    p = read("top_products").set_index("product_id")
    assert p.loc["P1", "revenue"] == 180.0 and p.loc["P1", "units_sold"] == 2
    assert p.loc["P1", "revenue_rank"] == 1 and p.loc["P2", "revenue"] == 0.0
    c = read("top_categories").iloc[0]
    assert c["category"] == "Toys" and c["revenue"] == 180.0 and c["revenue_share"] == 1.0


def test_retention_by_cohort(curated_repeat):
    _, read = curated_repeat
    r = read("retention")
    r["cohort_month"] = r["cohort_month"].astype(str)
    r = r.set_index("cohort_month")
    jan = r.loc["2026-01-01"]
    assert jan["cohort_size"] == 3 and jan["ever_repeated"] == 2
    assert jan["retention_30d"] == pytest.approx(0.3333, abs=1e-4)  # only C1 (15 days)
    assert jan["retention_60d"] == pytest.approx(0.3333, abs=1e-4)  # C2 took 67 days
    assert jan["retention_90d"] == pytest.approx(0.6667, abs=1e-4)  # C1 and C2
    assert r.loc["2026-02-01", "retention_90d"] == 0.0
    assert pd.isna(r.loc["2026-10-01", "retention_30d"])  # first order only 11 days ago


def test_customer_kpis_snapshot(curated_repeat):
    _, read = curated_repeat
    k = read("customer_kpis").iloc[0]
    assert k["customers_with_orders"] == 5 and k["active_customers_90d"] == 1
    assert k["lapsed_customers"] == 4 and k["churn_rate"] == 0.8
    assert k["repeat_customers"] == 2 and k["repeat_purchase_rate"] == 0.4
    assert k["avg_lifetime_value"] == 160.0


def test_rfm_scores_and_segments(curated_repeat):
    _, read = curated_repeat
    r = read("rfm").set_index("customer_id")
    assert r.loc["C1", "recency_days"] == 244
    assert r.loc["C1", "rfm_code"] == "135" and r.loc["C1", "segment"] == "at_risk"
    assert r.loc["C2", "segment"] == "hibernating"
    assert r.loc["C5", "r_score"] == 5 and r.loc["C5", "segment"] == "promising"


def test_missing_clean_files_raise(tmp_path):
    pytest.importorskip("duckdb")
    from dp.curate import curate
    with pytest.raises(FileNotFoundError):
        curate(tmp_path / "lake", SQL_DIR)
