"""连接检测与工作流状态。"""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.engine import Engine

from wenshu.services.connections import list_all_connections
from wenshu.services.metadata_staging import get_staging_stats, staging_tables_exist
from wenshu.services.stats import metadata_tables_exist


def check_connections() -> dict:
    items = list_all_connections(mask_secrets=True)
    raw_ok = any(i.get("role") == "raw" and i.get("source") == "platform" for i in items)
    meta_ok = any(i.get("role") == "meta" and i.get("source") == "platform" for i in items)
    return {
        "ok": raw_ok and meta_ok,
        "raw_configured": raw_ok,
        "meta_configured": meta_ok,
        "message": "连接已配置" if raw_ok and meta_ok else "请先在「连接配置」保存业务库与元数据库",
    }


def check_raw_db(engine: Engine) -> dict:
    try:
        with engine.connect() as conn:
            db = conn.execute(text("SELECT DATABASE()")).scalar()
            cnt = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
                      AND table_name NOT IN (
                        'table_meta','column_meta','table_relation','metric_def',
                        'synonym','kb_document','kb_chunk','vector_index_log',
                        'staging_table_meta','staging_column_meta'
                      )
                    """
                )
            ).scalar()
        return {"ok": True, "db_name": db, "table_count": int(cnt or 0), "message": "连接正常"}
    except Exception as exc:
        from wenshu.services.connections import _friendly_db_error

        return {"ok": False, "message": _friendly_db_error(exc)}


def check_meta_db(engine: Engine) -> dict:
    try:
        with engine.connect() as conn:
            db = conn.execute(text("SELECT DATABASE()")).scalar()
            ready = metadata_tables_exist(engine)
            table_count = 0
            column_count = 0
            if ready:
                table_count = int(conn.execute(text("SELECT COUNT(*) FROM table_meta WHERE is_enabled=1")).scalar() or 0)
                column_count = int(conn.execute(text("SELECT COUNT(*) FROM column_meta WHERE is_enabled=1")).scalar() or 0)
        return {
            "ok": True,
            "db_name": db,
            "metadata_ready": ready,
            "table_count": table_count,
            "column_count": column_count,
            "message": "连接正常",
        }
    except Exception as exc:
        from wenshu.services.connections import _friendly_db_error

        return {"ok": False, "message": _friendly_db_error(exc)}


def check_qdrant() -> dict:
    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from db_config import create_qdrant_client

        url = os.getenv("QDRANT_URL", "")
        collection = os.getenv("QDRANT_COLLECTION", "wenshu_knowledge")
        has_key = bool(os.getenv("QDRANT_API_KEY"))
        client = create_qdrant_client()
        exists = client.collection_exists(collection)
        points = 0
        if exists:
            info = client.get_collection(collection)
            points = info.points_count or 0
        return {
            "ok": True,
            "url": url,
            "collection": collection,
            "has_api_key": has_key,
            "collection_exists": exists,
            "points": points,
            "message": "连接正常" if exists else "已连接，collection 尚未创建",
        }
    except Exception as exc:
        from wenshu.services.connections import _friendly_db_error

        return {"ok": False, "message": _friendly_db_error(exc)}


def check_embedding() -> dict:
    from wenshu.services.model_config import get_embedding_config, resolve_embedding_provider

    provider = resolve_embedding_provider()
    cfg = get_embedding_config()
    has_api = cfg["has_api_key"]
    local_model = cfg["local_model"]
    dim = cfg["dim"]

    if provider == "openai":
        if not has_api:
            return {"ok": False, "provider": "openai", "message": "未配置 EMBEDDING_API_KEY"}
        return {
            "ok": True,
            "provider": "openai",
            "model": cfg["model"],
            "dim": dim,
            "message": f"线上 Embedding（{cfg['model']}，{dim} 维）",
        }

    try:
        import importlib.util

        os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
        os.environ.setdefault("USE_TF", "0")

        if importlib.util.find_spec("sentence_transformers") is None:
            return {
                "ok": False,
                "provider": "local",
                "message": "缺少 sentence-transformers，请运行: pip install sentence-transformers",
            }
        import torch  # noqa: F401
        import sentence_transformers  # noqa: F401

        runtime: dict = {}
        try:
            import sys
            from pathlib import Path

            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from build_vector_index import get_embed_runtime_info

            runtime = get_embed_runtime_info()
        except Exception:
            pass

        if torch.cuda.is_available():
            gpu_name = runtime.get("gpu_name") or torch.cuda.get_device_name(0)
            device_label = f"GPU · {gpu_name}"
        else:
            torch_build = getattr(torch.version, "cuda", None)
            if torch_build is None and "+cpu" in str(getattr(torch, "__version__", "")):
                device_label = "CPU（当前为 torch CPU 版，未使用显卡）"
            else:
                device_label = "CPU（未检测到可用 CUDA）"

        batch_size = runtime.get("batch_size")
        fp16 = runtime.get("fp16")
        extras = []
        if batch_size:
            extras.append(f"batch={batch_size}")
        if fp16 is not None and runtime.get("device") == "cuda":
            extras.append("fp16" if fp16 else "fp32")
        extra_label = f"，{', '.join(extras)}" if extras else ""

        return {
            "ok": True,
            "provider": "local",
            "model": local_model,
            "dim": dim,
            "device": device_label,
            "batch_size": batch_size,
            "fp16": fp16,
            "message": f"本地模型已就绪（{local_model}，{dim} 维，{device_label}{extra_label}）",
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "local",
            "message": f"Embedding 依赖异常: {exc}。请运行: pip install torch sentence-transformers scikit-learn numpy",
        }


def check_llm() -> dict:
    from wenshu.services.model_config import get_llm_config, llm_is_configured

    if not llm_is_configured():
        return {
            "ok": False,
            "provider": "none",
            "message": "未配置 LLM（本地 Ollama 或线上 API）",
        }
    cfg = get_llm_config()
    if cfg["provider"] == "openai":
        return {
            "ok": True,
            "provider": "openai",
            "model": cfg["model"],
            "message": f"线上 LLM（{cfg['model']}）",
        }
    return {
        "ok": True,
        "provider": "ollama",
        "model": cfg["ollama_model"],
        "url": cfg["ollama_url"],
        "message": f"本地 Ollama（{cfg['ollama_model']}）",
    }


def _empty_staging_stats() -> dict:
    return {
        "staging_table_count": 0,
        "staging_column_count": 0,
        "missing_table_comments": 0,
        "missing_column_comments": 0,
        "review_complete": False,
    }


def _metadata_cycle_state(staging: dict, meta: dict) -> dict:
    """暂存区审核完成，或已同步到 L1 且暂存已清空，均视为维护周期就绪。"""
    has_l1 = (meta.get("table_count") or 0) > 0
    has_staging = (staging.get("staging_table_count") or 0) > 0
    review_complete = bool(staging.get("review_complete"))
    synced_idle = has_l1 and not has_staging
    return {
        "has_l1": has_l1,
        "has_staging": has_staging,
        "review_done": review_complete or synced_idle,
        "sync_done": has_l1 and (review_complete or not has_staging),
    }


def get_workflow_status(raw_engine: Engine, meta_engine: Engine) -> dict:
    conn = check_connections()
    raw = check_raw_db(raw_engine)
    meta = check_meta_db(meta_engine)
    staging = _empty_staging_stats()
    if meta.get("ok"):
        try:
            staging = get_staging_stats(meta_engine)
        except Exception as exc:
            from wenshu.services.connections import _friendly_db_error

            staging = {**_empty_staging_stats(), "error": _friendly_db_error(exc)}
    qdrant = check_qdrant()
    embedding = check_embedding()
    llm = check_llm()
    cycle = _metadata_cycle_state(staging, meta)

    steps = [
        {
            "id": "connections",
            "title": "确认数据库连接",
            "desc": "配置并试连接业务库、元数据库",
            "done": conn.get("ok", False) and raw.get("ok", False) and meta.get("ok", False),
            "ready": True,
        },
        {
            "id": "init",
            "title": "初始化元数据表",
            "desc": "在元数据库创建 table_meta、staging 等系统表（仅首次需要）",
            "done": meta.get("metadata_ready", False),
            "ready": conn.get("ok", False) and raw.get("ok", False),
        },
        {
            "id": "scan",
            "title": "扫描原始表",
            "desc": "从业务库读取表/字段结构写入暂存区（只读，不改源表）。每次扫描会先清空整个暂存区再写入；未同步的编辑会丢失。暂存区已有数据时本步可跳过",
            "done": cycle["has_staging"],
            "ready": meta.get("metadata_ready", False),
        },
        {
            "id": "review",
            "title": "确认元数据",
            "desc": "在「元数据编辑」核对表/字段说明，可用 AI 补全缺失注释",
            "done": cycle["review_done"],
            "ready": staging.get("staging_table_count", 0) > 0,
        },
        {
            "id": "sync",
            "title": "同步到元数据库",
            "desc": "将暂存区写入元数据库（Upsert）；同步成功后对应暂存记录会清空，需重新扫描才能再编辑",
            "done": cycle["sync_done"],
            "ready": staging.get("review_complete", False),
        },
        {
            "id": "index",
            "title": "构建向量索引",
            "desc": "在「向量库同步」加载 Embedding 模型，将元数据向量化写入 Qdrant",
            "done": (
                cycle["sync_done"]
                and qdrant.get("ok")
                and (qdrant.get("points") or 0) > 0
            ),
            "ready": cycle["sync_done"] and embedding.get("ok", False),
        },
        {
            "id": "test",
            "title": "召回验证",
            "desc": "用自然语言测试能否召回相关表/字段",
            "done": cycle["sync_done"] and qdrant.get("points", 0) > 0,
            "ready": qdrant.get("points", 0) > 0
            and meta.get("table_count", 0) > 0,
        },
    ]

    current = 0
    for i, s in enumerate(steps):
        if not s["done"]:
            current = i
            break
    else:
        current = len(steps) - 1

    return {
        "steps": steps,
        "current_step": current,
        "all_done": all(s["done"] for s in steps[:6]),
        "staging": staging,
        "health": {
            "connections": conn,
            "raw_db": raw,
            "meta_db": meta,
            "qdrant": qdrant,
            "embedding": embedding,
            "llm": llm,
        },
    }
