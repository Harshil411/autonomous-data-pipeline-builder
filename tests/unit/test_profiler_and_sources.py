"""Source loading and profiling (requirements 1, 17, 20).

Agents see a schema, a few sample rows and column statistics - never the dataset. Sources
are loaded as text so that a corrupt value can never crash ingestion; corrupt *lines*
are captured for the quarantine table.
"""

import json
import sqlite3

import pandas as pd
import pytest

from adpb.contracts import SourceKind, SourceSpec
from adpb.tools.profiler import profile
from adpb.tools.sources import SourceError, load_raw


@pytest.fixture
def src_dir(tmp_path):
    (tmp_path / "orders.csv").write_text(
        "order_id,customer_id,amount\n1,10,5.00\n2,11,7.50\n3,10,abc\n4,12\n5,10,1.00,EXTRA,FIELDS\n"
    )
    (tmp_path / "customers.json").write_text(json.dumps(
        [{"customer_id": 10, "name": "Ada", "address": {"city": "Blacksburg"}},
         {"customer_id": 11, "name": "Grace", "address": {"city": "Roanoke"}}]))
    con = sqlite3.connect(tmp_path / "crm.db")
    con.execute("create table accounts (id integer, tier text)")
    con.executemany("insert into accounts values (?, ?)", [(1, "gold"), (2, None)])
    con.commit()
    con.close()
    return tmp_path


class TestLoadRaw:
    def test_csv_values_are_kept_as_text(self, src_dir):
        res = load_raw(SourceSpec(name="orders", kind=SourceKind.CSV, path="orders.csv"), src_dir)
        assert res.frame["amount"].tolist()[:3] == ["5.00", "7.50", "abc"]

    def test_corrupt_csv_lines_are_captured_not_fatal(self, src_dir):
        res = load_raw(SourceSpec(name="orders", kind=SourceKind.CSV, path="orders.csv"), src_dir)
        assert len(res.frame) == 4  # the 5-field line is not silently merged
        assert len(res.bad_lines) == 1 and "EXTRA" in res.bad_lines[0]

    def test_short_lines_become_nulls(self, src_dir):
        res = load_raw(SourceSpec(name="orders", kind=SourceKind.CSV, path="orders.csv"), src_dir)
        assert pd.isna(res.frame.iloc[3]["amount"])

    def test_json_records_are_flattened(self, src_dir):
        res = load_raw(SourceSpec(name="customers", kind=SourceKind.JSON,
                                  path="customers.json"), src_dir)
        assert "address_city" in res.frame.columns
        assert res.frame["customer_id"].tolist() == ["10", "11"]

    def test_sqlite_is_read_only_and_typed_as_text(self, src_dir):
        res = load_raw(SourceSpec(name="accounts", kind=SourceKind.SQLITE, path="crm.db",
                                  table="accounts"), src_dir)
        assert res.frame["id"].tolist() == ["1", "2"]
        assert pd.isna(res.frame["tier"].iloc[1])

    def test_sqlite_table_names_are_validated(self, src_dir):
        with pytest.raises(SourceError):
            load_raw(SourceSpec(name="a", kind=SourceKind.SQLITE, path="crm.db",
                                table="accounts; drop table accounts"), src_dir)

    def test_paths_cannot_escape_the_sources_folder(self, src_dir):
        with pytest.raises(SourceError, match="outside"):
            load_raw(SourceSpec(name="x", kind=SourceKind.CSV, path="../../etc/passwd"), src_dir)

    def test_column_names_are_normalised_to_identifiers(self, tmp_path):
        (tmp_path / "x.csv").write_text("Order ID,Total Amount ($)\n1,2\n")
        res = load_raw(SourceSpec(name="x", kind=SourceKind.CSV, path="x.csv"), tmp_path)
        assert res.frame.columns.tolist() == ["order_id", "total_amount"]


class TestProfile:
    def test_profile_has_stats_and_inferred_types(self):
        df = pd.DataFrame({"id": [str(i) for i in range(100)],
                           "amount": ["1.5"] * 99 + ["oops"],
                           "flag": ["yes", "no"] * 50,
                           "note": [None] * 100})
        p = profile(df, "raw.orders")
        cols = {c["name"]: c for c in p["columns"]}
        assert p["rows"] == 100
        assert cols["id"]["inferred_dtype"] == "integer"
        assert cols["amount"]["inferred_dtype"] == "float"
        assert cols["amount"]["unparseable_examples"] == ["oops"]
        assert cols["flag"]["inferred_dtype"] == "boolean"
        assert cols["note"]["null_fraction"] == 1.0
        assert cols["id"]["distinct"] == 100

    def test_profile_never_contains_more_than_a_few_rows(self):
        df = pd.DataFrame({"x": [str(i) for i in range(10_000)]})
        p = profile(df, "big", sample_rows=5)
        assert len(p["sample"]) == 5
        assert len(json.dumps(p)) < 5_000

    def test_low_cardinality_columns_list_every_value_with_counts(self):
        # Found in a live run: with only 3 example values the Validator guessed an
        # allowed-values rule that missed "refunded" and forced valid rows into quarantine.
        df = pd.DataFrame({"status": ["paid"] * 50 + ["pending"] * 30 + ["shipped"] * 15
                           + ["refunded"] * 5, "id": [str(i) for i in range(100)]})
        cols = {c["name"]: c for c in profile(df, "t")["columns"]}
        assert cols["status"]["values"] == {"paid": 50, "pending": 30, "shipped": 15,
                                            "refunded": 5}
        assert "values" not in cols["id"]  # too many distinct values to list

    def test_long_values_are_truncated_in_samples(self):
        df = pd.DataFrame({"blob": ["A" * 5_000]})
        p = profile(df, "t")
        assert len(p["sample"][0]["blob"]) <= 83
