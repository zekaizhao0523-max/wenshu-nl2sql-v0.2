"""SQL 多候选 AST 打分选优（对齐 icecoding rank_sql_candidates 思路）。"""

from __future__ import annotations

from sqlglot import exp

from wenshu.services.agent.llm_structured import SQLResult
from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.agent.sql_ast import SqlDialect


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def score_sql_candidate(
    sql: str,
    *,
    plan: QueryPlan,
    used_tables: list[str] | None = None,
    dialect: str = "mysql",
) -> tuple[float, list[str]]:
    """分数越高越好；同时返回扣分原因便于调试。"""
    reasons: list[str] = []
    score = 0.0
    text = (sql or "").strip()
    if not text:
        return -100.0, ["empty"]

    sqlsvc = SqlDialect(dialect)
    try:
        expr = sqlsvc.parse(text, dialect)
    except Exception as exc:  # noqa: BLE001
        return -50.0, [f"parse:{exc}"]

    danger = sqlsvc.is_dangerous(expr)
    if danger:
        return -100.0, [f"dangerous:{danger}"]

    score += 10.0
    ref_tables = {_norm(t) for t in sqlsvc.extract_tables(expr)}
    plan_tables = {_norm(t) for t in plan.target_tables}
    used = {_norm(t) for t in (used_tables or [])}

    if plan_tables and plan_tables.issubset(ref_tables | used):
        score += 20.0
    elif plan_tables & (ref_tables | used):
        score += 8.0
        reasons.append("partial_table_cover")
    else:
        score -= 15.0
        reasons.append("miss_plan_tables")

    extra = (ref_tables - plan_tables) if plan_tables else set()
    if extra:
        score -= 6.0 * len(extra)
        reasons.append(f"extra_tables:{sorted(extra)}")

    want_agg = any(f.aggregation for f in plan.output_fields)
    has_agg = bool(list(expr.find_all(exp.AggFunc)))
    if want_agg and has_agg:
        score += 15.0
    elif want_agg and not has_agg:
        score -= 20.0
        reasons.append("missing_aggregate")
    elif (not want_agg) and has_agg:
        score -= 5.0
        reasons.append("unexpected_aggregate")

    has_where = any(s.args.get("where") is not None for s in expr.find_all(exp.Select))
    if bool(plan.filters) == has_where:
        score += 5.0
    else:
        score -= 8.0
        reasons.append("where_mismatch")

    has_group = any(s.args.get("group") is not None for s in expr.find_all(exp.Select))
    if bool(plan.group_by) == has_group:
        score += 5.0
    else:
        score -= 6.0
        reasons.append("groupby_mismatch")

    if len(text) > 800:
        score -= 5.0
        reasons.append("too_long")
    if text.upper().count("JOIN") > max(0, len(plan.target_tables) - 1) + 1:
        score -= 8.0
        reasons.append("too_many_joins")

    return score, reasons


def rank_sql_candidates(
    candidates: list[SQLResult],
    *,
    plan: QueryPlan,
    dialect: str = "mysql",
) -> list[tuple[SQLResult, float, list[str]]]:
    ranked: list[tuple[SQLResult, float, list[str]]] = []
    for c in candidates:
        sc, reasons = score_sql_candidate(
            c.sql, plan=plan, used_tables=c.used_tables, dialect=dialect
        )
        ranked.append((c, sc, reasons))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked
