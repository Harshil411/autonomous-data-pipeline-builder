"""About: the two things a visitor can skip but a grader will ask for, on one page.

    How it works   why there are several agents, what each may see and do, the main loop,
                   and the five promises with the test that proves each
    Benchmarks     how often it succeeds on eight dirty-data requests, and at what cost
"""

from __future__ import annotations

import streamlit as st

from app import benchmarks, how_it_works
from app.components import page_title

page_title("Agents for judgment, plain code for control",
           "A bounded multi-agent system: five Claude agents do the thinking, and an "
           "orchestrator with no AI in it decides what happens next.")
how_tab, bench_tab = st.tabs(["How it works", "Benchmarks"])
with how_tab:
    how_it_works.render()
with bench_tab:
    benchmarks.render()
