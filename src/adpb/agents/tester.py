"""Tester agent: writes a stage's tests from its spec, then runs code against them (req 6).

Independence is the point (mitigation 5, "self-grading"): the Tester writes its assertions
from the specification *before any code exists* and never sees the code. The assertions
are then executed in the sandbox by deterministic code - first on a small sample, then on
the full data. The Tester's model has no tools; the Tester's *code* may call
``sandbox.run_stage`` and nothing else.
"""

from __future__ import annotations

import ast
from pathlib import Path

from adpb.agents.base import Agent, render_profile, render_schema
from adpb.agents.prompts import TESTER
from adpb.contracts import ExecutionResult, StageCode, StageSpec, StageTests


class Tester(Agent):
    __test__ = False  # not a pytest test class
    role = "tester"
    system_prompt = TESTER

    def write_tests(self, stage: StageSpec, input_profiles: dict[str, dict],
                    broken: str | None = None) -> StageTests:
        lines = [
            f"STAGE `{stage.id}` ({stage.kind})",
            f"DESCRIPTION:\n{stage.description}",
            f"OUTPUT SCHEMA: {render_schema(stage.output.model_dump(mode='json'))}",
            "INPUTS:",
            *(render_profile(input_profiles[name]) for name in stage.inputs),
        ]
        if broken:
            lines.append("SOME OF YOUR PREVIOUS TESTS COULD NOT EVEN BE EVALUATED (they raised "
                         f"errors, which means the test is wrong, not the code):\n{broken}\n"
                         "Rewrite the test set, fixing those expressions.")
        lines.append("Write the tests for this stage.")

        def check(tests: StageTests) -> StageTests:
            for t in tests.tests:  # every check must at least be valid Python syntax
                try:
                    tree = ast.parse(t.check, mode="eval")
                except SyntaxError as exc:
                    raise ValueError(f"test '{t.name}' is not a valid expression: {exc}") from exc
                if trap := _invert_reduction(tree):
                    raise ValueError(
                        f"test '{t.name}': `~...{trap}()` applies ~ to the single True/False "
                        f"that .{trap}() returns, not to each value, so it is wrong even for "
                        f"correct output. Write `not x.{trap}()` or `(~x).{trap}()`; for "
                        f"example `not x.any()`.")
            return tests.model_copy(update={"stage_id": stage.id})

        return self.ask("\n\n".join(lines), StageTests, stage=stage.id, check=check)

    def execute(self, stage: StageSpec, code: StageCode, tests: StageTests,
                inputs: dict[str, Path], job: str, run_root: Path,
                max_quarantine_fraction: float = 0.25) -> ExecutionResult:
        """Run the code and the tests in the sandbox (deterministic; no model call)."""
        result = self.ctx.gateway.call("tester", "sandbox", "run_stage", {
            "stage_id": stage.id, "code": code.code, "language": code.language,
            "inputs": {name: str(Path(p).relative_to(run_root)) for name, p in inputs.items()},
            "output_schema": stage.output.model_dump(mode="json"),
            "tests": [t.model_dump() for t in tests.tests], "job": job,
            "max_quarantine_fraction": max_quarantine_fraction,
        })
        result.setdefault("stage_id", stage.id)
        known = ExecutionResult.model_fields
        return ExecutionResult.model_validate({k: v for k, v in result.items() if k in known})


def _invert_reduction(tree: ast.AST) -> str | None:
    """Find `~<expr>.any()` / `~<expr>.all()`: a precedence trap seen in a live benchmark."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert)
                and isinstance(node.operand, ast.Call)
                and isinstance(node.operand.func, ast.Attribute)
                and node.operand.func.attr in ("any", "all")):
            return node.operand.func.attr
    return None
