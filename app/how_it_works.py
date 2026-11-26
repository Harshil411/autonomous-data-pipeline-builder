"""How it works: the design, for visitors and for Q&A at the table.

Shown as the "How it works" tab of the About page (app/views/about.py)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.theme import THEMES, active_mode


def arch(t: dict[str, str]) -> str:
    """The architecture diagram (Graphviz), coloured from the active look's tokens:
    maroon for the person, a strong outline for the Orchestrator."""
    return f"""
digraph G {{
  rankdir=LR; bgcolor="transparent"; nodesep=0.35; ranksep=0.55;
  node [shape=box, style="rounded,filled", fillcolor="{t['surface']}", color="{t['line-3']}",
        fontcolor="{t['text']}", fontname="Helvetica", fontsize=12, margin="0.18,0.08"];
  edge [color="{t['faint']}", fontcolor="{t['muted']}", fontname="Helvetica", fontsize=10];
  you [label="You\\n(request, approval)", fillcolor="{t['gate-2']}",
       color="{t['maroon-line']}", fontcolor="{t['gate-text']}"];
  orch [label="Orchestrator\\nplain code, no LLM\\norder, retries, budget",
        color="{t['text']}", penwidth=2];
  subgraph cluster_agents {{ label="LLM agents (Claude)"; color="{t['line-2']}";
    fontcolor="{t['muted']}"; fontname="Helvetica";
    planner [label="Planner"]; coder [label="Stage Coder"]; tester [label="Tester"];
    debugger [label="Debugger"]; validator [label="Validator"]; }}
  subgraph cluster_mcp {{ label="MCP servers (the only way to reach a tool)";
    color="{t['line-2']}"; fontcolor="{t['muted']}"; fontname="Helvetica";
    sandbox [label="sandbox\\nDocker, no network"]; memory [label="memory\\nChromaDB"];
    artifacts [label="artifacts\\nversioned files"]; git [label="git\\nmcp-server-git"];
    warehouse [label="warehouse\\nSQLite, staging + gate"]; }}
  you -> orch [label="request"]; orch -> you [label="approve?"];
  orch -> planner; orch -> coder; orch -> tester; orch -> debugger; orch -> validator;
  planner -> memory [style=dashed]; coder -> memory [style=dashed];
  debugger -> memory [style=dashed]; debugger -> sandbox [style=dashed];
  tester -> sandbox; validator -> sandbox;
  orch -> artifacts; orch -> git; orch -> warehouse [label="only writer"];
}}
"""


AGENTS = [
    ("Planner", "the whole request, source profiles", "memory search",
     "JSON plan: stages with input/output schemas", "write code or touch data"),
    ("Stage Coder", "one stage's spec, its input schemas and 5 sample rows",
     "memory search", "code for that stage only", "see other stages or the tests"),
    ("Tester", "the stage spec, before any code exists", "sandbox (from code, not the model)",
     "3-6 assertions, then runs them on a sample and on all the data", "see the code"),
    ("Debugger", "failing code, the exact error, what was already tried",
     "memory search, allow-listed shell in the sandbox", "a full replacement for the code",
     "change a test or a schema"),
    ("Validator", "output profiles and source profiles", "quality checks in the sandbox",
     "proposed rules; code decides pass or fail", "judge the data itself"),
    ("Orchestrator", "state, budgets, results", "artifacts, git, warehouse, memory writes",
     "order, retries, escalation, the report", "call an LLM - it is plain code"),
]

PROMISES = [
    ("write to your DB without your OK", "staging table + HMAC approval token bound to the "
     "data's fingerprint; no agent has the promote tool", "test_1_it_wont_write_to_your_db_..."),
    ("see your passwords or API keys", "secrets live only in MCP server environments; every "
     "log line is redacted", "test_2_it_wont_see_your_passwords_or_api_keys"),
    ("reach the internet from its sandbox", "docker --network none, read-only root, no "
     "capabilities, non-root user", "test_3_it_wont_reach_the_internet_from_its_sandbox"),
    ("retry the same fix forever", "3 attempts per stage, then one replan, then a clean "
     "failure with a report", "test_4_it_wont_retry_the_same_fix_forever"),
    ("run past its time or cost budget", "checked in code before every model call",
     "test_5_it_wont_run_past_its_cost_budget"),
]


def render() -> None:
    st.markdown("## Why more than one agent")
    st.markdown(
        "Each job needs different information and different permissions. The planner needs "
        "the whole request; the coder needs only one stage and its schema. Tests are written "
        "by a different agent than the code, before the code exists, so the code cannot grade "
        "itself. Pass or fail is always decided by running code in a sandbox, never by a "
        "model agreeing with another model.")
    st.graphviz_chart(arch(THEMES[active_mode()]), width="stretch")
    st.markdown("## What each agent sees, uses and cannot do")
    st.dataframe(pd.DataFrame(AGENTS, columns=["agent", "sees", "tools", "returns", "cannot"]),
                 hide_index=True, width="stretch")
    st.markdown("## The main loop")
    st.markdown(
        "1. Load every source as text and profile it (types, nulls, 5 sample rows).\n"
        "2. The Planner proposes stages with explicit schemas; code rejects plans with "
        "unknown inputs or cycles.\n"
        "3. For each stage: the Tester writes assertions, the Coder writes code, the sandbox "
        "runs it on a sample and then on all the data. On failure the Debugger patches the "
        "code - up to 3 attempts - then the Planner revises the plan once.\n"
        "4. The Validator's rules run as code. A failed check goes back to the stage that "
        "produced the data.\n"
        "5. The result is staged. Nothing reaches the real table until you approve.\n"
        "6. Every passing stage is a git commit, so the whole pipeline can be re-run from "
        "scratch with no model and produce the same fingerprint.")
    st.markdown("## Five things it won't do, and the test that proves each")
    st.dataframe(pd.DataFrame(PROMISES, columns=["it won't", "because", "test"]),
                 hide_index=True, width="stretch")
