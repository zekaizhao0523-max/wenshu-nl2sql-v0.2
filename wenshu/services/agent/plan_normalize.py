"""QueryPlan normalize：剪枝多余表、补聚合、语义图覆盖约束。"""

from __future__ import annotations

import re
from typing import Any

from wenshu.services.agent.plan_builder import _relations_for_tables
from wenshu.services.agent.plan_models import OutputFieldSpec, QueryPlan

_AGG_AVG = re.compile(r"(平均|均值)")
_AGG_SUM = re.compile(r"(合计|总和|总额|汇总|总计|求和)")
_AGG_COUNT = re.compile(r"(人数|家数|笔数|有多少|多少人|多少笔|数量|统计)")


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _pick_agg(question: str) -> str | None:
    if _AGG_AVG.search(question or ""):
        return "avg"
    if _AGG_SUM.search(question or ""):
        return "sum"
    if _AGG_COUNT.search(question or ""):
        return "count"
    return None


def _known_tables(retrieval: Any) -> set[str]:
    out = {_norm(t) for t in (getattr(retrieval, "expanded_tables", None) or [])}
    out |= {_norm(t) for t in (getattr(retrieval, "selected_tables", None) or [])}
    for c in list(getattr(retrieval, "s2_columns", None) or []) or list(
        getattr(retrieval, "columns", None) or []
    ):
        out.add(_norm(getattr(c, "table", None)))
    return {t for t in out if t}


def normalize_plan(
    plan: QueryPlan,
    *,
    question: str,
    semantic_graph: dict | None = None,
    retrieval: Any = None,
    meta_engine=None,
) -> tuple[QueryPlan, list[str]]:
    """返回 (规范化计划, 变更说明)。"""
    notes: list[str] = []
    graph = semantic_graph or {}
    data = plan.model_dump()
    tables = [str(t) for t in (data.get("target_tables") or []) if t]
    known = _known_tables(retrieval) if retrieval is not None else set()

    # 1) 表白名单：只能用召回内表
    if known:
        kept = [t for t in tables if _norm(t) in known]
        if kept and len(kept) < len(tables):
            notes.append(f"剪枝未召回表: {sorted({_norm(t) for t in tables} - known)}")
            tables = kept

    # 2) 单表意图：问句无强多表信号且召回主表明确 → 压到 1 表
    selected = list(getattr(retrieval, "selected_tables", None) or []) if retrieval else []
    want_agg = _pick_agg(question)
    force_multi = bool(graph.get("force_multi_table")) or str(graph.get("query_type") or "") in {
        "multi_fact",
    }
    if not force_multi and want_agg and selected and len(tables) > 1:
        primary = str(selected[0])
        tables = [t for t in tables if _norm(t) == _norm(primary)] or [primary]
        notes.append(f"聚合单表意图，收敛到 {tables[0]}")

    table_set = {_norm(t) for t in tables}
    # 3) 输出列：去掉不在 target_tables 的字段；补聚合
    outputs: list[dict] = []
    for f in data.get("output_fields") or []:
        if f.get("table") and _norm(f.get("table")) not in table_set:
            notes.append(f"去掉越界输出列 {f.get('table')}.{f.get('column')}")
            continue
        outputs.append(dict(f))
    if want_agg and outputs:
        if not any(o.get("aggregation") for o in outputs):
            outputs[0]["aggregation"] = want_agg
            outputs[0]["alias"] = f"{outputs[0].get('column') or 'v'}_{want_agg}"
            notes.append(f"补聚合 {want_agg}")
        # 聚合查询去掉无聚合的明细列（保留 group 维度过少时只留聚合列）
        if want_agg in {"count", "sum", "avg"} and len(outputs) > 1:
            dims = [o for o in outputs if not o.get("aggregation")]
            aggs = [o for o in outputs if o.get("aggregation")]
            if aggs and len(dims) > 2:
                outputs = aggs + dims[:1]
                notes.append("聚合查询收敛明细列")

    # 4) 语义图覆盖：measures / attributes 尽量出现在 output
    cols_pool = list(getattr(retrieval, "s2_columns", None) or []) or list(
        getattr(retrieval, "columns", None) or []
    )
    existing = {(_norm(o.get("table")), _norm(o.get("column"))) for o in outputs}

    def _match(phrase: str):
        p = (phrase or "").strip().lower()
        if not p:
            return None
        for c in cols_pool:
            name = _norm(getattr(c, "column", None))
            if p == name or p in name or name in p:
                return c
        return None

    for key, default_agg in (("measures", want_agg or "sum"), ("attributes", None)):
        for item in graph.get(key) or []:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", "")
            hit = _match(str(text or ""))
            if hit is None:
                continue
            t, c = str(getattr(hit, "table", "")), str(getattr(hit, "column", ""))
            if _norm(t) not in table_set and table_set:
                continue
            key2 = (_norm(t), _norm(c))
            if key2 in existing:
                continue
            agg = default_agg if key == "measures" and want_agg else None
            outputs.append(
                {
                    "concept": str(text or c),
                    "table": t,
                    "column": c,
                    "alias": f"{c}_{agg}" if agg else c,
                    "aggregation": agg,
                }
            )
            existing.add(key2)
            notes.append(f"语义覆盖补列 {t}.{c}")

    if not outputs and cols_pool:
        c0 = cols_pool[0]
        outputs.append(
            {
                "concept": str(getattr(c0, "column", "col")),
                "table": str(getattr(c0, "table", tables[0] if tables else "")),
                "column": str(getattr(c0, "column", "")),
                "alias": str(getattr(c0, "column", "col")),
            }
        )
        notes.append("fallback 补默认输出列")

    # 5) JOIN：仅保留 target_tables 内关系；多表无 join 则重拉
    joins = [
        j
        for j in (data.get("join_logic") or [])
        if _norm(j.get("left_table")) in table_set and _norm(j.get("right_table")) in table_set
    ]
    if len(tables) >= 2 and not joins and meta_engine is not None:
        joins = [j.model_dump() for j in _relations_for_tables(meta_engine, tables)]
        if joins:
            notes.append("重补 L1 JOIN")

    # 6) filters 剪到目标表
    filters = [
        f
        for f in (data.get("filters") or [])
        if not f.get("table") or _norm(f.get("table")) in table_set
    ]

    group_by: list[str] = []
    if any(o.get("aggregation") for o in outputs):
        for o in outputs:
            if not o.get("aggregation") and o.get("table") and o.get("column"):
                group_by.append(f"{o['table']}.{o['column']}")

    from wenshu.services.agent.logical_plan import infer_output_grain

    grain = infer_output_grain(
        QueryPlan.model_validate(
            {
                **data,
                "target_tables": tables or data.get("target_tables") or ["_missing"],
                "join_logic": joins,
                "filters": filters,
                "output_fields": outputs,
                "group_by": group_by,
            }
        ),
        question=question,
        semantic_graph=graph,
    )
    new_plan = QueryPlan.model_validate(
        {
            **data,
            "target_tables": tables or data.get("target_tables") or ["_missing"],
            "join_logic": joins,
            "filters": filters,
            "output_fields": outputs,
            "group_by": group_by,
            "output_grain": grain.model_dump(),
            "source": data.get("source") or plan.source,
        }
    )
    # 置信度微调
    if notes:
        new_plan.confidence = min(1.0, float(new_plan.confidence) + 0.05)
    return new_plan, notes
