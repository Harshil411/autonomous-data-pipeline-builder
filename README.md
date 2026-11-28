# Autonomous Data Pipeline Builder

A multi-agent system that turns a plain-English data request ("load these CSVs, clean them,
join them and write the result to the database") into a **tested, versioned data pipeline**,
and loads the result only after a person approves it.

ADS 5984 *Agentic AI in Practice*, Virginia Tech. Team: Harshil Agrawal, Siddharth Pal,
Vasu Patel.

**Live demo:** https://pipeline-builder.westus2.cloudapp.azure.com (open to anyone, under a
small daily spending cap; [how it is hosted](deploy/README.md)).

## What it does

1. You describe the data you need and point at the sources (CSV, JSON, SQLite or an API).
2. The **Planner** splits the job into stages, each with an explicit output schema.
3. For every stage the **Tester** writes assertions *before any code exists*, the **Stage
   Coder** writes the code, and a locked-down Docker sandbox runs it on a sample and then on
   all the data. If it fails, the **Debugger** patches the code (never the tests), up to
   three attempts; then the Planner revises the plan once.
4. The **Validator** proposes quality rules; deterministic code runs them. A failed check
   goes back to the stage that produced the data.
5. The result waits in a staging table. Nothing reaches the real table until you approve.
6. Every passing stage is a git commit, so the pipeline can be re-run from scratch with no
   model and produce byte-for-byte the same result.

The **Orchestrator** that drives all of this is plain code (a LangGraph state machine). It
owns order, retries, budgets and escalation, and it is the only component that can write to
the final database. Agents get judgment; code keeps control.

## Quick start

Requirements: macOS or Linux, Python 3.12, [uv](https://docs.astral.sh/uv/), Docker
(Docker Desktop, OrbStack or Colima), and a Claude API key (or VT ARC access).

```bash
# 1. Install
uv sync

# 2. Docker for the sandbox (macOS example with Colima), then build the sandbox image
brew install colima docker && colima start --cpu 4 --memory 6
uv run adpb build-sandbox

# 3. Configure: copy the template and add your key
cp .env.example .env        # then set ANTHROPIC_API_KEY=...

# 4. Check everything is in place
uv run adpb doctor

# 5a. The web app (the showcase demo)
uv run streamlit run app/Home.py

# 5b. Or the command line
uv run adpb run benchmarks/requests/shop_join.yaml     # builds, then asks before loading
```

On macOS with Colima, keep the project inside your home folder: Colima only shares the home
folder with its VM, so the sandbox could not see files elsewhere.

### Expected `.env` keys

| key | needed | purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes, for Claude | model credential (read by the Anthropic SDK) |
| `ADPB_PROVIDER` | no | `anthropic` (default) or `vtarc` |
| `VT_ARC_API_KEY`, `VT_ARC_BASE_URL`, `VT_ARC_MODEL` | for VT ARC | free fallback inside the VT network |
| `ADPB_MODEL_PLANNER` ... `ADPB_MODEL_VALIDATOR` | no | override a role's model |
| `ADPB_HOME` | no | where runs, memory and the warehouse live (default `./var`) |
| `ADPB_WAREHOUSE_URL` | no | `sqlite:///...` (default `var/warehouse.db`) |
| `ADPB_APPROVAL_SECRET` | no | HMAC secret for approval tokens (generated if unset) |
| `ADPB_API_ALLOWED_HOSTS` | for API sources | hosts the API ingester may contact |
| `ADPB_SANDBOX` | no | `docker` (default). `local` is for tests only and has no isolation |

## The app

The Streamlit app follows one journey, shown as three steps at the top of the page:

1. **Describe.** One request box and one button. Six buttons underneath load a ready-made
   example (web-shop orders, monthly revenue, sensor readings, CRM contacts, course grades)
   or let you upload your own CSV, JSON or SQLite files. An example is shown as a goal line
   and bullets; pressing Enter on a bullet starts the next one. Unedited, it is sent to the
   agents in its original wording. Expected result, table name, quality rules and the
   spending limit sit behind "More options".
2. **Build.** One headline says who is working and what they may not do ("The Debugger is
   fixing clean_orders. It may change the code, never the test."). Under it, the live
   graph: sources, stages, your approval and the database on one line, with the
   Orchestrator and the five agents below. The stage and the agent being worked on glow,
   with moving lines from the Orchestrator; passed stages turn green; the approval gate
   stays locked.
3. **Approve.** The evidence (tests passed, quality checks passed, rows set aside), a
   preview of the staged rows, and two buttons. After approval the gate opens, the database
   lights up and the first loaded rows are shown.

Everything else (the activity log, each stage's tests and code, the quality checks, cost
and limits, the written report) is one click away in a closed **Details** section.

| page | what it is for |
|---|---|
| Build | the journey above, live |
| Past runs | pick any recorded run; replay it at 1x to 8x, decide a waiting run, resume a failed one, or rebuild a finished one with no model |
| About | "How it works" (why several agents, what each may see and do, the five promises and the test behind each) and "Benchmarks" |

Things worth knowing:

* **Two looks.** Night (dark, the default) and Day (light); the button at the top right
  switches, and the browser remembers the choice. Both come from one stylesheet
  (`app/theme.py`): each look is a set of named colours, and no rule hard-codes a colour.
* **A reload never loses a run.** The run on screen is kept in the address bar
  (`?run=...`), so refreshing, or switching looks, comes back to it.
* **Live and replay are the same code.** The UI holds no pipeline state. `app/viewmodel.py`
  turns the run's event log into a `RunView`; live mode passes every event so far, replay
  passes the first *n*. The booth fallback therefore cannot drift from the real thing.
* **No internet needed.** Fonts (Geist, Geist Mono) are bundled in `app/static/`.

## Commands

| command | what it does |
|---|---|
| `adpb run REQUEST.yaml [--yes]` | build a pipeline; ask before loading |
| `adpb approve RUN_ID` / `adpb reject RUN_ID --note ...` | decide a paused run |
| `adpb resume RUN_ID` | rebuild only the failed stage and what depends on it |
| `adpb rerun RUN_ID` | re-run from scratch with the committed code and **no model**; compares fingerprints |
| `adpb status [RUN_ID]`, `adpb report RUN_ID` | inspect runs |
| `adpb replay RUN_ID` | print the recorded event stream |
| `adpb bench [--repeat N]` | run the 8-request benchmark suite (calls the model; costs money) |

A request file looks like `benchmarks/requests/shop_join.yaml`. One of the app's examples,
`course_grades.yaml`, is a class roster and a gradebook export with made-up students, for
instructors who want to see their own kind of data. It is not part of the benchmark suite.

## Architecture

```
            you ──request──▶ Orchestrator (LangGraph, plain code) ◀──approve── you
                                  │ order · retries · budget · escalation
      ┌───────────┬───────────┬───┴───────┬────────────┐
   Planner   Stage Coder    Tester     Debugger     Validator      (Claude)
      │           │           │           │            │
      └───────────┴───── ToolGateway: per-role allow-list, every call logged ─────┘
                                  │ MCP
     sandbox (Docker)   artifacts (versioned files)   git   memory (ChromaDB)   warehouse
```

| agent | sees | tools | cannot |
|---|---|---|---|
| Planner | whole request, source profiles | memory search | write code, touch data |
| Stage Coder | one stage's spec, input schemas, 5 sample rows | memory search | see other stages or the tests |
| Tester | the stage spec, before code exists | sandbox (from code, not the model) | see the code |
| Debugger | failing code, exact error, earlier attempts | memory search, allow-listed shell | change a test or a schema |
| Validator | output and source profiles | quality checks in the sandbox | decide pass/fail itself |

UML diagrams: [`docs/uml/uml_diagrams.pdf`](docs/uml/uml_diagrams.pdf) holds three class
diagrams (agents, contracts, the front-end) and four sequence diagrams (the main loop,
building one stage, quality routing and approval, the visitor's journey through the app).
Sources are in `docs/uml/*.mmd`; rebuild with `uv run python scripts/build_uml_html.py`,
then print `docs/uml/uml.html` to PDF (17 x 11 inches).

### Repository layout

```
src/adpb/
  contracts.py          typed hand-offs between agents (Pydantic)
  orchestrator/         engine.py (the LangGraph graph), rerun.py, sampling.py
  agents/               base.py (bounded loop), planner, coder, tester, debugger, validator, prompts
  llm/                  Claude, VT ARC (OpenAI-compatible) and scripted clients
  mcp_servers/          artifacts, warehouse, memory, sandbox (+ official mcp-server-git)
  sandbox/              Docker runner + shell allow-list; local runner for tests
  sandbox_runtime/      harness, schema conform, quality checks (runs inside the container)
  observability/        event log, budget, pricing, redaction, run report
  tools/                gateway, role allow-lists, source loader, profiler, fingerprint
  runtime.py cli.py config.py benchmark.py workspace.py approval.py
app/
  Home.py               page shell: header, logo, links, the Night/Day switch
  viewmodel.py          event log -> RunView; headline, journey step, proofs (pure functions)
  graph.py              the live graph, one SVG drawn from a RunView
  components.py         draws a run: headline, approval gate, graph, result, Details
  theme.py              both looks as named colours, and the one stylesheet that uses them
  prompts.py            shows an example request as bullets, sends the original text
  runner.py state.py    runs pipelines in background threads, one manager per server
  how_it_works.py benchmarks.py   the two tabs of the About page
  views/                build.py, runs.py, about.py (the three pages)
  static/               bundled fonts
benchmarks/             8 benchmark requests + a course-grades example, fixture generator, results
tests/                  unit / integration / e2e / ui
```

## Testing

The project was built test-first: every module's tests were written and seen failing before
the code (one exception, the five agent classes, is noted in the history). The full suite runs
**offline with no API key** using a scripted model.

```bash
uv run pytest                      # everything except live model calls (~1-2 min)
uv run pytest tests/unit           # fast unit tests
uv run pytest -m docker            # real-container isolation tests
uv run pytest -m live              # calls the real model (costs money)
```

| layer | what it proves |
|---|---|
| unit | contracts and plan validation, budgets, redaction, pricing, every quality rule, schema conformance and quarantine, harness, profiler, shell allow-list, model clients against a fake SDK, the agent loop |
| integration | each MCP server through a real MCP client (official Git server over stdio); the gateway's least-privilege rules; **real Docker isolation** (no network, host invisible, read-only root, non-root, timeout kill, memory cap); 11 whole-pipeline scenarios on the LangGraph graph; reproducible re-run; CLI; benchmark scoring |
| e2e | one named test per flyer promise; a full run over stdio MCP servers and the Docker sandbox |
| ui | the view model, the live graph (including runs that stop early and very long names), the headline; both looks (same colour names, WCAG contrast, no hard-coded colours); example requests shown as bullets but sent unchanged; Streamlit pages via `AppTest`, including a real approval click that loads the warehouse |

## Requirements traceability

| # | requirement | where | evidence |
|---|---|---|---|
| 1 | NL request + sources + expected result | `contracts.DataRequest`, `requests_io.py` | `test_request_files_resolve_sources_relative_to_the_file` |
| 2-3 | plan as JSON, stages with linked schemas | `contracts.PipelinePlan`, `agents/planner.py` | `tests/unit/test_contracts.py` |
| 4 | coder writes SQL or Python | `agents/coder.py`, harness (`duckdb` for SQL) | `test_sql_stages_run_on_duckdb...` |
| 5 | isolated execution, no network, limits | `sandbox/docker_runner.py`, `sandbox/Dockerfile` | `tests/integration/test_docker_sandbox.py` |
| 6 | tester runs on a sample, checks schema + assertions | `agents/tester.py`, `orchestrator/sampling.py` | `test_tester_never_sees_code...` |
| 7 | debugger, 3 retries | `agents/debugger.py`, `engine.build_stage` | `test_failing_stage_is_debugged_until_it_passes...` |
| 8 | escalate to the planner | `engine.replan` | `test_exhausted_retries_escalate_to_a_replan...` |
| 9 | quality checks (nulls, dupes, types, ranges, rows, keys) | `sandbox_runtime/checks.py` | `tests/unit/test_checks.py` |
| 10 | rules from request or data; failure goes to its stage | `agents/validator.py`, `engine.validate` | `test_quality_failure_is_routed_back...` |
| 11 | versioned intermediate outputs via Filesystem MCP | `mcp_servers/artifacts.py` | `TestArtifacts` |
| 12 | code in git via Git MCP | official `mcp-server-git` | `test_every_role_is_logged_and_every_passing_stage_is_a_commit` |
| 13 | final data in SQLite (PostgreSQL planned) | `mcp_servers/warehouse.py` | `TestWarehouse` |
| 14 | ChromaDB memory of pipelines, fixes, schemas | `mcp_servers/memory.py` | `TestMemory` |
| 15 | tools only via MCP, least privilege | `tools/gateway.py`, `tools/registry.py` | `tests/integration/test_gateway.py` |
| 16 | shell allow-list, sandbox only | `docker_runner.ShellPolicy` | `tests/unit/test_shell_policy.py` |
| 17 | schema + sample + stats, never the full data | `tools/profiler.py` | `test_no_full_dataset_and_no_secret_ever_reaches_a_prompt` |
| 18 | single-command reproducible re-run | `orchestrator/rerun.py`, `adpb rerun` | `test_rerun_reproduces_the_same_output_without_any_model` |
| 19 | rerun only the failed stage and downstream | `engine.restore_failed_run`, `adpb resume` | `test_resume_reruns_only_the_failed_stage...` |
| 20 | bad source data never fails the run; quarantine | `tools/sources.py`, `sandbox_runtime/conform.py` | `tests/unit/test_conform.py` |
| 21 | human approval before production writes | `approval.py`, warehouse `promote` | `test_1_it_wont_write_to_your_db_without_your_ok` |
| 22 | per-run log of agents, tools, tokens, cost | `observability/events.py` | `TestEventLog` |
| 23 | time, cost and token limits end the run cleanly | `observability/budget.py` | `test_5_it_wont_run_past_its_cost_budget` |
| 24 | secrets only in MCP env, never in prompts or logs | `runtime._servers`, `observability/redact.py` | `test_2_it_wont_see_your_passwords_or_api_keys` |
| 25 | run report | `observability/report.py` | `test_happy_path_pauses_for_approval_then_loads` |
| 26 | benchmark suite | `benchmark.py`, `benchmarks/suite.yaml` | `test_benchmark_measures_success_retries_time_and_cost` |

## Design choices, briefly

* **Bounded multi-agent, not autonomous.** Agents decide *how* to write and fix code;
  code decides *when* to stop, retry, escalate or write. Every loop has two exits: success
  or a hard cap (Module 3).
* **External verification over agreement.** Pass/fail comes from running code in a sandbox,
  never from one model approving another (Modules 4 and 13).
* **Contracts at every seam.** Agents exchange Pydantic objects; a plan with a cycle or an
  unknown input is rejected in code and sent back for repair (Modules 5 and 11).
* **No sampling parameters.** Current Claude models reject `temperature`; reproducibility
  comes from freezing the code that passed and re-running it without the model.
* **Least privilege.** The Tester's model has no tools; no agent can promote to
  production; the approval token is bound to the fingerprint of the exact staged rows.

## Known limits

* PostgreSQL is accepted by the design but only SQLite is implemented in the warehouse server.
* Datasets are expected to fit in memory (the requirements put multi-GB data out of scope).
* The approval secret lives with the orchestrator and the warehouse server; a production
  deployment would keep it in a secret manager.
