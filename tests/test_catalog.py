import pytest

from dp.catalog import (athena_type, create_table_sql, curated_tables, format_rows,
                        register_tables, run_query)


class FakeAthena:
    """Stands in for boto3's Athena client. Records every statement it is asked to run."""

    def __init__(self, states=("SUCCEEDED",), rows=None, reason="boom"):
        self.states, self.rows, self.reason = list(states), rows or [], reason
        self.statements, self.calls = [], 0

    def start_query_execution(self, QueryString, QueryExecutionContext, WorkGroup):
        self.statements.append((QueryString, QueryExecutionContext["Database"], WorkGroup))
        return {"QueryExecutionId": f"q{len(self.statements)}"}

    def get_query_execution(self, QueryExecutionId):
        state = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        return {"QueryExecution": {"Status": {"State": state, "StateChangeReason": self.reason}}}

    def get_query_results(self, QueryExecutionId, NextToken=None):
        data = lambda r: {"Data": [{"VarCharValue": v} for v in r]}
        if NextToken is None and len(self.rows) > 2:  # split the rows over two pages
            return {"ResultSet": {"Rows": [data(r) for r in self.rows[:2]]}, "NextToken": "t"}
        rest = self.rows[2:] if len(self.rows) > 2 else self.rows
        return {"ResultSet": {"Rows": [data(r) for r in rest]}}


def test_parquet_types_map_to_athena_types():
    assert athena_type("int64") == "bigint" and athena_type("int32") == "int"
    assert athena_type("double") == "double" and athena_type("bool") == "boolean"
    assert athena_type("string") == "string" and athena_type("date32[day]") == "date"
    assert athena_type("timestamp[us]") == "timestamp"
    assert athena_type("decimal128(18, 3)") == "decimal(18,3)"


def test_an_unknown_type_is_refused_instead_of_guessed():
    with pytest.raises(ValueError):
        athena_type("list<item: int64>")


def test_create_table_sql_points_at_the_models_prefix():
    sql = create_table_sql("decision_platform", "revenue_kpis",
                           [("grain", "string"), ("orders", "bigint")], "my-bucket")
    assert sql.startswith("CREATE EXTERNAL TABLE `decision_platform`.`revenue_kpis` (")
    assert "  `grain` string,\n  `orders` bigint\n)" in sql
    assert "STORED AS PARQUET" in sql
    assert "LOCATION 's3://my-bucket/curated/revenue_kpis/'" in sql


def test_curated_tables_come_from_the_model_folders(tmp_path):
    for model in ("rfm", "customer_360"):
        p = tmp_path / "curated" / model / f"{model}.parquet"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"")
    assert list(curated_tables(tmp_path)) == ["customer_360", "rfm"]


def test_register_drops_then_creates_each_table_from_its_own_schema(tmp_path):
    for model in ("rfm", "top_products"):
        p = tmp_path / "curated" / model / f"{model}.parquet"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"")
    client = FakeAthena()
    names = register_tables(client, tmp_path, "my-bucket",
                            read=lambda path: [(path.parent.name + "_col", "string")])
    assert names == ["rfm", "top_products"]
    sql = [s[0] for s in client.statements]
    assert sql[0] == "DROP TABLE IF EXISTS `decision_platform`.`rfm`"
    assert sql[1].startswith("CREATE EXTERNAL TABLE `decision_platform`.`rfm`") and "`rfm_col` string" in sql[1]
    assert sql[2].startswith("DROP TABLE") and "`top_products_col` string" in sql[3]
    assert {s[2] for s in client.statements} == {"decision-platform"}


def test_register_without_curated_files_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        register_tables(FakeAthena(), tmp_path, "my-bucket")


def test_run_query_waits_until_the_query_succeeds():
    client = FakeAthena(states=("QUEUED", "RUNNING", "SUCCEEDED"), rows=[["a", "b"], ["1", "2"]])
    assert run_query(client, "SELECT 1", "db", "wg", 0) == [["a", "b"], ["1", "2"]]
    assert client.calls == 3


def test_run_query_reads_every_page_of_results():
    rows = [["a"], ["1"], ["2"], ["3"]]
    assert run_query(FakeAthena(rows=rows), "SELECT a", "db", "wg", 0) == rows


def test_a_failed_query_raises_with_the_reason():
    with pytest.raises(RuntimeError) as e:
        run_query(FakeAthena(states=("RUNNING", "FAILED"), reason="TABLE_NOT_FOUND"), "SELECT 1", "db", "wg", 0)
    assert "failed" in str(e.value) and "TABLE_NOT_FOUND" in str(e.value)


def test_format_rows_aligns_columns_and_shows_nulls():
    text = format_rows([["month", "orders"], ["2026-10-01", "5"], ["2026-09-01", None]])
    assert text.splitlines()[0].startswith("month       orders")
    assert text.splitlines()[2].endswith("null")
