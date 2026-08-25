"""元数据 ID 生成（稳定、长度受限）。"""

from __future__ import annotations

import hashlib


def _hash_id(prefix: str, *parts: str) -> str:
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def make_table_id(db: str, table: str) -> str:
    return _hash_id("T", db, table)


def make_column_id(table_id: str, column: str) -> str:
    return _hash_id("C", table_id, column)


def make_relation_id(
    left_db: str,
    left_table: str,
    left_column: str,
    right_db: str,
    right_table: str,
    right_column: str,
) -> str:
    return _hash_id("R", left_db, left_table, left_column, right_db, right_table, right_column)


def make_metric_id(metric_name: str) -> str:
    return _hash_id("M", metric_name)


def make_synonym_id(term: str, target_type: str, target_id: str) -> str:
    return _hash_id("S", term, target_type, target_id)


def make_doc_id(title: str) -> str:
    return _hash_id("D", title)


def make_chunk_id(doc_id: str, chunk_index: int) -> str:
    return _hash_id("K", doc_id, str(chunk_index))
