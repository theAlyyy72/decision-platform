"""Register the curated Parquet files as Athena tables, and run SQL on them.

Each table is created from the real schema stored inside its local Parquet file
(data/lake/curated/<model>/<model>.parquet), so the table can never drift from the data.
The files in S3 must come from the same run (aws s3 sync data/lake s3://BUCKET/).
Tables are dropped and recreated on every run. They are external, so no data is deleted.

Needs AWS credentials in the environment:
    eval "$(aws configure export-credentials --profile decision-platform --format env)"

Run:
    python3 -m dp.catalog --bucket $BUCKET                       # create or refresh the tables
    python3 -m dp.catalog --bucket $BUCKET --query "SELECT ..."  # run a SQL query
"""
import argparse
import re
import sys
import time
from pathlib import Path

DEFAULT_REGION = "ap-southeast-2"
DEFAULT_DATABASE = "decision_platform"
DEFAULT_WORKGROUP = "decision-platform"

ARROW_TO_ATHENA = {
    "int8": "tinyint", "int16": "smallint", "int32": "int", "int64": "bigint",
    "float": "float", "double": "double",
    "string": "string", "large_string": "string",
    "bool": "boolean", "date32[day]": "date",
    "timestamp[ms]": "timestamp", "timestamp[us]": "timestamp", "timestamp[ns]": "timestamp",
}


def athena_type(arrow_type):
    """Athena column type for a pyarrow type given as text, e.g. 'int64' -> 'bigint'."""
    t = str(arrow_type)
    if t in ARROW_TO_ATHENA:
        return ARROW_TO_ATHENA[t]
    m = re.fullmatch(r"decimal(?:128|256)?\((\d+),\s*(\d+)\)", t)
    if m:
        return f"decimal({m.group(1)},{m.group(2)})"
    raise ValueError(f"no Athena type for Parquet type '{t}'")


def read_columns(path):
    """[(column name, Athena type)] from the schema of a Parquet file."""
    import pyarrow.parquet as pq
    schema = pq.read_schema(path)
    return [(f.name, athena_type(f.type)) for f in schema]


def curated_tables(lake_dir):
    """{table name: parquet path} for every model in curated/."""
    lake = Path(lake_dir) / "curated"
    return {p.parent.name: p for p in sorted(lake.glob("*/*.parquet"))}


def create_table_sql(database, table, columns, bucket):
    cols = ",\n".join(f"  `{name}` {kind}" for name, kind in columns)
    return (f"CREATE EXTERNAL TABLE `{database}`.`{table}` (\n{cols}\n)\n"
            f"STORED AS PARQUET\nLOCATION 's3://{bucket}/curated/{table}/'")


def run_query(client, sql, database, workgroup, poll_seconds=1.0):
    """Run one statement, wait for it, and return its rows (first row = column names for SELECT)."""
    qid = client.start_query_execution(
        QueryString=sql, QueryExecutionContext={"Database": database},
        WorkGroup=workgroup)["QueryExecutionId"]
    while True:
        status = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if status["State"] == "SUCCEEDED":
            break
        if status["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Athena query {status['State'].lower()}: "
                               f"{status.get('StateChangeReason', 'no reason given')}")
        time.sleep(poll_seconds)

    rows, token = [], None
    while True:
        kwargs = {"QueryExecutionId": qid}
        if token:
            kwargs["NextToken"] = token
        page = client.get_query_results(**kwargs)
        rows += [[c.get("VarCharValue") for c in r["Data"]] for r in page["ResultSet"]["Rows"]]
        token = page.get("NextToken")
        if not token:
            return rows


def register_tables(client, lake_dir, bucket, database=DEFAULT_DATABASE,
                    workgroup=DEFAULT_WORKGROUP, read=read_columns):
    """Drop and recreate one external table per curated model. Returns the table names."""
    tables = curated_tables(lake_dir)
    if not tables:
        raise FileNotFoundError(f"no curated parquet files in {Path(lake_dir) / 'curated'}. Run dp.curate first.")
    for table, path in tables.items():
        run_query(client, f"DROP TABLE IF EXISTS `{database}`.`{table}`", database, workgroup, 0)
        run_query(client, create_table_sql(database, table, read(path), bucket), database, workgroup, 0)
    return list(tables)


def format_rows(rows, limit=50):
    """Plain text table. Column widths come from the data."""
    shown = rows[:limit + 1]
    widths = [max(len(str(r[i])) if r[i] is not None else 4 for r in shown) for i in range(len(shown[0]))]
    lines = ["  ".join(str(v if v is not None else "null").ljust(w) for v, w in zip(r, widths)).rstrip()
             for r in shown]
    if len(rows) > limit + 1:
        lines.append(f"... {len(rows) - 1 - limit} more rows")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True, help="lake bucket, from: terraform -chdir=infra output -raw lake_bucket")
    ap.add_argument("--lake", default="data/lake")
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--database", default=DEFAULT_DATABASE)
    ap.add_argument("--workgroup", default=DEFAULT_WORKGROUP)
    ap.add_argument("--query", help="run this SQL instead of refreshing the tables")
    args = ap.parse_args()

    import boto3
    client = boto3.client("athena", region_name=args.region)
    try:
        if args.query:
            rows = run_query(client, args.query, args.database, args.workgroup)
            print(format_rows(rows) if rows else "(no rows)")
        else:
            for table in register_tables(client, args.lake, args.bucket, args.database, args.workgroup):
                print(f"created table {args.database}.{table}")
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
