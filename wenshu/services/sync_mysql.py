"""从 MySQL information_schema 同步原始业务表到 L1 元数据。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from wenshu.services.ids import make_column_id, make_table_id

# L1/L2 系统表，不参与业务表扫描
METADATA_TABLES = frozenset(
    {
        "table_meta",
        "column_meta",
        "table_relation",
        "metric_def",
        "synonym",
        "kb_document",
        "kb_chunk",
        "vector_index_log",
    }
)


@dataclass
class RawTable:
    db_name: str
    table_name: str
    comment: str | None


@dataclass
class RawColumn:
    db_name: str
    table_name: str
    column_name: str
    data_type: str
    comment: str | None
    ordinal_pos: int


def discover_mysql_tables(engine: Engine, include_tables: list[str] | None = None) -> tuple[list[RawTable], list[RawColumn]]:
    params: dict = {"meta_tables": tuple(METADATA_TABLES)}
    table_filter = ""
    if include_tables:
        table_filter = "AND t.table_name IN :include_tables"
        params["include_tables"] = tuple(include_tables)

    with engine.connect() as conn:
        db_name = conn.execute(text("SELECT DATABASE()")).scalar()
        table_rows = conn.execute(
            text(
                f"""
                SELECT t.table_name, t.table_comment
                FROM information_schema.tables t
                WHERE t.table_schema = DATABASE()
                  AND t.table_type = 'BASE TABLE'
                  AND t.table_name NOT IN :meta_tables
                  {table_filter}
                ORDER BY t.table_name
                """
            ),
            params,
        ).fetchall()

        col_rows = conn.execute(
            text(
                f"""
                SELECT c.table_name, c.column_name, c.column_type, c.column_comment, c.ordinal_position
                FROM information_schema.columns c
                JOIN information_schema.tables t
                  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                WHERE c.table_schema = DATABASE()
                  AND t.table_type = 'BASE TABLE'
                  AND c.table_name NOT IN :meta_tables
                  {table_filter}
                ORDER BY c.table_name, c.ordinal_position
                """
            ),
            params,
        ).fetchall()

    tables = [RawTable(db_name, r[0], r[1] or None) for r in table_rows]
    columns = [
        RawColumn(db_name, r[0], r[1], r[2], r[3] or None, int(r[4]))
        for r in col_rows
    ]
    return tables, columns


def list_raw_business_tables(raw_engine: Engine) -> list[dict]:
    """列出业务库可扫描的表（供「指定表扫描」下拉，无需先全库扫描）。"""
    tables, _ = discover_mysql_tables(raw_engine)
    return [
        {
            "table_id": make_table_id(t.db_name, t.table_name),
            "db_name": t.db_name,
            "table_name": t.table_name,
            "hive_comment": t.comment,
        }
        for t in tables
    ]


def upsert_tables(engine: Engine, tables: list[RawTable]) -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    now = datetime.now()

    with engine.begin() as conn:
        for t in tables:
            key = (t.db_name, t.table_name)
            existing = conn.execute(
                text("SELECT table_id FROM table_meta WHERE db_name = :db AND table_name = :tbl"),
                {"db": t.db_name, "tbl": t.table_name},
            ).fetchone()
            table_id = existing[0] if existing else make_table_id(t.db_name, t.table_name)
            mapping[key] = table_id

            conn.execute(
                text(
                    """
                    INSERT INTO table_meta
                        (table_id, db_name, table_name, hive_comment, description,
                         is_enabled, source, synced_at)
                    VALUES
                        (:table_id, :db_name, :table_name, :hive_comment, :hive_comment,
                         1, 'mysql', :synced_at)
                    ON DUPLICATE KEY UPDATE
                        hive_comment = VALUES(hive_comment),
                        description = IF(description IS NULL OR description = '', VALUES(hive_comment), description),
                        is_enabled = 1,
                        source = 'mysql',
                        synced_at = VALUES(synced_at)
                    """
                ),
                {
                    "table_id": table_id,
                    "db_name": t.db_name,
                    "table_name": t.table_name,
                    "hive_comment": t.comment,
                    "synced_at": now,
                },
            )
    return mapping


def upsert_columns(engine: Engine, columns: list[RawColumn], table_id_map: dict[tuple[str, str], str]) -> int:
    count = 0
    with engine.begin() as conn:
        for c in columns:
            table_id = table_id_map.get((c.db_name, c.table_name))
            if not table_id:
                continue
            column_id = make_column_id(table_id, c.column_name)
            existing_col = conn.execute(
                text("SELECT column_id FROM column_meta WHERE table_id = :tid AND column_name = :col"),
                {"tid": table_id, "col": c.column_name},
            ).fetchone()
            if existing_col:
                column_id = existing_col[0]
            conn.execute(
                text(
                    """
                    INSERT INTO column_meta
                        (column_id, table_id, column_name, data_type, hive_comment, description,
                         is_partition, ordinal_pos, is_enabled)
                    VALUES
                        (:column_id, :table_id, :column_name, :data_type, :hive_comment, :hive_comment,
                         0, :ordinal_pos, 1)
                    ON DUPLICATE KEY UPDATE
                        data_type = VALUES(data_type),
                        hive_comment = VALUES(hive_comment),
                        description = IF(description IS NULL OR description = '', VALUES(hive_comment), description),
                        ordinal_pos = VALUES(ordinal_pos),
                        is_enabled = 1
                    """
                ),
                {
                    "column_id": column_id,
                    "table_id": table_id,
                    "column_name": c.column_name,
                    "data_type": c.data_type,
                    "hive_comment": c.comment,
                    "ordinal_pos": c.ordinal_pos,
                },
            )
            count += 1
    return count


def disable_missing_tables(engine: Engine, db_name: str, seen: set[tuple[str, str]]) -> int:
    disabled = 0
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_id, db_name, table_name
                FROM table_meta
                WHERE db_name = :db AND source = 'mysql' AND is_enabled = 1
                """
            ),
            {"db": db_name},
        ).fetchall()
        for r in rows:
            if (r.db_name, r.table_name) not in seen:
                conn.execute(text("UPDATE table_meta SET is_enabled = 0 WHERE table_id = :tid"), {"tid": r.table_id})
                disabled += 1
    return disabled


def purge_missing_tables(engine: Engine, db_name: str, seen: set[tuple[str, str]]) -> dict:
    """硬删 L1 中业务库已不存在的表及其字段元数据。"""
    purged_table_ids: list[str] = []
    purged_column_ids: list[str] = []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_id, db_name, table_name
                FROM table_meta
                WHERE db_name = :db AND source = 'mysql'
                """
            ),
            {"db": db_name},
        ).fetchall()
        for r in rows:
            if (r.db_name, r.table_name) in seen:
                continue
            tid = r.table_id
            col_rows = conn.execute(
                text("SELECT column_id FROM column_meta WHERE table_id = :tid"),
                {"tid": tid},
            ).fetchall()
            purged_column_ids.extend(str(c[0]) for c in col_rows)
            conn.execute(text("DELETE FROM column_meta WHERE table_id = :tid"), {"tid": tid})
            conn.execute(text("DELETE FROM table_meta WHERE table_id = :tid"), {"tid": tid})
            purged_table_ids.append(tid)
    return {
        "tables_purged": len(purged_table_ids),
        "columns_purged": len(purged_column_ids),
        "purged_table_ids": purged_table_ids,
        "purged_column_ids": purged_column_ids,
    }


def sync_mysql_metadata(
    raw_engine: Engine,
    meta_engine: Engine,
    include_tables: list[str] | None = None,
) -> dict:
    tables, columns = discover_mysql_tables(raw_engine, include_tables)
    if not tables:
        with raw_engine.connect() as conn:
            db_name = conn.execute(text("SELECT DATABASE()")).scalar()
        return {
            "raw_db_name": db_name,
            "meta_db_name": _db_name(meta_engine),
            "tables_discovered": 0,
            "columns_discovered": 0,
            "tables_upserted": 0,
            "columns_upserted": 0,
            "tables_disabled": 0,
            "message": "未发现可同步的业务表，请先确认原始表已创建",
        }

    db_name = tables[0].db_name
    table_id_map = upsert_tables(meta_engine, tables)
    col_count = upsert_columns(meta_engine, columns, table_id_map)
    seen = {(t.db_name, t.table_name) for t in tables}
    disabled = disable_missing_tables(meta_engine, db_name, seen)

    return {
        "raw_db_name": db_name,
        "meta_db_name": _db_name(meta_engine),
        "tables_discovered": len(tables),
        "columns_discovered": len(columns),
        "tables_upserted": len(table_id_map),
        "columns_upserted": col_count,
        "tables_disabled": disabled,
    }


def _db_name(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT DATABASE()")).scalar()
