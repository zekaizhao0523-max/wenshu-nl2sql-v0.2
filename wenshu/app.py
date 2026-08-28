"""问数元数据管理平台 API。"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from db_config import dispose_mysql_engines, get_meta_mysql_engine, get_raw_mysql_engine  # noqa: E402

from wenshu.job_manager import job_manager  # noqa: E402
from wenshu.services import pipeline  # noqa: E402
from wenshu.services.connections import (  # noqa: E402
    _ensure_dotenv,
    apply_overlay_to_environ,
    get_connection_values,
    get_role_connection,
    list_all_connections,
    list_wizard,
    parse_connection_text,
    save_connection,
    test_connection,
)
from wenshu.services.model_config import (
    apply_model_settings_to_environ,
    get_model_settings,
    save_model_settings,
    test_embedding_connection,
    test_llm_connection,
)
from wenshu.services.metadata_staging import (  # noqa: E402
    count_llm_pending,
    clear_staging,
    get_staging_stats,
    inherit_l1_column_to_staging,
    list_staging_columns,
    list_staging_tables,
    load_staging_table_editor,
    merge_l1_into_staging,
    save_all_nonempty_staging_comments,
    update_staging_column,
    update_staging_columns_batch,
    update_staging_table,
    validate_staging,
)
from wenshu.services.l1_meta import (  # noqa: E402
    delete_chunk,
    delete_document,
    delete_metric,
    delete_relation,
    delete_synonym,
    list_chunks,
    list_documents,
    list_l1_columns,
    list_l1_tables,
    list_metrics,
    list_relations,
    list_synonyms,
    seed_retrieval_synonyms,
    save_chunk,
    save_document,
    save_metric,
    save_relation,
    save_synonym,
)
from wenshu.services.health import get_workflow_status  # noqa: E402
from wenshu.services.stats import get_overview, list_columns, list_tables, metadata_tables_exist  # noqa: E402
from wenshu.services.sync_mysql import list_raw_business_tables  # noqa: E402

# 本地 embedding 仅需 PyTorch，禁用 Transformers 的 TensorFlow 依赖
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")
_ensure_dotenv()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    apply_overlay_to_environ()
    apply_model_settings_to_environ()
    yield
    dispose_mysql_engines()


app = FastAPI(title="问数agent", version="2.3.0", lifespan=_lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class IndexRequest(BaseModel):
    full: bool = False
    types: str = "table,column,metric,join,doc_chunk"
    table_ids: Optional[list[str]] = None
    column_ids: Optional[list[str]] = None
    relation_ids: Optional[list[str]] = None
    metric_ids: Optional[list[str]] = None
    chunk_ids: Optional[list[str]] = None


class SyncRequest(BaseModel):
    table_ids: Optional[list[str]] = None
    column_ids: Optional[list[str]] = None
    update_vectors: bool = False
    purge_missing: bool = False
    disable_absent: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    db_names: Optional[list[str]] = None
    all_databases: bool = False
    evidence: str = Field(default="", max_length=4000)
    keyword_mode: Optional[str] = Field(
        default=None,
        description="关键词抽取：auto | llm | rule",
    )
    column_select: Optional[bool] = Field(
        default=None,
        description="是否对检索池做 LLM 两轮选列（S1/S2）；默认跟 SCHEMA_COLUMN_SELECT",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="澄清会话 ID（need_clarify 后 resume 时带上）",
    )
    clarify_answers: Optional[dict[str, str]] = Field(
        default=None,
        description="澄清回答 {question_id: answer_text}",
    )
    skip_clarify_gate: bool = Field(
        default=False,
        description="跳过澄清闸门（评测/调试）",
    )


class AgentQueryRequest(BaseModel):
    """对齐 icecoding：编排主链路（澄清 → 召回 → QueryPlan → SQL → 风险 → 沙箱）。"""

    query: str = Field(min_length=1, max_length=500)
    evidence: str = Field(default="", max_length=4000)
    trace_id: Optional[str] = None
    clarify_answers: Optional[dict[str, str]] = None
    keyword_mode: Optional[str] = "rule"
    column_select: bool = False
    skip_sandbox: Optional[bool] = None


class AgentApproveRequest(BaseModel):
    trace_id: str = Field(min_length=1)
    approved: bool = True
    skip_sandbox: Optional[bool] = None


class MSchemaRequest(BaseModel):
    """构建 M-Schema：可直接指定 table/column，或用 question 走向量召回。"""

    question: Optional[str] = Field(default=None, max_length=500)
    evidence: str = Field(default="", max_length=4000)
    table_ids: Optional[list[str]] = None
    column_ids: Optional[list[str]] = None
    db_name: Optional[str] = None
    db_names: Optional[list[str]] = None
    all_databases: bool = False
    limit: int = Field(default=30, ge=1, le=50)
    include_examples: bool = True
    example_num: int = Field(default=3, ge=0, le=5)
    include_relations: bool = True
    infer_keys: bool = False
    include_pk: bool = False
    column_select: bool = True
    keyword_mode: Optional[str] = Field(
        default=None,
        description="auto | off | llm | rule，与召回测试一致",
    )
    schema_stage: str = Field(
        default="s2",
        description="M-Schema 列来源：s2 | s1 | pool",
    )


class Nl2SqlPromptRequest(MSchemaRequest):
    question: str = Field(min_length=1, max_length=500)
    evidence: str = Field(default="", max_length=4000)
    dialect: Optional[str] = None


class ConnectionSaveRequest(BaseModel):
    values: dict = Field(default_factory=dict)
    engine: Optional[str] = None


class ConnectionTestRequest(BaseModel):
    type: Optional[str] = None
    role: Optional[str] = None
    engine: Optional[str] = None
    values: dict = Field(default_factory=dict)


class ConnectionParseRequest(BaseModel):
    type: Optional[str] = None
    role: Optional[str] = None
    engine: Optional[str] = None
    text: str = Field(min_length=1, max_length=4000)


class ScanRequest(BaseModel):
    apply_ddl: bool = False
    apply_llm: bool = False
    include_tables: Optional[list[str]] = None


class StagingTextUpdate(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class StagingColumnBatchItem(BaseModel):
    column_id: str = Field(min_length=1, max_length=64)
    description: str = Field(max_length=2000)


class StagingColumnsBatchUpdate(BaseModel):
    columns: list[StagingColumnBatchItem] = Field(min_length=1)


class StagingSaveAllRequest(BaseModel):
    current_table_id: Optional[str] = None
    table_description: Optional[str] = Field(default=None, max_length=2000)
    columns: Optional[list[StagingColumnBatchItem]] = None


class StagingInheritL1(BaseModel):
    l1_column_id: str = Field(min_length=1, max_length=64)


class StagingMergeL1Request(BaseModel):
    table_ids: Optional[list[str]] = None


class StagingClearRequest(BaseModel):
    table_ids: Optional[list[str]] = None


class LlmGenerateRequest(BaseModel):
    table_ids: Optional[list[str]] = None
    column_ids: Optional[list[str]] = None
    overwrite: bool = False
    table_only: bool = False
    columns_only: bool = False
    empty_only: bool = False


class RelationSave(BaseModel):
    relation_id: Optional[str] = None
    left_db: str = Field(min_length=1, max_length=128)
    left_table: str = Field(min_length=1, max_length=256)
    left_column: str = Field(min_length=1, max_length=256)
    right_db: str = Field(min_length=1, max_length=128)
    right_table: str = Field(min_length=1, max_length=256)
    right_column: str = Field(min_length=1, max_length=256)
    join_type: str = "LEFT JOIN"
    description: Optional[str] = ""
    is_enabled: bool = True


class MetricSave(BaseModel):
    metric_id: Optional[str] = None
    metric_name: str = Field(min_length=1, max_length=128)
    cn_name: Optional[str] = ""
    aliases: Optional[list[str]] = None
    definition: str = Field(min_length=1)
    sql_template: Optional[str] = ""
    related_tables: Optional[list[str]] = None
    domain: Optional[str] = ""
    is_enabled: bool = True


class SynonymSave(BaseModel):
    synonym_id: Optional[str] = None
    term: str = Field(min_length=1, max_length=256)
    target_type: str = Field(pattern="^(table|column|metric|concept)$")
    target_id: str = Field(min_length=1, max_length=64)
    is_enabled: bool = True


class DocumentSave(BaseModel):
    doc_id: Optional[str] = None
    title: str = Field(min_length=1, max_length=512)
    doc_type: str = "wiki"
    source_path: Optional[str] = ""
    domain: Optional[str] = ""
    is_enabled: bool = True


class ChunkSave(BaseModel):
    chunk_id: Optional[str] = None
    doc_id: str = Field(min_length=1, max_length=64)
    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    is_enabled: bool = True


def _engines():
    return get_raw_mysql_engine(), get_meta_mysql_engine()


@app.get("/")
def index_page():
    return FileResponse(STATIC_DIR / "index.html")


class ModelSettingsPayload(BaseModel):
    embedding: Optional[dict] = None
    llm: Optional[dict] = None


@app.get("/api/model-settings")
def api_model_settings_get(reveal: bool = False):
    apply_model_settings_to_environ()
    return get_model_settings(reveal_secrets=reveal)


@app.put("/api/model-settings")
def api_model_settings_save(body: ModelSettingsPayload):
    payload = body.model_dump(exclude_none=True)
    return {"ok": True, "message": "模型配置已保存", **save_model_settings(payload)}


@app.post("/api/model-settings/test-llm")
def api_model_settings_test_llm():
    apply_model_settings_to_environ()
    try:
        return test_llm_connection()
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@app.post("/api/model-settings/test-embedding")
def api_model_settings_test_embedding():
    apply_model_settings_to_environ()
    try:
        return test_embedding_connection()
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@app.get("/api/connections/types")
def api_connection_types():
    """向导 schema：三步 × 可选引擎。"""
    return {"steps": list_wizard()}


@app.post("/api/connections/dispose-pools")
def api_dispose_db_pools():
    """手动释放平台占用的 MySQL 连接池（远程库连接数满时可先调此接口）。"""
    dispose_mysql_engines()
    return {"ok": True, "message": "已释放 MySQL 连接池，下次请求将按需重建"}


@app.get("/api/connections")
def api_connections():
    return {"items": list_all_connections(mask_secrets=True)}


@app.post("/api/connections/test")
def api_connection_test(body: ConnectionTestRequest):
    try:
        return test_connection(body.type, body.values, role=body.role, engine=body.engine)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/connections/parse")
def api_connection_parse(body: ConnectionParseRequest):
    try:
        return parse_connection_text(body.type, body.text, role=body.role, engine=body.engine)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/connections/{conn_type}")
def api_connection_detail(conn_type: str, engine: Optional[str] = None, reveal: bool = False):
    """reveal=1 时返回明文密钥，仅供连接配置页编辑（本地平台）。"""
    try:
        mask = not reveal
        if engine:
            return get_role_connection(conn_type, engine, mask_secrets=mask)
        return get_connection_values(conn_type, mask_secrets=mask)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/connections/{conn_type}")
def api_connection_save(conn_type: str, body: ConnectionSaveRequest):
    """conn_type 可为角色 raw/meta/vector，或旧 id / role:engine。"""
    try:
        if body.engine:
            saved = save_connection(role=conn_type, engine=body.engine, values=body.values)
        else:
            saved = save_connection(conn_type, body.values)
        return {"ok": True, "message": "已保存", **saved}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/overview")
def api_overview():
    raw_engine, meta_engine = _engines()
    return get_overview(raw_engine, meta_engine)


@app.get("/api/raw/tables")
def api_raw_tables(q: str = ""):
    """业务库表清单（用于「指定表扫描」，不依赖暂存区）。"""
    raw_engine, _ = _engines()
    try:
        items = list_raw_business_tables(raw_engine)
    except Exception as exc:
        from wenshu.services.connections import _friendly_db_error

        raise HTTPException(400, _friendly_db_error(exc)) from exc
    if q.strip():
        kw = q.strip().lower()
        items = [
            t
            for t in items
            if kw in (t["table_name"] or "").lower()
            or kw in (t.get("hive_comment") or "").lower()
        ]
    return {"items": items}


@app.get("/api/workflow")
def api_workflow():
    raw_engine, meta_engine = _engines()
    return get_workflow_status(raw_engine, meta_engine)


@app.get("/api/tables")
def api_tables(limit: int = 200, offset: int = 0, q: str = ""):
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        return {"items": [], "message": "请先执行「初始化元数据表」"}
    items = list_tables(meta_engine, limit=limit, offset=offset)
    if q.strip():
        kw = q.strip().lower()
        items = [
            t
            for t in items
            if kw in (t["table_name"] or "").lower()
            or kw in (t["hive_comment"] or "").lower()
            or kw in (t["description"] or "").lower()
        ]
    return {"items": items}


@app.get("/api/tables/{table_id}/columns")
def api_columns(table_id: str):
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        raise HTTPException(400, "元数据表尚未初始化")
    return {"items": list_columns(meta_engine, table_id)}


def _submit_or_run(name: str, fn, async_run: bool):
    if async_run:
        job_id = job_manager.create(name, fn)
        return {"job_id": job_id, "async": True, "message": "任务已提交"}
    return {"result": fn(), "async": False}


@app.post("/api/pipeline/init")
def api_pipeline_init(async_run: bool = True):
    _, meta_engine = _engines()
    return _submit_or_run("init_metadata", lambda: pipeline.run_init(meta_engine), async_run)


@app.post("/api/pipeline/scan")
def api_pipeline_scan(body: ScanRequest, async_run: bool = True):
    raw_engine, meta_engine = _engines()

    def task():
        if not metadata_tables_exist(meta_engine):
            pipeline.run_init(meta_engine)
        return pipeline.run_scan(
            raw_engine,
            meta_engine,
            apply_ddl=body.apply_ddl,
            apply_llm=body.apply_llm,
            include_tables=body.include_tables,
        )

    return _submit_or_run("scan_raw", task, async_run)


@app.post("/api/pipeline/sync")
def api_pipeline_sync(body: Optional[SyncRequest] = None, async_run: bool = True):
    body = body or SyncRequest()
    raw_engine, meta_engine = _engines()

    def task():
        sync_kw = {
            "table_ids": body.table_ids,
            "column_ids": body.column_ids,
            "purge_missing": body.purge_missing,
            "disable_absent": body.disable_absent,
        }
        if body.update_vectors and (body.table_ids or body.column_ids):
            return pipeline.run_sync_and_index(
                raw_engine,
                meta_engine,
                table_ids=body.table_ids,
                column_ids=body.column_ids,
                purge_missing=body.purge_missing,
            )
        if body.update_vectors and not body.table_ids and not body.column_ids:
            sync = pipeline.run_sync(raw_engine, meta_engine, **sync_kw)
            index = pipeline.run_index(full=False)
            return {"sync": sync, "index": index}
        return pipeline.run_sync(raw_engine, meta_engine, **sync_kw)

    return _submit_or_run("commit_staging", task, async_run)


@app.post("/api/pipeline/index")
def api_pipeline_index(body: Optional[IndexRequest] = None, async_run: bool = True):
    body = body or IndexRequest()

    def task(set_progress):
        return pipeline.run_index(
            full=body.full,
            types=body.types,
            table_ids=body.table_ids,
            column_ids=body.column_ids,
            relation_ids=body.relation_ids,
            metric_ids=body.metric_ids,
            chunk_ids=body.chunk_ids,
            set_progress=set_progress,
        )

    return _submit_or_run("build_vector_index", task, async_run)


@app.post("/api/pipeline/run-all")
def api_pipeline_run_all(full_index: bool = False):
    raw_engine, meta_engine = _engines()
    job_id = job_manager.create(
        "run_all",
        lambda: pipeline.run_all(raw_engine, meta_engine, full_index=full_index),
    )
    return {"job_id": job_id, "async": True, "message": "全流程任务已提交"}


@app.get("/api/staging/stats")
def api_staging_stats():
    _, meta_engine = _engines()
    return validate_staging(meta_engine)


@app.get("/api/staging/tables")
def api_staging_tables(q: str = ""):
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        return {"items": [], "message": "请先执行「初始化元数据表」"}
    return {"items": list_staging_tables(meta_engine, q=q), **validate_staging(meta_engine)}


@app.get("/api/staging/tables/{table_id}/columns")
def api_staging_columns(table_id: str):
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        raise HTTPException(400, "元数据表尚未初始化")
    data = load_staging_table_editor(meta_engine, table_id)
    return {
        "table": data["table"],
        "items": data["items"],
        "l1_orphans": data.get("l1_orphans") or [],
    }


@app.put("/api/staging/tables/{table_id}")
def api_staging_update_table(table_id: str, body: StagingTextUpdate):
    _, meta_engine = _engines()
    try:
        update_staging_table(meta_engine, table_id, body.description)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": "已保存"}


@app.put("/api/staging/columns/{column_id}")
def api_staging_update_column(column_id: str, body: StagingTextUpdate):
    _, meta_engine = _engines()
    try:
        update_staging_column(meta_engine, column_id, body.description)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": "已保存"}


def _staging_update_columns_batch_handler(table_id: str, body: StagingColumnsBatchUpdate):
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        raise HTTPException(400, "元数据表尚未初始化")
    try:
        result = update_staging_columns_batch(
            meta_engine,
            table_id,
            [c.model_dump() for c in body.columns],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result["saved"] == 0:
        raise HTTPException(400, "没有可保存的字段注释（说明不能为空）")
    return {"ok": True, "message": f"已保存 {result['saved']} 个字段注释", **result}


@app.put("/api/staging/tables/{table_id}/columns/batch")
@app.post("/api/staging/tables/{table_id}/columns/batch")
def api_staging_update_columns_batch(table_id: str, body: StagingColumnsBatchUpdate):
    return _staging_update_columns_batch_handler(table_id, body)


@app.post("/api/staging/clear")
def api_staging_clear(body: Optional[StagingClearRequest] = None):
    """清空暂存区（全库或指定表；未同步的编辑将丢失）。"""
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        raise HTTPException(400, "元数据表尚未初始化")
    table_ids = body.table_ids if body else None
    try:
        return clear_staging(meta_engine, table_ids=table_ids)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/staging/merge-l1")
def api_staging_merge_l1(body: Optional[StagingMergeL1Request] = None):
    """用户在元数据编辑页手动触发：将 L1 同名表/字段说明合并进暂存（不覆盖 manual）。"""
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        raise HTTPException(400, "元数据表尚未初始化")
    table_ids = body.table_ids if body else None
    return merge_l1_into_staging(meta_engine, table_ids=table_ids)


@app.post("/api/staging/columns/{column_id}/inherit-l1")
def api_staging_inherit_l1(column_id: str, body: StagingInheritL1):
    """手动：将 L1 旧字段说明映射到暂存字段（改名字段场景）。"""
    _, meta_engine = _engines()
    try:
        return inherit_l1_column_to_staging(meta_engine, column_id, body.l1_column_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/staging/save-all-comments")
@app.put("/api/staging/save-all-comments")
def api_staging_save_all_comments(body: StagingSaveAllRequest):
    _, meta_engine = _engines()
    columns = None
    if body.columns:
        columns = [
            {"column_id": c.column_id, "description": c.description}
            for c in body.columns
            if (c.description or "").strip()
        ] or None
    return save_all_nonempty_staging_comments(
        meta_engine,
        current_table_id=body.current_table_id,
        current_table_description=body.table_description,
        current_columns=columns,
    )


@app.post("/api/staging/llm-pending")
def api_staging_llm_pending(body: LlmGenerateRequest):
    _, meta_engine = _engines()
    return count_llm_pending(
        meta_engine,
        table_ids=body.table_ids,
        column_ids=body.column_ids,
        overwrite=body.overwrite,
        table_only=body.table_only,
        columns_only=body.columns_only,
        empty_only=body.empty_only,
    )


@app.post("/api/staging/extract-ddl")
def api_staging_extract_ddl(async_run: bool = False):
    _, meta_engine = _engines()

    def task():
        return pipeline.run_apply_ddl(meta_engine)

    return _submit_or_run("extract_ddl", task, async_run)


@app.post("/api/staging/generate-llm")
def api_staging_generate_llm(body: LlmGenerateRequest, async_run: bool = True):
    _, meta_engine = _engines()

    def task(set_progress):
        return pipeline.run_apply_llm(
            meta_engine,
            table_ids=body.table_ids,
            column_ids=body.column_ids,
            overwrite=body.overwrite,
            table_only=body.table_only,
            columns_only=body.columns_only,
            empty_only=body.empty_only,
            set_progress=set_progress,
        )

    return _submit_or_run("generate_llm", task, async_run)


@app.post("/api/staging/generate-llm-all-columns")
def api_staging_generate_llm_all_columns(body: LlmGenerateRequest, async_run: bool = True):
    _, meta_engine = _engines()

    def task(set_progress):
        return pipeline.run_apply_llm_all_columns(
            meta_engine,
            overwrite=body.overwrite,
            empty_only=body.empty_only,
            per_table_timeout=360,
            set_progress=set_progress,
        )

    return _submit_or_run("generate_llm_all_columns", task, async_run)


# ---------- L1 扩展元数据（JOIN / 指标 / 同义词 / 知识库）----------


@app.get("/api/l1/tables")
def api_l1_tables():
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        return {"items": []}
    return {"items": list_l1_tables(meta_engine)}


@app.get("/api/l1/tables/{table_id}/columns")
def api_l1_table_columns(table_id: str):
    _, meta_engine = _engines()
    return {"items": list_l1_columns(meta_engine, table_id)}


@app.get("/api/l1/relations")
def api_l1_relations():
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        return {"items": []}
    return {"items": list_relations(meta_engine)}


@app.post("/api/l1/relations")
def api_l1_relation_save(body: RelationSave):
    _, meta_engine = _engines()
    data = body.model_dump()
    result = save_relation(meta_engine, data)
    vector_purge = None
    vector_purge_error = None
    if not data.get("is_enabled", True):
        try:
            vector_purge = pipeline.delete_relation_vectors(meta_engine, [result["relation_id"]])
        except Exception as exc:
            vector_purge_error = str(exc)
    out = {**result}
    if vector_purge:
        out["vector_purge"] = vector_purge
        out["message"] = "已保存；该 JOIN 已禁用，向量库中的对应向量已清理"
    elif vector_purge_error:
        out["vector_purge_error"] = vector_purge_error
        out["message"] = "已保存；向量库清理失败，请稍后在「向量库同步」重试或联系管理员"
    return out


@app.delete("/api/l1/relations/{relation_id}")
def api_l1_relation_delete(relation_id: str):
    _, meta_engine = _engines()
    delete_relation(meta_engine, relation_id)
    vector_purge = None
    vector_purge_error = None
    try:
        vector_purge = pipeline.delete_relation_vectors(meta_engine, [relation_id])
    except Exception as exc:
        vector_purge_error = str(exc)
    out = {
        "ok": True,
        "vector_purge": vector_purge or {"vectors_deleted": 0, "purged_relation_ids": [relation_id]},
    }
    if vector_purge_error:
        out["vector_purge_error"] = vector_purge_error
    return out


@app.get("/api/l1/metrics")
def api_l1_metrics():
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        return {"items": []}
    return {"items": list_metrics(meta_engine)}


@app.post("/api/l1/metrics")
def api_l1_metric_save(body: MetricSave):
    _, meta_engine = _engines()
    return save_metric(meta_engine, body.model_dump())


@app.delete("/api/l1/metrics/{metric_id}")
def api_l1_metric_delete(metric_id: str):
    _, meta_engine = _engines()
    delete_metric(meta_engine, metric_id)
    return {"ok": True}


@app.get("/api/l1/synonyms")
def api_l1_synonyms(q: str = ""):
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        return {"items": []}
    return {"items": list_synonyms(meta_engine, q=q)}


@app.post("/api/l1/synonyms")
def api_l1_synonym_save(body: SynonymSave):
    _, meta_engine = _engines()
    return save_synonym(meta_engine, body.model_dump())


@app.delete("/api/l1/synonyms/{synonym_id}")
def api_l1_synonym_delete(synonym_id: str):
    _, meta_engine = _engines()
    delete_synonym(meta_engine, synonym_id)
    return {"ok": True}


@app.post("/api/l1/synonyms/seed-retrieval")
def api_l1_synonyms_seed_retrieval():
    """把业务概念种子写入 L1 synonym(concept)，并停用旧 table/column 硬绑同义词。"""
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        raise HTTPException(400, "元数据表尚未初始化")
    from wenshu.services.schema_retrieval import refresh_retrieval_lexicon

    result = seed_retrieval_synonyms(meta_engine)
    refresh_retrieval_lexicon(meta_engine)
    return {"ok": True, "message": "已导入业务概念词典", **result}


@app.get("/api/l1/documents")
def api_l1_documents():
    _, meta_engine = _engines()
    if not metadata_tables_exist(meta_engine):
        return {"items": []}
    return {"items": list_documents(meta_engine)}


@app.post("/api/l1/documents")
def api_l1_document_save(body: DocumentSave):
    _, meta_engine = _engines()
    return save_document(meta_engine, body.model_dump())


@app.delete("/api/l1/documents/{doc_id}")
def api_l1_document_delete(doc_id: str):
    _, meta_engine = _engines()
    delete_document(meta_engine, doc_id)
    return {"ok": True}


@app.get("/api/l1/documents/{doc_id}/chunks")
def api_l1_chunks(doc_id: str):
    _, meta_engine = _engines()
    return {"items": list_chunks(meta_engine, doc_id)}


@app.post("/api/l1/chunks")
def api_l1_chunk_save(body: ChunkSave):
    _, meta_engine = _engines()
    return save_chunk(meta_engine, body.model_dump())


@app.delete("/api/l1/chunks/{chunk_id}")
def api_l1_chunk_delete(chunk_id: str):
    _, meta_engine = _engines()
    delete_chunk(meta_engine, chunk_id)
    return {"ok": True}


@app.get("/api/jobs")
def api_jobs():
    jobs = job_manager.list_recent()
    return {
        "items": [
            {
                "job_id": j.job_id,
                "name": j.name,
                "status": j.status,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "finished_at": j.finished_at,
                "error": j.error,
                "progress_pct": j.progress_pct,
                "progress_message": j.progress_message,
                "progress_done": j.progress_done,
                "progress_total": j.progress_total,
                "result": j.result if j.status == "success" else None,
            }
            for j in jobs
        ]
    }


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return {
        "job_id": job.job_id,
        "name": job.name,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "result": job.result,
        "error": job.error,
        "progress_pct": job.progress_pct,
        "progress_message": job.progress_message,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
    }


@app.get("/api/search/databases")
def api_search_databases():
    """已索引的原始库列表 + 当前连接的 raw 库名。"""
    from db_config import get_meta_mysql_engine, get_raw_database_name

    meta_engine = get_meta_mysql_engine()
    from wenshu.services.vector_search import list_indexed_databases

    indexed = list_indexed_databases(meta_engine)
    current_raw = get_raw_database_name()
    return {
        "indexed_databases": indexed,
        "current_raw_database": current_raw,
    }


@app.post("/api/nl2sql/mschema")
def api_nl2sql_mschema(body: MSchemaRequest):
    """从 L1 + 可选业务库 PK/Examples 构建 M-Schema 文本（XiYan 格式）。"""
    import build_vector_index as bvi
    from db_config import create_qdrant_client, get_meta_mysql_dsn, get_raw_database_name

    from wenshu.services.m_schema import build_mschema_from_question, build_mschema_text
    from wenshu.services.vector_search import resolve_search_db_names

    bvi.META_DSN = get_meta_mysql_dsn()
    bvi._load_dotenv()

    raw_engine, meta_engine = _engines()

    filter_dbs, _ = resolve_search_db_names(
        db_names=body.db_names,
        all_databases=body.all_databases,
        default_raw_db=body.db_name or get_raw_database_name(),
    )

    if body.question and not body.table_ids and not body.column_ids:
        client = create_qdrant_client()
        if not client.collection_exists(bvi.QDRANT_COLLECTION):
            raise HTTPException(400, "向量集合尚未创建，请先执行「构建向量索引」")
        return build_mschema_from_question(
            meta_engine,
            raw_engine if body.include_examples else None,
            question=body.question,
            evidence=body.evidence or "",
            qdrant_client=client,
            collection_name=bvi.QDRANT_COLLECTION,
            embed_fn=bvi.embed,
            db_names=filter_dbs,
            limit=body.limit,
            include_examples=body.include_examples,
            example_num=body.example_num,
            include_relations=body.include_relations,
            infer_keys=body.infer_keys,
            include_pk=body.include_pk,
            column_select=body.column_select,
            keyword_mode=body.keyword_mode,
            schema_stage=body.schema_stage,
        )

    db_name = body.db_name or (filter_dbs[0] if filter_dbs else get_raw_database_name())
    if not db_name:
        raise HTTPException(400, "请指定 db_name 或配置原始库连接")

    if not body.table_ids and not body.column_ids:
        raise HTTPException(400, "请提供 question（向量召回）或 table_ids / column_ids")

    mschema_text = build_mschema_text(
        meta_engine,
        db_name=db_name,
        table_ids=body.table_ids,
        column_ids=body.column_ids,
        raw_engine=raw_engine if body.include_examples else None,
        include_examples=body.include_examples,
        example_num=body.example_num,
        include_relations=body.include_relations,
        infer_keys=body.infer_keys,
        include_pk=body.include_pk,
    )
    return {
        "db_name": db_name,
        "mschema": mschema_text,
        "selection": {
            "table_ids": body.table_ids,
            "column_ids": body.column_ids,
        },
    }


@app.post("/api/nl2sql/prompt")
def api_nl2sql_prompt(body: Nl2SqlPromptRequest):
    """构建 XiYan 官方 NL2SQL Prompt（含 M-Schema）。"""
    from db_config import get_raw_database_name

    from wenshu.services.m_schema import build_nl2sql_prompt

    mschema_res = api_nl2sql_mschema(body)
    dialect = body.dialect
    if not dialect:
        try:
            from db_config import get_raw_mysql_engine

            dialect = get_raw_mysql_engine().dialect.name
        except Exception:
            dialect = "mysql"

    prompt = build_nl2sql_prompt(
        dialect=dialect,
        db_schema=mschema_res["mschema"],
        question=body.question,
        evidence=body.evidence,
    )
    return {
        **mschema_res,
        "dialect": dialect,
        "evidence": body.evidence,
        "prompt": prompt,
    }


@app.post("/api/agent/query")
def api_agent_query(body: AgentQueryRequest):
    """问数 Agent 编排入口（对齐 icecoding graph 主链路）。"""
    from wenshu.services.agent.orchestrator import run_agent

    try:
        state = run_agent(
            body.query,
            body.evidence or "",
            trace_id=body.trace_id,
            clarify_answers=body.clarify_answers,
            keyword_mode=body.keyword_mode,
            column_select=body.column_select,
            skip_sandbox=body.skip_sandbox,
        )
        return {"ok": True, **state.as_dict()}
    except Exception as exc:
        raise HTTPException(500, f"Agent 执行失败: {exc}") from exc


@app.post("/api/agent/approve")
def api_agent_approve(body: AgentApproveRequest):
    """人工审批 resume（对齐 icecoding human_review）。"""
    from wenshu.services.agent.orchestrator import run_agent

    try:
        state = run_agent(
            question="",
            evidence="",
            trace_id=body.trace_id,
            human_approved=body.approved,
            skip_sandbox=body.skip_sandbox,
        )
        return {"ok": True, **state.as_dict()}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"审批恢复失败: {exc}") from exc


@app.post("/api/search/test")
def api_search_test(body: SearchRequest):
    import build_vector_index as bvi
    from db_config import create_qdrant_client, get_meta_mysql_dsn, get_meta_mysql_engine, get_raw_database_name

    from wenshu.services.query_clarify import prepare_query
    from wenshu.services.schema_retrieval import retrieve_schema
    from wenshu.services.vector_search import resolve_search_db_names

    bvi.META_DSN = get_meta_mysql_dsn()
    bvi._load_dotenv()

    prepared = prepare_query(
        body.query,
        body.evidence or "",
        session_id=body.session_id,
        clarify_answers=body.clarify_answers,
        gate=not body.skip_clarify_gate,
    )
    if prepared.status == "need_clarify":
        return {
            "status": "need_clarify",
            "query": prepared.question,
            "evidence": prepared.evidence,
            "session_id": prepared.session_id,
            "semantic_graph": prepared.semantic_graph,
            "clarify_questions": prepared.clarify_questions,
            "clarify_rounds": prepared.clarify_rounds,
        }

    filter_dbs, filter_mode = resolve_search_db_names(
        db_names=body.db_names,
        all_databases=body.all_databases,
        default_raw_db=get_raw_database_name(),
    )

    try:
        client = create_qdrant_client()
        if not client.collection_exists(bvi.QDRANT_COLLECTION):
            raise HTTPException(400, "向量集合尚未创建，请先执行「构建向量索引」")
        embed_text = prepared.question
        if prepared.evidence:
            embed_text = f"{prepared.question}\n{prepared.evidence}"
        vector = bvi.embed([embed_text], is_query=True)[0]
        meta_engine = get_meta_mysql_engine()
        retrieval = retrieve_schema(
            client=client,
            collection_name=bvi.QDRANT_COLLECTION,
            meta_engine=meta_engine,
            query_vector=vector,
            question=prepared.question,
            evidence=prepared.evidence or "",
            db_names=filter_dbs,
            vector_limit=max(body.limit, 30),
            keyword_mode=body.keyword_mode,
            column_select=body.column_select,
        )
        preview = retrieval.preview_hits(limit=body.limit)

        def _dump_sel(cols):
            return [
                {
                    "score": round(getattr(c, "score", 0.0) or 0.0, 4),
                    "table": c.table,
                    "column": c.column,
                    "db": getattr(c, "db", ""),
                    "source": getattr(c, "source", ""),
                }
                for c in (cols or [])
            ]

        return {
            "status": "ok",
            "query": prepared.question,
            "evidence": prepared.evidence or "",
            "semantic_graph": prepared.semantic_graph,
            "clarify_rounds": prepared.clarify_rounds,
            "filter_mode": filter_mode,
            "filter_databases": filter_dbs or [],
            "retrieval_mode": retrieval.retrieval_style,
            "query_keywords": retrieval.query_keywords,
            "query_roles": retrieval.query_roles,
            "keyword_source": retrieval.keyword_source,
            "selected_tables": retrieval.selected_tables,
            "expanded_tables": retrieval.expanded_tables,
            "s1_columns": _dump_sel(retrieval.s1_columns),
            "s2_columns": _dump_sel(retrieval.s2_columns),
            "selection_meta": retrieval.selection_meta or {},
            "hits": [
                {
                    "score": h["score"],
                    "type": h["type"],
                    "table": h["table"],
                    "column": h["column"],
                    "db": h["db"],
                    "source": h.get("source"),
                }
                for h in preview
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"检索失败: {exc}") from exc
