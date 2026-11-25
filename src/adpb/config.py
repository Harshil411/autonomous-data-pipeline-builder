"""Settings, read from the environment (and a local .env file in the CLI and UI).

Expected keys (all optional except the model credential for live runs; see .env.example):
  ANTHROPIC_API_KEY        Claude credential (read by the Anthropic SDK itself)
  ADPB_PROVIDER            anthropic (default) | vtarc
  ADPB_MODEL_<ROLE>        override a role's model, e.g. ADPB_MODEL_PLANNER=claude-opus-5-5
  VT_ARC_BASE_URL / VT_ARC_API_KEY / VT_ARC_MODEL   the VT ARC fallback
  ADPB_HOME                where runs, memory and the warehouse live (default ./var)
  ADPB_WAREHOUSE_URL       sqlite:///... (default <home>/warehouse.db)
  ADPB_APPROVAL_SECRET     HMAC secret for approval tokens (generated on first use)
  ADPB_SANDBOX             docker (default) | local (tests only, no isolation)
  ADPB_API_ALLOWED_HOSTS   comma-separated hosts API sources may be fetched from
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLES = ("planner", "coder", "tester", "debugger", "validator")


@dataclass(frozen=True)
class Settings:
    home: Path
    provider: str = "anthropic"
    sandbox: str = "docker"
    embeddings: str = "default"
    warehouse_url: str = ""
    approval_secret: str = ""
    sample_rows: int = 300
    models: dict[str, str] = field(default_factory=dict)
    vt_arc_base_url: str = "https://llm-api.arc.vt.edu/api/v1"
    vt_arc_model: str = "vt-arc-llm"
    api_allowed_hosts: frozenset[str] = frozenset()

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def warehouse_path(self) -> Path:
        return Path(self.warehouse_url.removeprefix("sqlite:///"))


def _approval_secret(home: Path, env: Mapping[str, str]) -> str:
    if env.get("ADPB_APPROVAL_SECRET"):
        return env["ADPB_APPROVAL_SECRET"]
    path = home / ".approval_secret"
    if not path.exists():
        home.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def load_settings(env: Mapping[str, str] | None = None, home: Path | None = None,
                  **overrides) -> Settings:
    env = os.environ if env is None else env
    home = Path(home or env.get("ADPB_HOME") or REPO_ROOT / "var").resolve()
    models = {r: env[f"ADPB_MODEL_{r.upper()}"] for r in ROLES
              if env.get(f"ADPB_MODEL_{r.upper()}")}
    values = dict(
        home=home,
        provider=env.get("ADPB_PROVIDER", "anthropic"),
        sandbox=env.get("ADPB_SANDBOX", "docker"),
        embeddings=env.get("ADPB_EMBEDDINGS", "default"),
        warehouse_url=env.get("ADPB_WAREHOUSE_URL", f"sqlite:///{home / 'warehouse.db'}"),
        approval_secret=_approval_secret(home, env),
        models=models,
        vt_arc_base_url=env.get("VT_ARC_BASE_URL", "https://llm-api.arc.vt.edu/api/v1"),
        vt_arc_model=env.get("VT_ARC_MODEL", "vt-arc-llm"),
        api_allowed_hosts=frozenset(h.strip() for h in env.get("ADPB_API_ALLOWED_HOSTS", "")
                                    .split(",") if h.strip()),
    )
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values)
