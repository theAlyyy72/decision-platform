import pandas as pd
import pytest

from dp.validate import validate


def clean_tables():
    """One order, one item, one payment: 2 x 100 with 10% off = 180."""
    return {
        "customers": pd.DataFrame([{
            "customer_id": "C1", "full_name": "Aarav Sharma", "email": "a@example.com",
            "city": "Pune", "signup_date": "2026-01-05", "acquisition_channel": "organic",
            "marketing_opt_in": True}]),
        "products": pd.DataFrame([{
            "product_id": "P1", "product_name": "Toy 1", "category": "Toys", "price": 100.0,
            "unit_cost": 60.0, "created_at": "2025-01-01"}]),
        "orders": pd.DataFrame([{
            "order_id": "O1", "customer_id": "C1", "order_ts": "2026-10-01 10:00:00",
            "status": "delivered", "promised_days": 3, "delivery_days": 3.0}]),
        "order_items": pd.DataFrame([{
            "order_item_id": "OI1", "order_id": "O1", "product_id": "P1", "quantity": 2,
            "unit_price": 100.0, "discount_pct": 10}]),
        "payments": pd.DataFrame([{
            "payment_id": "PAY1", "order_id": "O1", "payment_method": "upi", "amount": 180.0,
            "payment_status": "paid", "paid_at": "2026-10-01 10:05:00"}]),
    }


def run(tmp_path, tables, **kw):
    lake = tmp_path / "lake"
    for name, df in tables.items():
        p = lake / "raw" / name / "ingest_date=2026-11-10" / f"{name}_b1.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False)
    kw.setdefault("max_quarantine_pct", 100)  # row-level tests: do not trip the threshold
    return lake, validate(lake, as_of="2026-11-10", **kw)


def reasons(lake, table):
    p = lake / "quarantine" / table / "ingest_date=2026-11-10" / f"{table}_b1.csv"
    if not p.exists():
        return []
    return pd.read_csv(p, dtype=str, keep_default_na=False)["reject_reason"].tolist()


def rows(report, table, rule):
    return report["tables"][table]["rules"][rule]["rows"]


def test_clean_data_passes_with_nothing_quarantined(tmp_path):
    lake, report = run(tmp_path, clean_tables())
    assert report["status"] == "pass"
    assert all(t["quarantined"] == 0 for t in report["tables"].values())
    assert not (lake / "quarantine").exists()
    assert (lake / "validated" / "orders" / "ingest_date=2026-11-10" / "orders_b1.csv").exists()


def test_duplicate_customer_row_is_quarantined(tmp_path):
    t = clean_tables()
    t["customers"] = pd.concat([t["customers"]] * 2, ignore_index=True)
    lake, report = run(tmp_path, t)
    assert reasons(lake, "customers") == ["duplicate_primary_key"]
    assert report["tables"]["customers"]["passed"] == 1


def test_bad_emails_only_warn_and_the_rows_stay(tmp_path):
    t = clean_tables()
    extra = t["customers"].assign(customer_id=["C2"], email=[""])
    extra2 = t["customers"].assign(customer_id=["C3"], email=["foo.example.com"])
    t["customers"] = pd.concat([t["customers"], extra, extra2], ignore_index=True)
    lake, report = run(tmp_path, t)
    assert rows(report, "customers", "missing_email") == 1
    assert rows(report, "customers", "invalid_email") == 1
    assert report["tables"]["customers"]["quarantined"] == 0
    assert report["tables"]["customers"]["status"] == "warn"


def test_wrong_date_format_is_quarantined(tmp_path):
    t = clean_tables()
    t["customers"].loc[0, "signup_date"] = "05/01/2026"
    lake, _ = run(tmp_path, t)
    assert reasons(lake, "customers") == ["bad_signup_date"]


@pytest.mark.parametrize("price", [0, None])
def test_zero_or_missing_price_is_quarantined(tmp_path, price):
    t = clean_tables()
    t["products"].loc[0, "price"] = price
    lake, _ = run(tmp_path, t)
    assert reasons(lake, "products") == ["bad_price"]


def test_future_dated_order_is_quarantined(tmp_path):
    t = clean_tables()
    t["orders"].loc[0, "order_ts"] = "2026-12-20 10:00:00"
    lake, _ = run(tmp_path, t)
    assert reasons(lake, "orders") == ["future_order_ts"]


def test_order_for_unknown_customer_is_quarantined(tmp_path):
    t = clean_tables()
    t["orders"].loc[0, "customer_id"] = "C900001"
    lake, _ = run(tmp_path, t)
    assert reasons(lake, "orders") == ["unknown_customer"]


def test_quarantined_customer_does_not_orphan_their_orders(tmp_path):
    t = clean_tables()
    t["customers"].loc[0, "signup_date"] = "05/01/2026"  # customer is quarantined...
    lake, report = run(tmp_path, t)
    assert report["tables"]["orders"]["quarantined"] == 0  # ...but the order is fine


@pytest.mark.parametrize("qty", [0, -1])
def test_bad_quantity_is_quarantined_and_payment_is_not_blamed(tmp_path, qty):
    t = clean_tables()
    t["order_items"].loc[0, "quantity"] = qty
    lake, report = run(tmp_path, t)
    assert reasons(lake, "order_items") == ["bad_quantity"]
    assert report["tables"]["payments"]["quarantined"] == 0


def test_item_with_unknown_product_is_quarantined(tmp_path):
    t = clean_tables()
    t["order_items"].loc[0, "product_id"] = "P999"
    lake, _ = run(tmp_path, t)
    assert reasons(lake, "order_items") == ["unknown_product"]


def test_payment_amount_that_does_not_match_the_items_is_quarantined(tmp_path):
    t = clean_tables()
    t["payments"].loc[0, "amount"] = 90.0
    lake, _ = run(tmp_path, t)
    assert reasons(lake, "payments") == ["amount_mismatch"]


def test_payment_method_text_variant_only_warns(tmp_path):
    t = clean_tables()
    t["payments"].loc[0, "payment_method"] = "UPI "
    lake, report = run(tmp_path, t)
    assert rows(report, "payments", "payment_method_text_variant") == 1
    assert report["tables"]["payments"]["quarantined"] == 0


def test_table_fails_when_too_many_rows_are_quarantined(tmp_path):
    t = clean_tables()
    t["products"].loc[0, "price"] = 0  # 1 of 1 rows = 100%
    _, report = run(tmp_path, t, max_quarantine_pct=5)
    assert report["tables"]["products"]["status"] == "fail"
    assert report["status"] == "fail"


def test_missing_raw_file_raises(tmp_path):
    t = clean_tables()
    del t["payments"]
    with pytest.raises(FileNotFoundError):
        run(tmp_path, t)
