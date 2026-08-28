"""XiYan 流程级多候选（不改字段召回，不用专用 SFT/选优权重）。

可复刻部分：
1. 确定性编译 + S1/S2 双 schema × 多策略 LLM → 候选池
2. 静态校验过滤
3. 执行失败 self-refine（再生成 1 次）
4. 执行结果聚类选优（sql_exec_select）

专用 QwenCoder / Selection 模型暂不接入。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from wenshu.services.agent.llm_structured import SQLResult, complete_sql
from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.agent.sql_ast import dialect_tips
from wenshu.services.agent.sql_candidate_rank import rank_sql_candidates
from wenshu.services.agent.sql_compiler import UnsupportedPlanError, compile_query_plan
from wenshu.services.agent.sql_exec_select import (
    exec_select_enabled,
    select_sql_by_execution,
)
from wenshu.services.agent.sql_validate import validate_sql_ast
from wenshu.services.comment_llm import llm_available

_PROMPTS = Path(__file__).resolve().parent / "prompts"


def ensemble_llm_enabled(*, exec_select: bool | None = None) -> bool:
    """XiYan 流程专用：是否追加 S1/S2 多策略 LLM。"""
    raw = os.getenv("AGENT_SQL_ENSEMBLE_LLM", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    if exec_select is None:
        from wenshu.services.agent.sql_exec_select import xiyan_pipeline_enabled

        exec_select = xiyan_pipeline_enabled()
    return bool(exec_select)


def _load_prompt(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _cols_fingerprint(cols: list[Any]) -> str:
    pairs = []
    for c in cols or []:
        t = str(getattr(c, "table", "") or "").lower()
        col = str(getattr(c, "column", "") or "").lower()
        if t and col:
            pairs.append(f"{t}.{col}")
    return "|".join(sorted(set(pairs)))


def schema_view_from_retrieval(
    retrieval: Any,
    plan: QueryPlan | None,
    *,
    mode: str = "s2",
) -> dict:
    """mode: s1 | s2 | auto（s2 优先回退 s1/columns）。"""
    s1 = list(getattr(retrieval, "s1_columns", None) or [])
    s2 = list(getattr(retrieval, "s2_columns", None) or [])
    base = list(getattr(retrieval, "columns", None) or [])
    if mode == "s1":
        cols = s1 or base
    elif mode == "s2":
        cols = s2 or s1 or base
    else:
        cols = s2 or s1 or base

    by_table: dict[str, list[str]] = {}
    for c in cols:
        t = str(getattr(c, "table", "") or "")
        col = str(getattr(c, "column", "") or "")
        if t and col:
            by_table.setdefault(t, [])
            if col not in by_table[t]:
                by_table[t].append(col)
    tables = [{"name": t, "columns": [{"name": c} for c in colnames]} for t, colnames in by_table.items()]
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
    return {"query_mschema": {"tables": tables, "relations": relations}, "schema_mode": mode}


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


def _add_cand(
    pool: list[dict],
    *,
    sql: str,
    used_tables: list[str],
    source: str,
    schema_mode: str | None = None,
) -> None:
    key = _norm_sql(sql)
    if not key:
        return
    if any(_norm_sql(str(c.get("sql") or "")) == key for c in pool):
        return
    row = {
        "sql": sql,
        "used_tables": list(used_tables),
        "source": source,
    }
    if schema_mode:
        row["schema_mode"] = schema_mode
    pool.append(row)


def _llm_sql_once(
    *,
    plan: QueryPlan,
    question: str,
    retrieval: Any,
    dialect: str,
    schema_mode: str,
    strategy: str,
    previous_sql: str | None = None,
    validation_errors: list[str] | None = None,
    execution_error: str | None = None,
) -> SQLResult:
    tpl = _load_prompt("sql_from_plan.txt")
    prompt = tpl.format(
        dialect=dialect,
        dialect_tips=dialect_tips(dialect),
        query_plan=json.dumps(plan.as_dict(), ensure_ascii=False, default=str),
        schema_view=json.dumps(
            schema_view_from_retrieval(retrieval, plan, mode=schema_mode),
            ensure_ascii=False,
            default=str,
        ),
        retry_feedback=_retry_feedback(
            previous_sql=previous_sql,
            validation_errors=validation_errors,
            execution_error=execution_error,
            dialect=dialect,
        ),
        user_query=json.dumps(question, ensure_ascii=False),
    )
    return complete_sql(prompt + strategy, retries=1)


def collect_sql_candidates(
    *,
    plan: QueryPlan,
    question: str,
    retrieval: Any,
    dialect: str = "mysql",
    prefer_deterministic: bool = True,
    allow_llm: bool = True,
    with_s1_s2: bool | None = None,
) -> list[dict]:
    """组装候选池：deterministic +（可选）S1/S2 × 双策略 LLM。"""
    if with_s1_s2 is None:
        with_s1_s2 = ensemble_llm_enabled()

    pool: list[dict] = []
    if prefer_deterministic:
        try:
            sql, used = compile_query_plan(plan, dialect=dialect)
            _add_cand(pool, sql=sql, used_tables=list(used), source="deterministic")
        except (UnsupportedPlanError, ValueError):
            pass

    if not (allow_llm and llm_available() and (with_s1_s2 or not pool)):
        return pool

    s1 = list(getattr(retrieval, "s1_columns", None) or [])
    s2 = list(getattr(retrieval, "s2_columns", None) or [])
    modes: list[str] = []
    fp1, fp2 = _cols_fingerprint(s1), _cols_fingerprint(s2)
    if s1:
        modes.append("s1")
    if s2 and fp2 != fp1:
        modes.append("s2")
    if not modes:
        modes = ["s2"]

    strategies = [
        (
            "model_conservative",
            "\n\n候选策略：采用最保守、最直接且与 QueryPlan 一致的 SQL 结构。"
            "当前 schema_view 来自精选列集合，勿使用未出现的表/列。",
        ),
        (
            "model_rederive",
            "\n\n候选策略：独立重新推导等价 SQL，重点检查 JOIN、聚合粒度和过滤条件，"
            "不得改变 QueryPlan 业务语义。",
        ),
    ]

    for mode in modes:
        for src_tag, strategy in strategies:
            try:
                r = _llm_sql_once(
                    plan=plan,
                    question=question,
                    retrieval=retrieval,
                    dialect=dialect,
                    schema_mode=mode,
                    strategy=strategy,
                )
                _add_cand(
                    pool,
                    sql=r.sql,
                    used_tables=list(r.used_tables),
                    source=f"{src_tag}_{mode}",
                    schema_mode=mode,
                )
            except Exception:
                continue
    return pool


def self_refine_on_exec_errors(
    *,
    candidates: list[dict],
    plan: QueryPlan,
    question: str,
    retrieval: Any,
    dialect: str = "mysql",
    execute_fn=None,
    max_refine: int = 2,
) -> list[dict]:
    """对执行失败的候选做最多 max_refine 次 self-refine，成功则并入池。"""
    if not llm_available() or max_refine <= 0:
        return candidates
    if execute_fn is None:
        from wenshu.services.agent.sandbox import execute_readonly

        execute_fn = execute_readonly

    out = list(candidates)
    refined = 0
    for c in list(candidates):
        if refined >= max_refine:
            break
        sql = str(c.get("sql") or "").strip()
        if not sql:
            continue
        try:
            ex = execute_fn(sql)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            ok = False
        else:
            ok = bool(getattr(ex, "ok", False))
            err = getattr(ex, "error", None) if not ok else None
        if ok:
            continue
        mode = str(c.get("schema_mode") or "s2")
        try:
            r = _llm_sql_once(
                plan=plan,
                question=question,
                retrieval=retrieval,
                dialect=dialect,
                schema_mode=mode,
                strategy="\n\n候选策略：根据执行错误修复 SQL，保持业务语义不变。",
                previous_sql=sql,
                execution_error=str(err or "execution failed"),
            )
        except Exception:
            continue
        before = len(out)
        _add_cand(
            out,
            sql=r.sql,
            used_tables=list(r.used_tables),
            source=f"refine_{mode}",
            schema_mode=mode,
        )
        if len(out) > before:
            refined += 1
    return out


def validate_candidates(
    candidates: list[dict],
    *,
    plan: QueryPlan,
    retrieval: Any,
    dialect: str = "mysql",
    failed_hashes: list[str] | None = None,
) -> tuple[list[dict], str | None, list[str]]:
    """返回 (validated, blocked_reason, last_errors)。"""
    failed_hashes = failed_hashes or []
    validated: list[dict] = []
    blocked_reason: str | None = None
    last_errors: list[str] = []
    for c in candidates:
        sql = str(c.get("sql") or "").strip()
        if not sql:
            continue
        used = [str(t) for t in (c.get("used_tables") or [])]
        src = str(c.get("source") or "model")
        v = validate_sql_ast(
            sql,
            plan=plan,
            used_tables=used,
            retrieval=retrieval,
            dialect=dialect,
            generation_source="deterministic" if src == "deterministic" else "model",
            failed_sql_hashes=failed_hashes,
        )
        if v.blocked_reason:
            blocked_reason = v.blocked_reason
            last_errors = list(v.errors)
            continue
        if v.ok:
            sc, reasons = None, None
            try:
                from wenshu.services.agent.sql_candidate_rank import score_sql_candidate

                sc, reasons = score_sql_candidate(
                    sql, plan=plan, used_tables=used, dialect=dialect
                )
            except Exception:
                pass
            validated.append(
                {
                    "sql": sql,
                    "used_tables": used,
                    "source": src,
                    "schema_mode": c.get("schema_mode"),
                    "score": sc,
                    "score_reasons": reasons,
                }
            )
        else:
            last_errors = list(v.errors)
    return validated, blocked_reason, last_errors


def run_pipeline_sql_select(
    *,
    plan: QueryPlan,
    question: str,
    retrieval: Any,
    dialect: str = "mysql",
    prefer_deterministic: bool = True,
    use_exec_select: bool | None = None,
    skip_sandbox: bool = False,
    max_refine: int = 2,
) -> dict[str, Any]:
    """端到端：收候选 → refine → 校验 → 执行选优。

    返回 dict: ok, sql, used_tables, source, candidates, selection, error, blocked_reason
    """
    if use_exec_select is None:
        use_exec_select = exec_select_enabled() and not skip_sandbox

    raw = collect_sql_candidates(
        plan=plan,
        question=question,
        retrieval=retrieval,
        dialect=dialect,
        prefer_deterministic=prefer_deterministic,
        allow_llm=True,
        with_s1_s2=ensemble_llm_enabled(exec_select=use_exec_select),
    )
    if not raw:
        return {
            "ok": False,
            "sql": "",
            "used_tables": [],
            "source": "",
            "candidates": [],
            "selection": None,
            "error": "无可用 SQL 候选",
            "blocked_reason": None,
        }

    validated, blocked, errs = validate_candidates(
        raw, plan=plan, retrieval=retrieval, dialect=dialect
    )

    # 先对已通过静态校验、但执行失败的候选做 self-refine
    if use_exec_select and validated:
        refined_raw = self_refine_on_exec_errors(
            candidates=validated,
            plan=plan,
            question=question,
            retrieval=retrieval,
            dialect=dialect,
            max_refine=max_refine,
        )
        if len(refined_raw) > len(validated):
            # 仅对新 refine 出的再校验
            extra = refined_raw[len(validated) :]
            extra_ok, _, _ = validate_candidates(
                extra, plan=plan, retrieval=retrieval, dialect=dialect
            )
            validated = validated + extra_ok
            raw = raw + extra

    if not validated:
        return {
            "ok": False,
            "sql": str(raw[0].get("sql") or "") if raw else "",
            "used_tables": list(raw[0].get("used_tables") or []) if raw else [],
            "source": str(raw[0].get("source") or "") if raw else "",
            "candidates": raw,
            "selection": None,
            "error": "；".join(errs[:5]) if errs else "候选均未通过静态校验",
            "blocked_reason": blocked,
        }

    # 附 AST 分到 candidates 展示
    ranked_meta = []
    try:
        fake = [SQLResult(sql=c["sql"], used_tables=c.get("used_tables") or []) for c in validated]
        ranked = rank_sql_candidates(fake, plan=plan, dialect=dialect)
        score_map = {
            _norm_sql(r.sql): (sc, reasons) for r, sc, reasons in ranked
        }
        for c in validated:
            sc, reasons = score_map.get(_norm_sql(c["sql"]), (c.get("score"), c.get("score_reasons")))
            c["score"] = sc
            c["score_reasons"] = reasons
            ranked_meta.append(c)
    except Exception:
        ranked_meta = validated

    if use_exec_select:
        sel = select_sql_by_execution(validated, plan=plan, dialect=dialect, max_exec=8)
        if sel.ok and sel.sql:
            return {
                "ok": True,
                "sql": sel.sql,
                "used_tables": list(sel.used_tables),
                "source": sel.source,
                "candidates": ranked_meta or raw,
                "selection": sel.as_dict(),
                "error": None,
                "blocked_reason": None,
            }

    best = ranked_meta[0] if ranked_meta else validated[0]
    return {
        "ok": True,
        "sql": best["sql"],
        "used_tables": list(best.get("used_tables") or []),
        "source": str(best.get("source") or "model"),
        "candidates": ranked_meta or raw,
        "selection": {"reason": "ast_only", "ok": True},
        "error": None,
        "blocked_reason": None,
    }
