"""SQL 生成（对齐 icecoding M7）：确定性编译优先，失败则 LLM Structured Output。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wenshu.services.agent.llm_structured import SQLResult, complete_sql
from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.agent.sql_ast import dialect_tips
from wenshu.services.agent.sql_candidate_rank import rank_sql_candidates
from wenshu.services.agent.sql_compiler import UnsupportedPlanError, compile_query_plan
from wenshu.services.comment_llm import llm_available

_PROMPTS = Path(__file__).resolve().parent / "prompts"


@dataclass
class SqlGenerationResult:
    sql: str
    used_tables: list[str] = field(default_factory=list)
    source: str = "deterministic"  # deterministic | model
    candidates: list[dict] = field(default_factory=list)
    error: str | None = None


def _load_prompt(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _schema_view_from_retrieval(retrieval: Any, plan: QueryPlan | None) -> dict:
    """对齐 icecoding execution schema：默认用 S2 精选列（非 S1/S2 双路生成）。"""
    cols = list(getattr(retrieval, "s2_columns", None) or []) or list(
        getattr(retrieval, "columns", None) or []
    )
    by_table: dict[str, list[str]] = {}
    for c in cols:
        t = str(getattr(c, "table", "") or "")
        col = str(getattr(c, "column", "") or "")
        if t and col:
            by_table.setdefault(t, [])
            if col not in by_table[t]:
                by_table[t].append(col)
    tables = []
    for t, colnames in by_table.items():
        tables.append({"name": t, "columns": [{"name": c} for c in colnames]})
    relations = []
    if plan:
        for j in plan.join_logic:
            relations.append(
                {
                    "source_table": j.left_table,
                    "source_columns": [j.left_column],
                    "target_table": j.right_table,
                    "target_columns": [j.right_column],
                }
            )
    return {"query_mschema": {"tables": tables, "relations": relations}}


def _retry_feedback(
    *,
    previous_sql: str | None,
    validation_errors: list[str] | None,
    execution_error: str | None,
    dialect: str,
) -> str:
    reasons = [*(validation_errors or [])]
    if execution_error:
        reasons.append(execution_error)
    if not reasons:
        return ""
    tpl = _load_prompt("sql_retry.txt")
    return tpl.format(
        previous_sql=previous_sql or "(空)",
        reasons=json.dumps(reasons[-5:], ensure_ascii=False),
        dialect=dialect,
        dialect_tips=dialect_tips(dialect),
    )


def _norm_sql(sql: str) -> str:
    return " ".join((sql or "").strip().lower().split())


def _candidate_policy() -> tuple[bool, int]:
    """对齐 icecoding sql_candidate_refinement：首轮可多候选，重试轮单候选。"""
    raw = os.getenv("AGENT_SQL_CANDIDATE_COUNT", "2").strip()
    try:
        count = max(1, min(3, int(raw)))
    except ValueError:
        count = 2
    enabled = os.getenv("AGENT_SQL_MULTI_CANDIDATE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return enabled, count


def generate_sql(
    *,
    plan: QueryPlan,
    question: str,
    retrieval: Any,
    dialect: str = "mysql",
    previous_sql: str | None = None,
    validation_errors: list[str] | None = None,
    execution_error: str | None = None,
    prefer_deterministic: bool = True,
    allow_llm: bool = True,
    multi_candidate: bool | None = None,
    pending_candidates: list[dict] | None = None,
) -> SqlGenerationResult:
    """对齐 icecoding M7。

    - 确定性编译成功 → 直接返回（不追加 LLM 候选）
    - 首轮 LLM：最多 2～3 条策略候选 → AST 打分选优
    - 重试轮：优先复用未选中的历史候选；否则单条 LLM + retry_feedback
    """
    is_retry = bool(
        previous_sql
        or validation_errors
        or execution_error
        or (pending_candidates and any(c.get("sql") for c in pending_candidates))
    )
    if multi_candidate is None:
        enabled, _ = _candidate_policy()
        multi_candidate = enabled and not is_retry

    # 重试：复用上一轮未选中候选（对齐 icecoding query_candidates 回退）
    if is_retry and pending_candidates:
        alts = [
            c
            for c in pending_candidates
            if (c.get("sql") or "").strip() and not c.get("selected")
        ]
        alts.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
        if alts:
            pick = alts[0]
            marked = []
            for c in pending_candidates:
                row = dict(c)
                row["selected"] = _norm_sql(str(c.get("sql") or "")) == _norm_sql(
                    str(pick.get("sql") or "")
                )
                marked.append(row)
            return SqlGenerationResult(
                sql=str(pick["sql"]),
                used_tables=[str(t) for t in (pick.get("used_tables") or [])],
                source=str(pick.get("source") or "model"),
                candidates=marked,
            )

    if prefer_deterministic:
        try:
            sql, used = compile_query_plan(plan, dialect=dialect)
            return SqlGenerationResult(
                sql=sql,
                used_tables=list(used),
                source="deterministic",
                candidates=[
                    {
                        "sql": sql,
                        "used_tables": list(used),
                        "source": "deterministic",
                        "rank": 1,
                        "score": float(plan.confidence or 1.0),
                        "selected": True,
                    }
                ],
            )
        except (UnsupportedPlanError, ValueError):
            pass

    if not allow_llm or not llm_available():
        return SqlGenerationResult(
            sql="",
            source="deterministic",
            error="确定性编译失败且 LLM 不可用",
        )

    tpl = _load_prompt("sql_from_plan.txt")
    prompt = tpl.format(
        dialect=dialect,
        dialect_tips=dialect_tips(dialect),
        query_plan=json.dumps(plan.as_dict(), ensure_ascii=False, default=str),
        schema_view=json.dumps(
            _schema_view_from_retrieval(retrieval, plan), ensure_ascii=False, default=str
        ),
        retry_feedback=_retry_feedback(
            previous_sql=previous_sql,
            validation_errors=validation_errors,
            execution_error=execution_error,
            dialect=dialect,
        ),
        user_query=json.dumps(question, ensure_ascii=False),
    )

    _, candidate_count = _candidate_policy()
    n = candidate_count if multi_candidate else 1
    strategies = [
        "\n\n候选策略：采用最保守、最直接且与 QueryPlan 一致的 SQL 结构。",
        "\n\n候选策略：独立重新推导等价 SQL，重点检查 JOIN、聚合粒度和过滤条件，不得改变 QueryPlan 业务语义。",
        "\n\n候选策略：重点核对 GROUP BY / HAVING / 过滤值与 QueryPlan 一致。",
    ]
    results: list[SQLResult] = []
    last_err: Exception | None = None
    for index in range(n):
        try:
            results.append(complete_sql(prompt + strategies[min(index, len(strategies) - 1)], retries=1))
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if not results and index == n - 1:
                return SqlGenerationResult(
                    sql="",
                    source="model",
                    error=f"LLM SQL 生成失败: {last_err}",
                )
    if not results:
        return SqlGenerationResult(
            sql="",
            source="model",
            error=f"LLM SQL 生成失败: {last_err}",
        )

    ranked = rank_sql_candidates(results, plan=plan, dialect=dialect)
    best, best_score, best_reasons = ranked[0]
    source = "model_sql_candidate" if len(ranked) > 1 else "model_sql_fallback"
    if is_retry:
        source = "model_sql_refiner"
    return SqlGenerationResult(
        sql=best.sql,
        used_tables=list(best.used_tables),
        source=source,
        candidates=[
            {
                "sql": r.sql,
                "used_tables": list(r.used_tables),
                "rank": i + 1,
                "score": sc,
                "score_reasons": reasons,
                "source": source,
                "selected": i == 0,
            }
            for i, (r, sc, reasons) in enumerate(ranked)
        ],
    )
