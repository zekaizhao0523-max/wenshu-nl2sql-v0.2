"""平台状态统计。"""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.engine import Engine


def metadata_tables_exist(meta_engine: Engine) -> bool:
    with meta_engine.connect() as conn:
        count = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_name = 'table_meta'
                """
            )
        ).scalar()
    return int(count or 0) > 0


def _count_raw_tables(raw_engine: Engine) -> int:
    with raw_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_type = 'BASE TABLE'
                      AND table_name NOT IN (
                        'table_meta','column_meta','table_relation','metric_def',
                        'synonym','kb_document','kb_chunk','vector_index_log'
                      )
                    """
                )
            ).scalar()
            or 0
        )


def get_overview(raw_engine: Engine, meta_engine: Engine) -> dict:
    stats = {
        "raw_db_name": None,
        "meta_db_name": None,
        "raw_table_count": 0,
        "metadata_ready": False,
        "table_meta_count": 0,
        "column_meta_count": 0,
        "metric_count": 0,
        "vector_index_count": 0,
        "last_sync_at": None,
        "connection_errors": [],
    }

    try:
        with raw_engine.connect() as conn:
            stats["raw_db_name"] = conn.execute(text("SELECT DATABASE()")).scalar()
        stats["raw_table_count"] = _count_raw_tables(raw_engine)
    except Exception as exc:
        stats["connection_errors"].append({"role": "raw", "message": str(exc)})

    try:
        with meta_engine.connect() as conn:
            stats["meta_db_name"] = conn.execute(text("SELECT DATABASE()")).scalar()
        meta_ready = metadata_tables_exist(meta_engine)
        stats["metadata_ready"] = meta_ready
        if meta_ready:
            with meta_engine.connect() as conn:
                stats["table_meta_count"] = int(
                    conn.execute(text("SELECT COUNT(*) FROM table_meta WHERE is_enabled = 1")).scalar() or 0
                )
                stats["column_meta_count"] = int(
                    conn.execute(text("SELECT COUNT(*) FROM column_meta WHERE is_enabled = 1")).scalar() or 0
                )
                stats["metric_count"] = int(
                    conn.execute(text("SELECT COUNT(*) FROM metric_def WHERE is_enabled = 1")).scalar() or 0
                )
                stats["vector_index_count"] = int(
                    conn.execute(text("SELECT COUNT(*) FROM vector_index_log")).scalar() or 0
                )
                last_sync = conn.execute(text("SELECT MAX(synced_at) FROM table_meta")).scalar()
                if last_sync:
                    stats["last_sync_at"] = last_sync.isoformat(sep=" ", timespec="seconds")
    except Exception as exc:
        stats["connection_errors"].append({"role": "meta", "message": str(exc)})

    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    collection = os.getenv("QDRANT_COLLECTION", "wenshu_knowledge")
    stats["qdrant_url"] = qdrant_url
    stats["qdrant_collection"] = collection
    stats["qdrant_ready"] = False
    stats["qdrant_points"] = None

    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from db_config import create_qdrant_client

        client = create_qdrant_client()
        if client.collection_exists(collection):
            info = client.get_collection(collection)
            stats["qdrant_ready"] = True
            stats["qdrant_points"] = info.points_count
    except Exception:
        pass

    return stats


def list_tables(meta_engine: Engine, limit: int = 200, offset: int = 0) -> list[dict]:
    if not metadata_tables_exist(meta_engine):
        return []

    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.table_id, t.db_name, t.table_name, t.cn_name, t.description,
                       t.hive_comment, t.domain, t.is_enabled, t.synced_at,
                       (SELECT COUNT(*) FROM column_meta c WHERE c.table_id = t.table_id AND c.is_enabled = 1) AS col_count
                FROM table_meta t
                ORDER BY t.table_name
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        ).fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "table_id": r[0],
                "db_name": r[1],
                "table_name": r[2],
                "cn_name": r[3],
                "description": r[4],
                "hive_comment": r[5],
                "domain": r[6],
                "is_enabled": bool(r[7]),
                "synced_at": r[8].isoformat(sep=" ", timespec="seconds") if r[8] else None,
                "column_count": int(r[9] or 0),
            }
        )
    return result


def list_columns(meta_engine: Engine, table_id: str) -> list[dict]:
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_id, column_name, data_type, description, hive_comment,
                       is_partition, is_enabled, ordinal_pos
                FROM column_meta
                WHERE table_id = :tid
                ORDER BY ordinal_pos, column_name
                """
            ),
            {"tid": table_id},
        ).fetchall()

    return [
        {
            "column_id": r[0],
            "column_name": r[1],
            "data_type": r[2],
            "description": r[3],
            "hive_comment": r[4],
            "is_partition": bool(r[5]),
            "is_enabled": bool(r[6]),
            "ordinal_pos": r[7],
        }
        for r in rows
    ]
