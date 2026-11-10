"""Turn the validated CSV files into typed, tidy Parquet files (the clean zone).

- input : validated/<table>/ingest_date=YYYY-MM-DD/<table>_<batch_id>.csv (latest partition)
- output: clean/<table>/<table>.parquet  (one snapshot per table, overwritten each run)
- validation already removed the rows that cannot be trusted, so this step only
  fixes types and formats. It never drops rows.

What changes:
- text is trimmed, emails and enums are lower case, an invalid email becomes null
- numbers, dates and timestamps get real types, blanks become null
- order_items gets a line_total column (quantity * unit_price after discount)
- payment_method variants ("UPI ", "Card") become the standard value ("upi", "card")

Run: python3 -m dp.clean --lake data/lake
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from dp.schemas import EXPECTED_COLUMNS

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def text(s, lower=False):
    """Trim; blank becomes null."""
    s = s.str.strip()
    s = s.mask(s == "")
    return s.str.lower() if lower else s


def number(s):
    return pd.to_numeric(text(s), errors="coerce")


def flag(s):
    return text(s, lower=True).map({"true": True, "false": False}).astype("boolean")


def clean_customers(df):
    email = text(df["email"], lower=True)
    email = email.where(email.str.match(EMAIL_RE, na=False))  # invalid email -> null
    return pd.DataFrame({
        "customer_id": text(df["customer_id"]),
        "full_name": text(df["full_name"]),
        "email": email,
        "city": text(df["city"]),
        "signup_date": pd.to_datetime(text(df["signup_date"]), format="%Y-%m-%d").dt.date,
        "acquisition_channel": text(df["acquisition_channel"], lower=True),
        "marketing_opt_in": flag(df["marketing_opt_in"]),
    })


def clean_products(df):
    return pd.DataFrame({
        "product_id": text(df["product_id"]),
        "product_name": text(df["product_name"]),
        "category": text(df["category"]),
        "price": number(df["price"]),
        "unit_cost": number(df["unit_cost"]),
        "created_at": pd.to_datetime(text(df["created_at"]), format="%Y-%m-%d").dt.date,
    })


def clean_orders(df):
    return pd.DataFrame({
        "order_id": text(df["order_id"]),
        "customer_id": text(df["customer_id"]),
        "order_ts": pd.to_datetime(text(df["order_ts"])),
        "status": text(df["status"], lower=True),
        "promised_days": number(df["promised_days"]).round().astype("Int64"),
        "delivery_days": number(df["delivery_days"]).round().astype("Int64"),  # null if cancelled
    })


def clean_order_items(df):
    qty, price, disc = number(df["quantity"]), number(df["unit_price"]), number(df["discount_pct"])
    return pd.DataFrame({
        "order_item_id": text(df["order_item_id"]),
        "order_id": text(df["order_id"]),
        "product_id": text(df["product_id"]),
        "quantity": qty.astype("Int64"),
        "unit_price": price,
        "discount_pct": disc.astype("Int64"),
        "line_total": (qty * price * (1 - disc / 100)).round(2),
    })


def clean_payments(df):
    return pd.DataFrame({
        "payment_id": text(df["payment_id"]),
        "order_id": text(df["order_id"]),
        "payment_method": text(df["payment_method"], lower=True),
        "amount": number(df["amount"]),
        "payment_status": text(df["payment_status"], lower=True),
        "paid_at": pd.to_datetime(text(df["paid_at"])),
    })


CLEANERS = {"customers": clean_customers, "products": clean_products, "orders": clean_orders,
            "order_items": clean_order_items, "payments": clean_payments}


def latest_validated(lake, table):
    files = sorted((lake / "validated" / table).glob(f"ingest_date=*/{table}_*.csv"))
    return files[-1] if files else None


def clean(lake_dir):
    """Clean every table. Returns {table: rows_written}."""
    lake = Path(lake_dir)
    paths = {t: latest_validated(lake, t) for t in EXPECTED_COLUMNS}
    missing = [t for t, p in paths.items() if p is None]
    if missing:
        raise FileNotFoundError(f"no validated file for: {', '.join(missing)}. Run dp.validate first.")

    rows = {}
    for table, path in paths.items():
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        out = CLEANERS[table](df)
        assert len(out) == len(df), "clean step must never drop rows"
        dest = lake / "clean" / table / f"{table}.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(dest, index=False)
        rows[table] = len(out)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="data/lake")
    args = ap.parse_args()
    try:
        rows = clean(args.lake)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    for table, n in rows.items():
        print(f"{table:<12} {n:>8,} rows -> clean/{table}/{table}.parquet")


if __name__ == "__main__":
    main()
