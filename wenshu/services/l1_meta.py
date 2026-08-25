"""L1 扩展元数据 CRUD：JOIN / 指标 / 同义词 / 知识库。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from wenshu.services.ids import (
    make_chunk_id,
    make_doc_id,
    make_metric_id,
    make_relation_id,
    make_synonym_id,
)


def _json_load(val: Any) -> Any:
    if val is None or val == "":
        return None
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return val


def _json_dump(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


# ---------- JOIN ----------


def list_relations(meta_engine: Engine) -> list[dict]:
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT relation_id, left_db, left_table, left_column,
                       right_db, right_table, right_column, join_type, description, is_enabled
                FROM table_relation
                ORDER BY left_table, right_table
                """
            )
        ).fetchall()
    return [
        {
            "relation_id": r[0],
            "left_db": r[1],
            "left_table": r[2],
            "left_column": r[3],
            "right_db": r[4],
            "right_table": r[5],
            "right_column": r[6],
            "join_type": r[7],
            "description": r[8],
            "is_enabled": bool(r[9]),
        }
        for r in rows
    ]


def save_relation(meta_engine: Engine, data: dict) -> dict:
    rid = data.get("relation_id") or make_relation_id(
        data["left_db"],
        data["left_table"],
        data["left_column"],
        data["right_db"],
        data["right_table"],
        data["right_column"],
    )
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO table_relation
                    (relation_id, left_db, left_table, left_column,
                     right_db, right_table, right_column, join_type, description, is_enabled)
                VALUES
                    (:relation_id, :left_db, :left_table, :left_column,
                     :right_db, :right_table, :right_column, :join_type, :description, :is_enabled)
                ON DUPLICATE KEY UPDATE
                    join_type = VALUES(join_type),
                    description = VALUES(description),
                    is_enabled = VALUES(is_enabled)
                """
            ),
            {
                "relation_id": rid,
                "left_db": data["left_db"],
                "left_table": data["left_table"],
                "left_column": data["left_column"],
                "right_db": data["right_db"],
                "right_table": data["right_table"],
                "right_column": data["right_column"],
                "join_type": data.get("join_type") or "LEFT JOIN",
                "description": data.get("description") or "",
                "is_enabled": 1 if data.get("is_enabled", True) else 0,
            },
        )
    return {"relation_id": rid, "message": "已保存"}


def delete_relation(meta_engine: Engine, relation_id: str) -> None:
    with meta_engine.begin() as conn:
        conn.execute(text("DELETE FROM table_relation WHERE relation_id = :id"), {"id": relation_id})


# ---------- 指标 ----------


def list_metrics(meta_engine: Engine) -> list[dict]:
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT metric_id, metric_name, cn_name, aliases, definition,
                       sql_template, related_tables, domain, is_enabled
                FROM metric_def
                ORDER BY metric_name
                """
            )
        ).fetchall()
    return [
        {
            "metric_id": r[0],
            "metric_name": r[1],
            "cn_name": r[2],
            "aliases": _json_load(r[3]),
            "definition": r[4],
            "sql_template": r[5],
            "related_tables": _json_load(r[6]),
            "domain": r[7],
            "is_enabled": bool(r[8]),
        }
        for r in rows
    ]


def save_metric(meta_engine: Engine, data: dict) -> dict:
    mid = data.get("metric_id") or make_metric_id(data["metric_name"])
    aliases = data.get("aliases")
    if isinstance(aliases, str):
        aliases = [a.strip() for a in aliases.split(",") if a.strip()]
    related = data.get("related_tables")
    if isinstance(related, str):
        related = [t.strip() for t in related.split(",") if t.strip()]
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO metric_def
                    (metric_id, metric_name, cn_name, aliases, definition,
                     sql_template, related_tables, domain, is_enabled)
                VALUES
                    (:metric_id, :metric_name, :cn_name, :aliases, :definition,
                     :sql_template, :related_tables, :domain, :is_enabled)
                ON DUPLICATE KEY UPDATE
                    cn_name = VALUES(cn_name),
                    aliases = VALUES(aliases),
                    definition = VALUES(definition),
                    sql_template = VALUES(sql_template),
                    related_tables = VALUES(related_tables),
                    domain = VALUES(domain),
                    is_enabled = VALUES(is_enabled)
                """
            ),
            {
                "metric_id": mid,
                "metric_name": data["metric_name"],
                "cn_name": data.get("cn_name") or "",
                "aliases": _json_dump(aliases or []),
                "definition": data["definition"],
                "sql_template": data.get("sql_template") or "",
                "related_tables": _json_dump(related or []),
                "domain": data.get("domain") or "",
                "is_enabled": 1 if data.get("is_enabled", True) else 0,
            },
        )
    return {"metric_id": mid, "message": "已保存"}


def delete_metric(meta_engine: Engine, metric_id: str) -> None:
    with meta_engine.begin() as conn:
        conn.execute(text("DELETE FROM metric_def WHERE metric_id = :id"), {"id": metric_id})


# ---------- 同义词 ----------


# 召回词典缓存世代：保存/删除/导入后递增，retrieve_schema 据此重载
_LEXICON_GENERATION = 0


def lexicon_generation() -> int:
    return _LEXICON_GENERATION


def bump_lexicon_generation() -> None:
    global _LEXICON_GENERATION
    _LEXICON_GENERATION += 1


def ensure_table_meta_expand_flag(meta_engine: Engine) -> bool:
    """老库补 require_hint_for_expand；列已存在则忽略。"""
    try:
        with meta_engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE table_meta ADD COLUMN require_hint_for_expand "
                    "TINYINT(1) NOT NULL DEFAULT 0 "
                    "COMMENT '邻表扩展需问句点名，未点名不自动带入'"
                )
            )
        return True
    except Exception:
        return False


def _parse_synonym_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _sync_column_synonyms_json(conn, column_id: str, term: str, *, add: bool) -> None:
    row = conn.execute(
        text("SELECT synonyms FROM column_meta WHERE column_id = :id"),
        {"id": column_id},
    ).fetchone()
    if not row:
        return
    items = _parse_synonym_list(row[0])
    term = (term or "").strip()
    if not term:
        return
    if add:
        if term not in items:
            items.append(term)
    else:
        items = [x for x in items if x != term]
    conn.execute(
        text("UPDATE column_meta SET synonyms = :s WHERE column_id = :id"),
        {"s": json.dumps(items, ensure_ascii=False), "id": column_id},
    )


def list_synonyms(meta_engine: Engine, q: str = "") -> list[dict]:
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.synonym_id, s.term, s.target_type, s.target_id, s.is_enabled,
                       t.table_name AS table_name,
                       t2.table_name AS col_table_name,
                       c.column_name AS column_name
                FROM synonym s
                LEFT JOIN table_meta t
                  ON s.target_type = 'table' AND s.target_id = t.table_id
                LEFT JOIN column_meta c
                  ON s.target_type = 'column' AND s.target_id = c.column_id
                LEFT JOIN table_meta t2
                  ON c.table_id = t2.table_id
                ORDER BY s.term, s.target_type
                """
            )
        ).fetchall()
    items = []
    for r in rows:
        table_name = r.table_name or r.col_table_name or ""
        column_name = r.column_name or ""
        if r.target_type == "column" and table_name and column_name:
            label = f"{table_name}.{column_name}"
        elif r.target_type == "table" and table_name:
            label = table_name
        elif r.target_type == "concept":
            label = f"概念:{r.target_id}"
        else:
            label = r.target_id
        items.append(
            {
                "synonym_id": r.synonym_id,
                "term": r.term,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "is_enabled": bool(r.is_enabled),
                "table_name": table_name,
                "column_name": column_name,
                "target_label": label,
            }
        )
    if q.strip():
        kw = q.strip().lower()
        items = [
            i
            for i in items
            if kw in i["term"].lower()
            or kw in (i["target_id"] or "").lower()
            or kw in (i["target_label"] or "").lower()
        ]
    return items


def save_synonym(meta_engine: Engine, data: dict) -> dict:
    sid = data.get("synonym_id") or make_synonym_id(
        data["term"], data["target_type"], data["target_id"]
    )
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO synonym (synonym_id, term, target_type, target_id, is_enabled)
                VALUES (:synonym_id, :term, :target_type, :target_id, :is_enabled)
                ON DUPLICATE KEY UPDATE
                    term = VALUES(term),
                    target_type = VALUES(target_type),
                    target_id = VALUES(target_id),
                    is_enabled = VALUES(is_enabled)
                """
            ),
            {
                "synonym_id": sid,
                "term": data["term"],
                "target_type": data["target_type"],
                "target_id": data["target_id"],
                "is_enabled": 1 if data.get("is_enabled", True) else 0,
            },
        )
        if data.get("target_type") == "column" and data.get("is_enabled", True):
            _sync_column_synonyms_json(conn, data["target_id"], data["term"], add=True)
    bump_lexicon_generation()
    return {"synonym_id": sid, "message": "已保存"}


def _load_business_concepts_from_db(conn) -> dict[str, tuple[str, ...]]:
    concepts: dict[str, list[str]] = {}
    for row in conn.execute(
        text(
            """
            SELECT s.term, s.target_id
            FROM synonym s
            WHERE s.is_enabled = 1 AND s.target_type = 'concept'
            ORDER BY s.target_id, CHAR_LENGTH(s.term) DESC
            """
        )
    ):
        term = str(row.term or "").strip()
        concept = str(row.target_id or "").strip()
        if not term or not concept:
            continue
        bucket = concepts.setdefault(concept, [])
        if term not in bucket:
            bucket.append(term)
        if concept not in bucket:
            bucket.insert(0, concept)
    return {k: tuple(v) for k, v in concepts.items()}


def delete_synonym(meta_engine: Engine, synonym_id: str) -> None:
    with meta_engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT term, target_type, target_id FROM synonym WHERE synonym_id = :id"
            ),
            {"id": synonym_id},
        ).fetchone()
        conn.execute(text("DELETE FROM synonym WHERE synonym_id = :id"), {"id": synonym_id})
        if row and row.target_type == "column":
            _sync_column_synonyms_json(conn, row.target_id, row.term, add=False)
    bump_lexicon_generation()


def load_retrieval_lexicon(meta_engine: Engine | None) -> dict[str, Any]:
    """从 L1 synonym(concept) / table_meta 加载召回词典。不再读取 table/column 型同义词。"""
    from wenshu.services.business_concept import alias_to_concept_map

    empty: dict[str, Any] = {
        "concepts": {},
        "alias_to_concept": {},
        "entity_concepts": frozenset(),
        "fact_need_hint": frozenset(),
    }
    if meta_engine is None:
        return empty
    fact_need_hint: set[str] = set()
    try:
        with meta_engine.connect() as conn:
            concepts = _load_business_concepts_from_db(conn)
            try:
                for row in conn.execute(
                    text(
                        """
                        SELECT table_name FROM table_meta
                        WHERE is_enabled = 1 AND require_hint_for_expand = 1
                        """
                    )
                ):
                    name = str(row.table_name or "").strip()
                    if name:
                        fact_need_hint.add(name.lower())
            except Exception:
                fact_need_hint.clear()
    except Exception:
        return empty
    return {
        "concepts": concepts,
        "alias_to_concept": alias_to_concept_map(concepts),
        "entity_concepts": frozenset(),
        "fact_need_hint": frozenset(fact_need_hint),
    }


def _seed_fact_require_hint(meta_engine: Engine, tables: tuple[str, ...] | list[str]) -> int:
    ensure_table_meta_expand_flag(meta_engine)
    flagged = 0
    try:
        with meta_engine.begin() as conn:
            for name in tables:
                res = conn.execute(
                    text(
                        """
                        UPDATE table_meta
                        SET require_hint_for_expand = 1
                        WHERE LOWER(table_name) = :n
                        """
                    ),
                    {"n": str(name).lower()},
                )
                flagged += int(res.rowcount or 0)
    except Exception:
        return 0
    return flagged


def seed_retrieval_synonyms(
    meta_engine: Engine,
    *,
    business_concepts: dict[str, tuple[str, ...]] | None = None,
    entity_concepts: frozenset[str] | tuple[str, ...] | None = None,
    fact_tables_require_hint: tuple[str, ...] | list[str] | None = None,
    disable_legacy_table_column: bool = True,
) -> dict:
    """把业务概念种子写入 synonym(target_type=concept)；可选停用旧 table/column 同义词。"""
    from wenshu.services.retrieval_lexicon_seed import (
        SEED_BUSINESS_CONCEPTS,
        SEED_ENTITY_CONCEPT_KEYS,
        SEED_FACT_TABLES_REQUIRE_HINT,
    )

    concepts = business_concepts or SEED_BUSINESS_CONCEPTS
    entity_keys = (
        frozenset(entity_concepts)
        if entity_concepts is not None
        else SEED_ENTITY_CONCEPT_KEYS
    )
    fact_tables = (
        SEED_FACT_TABLES_REQUIRE_HINT
        if fact_tables_require_hint is None
        else fact_tables_require_hint
    )
    inserted = 0
    reused = 0
    disabled_legacy = 0
    with meta_engine.begin() as conn:
        if disable_legacy_table_column:
            res = conn.execute(
                text(
                    """
                    UPDATE synonym SET is_enabled = 0
                    WHERE target_type IN ('table', 'column')
                    """
                )
            )
            disabled_legacy = int(res.rowcount or 0)

        # 全量刷新 concept：避免旧别名（如 借据号→借据）残留
        conn.execute(
            text("UPDATE synonym SET is_enabled = 0 WHERE target_type = 'concept'")
        )

        def _upsert_concept(term: str, concept_key: str) -> None:
            nonlocal inserted, reused
            sid = make_synonym_id(term, "concept", concept_key)
            existed = conn.execute(
                text("SELECT 1 FROM synonym WHERE synonym_id = :id"),
                {"id": sid},
            ).fetchone()
            conn.execute(
                text(
                    """
                    INSERT INTO synonym (synonym_id, term, target_type, target_id, is_enabled)
                    VALUES (:synonym_id, :term, 'concept', :target_id, 1)
                    ON DUPLICATE KEY UPDATE
                        term = VALUES(term),
                        target_type = 'concept',
                        target_id = VALUES(target_id),
                        is_enabled = 1
                    """
                ),
                {
                    "synonym_id": sid,
                    "term": term,
                    "target_id": concept_key,
                },
            )
            if existed:
                reused += 1
            else:
                inserted += 1

        for concept_key, aliases in concepts.items():
            key = str(concept_key or "").strip()
            if not key:
                continue
            seen_terms: set[str] = set()
            for alias in (key, *(aliases or ())):
                term = str(alias or "").strip()
                if not term or term in seen_terms:
                    continue
                seen_terms.add(term)
                _upsert_concept(term, key)

    bump_lexicon_generation()
    fact_flags = _seed_fact_require_hint(meta_engine, fact_tables)
    return {
        "inserted": inserted,
        "reused": reused,
        "disabled_legacy": disabled_legacy,
        "concept_count": len(concepts),
        "entity_concept_count": len(entity_keys),
        "fact_flags": fact_flags,
    }



# ---------- 知识库 ----------


def list_documents(meta_engine: Engine) -> list[dict]:
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT d.doc_id, d.title, d.doc_type, d.source_path, d.domain, d.is_enabled,
                       (SELECT COUNT(*) FROM kb_chunk c WHERE c.doc_id = d.doc_id) AS chunk_count
                FROM kb_document d
                ORDER BY d.updated_at DESC
                """
            )
        ).fetchall()
    return [
        {
            "doc_id": r[0],
            "title": r[1],
            "doc_type": r[2],
            "source_path": r[3],
            "domain": r[4],
            "is_enabled": bool(r[5]),
            "chunk_count": int(r[6] or 0),
        }
        for r in rows
    ]


def save_document(meta_engine: Engine, data: dict) -> dict:
    did = data.get("doc_id") or make_doc_id(data["title"])
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO kb_document
                    (doc_id, title, doc_type, source_path, domain, is_enabled)
                VALUES
                    (:doc_id, :title, :doc_type, :source_path, :domain, :is_enabled)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    doc_type = VALUES(doc_type),
                    source_path = VALUES(source_path),
                    domain = VALUES(domain),
                    is_enabled = VALUES(is_enabled)
                """
            ),
            {
                "doc_id": did,
                "title": data["title"],
                "doc_type": data.get("doc_type") or "wiki",
                "source_path": data.get("source_path") or "",
                "domain": data.get("domain") or "",
                "is_enabled": 1 if data.get("is_enabled", True) else 0,
            },
        )
    return {"doc_id": did, "message": "已保存"}


def delete_document(meta_engine: Engine, doc_id: str) -> None:
    with meta_engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_chunk WHERE doc_id = :id"), {"id": doc_id})
        conn.execute(text("DELETE FROM kb_document WHERE doc_id = :id"), {"id": doc_id})


def list_chunks(meta_engine: Engine, doc_id: str) -> list[dict]:
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT chunk_id, doc_id, chunk_index, content, is_enabled
                FROM kb_chunk
                WHERE doc_id = :doc_id
                ORDER BY chunk_index
                """
            ),
            {"doc_id": doc_id},
        ).fetchall()
    return [
        {
            "chunk_id": r[0],
            "doc_id": r[1],
            "chunk_index": r[2],
            "content": r[3],
            "is_enabled": bool(r[4]),
        }
        for r in rows
    ]


def save_chunk(meta_engine: Engine, data: dict) -> dict:
    doc_id = data["doc_id"]
    chunk_index = int(data["chunk_index"])
    cid = data.get("chunk_id") or make_chunk_id(doc_id, chunk_index)
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO kb_chunk
                    (chunk_id, doc_id, chunk_index, content, is_enabled)
                VALUES
                    (:chunk_id, :doc_id, :chunk_index, :content, :is_enabled)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    chunk_index = VALUES(chunk_index),
                    is_enabled = VALUES(is_enabled)
                """
            ),
            {
                "chunk_id": cid,
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": data["content"],
                "is_enabled": 1 if data.get("is_enabled", True) else 0,
            },
        )
    return {"chunk_id": cid, "message": "已保存"}


def delete_chunk(meta_engine: Engine, chunk_id: str) -> None:
    with meta_engine.begin() as conn:
        conn.execute(text("DELETE FROM kb_chunk WHERE chunk_id = :id"), {"id": chunk_id})


def list_l1_tables(meta_engine: Engine) -> list[dict]:
    """已同步 L1 表列表（供 JOIN/同义词 选择）。"""
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT table_id, db_name, table_name, description
                FROM table_meta WHERE is_enabled = 1
                ORDER BY table_name
                """
            )
        ).fetchall()
    return [
        {"table_id": r[0], "db_name": r[1], "table_name": r[2], "description": r[3] or ""}
        for r in rows
    ]


def list_l1_columns(meta_engine: Engine, table_id: str) -> list[dict]:
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_id, column_name, description
                FROM column_meta
                WHERE table_id = :tid AND is_enabled = 1
                ORDER BY ordinal_pos, column_name
                """
            ),
            {"tid": table_id},
        ).fetchall()
    return [{"column_id": r[0], "column_name": r[1], "description": r[2] or ""} for r in rows]
