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
PAGES = [
    st.Page("views/build.py", title="Build a pipeline", default=True),
    st.Page("views/runs.py", title="Runs and replay"),
    st.Page("views/benchmarks.py", title="Benchmarks"),
    st.Page("views/how_it_works.py", title="How it works"),
]
current = st.navigation(PAGES, position="hidden")

# Our own header band: the wordmark plus one link per page (Streamlit's top nav sits
# under its toolbar, which hid it on a 13-inch laptop).
cols = st.columns([2.2, 1.2, 1.2, 1, 1], vertical_alignment="center")
cols[0].markdown('<span class="wordmark">Pipeline Builder</span>', unsafe_allow_html=True)
for col, page in zip(cols[1:], PAGES, strict=True):
    col.page_link(page, label=page.title)
st.markdown('<div class="band"></div>', unsafe_allow_html=True)
current.run()
