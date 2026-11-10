import pandas as pd
import pytest

from dp.clean import CLEANERS, clean


def strings(**cols):
    return pd.DataFrame({k: v for k, v in cols.items()}, dtype=str)


def test_invalid_email_becomes_null_and_valid_email_is_lowercased():
    df = strings(customer_id=["C1", "C2", "C3"], full_name=["A B"] * 3,
                 email=["Aarav@Example.com", "foo.example.com", ""], city=["Pune"] * 3,
                 signup_date=["2026-01-05"] * 3, acquisition_channel=["Organic"] * 3,
                 marketing_opt_in=["True", "False", "True"])
    out = CLEANERS["customers"](df)
    assert out["email"].iloc[0] == "aarav@example.com"
    assert out["email"].isna().tolist() == [False, True, True]
    assert out["acquisition_channel"].iloc[0] == "organic"
    assert out["marketing_opt_in"].tolist() == [True, False, True]
    assert out["signup_date"].iloc[0] == pd.Timestamp("2026-01-05").date()


def test_orders_get_real_types_and_cancelled_delivery_stays_null():
    df = strings(order_id=["O1", "O2"], customer_id=["C1", "C1"],
                 order_ts=["2026-10-01 10:00:00", "2026-10-02 11:30:00"],
                 status=["Delivered", "cancelled"], promised_days=["3", "5"],
                 delivery_days=["4.0", ""])
    out = CLEANERS["orders"](df)
    assert str(out["order_ts"].dtype).startswith("datetime64")
    assert out["delivery_days"].iloc[0] == 4
    assert pd.isna(out["delivery_days"].iloc[1])
    assert out["status"].tolist() == ["delivered", "cancelled"]
    assert str(out["promised_days"].dtype) == "Int64"


def test_order_items_get_a_line_total_after_discount():
    df = strings(order_item_id=["OI1"], order_id=["O1"], product_id=["P1"], quantity=["2"],
                 unit_price=["100.0"], discount_pct=["10"])
    out = CLEANERS["order_items"](df)
    assert out["line_total"].iloc[0] == 180.0


def test_payment_method_variants_are_standardised():
    df = strings(payment_id=["PAY1", "PAY2", "PAY3"], order_id=["O1", "O2", "O3"],
                 payment_method=["UPI ", "Card", "cod"], amount=["180.0", "50", "10.5"],
                 payment_status=["paid"] * 3, paid_at=["2026-10-01 10:05:00"] * 3)
    out = CLEANERS["payments"](df)
    assert out["payment_method"].tolist() == ["upi", "card", "cod"]
    assert out["amount"].tolist() == [180.0, 50.0, 10.5]


def test_products_prices_are_numbers():
    df = strings(product_id=["P1"], product_name=["Toy 1"], category=["Toys"], price=["99.0"],
                 unit_cost=["60.5"], created_at=["2025-01-01"])
    out = CLEANERS["products"](df)
    assert out["price"].iloc[0] == 99.0 and out["unit_cost"].iloc[0] == 60.5


def test_clean_writes_one_parquet_per_table_without_dropping_rows(tmp_path):
    pytest.importorskip("pyarrow")
    lake = tmp_path / "lake"
    frames = {
        "customers": strings(customer_id=["C1"], full_name=["A B"], email=["a@example.com"],
                             city=["Pune"], signup_date=["2026-01-05"],
                             acquisition_channel=["organic"], marketing_opt_in=["True"]),
        "products": strings(product_id=["P1"], product_name=["Toy"], category=["Toys"],
                            price=["100"], unit_cost=["60"], created_at=["2025-01-01"]),
        "orders": strings(order_id=["O1"], customer_id=["C1"], order_ts=["2026-10-01 10:00:00"],
                          status=["delivered"], promised_days=["3"], delivery_days=["3.0"]),
        "order_items": strings(order_item_id=["OI1"], order_id=["O1"], product_id=["P1"],
                               quantity=["2"], unit_price=["100"], discount_pct=["10"]),
        "payments": strings(payment_id=["PAY1"], order_id=["O1"], payment_method=["UPI "],
                            amount=["180"], payment_status=["paid"],
                            paid_at=["2026-10-01 10:05:00"]),
    }
    for name, df in frames.items():
        p = lake / "validated" / name / "ingest_date=2026-11-10" / f"{name}_b1.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False)
    rows = clean(lake)
    assert rows == {t: 1 for t in frames}
    back = pd.read_parquet(lake / "clean" / "payments" / "payments.parquet")
    assert back["payment_method"].tolist() == ["upi"]


def test_missing_validated_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        clean(tmp_path / "lake")
