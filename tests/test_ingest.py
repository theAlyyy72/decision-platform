import json

from dp.ingest import LOG_NAME, ingest
from dp.schemas import EXPECTED_COLUMNS


def make_source(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for table, cols in EXPECTED_COLUMNS.items():
        rows = [",".join(cols)] + [",".join(f"{c}{i}" for c in cols) for i in (1, 2)]
        (folder / f"{table}.csv").write_text("\n".join(rows) + "\n")


def statuses(results):
    return {r["table"]: r["status"] for r in results}


def log_lines(lake):
    return [json.loads(x) for x in (lake / LOG_NAME).read_text().splitlines()]


def test_ingest_copies_files_unchanged_and_logs_them(tmp_path):
    src, lake = tmp_path / "src", tmp_path / "lake"
    make_source(src)
    results = ingest(src, lake, "2026-11-01", batch_id="b1")
    assert set(statuses(results).values()) == {"ingested"}
    raw = lake / "raw" / "orders" / "ingest_date=2026-11-01" / "orders_b1.csv"
    assert raw.read_bytes() == (src / "orders.csv").read_bytes()
    assert len(log_lines(lake)) == 5
    assert results[0]["rows"] == 2


def test_running_the_same_files_twice_does_nothing(tmp_path):
    src, lake = tmp_path / "src", tmp_path / "lake"
    make_source(src)
    ingest(src, lake, "2026-11-01", batch_id="b1")
    second = ingest(src, lake, "2026-11-01", batch_id="b2")
    assert set(statuses(second).values()) == {"skipped_duplicate"}
    assert len(list((lake / "raw" / "customers").rglob("*.csv"))) == 1


def test_a_changed_file_is_ingested_again(tmp_path):
    src, lake = tmp_path / "src", tmp_path / "lake"
    make_source(src)
    ingest(src, lake, "2026-11-01", batch_id="b1")
    with open(src / "customers.csv", "a") as f:
        f.write(",".join(f"{c}3" for c in EXPECTED_COLUMNS["customers"]) + "\n")
    second = statuses(ingest(src, lake, "2026-11-02", batch_id="b2"))
    assert second["customers"] == "ingested"
    assert second["orders"] == "skipped_duplicate"


def test_wrong_columns_are_rejected_and_nothing_is_written(tmp_path):
    src, lake = tmp_path / "src", tmp_path / "lake"
    make_source(src)
    (src / "orders.csv").write_text("order_id,cust,order_ts\no1,c1,2026-01-01\n")
    results = ingest(src, lake, "2026-11-01", batch_id="b1")
    orders = next(r for r in results if r["table"] == "orders")
    assert orders["status"] == "failed_schema"
    assert "customer_id" in orders["missing_columns"]
    assert "cust" in orders["unexpected_columns"]
    assert not (lake / "raw" / "orders").exists()


def test_missing_file_is_reported(tmp_path):
    src, lake = tmp_path / "src", tmp_path / "lake"
    make_source(src)
    (src / "payments.csv").unlink()
    assert statuses(ingest(src, lake, "2026-11-01", batch_id="b1"))["payments"] == "failed_missing_file"