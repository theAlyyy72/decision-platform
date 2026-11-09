"""Generate synthetic NovaMart data (customers, products, orders, order_items, payments).

All data is fake. Some data-quality problems are injected on purpose so that the
validation step later has something real to catch. What was injected is written
to _manifest.json.

Run: python3 src/dp/generate_data.py --customers 20000
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karan", "Ishaan", "Rahul", "Aman", "Dev",
         "Ananya", "Diya", "Priya", "Riya", "Sneha", "Kavya", "Neha", "Pooja", "Isha", "Meera"]
LAST = ["Sharma", "Verma", "Gupta", "Singh", "Khan", "Patel", "Reddy", "Iyer", "Nair", "Das",
        "Joshi", "Mehta", "Shah", "Kapoor", "Malhotra", "Chopra", "Bose", "Ali", "Yadav", "Pillai"]
CITIES = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Pune", "Kolkata",
          "Ahmedabad", "Jaipur", "Lucknow"]
CHANNELS = ["organic", "paid_search", "social", "referral", "email"]
CATEGORIES = {
    "Electronics": (1500, 40000),
    "Fashion": (300, 4000),
    "Home & Kitchen": (200, 8000),
    "Beauty": (150, 2500),
    "Sports": (400, 9000),
    "Books": (100, 900),
    "Toys": (150, 3000),
    "Grocery": (50, 1200),
}
PAY_METHODS = ["upi", "card", "cod", "netbanking", "wallet"]
PAY_P = [0.45, 0.25, 0.15, 0.10, 0.05]
# Festive season (Oct, Nov) is busier than the rest of the year
MONTH_WEIGHT = {1: 0.75, 2: 0.75, 3: 0.75, 4: 0.75, 5: 0.75, 6: 0.75,
                7: 0.75, 8: 0.8, 9: 0.85, 10: 1.0, 11: 1.0, 12: 0.9}


def make_customers(rng, n, start, end):
    """Return the public customers table and a hidden table that drives behaviour."""
    span = (end - start).days
    signup_day = (rng.beta(1.0, 1.3, n) * (span - 30)).astype(int)  # steady acquisition, slowly fading
    signup = start + pd.to_timedelta(signup_day, unit="D")

    engagement = rng.beta(2, 3, n)
    churns = rng.random(n) < (0.15 + 0.5 * (1 - engagement))
    remaining = np.maximum(span - signup_day - 30, 1)
    churn_day = 30 + rng.random(n) * remaining
    churn_date = signup + pd.to_timedelta(churn_day, unit="D")
    churn_date = pd.Series(churn_date).where(churns, end).to_numpy()

    ids = [f"C{i:06d}" for i in range(1, n + 1)]
    first = rng.choice(FIRST, n)
    last = rng.choice(LAST, n)
    customers = pd.DataFrame({
        "customer_id": ids,
        "full_name": [f"{a} {b}" for a, b in zip(first, last)],
        "email": [f"{a.lower()}.{b.lower()}{i}@example.com" for a, b, i in zip(first, last, range(1, n + 1))],
        "city": rng.choice(CITIES, n),
        "signup_date": signup.date,
        "acquisition_channel": rng.choice(CHANNELS, n, p=[0.30, 0.25, 0.20, 0.15, 0.10]),
        "marketing_opt_in": rng.random(n) < 0.6,
    })
    hidden = pd.DataFrame({
        "signup": signup,
        "engagement": engagement,
        "churn_date": pd.to_datetime(churn_date),
    })
    return customers, hidden


def make_products(rng, n=400):
    names = list(CATEGORIES)
    category = rng.choice(names, n, p=[0.08, 0.22, 0.18, 0.12, 0.08, 0.12, 0.08, 0.12])
    lo = np.array([CATEGORIES[c][0] for c in category])
    hi = np.array([CATEGORIES[c][1] for c in category])
    price = lo + (hi - lo) * rng.beta(1.2, 3.0, n)
    price = np.maximum(np.round(price / 10) * 10 - 1, lo)
    products = pd.DataFrame({
        "product_id": [f"P{i:04d}" for i in range(1, n + 1)],
        "product_name": [f"{c.split()[0]} Product {i:04d}" for i, c in enumerate(category, 1)],
        "category": category,
        "price": price,
        "unit_cost": np.round(price * rng.uniform(0.5, 0.8, n), 2),
        "created_at": (pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 300, n), unit="D")).date,
    })
    popularity = (rng.pareto(1.5, n) + 1) / (price / price.mean()) ** 0.7  # cheap items sell more
    return products, popularity / popularity.sum()


def make_orders(rng, customers, hidden, start, end):
    n = len(customers)
    first_order_gap = np.ceil(rng.exponential(7, n)).astype(int)
    active_start = hidden["signup"] + pd.to_timedelta(first_order_gap, unit="D")
    days_active = (hidden["churn_date"] - active_start).dt.days.clip(lower=0).to_numpy()

    rate_per_day = (0.015 + 0.35 * hidden["engagement"].to_numpy()) / 30
    n_orders = rng.poisson(rate_per_day * days_active * 1.15)  # a bit extra, thinned below

    cust_idx = np.repeat(np.arange(n), n_orders)
    offset_days = np.floor(rng.random(len(cust_idx)) * days_active[cust_idx]).astype(int)
    seconds = rng.integers(8 * 3600, 86400, len(cust_idx))
    order_ts = (active_start.iloc[cust_idx].reset_index(drop=True)
                + pd.to_timedelta(offset_days, unit="D")
                + pd.to_timedelta(seconds, unit="s"))

    weight = order_ts.dt.month.map(MONTH_WEIGHT).to_numpy()
    keep = (rng.random(len(order_ts)) < weight) & (order_ts <= end + pd.Timedelta(days=1)).to_numpy()
    cust_idx, order_ts = cust_idx[keep], order_ts[keep].reset_index(drop=True)

    order = np.argsort(order_ts.to_numpy())
    cust_idx, order_ts = cust_idx[order], order_ts.iloc[order].reset_index(drop=True)
    m = len(order_ts)

    status = rng.choice(["delivered", "cancelled", "returned"], m, p=[0.87, 0.06, 0.07])
    promised = rng.choice([2, 3, 5, 7], m, p=[0.2, 0.4, 0.3, 0.1])
    delay = (rng.random(m) < 0.12) * rng.integers(1, 6, m)
    delivery = (promised + delay).astype(float)
    delivery[status == "cancelled"] = np.nan

    return pd.DataFrame({
        "order_id": [f"O{i:07d}" for i in range(1, m + 1)],
        "customer_id": customers["customer_id"].to_numpy()[cust_idx],
        "order_ts": order_ts,
        "status": status,
        "promised_days": promised,
        "delivery_days": delivery,
    })


def make_order_items(rng, orders, products, popularity):
    n_items = np.minimum(rng.geometric(0.55, len(orders)), 5)
    order_idx = np.repeat(np.arange(len(orders)), n_items)
    total = len(order_idx)
    product_idx = rng.choice(len(products), total, p=popularity)
    return pd.DataFrame({
        "order_item_id": [f"OI{i:08d}" for i in range(1, total + 1)],
        "order_id": orders["order_id"].to_numpy()[order_idx],
        "product_id": products["product_id"].to_numpy()[product_idx],
        "quantity": rng.choice([1, 2, 3], total, p=[0.8, 0.15, 0.05]),
        "unit_price": products["price"].to_numpy()[product_idx],
        "discount_pct": rng.choice([0, 5, 10, 15, 20], total, p=[0.55, 0.15, 0.15, 0.10, 0.05]),
    })


def make_payments(rng, orders, items):
    line_total = items["quantity"] * items["unit_price"] * (1 - items["discount_pct"] / 100)
    totals = line_total.groupby(items["order_id"]).sum().round(2)
    amount = orders["order_id"].map(totals).to_numpy()

    status = np.where(orders["status"] == "delivered", "paid",
                      np.where(orders["status"] == "returned", "refunded",
                               rng.choice(["failed", "refunded"], len(orders))))
    minutes = rng.integers(1, 31, len(orders))
    return pd.DataFrame({
        "payment_id": [f"PAY{i:07d}" for i in range(1, len(orders) + 1)],
        "order_id": orders["order_id"],
        "payment_method": rng.choice(PAY_METHODS, len(orders), p=PAY_P),
        "amount": amount,
        "payment_status": status,
        "paid_at": orders["order_ts"] + pd.to_timedelta(minutes, unit="m"),
    })


def pick(rng, size, count):
    """Pick `count` distinct row positions out of `size` rows."""
    return rng.choice(size, min(count, size), replace=False)


def inject_defects(rng, t, end):
    """Break the clean tables in realistic ways. Returns the tables and a count of each defect."""
    log = {}

    # customers: exact duplicate rows, missing/invalid emails, mixed date formats
    c = t["customers"]
    dup = pick(rng, len(c), int(len(c) * 0.005))
    c = pd.concat([c, c.iloc[dup]], ignore_index=True)
    log["customers_duplicate_rows"] = len(dup)

    perm = rng.permutation(len(c))
    n_missing, n_invalid, n_date = (int(len(c) * f) for f in (0.02, 0.01, 0.01))
    missing, invalid = perm[:n_missing], perm[n_missing:n_missing + n_invalid]
    bad_date = perm[n_missing + n_invalid:n_missing + n_invalid + n_date]
    c.loc[missing, "email"] = None
    c.loc[invalid, "email"] = c.loc[invalid, "email"].str.replace("@", "", regex=False)
    c["signup_date"] = c["signup_date"].astype(str)
    c.loc[bad_date, "signup_date"] = pd.to_datetime(c.loc[bad_date, "signup_date"]).dt.strftime("%d/%m/%Y")
    log.update(customers_missing_email=len(missing), customers_invalid_email=len(invalid),
               customers_wrong_date_format=len(bad_date))
    t["customers"] = c

    # products: a few with a zero or missing price
    p = t["products"]
    bad = pick(rng, len(p), 3)
    p.loc[bad[:2], "price"] = 0
    p.loc[bad[2:], "price"] = np.nan
    log["products_bad_price"] = len(bad)

    # orders: duplicate rows, orders for unknown customers, orders dated in the future
    o = t["orders"]
    dup = pick(rng, len(o), int(len(o) * 0.003))
    o = pd.concat([o, o.iloc[dup]], ignore_index=True)
    perm = rng.permutation(len(o))
    n_orphan, n_future = int(len(o) * 0.002), int(len(o) * 0.001)
    orphan, future = perm[:n_orphan], perm[n_orphan:n_orphan + n_future]
    o.loc[orphan, "customer_id"] = [f"C9{i:05d}" for i in range(len(orphan))]
    o.loc[future, "order_ts"] = end + pd.to_timedelta(rng.integers(2, 60, len(future)), unit="D")
    log.update(orders_duplicate_rows=len(dup), orders_unknown_customer=len(orphan),
               orders_future_dated=len(future))
    t["orders"] = o

    # order_items: zero or negative quantity
    i = t["order_items"]
    bad = pick(rng, len(i), int(len(i) * 0.001))
    i.loc[bad, "quantity"] = rng.choice([0, -1], len(bad))
    log["order_items_bad_quantity"] = len(bad)

    # payments: amount does not match the order, inconsistent payment_method text
    pay = t["payments"]
    perm = rng.permutation(len(pay))
    n_amt, n_txt = int(len(pay) * 0.005), int(len(pay) * 0.01)
    amt, txt = perm[:n_amt], perm[n_amt:n_amt + n_txt]
    pay.loc[amt, "amount"] = (pay.loc[amt, "amount"] * rng.choice([0.5, 1.1, 2.0], len(amt))).round(2)
    method = pay["payment_method"].astype(object)
    for pos in txt:
        v = method.iloc[pos]
        method.iloc[pos] = rng.choice([v.upper(), v.title(), v + " "])
    pay["payment_method"] = method
    log.update(payments_amount_mismatch=len(amt), payments_method_text_variants=len(txt))

    return t, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--customers", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2025-05-01")
    ap.add_argument("--end", default="2026-10-31")
    ap.add_argument("--out", default="data/source")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)

    customers, hidden = make_customers(rng, args.customers, start, end)
    products, popularity = make_products(rng)
    orders = make_orders(rng, customers, hidden, start, end)
    items = make_order_items(rng, orders, products, popularity)
    payments = make_payments(rng, orders, items)

    tables = {"customers": customers, "products": products, "orders": orders,
              "order_items": items, "payments": payments}
    tables, defects = inject_defects(rng, tables, end)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False)

    manifest = {"seed": args.seed, "start": args.start, "end": args.end,
                "rows": {k: len(v) for k, v in tables.items()}, "injected_defects": defects}
    (out / "_manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"Wrote {len(tables)} tables to {out}/")
    for k, v in manifest["rows"].items():
        print(f"  {k}: {v:,} rows")
    print("Injected defects:")
    for k, v in defects.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()