"""Private runs for a public deployment. Off unless ADPB_PRIVATE_RUNS=1.

On a laptop everyone at the table shares one set of runs. On a public server each browser
gets its own data folder (runs, memory and warehouse), so a visitor can only ever list,
open, approve or replay the runs they started. The browser is recognised by a random
token kept in a cookie; the folder name is a hash of it, so a folder name seen on the
server cannot be turned back into a token.

Clearing cookies, or using another browser, starts a visitor afresh: there are no accounts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path

import streamlit as st

from adpb.config import Settings, load_settings

COOKIE = "pb_visitor"
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,64}")
_REMEMBER_JS = """<script>
document.cookie = %s + "=" + %s + "; path=/; max-age=31536000; SameSite=Lax" +
  (location.protocol === "https:" ? "; Secure" : "");
</script>"""


def private_runs() -> bool:
    return os.environ.get("ADPB_PRIVATE_RUNS", "").strip().lower() in ("1", "true", "yes")


def _cookie_token() -> str | None:
    try:
        return st.context.cookies.get(COOKIE)
    except Exception:  # no browser behind this session (tests, bare scripts)
        return None


def visitor_token() -> str | None:
    """This browser's token, made on the first visit. None when runs are shared."""
    if not private_runs():
        return None
    token = st.session_state.get("_visitor") or _cookie_token()
    if not (isinstance(token, str) and TOKEN.fullmatch(token)):
        token = secrets.token_urlsafe(32)
    st.session_state["_visitor"] = token
    return token


def visitor_home(base: Path, token: str) -> Path:
    return base / "visitors" / hashlib.sha256(token.encode()).hexdigest()[:32]


def settings() -> Settings:
    """The settings every page uses: the shared home, or this visitor's own folder."""
    shared = load_settings()
    token = visitor_token()
    return load_settings(home=visitor_home(shared.home, token)) if token else shared


def all_runs_dirs() -> list[Path]:
    """Every runs folder on the server, for the daily spending cap (which is shared)."""
    shared = load_settings()
    return [shared.runs_dir, *sorted((shared.home / "visitors").glob("*/runs"))]


def remember_visitor() -> None:
    """Store the token in the browser so a reload or a later visit finds the same runs."""
    token = visitor_token()
    if token:
        st.html(_REMEMBER_JS % (json.dumps(COOKIE), json.dumps(token)),
                unsafe_allow_javascript=True)
