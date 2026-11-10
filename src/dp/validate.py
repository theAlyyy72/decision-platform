"""Validate the latest raw files and split every table into clean rows and quarantined rows.

Rules:
- raw files are only read, never edited
- a rule either QUARANTINES a row (it leaves the clean data, with a reject_reason)
  or only WARNS (the row stays, the count goes into the report)
- a table FAILS if more than MAX_QUARANTINE_PCT of its rows are quarantined
- references (orders -> customers, ...) are checked against ALL ids in the raw file,
  so quarantining a customer does not wrongly quarantine that customer's orders

Layout (mirrors raw/ and S3):
    validated/<table>/ingest_date=YYYY-MM-DD/<table>_<batch_id>.csv
    quarantine/<table>/ingest_date=YYYY-MM-DD/<table>_<batch_id>.csv   (+ reject_reason)
    reports/validation_<timestamp>.json

Run: python3 -m dp.validate --lake data/lake
"""
import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from dp.schemas import EXPECTED_COLUMNS

QUARANTINE, WARN = "quarantine", "warn"
MAX_QUARANTINE_PCT = 5.0
ORDER_STATUS = {"delivered", "cancelled", "returned"}
PAYMENT_STATUS = {"paid", "failed", "refunded"}
PAYMENT_METHODS = {"upi", "card", "cod", "netbanking", "wallet"}
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


@dataclass
class Rule:
    name: str
    severity: str
    failed: pd.Series  # True where the row breaks the rule


def blank(s):
    return s.str.strip() == ""


def num(s):
    return pd.to_numeric(s, errors="coerce")


def pk_rules(df, col):
    missing = blank(df[col])
    dup = df.duplicated(subset=[col], keep="first") & ~missing
    return [Rule("missing_primary_key", QUARANTINE, missing),
            Rule("duplicate_primary_key", QUARANTINE, dup)]


def check_customers(df, ctx):
    email = df["email"]
    iso = df["signup_date"].str.match(DATE_RE)
    parsed = pd.to_datetime(df["signup_date"], format="%Y-%m-%d", errors="coerce")
    return pk_rules(df, "customer_id") + [
        Rule("missing_email", WARN, blank(email)),
        Rule("invalid_email", WARN, ~blank(email) & ~email.str.match(EMAIL_RE)),
        Rule("bad_signup_date", QUARANTINE, ~(iso & parsed.notna())),
    ]


def check_products(df, ctx):
    price = num(df["price"])
    return pk_rules(df, "product_id") + [
        Rule("bad_price", QUARANTINE, price.isna() | (price <= 0)),
    ]


def check_orders(df, ctx):
    ts = pd.to_datetime(df["order_ts"], errors="coerce")
    return pk_rules(df, "order_id") + [
        Rule("bad_order_ts", QUARANTINE, ts.isna()),
        Rule("future_order_ts", QUARANTINE, ts > ctx["as_of_end"]),
        Rule("unknown_customer", QUARANTINE, ~df["customer_id"].isin(ctx["ids"]["customers"])),
        Rule("invalid_status", QUARANTINE, ~df["status"].isin(ORDER_STATUS)),
    ]


def check_order_items(df, ctx):
    qty, price, disc = num(df["quantity"]), num(df["unit_price"]), num(df["discount_pct"])
    bad_qty = qty.isna() | (qty <= 0) | (qty != qty.round())
    bad_price = price.isna() | (price <= 0)
    bad_disc = disc.isna() | (disc < 0) | (disc > 100)
    bad_any = bad_qty | bad_price | bad_disc
    dup = df.duplicated(subset=["order_item_id"], keep="first")

    # needed later to check payment amounts
    good = ~bad_any & ~dup
    line = (qty * price * (1 - disc / 100))[good]
    ctx["order_totals"] = line.groupby(df.loc[good, "order_id"]).sum().round(2)
    ctx["orders_with_bad_items"] = set(df.loc[bad_any, "order_id"])

    return pk_rules(df, "order_item_id") + [
        Rule("bad_quantity", QUARANTINE, bad_qty),
        Rule("bad_unit_price", QUARANTINE, bad_price),
        Rule("bad_discount", QUARANTINE, bad_disc),
        Rule("unknown_order", QUARANTINE, ~df["order_id"].isin(ctx["ids"]["orders"])),
        Rule("unknown_product", QUARANTINE, ~df["product_id"].isin(ctx["ids"]["products"])),
    ]


def check_payments(df, ctx):
    amount = num(df["amount"])
    method = df["payment_method"].str.strip().str.lower()
    expected = df["order_id"].map(ctx["order_totals"])
    # an order with a broken item line cannot be re-added up, so it is skipped here
    checkable = expected.notna() & ~df["order_id"].isin(ctx["orders_with_bad_items"])
    mismatch = checkable & amount.notna() & ((amount - expected).abs() > 0.01)
    known = method.isin(PAYMENT_METHODS)
    return pk_rules(df, "payment_id") + [
        Rule("bad_amount", QUARANTINE, amount.isna() | (amount < 0)),
        Rule("amount_mismatch", QUARANTINE, mismatch),
        Rule("unknown_order", QUARANTINE, ~df["order_id"].isin(ctx["ids"]["orders"])),
        Rule("invalid_status", QUARANTINE, ~df["payment_status"].isin(PAYMENT_STATUS)),
        Rule("unknown_payment_method", QUARANTINE, ~known),
        Rule("payment_method_text_variant", WARN, known & (df["payment_method"] != method)),
    ]


# order matters: order_items must run before payments
CHECKS = {"customers": check_customers, "products": check_products, "orders": check_orders,
          "order_items": check_order_items, "payments": check_payments}


def latest_raw(lake, table, ingest_date=None):
    part = f"ingest_date={ingest_date}" if ingest_date else "ingest_date=*"
    files = sorted((lake / "raw" / table).glob(f"{part}/{table}_*.csv"))
    return files[-1] if files else None


def validate(lake_dir, ingest_date=None, as_of=None, max_quarantine_pct=MAX_QUARANTINE_PCT):
    """Validate the latest raw file of every table. Returns the report dict."""
    lake = Path(lake_dir)
    paths = {t: latest_raw(lake, t, ingest_date) for t in EXPECTED_COLUMNS}
    missing = [t for t, p in paths.items() if p is None]
    if missing:
        raise FileNotFoundError(f"no raw file for: {', '.join(missing)}. Run dp.ingest first.")
    frames = {t: pd.read_csv(p, dtype=str, keep_default_na=False) for t, p in paths.items()}

    ids = {t: set(frames[t][c][~blank(frames[t][c])])
           for t, c in (("customers", "customer_id"), ("products", "product_id"),
                        ("orders", "order_id"))}
    # anything dated after the end of the as-of day cannot have happened yet
    as_of_end = pd.Timestamp(as_of or date.today().isoformat()) + pd.Timedelta(days=1)
    ctx = {"ids": ids, "as_of_end": as_of_end}

    report = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "as_of": str(as_of_end.date() - pd.Timedelta(days=1)),
              "max_quarantine_pct": max_quarantine_pct, "tables": {}}

    for table, check in CHECKS.items():
        df = frames[table]
        rules = check(df, ctx)
        quarantined = pd.Series(False, index=df.index)
        reason = pd.Series("", index=df.index)
        for r in rules:
            if r.severity == QUARANTINE:
                quarantined |= r.failed
                reason = reason.mask(r.failed, reason + r.name + ";")
        reason = reason.str.rstrip(";")

        rel = paths[table].relative_to(lake / "raw")
        good_path, bad_path = lake / "validated" / rel, lake / "quarantine" / rel
        good_path.parent.mkdir(parents=True, exist_ok=True)
        df[~quarantined].to_csv(good_path, index=False)
        if quarantined.any():
            bad_path.parent.mkdir(parents=True, exist_ok=True)
            df[quarantined].assign(reject_reason=reason[quarantined]).to_csv(bad_path, index=False)
        elif bad_path.exists():
            bad_path.unlink()  # stale file from an earlier run

        n, q = len(df), int(quarantined.sum())
        pct = round(100 * q / n, 3) if n else 100.0
        counts = {r.name: {"severity": r.severity, "rows": int(r.failed.sum())} for r in rules}
        if n == 0 or pct > max_quarantine_pct:
            status = "fail"
        elif any(c["rows"] for c in counts.values()):
            status = "warn"
        else:
            status = "pass"
        report["tables"][table] = {"raw_file": str(paths[table]), "rows_in": n, "passed": n - q,
                                   "quarantined": q, "quarantine_pct": pct, "status": status,
                                   "rules": counts}

    statuses = {t["status"] for t in report["tables"].values()}
    report["status"] = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = lake / "reports" / f"validation_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    report["report_file"] = str(out)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--ingest-date", default=None, help="default: latest raw partition")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD, default: today")
    ap.add_argument("--max-quarantine-pct", type=float, default=MAX_QUARANTINE_PCT)
    args = ap.parse_args()

    try:
        report = validate(args.lake, args.ingest_date, args.as_of, args.max_quarantine_pct)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    for table, t in report["tables"].items():
        print(f"{table:<12} {t['status']:<5} in={t['rows_in']:>8,} passed={t['passed']:>8,} "
              f"quarantined={t['quarantined']:>6,} ({t['quarantine_pct']}%)")
        for name, c in t["rules"].items():
            if c["rows"]:
                print(f"    {name:<30} {c['rows']:>6,}  [{c['severity']}]")
    print(f"\nOverall: {report['status'].upper()}. Report: {report['report_file']}")
    if report["status"] == "fail":
        sys.exit(1)


if __name__ == "__main__":
    main()
