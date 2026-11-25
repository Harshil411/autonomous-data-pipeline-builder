"""Deterministic data-quality checks (requirement 9; mitigation 5).

Runs inside the sandbox (pandas only, no adpb imports). Each rule is a plain dict shaped
like ``adpb.contracts.QualityRule``; each check returns pass/fail, what it observed, how
many rows failed and up to five offending rows. The LLM Validator only *proposes* rules;
whether data is good is decided here, in code.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

SAMPLE_ROWS = 5
MAX_CELL_CHARS = 60

CheckFn = Callable[[pd.DataFrame, dict, dict[str, pd.DataFrame]], tuple[bool, str, pd.Series]]


def _rule_id(rule: dict) -> str:
    target = rule.get("column") or "+".join(rule.get("columns") or []) or "*"
    return f"{rule['dataset']}.{target}.{rule['kind']}"


def _cell(v):
    if v is None or (not isinstance(v, list | dict) and pd.isna(v)):
        return None
    if isinstance(v, bool | int | float):
        return v.item() if hasattr(v, "item") else v
    return str(v)[:MAX_CELL_CHARS]


def _sample(df: pd.DataFrame, mask: pd.Series) -> list[dict]:
    rows = df.loc[mask].head(SAMPLE_ROWS)
    return [{k: _cell(v) for k, v in r.items()} for r in rows.to_dict("records")]


def _none(df: pd.DataFrame) -> pd.Series:
    return pd.Series(False, index=df.index)


# -- individual checks: each returns (passed, observed, failing-row mask) ----------------


def _not_null(df, rule, _):
    mask = df[rule["column"]].isna()
    return not mask.any(), f"{int(mask.sum())} null value(s)", mask


def _max_null_fraction(df, rule, _):
    frac = float(df[rule["column"]].isna().mean()) if len(df) else 0.0
    limit = rule.get("max") if rule.get("max") is not None else 0.0
    return frac <= limit, f"null fraction {frac:.3f} (limit {limit})", df[rule["column"]].isna()


def _unique(df, rule, _):
    cols = rule.get("columns") or [rule["column"]]
    mask = df.duplicated(subset=cols, keep="first")
    return not mask.any(), f"{int(mask.sum())} duplicate key value(s) in {cols}", mask


def _no_duplicate_rows(df, rule, _):
    mask = df.duplicated(keep="first")
    return not mask.any(), f"{int(mask.sum())} duplicate row(s)", mask


def _dtype(df, rule, _):
    s, want = df[rule["column"]], rule["dtype"]
    ok = {
        "integer": pd.api.types.is_integer_dtype(s),
        "float": pd.api.types.is_float_dtype(s) or pd.api.types.is_integer_dtype(s),
        "boolean": pd.api.types.is_bool_dtype(s),
        "string": pd.api.types.is_string_dtype(s) or s.dtype == object,
        "date": pd.api.types.is_datetime64_any_dtype(s),
        "datetime": pd.api.types.is_datetime64_any_dtype(s),
    }.get(want, False)
    return bool(ok), f"dtype is {s.dtype}, expected {want}", _none(df)


def _range(df, rule, _):
    s = pd.to_numeric(df[rule["column"]], errors="coerce") \
        if not pd.api.types.is_datetime64_any_dtype(df[rule["column"]]) else df[rule["column"]]
    mask = pd.Series(False, index=df.index)
    if rule.get("min") is not None:
        mask |= (s < rule["min"]).fillna(False)
    if rule.get("max") is not None:
        mask |= (s > rule["max"]).fillna(False)
    lo, hi = (s.min(), s.max()) if s.notna().any() else (None, None)
    return not mask.any(), f"{int(mask.sum())} value(s) outside [{rule.get('min')}, " \
                           f"{rule.get('max')}]; observed [{lo}, {hi}]", mask


def _allowed_values(df, rule, _):
    s = df[rule["column"]]
    allowed = {str(v) for v in rule.get("values", [])}
    mask = s.notna() & ~s.astype("string").isin(allowed).fillna(False)
    seen = sorted(s.loc[mask].astype(str).unique().tolist())[:5]
    return not mask.any(), f"{int(mask.sum())} disallowed value(s), e.g. {seen}", mask


def _row_count(df, rule, _):
    n, lo, hi = len(df), rule.get("min"), rule.get("max")
    ok = (lo is None or n >= lo) and (hi is None or n <= hi)
    return ok, f"{n} row(s), expected [{lo}, {hi}]", _none(df)


def _foreign_key(df, rule, datasets):
    ref = datasets.get(rule["ref_dataset"])
    if ref is None or rule["ref_column"] not in ref.columns:
        return False, f"reference {rule['ref_dataset']}.{rule['ref_column']} missing", _none(df)
    keys = set(ref[rule["ref_column"]].dropna().astype(str))
    s = df[rule["column"]]
    mask = s.notna() & ~s.astype(str).isin(keys)
    return not mask.any(), f"{int(mask.sum())} value(s) with no match in " \
                           f"{rule['ref_dataset']}.{rule['ref_column']}", mask


CHECKS: dict[str, CheckFn] = {
    "not_null": _not_null,
    "max_null_fraction": _max_null_fraction,
    "unique": _unique,
    "no_duplicate_rows": _no_duplicate_rows,
    "dtype": _dtype,
    "range": _range,
    "allowed_values": _allowed_values,
    "row_count": _row_count,
    "foreign_key": _foreign_key,
}


def run_checks(datasets: dict[str, pd.DataFrame], rules: list[dict]) -> list[dict]:
    results = []
    for rule in rules:
        base = {"rule_id": _rule_id(rule), "dataset": rule["dataset"]}
        df = datasets.get(rule["dataset"])
        if df is None:
            results.append({**base, "passed": False, "observed": "dataset missing",
                            "failing_rows": 0, "sample": []})
            continue
        needed = [c for c in (rule.get("columns") or [rule.get("column")]) if c]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            results.append({**base, "passed": False, "observed": f"column(s) {missing} missing",
                            "failing_rows": 0, "sample": []})
            continue
        passed, observed, mask = CHECKS[rule["kind"]](df, rule, datasets)
        results.append({**base, "passed": bool(passed), "observed": observed,
                        "failing_rows": int(mask.sum()), "sample": _sample(df, mask)})
    return results
