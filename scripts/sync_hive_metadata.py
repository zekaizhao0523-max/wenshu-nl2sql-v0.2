#!/usr/bin/env python3
"""
从 Hive 自动发现库表字段，同步到 L1 元数据库（MySQL）。

用法:
  python scripts/sync_hive_metadata.py
  python scripts/sync_hive_metadata.py --databases ods,dwd
  python scripts/sync_hive_metadata.py --mode metastore

依赖:
  pip install pymysql sqlalchemy pyhive   # mode=hive
  pip install pymysql sqlalchemy           # mode=metastore
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

META_DSN = os.getenv("META_MYSQL_DSN", "mysql+pymysql://root:password@127.0.0.1:3306/wenshu_meta")
HIVE_HOST = os.getenv("HIVE_HOST", "127.0.0.1")
HIVE_PORT = int(os.getenv("HIVE_PORT", "10000"))
HIVE_USER = os.getenv("HIVE_USER", "hive")
DEFAULT_DATABASES = os.getenv("HIVE_DATABASES", "ods,dwd,dws").split(",")

METASTORE_DSN = os.getenv("METASTORE_MYSQL_DSN", "")


@dataclass
class HiveTable:
    db_name: str
    table_name: str
    comment: str | None


@dataclass
class HiveColumn:
    db_name: str
    table_name: str
    column_name: str
    data_type: str
    comment: str | None
    is_partition: bool
    ordinal_pos: int


# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------

def make_table_id(db: str, table: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", f"{db}_{table}")
    return f"T_{safe}"


def make_column_id(table_id: str, column: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", column)
    return f"{table_id}__{safe}"


# ---------------------------------------------------------------------------
# 发现：PyHive DESCRIBE
# ---------------------------------------------------------------------------

def discover_via_hive(databases: list[str]) -> tuple[list[HiveTable], list[HiveColumn]]:
    from pyhive import hive

    conn = hive.connect(host=HIVE_HOST, port=HIVE_PORT, username=HIVE_USER)
    cursor = conn.cursor()

    tables: list[HiveTable] = []
    columns: list[HiveColumn] = []

    for db in databases:
        cursor.execute(f"USE `{db}`")
        cursor.execute("SHOW TABLES")
        table_names = [row[0] for row in cursor.fetchall()]

        for table_name in table_names:
            table_comment = None
            partition_cols: set[str] = set()
            in_partition = False
            col_idx = 0

            cursor.execute(f"DESCRIBE FORMATTED `{db}`.`{table_name}`")
            for row in cursor.fetchall():
                col_name = (row[0] or "").strip()
                data_type = (row[1] or "").strip() if len(row) > 1 else ""
                comment = (row[2] or "").strip() if len(row) > 2 else ""

                if col_name.startswith("#"):
                    if "Partition Information" in col_name:
                        in_partition = True
                    continue
                if col_name == "":
                    continue
                if col_name == "Table Comment:":
                    table_comment = comment or data_type
                    continue

                if in_partition and col_name and data_type:
                    partition_cols.add(col_name)

                if col_name and data_type and col_name not in ("col_name",):
                    columns.append(
                        HiveColumn(
                            db_name=db,
                            table_name=table_name,
                            column_name=col_name,
                            data_type=data_type,
                            comment=comment or None,
                            is_partition=col_name in partition_cols,
                            ordinal_pos=col_idx,
                        )
                    )
                    col_idx += 1

            tables.append(HiveTable(db, table_name, table_comment))

    return tables, columns


# ---------------------------------------------------------------------------
# 发现：Metastore MySQL（更快，适合表多）
# ---------------------------------------------------------------------------

METASTORE_TABLES_SQL = """
SELECT d.NAME AS db_name, t.TBL_NAME AS table_name, tp.PARAM_VALUE AS comment
FROM TBLS t
JOIN DBS d ON t.DB_ID = d.DB_ID
LEFT JOIN TABLE_PARAMS tp ON t.TBL_ID = tp.TBL_ID AND tp.PARAM_KEY = 'comment'
WHERE d.NAME IN :dbs
"""

METASTORE_COLUMNS_SQL = """
SELECT d.NAME AS db_name, t.TBL_NAME AS table_name,
       c.COLUMN_NAME AS column_name, c.TYPE_NAME AS data_type,
       c.COMMENT AS comment, c.INTEGER_IDX AS ordinal_pos
FROM COLUMNS_V2 c
JOIN SDS s ON c.CD_ID = s.CD_ID
JOIN TBLS t ON s.SD_ID = t.SD_ID
JOIN DBS d ON t.DB_ID = d.DB_ID
WHERE d.NAME IN :dbs
ORDER BY d.NAME, t.TBL_NAME, c.INTEGER_IDX
"""

METASTORE_PARTITIONS_SQL = """
SELECT d.NAME AS db_name, t.TBL_NAME AS table_name, pk.PKEY_NAME AS column_name
FROM PARTITION_KEYS pk
JOIN TBLS t ON pk.TBL_ID = t.TBL_ID
JOIN DBS d ON t.DB_ID = d.DB_ID
WHERE d.NAME IN :dbs
"""


def discover_via_metastore(databases: list[str]) -> tuple[list[HiveTable], list[HiveColumn]]:
    if not METASTORE_DSN:
        raise RuntimeError("请设置环境变量 METASTORE_MYSQL_DSN")

    engine = create_engine(METASTORE_DSN)
    dbs_param = {"dbs": tuple(databases)}

    with engine.connect() as conn:
        table_rows = conn.execute(text(METASTORE_TABLES_SQL), dbs_param).fetchall()
        col_rows = conn.execute(text(METASTORE_COLUMNS_SQL), dbs_param).fetchall()
        part_rows = conn.execute(text(METASTORE_PARTITIONS_SQL), dbs_param).fetchall()

    partition_set = {(r.db_name, r.table_name, r.column_name) for r in part_rows}

    tables = [HiveTable(r.db_name, r.table_name, r.comment) for r in table_rows]
    columns = [
        HiveColumn(
            db_name=r.db_name,
            table_name=r.table_name,
            column_name=r.column_name,
            data_type=r.data_type,
            comment=r.comment,
            is_partition=(r.db_name, r.table_name, r.column_name) in partition_set,
            ordinal_pos=int(r.ordinal_pos),
        )
        for r in col_rows
    ]
    return tables, columns


# ---------------------------------------------------------------------------
# 写入 L1
# ---------------------------------------------------------------------------

def upsert_tables(engine, tables: Iterable[HiveTable]) -> dict[tuple[str, str], str]:
    """返回 (db, table) -> table_id 映射"""
    mapping: dict[tuple[str, str], str] = {}
    now = datetime.now()

    with engine.begin() as conn:
        for t in tables:
            table_id = make_table_id(t.db_name, t.table_name)
            mapping[(t.db_name, t.table_name)] = table_id

            conn.execute(
                text("""
                INSERT INTO table_meta
                    (table_id, db_name, table_name, hive_comment, description,
                     is_enabled, source, synced_at)
                VALUES
                    (:table_id, :db_name, :table_name, :hive_comment, :hive_comment,
                     1, 'hive', :synced_at)
                ON DUPLICATE KEY UPDATE
                    hive_comment = VALUES(hive_comment),
                    description = IF(description IS NULL OR description = '', VALUES(hive_comment), description),
                    is_enabled = 1,
                    synced_at = VALUES(synced_at)
                """),
                {
                    "table_id": table_id,
                    "db_name": t.db_name,
                    "table_name": t.table_name,
                    "hive_comment": t.comment,
                    "synced_at": now,
                },
            )

    return mapping


def upsert_columns(engine, columns: Iterable[HiveColumn], table_id_map: dict[tuple[str, str], str]) -> None:
    now = datetime.now()
    seen_tables: set[tuple[str, str]] = set()

    with engine.begin() as conn:
        for c in columns:
            key = (c.db_name, c.table_name)
            seen_tables.add(key)
            table_id = table_id_map.get(key)
            if not table_id:
                continue

            column_id = make_column_id(table_id, c.column_name)
            conn.execute(
                text("""
                INSERT INTO column_meta
                    (column_id, table_id, column_name, data_type, hive_comment, description,
                     is_partition, ordinal_pos, is_enabled)
                VALUES
                    (:column_id, :table_id, :column_name, :data_type, :hive_comment, :hive_comment,
                     :is_partition, :ordinal_pos, 1)
                ON DUPLICATE KEY UPDATE
                    data_type = VALUES(data_type),
                    hive_comment = VALUES(hive_comment),
                    description = IF(description IS NULL OR description = '', VALUES(hive_comment), description),
                    is_partition = VALUES(is_partition),
                    ordinal_pos = VALUES(ordinal_pos),
                    is_enabled = 1
                """),
                {
                    "column_id": column_id,
                    "table_id": table_id,
                    "column_name": c.column_name,
                    "data_type": c.data_type,
                    "hive_comment": c.comment,
                    "is_partition": int(c.is_partition),
                    "ordinal_pos": c.ordinal_pos,
                },
            )

        # 更新分区字段 JSON
        for db_name, table_name in seen_tables:
            table_id = table_id_map[(db_name, table_name)]
            parts = [c.column_name for c in columns if c.db_name == db_name and c.table_name == table_name and c.is_partition]
            conn.execute(
                text("UPDATE table_meta SET partition_cols = :parts, synced_at = :now WHERE table_id = :table_id"),
                {"parts": json.dumps(parts, ensure_ascii=False), "now": now, "table_id": table_id},
            )


def disable_missing_tables(engine, databases: list[str], seen: set[tuple[str, str]]) -> None:
    """Hive 中已不存在的表软下线"""
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT table_id, db_name, table_name FROM table_meta WHERE db_name IN :dbs AND is_enabled = 1"),
            {"dbs": tuple(databases)},
        ).fetchall()
        for r in rows:
            if (r.db_name, r.table_name) not in seen:
                conn.execute(
                    text("UPDATE table_meta SET is_enabled = 0 WHERE table_id = :tid"),
                    {"tid": r.table_id},
                )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Hive metadata to L1 MySQL")
    parser.add_argument("--databases", default=",".join(DEFAULT_DATABASES), help="逗号分隔库名")
    parser.add_argument("--mode", choices=["hive", "metastore"], default="hive")
    args = parser.parse_args()

    databases = [d.strip() for d in args.databases.split(",") if d.strip()]
    print(f"[sync] mode={args.mode}, databases={databases}")

    if args.mode == "hive":
        tables, columns = discover_via_hive(databases)
    else:
        tables, columns = discover_via_metastore(databases)

    print(f"[sync] discovered tables={len(tables)}, columns={len(columns)}")

    engine = create_engine(META_DSN)
    table_id_map = upsert_tables(engine, tables)
    upsert_columns(engine, columns, table_id_map)

    seen = {(t.db_name, t.table_name) for t in tables}
    disable_missing_tables(engine, databases, seen)

    print("[sync] done. next: python scripts/build_vector_index.py")


if __name__ == "__main__":
    main()
