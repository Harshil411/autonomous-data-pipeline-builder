"""Warehouse MCP server: the only path to the final database (requirements 13, 20, 21).

Writes happen in two steps:
  1. ``stage_table`` loads a dataset into ``staging__<table>`` and returns a fingerprint
     of the staged rows. Staging tables are not production data.
  2. ``promote`` copies staging into the real table - but only with an approval token
     that the orchestrator issues after a human approves (see adpb.approval).

The database URL and the approval secret come from this server's own environment
(``ADPB_WAREHOUSE_URL``, ``ADPB_APPROVAL_SECRET``) and are never returned by any tool, so
no prompt, code or log ever contains them (requirement 24).

SQLite is built in. PostgreSQL is accepted as a URL but needs the optional ``postgres``
extra; see README.

Run standalone:  python -m adpb.mcp_servers.warehouse
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pandas as pd

from adpb.approval import verify_token
from adpb.mcp_servers.common import MCPServer, ToolError, inside, new_server
from adpb.tools.fingerprint import dataset_fingerprint

_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
META_TABLES = ("_adpb_staging", "_adpb_loads")


def _check_table(name: str) -> str:
    if not _TABLE.match(name or "") or name.startswith(("staging__", "quarantine__", "_adpb")):
        raise ToolError(f"invalid target table name {name!r}")
    return name


def _sqlite_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ToolError("only sqlite:/// URLs are supported without the postgres extra")
    return Path(url.removeprefix("sqlite:///"))


def _json_rows(df: pd.DataFrame) -> list[dict]:
    return [{k: (None if pd.isna(v) else (v if isinstance(v, int | float | bool) else str(v)))
             for k, v in r.items()} for r in df.to_dict("records")]


def build_warehouse_server(url: str, approval_secret: str, allowed_root: Path) -> MCPServer:
    db_path = _sqlite_path(url)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    allowed_root = Path(allowed_root).resolve()
    server = new_server(
        "warehouse",
        "Final data store. Stage first; promotion to a real table needs a human approval "
        "token. Secrets are held by this server and never returned.",
    )

    def connect() -> sqlite3.Connection:
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE IF NOT EXISTS _adpb_staging (tbl TEXT PRIMARY KEY, "
                    "fingerprint TEXT, rows INTEGER, staged_at REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS _adpb_loads (run_id TEXT, tbl TEXT, "
                    "rows INTEGER, fingerprint TEXT, mode TEXT, loaded_at REAL)")
        return con

    def row_count(con, table: str) -> int | None:
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (table,)).fetchone()
        return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if exists else None  # noqa: S608

    @server.tool()
    def stage_table(dataset_path: str, table: str) -> dict:
        """Load a finished dataset into staging__<table>. Does NOT touch production."""
        table = _check_table(table)
        df = pd.read_parquet(inside(allowed_root, dataset_path))
        fingerprint = dataset_fingerprint(df)
        with closing(connect()) as con, con:
            df.to_sql(f"staging__{table}", con, if_exists="replace", index=False)
            con.execute("INSERT OR REPLACE INTO _adpb_staging VALUES (?, ?, ?, ?)",
                        (table, fingerprint, len(df), time.time()))
            existing = row_count(con, table)
        return {"staging_table": f"staging__{table}", "rows": len(df),
                "fingerprint": fingerprint, "columns": list(df.columns),
                "existing_production_rows": existing}

    @server.tool()
    def write_quarantine(paths: list[str], table: str) -> dict:
        """Store quarantined rows (with their _reason) in quarantine__<table>."""
        table = _check_table(table)
        frames = [pd.read_parquet(inside(allowed_root, p)).astype("string") for p in paths]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            {"_reason": pd.Series([], dtype="string")})
        with closing(connect()) as con, con:
            df.to_sql(f"quarantine__{table}", con, if_exists="replace", index=False)
        return {"table": f"quarantine__{table}", "rows": len(df)}

    @server.tool()
    def promote(table: str, run_id: str, approval_token: str, mode: str = "replace") -> dict:
        """Copy staging__<table> into <table>. Requires a human approval token."""
        table = _check_table(table)
        if mode not in ("replace", "append"):
            raise ToolError("mode must be 'replace' or 'append'")
        with closing(connect()) as con:
            staged = con.execute("SELECT fingerprint, rows FROM _adpb_staging WHERE tbl=?",
                                 (table,)).fetchone()
            if not staged:
                raise ToolError(f"nothing staged for '{table}'")
            fingerprint, rows = staged
            if not verify_token(approval_secret, approval_token, run_id, table, fingerprint):
                raise ToolError("approval token is missing or invalid for this run, table and "
                                "staged data; a human must approve the load")
            with con:  # one transaction: the old table is never half-replaced
                if mode == "replace":
                    con.execute(f'DROP TABLE IF EXISTS "{table}"')
                    con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM "staging__{table}"')  # noqa: S608
                else:
                    if row_count(con, table) is None:
                        con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM "staging__{table}" '  # noqa: S608
                                    "WHERE 0")
                    con.execute(f'INSERT INTO "{table}" SELECT * FROM "staging__{table}"')  # noqa: S608
                con.execute("INSERT INTO _adpb_loads VALUES (?, ?, ?, ?, ?, ?)",
                            (run_id, table, rows, fingerprint, mode, time.time()))
            total = row_count(con, table)
        return {"table": table, "rows": rows, "total_rows": total, "mode": mode}

    @server.tool()
    def preview(table: str, limit: int = 10) -> dict:
        """First rows of a table (staging__, quarantine__ or final). At most 50 rows."""
        if not _TABLE.match(table or ""):
            raise ToolError(f"invalid table name {table!r}")
        with closing(connect()) as con:
            if row_count(con, table) is None:
                raise ToolError(f"table '{table}' does not exist")
            df = pd.read_sql_query(f'SELECT * FROM "{table}" LIMIT ?', con,  # noqa: S608
                                   params=(max(1, min(limit, 50)),))
        return {"table": table, "rows": _json_rows(df)}

    @server.tool()
    def describe() -> dict:
        """All tables and their row counts (no credentials, no connection details)."""
        with closing(connect()) as con:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {"tables": {n: row_count(con, n) for n in names if n not in META_TABLES}}

    return server


if __name__ == "__main__":
    build_warehouse_server(
        url=os.environ["ADPB_WAREHOUSE_URL"],
        approval_secret=os.environ["ADPB_APPROVAL_SECRET"],
        allowed_root=Path(os.environ["ADPB_RUNS_DIR"]),
    ).run("stdio")
