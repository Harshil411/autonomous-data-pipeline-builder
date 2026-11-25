"""Stage Coder agent: one stage spec -> code (requirement 4).

The coder sees ONLY what one stage needs (the requirements document: "The code agent
requires only the requested step and the schema associated with that step"): the stage
spec, its input schemas or profiles, and - when re-coding after a failed quality check -
the failing checks. It never sees the whole plan or other stages' code.
"""

from __future__ import annotations

from adpb.agents.base import Agent, render_profile, render_schema
from adpb.agents.prompts import CODER
from adpb.contracts import StageCode, StageSpec


class StageCoder(Agent):
    role = "coder"
    system_prompt = CODER

    def write(self, stage: StageSpec, input_profiles: dict[str, dict],
              feedback: str | None = None) -> StageCode:
        lines = [
            f"STAGE `{stage.id}` ({stage.kind}, language: {stage.language})",
            f"DESCRIPTION:\n{stage.description}",
            f"OUTPUT SCHEMA: {render_schema(stage.output.model_dump(mode='json'))}",
            "INPUTS:",
        ]
        for name in stage.inputs:
            lines.append(render_profile(input_profiles[name]))
        if feedback:
            lines.append("THE PREVIOUS VERSION OF THIS STAGE PRODUCED DATA THAT FAILED THESE "
                         f"QUALITY CHECKS - fix the logic so they pass:\n{feedback}")
        lines.append(f"Write the {stage.language} code for this stage.")

        def check(code: StageCode) -> StageCode:
            # The coder answers for this stage only, in the planned language.
            return code.model_copy(update={"stage_id": stage.id, "language": stage.language})

        return self.ask("\n\n".join(lines), StageCode, stage=stage.id, check=check)
