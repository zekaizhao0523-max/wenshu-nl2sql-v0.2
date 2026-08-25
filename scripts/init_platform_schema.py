#!/usr/bin/env python3
"""
按 sql/templates/manifest.json 模块化建表。

用法:
  python scripts/init_platform_schema.py --list-sections
  python scripts/init_platform_schema.py --init
  python scripts/init_platform_schema.py --init --sections l1_core,staging
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from db_config import create_mysql_engine  # noqa: E402
from wenshu.services.schema_templates import (  # noqa: E402
    apply_schema_templates,
    list_sections,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="问数平台元数据库模块化建表")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-sections", action="store_true", help="列出可用模块")
    group.add_argument("--init", action="store_true", help="执行建表模板")
    parser.add_argument(
        "--sections",
        help="仅执行指定模块 id，逗号分隔（默认全部）",
    )
    args = parser.parse_args()

    if args.list_sections:
        for sec in list_sections():
            print(f"  {sec['id']}\t{sec.get('label', '')}\t({sec.get('file', '')})")
        return

    section_ids = None
    if args.sections:
        section_ids = [s.strip() for s in args.sections.split(",") if s.strip()]

    engine = create_mysql_engine()
    result = apply_schema_templates(engine, section_ids=section_ids)
    print(f"[init] 执行 {result['statements_executed']} 条 DDL")
    print(f"[init] 模块: {', '.join(result['sections_applied'])}")
    print(f"[init] 已有表: {', '.join(result['metadata_tables'])}")


if __name__ == "__main__":
    main()
