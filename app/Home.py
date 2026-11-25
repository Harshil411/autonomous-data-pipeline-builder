"""Streamlit entry point:  uv run streamlit run app/Home.py

Four pages: Build (describe, watch, approve), Past runs (replay), Benchmarks, How it works.
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
PAGES = [
    st.Page("views/build.py", title="Build", default=True),
    st.Page("views/runs.py", title="Past runs"),
    st.Page("views/benchmarks.py", title="Benchmarks"),
    st.Page("views/how_it_works.py", title="How it works"),
]
current = st.navigation(PAGES, position="hidden")

# Our own header: the wordmark on the left, one quiet link per page on the right.
# (Streamlit's built-in top nav sits under its toolbar, which hid it on a 13-inch laptop.)
cols = st.columns([5, 0.7, 1, 1.1, 1.2], vertical_alignment="center")
cols[0].markdown('<span class="wordmark"><i></i>Pipeline Builder</span>', unsafe_allow_html=True)
for col, page in zip(cols[1:], PAGES, strict=True):
    col.page_link(page, label=page.title)
current.run()
