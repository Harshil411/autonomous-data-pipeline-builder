"""Shared fixtures for whole-pipeline tests: dirty sources, a plan, and stage code.

The data is small but deliberately dirty, so every run exercises quarantine:
  * customers: one id that is not a number ("x9")
  * orders: a word in the amount column ("oops"), a negative amount, and an order whose
    customer does not exist (dropped by the inner join).
"""

from __future__ import annotations

from adpb.contracts import (
    BudgetLimits,
    ColumnSchema,
    DataRequest,
    DType,
    PlanDraft,
    QualityRule,
    RuleKind,
    RuleProposals,
    SourceKind,
    SourceSpec,
    StageCode,
    StageSpec,
    StageTest,
    StageTests,
    TableSchema,
)

CUSTOMERS_CSV = "customer_id,name\n1,Ada\n2,Grace\n3,Linus\nx9,Broken\n"
ORDERS_CSV = ("order_id,customer_id,amount\n100,1,$5.00\n101,2,$7.50\n102,1,$12.25\n"
              "103,3,$3.00\n104,2,oops\n105,9,$1.00\n106,1,-$2.00\n")


def request(**budget) -> DataRequest:
    return DataRequest(
        request="Load the customers and orders CSVs, clean them, join orders to customers "
                "and write the result to the database.",
        expected_result="one row per valid order with the customer's name",
        sources=[SourceSpec(name="customers", kind=SourceKind.CSV, path="customers.csv"),
                 SourceSpec(name="orders", kind=SourceKind.CSV, path="orders.csv")],
        target_table="customer_orders",
        budget=BudgetLimits(**budget),
    )


def _cols(*spec):
    return [ColumnSchema(name=n, dtype=t, nullable=nl) for n, t, nl in spec]


CLEAN_CUSTOMERS = StageSpec(
    id="clean_customers", kind="clean", inputs=["raw.customers"],
    description="Trim ids and parse them as integers.",
    output=TableSchema(name="clean_customers", primary_key=["customer_id"], columns=_cols(
        ("customer_id", DType.INTEGER, False), ("name", DType.STRING, True))))
CLEAN_ORDERS = StageSpec(
    id="clean_orders", kind="clean", inputs=["raw.orders"],
    description="Strip $ from amounts and parse numbers.",
    output=TableSchema(name="clean_orders", primary_key=["order_id"], columns=_cols(
        ("order_id", DType.INTEGER, False), ("customer_id", DType.INTEGER, False),
        ("amount", DType.FLOAT, True))))
JOIN = StageSpec(
    id="customer_orders", kind="join", inputs=["clean_orders", "clean_customers"],
    description="Inner join orders to customers on customer_id.",
    output=TableSchema(name="customer_orders", primary_key=["order_id"], columns=_cols(
        ("order_id", DType.INTEGER, False), ("customer_id", DType.INTEGER, False),
        ("name", DType.STRING, True), ("amount", DType.FLOAT, True))))

FK_RULE = QualityRule(kind=RuleKind.FOREIGN_KEY, dataset="customer_orders",
                      column="customer_id", ref_dataset="clean_customers",
                      ref_column="customer_id", origin="request")
PLAN = PlanDraft(stages=[CLEAN_CUSTOMERS, CLEAN_ORDERS, JOIN], final_dataset="customer_orders",
                 quality_rules=[FK_RULE], rationale="clean both, then join")


def code(stage_id: str, body: str) -> StageCode:
    return StageCode(stage_id=stage_id, code=body)


CODE_CUSTOMERS = code("clean_customers", '''
def run(inputs):
    df = inputs["raw.customers"].copy()
    df["customer_id"] = df["customer_id"].str.strip()
    return df[["customer_id", "name"]]
''')

# Keeps negative amounts: passes its own tests but fails the "amount >= 0" quality rule.
CODE_ORDERS_NAIVE = code("clean_orders", '''
def run(inputs):
    df = inputs["raw.orders"].copy()
    df["amount"] = df["amount"].str.replace("$", "", regex=False)
    return df[["order_id", "customer_id", "amount"]]
''')

CODE_ORDERS = code("clean_orders", '''
import pandas as pd

def run(inputs):
    df = inputs["raw.orders"].copy()
    df["amount"] = df["amount"].str.replace("$", "", regex=False)
    numeric = pd.to_numeric(df["amount"], errors="coerce")
    negative = numeric < 0
    rejected = df[negative].assign(_reason="negative amount")
    return df[~negative][["order_id", "customer_id", "amount"]], rejected
''')

CODE_ORDERS_BUGGY = code("clean_orders", '''
def run(inputs):
    return inputs["raw.order"]  # typo: there is no such dataset
''')

CODE_JOIN = code("customer_orders", '''
def run(inputs):
    orders, customers = inputs["clean_orders"], inputs["clean_customers"]
    joined = orders.merge(customers, on="customer_id", how="inner")
    return joined[["order_id", "customer_id", "name", "amount"]]
''')

CODE_JOIN_BUGGY = code("customer_orders", '''
def run(inputs):
    return inputs["clean_orders"].merge(inputs["customers"], on="customer_id")
''')


def tests(stage_id: str, *checks: str) -> StageTests:
    return StageTests(stage_id=stage_id, tests=[
        StageTest(name=f"t{i}", description="", check=c) for i, c in enumerate(checks, 1)])


TESTS_CUSTOMERS = tests("clean_customers", "out['name'].notna().all()")
TESTS_ORDERS = tests("clean_orders", "len(out) > 0")
TESTS_JOIN = tests("customer_orders", "out['order_id'].is_unique",
                   "set(out['customer_id']) <= set(inputs['clean_customers']['customer_id'])")

PASSING_RULES = RuleProposals(rules=[
    QualityRule(kind=RuleKind.ROW_COUNT, dataset="customer_orders", min=1)])
NON_NEGATIVE = RuleProposals(rules=[
    QualityRule(kind=RuleKind.RANGE, dataset="clean_orders", column="amount", min=0)])


def happy_script() -> dict:
    """Roles in order; the scripted model keeps one queue per role."""
    return {
        "planner": [PLAN],
        "tester": [TESTS_CUSTOMERS, TESTS_ORDERS, TESTS_JOIN],
        "coder": [CODE_CUSTOMERS, CODE_ORDERS, CODE_JOIN],
        "validator": [PASSING_RULES],
    }
