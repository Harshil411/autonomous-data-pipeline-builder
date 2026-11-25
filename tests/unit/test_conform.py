"""Schema conformance and quarantine (requirements 3, 6, 20).

After a stage's code runs, the harness forces its output onto the declared schema. Values
that cannot be converted do not crash the pipeline: the offending rows go to quarantine
with a reason. A stage that quarantines too much is treated as broken, so code cannot
"pass" by throwing data away.
"""

import pandas as pd
import pytest

from adpb.sandbox_runtime.conform import (
    ConformError,
    coerce_series,
    conform,
)


def _schema(*cols, pk=None):
    return {"name": "t", "primary_key": pk or [],
            "columns": [{"name": n, "dtype": t, "nullable": nl} for n, t, nl in cols]}


class TestCoerceSeries:
    def test_integers_from_strings(self):
        values, bad = coerce_series(pd.Series(["1", " 2 ", None, "x", "3.0", "3.5"]), "integer")
        assert values.tolist()[:3] == [1, 2, pd.NA]
        assert bad.tolist() == [False, False, False, True, False, True]
        assert str(values.dtype) == "Int64"

    def test_floats_accept_thousands_separators_and_reject_words(self):
        values, bad = coerce_series(pd.Series(["1,234.5", "7", "n/a"]), "float")
        assert values.tolist()[:2] == [1234.5, 7.0]
        assert bad.tolist() == [False, False, True]

    def test_booleans(self):
        values, bad = coerce_series(pd.Series(["Yes", "no", "TRUE", "0", "maybe"]), "boolean")
        assert values.tolist()[:4] == [True, False, True, False]
        assert bad.tolist()[-1]

    def test_dates(self):
        values, bad = coerce_series(pd.Series(["2026-01-05", "not a date", None]), "date")
        assert values.iloc[0] == pd.Timestamp("2026-01-05")
        assert bad.tolist() == [False, True, False]

    def test_null_like_strings_are_nulls_not_errors(self):
        values, bad = coerce_series(pd.Series(["", "NULL", "nan", "None"]), "integer")
        assert values.isna().all() and not bad.any()

    def test_already_typed_values_pass_through(self):
        values, bad = coerce_series(pd.Series([1, 2, 3]), "float")
        assert values.tolist() == [1.0, 2.0, 3.0] and not bad.any()


class TestConform:
    def test_columns_are_typed_reordered_and_extras_dropped(self):
        df = pd.DataFrame({"b": ["x", "y"], "a": ["1", "2"], "junk": [0, 0]})
        res = conform(df, _schema(("a", "integer", False), ("b", "string", True)))
        assert res.frame.columns.tolist() == ["a", "b"]
        assert res.frame["a"].tolist() == [1, 2]
        assert any("junk" in w for w in res.warnings)

    def test_unconvertible_rows_are_quarantined_with_a_reason(self):
        df = pd.DataFrame({"id": [str(i) for i in range(10)] + ["oops"], "v": ["1"] * 11})
        res = conform(df, _schema(("id", "integer", False), ("v", "integer", True)))
        assert len(res.frame) == 10
        assert len(res.quarantine) == 1
        assert "id" in res.quarantine["_reason"].iloc[0]
        assert "oops" in res.quarantine["_reason"].iloc[0]

    def test_nulls_in_required_columns_are_quarantined(self):
        df = pd.DataFrame({"id": ["1", None, "3", "4", "5"]})
        res = conform(df, _schema(("id", "integer", False)))
        assert len(res.frame) == 4 and len(res.quarantine) == 1
        assert "null" in res.quarantine["_reason"].iloc[0]

    def test_missing_required_column_is_a_hard_error(self):
        df = pd.DataFrame({"a": ["1"]})
        with pytest.raises(ConformError, match="missing required column.*b"):
            conform(df, _schema(("a", "integer", False), ("b", "string", False)))

    def test_missing_nullable_column_is_added_with_a_warning(self):
        # Requirement 20: a source that lost an optional column must not fail the run.
        res = conform(pd.DataFrame({"a": ["1"]}),
                      _schema(("a", "integer", False), ("note", "string", True)))
        assert res.frame["note"].isna().all()
        assert any("note" in w for w in res.warnings)

    def test_quarantining_most_of_the_data_is_a_failure_not_a_pass(self):
        df = pd.DataFrame({"id": ["x"] * 9 + ["1"]})
        with pytest.raises(ConformError, match="quarantined"):
            conform(df, _schema(("id", "integer", False)), max_quarantine_fraction=0.25)

    def test_stage_supplied_quarantine_rows_are_kept(self):
        df = pd.DataFrame({"id": ["1", "2", "3", "4"]})
        extra = pd.DataFrame({"id": ["bad"], "_reason": ["stage said so"]})
        res = conform(df, _schema(("id", "integer", False)), stage_quarantine=extra)
        assert res.quarantine["_reason"].tolist() == ["stage said so"]

    def test_empty_output_is_allowed_by_conform(self):
        # Row-count expectations are a quality rule, not a schema concern.
        res = conform(pd.DataFrame({"id": pd.Series([], dtype="object")}),
                      _schema(("id", "integer", False)))
        assert len(res.frame) == 0
