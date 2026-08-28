"""QueryPlan → SQL 编译（优先 sqlglot；不可用时回退字符串拼接）。"""

from __future__ import annotations

from wenshu.services.agent.plan_models import FilterSpec, OutputFieldSpec, QueryPlan


class UnsupportedPlanError(ValueError):
    pass


def _norm_table(name: str | None) -> str:
    return (name or "").strip().lower()


def _quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _col_ref(table: str | None, column: str) -> str:
    if table:
        return f"{_quote_ident(table)}.{_quote_ident(column)}"
    return _quote_ident(column)


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


def _proj(field: OutputFieldSpec) -> str:
    if field.expression:
        expr = field.expression
    elif field.column:
        expr = _col_ref(field.table, field.column)
        if field.aggregation == "count_distinct":
            expr = f"COUNT(DISTINCT {expr})"
        elif field.aggregation == "count":
            expr = f"COUNT({expr})"
        elif field.aggregation == "sum":
            expr = f"SUM({expr})"
        elif field.aggregation == "avg":
            expr = f"AVG({expr})"
        elif field.aggregation == "min":
            expr = f"MIN({expr})"
        elif field.aggregation == "max":
            expr = f"MAX({expr})"
    else:
        raise UnsupportedPlanError(f"输出字段 {field.concept!r} 缺少 column/expression")
    if field.alias:
        return f"{expr} AS {_quote_ident(field.alias)}"
    return expr


def _predicate(item: FilterSpec) -> str:
    col = _col_ref(item.table, item.column)
    if item.aggregation:
        if item.aggregation == "count_distinct":
            col = f"COUNT(DISTINCT {col})"
        else:
            col = f"{item.aggregation.upper()}({col})"
    op = item.operator
    if op in {"=", "!=", "<>", ">", ">=", "<", "<="}:
        return f"{col} {op} {_literal(item.value)}"
    if op in {"like", "not like"}:
        return f"{col} {op.upper()} {_literal(item.value)}"
    if op in {"in", "not in"}:
        values = item.value if isinstance(item.value, (list, tuple, set)) else [item.value]
        inner = ", ".join(_literal(v) for v in values)
        return f"{col} {op.upper()} ({inner})"
    if op == "between":
        if not isinstance(item.value, (list, tuple)) or len(item.value) != 2:
            raise UnsupportedPlanError("between 必须提供两个边界值")
        return f"{col} BETWEEN {_literal(item.value[0])} AND {_literal(item.value[1])}"
    if op in {"is", "is not"}:
        rhs = "NULL" if item.value is None or str(item.value).lower() == "null" else _literal(item.value)
        return f"{col} {op.upper()} {rhs}"
    raise UnsupportedPlanError(f"不支持的过滤运算符: {op}")


def _compile_with_sqlglot(plan: QueryPlan, dialect: str) -> str:
    import sqlglot
    from sqlglot import exp

    def column_ref(value: str, default_table: str | None = None) -> exp.Column:
        if "." in value:
            table, column = value.rsplit(".", 1)
            return exp.column(column, table=table)
        return exp.column(value, table=default_table)

    def projection(field: OutputFieldSpec) -> exp.Expression:
        if field.expression:
            expression = sqlglot.parse_one(field.expression, read=dialect)
        elif field.column:
            expression = column_ref(field.column, field.table)
            if field.aggregation == "count_distinct":
                expression = exp.Count(this=exp.Distinct(expressions=[expression]))
            elif field.aggregation:
                aggregates = {
                    "count": exp.Count,
                    "sum": exp.Sum,
                    "avg": exp.Avg,
                    "min": exp.Min,
                    "max": exp.Max,
                }
                expression = aggregates[field.aggregation](this=expression)
        else:
            raise UnsupportedPlanError(f"输出字段 {field.concept!r} 缺少 column/expression")
        if field.alias:
            return exp.alias_(expression, field.alias, quoted=True)
        return expression

    def predicate(item: FilterSpec) -> exp.Expression:
        column = column_ref(item.column, item.table)
        if item.aggregation == "count_distinct":
            column = exp.Count(this=exp.Distinct(expressions=[column]))
        elif item.aggregation:
            aggregates = {
                "count": exp.Count,
                "sum": exp.Sum,
                "avg": exp.Avg,
                "min": exp.Min,
                "max": exp.Max,
            }
            column = aggregates[item.aggregation](this=column)
        op = item.operator
        lit = exp.convert(item.value)
        if op == "=":
            return exp.EQ(this=column, expression=lit)
        if op in {"!=", "<>"}:
            return exp.NEQ(this=column, expression=lit)
        if op == ">":
            return exp.GT(this=column, expression=lit)
        if op == ">=":
            return exp.GTE(this=column, expression=lit)
        if op == "<":
            return exp.LT(this=column, expression=lit)
        if op == "<=":
            return exp.LTE(this=column, expression=lit)
        if op in {"like", "not like"}:
            pred = exp.Like(this=column, expression=lit)
            return exp.Not(this=pred) if op == "not like" else pred
        raise UnsupportedPlanError(f"sqlglot 路径暂不支持运算符: {op}")

    if not plan.output_fields:
        raise UnsupportedPlanError("计划未显式声明 output_fields")
    projections = [projection(field) for field in plan.output_fields]
    # 表名大小写与 L1 JOIN 可能不一致，连通性按 lower 判断
    base = plan.target_tables[0]
    query = exp.select(*projections).from_(exp.to_table(base))
    joined = {_norm_table(base)}
    pending = list(plan.join_logic)
    guard = 0
    while pending and guard < len(pending) + 5:
        guard += 1
        progress = False
        still = []
        for join in pending:
            lt, rt = _norm_table(join.left_table), _norm_table(join.right_table)
            if lt in joined and rt not in joined:
                table = join.right_table
            elif rt in joined and lt not in joined:
                table = join.left_table
            elif lt in joined and rt in joined:
                progress = True
                continue
            else:
                still.append(join)
                continue
            condition = exp.EQ(
                this=exp.column(join.left_column, table=join.left_table),
                expression=exp.column(join.right_column, table=join.right_table),
            )
            query = query.join(exp.to_table(table), on=condition, join_type=join.join_type)
            joined.add(_norm_table(table))
            progress = True
        pending = still
        if not progress:
            break
    if pending:
        raise UnsupportedPlanError("Join 顺序无法连接到当前关系树")
    if plan.filters:
        condition = predicate(plan.filters[0])
        for item in plan.filters[1:]:
            condition = exp.and_(condition, predicate(item))
        query = query.where(condition)
    if plan.group_by:
        query = query.group_by(*[column_ref(field) for field in plan.group_by])
    if plan.having:
        condition = predicate(plan.having[0])
        for item in plan.having[1:]:
            condition = exp.and_(condition, predicate(item))
        query = query.having(condition)
    if plan.order_by:
        orders = []
        for item in plan.order_by:
            if item.expression:
                expression = sqlglot.parse_one(item.expression, read=dialect)
            elif item.column:
                expression = column_ref(item.column, item.table)
            else:
                raise UnsupportedPlanError(f"排序 {item.concept!r} 缺少字段")
            orders.append(exp.Ordered(this=expression, desc=item.direction == "desc"))
        query = query.order_by(*orders)
    if plan.limit:
        query = query.limit(plan.limit)
    return query.sql(dialect=dialect)


def _compile_fallback(plan: QueryPlan) -> str:
    if not plan.output_fields:
        raise UnsupportedPlanError("计划未显式声明 output_fields")
    select_list = ", ".join(_proj(f) for f in plan.output_fields)
    from_table = _quote_ident(plan.target_tables[0])
    sql = f"SELECT {select_list} FROM {from_table}"
    joined = {_norm_table(plan.target_tables[0])}
    pending = list(plan.join_logic)
    guard = 0
    while pending and guard < len(pending) + 5:
        guard += 1
        progress = False
        still = []
        for join in pending:
            lt, rt = _norm_table(join.left_table), _norm_table(join.right_table)
            if lt in joined and rt not in joined:
                table = join.right_table
            elif rt in joined and lt not in joined:
                table = join.left_table
            elif lt in joined and rt in joined:
                progress = True
                continue
            else:
                still.append(join)
                continue
            jt = join.join_type.upper()
            on = (
                f"{_col_ref(join.left_table, join.left_column)} = "
                f"{_col_ref(join.right_table, join.right_column)}"
            )
            sql += f" {jt} JOIN {_quote_ident(table)} ON {on}"
            joined.add(_norm_table(table))
            progress = True
        pending = still
        if not progress:
            break
    if pending:
        raise UnsupportedPlanError("Join 顺序无法连接到当前关系树")
    if plan.filters:
        sql += " WHERE " + " AND ".join(_predicate(f) for f in plan.filters)
    if plan.group_by:
        parts = []
        for g in plan.group_by:
            if "." in g:
                t, c = g.rsplit(".", 1)
                parts.append(_col_ref(t, c))
            else:
                parts.append(_quote_ident(g))
        sql += " GROUP BY " + ", ".join(parts)
    if plan.having:
        sql += " HAVING " + " AND ".join(_predicate(f) for f in plan.having)
    if plan.order_by:
        orders = []
        for item in plan.order_by:
            if item.expression:
                expr = item.expression
            elif item.column:
                expr = _col_ref(item.table, item.column)
            else:
                continue
            orders.append(f"{expr} {'DESC' if item.direction == 'desc' else 'ASC'}")
        if orders:
            sql += " ORDER BY " + ", ".join(orders)
    if plan.limit:
        sql += f" LIMIT {int(plan.limit)}"
    return sql


def compile_query_plan(plan: QueryPlan, dialect: str = "mysql") -> tuple[str, list[str]]:
    try:
        import sqlglot  # noqa: F401

        sql = _compile_with_sqlglot(plan, dialect)
    except Exception:
        sql = _compile_fallback(plan)
    return sql, list(plan.target_tables)
