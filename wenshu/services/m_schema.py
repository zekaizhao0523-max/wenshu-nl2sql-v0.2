"""M-Schema 构建与 XiYan 官方 NL2SQL Prompt（路线 B：L1 元数据 + 可选业务库 PK/Examples）。"""

from __future__ import annotations

import datetime
import decimal
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# ---------------------------------------------------------------------------
# 参考 XGenerationLab/M-Schema：utils.examples_to_str
# ---------------------------------------------------------------------------


def _is_email(string: str) -> bool:
    return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", string))


def examples_to_str(examples: list) -> list[str]:
    """将采样值转为 M-Schema 可展示的字符串列表。"""
    values = list(examples)
    for i, val in enumerate(values):
        if isinstance(val, (datetime.date, datetime.datetime)):
            values = [val]
            break
        if isinstance(val, decimal.Decimal):
            values[i] = str(float(val))
        elif val is not None and _is_email(str(val)):
            values = []
            break
        elif val is not None and ("http://" in str(val) or "https://" in str(val)):
            values = []
            break
    return [str(v) for v in values if v is not None and len(str(v)) > 0]


# ---------------------------------------------------------------------------
# 参考 XGenerationLab/M-Schema：m_schema.MSchema
# ---------------------------------------------------------------------------


class MSchema:
    def __init__(self, db_id: str = "Anonymous", schema: str | None = None):
        self.db_id = db_id
        self.schema = schema
        self.tables: dict[str, dict] = {}
        self.foreign_keys: list[list] = []

    def add_table(self, name: str, fields: dict | None = None, comment: str | None = None) -> None:
        self.tables[name] = {"fields": (fields or {}).copy(), "examples": [], "comment": comment}

    def add_field(
        self,
        table_name: str,
        field_name: str,
        field_type: str = "",
        primary_key: bool = False,
        nullable: bool = True,
        default: Any = None,
        autoincrement: bool = False,
        comment: str = "",
        examples: list | None = None,
        **kwargs: Any,
    ) -> None:
        self.tables[table_name]["fields"][field_name] = {
            "type": field_type,
            "primary_key": primary_key,
            "nullable": nullable,
            "default": default if default is None else f"{default}",
            "autoincrement": autoincrement,
            "comment": comment,
            "examples": (examples or []).copy(),
            **kwargs,
        }

    def add_foreign_key(
        self,
        table_name: str,
        field_name: str,
        ref_schema: str | None,
        ref_table_name: str,
        ref_field_name: str,
    ) -> None:
        self.foreign_keys.append([table_name, field_name, ref_schema, ref_table_name, ref_field_name])

    def get_field_type(self, field_type: str, simple_mode: bool = True) -> str:
        if not simple_mode:
            return field_type
        return field_type.split("(")[0]

    def single_table_mschema(
        self,
        table_name: str,
        selected_columns: list[str] | None = None,
        example_num: int = 3,
        show_type_detail: bool = False,
    ) -> str:
        table_info = self.tables.get(table_name, {})
        output: list[str] = []
        table_comment = table_info.get("comment") or ""
        if table_comment and table_comment != "None":
            if self.schema:
                output.append(f"# Table: {self.schema}.{table_name}, {table_comment}")
            else:
                output.append(f"# Table: {table_name}, {table_comment}")
        elif self.schema:
            output.append(f"# Table: {self.schema}.{table_name}")
        else:
            output.append(f"# Table: {table_name}")

        field_lines: list[str] = []
        for field_name, field_info in table_info.get("fields", {}).items():
            if selected_columns is not None and field_name.lower() not in selected_columns:
                continue

            raw_type = self.get_field_type(field_info["type"], not show_type_detail)
            field_line = f"({field_name}:{raw_type.upper()}"
            comment = (field_info.get("comment") or "").strip()
            if comment:
                field_line += f", {comment}"

            if field_info.get("primary_key"):
                field_line += ", Primary Key"

            examples = field_info.get("examples") or []
            if examples and example_num > 0:
                examples = [s for s in examples if s is not None]
                examples = examples_to_str(examples)
                if len(examples) > example_num:
                    examples = examples[:example_num]

                if raw_type.upper() in ("DATE", "TIME", "DATETIME", "TIMESTAMP") and examples:
                    examples = [examples[0]]
                elif examples and max(len(s) for s in examples) > 20:
                    examples = [examples[0]] if max(len(s) for s in examples) <= 50 else []

                if examples:
                    example_str = ", ".join(str(example) for example in examples)
                    field_line += f", Examples: [{example_str}]"

            field_line += ")"
            field_lines.append(field_line)

        output.append("[")
        output.append(",\n".join(field_lines))
        output.append("]")
        return "\n".join(output)

    def to_mschema(
        self,
        selected_tables: list[str] | None = None,
        selected_columns: list[str] | None = None,
        example_num: int = 3,
        show_type_detail: bool = False,
    ) -> str:
        output: list[str] = []
        output.append(f"【DB_ID】 {self.db_id}")
        output.append("【Schema】")

        if selected_tables is not None:
            selected_tables = [s.lower() for s in selected_tables]
        if selected_columns is not None:
            selected_columns = [s.lower() for s in selected_columns]
            selected_tables = [s.split(".")[0].lower() for s in selected_columns]

        for table_name in self.tables:
            if selected_tables is None or table_name.lower() in selected_tables:
                column_names = list(self.tables[table_name]["fields"].keys())
                if selected_columns is not None:
                    cur_selected = [
                        c.lower()
                        for c in column_names
                        if f"{table_name}.{c}".lower() in selected_columns
                    ]
                else:
                    cur_selected = None
                output.append(
                    self.single_table_mschema(
                        table_name, cur_selected, example_num, show_type_detail
                    )
                )

        if self.foreign_keys:
            output.append("【Foreign keys】")
            for fk in self.foreign_keys:
                table1, column1, ref_schema, table2, column2 = fk
                if selected_tables is None or (
                    table1.lower() in selected_tables and table2.lower() in selected_tables
                ):
                    if ref_schema == self.schema:
                        output.append(f"{table1}.{column1}={table2}.{column2}")

        return "\n".join(output)


# XiYan M-Schema README 官方 Prompt 模板
XIYAN_NL2SQL_PROMPT = """You are now a {dialect} data analyst, and you are given a database schema as follows:

【Schema】
{db_schema}

【Question】
{question}

【Evidence】
{evidence}

Please read and understand the database schema carefully, and generate an executable SQL based on the user's question and evidence. The generated SQL is protected by ```sql and ```.
"""


def build_nl2sql_prompt(
    *,
    dialect: str,
    db_schema: str,
    question: str,
    evidence: str = "",
) -> str:
    return XIYAN_NL2SQL_PROMPT.format(
        dialect=dialect,
        db_schema=db_schema,
        question=question,
        evidence=evidence or "",
    )


def _effective_desc(description: str | None, hive_comment: str | None) -> str:
    return (description or hive_comment or "").strip()


def _fetch_pk_columns(raw_engine: Engine, db_name: str, table_name: str) -> set[str]:
    try:
        inspector = inspect(raw_engine)
        pk = inspector.get_pk_constraint(table_name, schema=db_name)
        return set(pk.get("constrained_columns") or [])
    except Exception:
        return set()


def _fetch_unique_single_columns(raw_engine: Engine, db_name: str, table_name: str) -> set[str]:
    """单列 UNIQUE 索引，数仓表里常作业务唯一键。"""
    cols: set[str] = set()
    try:
        inspector = inspect(raw_engine)
        for uc in inspector.get_unique_constraints(table_name, schema=db_name):
            names = uc.get("column_names") or []
            if len(names) == 1:
                cols.add(names[0])
        for idx in inspector.get_indexes(table_name, schema=db_name):
            if idx.get("unique"):
                names = idx.get("column_names") or []
                if len(names) == 1:
                    cols.add(names[0])
    except Exception:
        pass
    return cols


def _fetch_join_key_columns(meta_engine: Engine, db_name: str, table_name: str) -> set[str]:
    """从 L1 table_relation 收集参与 JOIN 的列（无 PK 时最可靠）。"""
    cols: set[str] = set()
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT left_column FROM table_relation
                WHERE is_enabled = 1 AND left_db = :db AND left_table = :tbl
                UNION
                SELECT right_column FROM table_relation
                WHERE is_enabled = 1 AND right_db = :db AND right_table = :tbl
                """
            ),
            {"db": db_name, "tbl": table_name},
        ).fetchall()
    cols.update(r[0] for r in rows if r[0])
    return cols


def _infer_logical_keys(
    table_name: str,
    column_names: list[str],
    *,
    db_pk: set[str],
    unique_cols: set[str],
    join_cols: set[str],
) -> tuple[set[str], dict[str, str]]:
    """
    无物理 PK 时的逻辑键推断。
    返回 (key_columns, {col_name: tag})，tag 用于写入列注释后缀。
    """
    tags: dict[str, str] = {}
    if db_pk:
        for c in db_pk:
            tags[c] = "pk"
        return db_pk, tags

    if unique_cols:
        for c in unique_cols:
            tags[c] = "unique"
        return unique_cols, tags

    if join_cols:
        for c in join_cols:
            if c in column_names:
                tags[c] = "join"
        return {c for c in join_cols if c in column_names}, tags

    lower_map = {c.lower(): c for c in column_names}
    candidates: list[str] = []
    stem = table_name.lower()
    if stem.endswith("s") and len(stem) > 1:
        singular = stem[:-1]
    else:
        singular = stem
    for name in (f"{stem}_id", f"{singular}_id", "id"):
        if name in lower_map:
            candidates.append(lower_map[name])
    for c in column_names:
        if c.lower().endswith("_id") and c not in candidates:
            candidates.append(c)

    if len(candidates) == 1:
        tags[candidates[0]] = "heuristic"
        return {candidates[0]}, tags
    if candidates:
        tags[candidates[0]] = "heuristic"
        return {candidates[0]}, tags

    return set(), tags


_KEY_TAG_SUFFIX = {
    "pk": "",
    "unique": " [唯一键]",
    "join": " [关联键]",
    "heuristic": " [推断键]",
}


def _apply_key_hint(comment: str, tag: str | None, *, is_db_pk: bool) -> str:
    base = (comment or "").strip()
    if is_db_pk:
        return base
    if tag and tag != "pk":
        suffix = _KEY_TAG_SUFFIX.get(tag, "")
        if suffix and suffix not in base:
            return (base + suffix).strip()
    return base


def _fetch_column_examples(
    raw_engine: Engine,
    db_name: str,
    table_name: str,
    column_name: str,
    *,
    max_num: int = 5,
) -> list:
    """从业务库采样 distinct 值（表名/列名来自 L1，非用户输入）。"""
    q = text(
        f"""
        SELECT DISTINCT `{column_name}` AS v
        FROM `{db_name}`.`{table_name}`
        WHERE `{column_name}` IS NOT NULL AND CAST(`{column_name}` AS CHAR) != ''
        LIMIT :lim
        """
    )
    try:
        with raw_engine.connect() as conn:
            rows = conn.execute(q, {"lim": max_num}).fetchall()
        return [r[0] for r in rows if r[0] is not None and str(r[0]).strip()]
    except Exception:
        return []


@dataclass
class MSchemaSelection:
    db_name: str
    table_ids: set[str] = field(default_factory=set)
    column_ids: set[str] = field(default_factory=set)
    selected_tables: set[str] = field(default_factory=set)
    selected_columns: set[str] = field(default_factory=set)


def selection_from_hits(meta_engine: Engine, hits: list) -> MSchemaSelection:
    """从向量检索结果解析 M-Schema 子集范围。"""
    table_ids: set[str] = set()
    column_ids: set[str] = set()
    selected_tables: set[str] = set()
    selected_columns: set[str] = set()
    db_name = ""

    for hit in hits:
        payload = getattr(hit, "payload", None) or (hit.get("payload") if isinstance(hit, dict) else {}) or {}
        obj_type = payload.get("object_type") or payload.get("type")
        obj_id = payload.get("object_id")
        if payload.get("db"):
            db_name = str(payload["db"])

        if obj_type == "table" and obj_id:
            table_ids.add(obj_id)
            if payload.get("table"):
                selected_tables.add(str(payload["table"]))
        elif obj_type == "column" and obj_id:
            column_ids.add(obj_id)
            if payload.get("table") and payload.get("column"):
                tbl = str(payload["table"])
                col = str(payload["column"])
                selected_tables.add(tbl)
                selected_columns.add(f"{tbl}.{col}")
        elif obj_type == "join":
            for side in ("left", "right"):
                val = payload.get(side)
                if val and "." in str(val):
                    parts = str(val).split(".")
                    if len(parts) >= 2:
                        selected_tables.add(parts[-2])
            if payload.get("left_db"):
                db_name = str(payload["left_db"])

    if column_ids:
        with meta_engine.connect() as conn:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids)))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids)}
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.column_id, c.column_name, t.table_id, t.table_name, t.db_name
                    FROM column_meta c
                    JOIN table_meta t ON c.table_id = t.table_id
                    WHERE c.column_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            for _cid, col_name, tid, tbl_name, db in rows:
                table_ids.add(tid)
                selected_tables.add(tbl_name)
                selected_columns.add(f"{tbl_name}.{col_name}")
                if db:
                    db_name = str(db)

    if table_ids and not db_name:
        with meta_engine.connect() as conn:
            placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
            params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
            row = conn.execute(
                text(
                    f"""
                    SELECT db_name FROM table_meta
                    WHERE table_id IN ({placeholders})
                    LIMIT 1
                    """
                ),
                params,
            ).fetchone()
            if row:
                db_name = str(row[0])

    return MSchemaSelection(
        db_name=db_name,
        table_ids=table_ids,
        column_ids=column_ids,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
    )


def selection_from_column_hits(
    meta_engine: Engine,
    db_name: str,
    columns: list,
) -> MSchemaSelection:
    """从召回/精选 ColumnHit 解析 L1 id，供 M-Schema 只输出这些列。"""
    selected_tables: set[str] = set()
    selected_columns: set[str] = set()
    table_ids: set[str] = set()
    column_ids: set[str] = set()
    pairs: list[tuple[str, str]] = []

    for item in columns or []:
        if hasattr(item, "table"):
            tbl = str(getattr(item, "table", "") or "").strip()
            col = str(getattr(item, "column", "") or "").strip()
        elif isinstance(item, dict):
            tbl = str(item.get("table") or "").strip()
            col = str(item.get("column") or "").strip()
        else:
            continue
        if not tbl or not col:
            continue
        pairs.append((tbl, col))
        selected_tables.add(tbl)
        selected_columns.add(f"{tbl}.{col}")

    if not pairs or not db_name:
        return MSchemaSelection(
            db_name=db_name,
            table_ids=table_ids,
            column_ids=column_ids,
            selected_tables=selected_tables,
            selected_columns=selected_columns,
        )

    with meta_engine.connect() as conn:
        for tbl, col in pairs:
            row = conn.execute(
                text(
                    """
                    SELECT c.column_id, t.table_id, t.table_name, c.column_name
                    FROM column_meta c
                    JOIN table_meta t ON c.table_id = t.table_id
                    WHERE t.db_name = :db
                      AND LOWER(t.table_name) = LOWER(:tbl)
                      AND LOWER(c.column_name) = LOWER(:col)
                      AND c.is_enabled = 1 AND t.is_enabled = 1
                    LIMIT 1
                    """
                ),
                {"db": db_name, "tbl": tbl, "col": col},
            ).fetchone()
            if not row:
                continue
            column_ids.add(str(row[0]))
            table_ids.add(str(row[1]))
            selected_tables.add(str(row[2]))
            selected_columns.add(f"{row[2]}.{row[3]}")

    return MSchemaSelection(
        db_name=db_name,
        table_ids=table_ids,
        column_ids=column_ids,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
    )


def _pick_schema_columns(retrieval, schema_stage: str, *, column_select: bool):
    stage = (schema_stage or "s2").strip().lower()
    if stage == "pool" or not column_select:
        return list(getattr(retrieval, "columns", None) or []), stage
    if stage == "s1":
        cols = getattr(retrieval, "s1_columns", None) or []
        if cols:
            return list(cols), "s1"
    cols = getattr(retrieval, "s2_columns", None) or []
    if cols:
        return list(cols), "s2"
    cols = getattr(retrieval, "s1_columns", None) or []
    if cols:
        return list(cols), "s1"
    return list(getattr(retrieval, "columns", None) or []), "pool"


def load_mschema_from_l1(
    meta_engine: Engine,
    *,
    db_name: str,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    raw_engine: Engine | None = None,
    include_examples: bool = True,
    example_num: int = 3,
    include_relations: bool = True,
    infer_keys: bool = False,
    include_pk: bool = False,
) -> MSchema:
    """从 L1 元数据库构建 MSchema；PK/Examples 可选从业务库补充。

    include_pk=False（默认）时不输出 Primary Key，也不做逻辑键推断标注。
    """
    mschema = MSchema(db_id=db_name, schema=db_name)

    col_filter_ids: set[str] | None = set(column_ids) if column_ids else None
    if col_filter_ids:
        with meta_engine.connect() as conn:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids or [])))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids or [])}
            extra_tables = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT table_id FROM column_meta
                    WHERE column_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
        table_ids = list(set(table_ids or []) | {r[0] for r in extra_tables})

    table_filter = ""
    params: dict = {"db": db_name}
    if table_ids:
        placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
        params.update({f"t{i}": tid for i, tid in enumerate(table_ids)})
        table_filter = f"AND t.table_id IN ({placeholders})"

    with meta_engine.connect() as conn:
        table_rows = conn.execute(
            text(
                f"""
                SELECT t.table_id, t.table_name, t.description, t.cn_name, t.hive_comment
                FROM table_meta t
                WHERE t.is_enabled = 1 AND t.db_name = :db {table_filter}
                ORDER BY t.table_name
                """
            ),
            params,
        ).fetchall()

        for tid, table_name, desc, cn_name, hive in table_rows:
            comment = _effective_desc(desc, hive) or (cn_name or "").strip() or None
            mschema.add_table(table_name, comment=comment)

            pk_cols: set[str] = set()
            unique_cols: set[str] = set()
            join_cols: set[str] = set()
            if include_pk and raw_engine:
                pk_cols = _fetch_pk_columns(raw_engine, db_name, table_name)
            if include_pk and infer_keys and raw_engine:
                unique_cols = _fetch_unique_single_columns(raw_engine, db_name, table_name)
            if include_pk and infer_keys:
                join_cols = _fetch_join_key_columns(meta_engine, db_name, table_name)

            col_rows = conn.execute(
                text(
                    """
                    SELECT column_name, data_type, description, hive_comment
                    FROM column_meta
                    WHERE table_id = :tid AND is_enabled = 1
                    ORDER BY ordinal_pos, column_name
                    """
                ),
                {"tid": tid},
            ).fetchall()

            col_names = [r[0] for r in col_rows]
            logical_keys: set[str] = set()
            key_tags: dict[str, str] = {}
            if include_pk and infer_keys:
                _, key_tags = _infer_logical_keys(
                    table_name,
                    col_names,
                    db_pk=pk_cols,
                    unique_cols=unique_cols,
                    join_cols=join_cols,
                )

            for col_name, data_type, c_desc, c_hive in col_rows:
                if col_filter_ids:
                    col_id_row = conn.execute(
                        text(
                            """
                            SELECT column_id FROM column_meta
                            WHERE table_id = :tid AND column_name = :col
                            """
                        ),
                        {"tid": tid, "col": col_name},
                    ).fetchone()
                    if col_id_row and col_id_row[0] not in col_filter_ids:
                        continue

                comment_col = _effective_desc(c_desc, c_hive)
                is_db_pk = include_pk and col_name in pk_cols
                tag = key_tags.get(col_name) if include_pk else None
                comment_col = _apply_key_hint(comment_col, tag, is_db_pk=is_db_pk)
                examples: list = []
                if include_examples and raw_engine:
                    examples = _fetch_column_examples(
                        raw_engine, db_name, table_name, col_name, max_num=5
                    )

                mschema.add_field(
                    table_name,
                    col_name,
                    field_type=data_type or "VARCHAR",
                    primary_key=is_db_pk,
                    comment=comment_col,
                    examples=examples,
                )

        if include_relations:
            rel_filter = ""
            rel_params: dict = {"db": db_name}
            if table_ids:
                names = [r[1] for r in table_rows]
                if names:
                    name_ph = ", ".join(f":n{i}" for i in range(len(names)))
                    rel_params.update({f"n{i}": n for i, n in enumerate(names)})
                    rel_filter = f"""
                        AND left_table IN ({name_ph}) AND right_table IN ({name_ph})
                    """

            rel_rows = conn.execute(
                text(
                    f"""
                    SELECT left_table, left_column, right_db, right_table, right_column
                    FROM table_relation
                    WHERE is_enabled = 1
                      AND (left_db = :db OR right_db = :db)
                      {rel_filter}
                    """
                ),
                rel_params,
            ).fetchall()

            known_tables = set(mschema.tables.keys())
            for left_t, left_c, right_db, right_t, right_c in rel_rows:
                if left_t in known_tables and right_t in known_tables:
                    mschema.add_foreign_key(left_t, left_c, right_db, right_t, right_c)

    return mschema


def build_mschema_text(
    meta_engine: Engine,
    *,
    db_name: str,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    raw_engine: Engine | None = None,
    selection: MSchemaSelection | None = None,
    include_examples: bool = True,
    example_num: int = 3,
    include_relations: bool = True,
    infer_keys: bool = False,
    include_pk: bool = False,
) -> str:
    mschema = load_mschema_from_l1(
        meta_engine,
        db_name=db_name,
        table_ids=table_ids,
        column_ids=column_ids,
        raw_engine=raw_engine,
        include_examples=include_examples,
        example_num=example_num,
        include_relations=include_relations,
        infer_keys=infer_keys,
        include_pk=include_pk,
    )

    sel_tables = list(selection.selected_tables) if selection and selection.selected_tables else None
    sel_columns = list(selection.selected_columns) if selection and selection.selected_columns else None

    if not sel_tables and table_ids:
        sel_tables = list(mschema.tables.keys())

    return mschema.to_mschema(
        selected_tables=sel_tables,
        selected_columns=sel_columns,
        example_num=example_num,
    )


def build_mschema_from_question(
    meta_engine: Engine,
    raw_engine: Engine | None,
    *,
    question: str,
    evidence: str = "",
    qdrant_client,
    collection_name: str,
    embed_fn,
    db_names: list[str] | None = None,
    limit: int = 30,
    include_examples: bool = True,
    example_num: int = 3,
    include_relations: bool = True,
    infer_keys: bool = False,
    include_pk: bool = False,
    column_select: bool = True,
    keyword_mode: str | None = None,
    schema_stage: str = "s2",
) -> dict:
    """XiYan 式召回 → S1/S2 精选 → L1 构建 M-Schema 文本。"""
    from wenshu.services.schema_retrieval import retrieve_schema

    embed_text = question
    if evidence and str(evidence).strip():
        embed_text = f"{question}\n{evidence.strip()}"
    vector = embed_fn([embed_text], is_query=True)[0]
    retrieval = retrieve_schema(
        client=qdrant_client,
        collection_name=collection_name,
        meta_engine=meta_engine,
        query_vector=vector,
        question=question,
        evidence=evidence or "",
        db_names=db_names,
        vector_limit=max(limit, 50),
        max_output_columns=limit,
        column_select=column_select,
        keyword_mode=keyword_mode,
    )
    db_name = retrieval.db_name
    schema_cols, stage_used = _pick_schema_columns(
        retrieval, schema_stage, column_select=column_select
    )
    if schema_cols and db_name:
        selection = selection_from_column_hits(meta_engine, db_name, schema_cols)
    else:
        selection = selection_from_hits(meta_engine, retrieval.raw_hits)
        if retrieval.expanded_tables:
            for tbl in retrieval.expanded_tables:
                selection.selected_tables.add(tbl)
    db_name = db_name or selection.db_name
    if not db_name and db_names:
        db_name = db_names[0]
    if not db_name:
        from db_config import get_raw_database_name

        db_name = get_raw_database_name() or ""

    if not db_name:
        raise ValueError("无法确定 db_name，请先配置原始库连接或指定 db_names")

    table_ids = list(selection.table_ids) or None
    column_ids = list(selection.column_ids) or None

    if not table_ids and not column_ids:
        raise ValueError("向量检索未命中任何表/字段，无法构建 M-Schema")

    mschema_text = build_mschema_text(
        meta_engine,
        db_name=db_name,
        table_ids=table_ids,
        column_ids=column_ids,
        raw_engine=raw_engine,
        selection=selection,
        include_examples=include_examples,
        example_num=example_num,
        include_relations=include_relations,
        infer_keys=infer_keys,
        include_pk=include_pk,
    )

    def _dump_cols(cols):
        return [
            {
                "table": getattr(c, "table", ""),
                "column": getattr(c, "column", ""),
                "source": getattr(c, "source", ""),
            }
            for c in (cols or [])
        ]

    return {
        "db_name": db_name,
        "mschema": mschema_text,
        "retrieval_style": retrieval.retrieval_style,
        "query_keywords": retrieval.query_keywords,
        "keyword_source": retrieval.keyword_source,
        "schema_stage": stage_used,
        "column_select": bool(column_select),
        "selection_meta": dict(getattr(retrieval, "selection_meta", None) or {}),
        "selected_tables": list(retrieval.selected_tables or []),
        "expanded_tables": list(retrieval.expanded_tables or []),
        "selection": {
            "table_ids": table_ids,
            "column_ids": column_ids,
            "selected_tables": sorted(selection.selected_tables),
            "selected_columns": sorted(selection.selected_columns),
        },
        "column_count": len(schema_cols),
        "s1_columns": _dump_cols(getattr(retrieval, "s1_columns", None)),
        "s2_columns": _dump_cols(getattr(retrieval, "s2_columns", None)),
        "hit_count": len(retrieval.raw_hits),
        "hits": retrieval.preview_hits(limit=min(limit, 15)),
    }
