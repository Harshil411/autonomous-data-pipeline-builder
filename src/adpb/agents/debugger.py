"""Debugger agent: failing code + evidence -> patched code (requirement 7).

The debugger's contract is narrow on purpose (flyer: "fixes the code, never the test"):
its output type has no field for tests or schemas, so it *cannot* weaken them. It is told
what earlier attempts tried, so it does not apply the same band-aid twice (mitigation 4).
"""

from __future__ import annotations

from adpb.agents.base import Agent, render_profile, render_schema
from adpb.agents.prompts import DEBUGGER
from adpb.contracts import DebugPatch, ExecutionResult, StageCode, StageSpec, StageTests


class Debugger(Agent):
    role = "debugger"
    system_prompt = DEBUGGER

    def fix(self, stage: StageSpec, code: StageCode, result: ExecutionResult,
            tests: StageTests, input_profiles: dict[str, dict],
            history: list[str]) -> tuple[StageCode, str]:
        failing = [f"- {t.name}: {t.detail}" for t in result.failed_tests]
        test_text = "\n".join(f"- {t.name}: `{t.check}` ({t.description})" for t in tests.tests)
        lines = [
            f"STAGE `{stage.id}` ({stage.kind}, {stage.language})",
            f"DESCRIPTION:\n{stage.description}",
            f"OUTPUT SCHEMA: {render_schema(stage.output.model_dump(mode='json'))}",
            f"CURRENT CODE:\n```\n{code.code}\n```",
            f"FAILURE: {result.error_type}\n{result.error}",
        ]
        if failing:
            lines.append("FAILING TESTS:\n" + "\n".join(failing))
        if result.schema_problems:
            lines.append("SCHEMA NOTES:\n" + "\n".join(result.schema_problems))
        if result.logs:
            lines.append(f"PRINTED OUTPUT:\n{result.logs}")
        lines.append(f"THE TESTS (fixed; you cannot change them):\n{test_text}")
        lines.append("INPUTS:")
        lines.extend(render_profile(input_profiles[name]) for name in stage.inputs)
        if history:
            lines.append("PREVIOUS ATTEMPTS THAT DID NOT WORK:\n" + "\n".join(history))
        lines.append("Diagnose the root cause and return the corrected code.")
        patch = self.ask("\n\n".join(lines), DebugPatch, stage=stage.id)
        fixed = StageCode(stage_id=stage.id, language=stage.language, code=patch.code,
                          notes=patch.diagnosis)
        return fixed, patch.diagnosis
