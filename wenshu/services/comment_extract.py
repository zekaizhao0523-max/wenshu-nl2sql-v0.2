"""从 sql/raw DDL 文件提取 COMMENT。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL_RAW_DIR = ROOT / "sql" / "raw"

_COL_COMMENT = re.compile(
    r"`(\w+)`\s+\w+(?:\([^)]*\))?\s+(?:DEFAULT\s+\S+\s+)?COMMENT\s+'((?:[^'\\]|\\.)*)'",
    re.I,
)
_TABLE_COMMENT = re.compile(
    r"\)\s*ENGINE=\w+[^;]*COMMENT\s*=\s*'((?:[^'\\]|\\.)*)'\s*;",
    re.I,
)
_TABLE_NAME = re.compile(r"CREATE TABLE IF NOT EXISTS `(\w+)`", re.I)


def parse_ddl_text(sql: str) -> dict:
    """返回 {table_name, table_comment, columns: {col: comment}}。"""
    m = _TABLE_NAME.search(sql)
    if not m:
        return {}
    table_name = m.group(1)
    table_comment = ""
    tm = _TABLE_COMMENT.search(sql)
    if tm:
        table_comment = tm.group(1).replace("\\'", "'")
    columns: dict[str, str] = {}
    for col, comment in _COL_COMMENT.findall(sql):
        columns[col.lower()] = comment.replace("\\'", "'")
    return {
        "table_name": table_name,
        "table_comment": table_comment or None,
        "columns": columns,
    }


def parse_ddl_file(path: Path) -> dict:
    if not path.exists():
        return {}
    return parse_ddl_text(path.read_text(encoding="utf-8"))


def load_all_ddl_comments(sql_dir: Path | None = None) -> dict[str, dict]:
    """按表名（大写）索引 DDL 注释。"""
    base = sql_dir or SQL_RAW_DIR
    result: dict[str, dict] = {}
    if not base.exists():
        return result
    for path in base.glob("*.sql"):
        parsed = parse_ddl_file(path)
        if parsed.get("table_name"):
            result[parsed["table_name"].upper()] = parsed
    return result
