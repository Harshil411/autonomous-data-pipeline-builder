"""Streamlit entry point:  uv run streamlit run app/Home.py

Four pages: build a pipeline (live), past runs (with replay), benchmarks, and how it works.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # make `app.*` importable when Streamlit runs this file
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="Pipeline Builder", layout="wide",
                   initial_sidebar_state="collapsed")

from app.components import setup_page  # noqa: E402

setup_page()
pages = st.navigation([
    st.Page("pages/build.py", title="Build a pipeline", default=True),
    st.Page("pages/runs.py", title="Runs and replay"),
    st.Page("pages/benchmarks.py", title="Benchmarks"),
    st.Page("pages/how_it_works.py", title="How it works"),
], position="top")
pages.run()
