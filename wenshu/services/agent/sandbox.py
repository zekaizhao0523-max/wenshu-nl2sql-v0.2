"""只读沙箱（对齐 icecoding M10 + MySQLExecutor）。

- START TRANSACTION READ ONLY
- 执行前 EXPLAIN FORMAT=JSON 守门
- 未聚合查询强制 LIMIT
- MAX_EXECUTION_TIME 超时
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

from wenshu.services.agent.sql_ast import SqlDialect


@dataclass
class ExplainResult:
    estimated_rows: int


@dataclass
class SandboxResult:
    ok: bool
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    error: str | None = None
    truncated: bool = False
    sql_executed: str | None = None
    estimated_rows: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def estimate_max_rows(plan: dict) -> int:
    total = 0
    rows = plan.get("rows")
    if rows is not None:
        try:
            total = max(total, int(rows))
        except (TypeError, ValueError):
            pass
    for value in plan.values():
        if isinstance(value, dict):
            total = max(total, estimate_max_rows(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    total = max(total, estimate_max_rows(item))
    return total


class MySQLExecutor:
    """对齐 icecoding MySQLExecutor。"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        charset: str = "utf8mb4",
    ):
        self.conn_kwargs = dict(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=database,
            charset=charset,
            connect_timeout=15,
            autocommit=False,
        )

    @classmethod
    def from_raw_engine_url(cls) -> "MySQLExecutor":
        from db_config import get_raw_mysql_dsn

        dsn = get_raw_mysql_dsn()
        # mysql+pymysql://user:pass@host:port/db?...
        url = dsn.replace("mysql+pymysql://", "mysql://", 1)
        u = urlparse(url)
        return cls(
            host=u.hostname or "127.0.0.1",
            port=u.port or 3306,
            user=unquote(u.username or ""),
            password=unquote(u.password or ""),
            database=(u.path or "").lstrip("/"),
        )

    def _connect(self):
        import pymysql

        return pymysql.connect(
            **self.conn_kwargs, cursorclass=pymysql.cursors.DictCursor
        )

    def explain(self, sql: str) -> ExplainResult:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN FORMAT=JSON {sql}")
                row = cur.fetchone()
                if not row:
                    return ExplainResult(estimated_rows=0)
                plan_text = row.get("EXPLAIN") if isinstance(row, dict) else row[0]
                if plan_text is None and isinstance(row, dict):
                    plan_text = next(iter(row.values()))
                plan = json.loads(plan_text)
                return ExplainResult(estimated_rows=estimate_max_rows(plan))

    def execute(self, sql: str, timeout_seconds: int = 30) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("START TRANSACTION READ ONLY")
                cur.execute(
                    "SET SESSION MAX_EXECUTION_TIME = %s", (int(timeout_seconds * 1000),)
                )
                cur.execute(sql)
                rows = cur.fetchall()
                cur.execute("COMMIT")
                return [dict(r) for r in rows]


def execute_readonly(
    sql: str,
    *,
    timeout_ms: int | None = None,
    max_rows: int | None = None,
    explain_row_threshold: int | None = None,
    dialect: str = "mysql",
) -> SandboxResult:
    timeout_ms = timeout_ms or int(os.getenv("AGENT_SQL_TIMEOUT_MS", "30000"))
    max_rows = max_rows or int(os.getenv("AGENT_SQL_MAX_ROWS", "200"))
    threshold = explain_row_threshold or int(
        os.getenv("AGENT_EXPLAIN_ROW_THRESHOLD", "1000000")
    )
    sqlsvc = SqlDialect(dialect)
    try:
        expr = sqlsvc.parse(sql, dialect)
    except Exception as exc:
        return SandboxResult(ok=False, error=f"执行前解析失败: {exc}")

    # 未聚合强制 LIMIT（对齐 M10）
    exec_sql = sql
    if not sqlsvc.has_aggregate_or_limit(expr):
        exec_sql = sqlsvc.enforce_limit(expr, max_rows, dialect)

    try:
        executor = MySQLExecutor.from_raw_engine_url()
    except Exception as exc:
        return SandboxResult(ok=False, error=f"无法创建执行器: {exc}")

    try:
        est = executor.explain(exec_sql)
    except Exception as exc:
        return SandboxResult(ok=False, error=f"EXPLAIN 失败: {exc}")
    if est.estimated_rows > threshold:
        return SandboxResult(
            ok=False,
            error=f"EXPLAIN 预估扫描 {est.estimated_rows} 行,超过阈值 {threshold},拒绝执行",
            estimated_rows=est.estimated_rows,
            sql_executed=exec_sql,
        )

    try:
        rows = executor.execute(exec_sql, timeout_seconds=max(1, timeout_ms // 1000))
    except Exception as exc:
        return SandboxResult(
            ok=False,
            error=f"执行报错: {exc}",
            estimated_rows=est.estimated_rows,
            sql_executed=exec_sql,
        )

    truncated = len(rows) > max_rows
    clean = [{k: _jsonable(v) for k, v in dict(r).items()} for r in rows[:max_rows]]
    return SandboxResult(
        ok=True,
        rows=clean,
        row_count=len(clean),
        truncated=truncated,
        sql_executed=exec_sql,
        estimated_rows=est.estimated_rows,
    )


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        from datetime import date, datetime
        from decimal import Decimal

        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return float(v)
    except Exception:
        pass
    return str(v)
