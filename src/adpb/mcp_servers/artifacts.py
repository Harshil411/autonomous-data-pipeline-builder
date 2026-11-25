"""Filesystem MCP server for run artifacts, with versioned names (requirement 11).

Scoped to one run's workspace. Every write creates a new version (``clean.v1.py``,
``clean.v2.py``, ...); nothing is ever overwritten and there is deliberately no delete
tool, so the full history of every intermediate output survives for audit and debugging.

Run standalone:  python -m adpb.mcp_servers.artifacts   (reads ADPB_RUN_DIR)
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pyarrow.parquet as pq

from adpb.mcp_servers.common import MCPServer, ToolError, inside, new_server

_VERSIONED = re.compile(r"^(?P<stem>.+)\.v(?P<n>\d+)(?P<ext>\.[A-Za-z0-9]+)$")
_DATASET = re.compile(r"^(raw\.)?[a-z][a-z0-9_]{0,62}$")
KINDS = {"data": "data", "sample": "data/samples", "quarantine": "data/quarantine"}


def _split(name: str) -> tuple[str, str]:
    """'clean.py' -> ('clean', '.py');  'raw.orders.parquet' -> ('raw.orders', '.parquet')."""
    stem, dot, ext = name.rpartition(".")
    return (stem, f".{ext}") if dot and stem else (name, "")


def _versions(folder: Path, stem: str, ext: str) -> list[int]:
    pattern = re.compile(rf"^{re.escape(stem)}\.v(\d+){re.escape(ext)}$")
    if not folder.exists():
        return []
    return sorted(int(m.group(1)) for p in folder.iterdir() if (m := pattern.match(p.name)))


def next_version_path(root: Path, rel: str) -> tuple[Path, int]:
    target = inside(root, rel)
    stem, ext = _split(target.name)
    n = (max(_versions(target.parent, stem, ext), default=0)) + 1
    return target.parent / f"{stem}.v{n}{ext}", n


def latest_version_path(root: Path, rel: str) -> tuple[Path, int]:
    target = inside(root, rel)
    if m := _VERSIONED.match(target.name):
        if not target.exists():
            raise ToolError(f"artifact '{rel}' not found")
        return target, int(m.group("n"))
    stem, ext = _split(target.name)
    versions = _versions(target.parent, stem, ext)
    if not versions:
        raise ToolError(f"no versions of '{rel}' exist yet")
    return target.parent / f"{stem}.v{versions[-1]}{ext}", versions[-1]


def build_artifacts_server(run_dir: Path) -> MCPServer:
    root = Path(run_dir).resolve()
    server = new_server(
        "artifacts",
        "Versioned file storage for one pipeline run. Writes never overwrite; there is no "
        "delete. Paths are relative to the run folder.",
    )

    @server.tool()
    def write_text(path: str, content: str) -> dict:
        """Write a text artifact (code, tests, plan) as a NEW version of `path`.

        Example: path="code/clean_orders.py" creates code/clean_orders.v1.py, then .v2.py...
        """
        target, n = next_version_path(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target.relative_to(root)), "version": n, "bytes": len(content)}

    @server.tool()
    def read_text(path: str) -> dict:
        """Read an artifact. Without a .vN suffix the latest version is returned."""
        target, n = latest_version_path(root, path)
        return {"path": str(target.relative_to(root)), "version": n,
                "content": target.read_text(encoding="utf-8")}

    @server.tool()
    def list_artifacts(prefix: str = "") -> dict:
        """List artifact paths under an optional folder prefix (e.g. "code")."""
        base = inside(root, prefix) if prefix else root
        if not base.exists():
            return {"paths": []}
        paths = sorted(str(p.relative_to(root)) for p in base.rglob("*")
                       if p.is_file() and ".git" not in p.parts and "sandbox" not in p.parts)
        return {"paths": paths}

    @server.tool()
    def store_dataset(name: str, source_path: str, kind: str = "data") -> dict:
        """Copy a parquet file produced in this run into versioned dataset storage.

        kind: "data" (stage output), "sample" (Tester sample) or "quarantine".
        """
        if not _DATASET.match(name):
            raise ToolError(f"invalid dataset name {name!r}")
        if kind not in KINDS:
            raise ToolError(f"kind must be one of {sorted(KINDS)}")
        src = inside(root, source_path)
        if not src.exists():
            raise ToolError(f"source file '{source_path}' does not exist")
        target, n = next_version_path(root, f"{KINDS[kind]}/{name}.parquet")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        rows = pq.ParquetFile(target).metadata.num_rows
        return {"path": str(target.relative_to(root)), "version": n, "rows": rows}

    @server.tool()
    def latest_dataset(name: str, kind: str = "data") -> dict:
        """Path of the newest version of a dataset."""
        target, n = latest_version_path(root, f"{KINDS.get(kind, 'data')}/{name}.parquet")
        return {"path": str(target.relative_to(root)), "version": n}

    return server


if __name__ == "__main__":
    build_artifacts_server(Path(os.environ["ADPB_RUN_DIR"])).run("stdio")
