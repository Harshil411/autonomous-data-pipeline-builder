"""Contract tests: the typed hand-offs between agents (requirements 2, 3, 10).

Every agent passes compact Pydantic objects, never chat history. These tests pin down
what a *valid* plan is, so a bad LLM plan is rejected by code before any stage runs
(mitigation 6: "structured output plus schema validation and dependency order").
"""

import pytest
from pydantic import ValidationError

from adpb.contracts import (
    ColumnSchema,
    DType,
    PipelinePlan,
    QualityRule,
    RuleKind,
    SourceKind,
    SourceSpec,
    StageSpec,
    TableSchema,
)


def _schema(name: str, *cols: tuple[str, DType], pk: list[str] | None = None) -> TableSchema:
    return TableSchema(
        name=name,
        columns=[ColumnSchema(name=c, dtype=t) for c, t in cols],
        primary_key=pk or [],
    )


def _plan(stages: list[StageSpec], sources: list[SourceSpec] | None = None) -> PipelinePlan:
    return PipelinePlan(
        request="load customers and orders, clean, join",
        sources=sources
        or [
            SourceSpec(name="customers", kind=SourceKind.CSV, path="customers.csv"),
            SourceSpec(name="orders", kind=SourceKind.CSV, path="orders.csv"),
        ],
        stages=stages,
        final_dataset=stages[-1].output.name,
        target_table="customer_orders",
    )


CUSTOMERS = _schema("clean_customers", ("customer_id", DType.INTEGER), ("name", DType.STRING),
                    pk=["customer_id"])
ORDERS = _schema("clean_orders", ("order_id", DType.INTEGER), ("customer_id", DType.INTEGER),
                 ("amount", DType.FLOAT), pk=["order_id"])
JOINED = _schema("customer_orders", ("order_id", DType.INTEGER), ("customer_id", DType.INTEGER),
                 ("name", DType.STRING), ("amount", DType.FLOAT))


def _three_stage_plan() -> PipelinePlan:
    return _plan([
        StageSpec(id="clean_customers", kind="clean", description="type the ids",
                  inputs=["raw.customers"], output=CUSTOMERS),
        StageSpec(id="clean_orders", kind="clean", description="type the ids and amounts",
                  inputs=["raw.orders"], output=ORDERS),
        StageSpec(id="customer_orders", kind="join", description="orders joined to customers",
                  inputs=["clean_customers", "clean_orders"], output=JOINED),
    ])


class TestTableSchema:
    def test_duplicate_column_names_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate column"):
            _schema("t", ("a", DType.STRING), ("a", DType.INTEGER))

    def test_primary_key_must_name_existing_columns(self):
        with pytest.raises(ValidationError, match="primary key"):
            _schema("t", ("a", DType.STRING), pk=["missing"])

    def test_identifiers_must_be_safe_sql_names(self):
        # Names flow into SQL and file paths, so anything but [a-z0-9_] is refused.
        with pytest.raises(ValidationError):
            _schema("t; drop table x", ("a", DType.STRING))
        with pytest.raises(ValidationError):
            _schema("t", ("bad name", DType.STRING))

    def test_column_lookup(self):
        assert ORDERS.column("amount").dtype is DType.FLOAT
        assert ORDERS.column_names == ["order_id", "customer_id", "amount"]


class TestPlanValidation:
    def test_valid_plan_orders_stages_topologically(self):
        plan = _three_stage_plan()
        assert [s.id for s in plan.execution_order()][-1] == "customer_orders"

    def test_stage_input_must_exist_upstream(self):
        with pytest.raises(ValidationError, match="unknown input"):
            _plan([StageSpec(id="clean_customers", kind="clean", description="x",
                             inputs=["raw.nope"], output=CUSTOMERS)])

    def test_cycles_are_rejected(self):
        a = StageSpec(id="a", kind="transform", description="x", inputs=["b"],
                      output=_schema("a", ("x", DType.STRING)))
        b = StageSpec(id="b", kind="transform", description="x", inputs=["a"],
                      output=_schema("b", ("x", DType.STRING)))
        with pytest.raises(ValidationError, match="cycle"):
            _plan([a, b])

    def test_stage_output_name_must_equal_stage_id(self):
        # One name per dataset keeps the lineage unambiguous: stage "x" writes dataset "x".
        with pytest.raises(ValidationError, match="output name"):
            _plan([StageSpec(id="clean", kind="clean", description="x",
                             inputs=["raw.customers"], output=CUSTOMERS)])

    def test_final_dataset_must_be_a_stage_output(self):
        plan = _three_stage_plan().model_dump()
        plan["final_dataset"] = "raw.customers"
        with pytest.raises(ValidationError, match="final_dataset"):
            PipelinePlan.model_validate(plan)

    def test_duplicate_stage_ids_are_rejected(self):
        s = StageSpec(id="clean_customers", kind="clean", description="x",
                      inputs=["raw.customers"], output=CUSTOMERS)
        with pytest.raises(ValidationError, match="duplicate stage"):
            _plan([s, s])

    def test_downstream_of_returns_the_stage_and_everything_after_it(self):
        # Requirement 19: when a stage fails, only it and its dependants re-run.
        plan = _three_stage_plan()
        assert plan.downstream_of("clean_orders") == ["clean_orders", "customer_orders"]
        assert plan.downstream_of("customer_orders") == ["customer_orders"]

    def test_producer_of_maps_a_dataset_back_to_its_stage(self):
        # Requirement 10: a quality failure is routed back to the stage that made the data.
        plan = _three_stage_plan()
        assert plan.producer_of("customer_orders") == "customer_orders"
        assert plan.producer_of("clean_orders") == "clean_orders"
        assert plan.producer_of("raw.orders") is None  # raw data has no producing stage

    def test_plan_json_round_trip(self):
        plan = _three_stage_plan()
        assert PipelinePlan.model_validate_json(plan.model_dump_json()) == plan


class TestQualityRule:
    def test_range_rule_needs_a_bound(self):
        with pytest.raises(ValidationError, match="min or max"):
            QualityRule(kind=RuleKind.RANGE, dataset="join", column="amount")

    def test_column_rules_need_a_column(self):
        with pytest.raises(ValidationError, match="column"):
            QualityRule(kind=RuleKind.NOT_NULL, dataset="join")

    def test_foreign_key_needs_a_reference(self):
        with pytest.raises(ValidationError, match="ref"):
            QualityRule(kind=RuleKind.FOREIGN_KEY, dataset="join", column="customer_id")

    def test_rule_id_is_stable_and_readable(self):
        rule = QualityRule(kind=RuleKind.NOT_NULL, dataset="join", column="amount")
        assert rule.id == "join.amount.not_null"


class TestSourceSpec:
    def test_file_sources_need_a_path(self):
        with pytest.raises(ValidationError, match="path"):
            SourceSpec(name="c", kind=SourceKind.CSV)

    def test_api_sources_need_a_url(self):
        with pytest.raises(ValidationError, match="url"):
            SourceSpec(name="c", kind=SourceKind.API)

    def test_sqlite_sources_need_a_table(self):
        with pytest.raises(ValidationError, match="table"):
            SourceSpec(name="c", kind=SourceKind.SQLITE, path="db.sqlite")

    def test_raw_dataset_name(self):
        assert SourceSpec(name="orders", kind=SourceKind.CSV, path="o.csv").dataset == "raw.orders"
