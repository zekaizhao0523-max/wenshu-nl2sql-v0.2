#!/usr/bin/env python3
"""
在 MySQL 中执行原始/示例表 DDL。

用法:
  python scripts/run_raw_ddl.py --file sql/raw/your_table.sql
  python scripts/run_raw_ddl.py --sql "CREATE TABLE ..."
  python scripts/run_raw_ddl.py --list
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from db_config import create_mysql_engine  # noqa: E402


def hive_to_mysql(sql: str) -> tuple[str, list[str]]:
    """
    将 Hive DDL 转为 MySQL 兼容 DDL。
    返回 (转换后 SQL, 变更说明列表)。
    """
    notes: list[str] = []
    out = sql

    # 去掉 Hive 特有语句
    for pattern, note in [
        (r"(?im)^\s*STORED\s+AS\s+\S+.*$", "移除 STORED AS"),
        (r"(?im)^\s*ROW\s+FORMAT\s+DELIMITED.*$", "移除 ROW FORMAT DELIMITED"),
        (r"(?im)^\s*FIELDS\s+TERMINATED\s+BY\s+.*$", "移除 FIELDS TERMINATED BY"),
        (r"(?im)^\s*LINES\s+TERMINATED\s+BY\s+.*$", "移除 LINES TERMINATED BY"),
        (r"(?im)^\s*LOCATION\s+['\"].*?['\"]\s*;?\s*$", "移除 LOCATION"),
        (r"(?im)^\s*TBLPROPERTIES\s*\(.*?\)\s*;?\s*$", "移除 TBLPROPERTIES"),
        (r"(?im)^\s*CLUSTERED\s+BY\s+.*$", "移除 CLUSTERED BY"),
        (r"(?im)^\s*SORTED\s+BY\s+.*$", "移除 SORTED BY"),
        (r"(?im)^\s*INTO\s+\d+\s+BUCKETS\s*;?\s*$", "移除 BUCKETS"),
    ]:
        if re.search(pattern, out):
            notes.append(note)
            out = re.sub(pattern, "", out)

    # EXTERNAL TABLE → TABLE
    if re.search(r"(?i)CREATE\s+EXTERNAL\s+TABLE", out):
        notes.append("EXTERNAL TABLE → TABLE")
        out = re.sub(r"(?i)CREATE\s+EXTERNAL\s+TABLE", "CREATE TABLE", out)

    # IF NOT EXISTS 保留
    if not re.search(r"(?i)IF\s+NOT\s+EXISTS", out):
        out = re.sub(r"(?i)CREATE\s+TABLE", "CREATE TABLE IF NOT EXISTS", out, count=1)
        notes.append("补充 IF NOT EXISTS")

    # 类型映射
    type_map = [
        (r"(?i)\bSTRING\b", "VARCHAR(512)"),
        (r"(?i)\bBIGINT\b", "BIGINT"),
        (r"(?i)\bINT\b", "INT"),
        (r"(?i)\bSMALLINT\b", "SMALLINT"),
        (r"(?i)\bTINYINT\b", "TINYINT"),
        (r"(?i)\bDOUBLE\b", "DOUBLE"),
        (r"(?i)\bFLOAT\b", "FLOAT"),
        (r"(?i)\bBOOLEAN\b", "TINYINT(1)"),
        (r"(?i)\bTIMESTAMP\b", "DATETIME"),
        (r"(?i)\bDATE\b", "DATE"),
        (r"(?i)\bDECIMAL\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", r"DECIMAL(\1,\2)"),
    ]
    for pat, repl in type_map:
        if re.search(pat, out):
            new_out = re.sub(pat, repl, out)
            if new_out != out and "STRING" in pat:
                notes.append("STRING → VARCHAR(512)")
            if "BOOLEAN" in pat and new_out != out:
                notes.append("BOOLEAN → TINYINT(1)")
            if "TIMESTAMP" in pat and new_out != out:
                notes.append("TIMESTAMP → DATETIME")
            out = new_out

    # 分区：Hive PARTITIONED BY → MySQL 普通列（去掉 PARTITIONED BY 子句，列并入表体）
    part_match = re.search(
        r"(?is)PARTITIONED\s+BY\s*\((.*?)\)\s*(;|$)",
        out,
    )
    if part_match:
        part_cols = part_match.group(1).strip()
        notes.append(f"分区列并入表字段: {part_cols}")
        out = re.sub(r"(?is)PARTITIONED\s+BY\s*\(.*?\)\s*", "", out)
        # 在最后一个 ) 前插入分区列（简化：追加到列定义末尾）
        out = re.sub(
            r"\)\s*(COMMENT\s*=.*?)?\s*;",
            lambda m: f", {part_cols}){m.group(1) or ''};",
            out,
            count=1,
        )

    # 反引号表名/库名
    out = re.sub(r"`(\w+)\.`(\w+)`", r"`\2`", out)  # ods.`table` → `table`

    # 清理多余空行
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if not out.endswith(";"):
        out += ";"

    return out, notes


def cmd_list() -> None:
    engine = create_mysql_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name, table_rows, table_comment
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name NOT LIKE '%_meta'
                  AND table_name NOT IN (
                    'table_meta','column_meta','table_relation',
                    'metric_def','synonym','kb_document','kb_chunk','vector_index_log'
                  )
                ORDER BY table_name
                """
            )
        ).fetchall()
    if not rows:
        print("[list] 当前无原始业务表")
        return
    print("[list] 原始表:")
    for name, rows_est, comment in rows:
        print(f"  - {name}  rows~={rows_est or 0}  {comment or ''}")


def cmd_run(sql: str, dry_run: bool = False) -> None:
    converted, notes = hive_to_mysql(sql)
    if notes:
        print("[convert] 转换说明:")
        for n in notes:
            print(f"  - {n}")
    print("[convert] 最终 SQL:")
    print(converted)
    if dry_run:
        print("[dry-run] 未执行")
        return
    engine = create_mysql_engine()
    with engine.begin() as conn:
        conn.execute(text(converted))
    print("[run] 执行成功 OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="执行原始表 DDL（Hive→MySQL）")
    parser.add_argument("--file", type=str, help="SQL 文件路径")
    parser.add_argument("--sql", type=str, help="直接传入 SQL 字符串")
    parser.add_argument("--list", action="store_true", help="列出原始表")
    parser.add_argument("--dry-run", action="store_true", help="仅转换预览，不执行")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.file:
        sql = Path(args.file).read_text(encoding="utf-8")
        cmd_run(sql, dry_run=args.dry_run)
    elif args.sql:
        cmd_run(args.sql, dry_run=args.dry_run)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
