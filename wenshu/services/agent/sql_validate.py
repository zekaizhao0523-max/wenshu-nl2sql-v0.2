"""静态校验（对齐 icecoding M8）：AST、data_scope 泄漏、输出列完整、严字段幻觉。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlglot import exp

from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.agent.sql_ast import SqlDialect


@dataclass
class StaticValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    sql: str | None = None
    non_retryable: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _data_scope_values() -> set[str]:
    """系统命名空间：不能当表字段字面量出现在 WHERE。"""
    raw = os.getenv("AGENT_DATA_SCOPE", "").strip()
    values = {v.strip() for v in raw.split(",") if v.strip()}
    for key in (
        "MYSQL_DATABASE",
        "RAW_MYSQL_DATABASE",
        "META_MYSQL_DATABASE",
        "AGENT_DB_SCOPE",
    ):
        v = (os.getenv(key) or "").strip()
        if v:
            values.add(v)
    # 常见数仓层级名（与 icecoding data_scope 同类）
    values.update({"risk_mart", "dw", "core", "ods", "dwd", "dws", "ads"})
    return values


def _plan_shape_errors(expr, plan: QueryPlan | None) -> list[str]:
    if plan is None:
        return []
    selects = list(expr.find_all(exp.Select))
    root_select = expr if isinstance(expr, exp.Select) else (selects[0] if selects else None)
    if root_select is None and isinstance(expr, exp.With):
        root_select = next(expr.find_all(exp.Select), None)
    if root_select is None:
        return ["SQL 缺少可验证的 SELECT 结构"]

    has_where = any(select.args.get("where") is not None for select in selects)
    has_having = any(select.args.get("having") is not None for select in selects)
    has_group = any(select.args.get("group") is not None for select in selects)
    root_order = root_select.args.get("order")
    root_limit = root_select.args.get("limit")
    errors: list[str] = []

    if bool(plan.filters) != has_where:
        errors.append("SQL WHERE 与已验证 QueryPlan 的过滤要求不一致")
    if bool(plan.having) != has_having:
        errors.append("SQL HAVING 与已验证 QueryPlan 的聚合过滤要求不一致")
    if bool(plan.group_by) != has_group:
        errors.append("SQL GROUP BY 与已验证 QueryPlan 的分组要求不一致")

    actual_orders = list(root_order.expressions) if root_order is not None else []
    if len(actual_orders) != len(plan.order_by):
        errors.append("SQL ORDER BY 数量与已验证 QueryPlan 不一致")
    else:
        for expected, actual in zip(plan.order_by, actual_orders):
            actual_direction = "desc" if bool(actual.args.get("desc")) else "asc"
            if actual_direction != expected.direction:
                errors.append(
                    f"SQL 排序方向不一致：{expected.concept or expected.column} 应为 {expected.direction}"
                )

    actual_limit = None
    if root_limit is not None and root_limit.expression is not None:
        try:
            actual_limit = int(root_limit.expression.this)
        except (TypeError, ValueError, AttributeError):
            actual_limit = None
    if plan.limit is not None and actual_limit != plan.limit:
        errors.append(
            f"SQL LIMIT 与已验证 QueryPlan 不一致：期望={plan.limit!r}，实际={actual_limit!r}"
        )
    return errors


def _known_tables_columns(retrieval: Any) -> tuple[set[str], dict[str, set[str]]]:
    cols = list(getattr(retrieval, "s2_columns", None) or []) or list(
        getattr(retrieval, "columns", None) or []
    )
    tables = {_norm(t) for t in (getattr(retrieval, "expanded_tables", None) or [])}
    tables |= {_norm(t) for t in (getattr(retrieval, "selected_tables", None) or [])}
    table_cols: dict[str, set[str]] = {}
    for c in cols:
        t = _norm(getattr(c, "table", None))
        col = _norm(getattr(c, "column", None))
        if t:
            tables.add(t)
        if t and col:
            table_cols.setdefault(t, set()).add(col)
    return tables, table_cols


def validate_sql_ast(
    sql: str,
    *,
    plan: QueryPlan | None = None,
    used_tables: list[str] | None = None,
    retrieval: Any = None,
    dialect: str = "mysql",
    generation_source: str = "model",
    failed_sql_hashes: list[str] | None = None,
) -> StaticValidationResult:
    text = (sql or "").strip()
    if not text:
        return StaticValidationResult(ok=False, errors=["SQL 为空"])

    upper = text.upper()
    if "INTO OUTFILE" in upper or "INTO DUMPFILE" in upper:
        return StaticValidationResult(
            ok=False,
            errors=["命中危险操作: Outfile,禁止执行"],
            blocked_reason="Outfile",
            non_retryable=True,
        )

    sqlsvc = SqlDialect(dialect)
    try:
        expr = sqlsvc.parse(text, dialect)
    except Exception as exc:  # noqa: BLE001
        return StaticValidationResult(ok=False, errors=[f"SQL 语法错误({dialect}): {exc}"])

    danger = sqlsvc.is_dangerous(expr)
    if danger:
        return StaticValidationResult(
            ok=False,
            errors=[f"命中危险操作: {danger},禁止执行"],
            blocked_reason=danger,
            non_retryable=True,
        )

    shape_errors = _plan_shape_errors(expr, plan)
    if shape_errors:
        return StaticValidationResult(ok=False, errors=shape_errors)

    # data_scope 泄漏：系统命名空间不得当字段字面量
    scope_values = _data_scope_values()
    leaked = {
        str(literal.this)
        for literal in expr.find_all(exp.Literal)
        if literal.is_string and str(literal.this) in scope_values
    }
    if leaked:
        return StaticValidationResult(
            ok=False,
            errors=[
                "SQL 把系统命名空间误作表字段值: "
                f"{sorted(leaked)}；data_scope 只能用于表权限"
            ],
        )

    ref_tables = sqlsvc.extract_tables(expr)
    if not ref_tables:
        return StaticValidationResult(ok=False, errors=["SQL 未引用任何表"])

    known, table_cols = _known_tables_columns(retrieval) if retrieval is not None else (set(), {})
    used = {_norm(t) for t in (used_tables or [])}
    ref_norm = {_norm(t) for t in ref_tables}
    # 对齐 icecoding：AST 表 ⊆ used_tables 且 ⊆ known（有清单时）
    if used:
        inconsistent = (ref_norm - used) | (ref_norm - known if known else set())
    else:
        inconsistent = ref_norm - known if known else set()
    if known and inconsistent:
        return StaticValidationResult(
            ok=False,
            errors=[
                f"引用了未检索到/未声明的表: {sorted(inconsistent)}"
                f"(AST表={ref_tables}, used_tables={sorted(used)})"
            ],
        )

    select_aliases = {
        e.alias_or_name
        for sel in expr.find_all(exp.Select)
        for e in sel.expressions
        if isinstance(e, exp.Alias)
    }
    alias_map: dict[str, str] = {}
    for t in expr.find_all(exp.Table):
        if t.alias and t.alias != t.name:
            alias_map[_norm(t.alias)] = _norm(t.name)
    derived_aliases = {
        _norm(subquery.alias)
        for subquery in expr.find_all(exp.Subquery)
        if subquery.alias
    }

    errors: list[str] = []

    # 输出列完整性
    root_select = next(expr.find_all(exp.Select), None)
    projected_columns: set[tuple[str | None, str]] = set()
    projected_aliases: set[str] = set()
    if root_select is not None:
        for projection in root_select.expressions:
            if projection.alias_or_name:
                projected_aliases.add(str(projection.alias_or_name))
            for column_expr in projection.find_all(exp.Column):
                qualifier = column_expr.table or None
                real = alias_map.get(_norm(qualifier), _norm(qualifier)) if qualifier else None
                projected_columns.add((real, _norm(column_expr.name)))
    for output in plan.output_fields if plan else []:
        if not output.table or not output.column:
            continue
        t, c = _norm(output.table), _norm(output.column)
        qualified = (t, c) in projected_columns
        unqualified = (None, c) in projected_columns
        aliased = bool(output.alias and output.alias in projected_aliases)
        owners = [rt for rt in ref_norm if c in (table_cols.get(rt) or set())]
        if not aliased and not qualified and not (unqualified and owners == [t]):
            errors.append(f"SQL SELECT 遗漏计划返回字段 {output.table}.{output.column}")

    # 字段幻觉：有表限定时必须属于该表
    for tbl, col in sqlsvc.extract_columns(expr):
        c = _norm(col)
        if c in {_norm(a) for a in select_aliases} or c == "*":
            continue
        if tbl is not None:
            if _norm(tbl) in derived_aliases:
                continue
            real = alias_map.get(_norm(tbl), _norm(tbl))
            allowed = table_cols.get(real) or set()
            if allowed and c not in allowed:
                errors.append(f"表 {real} 不存在字段 {col}")
            elif known and real not in known and real not in used:
                errors.append(f"字段限定表不在检索范围: {real}.{col}")
        else:
            all_cols = set()
            for s in table_cols.values():
                all_cols |= s
            if all_cols and c not in all_cols:
                errors.append(f"字段 {col} 不在检索到的 schema 内(疑似字段幻觉)")

    if errors:
        return StaticValidationResult(ok=False, errors=errors[:12])

    sql_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    repeated = bool(failed_sql_hashes) and sql_hash in (failed_sql_hashes or [])
    non_retryable = generation_source == "deterministic" or repeated
    return StaticValidationResult(
        ok=True,
        sql=text,
        non_retryable=non_retryable,
        errors=[],
    )


def validate_sql(sql: str) -> tuple[list[str], str | None]:
    res = validate_sql_ast(sql)
    return res.errors, res.blocked_reason
