"""产出最终 SQL（对齐 Agent 图：plan 校验重试 → SQL 校验重试）。

评测与线上共用：中间失败的 plan/SQL 只记过程，不作为最终评分对象。
流程默认对齐 icecoding M7→M8 重试；可选 XiYan 流程见 AGENT_SQL_XIYAN_PIPELINE。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from wenshu.services.agent.plan_builder import build_query_plan
from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.agent.plan_validate import validate_query_plan
from wenshu.services.agent.sql_ensemble import run_pipeline_sql_select
from wenshu.services.agent.sql_exec_select import xiyan_pipeline_enabled
from wenshu.services.agent.sql_generation import generate_sql
from wenshu.services.agent.sql_validate import validate_sql_ast


@dataclass
class FinalSqlResult:
    ok: bool
    sql: str = ""
    used_tables: list[str] = field(default_factory=list)
    source: str = ""
    plan: QueryPlan | None = None
    plan_errors: list[str] = field(default_factory=list)
    sql_errors: list[str] = field(default_factory=list)
    plan_attempts: int = 0
    sql_attempts: int = 0
    plan_retry_count: int = 0
    sql_retry_count: int = 0
    plan_first_pass: bool = False
    terminal: str = ""
    blocked_reason: str | None = None
    candidates: list[dict] = field(default_factory=list)
    selection: dict | None = None

    def as_dict(self) -> dict:
        data = asdict(self)
        if self.plan is not None:
            data["plan"] = self.plan.as_dict()
        return data


def _sql_hash(sql: str) -> str:
    return hashlib.sha256((sql or "").encode("utf-8")).hexdigest()


def produce_final_sql(
    *,
    question: str,
    evidence: str = "",
    semantic_graph: dict | None = None,
    retrieval: Any,
    meta_engine=None,
    dialect: str = "mysql",
    max_plan_retries: int = 2,
    max_sql_retries: int = 2,
    force_llm_sql: bool | None = None,
    use_xiyan: bool | None = None,
) -> FinalSqlResult:
    """对齐 graph：计划失败只回计划；SQL 静态校验失败只回 SQL 生成。"""
    if force_llm_sql is None:
        force_llm_sql = os.getenv("AGENT_SQL_LLM", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if use_xiyan is None:
        use_xiyan = xiyan_pipeline_enabled()
    # 兼容旧开关：仅开 EXEC_SELECT 也视为 XiYan 流程
    if not use_xiyan and os.getenv("AGENT_SQL_EXEC_SELECT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        use_xiyan = True

    graph = semantic_graph or {}
    plan: QueryPlan | None = None
    previous_plan: QueryPlan | None = None
    plan_errors: list[str] = []
    plan_retry_count = 0
    plan_attempts = 0
    plan_first_pass = False

    while True:
        plan_attempts += 1
        try:
            plan = build_query_plan(
                question=question,
                evidence=evidence or "",
                semantic_graph=graph,
                retrieval=retrieval,
                meta_engine=meta_engine,
                previous_errors=plan_errors or None,
                previous_plan=previous_plan,
            )
        except Exception as exc:  # noqa: BLE001
            plan_errors = [str(exc)]
            plan = None
        if plan is not None:
            plan_errors = validate_query_plan(
                plan,
                question=question,
                semantic_graph=graph,
                retrieval=retrieval,
                meta_engine=meta_engine,
            )
            if not plan_errors:
                plan_first_pass = plan_attempts == 1
                break
            previous_plan = plan
        plan_retry_count += 1
        if plan_retry_count > max_plan_retries:
            return FinalSqlResult(
                ok=False,
                plan=plan,
                plan_errors=list(plan_errors)[:8],
                plan_attempts=plan_attempts,
                plan_retry_count=plan_retry_count,
                plan_first_pass=False,
                terminal="plan_exhausted",
            )

    assert plan is not None

    # —— 可选 XiYan 流程（非 icecoding 默认）——
    if use_xiyan:
        pipe = run_pipeline_sql_select(
            plan=plan,
            question=question,
            retrieval=retrieval,
            dialect=dialect,
            prefer_deterministic=not force_llm_sql,
            use_exec_select=True,
            skip_sandbox=False,
            max_refine=2,
        )
        cands = list(pipe.get("candidates") or [])
        if pipe.get("ok") and pipe.get("sql"):
            return FinalSqlResult(
                ok=True,
                sql=str(pipe["sql"]),
                used_tables=list(pipe.get("used_tables") or []),
                source=str(pipe.get("source") or ""),
                plan=plan,
                plan_attempts=plan_attempts,
                plan_retry_count=plan_retry_count,
                plan_first_pass=plan_first_pass,
                sql_attempts=1,
                sql_retry_count=0,
                terminal="ok",
                candidates=cands,
                selection=pipe.get("selection"),
            )
        if pipe.get("blocked_reason"):
            return FinalSqlResult(
                ok=False,
                sql=str(pipe.get("sql") or ""),
                used_tables=list(pipe.get("used_tables") or []),
                source=str(pipe.get("source") or ""),
                plan=plan,
                plan_attempts=plan_attempts,
                plan_retry_count=plan_retry_count,
                plan_first_pass=plan_first_pass,
                sql_attempts=1,
                sql_errors=[str(pipe.get("error") or pipe["blocked_reason"])],
                blocked_reason=str(pipe["blocked_reason"]),
                terminal="sql_blocked",
                candidates=cands,
                selection=pipe.get("selection"),
            )
        # 选优未出最终 SQL：落入下方单路重试

    sql_retry_count = 0
    sql_attempts = 0
    previous_sql: str | None = None
    validation_errors: list[str] = []
    failed_hashes: list[str] = []
    last_candidates: list[dict] = []

    while True:
        sql_attempts += 1
        gen = generate_sql(
            plan=plan,
            question=question,
            retrieval=retrieval,
            dialect=dialect,
            previous_sql=previous_sql,
            validation_errors=validation_errors or None,
            prefer_deterministic=not force_llm_sql and sql_retry_count == 0,
            allow_llm=True,
            multi_candidate=(sql_retry_count == 0),
            pending_candidates=last_candidates if sql_retry_count > 0 else None,
        )
        if gen.error and not gen.sql:
            sql_retry_count += 1
            if sql_retry_count > max_sql_retries:
                return FinalSqlResult(
                    ok=False,
                    plan=plan,
                    plan_attempts=plan_attempts,
                    plan_retry_count=plan_retry_count,
                    plan_first_pass=plan_first_pass,
                    sql_attempts=sql_attempts,
                    sql_retry_count=sql_retry_count,
                    sql_errors=[gen.error],
                    terminal="generate_error",
                    source=gen.source,
                )
            continue

        last_candidates = list(gen.candidates) if gen.candidates else []
        previous_sql = gen.sql
        v = validate_sql_ast(
            gen.sql,
            plan=plan,
            used_tables=gen.used_tables,
            retrieval=retrieval,
            dialect=dialect,
            generation_source=gen.source,
            failed_sql_hashes=failed_hashes,
        )
        if v.blocked_reason:
            return FinalSqlResult(
                ok=False,
                sql=gen.sql,
                used_tables=list(gen.used_tables),
                source=gen.source,
                plan=plan,
                plan_attempts=plan_attempts,
                plan_retry_count=plan_retry_count,
                plan_first_pass=plan_first_pass,
                sql_attempts=sql_attempts,
                sql_retry_count=sql_retry_count,
                sql_errors=list(v.errors),
                blocked_reason=v.blocked_reason,
                terminal="sql_blocked",
                candidates=list(gen.candidates),
            )
        if v.ok:
            return FinalSqlResult(
                ok=True,
                sql=gen.sql,
                used_tables=list(gen.used_tables),
                source=gen.source,
                plan=plan,
                plan_attempts=plan_attempts,
                plan_retry_count=plan_retry_count,
                plan_first_pass=plan_first_pass,
                sql_attempts=sql_attempts,
                sql_retry_count=sql_retry_count,
                terminal="ok",
                candidates=list(gen.candidates),
            )

        h = _sql_hash(gen.sql)
        if gen.sql and h not in failed_hashes:
            failed_hashes.append(h)
        validation_errors = list(v.errors)
        if v.non_retryable:
            sql_retry_count = max_sql_retries + 1
        else:
            sql_retry_count += 1
        if sql_retry_count > max_sql_retries:
            return FinalSqlResult(
                ok=False,
                sql=gen.sql or "",
                used_tables=list(gen.used_tables),
                source=gen.source,
                plan=plan,
                plan_attempts=plan_attempts,
                plan_retry_count=plan_retry_count,
                plan_first_pass=plan_first_pass,
                sql_attempts=sql_attempts,
                sql_retry_count=sql_retry_count,
                sql_errors=validation_errors[:8],
                terminal="sql_exhausted",
                candidates=list(gen.candidates),
            )
