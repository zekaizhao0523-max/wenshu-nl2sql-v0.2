"""计划复杂度判定：简单走确定性，复杂走 LLM Structured QueryPlan。"""

from __future__ import annotations

import re
from typing import Any

_AGG_HINT = re.compile(r"(统计|合计|汇总|平均|均值|多少|数量|人数|家数|笔数|占比|分布|总计|求和)")
_MULTI_HINT = re.compile(r"(以及|及其|和|与|对应|关联|各自|分别|对比|按.{0,6}分组)")


def is_complex_query(
    *,
    question: str,
    evidence: str = "",
    semantic_graph: dict | None = None,
    retrieval: Any = None,
) -> bool:
    """复杂题：多表 / 聚合意图 / 低置信 → 应优先 LLM 写 QueryPlan。"""
    graph = semantic_graph or {}
    q = f"{question}\n{evidence}"
    tables = list(getattr(retrieval, "expanded_tables", None) or []) or list(
        getattr(retrieval, "selected_tables", None) or []
    )
    n_tables = len({(t or "").lower() for t in tables if t})
    conf = float(graph.get("confidence") or 0.7)
    query_action = str(graph.get("query_action") or "")
    query_type = str(graph.get("query_type") or "")

    if n_tables >= 2:
        return True
    if _AGG_HINT.search(q) or query_action in {"aggregate", "rank"}:
        return True
    if query_type in {"aggregation", "multi_fact", "fact_filter"}:
        return True
    if _MULTI_HINT.search(question or "") and n_tables >= 1:
        # 问句像多实体但召回只给了 1 表时仍可能复杂
        if len(graph.get("measures") or []) + len(graph.get("attributes") or []) >= 2:
            return True
    if conf < 0.55:
        return True
    return False
