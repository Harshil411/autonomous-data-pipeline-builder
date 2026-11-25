"""Planner agent: request + source profiles -> validated PipelinePlan (requirements 2-3, 8).

The planner is the only agent that sees the whole request. Its draft is assembled into a
`PipelinePlan` together with the request and sources the orchestrator already holds, and
that assembly runs the plan validators (unknown inputs, cycles, naming). Any violation is
sent back to the model as a precise repair request - "structured output plus schema
validation and dependency order" (mitigation 6).
"""

from __future__ import annotations

import json

from adpb.agents.base import Agent, render_profile, render_schema
from adpb.agents.prompts import PLANNER
from adpb.contracts import DataRequest, PipelinePlan, PlanDraft


class Planner(Agent):
    role = "planner"
    system_prompt = PLANNER

    def _assemble(self, request: DataRequest):
        def check(draft: PlanDraft) -> PipelinePlan:
            rules = [r.model_copy(update={"origin": "request"}) if r.origin == "derived" else r
                     for r in draft.quality_rules]
            return PipelinePlan(request=request.request, sources=request.sources,
                                stages=draft.stages, final_dataset=draft.final_dataset,
                                target_table=request.target_table, quality_rules=rules,
                                rationale=draft.rationale)
        return check

    @staticmethod
    def _brief(request: DataRequest, profiles: dict[str, dict]) -> str:
        parts = [f"REQUEST:\n{request.request}"]
        if request.expected_result:
            parts.append(f"EXPECTED RESULT:\n{request.expected_result}")
        if request.quality_rules:
            parts.append("QUALITY RULES STATED BY THE USER:\n" +
                         "\n".join(f"- {r}" for r in request.quality_rules))
        parts.append(f"FINAL TABLE NAME: {request.target_table}")
        parts.append("RAW SOURCES (refer to them as raw.<name>):")
        for src in request.sources:
            parts.append(f"source {src.name} ({src.kind}) {src.description}".strip())
            parts.append(render_profile(profiles[src.dataset]))
        return "\n\n".join(parts)

    def plan(self, request: DataRequest, profiles: dict[str, dict]) -> PipelinePlan:
        prompt = self._brief(request, profiles) + "\n\nProduce the pipeline plan."
        return self.ask(prompt, PlanDraft, check=self._assemble(request))

    def replan(self, request: DataRequest, profiles: dict[str, dict], previous: PipelinePlan,
               failed_stage: str, failure: str, passed: list[str]) -> PipelinePlan:
        """Escalation after a stage exhausted its retries (requirement 8)."""
        stages = "\n".join(f"- {s.id} [{s.kind}] inputs={s.inputs} -> "
                           f"{render_schema(s.output.model_dump(mode='json'))}: {s.description}"
                           for s in previous.stages)
        prompt = (
            self._brief(request, profiles)
            + f"\n\nPREVIOUS PLAN:\n{stages}"
            + f"\n\nSTAGE `{failed_stage}` FAILED after all retries. Last error:\n{failure}"
            + f"\n\nStages that already passed (keep them unchanged unless they cause the "
              f"failure): {json.dumps(passed)}"
            + "\n\nProduce a revised plan that avoids this failure - e.g. split the stage, "
              "relax an over-strict NOT NULL, fix a wrong input or join key, or describe the "
              "parsing more precisely."
        )
        return self.ask(prompt, PlanDraft, check=self._assemble(request))
