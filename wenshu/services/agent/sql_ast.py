"""SQL 方言 / AST 工具（对齐 icecoding sql_dialect.py）。"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

_DIALECT_TIPS: dict[str, str] = {
    "mysql": (
        "MySQL 语法要点:\n"
        "- 标识符用反引号 ` 包裹(如 `table_name`.`column_name`)\n"
        "- LIMIT count\n"
        "- 字符串连接用 CONCAT(), 日期格式化用 DATE_FORMAT()\n"
        "- 当前日期 CURDATE(), 当前时间 NOW()\n"
    ),
}


def dialect_tips(dialect: str) -> str:
    return _DIALECT_TIPS.get(dialect.lower(), f"请使用 {dialect} 标准 SQL 语法。")


class SqlDialect:
    def __init__(self, dialect: str = "mysql"):
        self.dialect = dialect

    def parse(self, sql: str, dialect: str | None = None):
        return sqlglot.parse_one(sql, read=dialect or self.dialect)

    def to_sql(self, expr, dialect: str | None = None) -> str:
        return expr.sql(dialect=dialect or self.dialect)

    # With 允许 CTE；其余非 SELECT 族顶层一律危险
    _SAFE_TOP = (exp.Select, exp.Subquery, exp.Union, exp.Except, exp.Intersect, exp.With)

    def is_dangerous(self, expr) -> str | None:
        if not isinstance(expr, self._SAFE_TOP):
            return type(expr).__name__
        for cmd in expr.find_all(exp.Command, exp.DDL):
            if not isinstance(cmd, self._SAFE_TOP):
                return type(cmd).__name__
        # SELECT … INTO OUTFILE / DUMPFILE（写文件）
        text = (expr.sql() or "").upper()
        if "INTO OUTFILE" in text or "INTO DUMPFILE" in text:
            return "Outfile"
        return None

    def extract_tables(self, expr) -> list[str]:
        cte_names = {cte.alias_or_name for cte in expr.find_all(exp.CTE)}
        out: list[str] = []
        for t in expr.find_all(exp.Table):
            name = t.name
            if name and name not in cte_names and name not in out:
                out.append(name)
        return out

    def extract_columns(self, expr) -> list[tuple[str | None, str]]:
        out: list[tuple[str | None, str]] = []
        for c in expr.find_all(exp.Column):
            if c.name == "*":
                continue
            out.append((c.table or None, c.name))
        return out

    def is_select_column(self, expr, table: str | None, column: str) -> bool:
        for c in expr.find_all(exp.Column):
            if c.name == column and (table is None or c.table is None or c.table == table):
                return True
        return False

    def is_column_in_aggregate(self, expr, column: str) -> bool:
        for agg in expr.find_all(exp.AggFunc):
            for c in agg.find_all(exp.Column):
                if c.name == column:
                    return True
        return False

    def has_aggregate_or_limit(self, expr) -> bool:
        if expr.args.get("limit"):
            return True
        if expr.args.get("group"):
            return True
        if expr.args.get("distinct"):
            return True
        if list(expr.find_all(exp.AggFunc)):
            return True
        return False

    def enforce_limit(self, expr, limit: int, dialect: str | None = None) -> str:
        expr = expr.copy()
        expr = expr.limit(limit)
        return expr.sql(dialect=dialect or self.dialect)
