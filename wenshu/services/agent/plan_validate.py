"""计划校验（对齐 icecoding M6 + logical_plan 关键检查）。

在问数已有 semantic_graph / 召回结构上复刻：
- 表白名单与字段归属
- 多表连通 / JOIN 覆盖
- 聚合意图禁止纯明细、GROUP BY 一致性
- 语义图 measures/filters/attributes 覆盖
- LogicalPlan 结构与粒度风险
"""

from __future__ import annotations

import re
from typing import Any

from wenshu.services.agent.logical_plan import (
    build_logical_plan,
    infer_output_grain,
    validate_logical_plan,
)
from wenshu.services.agent.plan_models import QueryPlan

_AGG_HINT = re.compile(r"(统计|合计|汇总|平均|均值|多少|数量|人数|家数|笔数|占比|分布|总计|求和)")


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _slot_texts(graph: dict, key: str) -> list[str]:
    out: list[str] = []
    for item in graph.get(key) or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            text = str(getattr(item, "text", "") or "").strip()
        if text:
            out.append(text)
    return out


def _fuzzy_hit(phrase: str, candidates: list[str]) -> bool:
    p = _norm(phrase)
    if not p:
        return True
    for c in candidates:
        n = _norm(c)
        if not n:
            continue
        if p == n or p in n or n in p:
            return True
    return False


def _retrieval_table_cols(retrieval: Any) -> tuple[set[str], dict[str, set[str]]]:
    if retrieval is None:
        return set(), {}
    tables = {_norm(t) for t in (getattr(retrieval, "expanded_tables", None) or [])}
    tables |= {_norm(t) for t in (getattr(retrieval, "selected_tables", None) or [])}
    cols = list(getattr(retrieval, "s2_columns", None) or []) or list(
        getattr(retrieval, "columns", None) or []
    )
    table_cols: dict[str, set[str]] = {}
    for c in cols:
        t = _norm(getattr(c, "table", None))
        col = _norm(getattr(c, "column", None))
        if t:
            tables.add(t)
        if t and col:
            table_cols.setdefault(t, set()).add(col)
    return {t for t in tables if t}, table_cols


def _validate_field_ref(
    field: str,
    table_cols: dict[str, set[str]],
    target_tables: set[str],
) -> str | None:
    if not field:
        return None
    if "." in field:
        table, column = field.rsplit(".", 1)
        t, c = _norm(table), _norm(column)
        if t not in target_tables:
            return f"字段 {field} 引用了计划外表 {table}"
        allowed = table_cols.get(t) or set()
        if allowed and c not in allowed:
            return f"表 {table} 不存在字段 {column}"
        return None
    c = _norm(field)
    owners = [t for t in target_tables if c in (table_cols.get(t) or set())]
    if table_cols and not owners:
        # 召回列不完整时不硬杀：仅当任一表有列清单却都不含该列
        if any(table_cols.get(t) for t in target_tables):
            return f"引用了不存在的字段 {field}"
        return None
    if len(owners) > 1:
        return f"字段 {field} 同时存在于多张目标表 {sorted(owners)},必须限定表名"
    return None


def _join_connected(plan: QueryPlan) -> bool:
    tables = [_norm(t) for t in plan.target_tables if t]
    if len(tables) <= 1:
        return True
    adj: dict[str, set[str]] = {t: set() for t in tables}
    for j in plan.join_logic:
        a, b = _norm(j.left_table), _norm(j.right_table)
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    seen: set[str] = set()
    stack = [tables[0]]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, set()) - seen)
    return seen == set(tables)


def _shortest_path_coverable(
    tables: list[str],
    meta_engine,
) -> tuple[bool, list[str]]:
    """用 L1 关系判断多表是否存在连通路径（最短路径的存在性）。"""
    if meta_engine is None or len(tables) <= 1:
        return True, []
    try:
        from wenshu.services.l1_meta import list_relations

        wanted = {_norm(t) for t in tables}
        adj: dict[str, set[str]] = {t: set() for t in wanted}
        for r in list_relations(meta_engine):
            if r.get("is_enabled") is False:
                continue
            lt, rt = _norm(r.get("left_table")), _norm(r.get("right_table"))
            if lt in wanted and rt in wanted:
                adj[lt].add(rt)
                adj[rt].add(lt)
    except Exception:
        return True, []
    if not any(adj.values()) and len(tables) >= 2:
        return False, ["L1 关系图中目标表不连通，无法形成合法 JOIN"]
    roots = list(adj)
    seen: set[str] = set()
    stack = [roots[0]]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, set()) - seen)
    if seen != set(adj):
        return False, [f"L1 关系无法连通全部目标表: {sorted(set(adj) - seen)}"]
    return True, []


def _relation_cardinalities(meta_engine, tables: list[str]) -> list[dict]:
    if meta_engine is None or not tables:
        return []
    try:
        from wenshu.services.l1_meta import list_relations

        wanted = {_norm(t) for t in tables}
        out = []
        for r in list_relations(meta_engine):
            if r.get("is_enabled") is False:
                continue
            lt, rt = _norm(r.get("left_table")), _norm(r.get("right_table"))
            if lt in wanted and rt in wanted:
                out.append(r)
        return out
    except Exception:
        return []


def validate_query_plan(
    plan: QueryPlan,
    *,
    question: str = "",
    semantic_graph: dict | None = None,
    retrieval: Any = None,
    meta_engine=None,
    logical_plan=None,
) -> list[str]:
    """返回校验错误列表；空列表表示通过。"""
    errors: list[str] = []
    graph = semantic_graph or {}
    if not plan.target_tables:
        errors.append("target_tables 为空")
    if not plan.output_fields:
        errors.append("output_fields 为空")

    known, table_cols = _retrieval_table_cols(retrieval)
    target = {_norm(t) for t in plan.target_tables}
    if known:
        for t in plan.target_tables:
            if _norm(t) not in known:
                errors.append(f"target_tables 中的表 {t} 不在检索到的 schema 内")

    for f in plan.output_fields:
        if f.table and _norm(f.table) not in target:
            errors.append(f"输出字段表不在 target_tables: {f.table}.{f.column}")
        if not f.column and not f.expression:
            errors.append(f"输出字段缺少 column/expression: {f.concept}")
        if f.table and f.column and table_cols:
            err = _validate_field_ref(f"{f.table}.{f.column}", table_cols, target)
            if err:
                errors.append(err)

    for j in plan.join_logic:
        if _norm(j.left_table) not in target or _norm(j.right_table) not in target:
            errors.append(f"JOIN 表未在 target_tables: {j.left_table}-{j.right_table}")
        if known:
            for tbl in (j.left_table, j.right_table):
                if _norm(tbl) not in known:
                    errors.append(f"join 引用了未检索到的表 {tbl}")
        if table_cols:
            if j.left_column and _norm(j.left_column) not in (table_cols.get(_norm(j.left_table)) or set()):
                # 仅当该表有列清单时检查
                if table_cols.get(_norm(j.left_table)):
                    errors.append(f"join 左表 {j.left_table} 不存在字段 {j.left_column}")
            if j.right_column and _norm(j.right_column) not in (table_cols.get(_norm(j.right_table)) or set()):
                if table_cols.get(_norm(j.right_table)):
                    errors.append(f"join 右表 {j.right_table} 不存在字段 {j.right_column}")

    if len(plan.target_tables) >= 2 and not plan.join_logic:
        errors.append("多表查询缺少 join_logic")
    elif len(plan.target_tables) >= 2 and not _join_connected(plan):
        errors.append("join_logic 未能连通全部 target_tables（非最短/缺失边）")

    ok_path, path_errs = _shortest_path_coverable(list(plan.target_tables), meta_engine)
    if not ok_path:
        errors.extend(path_errs)

    # WHERE / HAVING 约束
    if any(item.aggregation for item in plan.filters):
        errors.append("普通 WHERE filters 不得包含聚合表达式，聚合条件必须放入 having")
    if any(not item.aggregation for item in plan.having):
        errors.append("HAVING 条件必须声明 aggregation")

    # 聚合意图
    action = str(graph.get("query_action") or "")
    qtype = str(graph.get("query_type") or "")
    want_agg = (
        action in {"aggregate", "rank"}
        or qtype in {"aggregation", "multi_fact"}
        or bool(_AGG_HINT.search(question or ""))
    )
    aggregate_outputs = [f for f in plan.output_fields if f.aggregation]
    detail_outputs = [
        f for f in plan.output_fields if not f.aggregation and f.table and f.column
    ]
    if want_agg and not aggregate_outputs:
        errors.append("用户要求统计/汇总，但 QueryPlan 没有任何聚合输出")
    if want_agg and aggregate_outputs and len(detail_outputs) > 2 and not plan.group_by:
        errors.append("聚合查询含过多明细列且缺少 GROUP BY，禁止纯明细堆砌")

    group_refs = set(plan.group_by)
    if aggregate_outputs and detail_outputs:
        missing_group = {
            f"{f.table}.{f.column}"
            for f in detail_outputs
            if f"{f.table}.{f.column}" not in group_refs and (f.column or "") not in group_refs
        }
        if missing_group:
            errors.append(
                "聚合输出与非聚合输出混用时，非聚合字段必须进入 GROUP BY: "
                f"{sorted(missing_group)}"
            )

    if graph.get("dimensions") or graph.get("group_by"):
        if aggregate_outputs and not plan.group_by:
            errors.append("统计问题已声明业务分组粒度，但 QueryPlan 缺少 GROUP BY")

    # 语义覆盖：measures / filters / attributes
    output_labels = []
    for f in plan.output_fields:
        output_labels.extend([f.concept or "", f.column or "", f.alias or ""])
    filter_labels = []
    for f in [*plan.filters, *plan.having]:
        filter_labels.extend([f.column or "", str(f.value) if f.value is not None else ""])

    for measure in _slot_texts(graph, "measures"):
        if not _fuzzy_hit(measure, output_labels):
            errors.append(f"语义度量未进入 output_fields: {measure}")
    for attr in _slot_texts(graph, "attributes"):
        # 属性覆盖较宽松：出现在输出或过滤即可；未命中仅在聚合题上强制
        if want_agg and not _fuzzy_hit(attr, output_labels + filter_labels):
            errors.append(f"语义属性未进入计划输出/过滤: {attr}")
    for filt in graph.get("filters") or []:
        if isinstance(filt, dict):
            text = str(filt.get("text") or "").strip()
            value = filt.get("value")
        else:
            text = str(getattr(filt, "text", "") or "").strip()
            value = getattr(filt, "value", None)
        labels = filter_labels + output_labels
        if value is not None:
            labels.append(str(value))
        if text and not _fuzzy_hit(text, labels) and (
            value is None or not _fuzzy_hit(str(value), labels)
        ):
            errors.append(f"语义过滤条件未进入计划: {text}")

    # 多事实表聚合
    aggregate_tables = {f.table for f in plan.output_fields if f.aggregation and f.table}
    if len(aggregate_tables) > 1:
        if len(plan.group_by) != 1 or "." not in (plan.group_by[0] or ""):
            errors.append("多事实指标必须声明一个明确的共同分组粒度，禁止直接 JOIN 后聚合")

    # LogicalPlan
    lp = logical_plan
    if lp is None:
        # 确保 grain
        if not plan.output_grain or (
            plan.output_grain.level == "record" and aggregate_outputs
        ):
            plan = plan.model_copy(
                update={
                    "output_grain": infer_output_grain(
                        plan, question=question, semantic_graph=graph
                    )
                }
            )
        lp = build_logical_plan(plan, question=question, semantic_graph=graph)
    cards = _relation_cardinalities(meta_engine, list(plan.target_tables))
    errors.extend(validate_logical_plan(lp, relation_cardinalities=cards))

    # 去重保序
    return list(dict.fromkeys(errors))
