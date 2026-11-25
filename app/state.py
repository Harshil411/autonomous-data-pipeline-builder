"""Process-wide objects shared by all Streamlit pages and sessions."""

import streamlit as st

from app.runner import RunManager


@st.cache_resource
def manager() -> RunManager:
    """One RunManager per server process, so a run started on one page (or by one browser
    tab) can be watched and approved from any other."""
    return RunManager()
