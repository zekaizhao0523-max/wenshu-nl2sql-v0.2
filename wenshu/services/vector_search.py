"""向量检索：单集合 + payload.db + 检索时按库过滤（方案 A）。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

# 默认与历史行为一致：table/column/join/metric/doc 均可参与混排
DEFAULT_INCLUDE_TYPES = frozenset({"table", "column", "join", "metric", "doc_chunk"})


def list_indexed_databases(meta_engine: Engine) -> list[str]:
    """L1 中已启用表的去重库名列表。"""
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT db_name
                FROM table_meta
                WHERE is_enabled = 1 AND db_name IS NOT NULL AND db_name != ''
                ORDER BY db_name
                """
            )
        ).fetchall()
    return [str(r[0]) for r in rows]


def build_db_scope_filter(
    db_names: list[str] | None,
    *,
    include_types: frozenset[str] | set[str] | None = None,
):
    """
    Qdrant 过滤：表/字段限定 db；JOIN 任一侧在库内；指标/文档不受库限制。
    db_names 为空或 None 时不过滤库（仍可按 include_types 过滤类型）。
    include_types 默认含 table/column/join/metric/doc_chunk。
    """
    types = set(include_types or DEFAULT_INCLUDE_TYPES)

    from qdrant_client.http.models import FieldCondition, Filter, MatchAny, MatchValue

    clauses: list[Filter] = []

    names = sorted({n.strip() for n in (db_names or []) if (n or "").strip()})
    db_match = MatchAny(any=names) if names else None

    if "table" in types or "column" in types:
        schema_types = [t for t in ("table", "column") if t in types]
        if schema_types:
            if db_match is not None:
                clauses.append(
                    Filter(
                        must=[
                            FieldCondition(key="object_type", match=MatchAny(any=schema_types)),
                            FieldCondition(key="db", match=db_match),
                        ]
                    )
                )
            else:
                clauses.append(
                    Filter(
                        must=[
                            FieldCondition(key="object_type", match=MatchAny(any=schema_types)),
                        ]
                    )
                )

    if "join" in types:
        if db_match is not None:
            clauses.append(
                Filter(
                    must=[
                        FieldCondition(key="object_type", match=MatchValue(value="join")),
                        FieldCondition(key="left_db", match=db_match),
                    ]
                )
            )
            clauses.append(
                Filter(
                    must=[
                        FieldCondition(key="object_type", match=MatchValue(value="join")),
                        FieldCondition(key="right_db", match=db_match),
                    ]
                )
            )
        else:
            clauses.append(
                Filter(
                    must=[
                        FieldCondition(key="object_type", match=MatchValue(value="join")),
                    ]
                )
            )

    for global_type in ("metric", "doc_chunk"):
        if global_type in types:
            clauses.append(
                Filter(
                    must=[
                        FieldCondition(key="object_type", match=MatchValue(value=global_type)),
                    ]
                )
            )

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return Filter(should=clauses)


def resolve_search_db_names(
    *,
    db_names: list[str] | None,
    all_databases: bool,
    default_raw_db: str | None,
) -> tuple[list[str] | None, str]:
    """
    解析检索库范围。
    返回 (filter_db_names, filter_mode_label)。
    filter_db_names=None 表示不过滤（全库）。
    """
    if all_databases:
        return None, "all"

    if db_names:
        cleaned = [n.strip() for n in db_names if (n or "").strip()]
        if cleaned:
            return cleaned, "selected"

    if default_raw_db:
        return [default_raw_db], "current_raw"

    return None, "all_unconfigured"


def search_collection(
    client,
    collection_name: str,
    query_vector: list[float],
    *,
    limit: int = 10,
    db_names: list[str] | None = None,
    include_types: frozenset[str] | set[str] | None = None,
) -> list:
    """带可选 db 范围与 object_type 范围的向量检索。"""
    query_filter = build_db_scope_filter(db_names, include_types=include_types)
    kwargs = {
        "collection_name": collection_name,
        "query": query_vector,
        "limit": limit,
    }
    if query_filter is not None:
        kwargs["query_filter"] = query_filter
    result = client.query_points(**kwargs)
    return list(result.points or [])
