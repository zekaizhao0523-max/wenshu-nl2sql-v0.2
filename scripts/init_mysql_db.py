#!/usr/bin/env python3
"""
连接 MySQL 并执行模块化建表模板（sql/templates/）。

用法:
  # 1. 复制 .env.example 为 .env 并填写连接信息
  # 2. 安装依赖
  pip install sqlalchemy pymysql python-dotenv
  # 3. 测试连接
  python scripts/init_mysql_db.py --test
  # 4. 建表
  python scripts/init_mysql_db.py --init
  # 5. 查看已有表
  python scripts/init_mysql_db.py --list
  # 6. 列出模板模块
  python scripts/init_mysql_db.py --list-sections
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from db_config import create_mysql_engine, get_mysql_dsn  # noqa: E402
from wenshu.services.schema_templates import (  # noqa: E402
    apply_schema_templates,
    list_sections,
)


def cmd_test() -> None:
    engine = create_mysql_engine()
    dsn = get_mysql_dsn()
    safe_dsn = re.sub(r":([^:@/]+)@", ":***@", dsn)
    print(f"[test] 连接 DSN: {safe_dsn}")

    with engine.connect() as conn:
        version = conn.execute(text("SELECT VERSION()")).scalar()
        db_name = conn.execute(text("SELECT DATABASE()")).scalar()
        print(f"[test] MySQL 版本: {version}")
        print(f"[test] 当前数据库: {db_name}")
        print("[test] 连接成功 OK")


def cmd_list() -> None:
    engine = create_mysql_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name, table_comment
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                ORDER BY table_name
                """
            )
        ).fetchall()
    if not rows:
        print("[list] 当前库中暂无表")
        return
    print("[list] 已有表:")
    for name, comment in rows:
        print(f"  - {name}: {comment or ''}")


def cmd_list_sections() -> None:
    for sec in list_sections():
        print(f"  {sec['id']}\t{sec.get('label', '')}\t({sec.get('file', '')})")


def cmd_init(section_ids: list[str] | None = None) -> None:
    engine = create_mysql_engine()
    result = apply_schema_templates(engine, section_ids=section_ids)
    print(f"[init] 执行 {result['statements_executed']} 条 DDL")
    print(f"[init] 模块: {', '.join(result['sections_applied'])}")
    cmd_list()


def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL 连接测试与模块化建表")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true", help="测试连接")
    group.add_argument("--init", action="store_true", help="执行 sql/templates 建表")
    group.add_argument("--list", action="store_true", help="列出当前库中的表")
    group.add_argument("--list-sections", action="store_true", help="列出建表模板模块")
    parser.add_argument(
        "--sections",
        help="仅执行指定模块 id，逗号分隔",
    )
    args = parser.parse_args()
    if args.test:
        cmd_test()
    elif args.list:
        cmd_list()
    elif args.list_sections:
        cmd_list_sections()
    elif args.init:
        section_ids = None
        if args.sections:
            section_ids = [s.strip() for s in args.sections.split(",") if s.strip()]
        cmd_init(section_ids=section_ids)


if __name__ == "__main__":
    main()
