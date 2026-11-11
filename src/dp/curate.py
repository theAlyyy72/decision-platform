"""Build the curated zone: run every sql/*.sql model on the clean Parquet files with DuckDB.

- input : clean/<table>/<table>.parquet
- output: curated/<model>/<model>.parquet  (one file per sql/<model>.sql)
- each model is one SELECT. The token {{as_of}} is replaced with DATE 'YYYY-MM-DD'.
- models see the clean tables as views; orders (and their items and payments) are cut
  off at the as-of date, so a run can be repeated exactly.

Run: python3 -m dp.curate --lake data/lake --sql sql --as-of 2026-10-31
"""
import argparse
import sys
from datetime import date
from pathlib import Path

from dp.schemas import EXPECTED_COLUMNS


def clean_path(lake, table):
    return lake / "clean" / table / f"{table}.parquet"


def curate(lake_dir, sql_dir="sql", as_of="2026-10-31"):
    """Run all models. Returns {model: rows_written}."""
    import duckdb  # imported here so the other steps work without duckdb installed

    as_of = date.fromisoformat(as_of).isoformat()  # also guards the SQL text below
    lake, sql_dir = Path(lake_dir), Path(sql_dir)
    missing = [t for t in EXPECTED_COLUMNS if not clean_path(lake, t).exists()]
    if missing:
        raise FileNotFoundError(f"no clean file for: {', '.join(missing)}. Run dp.clean first.")
    models = sorted(sql_dir.glob("*.sql"))
    if not models:
        raise FileNotFoundError(f"no .sql models in {sql_dir}")

    con = duckdb.connect()
    read = lambda t: f"read_parquet('{clean_path(lake, t).as_posix()}')"
    con.execute(f"CREATE VIEW customers AS SELECT * FROM {read('customers')}")
    con.execute(f"CREATE VIEW products AS SELECT * FROM {read('products')}")
    con.execute(f"CREATE VIEW orders AS SELECT * FROM {read('orders')} "
                f"WHERE order_ts < CAST('{as_of}' AS DATE) + INTERVAL 1 DAY")
    for t in ("order_items", "payments"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM {read(t)} "
                    f"WHERE order_id IN (SELECT order_id FROM orders)")

    rows = {}
    for model in models:
        query = model.read_text().replace("{{as_of}}", f"DATE '{as_of}'").strip().rstrip(";")
        out = lake / "curated" / model.stem / f"{model.stem}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY (\n{query}\n) TO '{out.as_posix()}' (FORMAT PARQUET)")
        rows[model.stem] = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--sql", default="sql")
    ap.add_argument("--as-of", default="2026-10-31", help="YYYY-MM-DD, end of the data period")
    args = ap.parse_args()
    try:
        rows = curate(args.lake, args.sql, args.as_of)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    for name, n in rows.items():
        print(f"{name:<14} {n:>8,} rows -> curated/{name}/{name}.parquet")


if __name__ == "__main__":
    main()
