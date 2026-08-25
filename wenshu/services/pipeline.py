"""流水线编排：连接 → 初始化 → 扫描 → 审核 → 同步 → 向量化。"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from wenshu.services.init_metadata import init_metadata_tables
from wenshu.services.metadata_staging import (
    apply_ddl_to_staging,
    apply_llm_all_columns_by_table,
    apply_llm_to_staging,
    commit_staging_to_l1,
    scan_to_staging,
)


def run_init(meta_engine: Engine) -> dict:
    return init_metadata_tables(meta_engine)


def run_scan(
    raw_engine: Engine,
    meta_engine: Engine,
    apply_ddl: bool = False,
    apply_llm: bool = False,
    include_tables: list[str] | None = None,
) -> dict:
    return scan_to_staging(
        raw_engine,
        meta_engine,
        apply_ddl=apply_ddl,
        apply_llm=apply_llm,
        include_tables=include_tables,
    )


def _delete_purged_vectors(meta_engine: Engine, sync_result: dict) -> dict | None:
    purged_tables = sync_result.get("purged_table_ids") or []
    purged_columns = sync_result.get("purged_column_ids") or []
    if not purged_tables and not purged_columns:
        return None
    return delete_relation_vectors(meta_engine, relation_ids=[], table_ids=purged_tables, column_ids=purged_columns)


def delete_relation_vectors(
    meta_engine: Engine,
    relation_ids: list[str] | None = None,
    *,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
) -> dict | None:
    """从 Qdrant 与 vector_index_log 删除 JOIN（及可选表/字段）向量。"""
    relation_ids = list(relation_ids or [])
    table_ids = list(table_ids or [])
    column_ids = list(column_ids or [])
    if not relation_ids and not table_ids and not column_ids:
        return None

    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))

    import build_vector_index as bvi
    from db_config import create_qdrant_client, get_meta_mysql_dsn, get_meta_mysql_engine

    bvi.META_DSN = get_meta_mysql_dsn()
    engine = get_meta_mysql_engine()
    client = create_qdrant_client()
    return bvi.delete_vectors_for_objects(
        client,
        engine,
        table_ids=table_ids,
        column_ids=column_ids,
        relation_ids=relation_ids,
    )


def run_sync(
    raw_engine: Engine,
    meta_engine: Engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    purge_missing: bool = False,
    disable_absent: bool = False,
    delete_purged_vectors: bool = True,
) -> dict:
    """审核通过后，将 staging 写入 L1（支持全库 / 单表 / 单字段）。"""
    del raw_engine
    result = commit_staging_to_l1(
        meta_engine,
        table_ids=table_ids,
        column_ids=column_ids,
        purge_missing=purge_missing,
        disable_absent=disable_absent,
    )
    if purge_missing and delete_purged_vectors:
        vector_purge = _delete_purged_vectors(meta_engine, result)
        if vector_purge:
            result["vector_purge"] = vector_purge
    return result


def run_apply_ddl(meta_engine: Engine) -> dict:
    return apply_ddl_to_staging(meta_engine)


def run_apply_llm(
    meta_engine: Engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    overwrite: bool = False,
    table_only: bool = False,
    columns_only: bool = False,
    empty_only: bool = False,
    set_progress=None,
) -> dict:
    return apply_llm_to_staging(
        meta_engine,
        table_ids=table_ids,
        column_ids=column_ids,
        overwrite=overwrite,
        table_only=table_only,
        columns_only=columns_only,
        empty_only=empty_only,
        set_progress=set_progress,
    )


def run_apply_llm_all_columns(
    meta_engine: Engine,
    *,
    overwrite: bool = False,
    empty_only: bool = False,
    per_table_timeout: int = 360,
    set_progress=None,
) -> dict:
    return apply_llm_all_columns_by_table(
        meta_engine,
        overwrite=overwrite,
        empty_only=empty_only,
        per_table_timeout=per_table_timeout,
        set_progress=set_progress,
    )


def run_index(
    full: bool = False,
    types: str = "table,column,metric,join,doc_chunk",
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    relation_ids: list[str] | None = None,
    metric_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
    set_progress=None,
) -> dict:
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))

    import build_vector_index as bvi
    from db_config import get_meta_mysql_dsn, get_meta_mysql_engine

    sp = set_progress or (lambda **kwargs: None)
    sp(pct=1, message="正在初始化向量索引任务…")

    bvi.META_DSN = get_meta_mysql_dsn()
    type_set = set(t.strip() for t in types.split(",") if t.strip())

    if column_ids:
        type_set = type_set & {"column"}
        if not type_set:
            type_set = {"column"}
    elif table_ids:
        type_set = type_set & {"table", "column"}
        if not type_set:
            type_set = {"table", "column"}
    elif relation_ids:
        type_set = type_set & {"join"}
        if not type_set:
            type_set = {"join"}
    elif metric_ids:
        type_set = type_set & {"metric"}
        if not type_set:
            type_set = {"metric"}
    elif chunk_ids:
        type_set = type_set & {"doc_chunk"}
        if not type_set:
            type_set = {"doc_chunk"}

    from db_config import create_qdrant_client

    engine = get_meta_mysql_engine()
    sp(pct=3, message="正在从元数据库加载表/字段…")
    with engine.connect() as conn:
        items = bvi.load_objects(
            conn,
            type_set,
            table_ids=table_ids,
            column_ids=column_ids,
            relation_ids=relation_ids,
            metric_ids=metric_ids,
            chunk_ids=chunk_ids,
        )
    sp(pct=8, message=f"已加载 {len(items)} 个对象", done=0, total=len(items))

    client = create_qdrant_client()
    sp(pct=9, message="正在连接 Qdrant…")
    stats = bvi.upsert_vectors(client, engine, items, full=full, on_progress=sp)
    sp(pct=100, message="向量索引构建完成", done=len(items), total=len(items))

    scope = "full"
    if column_ids:
        scope = "column"
    elif table_ids:
        scope = "table"
    elif relation_ids:
        scope = "relation"
    elif metric_ids:
        scope = "metric"
    elif chunk_ids:
        scope = "chunk"

    return {
        "object_types": sorted(type_set),
        "objects_loaded": len(items),
        "full_rebuild": full,
        "collection": bvi.QDRANT_COLLECTION,
        "scope": scope,
        "table_ids": table_ids or [],
        "column_ids": column_ids or [],
        "relation_ids": relation_ids or [],
        "metric_ids": metric_ids or [],
        "chunk_ids": chunk_ids or [],
        **stats,
    }


def run_sync_and_index(
    raw_engine: Engine,
    meta_engine: Engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    full_index: bool = False,
    purge_missing: bool = False,
) -> dict:
    """同步元数据到 L1 后，按相同范围更新向量索引。"""
    sync_result = run_sync(
        raw_engine,
        meta_engine,
        table_ids=table_ids,
        column_ids=column_ids,
        purge_missing=purge_missing,
        delete_purged_vectors=True,
    )
    index_result = run_index(
        full=full_index,
        table_ids=table_ids,
        column_ids=column_ids,
    )
    return {"sync": sync_result, "index": index_result}


def run_all(
    raw_engine: Engine,
    meta_engine: Engine,
    full_index: bool = False,
    apply_llm: bool = False,
) -> dict:
    init_result = run_init(meta_engine)
    scan_result = run_scan(raw_engine, meta_engine, apply_ddl=False, apply_llm=apply_llm)
    sync_result: dict | None = None
    sync_error: str | None = None
    try:
        sync_result = run_sync(raw_engine, meta_engine)
    except Exception as exc:
        sync_error = str(exc)

    index_result: dict | None = None
    index_error: str | None = None
    if sync_result:
        try:
            index_result = run_index(full=full_index)
        except Exception as exc:
            index_error = str(exc)

    return {
        "init": init_result,
        "scan": scan_result,
        "sync": sync_result,
        "sync_error": sync_error,
        "index": index_result,
        "index_error": index_error,
    }
