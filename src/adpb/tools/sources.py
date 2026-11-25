"""Load the user's sources into raw, all-text DataFrames (requirements 1, 20).

Everything is read as text on purpose: a value like "abc" in a numeric column must not
crash ingestion. Typing happens later, in stage code, where bad values are quarantined
with a reason. CSV lines with the wrong number of fields are captured as ``bad_lines`` and
end up in the quarantine table instead of being silently merged or dropped.

API sources are fetched once into a JSON snapshot (host allow-list, size and time limits)
so the sandbox never needs network access and re-runs are reproducible.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from adpb.contracts import SourceKind, SourceSpec

_SQL_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
MAX_API_BYTES = 5_000_000


class SourceError(ValueError):
    pass


@dataclass
class RawLoad:
    frame: pd.DataFrame
    bad_lines: list[str] = field(default_factory=list)


def normalise_column(name: str) -> str:
    """'Total Amount ($)' -> 'total_amount'; guarantees an identifier the plan can use."""
    out = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    if not out:
        out = "col"
    if out[0].isdigit():
        out = f"c_{out}"
    return out[:63]


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    seen: dict[str, int] = {}
    names = []
    for col in df.columns:
        base = normalise_column(col)
        n = seen.get(base, 0)
        seen[base] = n + 1
        names.append(base if n == 0 else f"{base}_{n + 1}")
    df.columns = names
    return df


def _to_text(value):
    if value is None or (not isinstance(value, list | dict) and pd.isna(value)):
        return None
    if isinstance(value, list | dict):
        return json.dumps(value, default=str)
    return str(value)


def _text_frame(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({c: [_to_text(v) for v in df[c]] for c in df.columns},
                        dtype="string").astype(object).where(lambda d: d.notna(), None)


def resolve_inside(base: Path, relative: str) -> Path:
    """Join ``relative`` onto ``base`` and refuse anything that escapes it."""
    base = base.resolve()
    path = (base / relative).resolve()
    if base != path and base not in path.parents:
        raise SourceError(f"path '{relative}' is outside the sources folder")
    return path


def _records_from_json(data) -> list[dict]:
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        # {"data": [...]} -> the list; any other object is treated as a single record
        data = lists[0] if len(lists) == 1 else [data]
    if not isinstance(data, list):
        raise SourceError("JSON source must be a list of records or an object with one list")
    return [r if isinstance(r, dict) else {"value": r} for r in data]


def _load_json_file(path: Path) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8")
    try:
        records = _records_from_json(json.loads(text))
    except json.JSONDecodeError:  # JSON Lines
        records = _records_from_json([json.loads(line) for line in text.splitlines()
                                      if line.strip()])
    return pd.json_normalize(records, sep="_")


def load_raw(spec: SourceSpec, sources_dir: Path) -> RawLoad:
    sources_dir = Path(sources_dir)
    bad_lines: list[str] = []
    if spec.kind is SourceKind.CSV:
        path = resolve_inside(sources_dir, spec.path)

        def _capture(fields: list[str]):
            bad_lines.append(",".join(fields))
            return None  # skip the line; it is kept in bad_lines for quarantine

        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""],
                         engine="python", on_bad_lines=_capture)
    elif spec.kind is SourceKind.JSON:
        df = _load_json_file(resolve_inside(sources_dir, spec.path))
    elif spec.kind is SourceKind.SQLITE:
        if not _SQL_IDENT.match(spec.table or ""):
            raise SourceError(f"invalid SQLite table name {spec.table!r}")
        path = resolve_inside(sources_dir, spec.path)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # read-only by construction
        try:
            cur = con.execute(f'SELECT * FROM "{spec.table}"')  # noqa: S608 - validated ident
            cols = [d[0] for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols, dtype=object)
        finally:
            con.close()
    elif spec.kind is SourceKind.API:
        snapshot = sources_dir / f"{spec.name}.api.json"
        if not snapshot.exists():
            raise SourceError(f"API snapshot for '{spec.name}' missing; fetch it first")
        df = _load_json_file(snapshot)
    else:  # pragma: no cover - enum is exhaustive
        raise SourceError(f"unsupported source kind {spec.kind}")
    return RawLoad(frame=_normalise_columns(_text_frame(df)), bad_lines=bad_lines)


def fetch_api_snapshot(spec: SourceSpec, sources_dir: Path, allowed_hosts: set[str],
                       client=None) -> Path:
    """Fetch an API source once, on the host, into ``<name>.api.json``.

    Only hosts on the allow-list may be contacted; the response is size-capped.
    """
    host = urlparse(spec.url or "").hostname or ""
    if host not in allowed_hosts:
        raise SourceError(f"host '{host}' is not on the API allow-list {sorted(allowed_hosts)}")
    import httpx

    client = client or httpx.Client(timeout=15, follow_redirects=False)
    resp = client.get(spec.url)
    resp.raise_for_status()
    if len(resp.content) > MAX_API_BYTES:
        raise SourceError(f"API response larger than {MAX_API_BYTES} bytes")
    data = resp.json()
    out = Path(sources_dir) / f"{spec.name}.api.json"
    out.write_text(json.dumps(data))
    return out
