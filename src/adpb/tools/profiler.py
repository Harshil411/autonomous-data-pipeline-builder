"""Compact dataset profiles for agent prompts (requirement 17; mitigation 2).

"Agents should be able to see a schema, some sample data and statistics over the columns
in the data. The full dataset should not be present in any agent prompt." A profile is a
few kilobytes regardless of dataset size: per-column type inference, null rates, distinct
counts, min/max/mean, a handful of example values (truncated), and five sample rows.
"""

from __future__ import annotations

import pandas as pd

from adpb.sandbox_runtime.conform import coerce_series

INFER_ORDER = ["integer", "float", "boolean", "datetime", "string"]
INFER_THRESHOLD = 0.95
MAX_CHARS = 80
INFER_ROWS = 50_000
MAX_LISTED_VALUES = 25  # columns with this many distinct values or fewer list them all


def _short(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    s = str(v)
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS] + "..."


def _infer(s: pd.Series) -> tuple[str, list[str], pd.Series | None]:
    """Pick the first dtype that parses at least 95% of non-null values."""
    non_null = s.dropna()
    if non_null.empty:
        return "string", [], None
    for dtype in INFER_ORDER[:-1]:
        values, bad = coerce_series(non_null, dtype)
        ok_fraction = 1 - bad.mean()
        if ok_fraction >= INFER_THRESHOLD and values.notna().any():
            if dtype == "datetime" and (values.dropna().dt.normalize() == values.dropna()).all():
                dtype = "date"
            examples = non_null.reset_index(drop=True)[bad].astype(str).head(3).tolist()
            return dtype, [_short(e) for e in examples], values
    return "string", [], None


def profile(df: pd.DataFrame, name: str, sample_rows: int = 5) -> dict:
    head = df.head(INFER_ROWS)
    columns = []
    for col in df.columns:
        s = df[col]
        dtype, unparseable, typed = _infer(head[col])
        info = {
            "name": col,
            "inferred_dtype": dtype,
            "null_count": int(s.isna().sum()),
            "null_fraction": round(float(s.isna().mean()), 4) if len(s) else 0.0,
            "distinct": int(s.nunique(dropna=True)),
            "examples": [_short(v) for v in s.dropna().astype(str).unique()[:3]],
        }
        if unparseable:
            info["unparseable_examples"] = unparseable
        if 0 < info["distinct"] <= MAX_LISTED_VALUES:
            # The full domain, with counts: a rule like "status in {...}" can only be
            # proposed correctly if every value is visible, not just three examples.
            counts = s.dropna().astype(str).value_counts()
            info["values"] = {_short(v)[:40]: int(n) for v, n in counts.items()}
        if typed is not None and dtype in ("integer", "float"):
            info.update(min=float(typed.min()), max=float(typed.max()),
                        mean=round(float(typed.mean()), 4))
        columns.append(info)
    sample = [{k: _short(v) for k, v in r.items()}
              for r in df.head(sample_rows).to_dict("records")]
    return {"name": name, "rows": len(df), "columns": columns, "sample": sample}
