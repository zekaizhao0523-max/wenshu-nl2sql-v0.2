"""按 manifest 加载 sql/templates 建表模板。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "sql" / "templates"
MANIFEST_FILE = TEMPLATES_DIR / "manifest.json"
LEGACY_SCHEMA_FILE = ROOT / "sql" / "metadata_schema.sql"

EXPECTED_TABLES = (
    "table_meta",
    "column_meta",
    "table_relation",
    "metric_def",
    "synonym",
    "kb_document",
    "kb_chunk",
    "vector_index_log",
    "staging_table_meta",
    "staging_column_meta",
)


def split_sql_statements(sql: str) -> list[str]:
    parts = re.split(r";\s*\n", sql)
    statements: list[str] = []
    for part in parts:
        stmt = part.strip()
        if not stmt:
            continue
        lines = []
        for line in stmt.splitlines():
            stripped = line.strip()
            if not lines and (not stripped or stripped.startswith("--")):
                continue
            lines.append(line)
        cleaned = "\n".join(lines).strip()
        if cleaned:
            statements.append(cleaned)
    return statements


def load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        raise FileNotFoundError(f"找不到建表 manifest: {MANIFEST_FILE}")
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def list_sections() -> list[dict]:
    manifest = load_manifest()
    return list(manifest.get("sections") or [])


def apply_schema_templates(
    engine: Engine,
    section_ids: list[str] | None = None,
) -> dict:
    """按 manifest 顺序执行模板 DDL。section_ids 为空则执行全部模块。"""
    manifest = load_manifest()
    sections = list(manifest.get("sections") or [])
    if section_ids:
        wanted = set(section_ids)
        sections = [s for s in sections if s.get("id") in wanted]
        missing = wanted - {s.get("id") for s in sections}
        if missing:
            raise ValueError(f"未知 section: {', '.join(sorted(missing))}")

    executed = 0
    applied: list[str] = []
    with engine.begin() as conn:
        for sec in sections:
            rel = sec.get("file")
            if not rel:
                continue
            path = TEMPLATES_DIR / rel
            if not path.exists():
                raise FileNotFoundError(f"找不到模板文件: {path}")
            sql = path.read_text(encoding="utf-8")
            for stmt in split_sql_statements(sql):
                conn.execute(text(stmt))
                executed += 1
            applied.append(sec.get("id") or rel)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name IN (
                    'table_meta','column_meta','table_relation','metric_def',
                    'synonym','kb_document','kb_chunk','vector_index_log',
                    'staging_table_meta','staging_column_meta'
                  )
                ORDER BY table_name
                """
            )
        ).fetchall()

    return {
        "statements_executed": executed,
        "sections_applied": applied,
        "metadata_tables": [r[0] for r in rows],
    }


def apply_legacy_schema(engine: Engine) -> dict:
    """回退：执行单体 metadata_schema.sql。"""
    if not LEGACY_SCHEMA_FILE.exists():
        raise FileNotFoundError(f"找不到建表脚本: {LEGACY_SCHEMA_FILE}")
    sql = LEGACY_SCHEMA_FILE.read_text(encoding="utf-8")
    statements = split_sql_statements(sql)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name IN (
                    'table_meta','column_meta','table_relation','metric_def',
                    'synonym','kb_document','kb_chunk','vector_index_log',
                    'staging_table_meta','staging_column_meta'
                  )
                ORDER BY table_name
                """
            )
        ).fetchall()
    return {
        "statements_executed": len(statements),
        "sections_applied": ["legacy_metadata_schema"],
        "metadata_tables": [r[0] for r in rows],
    }
