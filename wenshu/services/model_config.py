"""Embedding / LLM 本地与线上（OpenAI 兼容）配置。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = ROOT / ".wenshu"
STORE_PATH = STORE_DIR / "model_settings.json"
MASK = "********"
STORE_VERSION = 1

_SECRET_KEYS = frozenset({"api_key", "embedding_api_key", "llm_api_key"})


def _ensure_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _mask_secret(value: str | None) -> str:
    if value:
        return MASK
    return ""


def _resolve_provider(explicit: str | None, *, has_api: bool, has_local: bool) -> str:
    p = (explicit or "auto").strip().lower()
    if p not in ("auto", "local", "openai", "ollama"):
        p = "auto"
    if p == "auto":
        if has_api:
            return "openai"
        if has_local:
            return "local" if not has_api else "openai"
        return "local"
    if p == "ollama":
        return "ollama"
    if p in ("openai", "local"):
        return p
    return "local"


def load_store() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"version": STORE_VERSION}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": STORE_VERSION}
    if not isinstance(data, dict):
        return {"version": STORE_VERSION}
    data.setdefault("version", STORE_VERSION)
    return data


def save_store(store: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": STORE_VERSION, **{k: v for k, v in store.items() if k != "version"}}
    payload["version"] = STORE_VERSION
    STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _store_section(key: str) -> dict[str, Any]:
    store = load_store()
    section = store.get(key)
    return dict(section) if isinstance(section, dict) else {}


def apply_model_settings_to_environ() -> None:
    """将平台保存的模型配置写入进程环境（优先于 .env 中的同名字段）。"""
    _ensure_dotenv()
    store = load_store()
    emb = store.get("embedding") if isinstance(store.get("embedding"), dict) else {}
    llm = store.get("llm") if isinstance(store.get("llm"), dict) else {}

    if emb.get("provider"):
        os.environ["EMBEDDING_PROVIDER"] = str(emb["provider"])
    if emb.get("local_model"):
        os.environ["LOCAL_EMBEDDING_MODEL"] = str(emb["local_model"])
    if emb.get("local_device"):
        os.environ["LOCAL_EMBEDDING_DEVICE"] = str(emb["local_device"])
    if emb.get("model"):
        os.environ["EMBEDDING_MODEL"] = str(emb["model"])
    if emb.get("dim") is not None and str(emb["dim"]).strip():
        os.environ["EMBEDDING_DIM"] = str(emb["dim"])
    if emb.get("api_base"):
        os.environ["EMBEDDING_API_BASE"] = str(emb["api_base"])
    if emb.get("api_key") and emb["api_key"] != MASK:
        os.environ["EMBEDDING_API_KEY"] = str(emb["api_key"])

    if llm.get("provider"):
        os.environ["LLM_PROVIDER"] = str(llm["provider"])
    if llm.get("ollama_url"):
        os.environ["LOCAL_LLM_URL"] = str(llm["ollama_url"])
    if llm.get("ollama_model"):
        os.environ["LOCAL_LLM_MODEL"] = str(llm["ollama_model"])
    if llm.get("model"):
        os.environ["LLM_MODEL"] = str(llm["model"])
    if llm.get("metadata_model"):
        os.environ["METADATA_LLM_MODEL"] = str(llm["metadata_model"])
    if llm.get("api_base"):
        os.environ["LLM_API_BASE"] = str(llm["api_base"])
    if llm.get("api_key") and llm["api_key"] != MASK:
        os.environ["LLM_API_KEY"] = str(llm["api_key"])
        os.environ.setdefault("OPENAI_API_KEY", str(llm["api_key"]))


def resolve_embedding_provider() -> str:
    _ensure_dotenv()
    explicit = os.getenv("EMBEDDING_PROVIDER")
    has_api = bool(os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"))
    has_local = bool(os.getenv("LOCAL_EMBEDDING_MODEL"))
    p = _resolve_provider(explicit, has_api=has_api, has_local=has_local)
    if p == "openai":
        return "openai"
    return "local"


def resolve_llm_provider() -> str:
    _ensure_dotenv()
    explicit = os.getenv("LLM_PROVIDER")
    has_api = bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))
    has_ollama = bool(
        (os.getenv("LOCAL_LLM_URL") or os.getenv("METADATA_LLM_URL") or "").strip()
    )
    p = (explicit or "auto").strip().lower()
    if p == "auto":
        if has_api:
            return "openai"
        if has_ollama:
            return "ollama"
        return "ollama"
    if p in ("openai", "ollama"):
        return p
    if p == "local":
        return "ollama"
    return "ollama"


def get_embedding_config() -> dict[str, Any]:
    _ensure_dotenv()
    provider = resolve_embedding_provider()
    dim_raw = os.getenv("EMBEDDING_DIM", "1024" if provider == "local" else "1536")
    try:
        dim = int(dim_raw)
    except ValueError:
        dim = 1024
    return {
        "provider": provider,
        "local_model": os.getenv("LOCAL_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
        "local_device": os.getenv("LOCAL_EMBEDDING_DEVICE", "auto"),
        "model": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        "api_base": os.getenv("EMBEDDING_API_BASE", ""),
        "dim": dim,
        "has_api_key": bool(os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")),
    }


def get_llm_config(*, metadata: bool = False) -> dict[str, Any]:
    _ensure_dotenv()
    provider = resolve_llm_provider()
    if metadata:
        model = (
            os.getenv("METADATA_LLM_MODEL")
            or os.getenv("LLM_MODEL")
            or os.getenv("LOCAL_LLM_MODEL")
            or "qwen-plus"
        )
    else:
        model = (
            os.getenv("LLM_MODEL")
            or os.getenv("LOCAL_LLM_MODEL")
            or os.getenv("METADATA_LLM_MODEL")
            or "qwen3:4b"
        )
    return {
        "provider": provider,
        "ollama_url": (
            os.getenv("LOCAL_LLM_URL") or os.getenv("METADATA_LLM_URL") or ""
        ).rstrip("/"),
        "ollama_model": os.getenv("LOCAL_LLM_MODEL") or os.getenv("METADATA_LLM_MODEL") or "qwen3:4b",
        "model": model,
        "api_base": os.getenv("LLM_API_BASE", ""),
        "has_api_key": bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")),
    }


def llm_is_configured() -> bool:
    cfg = get_llm_config()
    if cfg["provider"] == "openai":
        return cfg["has_api_key"] and bool(cfg["model"])
    return bool(cfg["ollama_url"])


def embedding_is_configured() -> bool:
    cfg = get_embedding_config()
    if cfg["provider"] == "openai":
        return cfg["has_api_key"] and bool(cfg["model"])
    return bool(cfg["local_model"])


def get_model_settings(*, reveal_secrets: bool = False) -> dict[str, Any]:
    _ensure_dotenv()
    store_emb = _store_section("embedding")
    store_llm = _store_section("llm")
    emb_cfg = get_embedding_config()
    llm_cfg = get_llm_config()

    emb_api_key = store_emb.get("api_key") or os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    llm_api_key = store_llm.get("api_key") or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""

    embedding = {
        "provider": store_emb.get("provider") or os.getenv("EMBEDDING_PROVIDER") or "auto",
        "local_model": store_emb.get("local_model") or emb_cfg["local_model"],
        "local_device": store_emb.get("local_device") or emb_cfg["local_device"],
        "model": store_emb.get("model") or emb_cfg["model"],
        "api_base": store_emb.get("api_base") or emb_cfg["api_base"],
        "dim": int(store_emb.get("dim") or emb_cfg["dim"]),
        "api_key": emb_api_key if reveal_secrets else _mask_secret(emb_api_key),
        "api_key_set": bool(emb_api_key),
        "resolved_provider": emb_cfg["provider"],
        "ok": embedding_is_configured(),
    }
    llm = {
        "provider": store_llm.get("provider") or os.getenv("LLM_PROVIDER") or "auto",
        "ollama_url": store_llm.get("ollama_url") or llm_cfg["ollama_url"],
        "ollama_model": store_llm.get("ollama_model") or llm_cfg["ollama_model"],
        "model": store_llm.get("model") or os.getenv("LLM_MODEL") or "",
        "metadata_model": store_llm.get("metadata_model") or os.getenv("METADATA_LLM_MODEL") or "",
        "api_base": store_llm.get("api_base") or llm_cfg["api_base"],
        "api_key": llm_api_key if reveal_secrets else _mask_secret(llm_api_key),
        "api_key_set": bool(llm_api_key),
        "resolved_provider": llm_cfg["provider"],
        "ok": llm_is_configured(),
    }
    return {"embedding": embedding, "llm": llm}


def save_model_settings(payload: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    prev_emb = store.get("embedding") if isinstance(store.get("embedding"), dict) else {}
    prev_llm = store.get("llm") if isinstance(store.get("llm"), dict) else {}

    emb_in = payload.get("embedding") if isinstance(payload.get("embedding"), dict) else {}
    llm_in = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}

    emb_out: dict[str, Any] = {}
    for key in ("provider", "local_model", "local_device", "model", "api_base", "dim"):
        if key in emb_in and emb_in[key] is not None and str(emb_in[key]).strip() != "":
            emb_out[key] = emb_in[key]
    if emb_in.get("api_key") and emb_in["api_key"] != MASK:
        emb_out["api_key"] = emb_in["api_key"]
    elif prev_emb.get("api_key"):
        emb_out["api_key"] = prev_emb["api_key"]

    llm_out: dict[str, Any] = {}
    for key in (
        "provider",
        "ollama_url",
        "ollama_model",
        "model",
        "metadata_model",
        "api_base",
    ):
        if key in llm_in and llm_in[key] is not None:
            llm_out[key] = llm_in[key]
    if llm_in.get("api_key") and llm_in["api_key"] != MASK:
        llm_out["api_key"] = llm_in["api_key"]
    elif prev_llm.get("api_key"):
        llm_out["api_key"] = prev_llm["api_key"]

    store["embedding"] = emb_out
    store["llm"] = llm_out
    save_store(store)
    apply_model_settings_to_environ()
    return get_model_settings(reveal_secrets=False)


def test_llm_connection(timeout: int = 30) -> dict[str, Any]:
    from wenshu.services.comment_llm import _call_llm_json

    data = _call_llm_json('只输出 JSON：{"ok":true,"message":"pong"}', timeout=timeout)
    if isinstance(data, dict) and data.get("ok"):
        cfg = get_llm_config()
        return {
            "ok": True,
            "provider": cfg["provider"],
            "model": cfg["model"],
            "message": "LLM 连通正常",
        }
    return {
        "ok": False,
        "provider": resolve_llm_provider(),
        "message": "LLM 未返回有效 JSON，请检查模型、Key 或 Base URL",
    }


def test_embedding_connection() -> dict[str, Any]:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from build_vector_index import embed

    vectors = embed(["问数平台 embedding 连通测试"], is_query=True)
    if not vectors or not vectors[0]:
        return {"ok": False, "message": "Embedding 返回空向量"}
    cfg = get_embedding_config()
    return {
        "ok": True,
        "provider": cfg["provider"],
        "model": cfg["local_model"] if cfg["provider"] == "local" else cfg["model"],
        "dim": len(vectors[0]),
        "message": f"Embedding 连通正常（{len(vectors[0])} 维）",
    }
