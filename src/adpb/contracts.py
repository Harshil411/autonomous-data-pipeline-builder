"""Typed contracts shared by every component of the pipeline builder.

Design rule (requirements 2-3, mitigation 7 "contract-first output schemas"): agents never
pass free-form chat to each other. The planner emits a `PipelinePlan`, each stage carries an
explicit input list and an output `TableSchema`, and every later agent receives only the
slice of the plan it needs. Because these are Pydantic models, a malformed plan from an LLM
fails validation in code *before* anything runs - the LLM never gets to "explain away" a
plan that references a dataset that does not exist.

Naming convention used throughout the system:
  * a raw source called ``orders`` is the dataset ``raw.orders``;
  * a stage with id ``clean_orders`` produces exactly one dataset, also named ``clean_orders``.
One name per dataset keeps lineage unambiguous, which is what lets the orchestrator route a
failed quality check back to the stage that produced the bad data (requirement 10).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Identifiers end up in SQL statements and file names, so they are restricted to a safe
# alphabet. This is a guardrail against both prompt injection ("name the table
# `x; DROP TABLE users`") and path traversal.
IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
Identifier = Annotated[str, Field(pattern=IDENT_RE.pattern)]

RAW_PREFIX = "raw."


class _Frozen(BaseModel):
    """Base for contracts: unknown keys are rejected so an LLM cannot smuggle in fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------------------


class DType(StrEnum):
    """Logical column types. The sandbox harness maps these onto pandas dtypes."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


class ColumnSchema(_Frozen):
    name: Identifier
    dtype: DType
    nullable: bool = True
    description: str = ""


class TableSchema(_Frozen):
    """The output contract of one stage: column names, types, nullability and key."""

    name: Identifier
    columns: list[ColumnSchema] = Field(min_length=1)
    primary_key: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> TableSchema:
        names = [c.name for c in self.columns]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate column name(s): {sorted(dupes)}")
        missing = [k for k in self.primary_key if k not in names]
        if missing:
            raise ValueError(f"primary key column(s) not in schema: {missing}")
        return self

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnSchema:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)


# --------------------------------------------------------------------------------------
# Sources and the user's request
# --------------------------------------------------------------------------------------


class SourceKind(StrEnum):
    CSV = "csv"
    JSON = "json"
    SQLITE = "sqlite"
    API = "api"


class SourceSpec(_Frozen):
    """One input the user supplied. ``path`` is relative to the run's ``sources/`` folder."""

    name: Identifier
    kind: SourceKind
    path: str | None = None
    url: str | None = None
    table: str | None = None  # for SQLite sources
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> SourceSpec:
        if self.kind in (SourceKind.CSV, SourceKind.JSON, SourceKind.SQLITE) and not self.path:
            raise ValueError(f"{self.kind} source '{self.name}' needs a path")
        if self.kind is SourceKind.API and not self.url:
            raise ValueError(f"api source '{self.name}' needs a url")
        if self.kind is SourceKind.SQLITE and not self.table:
            raise ValueError(f"sqlite source '{self.name}' needs a table")
        return self

    @property
    def dataset(self) -> str:
        """Name of the raw dataset this source becomes inside the pipeline."""
        return f"{RAW_PREFIX}{self.name}"


class BudgetLimits(_Frozen):
    """Per-run ceilings (requirement 23). The run ends cleanly when any one is reached."""

    max_usd: float = Field(default=1.00, gt=0)
    max_tokens: int = Field(default=400_000, gt=0)
    max_seconds: float = Field(default=900, gt=0)
    max_retries_per_stage: int = Field(default=3, ge=1, le=10)
    max_replans: int = Field(default=1, ge=0, le=3)
    max_quality_rounds: int = Field(default=2, ge=1, le=5)


class DataRequest(_Frozen):
    """Requirement 1: a natural-language request, its sources, and the expected result."""

    request: str = Field(min_length=10, max_length=4000)
    sources: list[SourceSpec] = Field(min_length=1)
    expected_result: str = ""
    target_table: Identifier = "pipeline_output"
    quality_rules: list[str] = Field(default_factory=list)  # optional rules stated by the user
    budget: BudgetLimits = Field(default_factory=BudgetLimits)


# --------------------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------------------

StageKind = Literal["ingest", "clean", "transform", "join", "aggregate"]


class StageSpec(_Frozen):
    """One step of the pipeline. Its code is written later by the Stage Coder."""

    id: Identifier
    kind: StageKind
    description: str = Field(min_length=1, max_length=1500)
    inputs: list[str] = Field(min_length=1)
    output: TableSchema
    language: Literal["python", "sql"] = "python"


class PipelinePlan(_Frozen):
    """The planner's output, checked here for linkage, cycles and naming."""

    request: str
    sources: list[SourceSpec] = Field(min_length=1)
    stages: list[StageSpec] = Field(min_length=1, max_length=12)
    final_dataset: str
    target_table: Identifier
    quality_rules: list[QualityRule] = Field(default_factory=list)
    rationale: str = ""

    @model_validator(mode="after")
    def _check(self) -> PipelinePlan:
        ids = [s.id for s in self.stages]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate stage id(s): {sorted(dupes)}")
        for s in self.stages:
            if s.output.name != s.id:
                raise ValueError(
                    f"stage '{s.id}': output name '{s.output.name}' must equal the stage id"
                )
        known = {src.dataset for src in self.sources} | set(ids)
        for s in self.stages:
            unknown = [i for i in s.inputs if i not in known]
            if unknown:
                raise ValueError(f"stage '{s.id}' has unknown input(s): {unknown}")
        self._topological()  # raises on a cycle
        if self.final_dataset not in ids:
            raise ValueError(f"final_dataset '{self.final_dataset}' is not a stage output")
        return self

    # -- graph helpers ---------------------------------------------------------------

    def stage(self, stage_id: str) -> StageSpec:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise KeyError(stage_id)

    def _topological(self) -> list[StageSpec]:
        """Kahn's algorithm; keeps the planner's order among independent stages."""
        by_id = {s.id: s for s in self.stages}
        deps = {s.id: {i for i in s.inputs if i in by_id} for s in self.stages}
        order: list[StageSpec] = []
        ready = [s.id for s in self.stages if not deps[s.id]]
        while ready:
            current = ready.pop(0)
            order.append(by_id[current])
            for s in self.stages:
                if current in deps[s.id]:
                    deps[s.id].discard(current)
                    if not deps[s.id] and s.id not in [o.id for o in order] and s.id not in ready:
                        ready.append(s.id)
        if len(order) != len(self.stages):
            stuck = sorted(set(by_id) - {o.id for o in order})
            raise ValueError(f"stage dependency cycle among: {stuck}")
        return order

    def execution_order(self) -> list[StageSpec]:
        return self._topological()

    def downstream_of(self, stage_id: str) -> list[str]:
        """The stage itself plus every stage that (transitively) consumes its output."""
        affected = {stage_id}
        for s in self.execution_order():  # topological order => one pass is enough
            if any(i in affected for i in s.inputs):
                affected.add(s.id)
        return [s.id for s in self.execution_order() if s.id in affected]

    def producer_of(self, dataset: str) -> str | None:
        """Which stage produced ``dataset``; ``None`` for raw sources."""
        return dataset if dataset in {s.id for s in self.stages} else None

    def input_schemas(self, stage_id: str) -> dict[str, TableSchema | None]:
        """Schemas of a stage's inputs; raw sources have no declared schema (``None``)."""
        out: dict[str, TableSchema | None] = {}
        for name in self.stage(stage_id).inputs:
            out[name] = self.stage(name).output if self.producer_of(name) else None
        return out


# --------------------------------------------------------------------------------------
# Quality rules (requirement 9-10)
# --------------------------------------------------------------------------------------


class RuleKind(StrEnum):
    NOT_NULL = "not_null"
    MAX_NULL_FRACTION = "max_null_fraction"
    UNIQUE = "unique"
    NO_DUPLICATE_ROWS = "no_duplicate_rows"
    DTYPE = "dtype"
    RANGE = "range"
    ALLOWED_VALUES = "allowed_values"
    ROW_COUNT = "row_count"
    FOREIGN_KEY = "foreign_key"


_COLUMN_RULES = {
    RuleKind.NOT_NULL, RuleKind.MAX_NULL_FRACTION, RuleKind.UNIQUE, RuleKind.DTYPE,
    RuleKind.RANGE, RuleKind.ALLOWED_VALUES, RuleKind.FOREIGN_KEY,
}


class QualityRule(_Frozen):
    """A declarative check. Rules are *data*, executed by deterministic code - never judged
    by an LLM (mitigation 5: "a separate validator using code checks")."""

    kind: RuleKind
    dataset: str
    column: str | None = None
    columns: list[str] = Field(default_factory=list)  # for multi-column uniqueness
    min: float | None = None
    max: float | None = None
    values: list[str | int | float | bool] = Field(default_factory=list)
    dtype: DType | None = None
    ref_dataset: str | None = None
    ref_column: str | None = None
    origin: Literal["schema", "request", "derived"] = "derived"
    reason: str = ""

    @model_validator(mode="after")
    def _check(self) -> QualityRule:
        if self.kind in _COLUMN_RULES and not self.column and not (
            self.kind is RuleKind.UNIQUE and self.columns
        ):
            raise ValueError(f"{self.kind} rule needs a column")
        if self.kind in (RuleKind.RANGE, RuleKind.ROW_COUNT, RuleKind.MAX_NULL_FRACTION) and (
            self.min is None and self.max is None
        ):
            raise ValueError(f"{self.kind} rule needs a min or max")
        if self.kind is RuleKind.FOREIGN_KEY and not (self.ref_dataset and self.ref_column):
            raise ValueError("foreign_key rule needs ref_dataset and ref_column")
        if self.kind is RuleKind.DTYPE and self.dtype is None:
            raise ValueError("dtype rule needs a dtype")
        if self.kind is RuleKind.ALLOWED_VALUES and not self.values:
            raise ValueError("allowed_values rule needs values")
        return self

    @property
    def id(self) -> str:
        target = self.column or "+".join(self.columns) or "*"
        return f"{self.dataset}.{target}.{self.kind.value}"


class CheckResult(_Frozen):
    rule_id: str
    dataset: str
    passed: bool
    observed: str = ""
    failing_rows: int = 0
    sample: list[dict] = Field(default_factory=list)  # a few offending rows, never the data


# --------------------------------------------------------------------------------------
# Tests written by the Tester (before it sees any code)
# --------------------------------------------------------------------------------------


class StageTest(_Frozen):
    """One behavioural assertion about a stage's output.

    ``check`` is a single Python expression evaluated *inside the sandbox* with ``out``
    (the stage output DataFrame) and ``inputs`` (dict of input DataFrames) in scope,
    e.g. ``out["amount"].ge(0).all()``.
    """

    name: Identifier
    description: str
    check: str = Field(min_length=3, max_length=500)


class StageTests(_Frozen):
    stage_id: str
    tests: list[StageTest] = Field(min_length=1, max_length=12)

    @field_validator("tests")
    @classmethod
    def _unique_names(cls, v: list[StageTest]) -> list[StageTest]:
        names = [t.name for t in v]
        if len(set(names)) != len(names):
            raise ValueError("duplicate test names")
        return v


# --------------------------------------------------------------------------------------
# Execution results
# --------------------------------------------------------------------------------------


class StageCode(_Frozen):
    """What the Stage Coder (or Debugger) hands back: code only, never tests or schemas."""

    stage_id: str
    language: Literal["python", "sql"] = "python"
    code: str = Field(min_length=10, max_length=20_000)
    notes: str = ""


class TestOutcome(_Frozen):
    name: str
    passed: bool
    detail: str = ""


class ExecutionResult(_Frozen):
    """The sandbox's verdict on one run of one stage (sample or full data)."""

    stage_id: str
    ok: bool
    error_type: str | None = None  # e.g. "KeyError", "SchemaMismatch", "Timeout"
    error: str = ""                # traceback / message, truncated
    rows_in: dict[str, int] = Field(default_factory=dict)
    rows_out: int = 0
    rows_quarantined: int = 0
    schema_problems: list[str] = Field(default_factory=list)
    tests: list[TestOutcome] = Field(default_factory=list)
    duration_s: float = 0.0
    output_path: str | None = None
    quarantine_path: str | None = None

    @property
    def failed_tests(self) -> list[TestOutcome]:
        return [t for t in self.tests if not t.passed]


class StageStatus(StrEnum):
    PENDING = "pending"
    CODING = "coding"
    TESTING = "testing"
    DEBUGGING = "debugging"
    PASSED = "passed"
    FAILED = "failed"


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"


# Pydantic needs the forward reference to QualityRule resolved for PipelinePlan.
PipelinePlan.model_rebuild()
