"""Force a stage's output onto its declared schema; quarantine what does not fit.

This file runs INSIDE the sandbox container as well as on the host, so it imports only
pandas/numpy and the standard library (no adpb imports, no pydantic). Schemas arrive as
plain dicts shaped like ``adpb.contracts.TableSchema``.

Requirement 20: "Errors in the source data (missing columns, changed schema, corrupt rows)
should not cause a failure. Corrupt rows should be placed into a quarantine table."
So a value that will not convert sends its *row* to quarantine with a reason, and a
missing optional column is added as nulls. Two things still fail the stage, because they
mean the code is wrong rather than the data is dirty:
  * a missing *required* column, and
  * quarantining more than ``max_quarantine_fraction`` of the rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

NULL_LIKE = {"", "null", "none", "nan", "nat"}
TRUE_WORDS = {"true", "t", "yes", "y", "1"}
FALSE_WORDS = {"false", "f", "no", "n", "0"}
PANDAS_DTYPES = {"string": "string", "integer": "Int64", "float": "Float64",
                 "boolean": "boolean", "date": "datetime64[ns]", "datetime": "datetime64[ns]"}
MIN_ROWS_FOR_FRACTION_CHECK = 10


class ConformError(ValueError):
    """The output cannot be made to fit the schema; the stage code must change."""


@dataclass
class ConformResult:
    frame: pd.DataFrame
    quarantine: pd.DataFrame
    warnings: list[str] = field(default_factory=list)


def _null_mask(s: pd.Series) -> pd.Series:
    """True where a value is missing or a null-like string ("", "NULL", "nan", ...)."""
    missing = s.isna()
    if s.dtype == object or pd.api.types.is_string_dtype(s):
        text = s.astype("string").str.strip().str.lower()
        missing = missing | text.isin(NULL_LIKE).fillna(False)
    return missing.astype(bool)


def coerce_series(s: pd.Series, dtype: str) -> tuple[pd.Series, pd.Series]:
    """Convert ``s`` to ``dtype``. Returns (converted values, mask of unconvertible values).

    Nulls and null-like strings become NA and are *not* counted as unconvertible.
    """
    s = s.reset_index(drop=True)
    is_null = _null_mask(s)
    if dtype == "string":
        values = s.astype("string").where(~is_null, pd.NA)
        return values, pd.Series(False, index=s.index)

    if dtype in ("integer", "float"):
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            numeric = pd.to_numeric(s, errors="coerce")
        else:
            text = s.astype("string").str.strip().str.replace(",", "", regex=False)
            numeric = pd.to_numeric(text.where(~is_null, None), errors="coerce")
        bad = numeric.isna() & ~is_null
        if dtype == "integer":
            fractional = numeric.notna() & (numeric != np.floor(numeric.fillna(0)))
            bad = bad | fractional
            numeric = numeric.where(~bad)
            return numeric.round().astype("Int64"), bad.astype(bool)
        return numeric.astype("Float64"), bad.astype(bool)

    if dtype == "boolean":
        if pd.api.types.is_bool_dtype(s):
            return s.astype("boolean"), pd.Series(False, index=s.index)
        text = s.astype("string").str.strip().str.lower()
        values = pd.Series(pd.NA, index=s.index, dtype="boolean")
        values[text.isin(TRUE_WORDS).fillna(False)] = True
        values[text.isin(FALSE_WORDS).fillna(False)] = False
        bad = values.isna() & ~is_null
        return values, bad.astype(bool)

    if dtype in ("date", "datetime"):
        if pd.api.types.is_datetime64_any_dtype(s):
            parsed = s
        else:
            text = s.astype("string").where(~is_null, None)
            parsed = pd.to_datetime(text, errors="coerce", format="mixed")
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_convert("UTC").dt.tz_localize(None)
        parsed = parsed.astype("datetime64[ns]")
        if dtype == "date":
            parsed = parsed.dt.normalize()
        bad = parsed.isna() & ~is_null
        return parsed, bad.astype(bool)

    raise ConformError(f"unknown dtype '{dtype}'")


def _as_text(df: pd.DataFrame) -> pd.DataFrame:
    """Quarantine rows are stored as text so dirty values survive the trip to parquet."""
    return df.astype("string") if len(df.columns) else df


def conform(
    df: pd.DataFrame,
    schema: dict,
    max_quarantine_fraction: float = 0.25,
    stage_quarantine: pd.DataFrame | None = None,
) -> ConformResult:
    if not isinstance(df, pd.DataFrame):
        raise ConformError(f"stage must return a pandas DataFrame, got {type(df).__name__}")
    df = df.reset_index(drop=True)
    warnings: list[str] = []
    columns = schema["columns"]
    wanted = [c["name"] for c in columns]

    missing_required = [c["name"] for c in columns
                        if c["name"] not in df.columns and not c.get("nullable", True)]
    if missing_required:
        raise ConformError(f"missing required column(s): {missing_required}; "
                           f"output has {list(df.columns)}")
    extra = [c for c in df.columns if c not in wanted]
    if extra:
        warnings.append(f"dropped extra column(s): {extra}")

    out = pd.DataFrame(index=df.index)
    reasons = pd.Series("", index=df.index, dtype=object)
    for col in columns:
        name, dtype = col["name"], col["dtype"]
        if name not in df.columns:
            warnings.append(f"added missing nullable column '{name}' as nulls")
            out[name] = pd.Series(pd.NA, index=df.index).astype(PANDAS_DTYPES[dtype])
            continue
        values, bad = coerce_series(df[name], dtype)
        values.index = df.index
        bad.index = df.index
        for i in df.index[bad]:
            raw = str(df.at[i, name])[:40]
            reasons[i] += f"{name}: cannot convert {raw!r} to {dtype}; "
        if not col.get("nullable", True):
            null_rows = values.isna() & ~bad
            for i in df.index[null_rows]:
                reasons[i] += f"{name} is null (column is required); "
        out[name] = values

    bad_rows = reasons != ""
    quarantine = _as_text(df.loc[bad_rows]).assign(_reason=reasons[bad_rows].str.rstrip("; "))
    if stage_quarantine is not None and len(stage_quarantine):
        sq = _as_text(stage_quarantine.reset_index(drop=True))
        if "_reason" not in sq.columns:
            sq = sq.assign(_reason="flagged by stage code")
        quarantine = pd.concat([quarantine, sq], ignore_index=True)

    kept = out.loc[~bad_rows].reset_index(drop=True)
    total = len(kept) + len(quarantine)
    if total >= MIN_ROWS_FOR_FRACTION_CHECK and len(quarantine) / total > max_quarantine_fraction:
        examples = quarantine["_reason"].head(3).tolist()
        raise ConformError(
            f"{len(quarantine)} of {total} rows quarantined (limit "
            f"{max_quarantine_fraction:.0%}); this looks like a code bug, not dirty data. "
            f"Examples: {examples}")
    return ConformResult(frame=kept, quarantine=quarantine.reset_index(drop=True),
                         warnings=warnings)
