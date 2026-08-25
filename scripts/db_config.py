"""MySQL 连接配置：业务库与元数据库分离。"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# 共享 MySQL（如 SQLPub 免费版）并发连接数有限，控制池大小避免占满
_MYSQL_POOL_KW = {
    "pool_pre_ping": True,
    "pool_recycle": 1800,
    "pool_size": 2,
    "max_overflow": 2,
}

_raw_engine: Engine | None = None
_meta_engine: Engine | None = None


def _apply_platform_overlay() -> None:
    """应用平台「连接配置」页保存的参数（优先于 .env）。"""
    try:
        from wenshu.services.connections import apply_overlay_to_environ

        apply_overlay_to_environ()
    except Exception:
        pass


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
        except ImportError:
            pass
    _apply_platform_overlay()


def _build_dsn(host: str, port: str, database: str, user: str, password: str) -> str:
    user_q = quote_plus(user)
    password_q = quote_plus(password)
    return f"mysql+pymysql://{user_q}:{password_q}@{host}:{port}/{database}?charset=utf8mb4"


def get_raw_mysql_dsn() -> str:
    """原始业务表所在库（默认 vectortest）。"""
    _load_dotenv()
    dsn = os.getenv("RAW_MYSQL_DSN")
    if dsn:
        return dsn

    host = os.getenv("RAW_MYSQL_HOST") or os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("RAW_MYSQL_PORT") or os.getenv("MYSQL_PORT", "3306")
    database = os.getenv("RAW_MYSQL_DATABASE") or os.getenv("MYSQL_DATABASE", "vectortest")
    user = os.getenv("RAW_MYSQL_USER") or os.getenv("MYSQL_USER", "root")
    password = os.getenv("RAW_MYSQL_PASSWORD") or os.getenv("MYSQL_PASSWORD", "")
    return _build_dsn(host, port, database, user, password)


def get_meta_mysql_dsn() -> str:
    """L1/L2 元数据所在库（默认 metadata_vector）。"""
    _load_dotenv()
    dsn = os.getenv("META_MYSQL_DSN")
    if dsn:
        return dsn

    host = os.getenv("META_MYSQL_HOST") or os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("META_MYSQL_PORT") or os.getenv("MYSQL_PORT", "3306")
    database = os.getenv("META_MYSQL_DATABASE") or os.getenv("MYSQL_DATABASE", "metadata_vector")
    user = os.getenv("META_MYSQL_USER") or os.getenv("MYSQL_USER", "root")
    password = os.getenv("META_MYSQL_PASSWORD") or os.getenv("MYSQL_PASSWORD", "")
    return _build_dsn(host, port, database, user, password)


def get_mysql_dsn() -> str:
    """兼容旧脚本：指向元数据库。"""
    return get_meta_mysql_dsn()


def get_raw_database_name() -> str | None:
    """当前配置的原始业务库名（连接向导 / .env）。"""
    _load_dotenv()
    db = os.getenv("RAW_MYSQL_DATABASE") or os.getenv("MYSQL_DATABASE")
    if db and db.strip():
        return db.strip()
    dsn = os.getenv("RAW_MYSQL_DSN") or ""
    if not dsn:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(dsn.replace("mysql+pymysql", "mysql"))
        path = (parsed.path or "").strip("/")
        if path:
            return path.split("?")[0]
    except Exception:
        pass
    return None


def get_raw_mysql_engine(echo: bool = False) -> Engine:
    """复用全局连接池（避免每次 API 请求新建池占满远程 MySQL 连接数）。"""
    global _raw_engine
    if _raw_engine is None:
        _raw_engine = create_engine(get_raw_mysql_dsn(), echo=echo, **_MYSQL_POOL_KW)
    return _raw_engine


def get_meta_mysql_engine(echo: bool = False) -> Engine:
    global _meta_engine
    if _meta_engine is None:
        _meta_engine = create_engine(get_meta_mysql_dsn(), echo=echo, **_MYSQL_POOL_KW)
    return _meta_engine


def dispose_mysql_engines() -> None:
    """释放连接池占用的 MySQL 连接（配置变更或服务关闭时调用）。"""
    global _raw_engine, _meta_engine
    for eng in (_raw_engine, _meta_engine):
        if eng is not None:
            eng.dispose()
    _raw_engine = None
    _meta_engine = None


def reset_mysql_engines() -> None:
    """连接参数变更后：销毁旧池，下次请求按新配置重建。"""
    dispose_mysql_engines()


def create_raw_mysql_engine(echo: bool = False) -> Engine:
    return get_raw_mysql_engine(echo=echo)


def create_meta_mysql_engine(echo: bool = False) -> Engine:
    return get_meta_mysql_engine(echo=echo)


def create_mysql_engine(echo: bool = False) -> Engine:
    """兼容旧脚本：元数据库连接。"""
    return create_meta_mysql_engine(echo=echo)


def create_qdrant_client():
    """Qdrant 客户端；Qdrant Cloud 需配置 QDRANT_API_KEY。"""
    _load_dotenv()
    from qdrant_client import QdrantClient

    url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    api_key = os.getenv("QDRANT_API_KEY")
    # Windows 系统代理会导致部分云端 HTTPS 握手失败（EOF in violation of protocol）
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    kwargs = {"url": url, "timeout": 60, "check_compatibility": False}
    if api_key:
        kwargs["api_key"] = api_key
    return QdrantClient(**kwargs)
