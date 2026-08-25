#!/usr/bin/env python3
"""
从 L1/L2 元数据库读取 enabled 对象，生成 embedding，写入向量库。

用法:
  python scripts/build_vector_index.py
  python scripts/build_vector_index.py --full
  python scripts/build_vector_index.py --types column,metric

依赖:
  pip install pymysql sqlalchemy qdrant-client openai
  或替换 embed 函数为本地 BGE 模型
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")
from datetime import datetime
from typing import Callable

from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

META_DSN = os.getenv("META_MYSQL_DSN", "mysql+pymysql://root:password@127.0.0.1:3306/wenshu_meta")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "wenshu_knowledge")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "auto")  # auto | local | openai
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
BUILD_VERSION = datetime.now().strftime("%Y%m%d%H%M")

_local_st_model = None
_local_embed_device: str | None = None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_embed_batch_size(device: str | None = None) -> int:
    configured = os.getenv("LOCAL_EMBEDDING_BATCH_SIZE")
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            pass
    dev = device or _resolve_embed_device()
    return 48 if dev == "cuda" else 16


def _resolve_index_batch_size(device: str | None = None) -> int:
    return _resolve_embed_batch_size(device)


def _use_fp16(device: str) -> bool:
    if device != "cuda":
        return False
    if os.getenv("LOCAL_EMBEDDING_FP16") is not None:
        return _env_flag("LOCAL_EMBEDDING_FP16", False)
    return True


def get_embed_runtime_info() -> dict:
    """供健康检查/日志展示当前 embedding 运行设备。"""
    device = _resolve_embed_device()
    info = {
        "device": device,
        "batch_size": _resolve_embed_batch_size(device),
        "fp16": _use_fp16(device),
    }
    if device == "cuda":
        try:
            import torch

            info["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["gpu_mem_gb"] = round(props.total_memory / (1024**3), 1)
        except Exception:
            pass
    return info


def _load_dotenv() -> None:
    from pathlib import Path

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
        except ImportError:
            pass


def _resolve_provider() -> str:
    try:
        from wenshu.services.model_config import resolve_embedding_provider

        return resolve_embedding_provider()
    except Exception:
        provider = (os.getenv("EMBEDDING_PROVIDER") or EMBEDDING_PROVIDER or "auto").lower()
        if provider != "auto":
            return provider
        if os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"):
            return "openai"
        return "local"


def get_embedding_dim() -> int:
    """返回当前 embedding 向量维度（local 模式可从模型自动检测）。"""
    if _resolve_provider() == "local":
        model = _get_local_model()
        return model.get_sentence_embedding_dimension()
    return int(os.getenv("EMBEDDING_DIM", str(EMBEDDING_DIM)))


def _resolve_embed_device() -> str:
    configured = (os.getenv("LOCAL_EMBEDDING_DEVICE") or "auto").lower()
    if configured != "auto":
        return configured
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _get_local_model():
    global _local_st_model, _local_embed_device
    if _local_st_model is None:
        import torch
        from sentence_transformers import SentenceTransformer

        model_name = os.getenv("LOCAL_EMBEDDING_MODEL", LOCAL_EMBEDDING_MODEL)
        device = _resolve_embed_device()
        use_fp16 = _use_fp16(device)
        model_kwargs = {}
        if use_fp16:
            model_kwargs["torch_dtype"] = torch.float16

        print(
            f"[embed] loading local model: {model_name} "
            f"device={device} fp16={use_fp16} batch={_resolve_embed_batch_size(device)}"
        )
        # Qwen3 Embedding 官方建议 tokenizer padding_side=left
        _local_st_model = SentenceTransformer(
            model_name,
            device=device,
            model_kwargs=model_kwargs or None,
            tokenizer_kwargs={"padding_side": "left"},
        )
        _local_embed_device = device
    return _local_st_model


def embed(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    _load_dotenv()
    provider = _resolve_provider()
    if provider == "local":
        model = _get_local_model()
        device = _local_embed_device or _resolve_embed_device()
        batch_size = _resolve_embed_batch_size(device)
        encode_kwargs: dict = {
            "normalize_embeddings": True,
            "show_progress_bar": False,
            "batch_size": batch_size,
            "convert_to_numpy": True,
        }
        # Qwen3-Embedding 等 instruct 模型：query 侧使用内置 query prompt
        if is_query and getattr(model, "prompts", None) and "query" in model.prompts:
            encode_kwargs["prompt_name"] = "query"
        vectors = model.encode(texts, **encode_kwargs)
        return [v.tolist() for v in vectors]

    api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "请设置 EMBEDDING_API_KEY，或改用免费本地方案：EMBEDDING_PROVIDER=local"
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=os.getenv("EMBEDDING_API_BASE"))
    model = os.getenv("EMBEDDING_MODEL", EMBEDDING_MODEL)
    dim = get_embedding_dim()
    try:
        api_batch = int(os.getenv("EMBEDDING_API_BATCH_SIZE", "10"))
    except ValueError:
        api_batch = 10
    api_batch = max(1, min(api_batch, 10))

    out: list[list[float]] = []
    for i in range(0, len(texts), api_batch):
        chunk = texts[i : i + api_batch]
        kwargs: dict = {"model": model, "input": chunk}
        if dim and model.startswith(("text-embedding-v", "qwen")):
            kwargs["dimensions"] = dim
        resp = client.embeddings.create(**kwargs)
        out.extend(item.embedding for item in resp.data)
    return out


def text_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# embed 文本构造
# ---------------------------------------------------------------------------

def build_table_text(row) -> str:
    return f"""库：{row.db_name}
表：{row.db_name}.{row.table_name}
中文名：{row.cn_name or ''}
说明：{row.description or row.hive_comment or ''}
主题域：{row.domain or ''}
示例问题：{row.sample_questions or ''}"""


def build_column_text(row) -> str:
    syns = ""
    if row.synonyms:
        try:
            syns = ", ".join(json.loads(row.synonyms))
        except Exception:
            syns = str(row.synonyms)
    table_desc = ""
    if hasattr(row, "table_description"):
        table_desc = (row.table_description or "").strip()
    if not table_desc and hasattr(row, "t_description"):
        table_desc = (row.t_description or "").strip()
    lines = [
        f"库：{row.db_name}",
        f"表：{row.db_name}.{row.table_name}（{row.cn_name or ''}）",
    ]
    if table_desc:
        lines.append(f"表说明：{table_desc}")
    lines.extend(
        [
            f"字段：{row.column_name}",
            f"类型：{row.data_type}",
            f"含义：{row.description or row.hive_comment or ''}",
            f"同义词：{syns}",
            f"主题域：{row.domain or ''}",
        ]
    )
    return "\n".join(lines)


def build_metric_text(row) -> str:
    aliases = ", ".join(json.loads(row.aliases)) if row.aliases else ""
    tables = ", ".join(json.loads(row.related_tables)) if row.related_tables else ""
    return f"""指标：{row.metric_name}
别名：{aliases}
口径：{row.definition}
涉及表：{tables}"""


def build_join_text(row) -> str:
    return f"""JOIN：{row.left_db}.{row.left_table}.{row.left_column} = {row.right_db}.{row.right_table}.{row.right_column}
类型：{row.join_type}
说明：{row.description or ''}"""


def build_doc_text(row) -> str:
    return f"""文档：{row.title}
主题域：{row.domain or ''}
内容：{row.content}"""


# ---------------------------------------------------------------------------
# 从 MySQL 加载对象
# ---------------------------------------------------------------------------

def load_objects(
    conn,
    types: set[str],
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    relation_ids: list[str] | None = None,
    metric_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
):
    items = []

    if column_ids:
        types = types & {"column"}
        if "column" in types:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids)))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids)}
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.column_id, c.column_name, c.data_type, c.description, c.hive_comment,
                           c.synonyms, t.table_id, t.db_name, t.table_name, t.cn_name, t.domain,
                           t.description AS table_description
                    FROM column_meta c
                    JOIN table_meta t ON c.table_id = t.table_id
                    WHERE c.is_enabled = 1 AND t.is_enabled = 1
                      AND c.column_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            for r in rows:
                items.append({
                    "object_type": "column",
                    "object_id": r.column_id,
                    "vector_id": f"column:{r.column_id}",
                    "embed_text": build_column_text(r),
                    "payload": {
                        "object_type": "column",
                        "object_id": r.column_id,
                        "db": r.db_name,
                        "table": r.table_name,
                        "column": r.column_name,
                        "domain": r.domain,
                    },
                })
        return items

    table_filter = ""
    col_table_filter = ""
    params: dict = {}
    if table_ids:
        placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
        params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
        table_filter = f"AND table_id IN ({placeholders})"
        col_table_filter = f"AND c.table_id IN ({placeholders})"

    if "table" in types:
        rows = conn.execute(
            text(
                f"""
                SELECT table_id, db_name, table_name, cn_name, description, hive_comment,
                       domain, sample_questions
                FROM table_meta WHERE is_enabled = 1 {table_filter}
                """
            ),
            params,
        ).fetchall()
        for r in rows:
            items.append({
                "object_type": "table",
                "object_id": r.table_id,
                "vector_id": f"table:{r.table_id}",
                "embed_text": build_table_text(r),
                "payload": {
                    "object_type": "table",
                    "object_id": r.table_id,
                    "db": r.db_name,
                    "table": r.table_name,
                    "domain": r.domain,
                },
            })

    if "column" in types:
        rows = conn.execute(
            text(
                f"""
                SELECT c.column_id, c.column_name, c.data_type, c.description, c.hive_comment,
                       c.synonyms, t.table_id, t.db_name, t.table_name, t.cn_name, t.domain,
                       t.description AS table_description
                FROM column_meta c
                JOIN table_meta t ON c.table_id = t.table_id
                WHERE c.is_enabled = 1 AND t.is_enabled = 1
                {col_table_filter}
                """
            ),
            params,
        ).fetchall()
        for r in rows:
            items.append({
                "object_type": "column",
                "object_id": r.column_id,
                "vector_id": f"column:{r.column_id}",
                "embed_text": build_column_text(r),
                "payload": {
                    "object_type": "column",
                    "object_id": r.column_id,
                    "db": r.db_name,
                    "table": r.table_name,
                    "column": r.column_name,
                    "domain": r.domain,
                },
            })

    if table_ids or column_ids:
        return items

    if relation_ids:
        types = types & {"join"}
        if "join" in types:
            placeholders = ", ".join(f":r{i}" for i in range(len(relation_ids)))
            params = {f"r{i}": rid for i, rid in enumerate(relation_ids)}
            rows = conn.execute(
                text(
                    f"""
                    SELECT relation_id, left_db, left_table, left_column,
                           right_db, right_table, right_column, join_type, description
                    FROM table_relation WHERE is_enabled = 1
                      AND relation_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            for r in rows:
                items.append({
                    "object_type": "join",
                    "object_id": r.relation_id,
                    "vector_id": f"join:{r.relation_id}",
                    "embed_text": build_join_text(r),
                    "payload": {
                        "object_type": "join",
                        "object_id": r.relation_id,
                        "left_db": r.left_db,
                        "right_db": r.right_db,
                        "left": f"{r.left_db}.{r.left_table}.{r.left_column}",
                        "right": f"{r.right_db}.{r.right_table}.{r.right_column}",
                    },
                })
        return items

    if metric_ids:
        types = types & {"metric"}
        if "metric" in types:
            placeholders = ", ".join(f":m{i}" for i in range(len(metric_ids)))
            params = {f"m{i}": mid for i, mid in enumerate(metric_ids)}
            rows = conn.execute(
                text(
                    f"""
                    SELECT metric_id, metric_name, aliases, definition, related_tables, domain
                    FROM metric_def WHERE is_enabled = 1
                      AND metric_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            for r in rows:
                items.append({
                    "object_type": "metric",
                    "object_id": r.metric_id,
                    "vector_id": f"metric:{r.metric_id}",
                    "embed_text": build_metric_text(r),
                    "payload": {
                        "object_type": "metric",
                        "object_id": r.metric_id,
                        "metric": r.metric_name,
                        "domain": r.domain,
                    },
                })
        return items

    if chunk_ids:
        types = types & {"doc_chunk"}
        if "doc_chunk" in types:
            placeholders = ", ".join(f":k{i}" for i in range(len(chunk_ids)))
            params = {f"k{i}": cid for i, cid in enumerate(chunk_ids)}
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.chunk_id, c.content, d.title, d.domain
                    FROM kb_chunk c
                    JOIN kb_document d ON c.doc_id = d.doc_id
                    WHERE c.is_enabled = 1 AND d.is_enabled = 1
                      AND c.chunk_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            for r in rows:
                items.append({
                    "object_type": "doc_chunk",
                    "object_id": r.chunk_id,
                    "vector_id": f"doc:{r.chunk_id}",
                    "embed_text": build_doc_text(r),
                    "payload": {
                        "object_type": "doc_chunk",
                        "object_id": r.chunk_id,
                        "domain": r.domain,
                    },
                })
        return items

    if "metric" in types:
        rows = conn.execute(text("""
            SELECT metric_id, metric_name, aliases, definition, related_tables, domain
            FROM metric_def WHERE is_enabled = 1
        """)).fetchall()
        for r in rows:
            items.append({
                "object_type": "metric",
                "object_id": r.metric_id,
                "vector_id": f"metric:{r.metric_id}",
                "embed_text": build_metric_text(r),
                "payload": {
                    "object_type": "metric",
                    "object_id": r.metric_id,
                    "metric": r.metric_name,
                    "domain": r.domain,
                },
            })

    if "join" in types:
        rows = conn.execute(text("""
            SELECT relation_id, left_db, left_table, left_column,
                   right_db, right_table, right_column, join_type, description
            FROM table_relation WHERE is_enabled = 1
        """)).fetchall()
        for r in rows:
            items.append({
                "object_type": "join",
                "object_id": r.relation_id,
                "vector_id": f"join:{r.relation_id}",
                "embed_text": build_join_text(r),
                "payload": {
                    "object_type": "join",
                    "object_id": r.relation_id,
                    "left_db": r.left_db,
                    "right_db": r.right_db,
                    "left": f"{r.left_db}.{r.left_table}.{r.left_column}",
                    "right": f"{r.right_db}.{r.right_table}.{r.right_column}",
                },
            })

    if "doc_chunk" in types:
        rows = conn.execute(text("""
            SELECT c.chunk_id, c.content, d.title, d.domain
            FROM kb_chunk c
            JOIN kb_document d ON c.doc_id = d.doc_id
            WHERE c.is_enabled = 1 AND d.is_enabled = 1
        """)).fetchall()
        for r in rows:
            items.append({
                "object_type": "doc_chunk",
                "object_id": r.chunk_id,
                "vector_id": f"doc:{r.chunk_id}",
                "embed_text": build_doc_text(r),
                "payload": {
                    "object_type": "doc_chunk",
                    "object_id": r.chunk_id,
                    "domain": r.domain,
                },
            })

    return items


# ---------------------------------------------------------------------------
# 向量库 upsert
# ---------------------------------------------------------------------------

def ensure_collection(
    client,
    *,
    recreate_on_dim_mismatch: bool = False,
    on_progress: Callable[..., None] | None = None,
):
    from qdrant_client.http.models import Distance, VectorParams

    if on_progress and _resolve_provider() == "local":
        on_progress(pct=9, message="正在加载 Embedding 模型（首次约 1–3 分钟，请耐心等待）…")

    dim = get_embedding_dim()
    if client.collection_exists(QDRANT_COLLECTION):
        info = client.get_collection(QDRANT_COLLECTION)
        existing = getattr(getattr(info, "config", None), "params", None)
        vectors = getattr(existing, "vectors", None) if existing else None
        existing_dim = getattr(vectors, "size", None) if vectors is not None else None
        if existing_dim is not None and int(existing_dim) != int(dim):
            if not recreate_on_dim_mismatch:
                raise RuntimeError(
                    f"Qdrant 集合维度为 {existing_dim}，当前 embedding 为 {dim}。"
                    f"请使用 --full 全量重建，或手动删除集合 {QDRANT_COLLECTION} 后重试。"
                )
            print(f"[index] dim mismatch {existing_dim}->{dim}, recreating collection")
            client.delete_collection(QDRANT_COLLECTION)
        else:
            return

    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    print(f"[index] created collection dim={dim}")


def _load_hash_cache(engine, items: list[dict]) -> dict[tuple[str, str], str]:
    """一次性读取 vector_index_log，避免逐条查库。"""
    if not items:
        return {}
    cache: dict[tuple[str, str], str] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT object_type, object_id, embed_text_hash FROM vector_index_log")
        ).fetchall()
    for row in rows:
        cache[(row.object_type, row.object_id)] = row.embed_text_hash
    return cache


def upsert_vectors(
    client,
    engine,
    items: list[dict],
    full: bool = False,
    on_progress: Callable[..., None] | None = None,
) -> dict:
    from qdrant_client.http.models import PointStruct

    ensure_collection(client, recreate_on_dim_mismatch=full, on_progress=on_progress)
    device = _resolve_embed_device()
    batch_size = _resolve_index_batch_size(device)
    upserted = 0
    skipped = 0
    total = len(items)
    hash_cache = _load_hash_cache(engine, items) if not full else {}

    if on_progress:
        on_progress(pct=10, message=f"开始向量化 {total} 个对象", done=0, total=total)

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        to_embed: list[dict] = []

        for item in batch:
            h = text_hash(item["embed_text"])
            if not full:
                cached = hash_cache.get((item["object_type"], item["object_id"]))
                if cached == h:
                    skipped += 1
                    continue
            item["_hash"] = h
            to_embed.append(item)

        processed = min(i + batch_size, total)
        if on_progress:
            pct = 10 + int(84 * processed / max(total, 1))
            on_progress(
                pct=pct,
                message=f"向量化 {processed}/{total}（本批待写入 {len(to_embed)}）",
                done=processed,
                total=total,
            )

        if not to_embed:
            continue

        vectors = embed([x["embed_text"] for x in to_embed])
        points = []
        for item, vec in zip(to_embed, vectors):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, item["vector_id"]))
            points.append(PointStruct(id=point_id, vector=vec, payload=item["payload"]))

        client.upsert(collection_name=QDRANT_COLLECTION, points=points)

        with engine.begin() as conn:
            log_rows = [
                {
                    "t": item["object_type"],
                    "id": item["object_id"],
                    "h": item["_hash"],
                    "vid": item["vector_id"],
                    "ver": BUILD_VERSION,
                }
                for item in to_embed
            ]
            if log_rows:
                conn.execute(
                    text("""
                        INSERT INTO vector_index_log
                            (object_type, object_id, embed_text_hash, vector_id, build_version)
                        VALUES (:t, :id, :h, :vid, :ver)
                        ON DUPLICATE KEY UPDATE
                            embed_text_hash = VALUES(embed_text_hash),
                            vector_id = VALUES(vector_id),
                            build_version = VALUES(build_version),
                            indexed_at = CURRENT_TIMESTAMP
                    """),
                    log_rows,
                )
        upserted += len(to_embed)
        print(f"[index] batch upserted={len(to_embed)}")

    print(f"[index] total upserted={upserted}, skipped={skipped}")
    return {"upserted": upserted, "skipped": skipped, "total": len(items)}


def delete_vectors_for_objects(
    client,
    engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    relation_ids: list[str] | None = None,
) -> dict:
    """从 Qdrant 与 vector_index_log 删除指定表/字段/JOIN 向量。"""
    from qdrant_client.http.models import PointIdsList

    table_ids = list(table_ids or [])
    column_ids = list(column_ids or [])
    relation_ids = list(relation_ids or [])
    point_ids: list[str] = []
    for tid in table_ids:
        point_ids.append(str(uuid.uuid5(uuid.NAMESPACE_URL, f"table:{tid}")))
    for cid in column_ids:
        point_ids.append(str(uuid.uuid5(uuid.NAMESPACE_URL, f"column:{cid}")))
    for rid in relation_ids:
        point_ids.append(str(uuid.uuid5(uuid.NAMESPACE_URL, f"join:{rid}")))

    deleted = 0
    if point_ids and client.collection_exists(QDRANT_COLLECTION):
        client.delete(collection_name=QDRANT_COLLECTION, points_selector=PointIdsList(points=point_ids))
        deleted = len(point_ids)

    with engine.begin() as conn:
        for tid in table_ids:
            conn.execute(
                text("DELETE FROM vector_index_log WHERE object_type='table' AND object_id=:id"),
                {"id": tid},
            )
        for cid in column_ids:
            conn.execute(
                text("DELETE FROM vector_index_log WHERE object_type='column' AND object_id=:id"),
                {"id": cid},
            )
        for rid in relation_ids:
            conn.execute(
                text("DELETE FROM vector_index_log WHERE object_type='join' AND object_id=:id"),
                {"id": rid},
            )

    print(
        f"[index] deleted vectors={deleted} "
        f"tables={len(table_ids)} columns={len(column_ids)} joins={len(relation_ids)}"
    )
    return {
        "vectors_deleted": deleted,
        "purged_table_ids": table_ids,
        "purged_column_ids": column_ids,
        "purged_relation_ids": relation_ids,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Build vector index from L1/L2 metadata")
    parser.add_argument("--full", action="store_true", help="忽略 hash，全量 re-embed")
    parser.add_argument(
        "--types",
        default="table,column,metric,join,doc_chunk",
        help="逗号分隔 object_type",
    )
    args = parser.parse_args()

    types = set(t.strip() for t in args.types.split(",") if t.strip())
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from db_config import dispose_mysql_engines, get_meta_mysql_dsn, get_meta_mysql_engine

    global META_DSN
    META_DSN = get_meta_mysql_dsn()
    engine = get_meta_mysql_engine()

    print(f"[index] embedding provider={_resolve_provider()}, dim={get_embedding_dim()}")
    if _resolve_provider() == "local":
        print(f"[index] embed runtime={get_embed_runtime_info()}")
    print(f"[index] loading types={types}")
    try:
        with engine.connect() as conn:
            items = load_objects(conn, types)
        print(f"[index] objects={len(items)}")

        from db_config import create_qdrant_client

        client = create_qdrant_client()
        upsert_vectors(client, engine, items, full=args.full)
    finally:
        dispose_mysql_engines()
    print("[index] done")


if __name__ == "__main__":
    main()
