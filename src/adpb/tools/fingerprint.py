"""Order-independent content hash of a DataFrame.

Used twice: the approval token is bound to the fingerprint of the staged data
(requirement 21), and a re-run is "reproducible" when it yields the same fingerprint as
the original run (requirement 18).
"""

from __future__ import annotations

import hashlib

import pandas as pd


def dataset_fingerprint(df: pd.DataFrame) -> str:
    text = df.astype("string").fillna("\x00NULL")
    if len(text.columns):
        text = text.sort_values(list(text.columns), kind="mergesort")
    payload = "\x1f".join(f"{c}:{df[c].dtype}" for c in df.columns) + "\n"
    payload += text.to_csv(index=False, header=False)
    return hashlib.sha256(payload.encode()).hexdigest()
