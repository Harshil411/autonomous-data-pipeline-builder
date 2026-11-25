"""Deterministic test samples (requirement 6: "test each stage on a small sample").

A purely random sample would often miss the dirty rows that break code, so the sample
always contains the first rows, rows with missing values, and then a seeded random fill.
The same input always yields the same sample.
"""

from __future__ import annotations

import pandas as pd

HEAD_ROWS = 50


def make_sample(df: pd.DataFrame, n: int = 300, seed: int = 0) -> pd.DataFrame:
    if len(df) <= n:
        return df.copy()
    chosen = set(df.index[:HEAD_ROWS])
    with_nulls = df.index[df.isna().any(axis=1)]
    chosen |= set(with_nulls[: n // 4])
    remaining = df.drop(index=list(chosen))
    fill = max(0, n - len(chosen))
    if fill and len(remaining):
        chosen |= set(remaining.sample(n=min(fill, len(remaining)), random_state=seed).index)
    return df.loc[sorted(chosen)].reset_index(drop=True)
