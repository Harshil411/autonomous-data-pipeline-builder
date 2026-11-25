"""Who may call which tool (requirement 15; mitigation 13 "per-agent tool lists").

Two lists per role:
  * ``ROLE_TOOLS``  - every tool the role's *code* may call through the gateway;
  * ``LLM_VISIBLE`` - the subset offered to the role's *model* as callable tools.

Keeping them apart matters: the Tester's code runs the sandbox, but the Tester's model is
never handed a sandbox tool - it only writes assertions. Production writes
(``warehouse.promote``) belong to the orchestrator alone, and still need a human token.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ROLE_TOOLS: dict[str, frozenset[str]] = {
    "planner": frozenset({"memory.search_pipelines", "memory.search_schemas"}),
    "coder": frozenset({"memory.search_fixes"}),
    "debugger": frozenset({"memory.search_fixes", "sandbox.run_command"}),
    "tester": frozenset({"sandbox.run_stage"}),
    "validator": frozenset({"sandbox.run_quality", "warehouse.preview"}),
    "orchestrator": frozenset({
        "artifacts.write_text", "artifacts.read_text", "artifacts.list_artifacts",
        "artifacts.store_dataset", "artifacts.latest_dataset",
        "git.git_add", "git.git_commit", "git.git_log", "git.git_show", "git.git_status",
        "git.git_diff_staged",
        "sandbox.run_stage",
        "warehouse.stage_table", "warehouse.write_quarantine", "warehouse.promote",
        "warehouse.preview", "warehouse.describe",
        "memory.remember_fix", "memory.remember_pipeline", "memory.remember_schema",
        "memory.search_pipelines",
    }),
}

LLM_VISIBLE: dict[str, frozenset[str]] = {
    "planner": frozenset({"memory.search_pipelines", "memory.search_schemas"}),
    "coder": frozenset({"memory.search_fixes"}),
    "debugger": frozenset({"memory.search_fixes", "sandbox.run_command"}),
    "tester": frozenset(),
    "validator": frozenset(),
}


@dataclass(frozen=True)
class ToolPolicy:
    role_tools: dict[str, frozenset[str]] = field(default_factory=lambda: dict(ROLE_TOOLS))
    llm_visible: dict[str, frozenset[str]] = field(default_factory=lambda: dict(LLM_VISIBLE))

    def allows(self, role: str, server: str, tool: str) -> bool:
        return f"{server}.{tool}" in self.role_tools.get(role, frozenset())

    def visible(self, role: str) -> frozenset[str]:
        return self.llm_visible.get(role, frozenset()) & self.role_tools.get(role, frozenset())
