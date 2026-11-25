"""Memory MCP server backed by ChromaDB (requirement 14; Module 6, Lab 3).

Three collections of long-term (semantic/episodic) memory shared across runs:
  * ``pipelines`` - past requests and how their plans turned out (Planner reads);
  * ``fixes``     - error signatures and the fix that made the tests pass (Coder and
                    Debugger read). Only *verified* fixes are written - the orchestrator
                    calls ``remember_fix`` after a patched stage passes, never before
                    (mitigation 8: "stores only working fixes");
  * ``schemas``   - source column lists seen before (Planner reads).

Retrieval is metadata-filtered (e.g. fixes for ``clean`` stages only), the same technique
as Lab 3's ``retrieve(..., where=...)``.

Run standalone:  python -m adpb.mcp_servers.memory   (reads ADPB_MEMORY_DIR)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from adpb.mcp_servers.common import MCPServer, new_server

DIM = 256


class HashEmbedding(EmbeddingFunction[Documents]):
    """Deterministic bag-of-words hashing embedding: offline, instant, test-friendly.

    Used in tests and as a fallback when the ONNX model is unavailable. Similar error
    messages share tokens, so nearest-neighbour search still behaves sensibly.
    """

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        out = []
        for text in input:
            vec = [0.0] * DIM
            for tok in re.findall(r"[a-z0-9_]+", text.lower()):
                h = int(hashlib.md5(tok.encode(), usedforsecurity=False).hexdigest(), 16)
                vec[h % DIM] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out

    @staticmethod
    def name() -> str:
        return "adpb-hash"

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> HashEmbedding:
        return HashEmbedding()


def default_embedding() -> EmbeddingFunction:
    """ChromaDB's local MiniLM ONNX model; falls back to hashing if it cannot load."""
    try:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        ef = DefaultEmbeddingFunction()
        ef(["warm up"])
        return ef
    except Exception:
        return HashEmbedding()


def _id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def build_memory_server(path: Path, embedding: EmbeddingFunction | None = None) -> MCPServer:
    client = chromadb.PersistentClient(path=str(path))
    ef = embedding or default_embedding()
    cols = {name: client.get_or_create_collection(name, embedding_function=ef,
                                                  metadata={"hnsw:space": "cosine"})
            for name in ("pipelines", "fixes", "schemas")}
    server = new_server(
        "memory",
        "Long-term memory of past pipelines, verified error fixes and source schemas.",
    )

    def search(col: str, query: str, k: int, where: dict | None = None) -> list[dict]:
        collection = cols[col]
        count = collection.count()
        if count == 0:
            return []
        res = collection.query(query_texts=[query], n_results=min(k, count), where=where or None)
        out = []
        for meta, dist in zip(res["metadatas"][0], res["distances"][0], strict=True):
            item = dict(meta)
            if "columns" in item:
                item["columns"] = json.loads(item["columns"])
            item["distance"] = round(float(dist), 4)
            out.append(item)
        return out

    @server.tool()
    def search_fixes(error: str, stage_kind: str | None = None, k: int = 3) -> dict:
        """Find verified fixes for errors similar to `error` (optionally same stage kind)."""
        where = {"stage_kind": stage_kind} if stage_kind else None
        return {"results": search("fixes", error[:2000], k, where)}

    @server.tool()
    def remember_fix(error: str, fix: str, stage_kind: str, code_excerpt: str = "") -> dict:
        """Store a fix that made a failing stage pass. Orchestrator only."""
        meta = {"error": error[:1500], "fix": fix[:1500], "stage_kind": stage_kind,
                "code_excerpt": code_excerpt[:1500]}
        cols["fixes"].upsert(ids=[_id(error, fix)], documents=[error[:2000]], metadatas=[meta])
        return {"stored": True}

    @server.tool()
    def search_pipelines(query: str, k: int = 3) -> dict:
        """Find past pipeline requests similar to `query`, with their plan summary."""
        return {"results": search("pipelines", query[:2000], k)}

    @server.tool()
    def remember_pipeline(request: str, summary: str, outcome: str) -> dict:
        """Store a finished pipeline (request, plan summary, outcome). Orchestrator only."""
        meta = {"request": request[:1500], "summary": summary[:2500], "outcome": outcome}
        cols["pipelines"].upsert(ids=[_id(request, summary)], documents=[request[:2000]],
                                 metadatas=[meta])
        return {"stored": True}

    @server.tool()
    def search_schemas(query: str, k: int = 3) -> dict:
        """Find previously seen source schemas related to `query`."""
        return {"results": search("schemas", query[:2000], k)}

    @server.tool()
    def remember_schema(source: str, columns: list[str], description: str = "") -> dict:
        """Store a source's column list. Orchestrator only."""
        meta = {"source": source, "columns": json.dumps(columns[:200]),
                "description": description[:500]}
        doc = f"{source}: {' '.join(columns[:200])} {description}"
        cols["schemas"].upsert(ids=[_id(source, *columns)], documents=[doc], metadatas=[meta])
        return {"stored": True}

    return server


if __name__ == "__main__":
    build_memory_server(Path(os.environ["ADPB_MEMORY_DIR"])).run("stdio")
