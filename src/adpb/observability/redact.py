"""Secret redaction for everything that is logged or shown (requirement 24).

Secrets live only in environment variables that the MCP servers read. As defence in depth,
every event written to the run log passes through a `Redactor` that masks:
  1. the literal values of sensitive environment variables (names containing KEY, TOKEN,
     SECRET, PASSWORD, PASS, DSN or CREDENTIAL), and
  2. anything shaped like a well-known credential (Anthropic/OpenAI keys, GitHub tokens,
     passwords embedded in database URLs), even if we never saw the value.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

MASK = "[REDACTED]"
MIN_SECRET_LEN = 6  # shorter values ("1", "on") would shred ordinary text
_SENSITIVE_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|DSN|CREDENTIAL)", re.I)
_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]
# scheme://user:password@host  -> keep user and host, mask the password
_URL_PASSWORD = re.compile(r"([a-z][a-z0-9+.\-]*://[^:/@\s]+:)([^@\s]+)(@)", re.I)


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()):
        # Longest first, so a secret that contains another secret is fully masked.
        self._secrets = sorted({s for s in secrets if s and len(s) >= MIN_SECRET_LEN},
                               key=len, reverse=True)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Redactor:
        env = os.environ if env is None else env
        return cls(v for k, v in env.items() if _SENSITIVE_NAME.search(k))

    def text(self, value: str) -> str:
        for s in self._secrets:
            value = value.replace(s, MASK)
        for pat in _PATTERNS:
            value = pat.sub(MASK, value)
        return _URL_PASSWORD.sub(rf"\1{MASK}\3", value)

    def obj(self, value: Any) -> Any:
        """Redact strings anywhere inside dicts, lists and tuples."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {k: self.obj(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.obj(v) for v in value]
        return value
