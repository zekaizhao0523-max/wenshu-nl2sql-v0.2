#!/usr/bin/env python3
"""无库单测：QueryPlan → SQL → AST 静态校验 → 风险判定 → 沙箱估算。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wenshu.services.agent.plan_models import (  # noqa: E402
    FilterSpec,
    JoinSpec,
    OutputFieldSpec,
    QueryPlan,
)
from wenshu.services.agent.risk import assess_risk  # noqa: E402
from wenshu.services.agent.sandbox import estimate_max_rows  # noqa: E402
from wenshu.services.agent.sql_ast import SqlDialect  # noqa: E402
from wenshu.services.agent.sql_compiler import compile_query_plan  # noqa: E402
from wenshu.services.agent.sql_validate import validate_sql, validate_sql_ast  # noqa: E402


class _Col:
    def __init__(self, table: str, column: str):
        self.table = table
        self.column = column


class _Retrieval:
    def __init__(self, tables: list[str], columns: list[_Col]):
        self.expanded_tables = tables
        self.selected_tables = tables
        self.s2_columns = columns
        self.columns = columns


def test_compile_simple_filter() -> None:
    plan = QueryPlan(
        target_tables=["demo_customers"],
        filters=[FilterSpec(table="demo_customers", column="age", operator=">", value=30)],
        output_fields=[
            OutputFieldSpec(
                concept="count",
                table="demo_customers",
                column="cust_id",
                alias="cnt",
                aggregation="count",
            )
        ],
        limit=100,
    )
    sql, tables = compile_query_plan(plan)
    assert "demo_customers" in tables
    assert "SELECT" in sql.upper()
    assert "age" in sql.lower()
    retrieval = _Retrieval(
        ["demo_customers"],
        [_Col("demo_customers", "age"), _Col("demo_customers", "cust_id")],
    )
    v = validate_sql_ast(
        sql,
        plan=plan,
        used_tables=tables,
        retrieval=retrieval,
        generation_source="deterministic",
    )
    assert v.ok, v.errors
    assert v.blocked_reason is None


def test_compile_join() -> None:
    plan = QueryPlan(
        target_tables=["demo_orders", "demo_customers"],
        join_logic=[
            JoinSpec(
                left_table="demo_orders",
                left_column="cust_id",
                right_table="demo_customers",
                right_column="cust_id",
            )
        ],
        output_fields=[
            OutputFieldSpec(concept="姓名", table="demo_customers", column="name", alias="name"),
            OutputFieldSpec(concept="金额", table="demo_orders", column="amount", alias="amount"),
        ],
        limit=50,
    )
    sql, used = compile_query_plan(plan)
    assert "JOIN" in sql.upper()
    retrieval = _Retrieval(
        ["demo_orders", "demo_customers"],
        [
            _Col("demo_customers", "name"),
            _Col("demo_customers", "cust_id"),
            _Col("demo_orders", "amount"),
            _Col("demo_orders", "cust_id"),
        ],
    )
    v = validate_sql_ast(sql, plan=plan, used_tables=used, retrieval=retrieval)
    assert v.ok, v.errors


def test_block_write_sql() -> None:
    errs, blocked = validate_sql("DELETE FROM demo_customers")
    assert blocked == "Delete"
    assert errs


def test_block_outfile_sql() -> None:
    sql = "SELECT * FROM demo_customers INTO OUTFILE '/tmp/x'"
    v = validate_sql_ast(sql)
    assert not v.ok
    assert v.blocked_reason == "Outfile"
    assert v.non_retryable


def test_risk_sensitive_phone() -> None:
    risk = assess_risk(
        sql="SELECT phone_no FROM demo_customers LIMIT 10",
        question="查手机号",
        plan_confidence=0.9,
        approval_enabled=True,
    )
    assert risk.decision in {"approval_required", "pass"}


def test_risk_hard_block_outfile() -> None:
    risk = assess_risk(
        sql="SELECT * FROM t INTO OUTFILE '/tmp/x'",
        question="导出",
        approval_enabled=True,
    )
    assert risk.decision == "hard_block"


def test_explain_row_estimate() -> None:
    plan = {"query_block": {"table": {"rows": 42}, "nested_loop": [{"table": {"rows": 100}}]}}
    assert estimate_max_rows(plan) >= 100


def test_enforce_limit() -> None:
    d = SqlDialect("mysql")
    expr = d.parse("SELECT a FROM t")
    out = d.enforce_limit(expr, 50, "mysql")
    assert "LIMIT" in out.upper()
    assert "50" in out


def test_result_match_bag_equality() -> None:
    from wenshu.services.agent.sql_result_match import results_match

    a = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
    b = [{"y": 2, "x": 1}, {"y": 4, "x": 3}]  # 列序不同
    assert results_match(a, b)
    assert not results_match(a, [{"x": 1, "y": 2}])


def test_plan_routing_complex() -> None:
    from wenshu.services.agent.plan_routing import is_complex_query

    assert is_complex_query(question="统计订单平均金额", retrieval=_Retrieval(["t1"], []))
    assert is_complex_query(
        question="查客户姓名",
        retrieval=_Retrieval(["demo_orders", "demo_customers"], []),
    )
    assert not is_complex_query(
        question="查客户姓名",
        retrieval=_Retrieval(["demo_customers"], []),
        semantic_graph={"confidence": 0.9},
    )


def test_plan_normalize_prune_and_agg() -> None:
    from wenshu.services.agent.plan_normalize import normalize_plan

    plan = QueryPlan(
        target_tables=["demo_orders", "ghost_table"],
        output_fields=[
            OutputFieldSpec(concept="金额", table="demo_orders", column="amount", alias="amount"),
            OutputFieldSpec(concept="幽灵", table="ghost_table", column="x", alias="x"),
        ],
        limit=100,
    )
    retrieval = _Retrieval(
        ["demo_orders"],
        [_Col("demo_orders", "amount")],
    )
    new_plan, notes = normalize_plan(
        plan,
        question="统计订单平均金额",
        retrieval=retrieval,
    )
    assert "ghost_table" not in {_norm_t(t) for t in new_plan.target_tables}
    assert any(f.aggregation for f in new_plan.output_fields)
    assert notes


def _norm_t(name: str) -> str:
    return (name or "").strip().lower()


def test_sql_candidate_rank_prefers_agg() -> None:
    from wenshu.services.agent.llm_structured import SQLResult
    from wenshu.services.agent.sql_candidate_rank import rank_sql_candidates

    plan = QueryPlan(
        target_tables=["demo_orders"],
        output_fields=[
            OutputFieldSpec(
                concept="金额",
                table="demo_orders",
                column="amount",
                alias="avg_amount",
                aggregation="avg",
            )
        ],
        limit=100,
    )
    c1 = SQLResult(sql="SELECT amount FROM demo_orders LIMIT 100", used_tables=["demo_orders"])
    c2 = SQLResult(
        sql="SELECT AVG(amount) AS avg_amount FROM demo_orders LIMIT 100",
        used_tables=["demo_orders"],
    )
    ranked = rank_sql_candidates([c1, c2], plan=plan)
    assert ranked[0][0].sql.upper().startswith("SELECT AVG")


def test_langgraph_compiles() -> None:
    from wenshu.services.agent.graph import build_agent_graph

    g = build_agent_graph(approval_enabled=True)
    assert g is not None
    g2 = build_agent_graph(approval_enabled=False)
    assert g2 is not None


def test_plan_validate_requires_agg() -> None:
    from wenshu.services.agent.plan_validate import validate_query_plan

    plan = QueryPlan(
        target_tables=["demo_orders"],
        output_fields=[
            OutputFieldSpec(concept="金额", table="demo_orders", column="amount", alias="amount"),
        ],
        limit=100,
    )
    errs = validate_query_plan(
        plan,
        question="统计订单平均金额",
        semantic_graph={"query_action": "aggregate", "measures": [{"text": "金额"}]},
        retrieval=_Retrieval(["demo_orders"], [_Col("demo_orders", "amount")]),
    )
    assert any("聚合" in e for e in errs)


def test_plan_validate_join_connectivity() -> None:
    from wenshu.services.agent.plan_validate import validate_query_plan

    plan = QueryPlan(
        target_tables=["demo_orders", "demo_customers"],
        join_logic=[],
        output_fields=[
            OutputFieldSpec(concept="金额", table="demo_orders", column="amount", alias="amount"),
        ],
        limit=100,
    )
    errs = validate_query_plan(
        plan,
        question="查订单金额",
        retrieval=_Retrieval(
            ["demo_orders", "demo_customers"],
            [_Col("demo_orders", "amount"), _Col("demo_customers", "name")],
        ),
    )
    assert any("join" in e.lower() or "JOIN" in e or "连通" in e for e in errs)


def test_m8_output_completeness_and_scope_leak() -> None:
    from wenshu.services.agent.plan_models import OutputGrain

    plan = QueryPlan(
        target_tables=["demo_orders"],
        output_fields=[
            OutputFieldSpec(
                concept="金额",
                table="demo_orders",
                column="amount",
                alias="avg_amount",
                aggregation="avg",
            )
        ],
        group_by=[],
        limit=100,
        output_grain=OutputGrain(level="global", keys=[]),
    )
    retrieval = _Retrieval(["demo_orders"], [_Col("demo_orders", "amount")])
    # 缺输出列
    v = validate_sql_ast(
        "SELECT 1 AS x FROM demo_orders LIMIT 100",
        plan=plan,
        used_tables=["demo_orders"],
        retrieval=retrieval,
        generation_source="model",
    )
    assert not v.ok
    assert any("遗漏" in e for e in v.errors)
    # data_scope 泄漏：字面量出现在 SELECT 投影中
    v2 = validate_sql_ast(
        "SELECT AVG(amount) AS avg_amount, 'dwd' AS scope FROM demo_orders LIMIT 100",
        plan=plan,
        used_tables=["demo_orders"],
        retrieval=retrieval,
        generation_source="model",
    )
    assert not v2.ok
    assert any("命名空间" in e for e in v2.errors)


def test_m8_strict_column_hallucination() -> None:
    plan = QueryPlan(
        target_tables=["demo_orders"],
        output_fields=[
            OutputFieldSpec(concept="金额", table="demo_orders", column="amount", alias="amount")
        ],
        limit=100,
    )
    retrieval = _Retrieval(["demo_orders"], [_Col("demo_orders", "amount")])
    v = validate_sql_ast(
        "SELECT demo_orders.ghost_col FROM demo_orders LIMIT 100",
        plan=plan,
        used_tables=["demo_orders"],
        retrieval=retrieval,
    )
    assert not v.ok
    assert any("不存在字段" in e or "幻觉" in e for e in v.errors)


def test_m11_deterministic_summary() -> None:
    from wenshu.services.agent.result_interpretation import interpret_result

    plan = QueryPlan(
        target_tables=["demo_orders"],
        output_fields=[
            OutputFieldSpec(
                concept="平均金额",
                table="demo_orders",
                column="amount",
                alias="avg_amount",
                aggregation="avg",
            )
        ],
        limit=100,
        output_grain={"level": "global", "keys": []},
    )
    text, summary = interpret_result(
        question="订单平均金额",
        rows=[{"avg_amount": 42}],
        plan=plan,
    )
    assert summary is not None
    assert "42" in text or "平均" in text or summary.headline


def test_logical_plan_build() -> None:
    from wenshu.services.agent.logical_plan import build_logical_plan, validate_logical_plan

    plan = QueryPlan(
        target_tables=["demo_orders"],
        output_fields=[
            OutputFieldSpec(
                concept="金额",
                table="demo_orders",
                column="amount",
                alias="avg_amount",
                aggregation="avg",
            )
        ],
        limit=100,
    )
    lp = build_logical_plan(plan, question="平均金额", semantic_graph={"query_action": "aggregate"})
    assert any(op.kind == "aggregate" for op in lp.operations)
    assert lp.output_grain.level in {"global", "aggregate"}
    assert not validate_logical_plan(lp)


def test_produce_final_sql_simple() -> None:
    from wenshu.services.agent.final_sql import produce_final_sql

    retrieval = _Retrieval(
        ["demo_orders"],
        [_Col("demo_orders", "amount"), _Col("demo_orders", "order_id")],
    )
    final = produce_final_sql(
        question="统计订单平均金额",
        semantic_graph={"query_action": "aggregate", "measures": [{"text": "amount"}]},
        retrieval=retrieval,
        max_plan_retries=2,
        max_sql_retries=2,
        force_llm_sql=False,
    )
    assert final.ok, (final.terminal, final.plan_errors, final.sql_errors)
    assert "AVG" in final.sql.upper() or "avg" in final.sql.lower()
    assert final.plan_attempts >= 1
    assert final.sql_attempts >= 1


if __name__ == "__main__":
    for fn in [
        test_compile_simple_filter,
        test_compile_join,
        test_block_write_sql,
        test_block_outfile_sql,
        test_risk_sensitive_phone,
        test_risk_hard_block_outfile,
        test_explain_row_estimate,
        test_enforce_limit,
        test_result_match_bag_equality,
        test_plan_routing_complex,
        test_plan_normalize_prune_and_agg,
        test_sql_candidate_rank_prefers_agg,
        test_langgraph_compiles,
        test_plan_validate_requires_agg,
        test_plan_validate_join_connectivity,
        test_m8_output_completeness_and_scope_leak,
        test_m8_strict_column_hallucination,
        test_m11_deterministic_summary,
        test_logical_plan_build,
        test_produce_final_sql_simple,
    ]:
        fn()
        print(f"OK {fn.__name__}")
