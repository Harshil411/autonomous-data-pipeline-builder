"""Read a data request from a YAML file (the CLI and benchmark format).

    request: Load the customers and orders CSVs, clean them, join, write to the database.
    expected_result: one row per valid order with the customer's name
    target_table: customer_orders
    quality_rules: ["amounts are never negative"]
    budget: {max_usd: 0.50}
    sources:
      - {name: customers, kind: csv, path: data/customers.csv}   # relative to this file
      - {name: orders, kind: csv, path: data/orders.csv}
      - {name: rates, kind: api, url: "http://127.0.0.1:8765/rates.json"}

Returns the `DataRequest` (source paths reduced to file names, because files are copied
into the run's own sources folder) and the list of files to copy.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from adpb.contracts import DataRequest


def load_request_file(path: Path) -> tuple[DataRequest, list[Path]]:
    path = Path(path).resolve()
    spec = yaml.safe_load(path.read_text())
    files: list[Path] = []
    sources = []
    for src in spec.get("sources", []):
        src = dict(src)
        if src.get("path"):
            file = (path.parent / src["path"]).resolve()
            if not file.exists():
                raise FileNotFoundError(f"source file not found: {file}")
            files.append(file)
            src["path"] = file.name
        sources.append(src)
    spec["sources"] = sources
    return DataRequest.model_validate(spec), files
