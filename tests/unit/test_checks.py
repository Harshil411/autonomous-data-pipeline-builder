"""Deterministic quality checks (requirement 9; mitigation 5).

Rules are data; these functions are the only judge. No LLM decides whether data is good.
"""

import pandas as pd
import pytest

from adpb.contracts import DType, QualityRule, RuleKind
from adpb.sandbox_runtime.checks import run_checks

ORDERS = pd.DataFrame({
    "order_id": pd.array([1, 2, 3, 3], dtype="Int64"),
    "customer_id": pd.array([10, 11, 99, 99], dtype="Int64"),
    "amount": pd.array([5.0, None, 20.0, 20.0], dtype="Float64"),
    "status": ["paid", "paid", "refunded", "refunded"],
})
CUSTOMERS = pd.DataFrame({"customer_id": pd.array([10, 11, 12], dtype="Int64")})
DATA = {"orders": ORDERS, "customers": CUSTOMERS}


def _check(**kw):
    rule = QualityRule(dataset="orders", **kw)
    [result] = run_checks(DATA, [rule.model_dump(mode="json")])
    return result


def test_not_null():
    r = _check(kind=RuleKind.NOT_NULL, column="amount")
    assert not r["passed"] and r["failing_rows"] == 1


def test_max_null_fraction():
    assert _check(kind=RuleKind.MAX_NULL_FRACTION, column="amount", max=0.3)["passed"]
    assert not _check(kind=RuleKind.MAX_NULL_FRACTION, column="amount", max=0.1)["passed"]


def test_unique_single_and_multi_column():
    assert not _check(kind=RuleKind.UNIQUE, column="order_id")["passed"]
    assert _check(kind=RuleKind.UNIQUE, columns=["order_id", "status"],
                  column=None)["passed"] is False  # rows 3 and 4 are identical


def test_no_duplicate_rows():
    r = _check(kind=RuleKind.NO_DUPLICATE_ROWS)
    assert not r["passed"] and r["failing_rows"] == 1


def test_dtype():
    assert _check(kind=RuleKind.DTYPE, column="amount", dtype=DType.FLOAT)["passed"]
    assert not _check(kind=RuleKind.DTYPE, column="status", dtype=DType.INTEGER)["passed"]


def test_range_ignores_nulls():
    assert _check(kind=RuleKind.RANGE, column="amount", min=0, max=100)["passed"]
    r = _check(kind=RuleKind.RANGE, column="amount", min=10)
    assert not r["passed"] and r["failing_rows"] == 1


def test_allowed_values():
    assert _check(kind=RuleKind.ALLOWED_VALUES, column="status",
                  values=["paid", "refunded"])["passed"]
    assert not _check(kind=RuleKind.ALLOWED_VALUES, column="status", values=["paid"])["passed"]


def test_row_count():
    assert _check(kind=RuleKind.ROW_COUNT, min=1, max=10)["passed"]
    assert not _check(kind=RuleKind.ROW_COUNT, min=5)["passed"]


def test_foreign_key_integrity():
    r = _check(kind=RuleKind.FOREIGN_KEY, column="customer_id", ref_dataset="customers",
               ref_column="customer_id")
    assert not r["passed"] and r["failing_rows"] == 2
    assert r["sample"]  # shows a few offending rows, not the dataset


def test_missing_column_fails_the_rule_instead_of_crashing():
    r = _check(kind=RuleKind.NOT_NULL, column="nope")
    assert not r["passed"] and "missing" in r["observed"]


def test_missing_dataset_fails_the_rule():
    [r] = run_checks(DATA, [QualityRule(kind=RuleKind.ROW_COUNT, dataset="ghost",
                                        min=1).model_dump(mode="json")])
    assert not r["passed"]


def test_samples_are_small():
    big = pd.DataFrame({"x": pd.array([None] * 500, dtype="Int64")})
    [r] = run_checks({"big": big}, [QualityRule(kind=RuleKind.NOT_NULL, dataset="big",
                                                column="x").model_dump(mode="json")])
    assert r["failing_rows"] == 500 and len(r["sample"]) <= 5


@pytest.mark.parametrize("kind", list(RuleKind))
def test_every_rule_kind_has_an_implementation(kind):
    from adpb.sandbox_runtime.checks import CHECKS
    assert kind.value in CHECKS
