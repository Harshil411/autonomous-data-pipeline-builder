"""Streamlit entry point:  uv run streamlit run app/Home.py

Three pages: Build (describe, watch, approve), Past runs (replay) and About (how it works,
benchmarks).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # make `app.*` importable when Streamlit runs this file
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from app.components import favicon, look_switch, setup_page, wordmark  # noqa: E402

st.set_page_config(page_title="Pipeline Builder", page_icon=favicon(), layout="wide",
                   initial_sidebar_state="collapsed")

setup_page()
PAGES = [
    st.Page("views/build.py", title="Build", default=True),
    st.Page("views/runs.py", title="Past runs"),
    st.Page("views/about.py", title="About"),
]
current = st.navigation(PAGES, position="hidden")

# Our own header: the wordmark on the left, one quiet link per page on the right.
# (Streamlit's built-in top nav sits under its toolbar, which hid it on a 13-inch laptop.)
cols = st.columns([6.4, 0.7, 1, 0.8, 0.85], vertical_alignment="center")
cols[0].markdown(wordmark(), unsafe_allow_html=True)
for col, page in zip(cols[1:4], PAGES, strict=True):
    col.page_link(page, label=page.title)
with cols[4]:
    look_switch()  # Night (dark) or Day (light)
current.run()
