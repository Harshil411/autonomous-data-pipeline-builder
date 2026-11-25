"""Validator agent: proposes quality rules; code executes them (requirements 9-10).

Rules come from three places:
  * schema  - derived mechanically from each stage's output contract (dtype, NOT NULL,
              primary-key uniqueness, non-empty);
  * request - stated by the user and carried by the plan;
  * derived - proposed by this agent's model from the data profiles.
Proposed rules that reference unknown datasets or columns are discarded. Every rule is then
run by ``sandbox.run_quality`` - deterministic code, not an LLM opinion (mitigation 5).
"""

from __future__ import annotations

from pathlib import Path

from adpb.agents.base import Agent, render_profile, render_schema
from adpb.agents.prompts import VALIDATOR
from adpb.contracts import CheckResult, PipelinePlan, QualityRule, RuleKind, RuleProposals


def schema_rules(plan: PipelinePlan) -> list[QualityRule]:
    rules: list[QualityRule] = []
    for stage in plan.stages:
        out = stage.output
        rules.append(QualityRule(kind=RuleKind.ROW_COUNT, dataset=stage.id, min=1,
                                 origin="schema", reason="every stage must produce rows"))
        for col in out.columns:
            rules.append(QualityRule(kind=RuleKind.DTYPE, dataset=stage.id, column=col.name,
                                     dtype=col.dtype, origin="schema"))
            if not col.nullable:
                rules.append(QualityRule(kind=RuleKind.NOT_NULL, dataset=stage.id,
                                         column=col.name, origin="schema"))
        if out.primary_key:
            rules.append(QualityRule(kind=RuleKind.UNIQUE, dataset=stage.id,
                                     column=out.primary_key[0] if len(out.primary_key) == 1
                                     else None,
                                     columns=out.primary_key if len(out.primary_key) > 1 else [],
                                     origin="schema", reason="primary key"))
    return rules


def usable(rule: QualityRule, plan: PipelinePlan) -> bool:
    """Keep only rules whose dataset and columns exist in the plan."""
    try:
        cols = set(plan.stage(rule.dataset).output.column_names)
    except KeyError:
        return False
    needed = [c for c in [rule.column, *rule.columns] if c]
    if any(c not in cols for c in needed):
        return False
    if rule.kind is RuleKind.FOREIGN_KEY:
        # The stage blamed for a failure must be able to fix it, so the referenced
        # dataset has to be one of its (transitive) inputs.
        if rule.ref_dataset not in upstream(plan, rule.dataset):
            return False
        try:
            return rule.ref_column in plan.stage(rule.ref_dataset).output.column_names
        except KeyError:
            return False
    return True


def upstream(plan: PipelinePlan, stage_id: str) -> set[str]:
    """Every stage whose output (directly or indirectly) feeds ``stage_id``."""
    seen: set[str] = set()
    todo = list(plan.stage(stage_id).inputs)
    while todo:
        name = todo.pop()
        if name in seen or plan.producer_of(name) is None:
            continue
        seen.add(name)
        todo.extend(plan.stage(name).inputs)
    return seen


def dedupe(rules: list[QualityRule]) -> list[QualityRule]:
    seen, out = set(), []
    for r in rules:
        if r.id not in seen:
            seen.add(r.id)
            out.append(r)
    return out


class Validator(Agent):
    role = "validator"
    system_prompt = VALIDATOR

    def propose(self, plan: PipelinePlan, output_profiles: dict[str, dict],
                raw_profiles: dict[str, dict]) -> list[QualityRule]:
        lines = [f"REQUEST:\n{plan.request}", "PIPELINE OUTPUT DATASETS:"]
        for stage in plan.stages:
            lines.append(f"{render_schema(stage.output.model_dump(mode='json'))}: "
                         f"{stage.description}")
            lines.append(render_profile(output_profiles[stage.id]))
        lines.append("RAW SOURCES:")
        lines.extend(render_profile(p) for p in raw_profiles.values())
        lines.append("Propose quality rules.")
        proposals = self.ask("\n\n".join(lines), RuleProposals)
        return [r.model_copy(update={"origin": "derived"}) for r in proposals.rules]

    def all_rules(self, plan: PipelinePlan, derived: list[QualityRule]) -> list[QualityRule]:
        candidates = schema_rules(plan) + list(plan.quality_rules) + derived
        return dedupe([r for r in candidates if usable(r, plan)])

    def check(self, rules: list[QualityRule], datasets: dict[str, Path], run_root: Path,
              job: str) -> list[CheckResult]:
        """Execute rules in the sandbox. Deterministic; no model call."""
        res = self.ctx.gateway.call("validator", "sandbox", "run_quality", {
            "datasets": {n: str(Path(p).relative_to(run_root)) for n, p in datasets.items()},
            "rules": [r.model_dump(mode="json") for r in rules], "job": job})
        if not res.get("ok"):
            raise RuntimeError(f"quality job failed: {res.get('error')}")
        return [CheckResult.model_validate(c) for c in res["checks"]]
