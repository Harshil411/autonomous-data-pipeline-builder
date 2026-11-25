"""Human approval tokens for writes to production data (requirement 21).

When a person clicks *Approve* in the UI (or answers "y" in the CLI), the orchestrator
issues an HMAC token bound to (run id, target table, fingerprint of the staged data). The
warehouse MCP server holds the same secret in its own environment and refuses to promote a
staging table without a matching token. Consequences:

* no agent can load production data: none has the promote tool, and none knows the secret;
* an approval cannot be replayed onto different data: if the staged rows change, the
  fingerprint changes and the old token stops matching.
"""

from __future__ import annotations

import hashlib
import hmac


def _message(run_id: str, table: str, fingerprint: str) -> bytes:
    return f"{run_id}\x1f{table}\x1f{fingerprint}".encode()


def issue_token(secret: str, run_id: str, table: str, fingerprint: str) -> str:
    return hmac.new(secret.encode(), _message(run_id, table, fingerprint),
                    hashlib.sha256).hexdigest()


def verify_token(secret: str, token: str, run_id: str, table: str, fingerprint: str) -> bool:
    expected = issue_token(secret, run_id, table, fingerprint)
    return hmac.compare_digest(expected, token or "")
