"""QueryPlan 构建（对齐 icecoding M5b）：确定性优先，失败/重试走 LLM Structured Output。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from wenshu.services.agent.llm_structured import complete_structured
from wenshu.services.agent.plan_models import (
    FilterSpec,
    JoinSpec,
    OutputFieldSpec,
    QueryPlan,
)
from wenshu.services.comment_llm import llm_available

_PROMPTS = Path(__file__).resolve().parent / "prompts"

_AGG_HINT = re.compile(r"(统计|合计|汇总|多少|数量|平均|占比|分布)")
_COUNT_HINT = re.compile(r"(多少|数量|有多少|一共|共有)")


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _pick_columns(retrieval: Any, prefer_s2: bool = True) -> list[Any]:
    cols = []
    if prefer_s2:
        cols = list(getattr(retrieval, "s2_columns", None) or [])
    if not cols:
        cols = list(getattr(retrieval, "s1_columns", None) or [])
    if not cols:
        cols = list(getattr(retrieval, "columns", None) or [])
    return cols


def _match_col(cols: list[Any], phrase: str) -> Any | None:
    p = (phrase or "").strip().lower()
    if not p:
        return None
    for c in cols:
        name = _norm(getattr(c, "column", None))
        table = _norm(getattr(c, "table", None))
        if p == name or p in name or name in p:
            return c
        # 中文概念常在 source 或后续 description；先用列名模糊
        if table and p in table:
            return c
    return None


def _relations_for_tables(meta_engine, tables: list[str]) -> list[JoinSpec]:
    if not tables or meta_engine is None:
        return []
    from wenshu.services.l1_meta import list_relations

    wanted = {_norm(t) for t in tables}
    joins: list[JoinSpec] = []
    try:
        rows = list_relations(meta_engine)
    except Exception:
        return []
    for r in rows:
        if r.get("is_enabled") is False:
            continue
        lt = _norm(r.get("left_table"))
        rt = _norm(r.get("right_table"))
        if lt in wanted and rt in wanted:
            joins.append(
                JoinSpec(
                    left_table=str(r.get("left_table")),
                    left_column=str(r.get("left_column")),
                    right_table=str(r.get("right_table")),
                    right_column=str(r.get("right_column")),
                    join_type="inner",
                )
            )
    return joins


def _schema_view(retrieval: Any, meta_engine=None) -> dict:
    cols = _pick_columns(retrieval)
    by_table: dict[str, list[str]] = {}
    for c in cols:
        t = str(getattr(c, "table", "") or "")
        col = str(getattr(c, "column", "") or "")
        if t and col:
            by_table.setdefault(t, [])
            if col not in by_table[t]:
                by_table[t].append(col)
    tables = list(getattr(retrieval, "expanded_tables", None) or []) or list(
        getattr(retrieval, "selected_tables", None) or []
    )
    for t in tables:
        by_table.setdefault(str(t), [])
    joins = _relations_for_tables(meta_engine, list(by_table.keys()))
    return {
        "query_mschema": {
            "tables": [
                {"name": t, "columns": [{"name": c} for c in colnames]}
                for t, colnames in by_table.items()
            ],
            "relations": [
                {
                    "source_table": j.left_table,
                    "source_columns": [j.left_column],
                    "target_table": j.right_table,
                    "target_columns": [j.right_column],
                }
                for j in joins
            ],
        }
    }


def build_query_plan_deterministic(
    *,
    question: str,
    evidence: str = "",
    semantic_graph: dict | None = None,
    retrieval: Any,
    meta_engine=None,
    default_limit: int = 100,
) -> QueryPlan:
    graph = semantic_graph or {}
    roles = getattr(retrieval, "query_roles", None) or {}
    if not graph and isinstance(roles, dict):
        graph = roles.get("semantic_graph") or roles.get("intent") or {}

    cols = _pick_columns(retrieval)
    tables = list(getattr(retrieval, "expanded_tables", None) or [])
    if not tables:
        tables = list(getattr(retrieval, "selected_tables", None) or [])
    if not tables and cols:
        seen = set()
        for c in cols:
            t = getattr(c, "table", None)
            if t and _norm(t) not in seen:
                seen.add(_norm(t))
                tables.append(t)
    if not tables:
        raise ValueError("召回结果无可用表，无法生成 QueryPlan")

    query_type = str(graph.get("query_type") or "unknown")
    query_action = str(graph.get("query_action") or "unknown")
    q = f"{question}\n{evidence}"
    want_agg = bool(_AGG_HINT.search(q)) or query_action in {"aggregate", "rank"} or query_type in {
        "aggregation",
        "multi_fact",
        "fact_filter",
    }
    want_count = bool(_COUNT_HINT.search(q))

    output_fields: list[OutputFieldSpec] = []
    used: set[tuple[str, str]] = set()

    def _add_field(col_obj: Any, *, concept: str, aggregation: str | None = None) -> None:
        table = str(getattr(col_obj, "table", "") or "")
        column = str(getattr(col_obj, "column", "") or "")
        key = (_norm(table), _norm(column))
        if not table or not column or key in used:
            return
        used.add(key)
        alias = f"{column}_{aggregation}" if aggregation else column
        output_fields.append(
            OutputFieldSpec(
                concept=concept or column,
                table=table,
                column=column,
                alias=alias,
                aggregation=aggregation,  # type: ignore[arg-type]
            )
        )

    # 过滤条件
    filters: list[FilterSpec] = []
    for item in graph.get("filters") or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "")
            op = str(item.get("operator") or "=")
            value = item.get("value")
        else:
            text = str(getattr(item, "text", "") or "")
            op = str(getattr(item, "operator", None) or "=")
            value = getattr(item, "value", None)
        hit = _match_col(cols, text)
        if hit is None:
            continue
        filters.append(
            FilterSpec(
                table=str(getattr(hit, "table", "")),
                column=str(getattr(hit, "column", "")),
                operator=op if op in {"=", "!=", ">", ">=", "<", "<=", "<>"} else "=",  # type: ignore[arg-type]
                value=value,
            )
        )
        if want_agg and not want_count:
            _add_field(hit, concept=text)

    # 度量 / 属性 → 输出列
    for key, default_agg in (("measures", "sum" if want_agg else None), ("attributes", None), ("dimensions", None)):
        for item in graph.get(key) or []:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", "")
            text = str(text or "")
            hit = _match_col(cols, text)
            if hit is None:
                continue
            agg = default_agg if key == "measures" and want_agg and not want_count else None
            _add_field(hit, concept=text, aggregation=agg)

    # fallback：取召回前若干列
    if not output_fields:
        if want_count:
            c0 = cols[0]
            output_fields.append(
                OutputFieldSpec(
                    concept="count",
                    table=str(getattr(c0, "table", tables[0])),
                    column=str(getattr(c0, "column", "*") or "*"),
                    alias="cnt",
                    aggregation="count",
                )
            )
        else:
            for c in cols[:6]:
                _add_field(c, concept=str(getattr(c, "column", "")))

    if want_count and not any(f.aggregation == "count" for f in output_fields):
        c0 = cols[0] if cols else None
        if c0 is not None:
            output_fields.insert(
                0,
                OutputFieldSpec(
                    concept="count",
                    table=str(getattr(c0, "table", tables[0])),
                    column=str(getattr(c0, "column", "cust_id") or "cust_id"),
                    alias="cnt",
                    aggregation="count",
                ),
            )

    group_by: list[str] = []
    if any(f.aggregation for f in output_fields):
        for f in output_fields:
            if not f.aggregation and f.table and f.column:
                group_by.append(f"{f.table}.{f.column}")

    # 确保 filters / outputs 涉及的表都在 target_tables
    needed = {_norm(t) for t in tables}
    for f in output_fields:
        if f.table:
            needed.add(_norm(f.table))
    for flt in filters:
        if flt.table:
            needed.add(_norm(flt.table))
    # 保序
    ordered_tables: list[str] = []
    seen_t: set[str] = set()
    for t in tables:
        n = _norm(t)
        if n in needed and n not in seen_t:
            seen_t.add(n)
            ordered_tables.append(t)
    for f in output_fields:
        n = _norm(f.table)
        if n and n not in seen_t and f.table:
            seen_t.add(n)
            ordered_tables.append(f.table)

    joins = _relations_for_tables(meta_engine, ordered_tables)
    conf = float(graph.get("confidence") or 0.5)
    if output_fields and ordered_tables:
        conf = min(1.0, conf + 0.2)

    return QueryPlan(
        target_tables=ordered_tables,
        join_logic=joins,
        filters=filters,
        group_by=group_by,
        output_fields=output_fields,
        limit=default_limit,
        confidence=conf,
        source="deterministic",
    )


def build_query_plan_llm(
    *,
    question: str,
    evidence: str = "",
    semantic_graph: dict | None = None,
    retrieval: Any,
    meta_engine=None,
    previous_errors: list[str] | None = None,
    previous_plan: QueryPlan | dict | None = None,
) -> QueryPlan:
    """对齐 M5b：complete_structured(QueryPlan)。"""
    if not llm_available():
        raise RuntimeError("LLM 未配置，无法生成结构化 QueryPlan")
    tpl = (_PROMPTS / "plan_generation.txt").read_text(encoding="utf-8")
    retry = ""
    if previous_errors:
        prev = previous_plan
        if isinstance(prev, QueryPlan):
            prev = prev.as_dict()
        retry = (_PROMPTS / "plan_retry.txt").read_text(encoding="utf-8").format(
            previous_plan=json.dumps(prev or {}, ensure_ascii=False, default=str),
            errors=json.dumps(previous_errors[-5:], ensure_ascii=False),
        )
    prompt = tpl.format(
        user_query=json.dumps(question, ensure_ascii=False),
        evidence=json.dumps(evidence or "", ensure_ascii=False),
        schema_view=json.dumps(
            _schema_view(retrieval, meta_engine), ensure_ascii=False, default=str
        ),
        semantic_graph=json.dumps(semantic_graph or {}, ensure_ascii=False, default=str),
        retry_feedback=retry,
    )
    plan = complete_structured(prompt, QueryPlan, retries=1, timeout=120)
    plan.source = "model"
    return plan


def build_query_plan(
    *,
    question: str,
    evidence: str = "",
    semantic_graph: dict | None = None,
    retrieval: Any,
    meta_engine=None,
    default_limit: int = 100,
    previous_errors: list[str] | None = None,
    previous_plan: QueryPlan | dict | None = None,
    force_llm: bool | None = None,
) -> QueryPlan:
    """简单题确定性；复杂题 / 重试 / AGENT_PLAN_LLM → LLM Structured QueryPlan。

    生成后统一 normalize（剪枝/聚合/语义覆盖）。
    """
    from wenshu.services.agent.plan_normalize import normalize_plan
    from wenshu.services.agent.plan_routing import is_complex_query

    if force_llm is None:
        force_llm = os.getenv("AGENT_PLAN_LLM", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    complex_q = is_complex_query(
        question=question,
        evidence=evidence,
        semantic_graph=semantic_graph,
        retrieval=retrieval,
    )
    # 复杂题主路径 LLM；重试必 LLM；显式 force 亦然
    use_llm = force_llm or bool(previous_errors) or complex_q
    plan: QueryPlan | None = None
    if use_llm and llm_available():
        try:
            plan = build_query_plan_llm(
                question=question,
                evidence=evidence,
                semantic_graph=semantic_graph,
                retrieval=retrieval,
                meta_engine=meta_engine,
                previous_errors=previous_errors,
                previous_plan=previous_plan,
            )
        except Exception:
            if force_llm and not previous_errors and not complex_q:
                raise
            plan = None
    if plan is None:
        plan = build_query_plan_deterministic(
            question=question,
            evidence=evidence,
            semantic_graph=semantic_graph,
            retrieval=retrieval,
            meta_engine=meta_engine,
            default_limit=default_limit,
        )
    plan, _notes = normalize_plan(
        plan,
        question=question,
        semantic_graph=semantic_graph,
        retrieval=retrieval,
        meta_engine=meta_engine,
    )
    from wenshu.services.agent.logical_plan import infer_output_grain

    plan = plan.model_copy(
        update={
            "output_grain": infer_output_grain(
                plan, question=question, semantic_graph=semantic_graph
            )
        }
    )
    return plan


# 兼容旧导入路径
from wenshu.services.agent.plan_validate import validate_query_plan  # noqa: E402
