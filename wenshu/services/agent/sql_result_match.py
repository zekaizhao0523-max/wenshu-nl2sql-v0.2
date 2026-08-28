"""方案 A：gold_sql 双跑 + 结果集比对（Execution / Result Accuracy）。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from decimal import Decimal
from typing import Any

from wenshu.services.agent.plan_models import FilterSpec, JoinSpec, OutputFieldSpec, QueryPlan
from wenshu.services.agent.sql_compiler import UnsupportedPlanError, compile_query_plan


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, Decimal):
        return round(float(v), 6)
    if isinstance(v, float):
        return round(v, 6)
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if isinstance(v, (int, str)):
        return v
    return str(v)


def normalize_result_rows(rows: list[dict] | None) -> list[tuple]:
    """BIRD 风格：忽略列名顺序，按值元组做多重集比较。"""
    out: list[tuple] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        vals = [_cell(row[k]) for k in sorted(row.keys(), key=lambda x: str(x).lower())]
        out.append(tuple(vals))
    return out


def results_match(pred_rows: list[dict] | None, gold_rows: list[dict] | None) -> bool:
    return Counter(normalize_result_rows(pred_rows)) == Counter(normalize_result_rows(gold_rows))


def _all_l1_relations(meta_engine) -> list[dict]:
    if meta_engine is None:
        return []
    try:
        from wenshu.services.l1_meta import list_relations

        return [r for r in list_relations(meta_engine) if r.get("is_enabled") is not False]
    except Exception:
        return []


def _join_specs_between(meta_engine, tables: list[str]) -> list[JoinSpec]:
    wanted = {_norm(t) for t in tables}
    name_map = {_norm(t): t for t in tables}
    joins: list[JoinSpec] = []
    for r in _all_l1_relations(meta_engine):
        lt, rt = _norm(r.get("left_table")), _norm(r.get("right_table"))
        if lt in wanted and rt in wanted:
            joins.append(
                JoinSpec(
                    left_table=name_map.get(lt, str(r.get("left_table"))),
                    left_column=str(r.get("left_column")),
                    right_table=name_map.get(rt, str(r.get("right_table"))),
                    right_column=str(r.get("right_column")),
                    join_type="inner",
                )
            )
    return joins


def _fallback_key_joins(tables: list[str]) -> list[JoinSpec]:
    """L1 无直接边时，用业务主键兜底（仅 gold 生成）。"""
    norms = {_norm(t): t for t in tables}
    pairs = [
        (
            "dwd_ev_repay_plan",
            "dwd_ev_repay_detail",
            [("loan_no", "loan_no"), ("term_no", "term_no")],
        ),
        ("dwd_ev_overdue_repay", "dwd_ev_repay_plan", [("loan_no", "loan_no")]),
        ("dwd_ev_overdue_repay", "dwd_ev_repay_detail", [("loan_no", "loan_no")]),
        (
            "dwd_ev_indv_loan_app",
            "dwd_ar_loan_info",
            [("cust_id", "cust_id"), ("prd_code", "prd_code")],
        ),
        ("dwd_ev_tran_flow_info", "dwd_ip_indv_cust_info", [("cust_id", "cust_id")]),
        ("dwd_ev_tran_flow_info", "dwd_ev_indv_loan_app", [("cust_id", "cust_id")]),
        ("dwd_ev_tran_flow_info", "dwd_ev_indv_crd_app", [("cust_id", "cust_id")]),
    ]
    out: list[JoinSpec] = []
    for a, b, keys in pairs:
        if a in norms and b in norms:
            for lc, rc in keys:
                out.append(
                    JoinSpec(
                        left_table=norms[a],
                        left_column=lc,
                        right_table=norms[b],
                        right_column=rc,
                        join_type="inner",
                    )
                )
    return out


def _connect_tables_with_bridges(
    meta_engine, must_tables: list[str]
) -> tuple[list[str], list[JoinSpec]]:
    """用 L1 关系图为 must_tables 找连通生成树，必要时引入桥表。"""
    must = list(dict.fromkeys(must_tables))
    if len(must) <= 1:
        return must, []

    relations = _all_l1_relations(meta_engine)
    # undirected adjacency: table -> list[(neighbor, JoinSpec)]
    adj: dict[str, list[tuple[str, JoinSpec]]] = defaultdict(list)
    canon: dict[str, str] = {}
    for r in relations:
        lt, rt = str(r.get("left_table") or ""), str(r.get("right_table") or "")
        if not lt or not rt:
            continue
        ln, rn = _norm(lt), _norm(rt)
        canon.setdefault(ln, lt)
        canon.setdefault(rn, rt)
        js = JoinSpec(
            left_table=lt,
            left_column=str(r.get("left_column")),
            right_table=rt,
            right_column=str(r.get("right_column")),
            join_type="inner",
        )
        adj[ln].append((rn, js))
        adj[rn].append((ln, js))

    must_n = [_norm(t) for t in must]
    for t, tn in zip(must, must_n):
        canon.setdefault(tn, t)

    # 若诱导子图已连通
    direct = _join_specs_between(meta_engine, must)
    if _tables_connected(must, direct):
        return must, direct

    # Steiner 启发式：从第一张 must 出发，反复接最短路到未覆盖 must
    if must_n[0] not in adj and not any(n in adj for n in must_n):
        # 全无 L1 边 → key fallback
        fb = _fallback_key_joins(must)
        return must, fb

    covered = {must_n[0]}
    chosen_joins: list[JoinSpec] = []
    used_nodes = {must_n[0]}

    def shortest_path(src: str, targets: set[str]) -> list[tuple[str, JoinSpec]] | None:
        if src in targets:
            return []
        prev: dict[str, tuple[str, JoinSpec] | None] = {src: None}
        q = deque([src])
        found = None
        while q:
            cur = q.popleft()
            for nxt, js in adj.get(cur, []):
                if nxt in prev:
                    continue
                prev[nxt] = (cur, js)
                if nxt in targets:
                    found = nxt
                    q.clear()
                    break
                q.append(nxt)
        if found is None:
            return None
        path: list[tuple[str, JoinSpec]] = []
        node = found
        while node != src:
            parent, js = prev[node]  # type: ignore[misc]
            path.append((node, js))
            node = parent
        path.reverse()
        return path

    pending = set(must_n[1:])
    while pending:
        path = shortest_path(next(iter(covered)), pending)
        # also try from any covered
        if path is None:
            best = None
            for src in list(covered):
                cand = shortest_path(src, pending)
                if cand is not None and (best is None or len(cand) < len(best)):
                    best = cand
            path = best
        if path is None:
            # 尝试 fallback keys among remaining
            rem_tables = [canon[n] for n in ([next(iter(covered))] + list(pending))]
            fb = _fallback_key_joins(rem_tables)
            if fb:
                return [canon[n] for n in used_nodes] + [
                    canon[n] for n in pending if n not in used_nodes
                ], chosen_joins + fb
            return must, chosen_joins + _fallback_key_joins(must)
        for node, js in path:
            chosen_joins.append(js)
            used_nodes.add(node)
            covered.add(node)
            pending.discard(node)

    # 规范表名顺序：must 在前，桥表随后
    bridges = [canon[n] for n in used_nodes if n not in set(must_n)]
    ordered = must + bridges
    # 去重 joins
    uniq: list[JoinSpec] = []
    seen = set()
    for j in chosen_joins:
        key = (
            _norm(j.left_table),
            _norm(j.left_column),
            _norm(j.right_table),
            _norm(j.right_column),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(j)
    if not _tables_connected(ordered, uniq):
        uniq = uniq + _fallback_key_joins(ordered)
    return ordered, uniq


def _tables_connected(tables: list[str], joins: list[JoinSpec]) -> bool:
    nodes = {_norm(t) for t in tables}
    if len(nodes) <= 1:
        return True
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for j in joins:
        a, b = _norm(j.left_table), _norm(j.right_table)
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    start = next(iter(nodes))
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur] - seen:
            seen.add(nxt)
            stack.append(nxt)
    return seen == nodes


def _infer_filters(question: str, tables: list[str], cols: list[dict]) -> list[FilterSpec]:
    q = question or ""
    col_names = {( _norm(c["table"]), _norm(c["column"]) ): c for c in cols}
    table_set = {_norm(t): t for t in tables}
    filters: list[FilterSpec] = []

    # 逾期
    if re.search(r"存在逾期|逾期的借据|有逾期", q):
        for tnorm, tname in table_set.items():
            if "loan" in tnorm or tnorm.endswith("loan_info"):
                filters.append(
                    FilterSpec(table=tname, column="ovd_bal", operator=">", value=0)
                )
                break

    # N 岁以上 / 超过 N 岁
    m_age = re.search(r"(\d+)\s*岁\s*(以上|及以上)|超过\s*(\d+)\s*岁", q)
    if m_age:
        age_n = int(m_age.group(1) or m_age.group(3))
        for (t, c), raw in col_names.items():
            if c == "age":
                filters.append(
                    FilterSpec(
                        table=str(raw["table"]),
                        column=str(raw["column"]),
                        operator=">=",
                        value=age_n,
                    )
                )
                break

    # 职工人数超过 N
    m = re.search(r"职工人数超过\s*(\d+)|人数超过\s*(\d+)", q)
    if m:
        n = int(m.group(1) or m.group(2))
        for (t, c), raw in col_names.items():
            if c in {"employee_number", "employee_num", "staff_num"}:
                filters.append(
                    FilterSpec(
                        table=str(raw["table"]),
                        column=str(raw["column"]),
                        operator=">",
                        value=n,
                    )
                )
                break

    # 已婚
    if re.search(r"已婚", q):
        for (t, c), raw in col_names.items():
            if c == "marriage":
                filters.append(
                    FilterSpec(
                        table=str(raw["table"]),
                        column=str(raw["column"]),
                        operator="like",
                        value="%婚%",
                    )
                )
                break

    # 本科以上 — 简化：学历 in 本科/硕士/博士
    if re.search(r"本科以上|本科及以上", q):
        for (t, c), raw in col_names.items():
            if "school" in c or c in {"highest_schooling", "education"}:
                filters.append(
                    FilterSpec(
                        table=str(raw["table"]),
                        column=str(raw["column"]),
                        operator="in",
                        value=["本科", "硕士", "博士"],
                    )
                )
                break

    return filters


def _infer_outputs_and_group(
    question: str, cols: list[dict]
) -> tuple[list[OutputFieldSpec], list[str], bool]:
    """返回 outputs, group_by, is_global_agg。"""
    q = question or ""
    n = len(cols)
    want_avg = bool(re.search(r"平均|均值|avg", q, re.I))
    want_sum = bool(re.search(r"合计|总和|总额|汇总|总计|求和", q))
    want_count = bool(re.search(r"(人数|家数|笔数|有多少|多少人|多少笔|有多少)", q))
    # 「统计」单独不够，避免把「清单」类误伤；与分组/交叉一起时算聚合
    want_group = bool(re.search(r"分组|交叉|分布|分别", q))
    want_stat = bool(re.search(r"统计", q))

    outputs: list[OutputFieldSpec] = []
    group_by: list[str] = []

    # 维度列：性别/婚姻/产品名/节点/日期/宽限天数等
    dim_hint = re.compile(
        r"(sex|marriage|prd_name|prd_code|app_node|grace_day|int_repay_date|"
        r"practical_pay_date|highest_schooling|ent_scale)",
        re.I,
    )

    if want_group or (want_stat and want_group):
        # 分布/分组：维度列 GROUP BY + COUNT
        dims = []
        measures = []
        for c in cols:
            col = str(c["column"])
            if dim_hint.search(col) or re.search(r"名称|性别|婚姻|产品|节点|日期|天数", question):
                # 问句提到的概念优先当维度
                dims.append(c)
            else:
                measures.append(c)
        if not dims:
            # 无明确维度时：非金额类当维度
            for c in cols:
                col = str(c["column"]).lower()
                if any(x in col for x in ("amt", "bal", "amount", "sum", "income", "rate")):
                    measures.append(c)
                else:
                    dims.append(c)
        if not dims and cols:
            dims = [cols[0]]
            measures = cols[1:]

        for c in dims:
            table, column = str(c["table"]), str(c["column"])
            outputs.append(
                OutputFieldSpec(concept=column, table=table, column=column, alias=column)
            )
            group_by.append(f"{table}.{column}")

        # 计数
        if want_count or want_stat or want_group:
            # COUNT 用首个 id 列或 *
            id_col = next(
                (
                    c
                    for c in cols
                    if str(c["column"]).lower().endswith("_id")
                    or str(c["column"]).lower() in {"cust_id", "loan_no", "app_no"}
                ),
                dims[0],
            )
            outputs.append(
                OutputFieldSpec(
                    concept="count",
                    table=str(id_col["table"]),
                    column=str(id_col["column"]),
                    alias="cnt",
                    aggregation="count",
                )
            )
        for c in measures:
            table, column = str(c["table"]), str(c["column"])
            col_l = column.lower()
            agg = None
            if want_sum or any(x in col_l for x in ("amt", "bal", "amount", "sum")):
                agg = "sum"
            if agg:
                outputs.append(
                    OutputFieldSpec(
                        concept=column,
                        table=table,
                        column=column,
                        alias=f"{column}_{agg}",
                        aggregation=agg,  # type: ignore[arg-type]
                    )
                )
        return outputs, group_by, False

    # 非分组：按列推断聚合
    for i, c in enumerate(cols):
        table, column = str(c["table"]), str(c["column"])
        col_l = column.lower()
        agg = None
        if want_avg and i == 0:
            agg = "avg"
        elif want_sum and (
            i == 0 or any(x in col_l for x in ("amt", "bal", "amount", "sum", "income"))
        ):
            if i == 0 or want_sum:
                agg = "sum"
                # 只给金额列 sum；若首列不是金额且后面有金额，跳过首列 sum
                if i == 0 and n > 1 and not any(
                    x in col_l for x in ("amt", "bal", "amount", "sum", "income", "cred")
                ):
                    agg = None
        elif want_count and (
            n == 1 or col_l.endswith("_id") or col_l in {"cust_id", "loan_no", "app_no"}
        ):
            if i == 0 or n == 1:
                agg = "count"

        # 「求和/总计」且单列金额
        if not agg and want_sum and n == 1:
            agg = "sum"
        if not agg and want_avg and n == 1:
            agg = "avg"
        if not agg and want_count and n == 1 and not want_avg:
            agg = "count"

        alias = f"{column}_{agg}" if agg else column
        outputs.append(
            OutputFieldSpec(
                concept=column,
                table=table,
                column=column,
                alias=alias,
                aggregation=agg,  # type: ignore[arg-type]
            )
        )

    # 若求和意图但没标上任何 agg（列名不像金额），强制第一列 SUM
    if want_sum and not any(o.aggregation for o in outputs) and outputs:
        o0 = outputs[0]
        outputs[0] = o0.model_copy(
            update={"aggregation": "sum", "alias": f"{o0.column}_sum"}
        )

    has_agg = any(o.aggregation for o in outputs)
    if has_agg:
        for o in outputs:
            if not o.aggregation and o.table and o.column:
                group_by.append(f"{o.table}.{o.column}")

    is_global = has_agg and not group_by
    return outputs, group_by, is_global


def build_gold_sql(
    item: dict,
    meta_engine=None,
    *,
    limit: int = 100,
    force: bool = False,
) -> str | None:
    """由 must_tables / must_columns + L1 JOIN（可桥接）编译标准 SQL。"""
    if item.get("gold_sql") and not force:
        return str(item["gold_sql"]).strip() or None

    tables = [str(t) for t in (item.get("must_tables") or []) if t]
    cols = [
        c
        for c in (item.get("must_columns") or [])
        if isinstance(c, dict) and c.get("table") and c.get("column")
    ]
    if not tables or not cols:
        return None

    question = str(item.get("question") or "")
    outputs, group_by, _is_global = _infer_outputs_and_group(question, cols)
    filters = _infer_filters(question, tables, cols)

    all_tables, joins = _connect_tables_with_bridges(meta_engine, tables)
    if len(tables) >= 2 and not joins:
        return None
    if len(all_tables) >= 2 and not _tables_connected(all_tables, joins):
        return None

    # 与 Agent QueryPlan 默认 limit=100 对齐：聚合/明细统一带 LIMIT，
    # 避免评测时「gold 无 LIMIT、pred 有 LIMIT」造成不必要的口径差。
    plan = QueryPlan(
        target_tables=all_tables,
        join_logic=joins,
        filters=filters,
        output_fields=outputs,
        group_by=group_by,
        limit=limit,
        confidence=1.0,
        source="gold",
    )
    # gold 生成不做语义覆盖类严校验（无 retrieval/semantic_graph）
    errs = []
    if not plan.target_tables:
        errs.append("no tables")
    if not plan.output_fields:
        errs.append("no outputs")
    if len(plan.target_tables) >= 2 and not plan.join_logic:
        errs.append("no joins")
    if errs:
        return None
    try:
        sql, _ = compile_query_plan(plan, dialect="mysql")
    except (UnsupportedPlanError, ValueError):
        return None
    return sql


def evaluate_result_accuracy(
    *,
    pred_sql: str | None,
    gold_sql: str | None,
    execute_fn,
) -> dict:
    """执行 pred / gold，比对结果集。

    execute_fn(sql) -> object with .ok, .rows, .error, .row_count
    """
    out: dict[str, Any] = {
        "gold_sql": gold_sql,
        "gold_exec_ok": None,
        "pred_exec_ok": None,
        "result_acc": None,
        "gold_row_count": None,
        "pred_row_count": None,
        "gold_error": None,
        "pred_error": None,
    }
    if not gold_sql:
        out["gold_error"] = "missing gold_sql"
        return out
    if not (pred_sql or "").strip():
        out["pred_exec_ok"] = False
        out["pred_error"] = "empty pred sql"
        out["result_acc"] = False
        return out

    gold_ex = execute_fn(gold_sql)
    out["gold_exec_ok"] = bool(gold_ex.ok)
    if not gold_ex.ok:
        out["gold_error"] = gold_ex.error
        return out
    out["gold_row_count"] = gold_ex.row_count

    pred_ex = execute_fn(pred_sql)
    out["pred_exec_ok"] = bool(pred_ex.ok)
    if not pred_ex.ok:
        out["pred_error"] = pred_ex.error
        out["result_acc"] = False
        return out
    out["pred_row_count"] = pred_ex.row_count
    out["result_acc"] = results_match(pred_ex.rows, gold_ex.rows)
    return out
