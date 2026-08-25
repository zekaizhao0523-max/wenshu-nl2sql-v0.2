"""数据源连接配置：三步向导（原始源 → 元数据库 → 向量库）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, unquote

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = ROOT / ".wenshu"
STORE_PATH = STORE_DIR / "connections.json"
MASK = "********"
STORE_VERSION = 2

_SECRET_KEYS = {"password", "api_key", "token"}

_MYSQL_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1 或 mysql.example.com"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "3306", "placeholder": "3306"},
    {"key": "database", "label": "数据库名", "type": "text", "required": True, "placeholder": "vectortest"},
    {"key": "user", "label": "用户名", "type": "text", "required": True, "placeholder": "readonly_user"},
    {"key": "password", "label": "密码", "type": "password", "required": True, "secret": True},
]

_PGSQL_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "5432", "placeholder": "5432"},
    {"key": "database", "label": "数据库名", "type": "text", "required": True, "placeholder": "postgres"},
    {"key": "user", "label": "用户名", "type": "text", "required": True, "placeholder": "postgres"},
    {"key": "password", "label": "密码", "type": "password", "required": True, "secret": True},
    {"key": "schema", "label": "Schema", "type": "text", "required": False, "default": "public", "placeholder": "public"},
]

_HIVE_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "hive-server"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "10000"},
    {"key": "user", "label": "用户名", "type": "text", "required": True, "default": "hive"},
    {"key": "databases", "label": "扫描库（逗号分隔）", "type": "text", "required": False, "default": "ods,dwd,dws", "placeholder": "ods,dwd,dws"},
]

_QDRANT_FIELDS = [
    {"key": "url", "label": "服务地址", "type": "text", "required": True, "placeholder": "http://127.0.0.1:6333 或 Cloud URL"},
    {"key": "api_key", "label": "API Key", "type": "password", "required": False, "secret": True, "placeholder": "Cloud 必填，本地可留空"},
    {"key": "collection", "label": "Collection 名", "type": "text", "required": True, "default": "wenshu_knowledge"},
]

_MILVUS_FIELDS = [
    {"key": "uri", "label": "服务地址", "type": "text", "required": True, "placeholder": "http://127.0.0.1:19530"},
    {"key": "token", "label": "Token / API Key", "type": "password", "required": False, "secret": True, "placeholder": "Zilliz Cloud 等云端必填"},
    {"key": "user", "label": "用户名", "type": "text", "required": False, "placeholder": "可选"},
    {"key": "password", "label": "密码", "type": "password", "required": False, "secret": True},
    {"key": "collection", "label": "Collection 名", "type": "text", "required": True, "default": "wenshu_knowledge"},
]

_CHROMA_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "8000"},
    {"key": "ssl", "label": "启用 SSL（true/false）", "type": "text", "required": False, "default": "false"},
    {"key": "collection", "label": "Collection 名", "type": "text", "required": True, "default": "wenshu_knowledge"},
]

_PGVECTOR_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "5432"},
    {"key": "database", "label": "数据库名", "type": "text", "required": True, "placeholder": "postgres"},
    {"key": "user", "label": "用户名", "type": "text", "required": True},
    {"key": "password", "label": "密码", "type": "password", "required": True, "secret": True},
    {"key": "schema", "label": "Schema", "type": "text", "required": False, "default": "public"},
    {"key": "table", "label": "向量表名", "type": "text", "required": True, "default": "wenshu_embeddings"},
]

_CLICKHOUSE_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "HTTP 端口", "type": "number", "required": True, "default": "8123", "placeholder": "8123"},
    {"key": "database", "label": "数据库名", "type": "text", "required": True, "default": "default"},
    {"key": "user", "label": "用户名", "type": "text", "required": True, "default": "default"},
    {"key": "password", "label": "密码", "type": "password", "required": False, "secret": True},
]

_DORIS_FIELDS = [
    {"key": "host", "label": "主机（FE）", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "MySQL 协议端口", "type": "number", "required": True, "default": "9030", "placeholder": "9030"},
    {"key": "database", "label": "数据库名", "type": "text", "required": True},
    {"key": "user", "label": "用户名", "type": "text", "required": True, "default": "root"},
    {"key": "password", "label": "密码", "type": "password", "required": False, "secret": True},
]

_ORACLE_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "1521"},
    {"key": "service_name", "label": "Service Name", "type": "text", "required": True, "placeholder": "ORCLPDB1"},
    {"key": "user", "label": "用户名", "type": "text", "required": True},
    {"key": "password", "label": "密码", "type": "password", "required": True, "secret": True},
]

_SQLSERVER_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "1433"},
    {"key": "database", "label": "数据库名", "type": "text", "required": True},
    {"key": "user", "label": "用户名", "type": "text", "required": True},
    {"key": "password", "label": "密码", "type": "password", "required": True, "secret": True},
]

_TRINO_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "trino.example.com"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "8080"},
    {"key": "user", "label": "用户名", "type": "text", "required": True, "default": "trino"},
    {"key": "password", "label": "密码", "type": "password", "required": False, "secret": True},
    {"key": "catalog", "label": "Catalog", "type": "text", "required": True, "placeholder": "hive"},
    {"key": "schema", "label": "Schema", "type": "text", "required": False, "default": "default"},
]

_IMPALA_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "impala-host"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "21050"},
    {"key": "database", "label": "默认库", "type": "text", "required": False, "default": "default"},
    {"key": "user", "label": "用户名", "type": "text", "required": False},
    {"key": "password", "label": "密码", "type": "password", "required": False, "secret": True},
]

_SQLITE_FIELDS = [
    {"key": "path", "label": "数据库文件路径", "type": "text", "required": True, "placeholder": "./data/wenshu_meta.db"},
]

_GREENPLUM_FIELDS = [
    {"key": "host", "label": "主机", "type": "text", "required": True, "placeholder": "127.0.0.1"},
    {"key": "port", "label": "端口", "type": "number", "required": True, "default": "5432"},
    {"key": "database", "label": "数据库名", "type": "text", "required": True},
    {"key": "user", "label": "用户名", "type": "text", "required": True},
    {"key": "password", "label": "密码", "type": "password", "required": True, "secret": True},
    {"key": "schema", "label": "Schema", "type": "text", "required": False, "default": "public"},
]

# 三步向导：先选角色，再选引擎，再填参数
WIZARD_STEPS: list[dict[str, Any]] = [
    {
        "id": "raw",
        "step": 1,
        "label": "原始数据源",
        "description": "业务表 / 数仓所在平台，用于发现与同步表结构",
        "engines": [
            {"id": "mysql", "label": "MySQL", "description": "常见 ODS/业务库，或同步落地表", "fields": _MYSQL_FIELDS},
            {"id": "hive", "label": "Hive", "description": "经典大数据数仓（HiveServer2）", "fields": _HIVE_FIELDS},
            {"id": "pgsql", "label": "PostgreSQL", "description": "业务库或分析库", "fields": _PGSQL_FIELDS},
            {"id": "greenplum", "label": "Greenplum", "description": "基于 PG 的 MPP 数仓", "fields": _GREENPLUM_FIELDS},
            {"id": "clickhouse", "label": "ClickHouse", "description": "常见 OLAP 分析库", "fields": _CLICKHOUSE_FIELDS},
            {"id": "doris", "label": "Doris / StarRocks", "description": "兼容 MySQL 协议的实时数仓", "fields": _DORIS_FIELDS},
            {"id": "trino", "label": "Trino / Presto", "description": "跨源联邦查询引擎", "fields": _TRINO_FIELDS},
            {"id": "impala", "label": "Impala", "description": "CDH/Cloudera 常见 SQL 引擎", "fields": _IMPALA_FIELDS},
            {"id": "oracle", "label": "Oracle", "description": "企业核心业务库 / 传统数仓", "fields": _ORACLE_FIELDS},
            {"id": "sqlserver", "label": "SQL Server", "description": "企业业务库或报表库", "fields": _SQLSERVER_FIELDS},
        ],
    },
    {
        "id": "meta",
        "step": 2,
        "label": "元数据库",
        "description": "存放 L1/L2 元数据与知识库表（平台自身库）",
        "engines": [
            {"id": "mysql", "label": "MySQL", "description": "推荐，与当前脚本完全兼容", "fields": _MYSQL_FIELDS},
            {"id": "pgsql", "label": "PostgreSQL", "description": "可选，需适配 DDL/同步脚本", "fields": _PGSQL_FIELDS},
            {"id": "sqlite", "label": "SQLite", "description": "本地单机试用，无需额外部署", "fields": _SQLITE_FIELDS},
            {"id": "sqlserver", "label": "SQL Server", "description": "企业内已有 SQL Server 时可选", "fields": _SQLSERVER_FIELDS},
            {"id": "oracle", "label": "Oracle", "description": "企业内已有 Oracle 时可选", "fields": _ORACLE_FIELDS},
        ],
    },
    {
        "id": "vector",
        "step": 3,
        "label": "向量库",
        "description": "存放表/字段语义向量，供自然语言召回",
        "engines": [
            {"id": "qdrant", "label": "Qdrant", "description": "当前默认，本地或 Cloud", "fields": _QDRANT_FIELDS},
            {"id": "milvus", "label": "Milvus / Zilliz", "description": "企业常见向量库，支持大规模检索", "fields": _MILVUS_FIELDS},
            {"id": "chroma", "label": "Chroma", "description": "轻量本地/服务端向量库", "fields": _CHROMA_FIELDS},
            {"id": "pgvector", "label": "pgvector", "description": "PostgreSQL 向量扩展，适合已有 PG 环境", "fields": _PGVECTOR_FIELDS},
        ],
    },
]

# role + engine → 环境变量
_ENV_MAP: dict[str, dict[str, dict[str, str]]] = {
    "raw": {
        "mysql": {
            "host": "RAW_MYSQL_HOST",
            "port": "RAW_MYSQL_PORT",
            "database": "RAW_MYSQL_DATABASE",
            "user": "RAW_MYSQL_USER",
            "password": "RAW_MYSQL_PASSWORD",
        },
        "hive": {
            "host": "HIVE_HOST",
            "port": "HIVE_PORT",
            "user": "HIVE_USER",
            "databases": "HIVE_DATABASES",
        },
        "pgsql": {
            "host": "RAW_PG_HOST",
            "port": "RAW_PG_PORT",
            "database": "RAW_PG_DATABASE",
            "user": "RAW_PG_USER",
            "password": "RAW_PG_PASSWORD",
            "schema": "RAW_PG_SCHEMA",
        },
        "clickhouse": {
            "host": "RAW_CH_HOST",
            "port": "RAW_CH_PORT",
            "database": "RAW_CH_DATABASE",
            "user": "RAW_CH_USER",
            "password": "RAW_CH_PASSWORD",
        },
        "doris": {
            "host": "RAW_DORIS_HOST",
            "port": "RAW_DORIS_PORT",
            "database": "RAW_DORIS_DATABASE",
            "user": "RAW_DORIS_USER",
            "password": "RAW_DORIS_PASSWORD",
        },
        "greenplum": {
            "host": "RAW_GP_HOST",
            "port": "RAW_GP_PORT",
            "database": "RAW_GP_DATABASE",
            "user": "RAW_GP_USER",
            "password": "RAW_GP_PASSWORD",
            "schema": "RAW_GP_SCHEMA",
        },
        "oracle": {
            "host": "RAW_ORACLE_HOST",
            "port": "RAW_ORACLE_PORT",
            "service_name": "RAW_ORACLE_SERVICE",
            "user": "RAW_ORACLE_USER",
            "password": "RAW_ORACLE_PASSWORD",
        },
        "sqlserver": {
            "host": "RAW_MSSQL_HOST",
            "port": "RAW_MSSQL_PORT",
            "database": "RAW_MSSQL_DATABASE",
            "user": "RAW_MSSQL_USER",
            "password": "RAW_MSSQL_PASSWORD",
        },
        "trino": {
            "host": "RAW_TRINO_HOST",
            "port": "RAW_TRINO_PORT",
            "user": "RAW_TRINO_USER",
            "password": "RAW_TRINO_PASSWORD",
            "catalog": "RAW_TRINO_CATALOG",
            "schema": "RAW_TRINO_SCHEMA",
        },
        "impala": {
            "host": "RAW_IMPALA_HOST",
            "port": "RAW_IMPALA_PORT",
            "database": "RAW_IMPALA_DATABASE",
            "user": "RAW_IMPALA_USER",
            "password": "RAW_IMPALA_PASSWORD",
        },
    },
    "meta": {
        "mysql": {
            "host": "META_MYSQL_HOST",
            "port": "META_MYSQL_PORT",
            "database": "META_MYSQL_DATABASE",
            "user": "META_MYSQL_USER",
            "password": "META_MYSQL_PASSWORD",
        },
        "pgsql": {
            "host": "META_PG_HOST",
            "port": "META_PG_PORT",
            "database": "META_PG_DATABASE",
            "user": "META_PG_USER",
            "password": "META_PG_PASSWORD",
            "schema": "META_PG_SCHEMA",
        },
        "sqlite": {
            "path": "META_SQLITE_PATH",
        },
        "sqlserver": {
            "host": "META_MSSQL_HOST",
            "port": "META_MSSQL_PORT",
            "database": "META_MSSQL_DATABASE",
            "user": "META_MSSQL_USER",
            "password": "META_MSSQL_PASSWORD",
        },
        "oracle": {
            "host": "META_ORACLE_HOST",
            "port": "META_ORACLE_PORT",
            "service_name": "META_ORACLE_SERVICE",
            "user": "META_ORACLE_USER",
            "password": "META_ORACLE_PASSWORD",
        },
    },
    "vector": {
        "qdrant": {
            "url": "QDRANT_URL",
            "api_key": "QDRANT_API_KEY",
            "collection": "QDRANT_COLLECTION",
        },
        "milvus": {
            "uri": "MILVUS_URI",
            "token": "MILVUS_TOKEN",
            "user": "MILVUS_USER",
            "password": "MILVUS_PASSWORD",
            "collection": "MILVUS_COLLECTION",
        },
        "chroma": {
            "host": "CHROMA_HOST",
            "port": "CHROMA_PORT",
            "ssl": "CHROMA_SSL",
            "collection": "CHROMA_COLLECTION",
        },
        "pgvector": {
            "host": "PGVECTOR_HOST",
            "port": "PGVECTOR_PORT",
            "database": "PGVECTOR_DATABASE",
            "user": "PGVECTOR_USER",
            "password": "PGVECTOR_PASSWORD",
            "schema": "PGVECTOR_SCHEMA",
            "table": "PGVECTOR_TABLE",
        },
    },
}

# 旧版扁平 key → (role, engine)
_LEGACY_KEY_MAP = {
    "mysql_raw": ("raw", "mysql"),
    "mysql_meta": ("meta", "mysql"),
    "hive": ("raw", "hive"),
    "qdrant": ("vector", "qdrant"),
}


def _ensure_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


def _step_def(role: str) -> dict[str, Any]:
    for step in WIZARD_STEPS:
        if step["id"] == role:
            return step
    raise KeyError(f"未知配置步骤: {role}")


def _engine_def(role: str, engine: str) -> dict[str, Any]:
    step = _step_def(role)
    for eng in step["engines"]:
        if eng["id"] == engine:
            return eng
    raise KeyError(f"步骤 {role} 不支持引擎: {engine}")


def list_wizard() -> list[dict[str, Any]]:
    return WIZARD_STEPS


def list_connection_types() -> list[dict[str, Any]]:
    """兼容旧接口：展开为 role::engine 列表。"""
    items = []
    for step in WIZARD_STEPS:
        for eng in step["engines"]:
            items.append(
                {
                    "id": f"{step['id']}:{eng['id']}",
                    "role": step["id"],
                    "engine": eng["id"],
                    "label": f"{step['label']} · {eng['label']}",
                    "description": eng.get("description") or step.get("description"),
                    "fields": eng["fields"],
                    "step": step["step"],
                }
            )
    return items


def load_store() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"version": STORE_VERSION, "roles": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": STORE_VERSION, "roles": {}}

    if not isinstance(data, dict):
        return {"version": STORE_VERSION, "roles": {}}

    # 新格式
    if "roles" in data and isinstance(data["roles"], dict):
        data.setdefault("version", STORE_VERSION)
        return data

    # 旧格式迁移：{"mysql_raw": {...}, ...}
    roles: dict[str, Any] = {}
    for legacy_key, values in data.items():
        if legacy_key in ("version", "roles") or not isinstance(values, dict):
            continue
        mapped = _LEGACY_KEY_MAP.get(legacy_key)
        if not mapped:
            continue
        role, engine = mapped
        roles[role] = {"engine": engine, "values": {k: str(v) for k, v in values.items() if v is not None}}
    return {"version": STORE_VERSION, "roles": roles}


def save_store(store: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": STORE_VERSION, "roles": store.get("roles") or {}}
    STORE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_overlay_to_environ() -> None:
    """将平台保存的连接写入进程环境，优先于 .env。"""
    _ensure_dotenv()
    store = load_store()
    roles = store.get("roles") or {}
    for role, cfg in roles.items():
        if not isinstance(cfg, dict):
            continue
        engine = cfg.get("engine")
        values = cfg.get("values") or {}
        if not engine:
            continue
        os.environ[f"{role.upper()}_SOURCE_TYPE" if role == "raw" else f"{role.upper()}_DB_TYPE"] = str(engine)
        if role == "raw":
            os.environ["RAW_SOURCE_TYPE"] = str(engine)
        elif role == "meta":
            os.environ["META_DB_TYPE"] = str(engine)
        elif role == "vector":
            os.environ["VECTOR_DB_TYPE"] = str(engine)

        env_map = _ENV_MAP.get(role, {}).get(str(engine), {})
        for field_key, env_key in env_map.items():
            val = values.get(field_key)
            if val is None or val == "":
                continue
            os.environ[env_key] = str(val)


def _defaults_from_env(role: str, engine: str) -> dict[str, str]:
    eng = _engine_def(role, engine)
    field_defaults = {f["key"]: str(f.get("default") or "") for f in eng["fields"]}
    result = dict(field_defaults)
    env_map = _ENV_MAP.get(role, {}).get(engine, {})

    if role == "raw" and engine == "mysql":
        result.update(
            {
                "host": os.getenv("RAW_MYSQL_HOST") or os.getenv("MYSQL_HOST") or field_defaults.get("host", ""),
                "port": os.getenv("RAW_MYSQL_PORT") or os.getenv("MYSQL_PORT") or field_defaults.get("port", "3306"),
                "database": os.getenv("RAW_MYSQL_DATABASE") or os.getenv("MYSQL_DATABASE") or field_defaults.get("database", ""),
                "user": os.getenv("RAW_MYSQL_USER") or os.getenv("MYSQL_USER") or "",
                "password": os.getenv("RAW_MYSQL_PASSWORD") or os.getenv("MYSQL_PASSWORD") or "",
            }
        )
    elif role == "meta" and engine == "mysql":
        result.update(
            {
                "host": os.getenv("META_MYSQL_HOST") or os.getenv("MYSQL_HOST") or "",
                "port": os.getenv("META_MYSQL_PORT") or os.getenv("MYSQL_PORT") or field_defaults.get("port", "3306"),
                "database": os.getenv("META_MYSQL_DATABASE") or os.getenv("MYSQL_DATABASE") or field_defaults.get("database", ""),
                "user": os.getenv("META_MYSQL_USER") or os.getenv("MYSQL_USER") or "",
                "password": os.getenv("META_MYSQL_PASSWORD") or os.getenv("MYSQL_PASSWORD") or "",
            }
        )
    else:
        for field_key, env_key in env_map.items():
            result[field_key] = os.getenv(env_key) or field_defaults.get(field_key, "")
    return result


def _guess_engine(role: str) -> str:
    store = load_store()
    roles = store.get("roles") or {}
    if role in roles and roles[role].get("engine"):
        return str(roles[role]["engine"])
    if role == "raw":
        return os.getenv("RAW_SOURCE_TYPE") or "mysql"
    if role == "meta":
        return os.getenv("META_DB_TYPE") or "mysql"
    if role == "vector":
        return os.getenv("VECTOR_DB_TYPE") or "qdrant"
    return _step_def(role)["engines"][0]["id"]


def _mask_values(values: dict[str, str], *, mask_secrets: bool) -> tuple[dict[str, str], dict[str, bool]]:
    out = dict(values)
    secrets_set: dict[str, bool] = {}
    for key in _SECRET_KEYS:
        if key not in out:
            continue
        has = bool(out.get(key))
        secrets_set[key] = has
        if mask_secrets and has:
            out[key] = MASK
        elif mask_secrets:
            out[key] = ""
    return out, secrets_set


def get_role_connection(role: str, engine: str | None = None, *, mask_secrets: bool = True) -> dict[str, Any]:
    _ensure_dotenv()
    _step_def(role)
    store = load_store()
    roles = store.get("roles") or {}
    saved = roles.get(role) if isinstance(roles.get(role), dict) else None
    eng = engine or (saved.get("engine") if saved else None) or _guess_engine(role)
    _engine_def(role, eng)

    values = _defaults_from_env(role, eng)
    from_platform = False
    if saved and saved.get("engine") == eng:
        from_platform = True
        for k, v in (saved.get("values") or {}).items():
            if v is not None and v != "":
                values[k] = str(v)

    masked, secrets_set = _mask_values(values, mask_secrets=mask_secrets)
    return {
        "role": role,
        "engine": eng,
        "type": f"{role}:{eng}",  # 兼容旧前端
        "values": masked,
        "secrets_set": secrets_set,
        "source": "platform" if from_platform else "env",
    }


def get_connection_values(conn_type: str, *, mask_secrets: bool = True) -> dict[str, Any]:
    """兼容旧 type id：mysql_raw / hive / raw:mysql。"""
    if ":" in conn_type:
        role, engine = conn_type.split(":", 1)
        return get_role_connection(role, engine, mask_secrets=mask_secrets)
    if conn_type in _LEGACY_KEY_MAP:
        role, engine = _LEGACY_KEY_MAP[conn_type]
        return get_role_connection(role, engine, mask_secrets=mask_secrets)
    # 也可能直接传 role
    return get_role_connection(conn_type, mask_secrets=mask_secrets)


def list_all_connections(*, mask_secrets: bool = True) -> list[dict[str, Any]]:
    return [get_role_connection(step["id"], mask_secrets=mask_secrets) for step in WIZARD_STEPS]


def _resolve_secrets(role: str, engine: str, values: dict[str, str]) -> dict[str, str]:
    current = get_role_connection(role, engine, mask_secrets=False)["values"]
    merged = dict(current)
    for k, v in values.items():
        if k in _SECRET_KEYS and (v is None or v == "" or v == MASK):
            continue
        if v is None:
            continue
        merged[k] = str(v).strip()
    return merged


def _mysql_dsn(values: dict[str, str]) -> str:
    user = quote_plus(values.get("user") or "")
    password = quote_plus(values.get("password") or "")
    host = values.get("host") or "127.0.0.1"
    port = values.get("port") or "3306"
    database = values.get("database") or ""
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


def _pgsql_dsn(values: dict[str, str]) -> str:
    user = quote_plus(values.get("user") or "")
    password = quote_plus(values.get("password") or "")
    host = values.get("host") or "127.0.0.1"
    port = values.get("port") or "5432"
    database = values.get("database") or ""
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


def _oracle_dsn(values: dict[str, str]) -> str:
    user = quote_plus(values.get("user") or "")
    password = quote_plus(values.get("password") or "")
    host = values.get("host") or "127.0.0.1"
    port = values.get("port") or "1521"
    service = values.get("service_name") or "ORCL"
    return f"oracle+oracledb://{user}:{password}@{host}:{port}/?service_name={service}"


def _mssql_dsn(values: dict[str, str]) -> str:
    user = quote_plus(values.get("user") or "")
    password = quote_plus(values.get("password") or "")
    host = values.get("host") or "127.0.0.1"
    port = values.get("port") or "1433"
    database = values.get("database") or ""
    return f"mssql+pymssql://{user}:{password}@{host}:{port}/{database}"


def _trino_dsn(values: dict[str, str]) -> str:
    user = quote_plus(values.get("user") or "trino")
    password = values.get("password") or ""
    host = values.get("host") or "127.0.0.1"
    port = values.get("port") or "8080"
    catalog = values.get("catalog") or "hive"
    schema = values.get("schema") or "default"
    auth = f"{user}:{quote_plus(password)}@" if password else f"{user}@"
    return f"trino://{auth}{host}:{port}/{catalog}/{schema}"


def _normalize_target(conn_type: str | None, role: str | None, engine: str | None) -> tuple[str, str]:
    if role and engine:
        _engine_def(role, engine)
        return role, engine
    if conn_type and ":" in conn_type:
        r, e = conn_type.split(":", 1)
        _engine_def(r, e)
        return r, e
    if conn_type and conn_type in _LEGACY_KEY_MAP:
        return _LEGACY_KEY_MAP[conn_type]
    if role and not engine:
        return role, _guess_engine(role)
    raise KeyError("请指定 role + engine，或兼容的 type")


def _friendly_db_error(exc: Exception) -> str:
    msg = str(exc)
    if "1040" in msg and "Too many connections" in msg:
        return (
            "MySQL 连接数已满（错误 1040）。"
            " 请点击侧栏或连接配置页的「释放 DB 连接」，关闭其他数据库客户端后再试。"
        )
    if "2013" in msg and "Lost connection" in msg:
        return (
            "MySQL 在连接/查询阶段被服务器断开（错误 2013）。"
            " 密码若错误通常会报 1045；若仍是 2013，多半是 SQLPub 侧问题："
            "实例被锁定（免费版存储超 0.5GB、余额不足）、数据库已禁用/删除，或 Serverless 冷启动失败。"
            " 请检查数据库实例状态、存储用量和库名，确认后再试连接。"
        )
    if "1045" in msg and "Access denied" in msg:
        return "用户名或密码错误，请对照 SQLPub 控制台核对账号密码。"
    if "2003" in msg or "Can't connect" in msg:
        return f"无法连上 MySQL 主机（网络/端口/防火墙）：{msg.split(chr(10))[0]}"
    return msg.split("\n")[0] if msg else "连接失败"


def test_connection(
    conn_type: str | None = None,
    values: dict[str, str] | None = None,
    *,
    role: str | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    role, engine = _normalize_target(conn_type, role, engine)
    resolved = _resolve_secrets(role, engine, values or {})

    def _conn_summary(cfg: dict) -> str:
        host = cfg.get("host") or cfg.get("url") or "-"
        if engine == "mysql":
            return f"{cfg.get('user', '-')}@{host}:{cfg.get('port', '')}/{cfg.get('database', '-')}"
        if engine == "qdrant":
            return f"{host} · collection={cfg.get('collection', '-')}"
        return str(host)

    def _secret_sources(form_values: dict, resolved_cfg: dict) -> dict[str, str]:
        src = {}
        for key in _SECRET_KEYS:
            if key not in resolved_cfg or not resolved_cfg.get(key):
                continue
            raw = (form_values or {}).get(key)
            src[key] = "form" if raw and raw not in ("", MASK) else "saved"
        return src

    form_values = values or {}
    summary = _conn_summary(resolved)
    secret_src = _secret_sources(form_values, resolved)

    try:
        if engine == "mysql":
            for key in ("host", "port", "database", "user"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            if not resolved.get("password"):
                return {"ok": False, "message": "缺少密码（请在表单输入，或先保存连接配置）"}
            eng = create_engine(
                _mysql_dsn(resolved),
                pool_pre_ping=True,
                pool_size=1,
                max_overflow=0,
            )
            with eng.connect() as conn:
                db = conn.execute(text("SELECT DATABASE()")).scalar()
                ver = conn.execute(text("SELECT VERSION()")).scalar()
            eng.dispose()
            pwd_hint = "密码来自表单" if secret_src.get("password") == "form" else "密码来自已保存配置"
            return {
                "ok": True,
                "message": f"连接成功 · {summary} · 库 {db} · MySQL {ver} · {pwd_hint}",
                "used": {k: resolved.get(k) for k in ("host", "port", "database", "user") if resolved.get(k)},
                "secret_sources": secret_src,
            }

        if engine in ("pgsql", "greenplum"):
            for key in ("host", "port", "database", "user"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            try:
                eng = create_engine(_pgsql_dsn(resolved), pool_pre_ping=True)
                with eng.connect() as conn:
                    db = conn.execute(text("SELECT current_database()")).scalar()
                    ver = conn.execute(text("SELECT version()")).scalar()
                eng.dispose()
            except Exception as exc:
                msg = str(exc)
                if "psycopg2" in msg.lower() or "no module" in msg.lower():
                    return {"ok": False, "message": "未安装 PostgreSQL 驱动，请执行: pip install psycopg2-binary"}
                raise
            label = "Greenplum/PG" if engine == "greenplum" else "PostgreSQL"
            short = (ver or "").split("\n")[0][:60]
            return {"ok": True, "message": f"连接成功 · 库 {db} · {label} {short}"}

        if engine == "hive":
            if not resolved.get("host"):
                return {"ok": False, "message": "缺少必填项: host"}
            try:
                from pyhive import hive
            except ImportError:
                return {"ok": False, "message": "未安装 pyhive，请先 pip install pyhive"}
            port = int(resolved.get("port") or 10000)
            conn = hive.connect(
                host=resolved["host"],
                port=port,
                username=resolved.get("user") or "hive",
            )
            cursor = conn.cursor()
            cursor.execute("SHOW DATABASES")
            dbs = [r[0] for r in cursor.fetchall()[:20]]
            cursor.close()
            conn.close()
            return {"ok": True, "message": f"连接成功 · 可见库: {', '.join(dbs) or '(空)'}"}

        if engine == "impala":
            if not resolved.get("host"):
                return {"ok": False, "message": "缺少必填项: host"}
            try:
                from impala.dbapi import connect as impala_connect
            except ImportError:
                return {"ok": False, "message": "未安装 impyla，请执行: pip install impyla"}
            conn = impala_connect(
                host=resolved["host"],
                port=int(resolved.get("port") or 21050),
                database=resolved.get("database") or "default",
                user=resolved.get("user") or None,
                password=resolved.get("password") or None,
            )
            cursor = conn.cursor()
            cursor.execute("SHOW DATABASES")
            dbs = [r[0] for r in cursor.fetchall()[:20]]
            cursor.close()
            conn.close()
            return {"ok": True, "message": f"连接成功 · 可见库: {', '.join(dbs) or '(空)'}"}

        if engine == "oracle":
            for key in ("host", "port", "service_name", "user"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            try:
                eng = create_engine(_oracle_dsn(resolved), pool_pre_ping=True)
                with eng.connect() as conn:
                    ver = conn.execute(text("SELECT BANNER FROM v$version WHERE ROWNUM=1")).scalar()
                    user = conn.execute(text("SELECT USER FROM dual")).scalar()
                eng.dispose()
            except Exception as exc:
                msg = str(exc)
                if "oracledb" in msg.lower() or "cx_oracle" in msg.lower() or "no module" in msg.lower():
                    return {"ok": False, "message": "未安装 Oracle 驱动，请执行: pip install oracledb"}
                raise
            return {"ok": True, "message": f"连接成功 · 用户 {user} · {(ver or '')[:50]}"}

        if engine == "sqlserver":
            for key in ("host", "port", "database", "user"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            try:
                eng = create_engine(_mssql_dsn(resolved), pool_pre_ping=True)
                with eng.connect() as conn:
                    db = conn.execute(text("SELECT DB_NAME()")).scalar()
                    ver = conn.execute(text("SELECT @@VERSION")).scalar()
                eng.dispose()
            except Exception as exc:
                msg = str(exc)
                if "pymssql" in msg.lower() or "no module" in msg.lower():
                    return {"ok": False, "message": "未安装 SQL Server 驱动，请执行: pip install pymssql"}
                raise
            short = (ver or "").split("\n")[0][:60]
            return {"ok": True, "message": f"连接成功 · 库 {db} · {short}"}

        if engine == "trino":
            for key in ("host", "port", "user", "catalog"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            try:
                eng = create_engine(_trino_dsn(resolved))
                with eng.connect() as conn:
                    ver = conn.execute(text("SELECT version()")).scalar()
                    cat = resolved.get("catalog")
                eng.dispose()
            except Exception as exc:
                msg = str(exc)
                if "trino" in msg.lower() and ("no module" in msg.lower() or "not installed" in msg.lower()):
                    return {"ok": False, "message": "未安装 Trino 驱动，请执行: pip install trino"}
                raise
            return {"ok": True, "message": f"连接成功 · catalog {cat} · Trino {ver}"}

        if engine == "sqlite":
            path = resolved.get("path") or ""
            if not path:
                return {"ok": False, "message": "缺少必填项: path"}
            db_path = Path(path)
            if not db_path.is_absolute():
                db_path = ROOT / path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            eng = create_engine(f"sqlite:///{db_path.as_posix()}")
            with eng.connect() as conn:
                ver = conn.execute(text("SELECT sqlite_version()")).scalar()
            eng.dispose()
            return {"ok": True, "message": f"连接成功 · 文件 {db_path} · SQLite {ver}"}

        if engine == "qdrant":
            if not resolved.get("url"):
                return {"ok": False, "message": "缺少必填项: url"}
            from qdrant_client import QdrantClient

            kwargs: dict[str, Any] = {"url": resolved["url"], "timeout": 10}
            if resolved.get("api_key"):
                kwargs["api_key"] = resolved["api_key"]
            client = QdrantClient(**kwargs)
            cols = client.get_collections()
            names = [c.name for c in cols.collections]
            collection = resolved.get("collection") or "wenshu_knowledge"
            hint = f"已存在 collection: {', '.join(names)}" if names else "暂无 collection"
            exists = collection in names
            return {
                "ok": True,
                "message": f"连接成功 · {hint}"
                + (f" · 目标「{collection}」已就绪" if exists else f" · 目标「{collection}」尚未创建"),
            }

        if engine == "milvus":
            if not resolved.get("uri"):
                return {"ok": False, "message": "缺少必填项: uri"}
            try:
                from pymilvus import MilvusClient
            except ImportError:
                return {"ok": False, "message": "未安装 pymilvus，请执行: pip install pymilvus"}
            client_kwargs: dict[str, Any] = {"uri": resolved["uri"]}
            if resolved.get("token"):
                client_kwargs["token"] = resolved["token"]
            if resolved.get("user"):
                client_kwargs["user"] = resolved["user"]
            if resolved.get("password"):
                client_kwargs["password"] = resolved["password"]
            client = MilvusClient(**client_kwargs)
            names = client.list_collections()
            collection = resolved.get("collection") or "wenshu_knowledge"
            exists = collection in names
            hint = f"已有 collection: {', '.join(names[:8])}" if names else "暂无 collection"
            return {
                "ok": True,
                "message": f"连接成功 · {hint}"
                + (f" · 目标「{collection}」已就绪" if exists else f" · 目标「{collection}」尚未创建"),
            }

        if engine == "chroma":
            if not resolved.get("host"):
                return {"ok": False, "message": "缺少必填项: host"}
            try:
                import chromadb
            except ImportError:
                return {"ok": False, "message": "未安装 chromadb，请执行: pip install chromadb"}
            ssl = str(resolved.get("ssl") or "false").lower() in ("1", "true", "yes")
            client = chromadb.HttpClient(
                host=resolved["host"],
                port=int(resolved.get("port") or 8000),
                ssl=ssl,
            )
            cols = client.list_collections()
            names = [c.name for c in cols]
            collection = resolved.get("collection") or "wenshu_knowledge"
            exists = collection in names
            hint = f"已有 collection: {', '.join(names[:8])}" if names else "暂无 collection"
            return {
                "ok": True,
                "message": f"连接成功 · {hint}"
                + (f" · 目标「{collection}」已就绪" if exists else f" · 目标「{collection}」尚未创建"),
            }

        if engine == "pgvector":
            for key in ("host", "port", "database", "user"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            try:
                eng = create_engine(_pgsql_dsn(resolved), pool_pre_ping=True)
                with eng.connect() as conn:
                    db = conn.execute(text("SELECT current_database()")).scalar()
                    ext = conn.execute(
                        text("SELECT extname FROM pg_extension WHERE extname='vector'")
                    ).scalar()
                eng.dispose()
            except Exception as exc:
                msg = str(exc)
                if "psycopg2" in msg.lower() or "no module" in msg.lower():
                    return {"ok": False, "message": "未安装 PostgreSQL 驱动，请执行: pip install psycopg2-binary"}
                raise
            if not ext:
                return {"ok": False, "message": f"已连上库 {db}，但未安装 pgvector 扩展（CREATE EXTENSION vector）"}
            table = resolved.get("table") or "wenshu_embeddings"
            return {"ok": True, "message": f"连接成功 · 库 {db} · pgvector 已启用 · 目标表「{table}」"}

        if engine == "clickhouse":
            for key in ("host", "port", "database", "user"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            try:
                import clickhouse_connect
            except ImportError:
                return {"ok": False, "message": "未安装 clickhouse-connect，请执行: pip install clickhouse-connect"}
            client = clickhouse_connect.get_client(
                host=resolved["host"],
                port=int(resolved.get("port") or 8123),
                username=resolved.get("user") or "default",
                password=resolved.get("password") or "",
                database=resolved.get("database") or "default",
            )
            ver = client.query("SELECT version()").first_item
            db = resolved.get("database") or "default"
            return {"ok": True, "message": f"连接成功 · 库 {db} · ClickHouse {ver}"}

        if engine == "doris":
            for key in ("host", "port", "database", "user"):
                if not resolved.get(key):
                    return {"ok": False, "message": f"缺少必填项: {key}"}
            eng = create_engine(
                _mysql_dsn(resolved),
                pool_pre_ping=True,
                pool_size=1,
                max_overflow=0,
            )
            with eng.connect() as conn:
                db = conn.execute(text("SELECT DATABASE()")).scalar()
                ver = conn.execute(text("SELECT VERSION()")).scalar()
            eng.dispose()
            return {"ok": True, "message": f"连接成功 · 库 {db} · {ver}"}

        return {"ok": False, "message": f"未知引擎: {engine}"}
    except Exception as exc:
        return {"ok": False, "message": _friendly_db_error(exc)}


def save_connection(
    conn_type: str | None = None,
    values: dict[str, str] | None = None,
    *,
    role: str | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    role, engine = _normalize_target(conn_type, role, engine)
    eng_def = _engine_def(role, engine)
    resolved = _resolve_secrets(role, engine, values or {})

    for field in eng_def["fields"]:
        if field.get("required") and field["key"] not in _SECRET_KEYS and not resolved.get(field["key"]):
            raise ValueError(f"缺少必填项: {field['label']}")
        if field.get("required") and field["key"] in _SECRET_KEYS and not resolved.get(field["key"]):
            raise ValueError(f"缺少必填项: {field['label']}")

    store = load_store()
    roles = store.setdefault("roles", {})
    allowed = {f["key"] for f in eng_def["fields"]}
    roles[role] = {
        "engine": engine,
        "values": {k: str(v) for k, v in resolved.items() if k in allowed and v is not None},
    }
    save_store(store)
    apply_overlay_to_environ()
    try:
        from db_config import reset_mysql_engines

        reset_mysql_engines()
    except Exception:
        pass
    return get_role_connection(role, engine, mask_secrets=True)


def parse_connection_text(
    conn_type: str | None = None,
    text: str = "",
    *,
    role: str | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    role, engine = _normalize_target(conn_type, role, engine)
    text = (text or "").strip()
    if not text:
        return {"ok": False, "values": {}, "message": "请粘贴连接串或配置文本"}

    values: dict[str, str] = {}
    message = "已用规则解析"

    if engine == "mysql":
        mysql_m = re.search(
            r"(?:mysql(?:\+pymysql)?|jdbc:mysql)://([^:@/\s]+):([^@/\s]+)@([^:/\s]+):?(\d+)?/([^\s?]+)",
            text,
            re.I,
        )
        if mysql_m:
            values = {
                "user": unquote(mysql_m.group(1)),
                "password": unquote(mysql_m.group(2)),
                "host": mysql_m.group(3),
                "port": mysql_m.group(4) or "3306",
                "database": mysql_m.group(5).rstrip("/"),
            }
        else:
            mysql_m = re.search(
                r"(?:mysql(?:\+pymysql)?|jdbc:mysql)://([^:@/\s]+)@([^:/\s]+):?(\d+)?/([^\s?]+)",
                text,
                re.I,
            )
            if mysql_m:
                values = {
                    "user": unquote(mysql_m.group(1)),
                    "host": mysql_m.group(2),
                    "port": mysql_m.group(3) or "3306",
                    "database": mysql_m.group(4).rstrip("/"),
                }

    if not values and engine == "pgsql":
        pg_m = re.search(
            r"(?:postgresql(?:\+psycopg2)?|postgres|jdbc:postgresql)://([^:@/\s]+):([^@/\s]+)@([^:/\s]+):?(\d+)?/([^\s?]+)",
            text,
            re.I,
        )
        if pg_m:
            values = {
                "user": unquote(pg_m.group(1)),
                "password": unquote(pg_m.group(2)),
                "host": pg_m.group(3),
                "port": pg_m.group(4) or "5432",
                "database": pg_m.group(5).rstrip("/"),
            }

    if not values and engine == "qdrant":
        url_m = re.search(r"https?://[^\s]+", text)
        if url_m:
            values["url"] = url_m.group(0).rstrip("/")
        key_m = re.search(r"(?:api[_-]?key|API[_-]?KEY)\s*[=:]\s*(\S+)", text)
        if key_m:
            values["api_key"] = key_m.group(1).strip("'\"")
        col_m = re.search(r"(?:collection|COLLECTION)\s*[=:]\s*(\S+)", text)
        if col_m:
            values["collection"] = col_m.group(1).strip("'\"")

    if not values and engine == "milvus":
        uri_m = re.search(r"https?://[^\s]+", text)
        if uri_m:
            values["uri"] = uri_m.group(0).rstrip("/")
        token_m = re.search(r"(?:token|api[_-]?key)\s*[=:]\s*(\S+)", text, re.I)
        if token_m:
            values["token"] = token_m.group(1).strip("'\"")
        col_m = re.search(r"(?:collection)\s*[=:]\s*(\S+)", text, re.I)
        if col_m:
            values["collection"] = col_m.group(1).strip("'\"")

    if not values and engine in ("chroma", "pgvector", "clickhouse", "doris"):
        for m in re.finditer(r"(?m)^\s*([A-Za-z_][\w]*)\s*[=:]\s*(.+?)\s*$", text):
            k, v = m.group(1).lower(), m.group(2).strip().strip("'\"")
            mapping = {
                "host": "host",
                "port": "port",
                "database": "database",
                "user": "user",
                "username": "user",
                "password": "password",
                "schema": "schema",
                "table": "table",
                "collection": "collection",
                "ssl": "ssl",
                "uri": "uri",
                "token": "token",
            }
            if k in mapping:
                values[mapping[k]] = v
        url_m = re.search(r"https?://([^:/\s]+):?(\d+)?", text)
        if url_m and "host" not in values and engine == "chroma":
            values["host"] = url_m.group(1)
            if url_m.group(2):
                values["port"] = url_m.group(2)

    if not values and engine == "hive":
        host_m = re.search(r"(?:host|HIVE_HOST)\s*[=:]\s*(\S+)", text, re.I)
        port_m = re.search(r"(?:port|HIVE_PORT)\s*[=:]\s*(\d+)", text, re.I)
        user_m = re.search(r"(?:user|username|HIVE_USER)\s*[=:]\s*(\S+)", text, re.I)
        if host_m:
            values["host"] = host_m.group(1).strip("'\"")
        if port_m:
            values["port"] = port_m.group(1)
        if user_m:
            values["user"] = user_m.group(1).strip("'\"")

    if not values:
        for m in re.finditer(r"(?m)^\s*([A-Za-z_][\w]*)\s*[=:]\s*(.+?)\s*$", text):
            k, v = m.group(1).lower(), m.group(2).strip().strip("'\"")
            mapping = {
                "host": "host",
                "port": "port",
                "database": "database",
                "db": "database",
                "user": "user",
                "username": "user",
                "password": "password",
                "schema": "schema",
                "url": "url",
                "api_key": "api_key",
                "collection": "collection",
                "databases": "databases",
            }
            if k in mapping:
                values[mapping[k]] = v

    if not values:
        llm_values = _try_local_llm_parse(role, engine, text)
        if llm_values:
            values = llm_values
            message = "已用本地大模型解析"

    if not values:
        return {"ok": False, "values": {}, "message": "未能解析，请检查格式或手动填写"}

    allowed = {f["key"] for f in _engine_def(role, engine)["fields"]}
    values = {k: v for k, v in values.items() if k in allowed}
    return {"ok": True, "values": values, "message": message}


def _try_local_llm_parse(role: str, engine: str, text: str) -> dict[str, str] | None:
    from wenshu.services.comment_llm import _call_llm_json, llm_available

    if not llm_available():
        return None

    fields = [f["key"] for f in _engine_def(role, engine)["fields"]]
    prompt = (
        f"从下面文本中提取数据库连接参数，角色={role}，引擎={engine}，需要字段: {', '.join(fields)}。\n"
        "只输出一个 JSON 对象，不要 markdown，不要解释。缺的字段省略。\n\n"
        f"文本:\n{text[:2000]}"
    )
    try:
        data = _call_llm_json(prompt, timeout=30)
        if not isinstance(data, dict):
            return None
        return {str(k): str(v) for k, v in data.items() if v is not None and str(v).strip()}
    except Exception:
        return None
