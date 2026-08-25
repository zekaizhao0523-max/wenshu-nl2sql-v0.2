"""初始化 L1/L2 元数据表。"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from wenshu.services.schema_templates import apply_legacy_schema, apply_schema_templates


def init_metadata_tables(engine: Engine) -> dict:
    try:
        return apply_schema_templates(engine)
    except FileNotFoundError:
        return apply_legacy_schema(engine)
