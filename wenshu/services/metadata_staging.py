"""扫描暂存、注释补全、审核通过后写入 L1。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from wenshu.services.comment_extract import load_all_ddl_comments
from wenshu.services.comment_llm import (
    generate_table_comments,
    generate_table_description_only,
    llm_available,
)
from wenshu.services.ids import make_column_id, make_table_id
from wenshu.services.stats import metadata_tables_exist
from wenshu.services.sync_mysql import (
    METADATA_TABLES,
    disable_missing_tables,
    discover_mysql_tables,
    purge_missing_tables,
)


def staging_tables_exist(meta_engine: Engine) -> bool:
    with meta_engine.connect() as conn:
        n = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_name = 'staging_table_meta'
                """
            )
        ).scalar()
    return int(n or 0) > 0


def _empty(s: str | None) -> bool:
    return not (s or "").strip()


def _llm_col_desc(gen: dict, column_name: str) -> str | None:
    cols = gen.get("columns") or {}
    if not isinstance(cols, dict):
        return None
    if column_name in cols and cols[column_name]:
        return str(cols[column_name]).strip()
    lower = column_name.lower()
    for key, val in cols.items():
        if str(key).lower() == lower and val:
            return str(val).strip()
    return None


def _pick_comment(*candidates: str | None) -> tuple[str | None, str | None]:
    """按优先级 ddl > schema > 返回 (text, source)。"""
    for src, val in candidates:
        if not _empty(val):
            return (val.strip(), src)
    return (None, None)


def _effective_description(description: str | None, hive_comment: str | None) -> str:
    return ((description or hive_comment or "")).strip()


def _resolve_l1_table(conn, db_name: str, table_name: str):
    return conn.execute(
        text(
            """
            SELECT table_id, description, cn_name, domain, sample_questions, hive_comment
            FROM table_meta
            WHERE db_name = :db AND table_name = :tbl AND is_enabled = 1
            """
        ),
        {"db": db_name, "tbl": table_name},
    ).fetchone()


def _resolve_match_status(*, has_l1: bool, comment_source: str | None) -> str:
    src = (comment_source or "").strip().lower()
    if src == "manual":
        return "manual"
    if src == "l1_mapped":
        return "l1_mapped"
    if src == "l1":
        return "inherited"
    return "inherited" if has_l1 else "new"


def merge_l1_into_staging(
    meta_engine: Engine,
    table_ids: list[str] | None = None,
) -> dict:
    """将 L1 同名表/字段说明合并进暂存（不覆盖 manual）。由用户在元数据编辑页手动触发。"""
    if not staging_tables_exist(meta_engine) or not metadata_tables_exist(meta_engine):
        return {
            "merged_tables": 0,
            "merged_columns": 0,
            "new_tables": 0,
            "new_columns": 0,
            "message": "staging 或 L1 未就绪",
        }

    table_filter = ""
    params: dict = {}
    if table_ids:
        placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
        params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
        table_filter = f"AND t.table_id IN ({placeholders})"

    merged_tables = 0
    merged_columns = 0
    new_tables = 0
    new_columns = 0

    with meta_engine.begin() as conn:
        staging_tables = conn.execute(
            text(
                f"""
                SELECT t.table_id, t.db_name, t.table_name, t.description, t.comment_source
                FROM staging_table_meta t
                WHERE 1=1 {table_filter}
                """
            ),
            params,
        ).fetchall()

        for st_tid, db_name, table_name, _st_desc, st_source in staging_tables:
            l1_row = _resolve_l1_table(conn, db_name, table_name)
            if l1_row:
                l1_desc = (l1_row.description or l1_row.hive_comment or "").strip()
                if l1_desc and (st_source or "").lower() != "manual":
                    conn.execute(
                        text(
                            """
                            UPDATE staging_table_meta
                            SET description = :d, comment_source = 'l1'
                            WHERE table_id = :tid
                            """
                        ),
                        {"d": l1_desc, "tid": st_tid},
                    )
                    merged_tables += 1
            else:
                new_tables += 1

            l1_col_map: dict[str, object] = {}
            if l1_row:
                l1_cols = conn.execute(
                    text(
                        """
                        SELECT column_name, description, hive_comment
                        FROM column_meta
                        WHERE table_id = :tid AND is_enabled = 1
                        """
                    ),
                    {"tid": l1_row.table_id},
                ).fetchall()
                l1_col_map = {r.column_name: r for r in l1_cols}

            st_cols = conn.execute(
                text(
                    """
                    SELECT column_id, column_name, description, comment_source
                    FROM staging_column_meta
                    WHERE table_id = :tid
                    """
                ),
                {"tid": st_tid},
            ).fetchall()

            for _cid, col_name, _c_desc, c_source in st_cols:
                l1_col = l1_col_map.get(col_name)
                if l1_col:
                    l1_c_desc = (l1_col.description or l1_col.hive_comment or "").strip()
                    if l1_c_desc and (c_source or "").lower() != "manual":
                        conn.execute(
                            text(
                                """
                                UPDATE staging_column_meta
                                SET description = :d, comment_source = 'l1'
                                WHERE column_id = :cid
                                """
                            ),
                            {"d": l1_c_desc, "cid": _cid},
                        )
                        merged_columns += 1
                else:
                    new_columns += 1

    return {
        "merged_tables": merged_tables,
        "merged_columns": merged_columns,
        "new_tables": new_tables,
        "new_columns": new_columns,
        "message": (
            f"已从 L1 合并 {merged_tables} 表、{merged_columns} 字段说明；"
            f"新增 {new_tables} 表、{new_columns} 字段待填写"
        ),
    }


def list_l1_orphan_columns(
    meta_engine: Engine,
    table_id: str,
) -> list[dict]:
    """L1 有、本次扫描暂存没有的字段（可能已删除或改名）。"""
    if not staging_tables_exist(meta_engine) or not metadata_tables_exist(meta_engine):
        return []
    with meta_engine.connect() as conn:
        st = conn.execute(
            text(
                """
                SELECT db_name, table_name FROM staging_table_meta WHERE table_id = :tid
                """
            ),
            {"tid": table_id},
        ).fetchone()
        if not st:
            return []
        staging_names = {
            r[0]
            for r in conn.execute(
                text("SELECT column_name FROM staging_column_meta WHERE table_id = :tid"),
                {"tid": table_id},
            ).fetchall()
        }
        l1_row = _resolve_l1_table(conn, st.db_name, st.table_name)
        if not l1_row:
            return []
        rows = conn.execute(
            text(
                """
                SELECT column_id, column_name, data_type, description, hive_comment
                FROM column_meta
                WHERE table_id = :tid AND is_enabled = 1
                ORDER BY ordinal_pos, column_name
                """
            ),
            {"tid": l1_row.table_id},
        ).fetchall()

    orphans = []
    for r in rows:
        if r.column_name in staging_names:
            continue
        orphans.append(
            {
                "column_id": r.column_id,
                "column_name": r.column_name,
                "data_type": r.data_type,
                "description": _effective_description(r.description, r.hive_comment) or None,
            }
        )
    return orphans


def inherit_l1_column_to_staging(
    meta_engine: Engine,
    staging_column_id: str,
    l1_column_id: str,
) -> dict:
    """手动：将 L1 字段说明复制到暂存区指定字段（用于改名映射）。"""
    with meta_engine.begin() as conn:
        st_col = conn.execute(
            text(
                """
                SELECT c.column_id, c.column_name, t.db_name, t.table_name
                FROM staging_column_meta c
                JOIN staging_table_meta t ON t.table_id = c.table_id
                WHERE c.column_id = :cid
                """
            ),
            {"cid": staging_column_id},
        ).fetchone()
        if not st_col:
            raise ValueError("暂存字段不存在")

        l1_col = conn.execute(
            text(
                """
                SELECT c.column_id, c.column_name, c.description, c.hive_comment,
                       t.db_name, t.table_name
                FROM column_meta c
                JOIN table_meta t ON t.table_id = c.table_id
                WHERE c.column_id = :cid AND c.is_enabled = 1 AND t.is_enabled = 1
                """
            ),
            {"cid": l1_column_id},
        ).fetchone()
        if not l1_col:
            raise ValueError("L1 字段不存在或已禁用")
        if (l1_col.db_name, l1_col.table_name) != (st_col.db_name, st_col.table_name):
            raise ValueError("只能映射同一张业务表下的字段")

        desc = _effective_description(l1_col.description, l1_col.hive_comment)
        if _empty(desc):
            raise ValueError("L1 字段没有可继承的说明")

        conn.execute(
            text(
                """
                UPDATE staging_column_meta
                SET description = :d, comment_source = 'l1_mapped'
                WHERE column_id = :cid
                """
            ),
            {"d": desc.strip(), "cid": staging_column_id},
        )

    return {
        "ok": True,
        "staging_column_id": staging_column_id,
        "from_l1_column_id": l1_column_id,
        "from_column_name": l1_col.column_name,
        "to_column_name": st_col.column_name,
        "message": f"已将 L1 字段 {l1_col.column_name} 的说明应用到 {st_col.column_name}",
    }


def materialize_staging_comments(
    meta_engine: Engine,
    table_id: str | None = None,
    *,
    apply_ddl: bool = False,
) -> None:
    """将 hive_comment / DDL 建表注释写入 description（不覆盖 manual）。

    apply_ddl 仅应在扫描或显式「提取 DDL」时开启；列表加载时跳过以免逐行 UPDATE 拖慢页面。
    """
    if apply_ddl:
        apply_ddl_to_staging(meta_engine)
    table_filter = "AND table_id = :tid" if table_id else ""
    col_filter = "AND table_id = :tid" if table_id else ""
    params = {"tid": table_id} if table_id else {}
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                f"""
                UPDATE staging_table_meta
                SET description = hive_comment,
                    comment_source = COALESCE(comment_source, 'schema')
                WHERE (description IS NULL OR description = '')
                  AND hive_comment IS NOT NULL AND TRIM(hive_comment) != ''
                  AND (comment_source IS NULL OR comment_source != 'manual')
                  {table_filter}
                """
            ),
            params,
        )
        conn.execute(
            text(
                f"""
                UPDATE staging_column_meta
                SET description = hive_comment,
                    comment_source = COALESCE(comment_source, 'schema')
                WHERE (description IS NULL OR description = '')
                  AND hive_comment IS NOT NULL AND TRIM(hive_comment) != ''
                  AND (comment_source IS NULL OR comment_source != 'manual')
                  {col_filter}
                """
            ),
            params,
        )
        conn.execute(
            text(
                f"""
                UPDATE staging_table_meta
                SET comment_source = COALESCE(comment_source, 'schema')
                WHERE comment_source IS NULL
                  AND description IS NOT NULL AND TRIM(description) != ''
                  {table_filter}
                """
            ),
            params,
        )
        conn.execute(
            text(
                f"""
                UPDATE staging_column_meta
                SET comment_source = COALESCE(comment_source, 'schema')
                WHERE comment_source IS NULL
                  AND description IS NOT NULL AND TRIM(description) != ''
                  {col_filter}
                """
            ),
            params,
        )


def scan_to_staging(
    raw_engine: Engine,
    meta_engine: Engine,
    apply_ddl: bool = False,
    apply_llm: bool = False,
    include_tables: list[str] | None = None,
) -> dict:
    """从业务库扫描结构写入 staging。源库 COMMENT 默认填入 description，hive_comment 作备份。"""
    if not staging_tables_exist(meta_engine):
        raise RuntimeError("staging 表不存在，请先执行「初始化元数据表」")

    tables, columns = discover_mysql_tables(raw_engine, include_tables=include_tables)
    ddl_map = load_all_ddl_comments()
    now = datetime.now()

    cleared_columns = 0
    cleared_tables = 0
    with meta_engine.begin() as conn:
        cleared_columns = int(conn.execute(text("DELETE FROM staging_column_meta")).rowcount or 0)
        cleared_tables = int(conn.execute(text("DELETE FROM staging_table_meta")).rowcount or 0)

    cols_by_table: dict[tuple[str, str], list] = {}
    for c in columns:
        key = (c.db_name, c.table_name)
        cols_by_table.setdefault(key, []).append(c)

    for t in tables:
        key = (t.db_name, t.table_name)
        table_id = make_table_id(t.db_name, t.table_name)
        ddl = ddl_map.get(t.table_name.upper(), {})
        schema_comment = (t.comment or "").strip() or None
        ddl_table_comment = (ddl.get("table_comment") or "").strip() or None
        table_desc, table_source = _pick_comment(
            ("ddl", ddl_table_comment),
            ("schema", schema_comment),
        )
        table_hive = schema_comment or ddl_table_comment

        with meta_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO staging_table_meta
                        (table_id, db_name, table_name, description, hive_comment,
                         comment_source, scanned_at)
                    VALUES
                        (:table_id, :db_name, :table_name, :description, :hive_comment,
                         :comment_source, :scanned_at)
                    """
                ),
                {
                    "table_id": table_id,
                    "db_name": t.db_name,
                    "table_name": t.table_name,
                    "description": table_desc,
                    "hive_comment": table_hive,
                    "comment_source": table_source,
                    "scanned_at": now,
                },
            )
            ddl_cols = ddl.get("columns") or {}
            for c in cols_by_table.get(key, []):
                col_schema = (c.comment or "").strip() or None
                ddl_col = (ddl_cols.get(c.column_name.lower()) or "").strip() or None
                col_desc, col_source = _pick_comment(
                    ("ddl", ddl_col),
                    ("schema", col_schema),
                )
                col_hive = col_schema or ddl_col
                conn.execute(
                    text(
                        """
                        INSERT INTO staging_column_meta
                            (column_id, table_id, column_name, data_type, description,
                             hive_comment, comment_source, ordinal_pos)
                        VALUES
                            (:column_id, :table_id, :column_name, :data_type, :description,
                             :hive_comment, :comment_source, :ordinal_pos)
                        """
                    ),
                    {
                        "column_id": make_column_id(table_id, c.column_name),
                        "table_id": table_id,
                        "column_name": c.column_name,
                        "data_type": c.data_type,
                        "description": col_desc,
                        "hive_comment": col_hive,
                        "comment_source": col_source,
                        "ordinal_pos": c.ordinal_pos,
                    },
                )

    materialize_staging_comments(meta_engine, apply_ddl=True)

    llm_stats = {"tables_processed": 0, "filled": 0}
    if apply_llm:
        llm_stats = apply_llm_to_staging(meta_engine)

    stats = get_staging_stats(meta_engine)
    return {
        "tables_scanned": len(tables),
        "columns_scanned": len(columns),
        "scope": "partial" if include_tables else "full",
        "include_tables": include_tables or [],
        "staging_cleared_tables": cleared_tables,
        "staging_cleared_columns": cleared_columns,
        "llm": llm_stats,
        **stats,
    }


def apply_ddl_to_staging(meta_engine: Engine) -> dict:
    """对 staging 中仍缺注释的项，用 sql/raw DDL 补全（不覆盖 manual）。"""
    ddl_map = load_all_ddl_comments()
    updated = 0
    with meta_engine.begin() as conn:
        rows = conn.execute(
            text("SELECT table_id, table_name, description, comment_source FROM staging_table_meta")
        ).fetchall()
        for table_id, table_name, desc, source in rows:
            ddl = ddl_map.get(table_name.upper(), {})
            if source == "manual":
                continue
            if _empty(desc) and ddl.get("table_comment"):
                conn.execute(
                    text(
                        """
                        UPDATE staging_table_meta
                        SET description = :d,
                            hive_comment = COALESCE(hive_comment, :d),
                            comment_source = 'ddl'
                        WHERE table_id = :tid
                        """
                    ),
                    {"d": ddl["table_comment"], "tid": table_id},
                )
                updated += 1

        col_rows = conn.execute(
            text(
                """
                SELECT c.column_id, t.table_name, c.column_name, c.description, c.comment_source
                FROM staging_column_meta c
                JOIN staging_table_meta t ON t.table_id = c.table_id
                """
            )
        ).fetchall()
        for col_id, table_name, col_name, desc, source in col_rows:
            if source == "manual":
                continue
            ddl = ddl_map.get(table_name.upper(), {})
            ddl_col = (ddl.get("columns") or {}).get(col_name.lower())
            if _empty(desc) and ddl_col:
                conn.execute(
                    text(
                        """
                        UPDATE staging_column_meta
                        SET description = :d,
                            hive_comment = COALESCE(hive_comment, :d),
                            comment_source = 'ddl'
                        WHERE column_id = :cid
                        """
                    ),
                    {"d": ddl_col, "cid": col_id},
                )
                updated += 1
    return {"updated": updated, **get_staging_stats(meta_engine)}


def _table_need_llm(
    table_desc: str | None,
    table_hive_comment: str | None,
    *,
    overwrite: bool,
    empty_only: bool,
) -> bool:
    eff = _effective_description(table_desc, table_hive_comment)
    if empty_only:
        return _empty(eff)
    return overwrite or _empty(table_desc)


def count_llm_pending(
    meta_engine: Engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    overwrite: bool = False,
    table_only: bool = False,
    columns_only: bool = False,
    empty_only: bool = False,
) -> dict:
    """统计 AI 补全待处理数量（与 apply_llm_to_staging 规则一致）。"""
    if not staging_tables_exist(meta_engine):
        return {
            "pending_tables": 0,
            "pending_columns": 0,
            "pending_total": 0,
            "message": "请先执行「扫描原始表」",
        }

    target_column_ids: set[str] | None = set(column_ids) if column_ids else None
    pending_tables = 0
    pending_columns = 0

    with meta_engine.connect() as conn:
        if target_column_ids:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids or [])))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids or [])}
            col_rows = conn.execute(
                text(
                    f"""
                    SELECT c.column_id, c.table_id
                    FROM staging_column_meta c
                    WHERE c.column_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            table_ids_from_cols = list({r[1] for r in col_rows})
            if table_ids:
                table_ids = list(set(table_ids) | set(table_ids_from_cols))
            else:
                table_ids = table_ids_from_cols

        if table_ids:
            placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
            params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
            tables = conn.execute(
                text(
                    f"""
                    SELECT table_id, db_name, table_name, description, hive_comment
                    FROM staging_table_meta
                    WHERE table_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
        else:
            tables = conn.execute(
                text(
                    "SELECT table_id, db_name, table_name, description, hive_comment "
                    "FROM staging_table_meta"
                )
            ).fetchall()

    for table_id, _db_name, _table_name, table_desc, table_hive_comment in tables:
        with meta_engine.connect() as conn:
            cols = conn.execute(
                text(
                    """
                    SELECT column_id, column_name, data_type, description, hive_comment,
                           comment_source
                    FROM staging_column_meta
                    WHERE table_id = :tid
                    ORDER BY ordinal_pos
                    """
                ),
                {"tid": table_id},
            ).fetchall()

        col_dicts = [
            {
                "column_id": r[0],
                "column_name": r[1],
                "data_type": r[2],
                "description": r[3],
                "hive_comment": r[4],
                "comment_source": r[5],
            }
            for r in cols
        ]

        def _need_col(c: dict) -> bool:
            if target_column_ids is not None and c["column_id"] not in target_column_ids:
                return False
            if table_only:
                return False
            if (c.get("comment_source") or "") == "manual":
                return False
            if empty_only:
                return _empty(_effective_description(c.get("description"), c.get("hive_comment")))
            if overwrite:
                return True
            if _empty(c["description"]):
                return True
            return (c.get("comment_source") or "") == "schema"

        if table_only:
            if _table_need_llm(
                table_desc, table_hive_comment, overwrite=overwrite, empty_only=empty_only
            ):
                pending_tables += 1
        elif columns_only:
            col_pending = sum(1 for c in col_dicts if _need_col(c))
            pending_columns += col_pending
            if col_pending > 0 and not table_ids:
                pending_tables += 1
        else:
            if target_column_ids is None and _table_need_llm(
                table_desc, table_hive_comment, overwrite=overwrite, empty_only=empty_only
            ):
                pending_tables += 1
            pending_columns += sum(1 for c in col_dicts if _need_col(c))

    if table_only:
        pending_total = pending_tables
        message = (
            f"待补全 {pending_tables} 张表注释"
            if pending_tables
            else "所有表注释已齐全，无需补全"
        )
    elif columns_only and table_ids and len(table_ids) == 1:
        pending_total = pending_columns
        message = (
            f"待补全 {pending_columns} 个字段注释"
            if pending_columns
            else "当前表字段注释已齐全，无需补全"
        )
    elif columns_only:
        pending_total = pending_tables
        message = (
            f"待补全 {pending_tables} 张表的字段注释（共 {pending_columns} 个字段）"
            if pending_tables
            else "所有表的字段注释已齐全，无需补全"
        )
    else:
        pending_total = pending_tables + pending_columns
        message = (
            f"待补全 {pending_tables} 张表、{pending_columns} 个字段"
            if pending_total
            else "注释已齐全，无需补全"
        )

    return {
        "pending_tables": pending_tables,
        "pending_columns": pending_columns,
        "pending_total": pending_total,
        "message": message,
    }


def save_all_nonempty_staging_comments(
    meta_engine: Engine,
    *,
    current_table_id: str | None = None,
    current_table_description: str | None = None,
    current_columns: list[dict] | None = None,
) -> dict:
    """全库保存非空注释：先应用当前表 UI 编辑，再批量固化 hive_comment → description。"""
    if not staging_tables_exist(meta_engine):
        raise ValueError("请先执行「扫描原始表」")

    if current_table_id and (current_table_description or "").strip():
        update_staging_table(meta_engine, current_table_id, current_table_description.strip())

    col_batch_result = None
    if current_table_id and current_columns:
        nonempty_cols = [
            c for c in current_columns if (c.get("description") or "").strip()
        ]
        if nonempty_cols:
            col_batch_result = update_staging_columns_batch(
                meta_engine, current_table_id, nonempty_cols
            )

    with meta_engine.begin() as conn:
        newly_tables = int(
            conn.execute(
                text(
                    """
                    UPDATE staging_table_meta
                    SET description = TRIM(hive_comment),
                        comment_source = COALESCE(comment_source, 'schema')
                    WHERE (description IS NULL OR TRIM(description) = '')
                      AND hive_comment IS NOT NULL AND TRIM(hive_comment) != ''
                      AND (comment_source IS NULL OR comment_source != 'manual')
                    """
                )
            ).rowcount
            or 0
        )
        newly_columns = int(
            conn.execute(
                text(
                    """
                    UPDATE staging_column_meta
                    SET description = TRIM(hive_comment),
                        comment_source = COALESCE(comment_source, 'schema')
                    WHERE (description IS NULL OR TRIM(description) = '')
                      AND hive_comment IS NOT NULL AND TRIM(hive_comment) != ''
                      AND (comment_source IS NULL OR comment_source != 'manual')
                    """
                )
            ).rowcount
            or 0
        )
        saved_tables = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM staging_table_meta
                    WHERE TRIM(COALESCE(description, '')) != ''
                    """
                )
            ).scalar()
            or 0
        )
        saved_columns = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM staging_column_meta
                    WHERE TRIM(COALESCE(description, '')) != ''
                    """
                )
            ).scalar()
            or 0
        )
        skipped_tables = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM staging_table_meta
                    WHERE COALESCE(NULLIF(TRIM(description), ''), NULLIF(TRIM(hive_comment), '')) IS NULL
                    """
                )
            ).scalar()
            or 0
        )
        skipped_columns = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM staging_column_meta
                    WHERE COALESCE(NULLIF(TRIM(description), ''), NULLIF(TRIM(hive_comment), '')) IS NULL
                    """
                )
            ).scalar()
            or 0
        )

    parts = [f"暂存区共 {saved_tables} 表、{saved_columns} 字段有非空注释"]
    if newly_tables or newly_columns:
        parts.append(f"本次新固化 {newly_tables} 表、{newly_columns} 字段")
    if skipped_tables or skipped_columns:
        parts.append(f"跳过空说明 {skipped_tables} 表、{skipped_columns} 字段")

    result = {
        "saved_tables": saved_tables,
        "saved_columns": saved_columns,
        "newly_materialized_tables": newly_tables,
        "newly_materialized_columns": newly_columns,
        "skipped_tables": skipped_tables,
        "skipped_columns": skipped_columns,
        "message": "；".join(parts),
        **get_staging_stats(meta_engine),
    }
    if col_batch_result:
        result["current_table_batch"] = col_batch_result
    return result


def apply_llm_to_staging(
    meta_engine: Engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    overwrite: bool = False,
    table_only: bool = False,
    columns_only: bool = False,
    empty_only: bool = False,
    set_progress: Callable[..., None] | None = None,
) -> dict:
    if not llm_available():
        raise RuntimeError("未配置 LLM（本地 Ollama 或线上 API），无法自动生成注释")

    pending = count_llm_pending(
        meta_engine,
        table_ids=table_ids,
        column_ids=column_ids,
        overwrite=overwrite,
        table_only=table_only,
        columns_only=columns_only,
        empty_only=empty_only,
    )
    if pending["pending_total"] == 0:
        return {
            "tables_processed": 0,
            "filled": 0,
            "errors": [],
            "skipped": True,
            "message": pending["message"],
            **pending,
            **get_staging_stats(meta_engine),
        }

    sp = set_progress or (lambda **kwargs: None)
    target_column_ids: set[str] | None = set(column_ids) if column_ids else None
    processed = 0
    filled = 0
    errors: list[str] = []
    pending_total = pending["pending_total"]

    sp(
        pct=0,
        message=f"0/{pending_total}：准备 AI 补全。成功：0；失败：0",
        done=0,
        total=pending_total,
    )

    with meta_engine.connect() as conn:
        if target_column_ids:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids or [])))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids or [])}
            col_rows = conn.execute(
                text(
                    f"""
                    SELECT c.column_id, c.table_id, t.db_name, t.table_name
                    FROM staging_column_meta c
                    JOIN staging_table_meta t ON c.table_id = t.table_id
                    WHERE c.column_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            table_ids_from_cols = list({r[1] for r in col_rows})
            if table_ids:
                table_ids = list(set(table_ids) | set(table_ids_from_cols))
            else:
                table_ids = table_ids_from_cols

        if table_ids:
            placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
            params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
            tables = conn.execute(
                text(
                    f"""
                    SELECT table_id, db_name, table_name, description, hive_comment
                    FROM staging_table_meta
                    WHERE table_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
        else:
            tables = conn.execute(
                text(
                    "SELECT table_id, db_name, table_name, description, hive_comment "
                    "FROM staging_table_meta"
                )
            ).fetchall()

    fail_count = 0
    success_count = 0
    done_units = 0

    for table_id, db_name, table_name, table_desc, table_hive_comment in tables:
        with meta_engine.connect() as conn:
            cols = conn.execute(
                text(
                    """
                    SELECT column_id, column_name, data_type, description, hive_comment,
                           comment_source
                    FROM staging_column_meta
                    WHERE table_id = :tid
                    ORDER BY ordinal_pos
                    """
                ),
                {"tid": table_id},
            ).fetchall()

        col_dicts = [
            {
                "column_id": r[0],
                "column_name": r[1],
                "data_type": r[2],
                "description": r[3],
                "hive_comment": r[4],
                "comment_source": r[5],
            }
            for r in cols
        ]

        def _need_col(c: dict) -> bool:
            if target_column_ids is not None and c["column_id"] not in target_column_ids:
                return False
            if table_only:
                return False
            if (c.get("comment_source") or "") == "manual":
                return False
            if empty_only:
                return _empty(_effective_description(c.get("description"), c.get("hive_comment")))
            if overwrite:
                return True
            src = (c.get("comment_source") or "")
            if _empty(c["description"]):
                return True
            return src == "schema"

        eff_table_desc = _effective_description(table_desc, table_hive_comment)
        if table_only:
            if empty_only:
                need_table = _empty(eff_table_desc)
            else:
                need_table = overwrite or _empty(table_desc)
            need_cols = False
        elif columns_only:
            need_table = False
            need_cols = any(_need_col(c) for c in col_dicts)
        else:
            if empty_only:
                need_table = target_column_ids is None and _empty(eff_table_desc)
            else:
                need_table = target_column_ids is None and (overwrite or _empty(table_desc))
            need_cols = any(_need_col(c) for c in col_dicts)
        if not need_table and not need_cols:
            continue

        work_units = 0
        if table_only or (need_table and not columns_only):
            work_units += 1 if need_table else 0
        if need_cols:
            work_units += sum(1 for c in col_dicts if _need_col(c))
        if work_units == 0:
            continue

        sp(
            pct=int(100 * done_units / max(pending_total, 1)),
            message=(
                f"{done_units}/{pending_total}：当前执行表 {table_name}。"
                f"成功：{success_count}；失败：{fail_count}"
            ),
            done=done_units,
            total=pending_total,
        )

        try:
            if table_only:
                table_desc = generate_table_description_only(
                    db_name,
                    table_name,
                    col_dicts,
                    existing_desc=table_desc,
                    hive_comment=table_hive_comment,
                )
                gen = {"table_description": table_desc, "columns": {}}
            else:
                gen = generate_table_comments(
                    db_name,
                    table_name,
                    [c for c in col_dicts if _need_col(c)] if not table_only else col_dicts,
                    schema_columns=col_dicts,
                    need_table_desc=bool(need_table),
                    existing_table_desc=table_desc,
                    table_hive_comment=table_hive_comment,
                )
        except Exception as exc:
            errors.append(f"{table_name}: {exc}")
            fail_count += 1
            done_units += work_units
            sp(
                pct=int(100 * done_units / max(pending_total, 1)),
                message=(
                    f"{done_units}/{pending_total}：表 {table_name} 失败。"
                    f"成功：{success_count}；失败：{fail_count}"
                ),
                done=done_units,
                total=pending_total,
            )
            continue
        processed += 1
        success_count += 1
        done_units += work_units

        with meta_engine.begin() as conn:
            if need_table and gen.get("table_description"):
                if overwrite:
                    conn.execute(
                        text(
                            """
                            UPDATE staging_table_meta
                            SET description = :d, comment_source = 'llm'
                            WHERE table_id = :tid
                            """
                        ),
                        {"d": gen["table_description"], "tid": table_id},
                    )
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE staging_table_meta
                            SET description = :d, comment_source = 'llm'
                            WHERE table_id = :tid AND (description IS NULL OR description = '')
                            """
                        ),
                        {"d": gen["table_description"], "tid": table_id},
                    )
                filled += 1

            for c in col_dicts:
                if not _need_col(c):
                    continue
                new_desc = _llm_col_desc(gen, c["column_name"])
                if new_desc:
                    if overwrite:
                        result = conn.execute(
                            text(
                                """
                                UPDATE staging_column_meta
                                SET description = :d, comment_source = 'llm'
                                WHERE column_id = :cid
                                """
                            ),
                            {"d": new_desc, "cid": c["column_id"]},
                        )
                    else:
                        result = conn.execute(
                            text(
                                """
                                UPDATE staging_column_meta
                                SET description = :d, comment_source = 'llm'
                                WHERE column_id = :cid
                                  AND (description IS NULL OR description = '')
                                  AND (comment_source IS NULL OR comment_source != 'manual')
                                """
                            ),
                            {"d": new_desc, "cid": c["column_id"]},
                        )
                    if result.rowcount:
                        filled += 1

        sp(
            pct=int(100 * done_units / max(pending_total, 1)),
            message=(
                f"{done_units}/{pending_total}：表 {table_name} 完成。"
                f"成功：{success_count}；失败：{fail_count}"
            ),
            done=done_units,
            total=pending_total,
        )

    sp(
        pct=100,
        message=f"完成。成功：{success_count}；失败：{fail_count}",
        done=pending_total,
        total=pending_total,
    )

    result = {
        "tables_processed": processed,
        "filled": filled,
        "errors": errors,
        "success_count": success_count,
        "fail_count": fail_count,
        **pending,
        **get_staging_stats(meta_engine),
    }
    if processed > 0 and filled == 0 and not errors:
        result["message"] = "AI 已调用但未返回可写入的表/字段说明，请确认 Ollama 运行中并重试"
    if errors and processed == 0:
        raise RuntimeError("LLM 补全失败：\n" + "\n".join(errors[:5]))
    return result


def _column_need_llm(c: dict, *, overwrite: bool, empty_only: bool) -> bool:
    if (c.get("comment_source") or "") == "manual":
        return False
    if empty_only:
        return _empty(_effective_description(c.get("description"), c.get("hive_comment")))
    if overwrite:
        return True
    if _empty(c.get("description")):
        return True
    return (c.get("comment_source") or "") == "schema"


def _persist_column_llm_results(
    meta_engine: Engine,
    col_dicts: list[dict],
    gen: dict,
    *,
    need_col,
    overwrite: bool,
) -> int:
    filled = 0
    with meta_engine.begin() as conn:
        for c in col_dicts:
            if not need_col(c):
                continue
            new_desc = _llm_col_desc(gen, c["column_name"])
            if not new_desc:
                continue
            if overwrite:
                result = conn.execute(
                    text(
                        """
                        UPDATE staging_column_meta
                        SET description = :d, comment_source = 'llm'
                        WHERE column_id = :cid
                        """
                    ),
                    {"d": new_desc, "cid": c["column_id"]},
                )
            else:
                result = conn.execute(
                    text(
                        """
                        UPDATE staging_column_meta
                        SET description = :d, comment_source = 'llm'
                        WHERE column_id = :cid
                          AND (description IS NULL OR description = '')
                          AND (comment_source IS NULL OR comment_source != 'manual')
                        """
                    ),
                    {"d": new_desc, "cid": c["column_id"]},
                )
            if result.rowcount:
                filled += 1
    return filled


def _llm_fill_columns_one_table(
    meta_engine: Engine,
    table_id: str,
    db_name: str,
    table_name: str,
    col_dicts: list[dict],
    *,
    table_hive_comment: str | None = None,
    table_description: str | None = None,
    overwrite: bool,
    empty_only: bool,
    per_table_timeout: int,
) -> int:
    need_col = lambda c: _column_need_llm(c, overwrite=overwrite, empty_only=empty_only)
    targets = [c for c in col_dicts if need_col(c)]
    if not targets:
        return 0
    gen = generate_table_comments(
        db_name,
        table_name,
        targets,
        schema_columns=col_dicts,
        table_hive_comment=table_hive_comment,
        existing_table_desc=table_description,
        need_table_desc=False,
        timeout=per_table_timeout,
    )
    return _persist_column_llm_results(
        meta_engine,
        col_dicts,
        gen,
        need_col=need_col,
        overwrite=overwrite,
    )


def apply_llm_all_columns_by_table(
    meta_engine: Engine,
    *,
    overwrite: bool = False,
    empty_only: bool = False,
    per_table_timeout: int = 360,
    set_progress: Callable[..., None] | None = None,
) -> dict:
    """逐表 AI 补全字段注释；单表超时跳过并继续。"""
    if not llm_available():
        raise RuntimeError("未配置 LLM（本地 Ollama 或线上 API），无法自动生成注释")

    sp = set_progress or (lambda **kwargs: None)
    with meta_engine.connect() as conn:
        tables = conn.execute(
            text(
                """
                SELECT table_id, db_name, table_name, description, hive_comment
                FROM staging_table_meta
                ORDER BY table_name
                """
            )
        ).fetchall()

    work_tables: list[tuple] = []
    for table_id, db_name, table_name, table_desc, table_hive_comment in tables:
        with meta_engine.connect() as conn:
            cols = conn.execute(
                text(
                    """
                    SELECT column_id, column_name, data_type, description, hive_comment,
                           comment_source
                    FROM staging_column_meta
                    WHERE table_id = :tid
                    ORDER BY ordinal_pos
                    """
                ),
                {"tid": table_id},
            ).fetchall()
        col_dicts = [
            {
                "column_id": r[0],
                "column_name": r[1],
                "data_type": r[2],
                "description": r[3],
                "hive_comment": r[4],
                "comment_source": r[5],
            }
            for r in cols
        ]
        if any(_column_need_llm(c, overwrite=overwrite, empty_only=empty_only) for c in col_dicts):
            work_tables.append((table_id, db_name, table_name, table_desc, table_hive_comment, col_dicts))

    total = len(work_tables)
    if total == 0:
        pending = count_llm_pending(
            meta_engine, columns_only=True, overwrite=overwrite, empty_only=empty_only
        )
        return {
            "mode": "all_columns_by_table",
            "total_tables": 0,
            "success_count": 0,
            "fail_count": 0,
            "skipped_no_work": len(tables),
            "filled": 0,
            "failed_tables": [],
            "errors": [],
            "skipped": True,
            "message": pending["message"],
            **pending,
            **get_staging_stats(meta_engine),
        }

    success_count = 0
    fail_count = 0
    filled_total = 0
    failed_tables: list[dict] = []

    sp(pct=0, message=f"0/{total}：准备逐表补全字段。成功：0；失败：0", done=0, total=total)

    for idx, (table_id, db_name, table_name, table_desc, table_hive_comment, col_dicts) in enumerate(
        work_tables, 1
    ):
        sp(
            pct=int(100 * (idx - 1) / max(total, 1)),
            message=(
                f"{idx - 1}/{total}：当前执行表 {table_name}。"
                f"成功：{success_count}；失败：{fail_count}"
            ),
            done=idx - 1,
            total=total,
        )

        def _work() -> int:
            return _llm_fill_columns_one_table(
                meta_engine,
                table_id,
                db_name,
                table_name,
                col_dicts,
                table_hive_comment=table_hive_comment,
                table_description=table_desc,
                overwrite=overwrite,
                empty_only=empty_only,
                per_table_timeout=per_table_timeout,
            )

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                filled = pool.submit(_work).result(timeout=per_table_timeout)
            filled_total += filled
            success_count += 1
        except FuturesTimeoutError:
            fail_count += 1
            failed_tables.append(
                {
                    "table_id": table_id,
                    "table_name": table_name,
                    "error": f"超时（{per_table_timeout} 秒）",
                }
            )
        except Exception as exc:
            fail_count += 1
            failed_tables.append(
                {"table_id": table_id, "table_name": table_name, "error": str(exc)}
            )

        sp(
            pct=int(100 * idx / max(total, 1)),
            message=(
                f"{idx}/{total}：表 {table_name} 完成。"
                f"成功：{success_count}；失败：{fail_count}"
            ),
            done=idx,
            total=total,
        )

    sp(
        pct=100,
        message=f"完成。成功：{success_count}；失败：{fail_count}",
        done=total,
        total=total,
    )

    pending = count_llm_pending(
        meta_engine, columns_only=True, overwrite=overwrite, empty_only=empty_only
    )
    result = {
        "mode": "all_columns_by_table",
        "total_tables": total,
        "success_count": success_count,
        "fail_count": fail_count,
        "skipped_no_work": len(tables) - total,
        "filled": filled_total,
        "failed_tables": failed_tables,
        "errors": [f"{t['table_name']}: {t['error']}" for t in failed_tables],
        "per_table_timeout_sec": per_table_timeout,
        **pending,
        **get_staging_stats(meta_engine),
    }
    if fail_count:
        result["message"] = f"完成：{success_count} 张表成功，{fail_count} 张表失败"
    elif filled_total == 0 and success_count == total and total > 0:
        result["message"] = "补全完成，但未写入新说明（可能模型未返回有效内容）"
    else:
        result["message"] = f"完成：共写入 {filled_total} 条字段说明"
    return result


def get_staging_stats(meta_engine: Engine) -> dict:
    if not staging_tables_exist(meta_engine):
        return {
            "staging_table_count": 0,
            "staging_column_count": 0,
            "missing_table_comments": 0,
            "missing_column_comments": 0,
            "review_complete": False,
        }
    with meta_engine.connect() as conn:
        tc = int(conn.execute(text("SELECT COUNT(*) FROM staging_table_meta")).scalar() or 0)
        cc = int(conn.execute(text("SELECT COUNT(*) FROM staging_column_meta")).scalar() or 0)
        mt = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM staging_table_meta
                    WHERE COALESCE(NULLIF(description, ''), NULLIF(hive_comment, '')) IS NULL
                    """
                )
            ).scalar()
            or 0
        )
        mc = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM staging_column_meta
                    WHERE COALESCE(NULLIF(description, ''), NULLIF(hive_comment, '')) IS NULL
                    """
                )
            ).scalar()
            or 0
        )
    return {
        "staging_table_count": tc,
        "staging_column_count": cc,
        "missing_table_comments": mt,
        "missing_column_comments": mc,
        "review_complete": tc > 0 and mt == 0 and mc == 0,
    }


def get_staging_pending_overview(meta_engine: Engine) -> dict:
    """暂存区待同步概览：凡在 staging 中的表/字段，同步前进 L1 前均视为待同步。"""
    if not staging_tables_exist(meta_engine):
        return {
            "items": [],
            "manual_table_count": 0,
            "manual_column_count": 0,
            "summary": "暂存区为空，请先在「元数据同步」扫描原始表",
        }
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.table_id, t.table_name, t.description, t.hive_comment, t.comment_source,
                       (SELECT COUNT(*) FROM staging_column_meta c WHERE c.table_id = t.table_id) AS col_total,
                       (SELECT COUNT(*) FROM staging_column_meta c
                        WHERE c.table_id = t.table_id
                          AND COALESCE(NULLIF(c.description, ''), NULLIF(c.hive_comment, '')) IS NOT NULL
                       ) AS col_filled,
                       (SELECT COUNT(*) FROM staging_column_meta c
                        WHERE c.table_id = t.table_id AND c.comment_source = 'manual'
                       ) AS col_manual
                FROM staging_table_meta t
                ORDER BY t.table_name
                """
            )
        ).fetchall()
        manual_tables = int(
            conn.execute(
                text("SELECT COUNT(*) FROM staging_table_meta WHERE comment_source = 'manual'")
            ).scalar()
            or 0
        )
        manual_columns = int(
            conn.execute(
                text("SELECT COUNT(*) FROM staging_column_meta WHERE comment_source = 'manual'")
            ).scalar()
            or 0
        )

    items = []
    for r in rows:
        table_filled = not _empty(_effective_description(r[2], r[3]))
        items.append(
            {
                "table_id": r[0],
                "table_name": r[1],
                "table_comment_filled": table_filled,
                "table_comment_source": r[4] or ("schema" if r[3] else None),
                "column_total": int(r[5] or 0),
                "column_filled": int(r[6] or 0),
                "column_manual_count": int(r[7] or 0),
                "has_manual_edit": (r[4] == "manual") or int(r[7] or 0) > 0,
            }
        )

    tc = len(items)
    if tc == 0:
        summary = "暂存区为空"
    else:
        summary = f"暂存区 {tc} 表待同步（手工编辑：{manual_tables} 表 / {manual_columns} 字段）"

    return {
        "items": items,
        "manual_table_count": manual_tables,
        "manual_column_count": manual_columns,
        "summary": summary,
    }


def list_staging_tables(meta_engine: Engine, q: str = "") -> list[dict]:
    if not staging_tables_exist(meta_engine):
        return []
    materialize_staging_comments(meta_engine)
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.table_id, t.db_name, t.table_name, t.description, t.hive_comment,
                       t.comment_source, t.scanned_at,
                       (SELECT COUNT(*) FROM staging_column_meta c WHERE c.table_id = t.table_id) AS col_count,
                       (SELECT COUNT(*) FROM staging_column_meta c
                        WHERE c.table_id = t.table_id
                          AND (COALESCE(NULLIF(c.description, ''), NULLIF(c.hive_comment, '')) IS NULL)) AS missing_cols
                FROM staging_table_meta t
                ORDER BY t.table_name
                """
            )
        ).fetchall()

    items = []
    with meta_engine.connect() as conn:
        for r in rows:
            l1_row = _resolve_l1_table(conn, r[1], r[2])
            match_status = _resolve_match_status(has_l1=bool(l1_row), comment_source=r[5])
            inherited_cols = 0
            new_cols = 0
            if l1_row:
                l1_names = {
                    x[0]
                    for x in conn.execute(
                        text(
                            "SELECT column_name FROM column_meta WHERE table_id = :tid AND is_enabled = 1"
                        ),
                        {"tid": l1_row.table_id},
                    ).fetchall()
                }
                st_names = {
                    x[0]
                    for x in conn.execute(
                        text("SELECT column_name FROM staging_column_meta WHERE table_id = :tid"),
                        {"tid": r[0]},
                    ).fetchall()
                }
                inherited_cols = len(st_names & l1_names)
                new_cols = len(st_names - l1_names)
            else:
                new_cols = int(r[7] or 0)

            item = {
                "table_id": r[0],
                "db_name": r[1],
                "table_name": r[2],
                "description": _effective_description(r[3], r[4]) or None,
                "hive_comment": r[4],
                "comment_source": r[5] or (r[4] and "schema" or None),
                "match_status": match_status,
                "inherited_column_count": inherited_cols,
                "new_column_count": new_cols,
                "scanned_at": r[6].isoformat(sep=" ", timespec="seconds") if r[6] else None,
                "column_count": int(r[7] or 0),
                "missing_column_comments": int(r[8] or 0),
                "missing_table_comment": _empty(_effective_description(r[3], r[4])),
            }
            items.append(item)

    if q.strip():
        kw = q.strip().lower()
        items = [
            t
            for t in items
            if kw in (t["table_name"] or "").lower()
            or kw in (t["description"] or "").lower()
        ]
    return items


def load_staging_table_editor(meta_engine: Engine, table_id: str) -> dict:
    """加载单表编辑视图：补全建表 comment 后返回表头 + 字段列表。"""
    materialize_staging_comments(meta_engine, table_id=table_id)
    with meta_engine.connect() as conn:
        trow = conn.execute(
            text(
                """
                SELECT table_id, db_name, table_name, description, hive_comment, comment_source
                FROM staging_table_meta
                WHERE table_id = :tid
                """
            ),
            {"tid": table_id},
        ).fetchone()
        rows = conn.execute(
            text(
                """
                SELECT column_id, column_name, data_type, description, hive_comment,
                       comment_source, ordinal_pos
                FROM staging_column_meta
                WHERE table_id = :tid
                ORDER BY ordinal_pos, column_name
                """
            ),
            {"tid": table_id},
        ).fetchall()

    table = None
    l1_orphans: list[dict] = []
    if trow:
        with meta_engine.connect() as conn:
            l1_row = _resolve_l1_table(conn, trow[1], trow[2])
            table = {
                "table_id": trow[0],
                "db_name": trow[1],
                "table_name": trow[2],
                "description": _effective_description(trow[3], trow[4]) or None,
                "hive_comment": trow[4],
                "comment_source": trow[5] or (trow[4] and "schema" or None),
                "match_status": _resolve_match_status(has_l1=bool(l1_row), comment_source=trow[5]),
                "missing_table_comment": _empty(_effective_description(trow[3], trow[4])),
            }
            l1_names = set()
            if l1_row:
                l1_names = {
                    x[0]
                    for x in conn.execute(
                        text(
                            "SELECT column_name FROM column_meta WHERE table_id = :tid AND is_enabled = 1"
                        ),
                        {"tid": l1_row.table_id},
                    ).fetchall()
                }
        l1_orphans = list_l1_orphan_columns(meta_engine, table_id)

    items = []
    with meta_engine.connect() as conn:
        l1_row = _resolve_l1_table(conn, trow[1], trow[2]) if trow else None
        l1_name_set = set()
        if l1_row:
            l1_name_set = {
                x[0]
                for x in conn.execute(
                    text("SELECT column_name FROM column_meta WHERE table_id = :tid AND is_enabled = 1"),
                    {"tid": l1_row.table_id},
                ).fetchall()
            }
        for r in rows:
            has_l1 = r[1] in l1_name_set if l1_row else False
            items.append(
                {
                    "column_id": r[0],
                    "column_name": r[1],
                    "data_type": r[2],
                    "description": _effective_description(r[3], r[4]) or None,
                    "hive_comment": r[4],
                    "comment_source": r[5] or (r[4] and "schema" or None),
                    "match_status": _resolve_match_status(has_l1=has_l1, comment_source=r[5]),
                    "ordinal_pos": r[6],
                    "missing_comment": _empty(_effective_description(r[3], r[4])),
                }
            )
    missing_cols = sum(1 for c in items if c["missing_comment"])
    if table:
        table["missing_column_comments"] = missing_cols
        table["column_count"] = len(items)
        table["new_column_count"] = sum(1 for c in items if c["match_status"] == "new")
        table["inherited_column_count"] = sum(
            1 for c in items if c["match_status"] in {"inherited", "l1_mapped", "manual"}
        )
    return {"table": table, "items": items, "l1_orphans": l1_orphans}


def list_staging_columns(meta_engine: Engine, table_id: str) -> list[dict]:
    return load_staging_table_editor(meta_engine, table_id)["items"]


def update_staging_table(meta_engine: Engine, table_id: str, description: str) -> None:
    desc = (description or "").strip()
    if not desc:
        raise ValueError("表说明不能为空")
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE staging_table_meta
                SET description = :d, comment_source = 'manual'
                WHERE table_id = :tid
                """
            ),
            {"d": desc, "tid": table_id},
        )


def update_staging_column(meta_engine: Engine, column_id: str, description: str) -> None:
    desc = (description or "").strip()
    if not desc:
        raise ValueError("字段说明不能为空")
    with meta_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE staging_column_meta
                SET description = :d, comment_source = 'manual'
                WHERE column_id = :cid
                """
            ),
            {"d": desc, "cid": column_id},
        )


def update_staging_columns_batch(
    meta_engine: Engine,
    table_id: str,
    columns: list[dict],
) -> dict:
    """批量保存同表下多个字段说明（跳过空说明）。"""
    saved = 0
    skipped = 0
    batch_params: list[dict] = []
    for item in columns:
        col_id = item.get("column_id")
        desc = (item.get("description") or "").strip()
        if not col_id:
            skipped += 1
            continue
        if not desc:
            skipped += 1
            continue
        batch_params.append({"d": desc, "cid": col_id, "tid": table_id})

    if not batch_params:
        return {"saved": 0, "skipped": skipped, "table_id": table_id}

    with meta_engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE staging_column_meta
                SET description = :d, comment_source = 'manual'
                WHERE column_id = :cid AND table_id = :tid
                """
            ),
            batch_params,
        )
        saved = int(result.rowcount or 0)
        skipped += len(batch_params) - saved
    return {"saved": saved, "skipped": skipped, "table_id": table_id}


def validate_staging(
    meta_engine: Engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
) -> dict:
    stats = get_staging_stats(meta_engine)
    pending = get_staging_pending_overview(meta_engine)
    stats = {**stats, "pending_overview": pending}
    missing: list[dict] = []
    if stats["staging_table_count"] == 0:
        return {"ok": False, "message": "请先执行「扫描原始表」", "missing": [], **stats}

    with meta_engine.connect() as conn:
        if column_ids:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids)))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids)}
            col_rows = conn.execute(
                text(
                    f"""
                    SELECT c.column_id, t.table_name, c.column_name, c.description, c.hive_comment
                    FROM staging_column_meta c
                    JOIN staging_table_meta t ON t.table_id = c.table_id
                    WHERE c.column_id IN ({placeholders})
                """
                ),
                params,
            ).fetchall()
            for col_id, table_name, col_name, desc, hive in col_rows:
                if _empty(_effective_description(desc, hive)):
                    missing.append(
                        {"type": "column", "column_id": col_id, "table": table_name, "name": col_name}
                    )
            ok = len(missing) == 0 and len(col_rows) == len(column_ids)
            msg = "字段说明完整，可以同步" if ok else f"仍有 {len(missing)} 个字段缺少说明"
            return {"ok": ok, "message": msg, "missing": missing, **stats}

        table_filter = ""
        params: dict = {}
        if table_ids:
            placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
            params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
            table_filter = f"AND table_id IN ({placeholders})"

        for r in conn.execute(
            text(
                f"""
                SELECT table_id, table_name FROM staging_table_meta
                WHERE COALESCE(NULLIF(description, ''), NULLIF(hive_comment, '')) IS NULL {table_filter}
                """
            ),
            params,
        ).fetchall():
            missing.append({"type": "table", "table_id": r[0], "name": r[1]})

        col_filter = ""
        if table_ids:
            col_filter = f"AND c.table_id IN ({placeholders})"
        for r in conn.execute(
            text(
                f"""
                SELECT c.column_id, t.table_name, c.column_name
                FROM staging_column_meta c
                JOIN staging_table_meta t ON t.table_id = c.table_id
                WHERE COALESCE(NULLIF(c.description, ''), NULLIF(c.hive_comment, '')) IS NULL {col_filter}
                LIMIT 50
                """
            ),
            params,
        ).fetchall():
            missing.append(
                {"type": "column", "column_id": r[0], "table": r[1], "name": r[2]}
            )

    if table_ids:
        ok = len(missing) == 0
    else:
        ok = stats["review_complete"]
    if ok:
        msg = "审核通过，可以同步"
    elif table_ids:
        msg = f"所选表仍有 {len([m for m in missing if m['type'] == 'table'])} 张表、{len([m for m in missing if m['type'] == 'column'])} 个字段缺少说明"
    else:
        msg = f"仍有 {stats['missing_table_comments']} 张表、{stats['missing_column_comments']} 个字段缺少说明"
    return {"ok": ok, "message": msg, "missing": missing, **stats}


def _normalize_data_type(data_type: str | None) -> str:
    value = (data_type or "VARCHAR").strip() or "VARCHAR"
    return value[:128]


def _upsert_table_meta_row(
    conn,
    *,
    table_id: str,
    db_name: str,
    table_name: str,
    description: str | None,
    hive_comment: str | None,
    synced_at: datetime,
) -> str:
    """写入 table_meta，返回 L1 实际 table_id（兼容 uk_db_table 与旧数据 id 不一致）。"""
    conn.execute(
        text(
            """
            INSERT INTO table_meta
                (table_id, db_name, table_name, hive_comment, description,
                 is_enabled, source, synced_at)
            VALUES
                (:table_id, :db_name, :table_name, :hive_comment, :description,
                 1, 'mysql', :synced_at)
            ON DUPLICATE KEY UPDATE
                hive_comment = VALUES(hive_comment),
                description = VALUES(description),
                is_enabled = 1,
                source = 'mysql',
                synced_at = VALUES(synced_at)
            """
        ),
        {
            "table_id": table_id,
            "db_name": db_name,
            "table_name": table_name,
            "hive_comment": hive_comment,
            "description": description,
            "synced_at": synced_at,
        },
    )
    row = conn.execute(
        text("SELECT table_id FROM table_meta WHERE db_name = :db AND table_name = :tbl"),
        {"db": db_name, "tbl": table_name},
    ).fetchone()
    return row[0] if row else table_id


def _fetch_staging_tables_by_ids(conn, table_ids: list[str]) -> list:
    if not table_ids:
        return []
    placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
    params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
    return conn.execute(
        text(
            f"""
            SELECT table_id, db_name, table_name, description, hive_comment
            FROM staging_table_meta
            WHERE table_id IN ({placeholders})
            """
        ),
        params,
    ).fetchall()


def _assert_full_staging_for_absent_actions(
    meta_engine: Engine,
    db_name: str,
    seen: set[tuple[str, str]],
) -> None:
    """软禁用/硬删仅允许暂存区覆盖该库绝大部分业务表时执行。"""
    skip = {"staging_table_meta", "staging_column_meta"}
    seen = {(db, tbl) for db, tbl in seen if tbl not in skip}
    with meta_engine.connect() as conn:
        l1_total = int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM table_meta
                    WHERE db_name = :db AND source = 'mysql'
                      AND table_name NOT IN ('staging_table_meta', 'staging_column_meta')
                    """
                ),
                {"db": db_name},
            ).scalar()
            or 0
        )
    if l1_total == 0:
        return
    staging_count = len(seen)
    min_required = max(1, int(l1_total * 0.9))
    if staging_count < min_required:
        raise ValueError(
            f"暂存区仅有 {staging_count} 张业务表，元数据库中该库已有 {l1_total} 张。"
            "未覆盖全库时不能软禁用或硬删「不在暂存中的表」。"
            "请先全库扫描，或取消勾选「软禁用…/硬删…」。"
        )


def commit_staging_to_l1(
    meta_engine: Engine,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
    purge_missing: bool = False,
    disable_absent: bool = False,
) -> dict:
    """将已审核的 staging 写入 table_meta / column_meta。"""
    check = validate_staging(meta_engine, table_ids=table_ids, column_ids=column_ids)
    if not check["ok"]:
        raise ValueError(check["message"])

    now = datetime.now()
    tables_upserted = 0
    columns_upserted = 0
    scope = "full"
    if column_ids:
        scope = "column"
    elif table_ids:
        scope = "table"

    with meta_engine.begin() as conn:
        st_cols: list = []
        if column_ids:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids)))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids)}
            st_cols = conn.execute(
                text(
                    f"""
                    SELECT column_id, table_id, column_name, data_type, description,
                           hive_comment, ordinal_pos
                    FROM staging_column_meta
                    WHERE column_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            parent_ids = list({r[1] for r in st_cols})
            st_tables = _fetch_staging_tables_by_ids(conn, parent_ids)
        elif table_ids:
            placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
            params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
            table_filter = f"WHERE table_id IN ({placeholders})"
            st_tables = conn.execute(
                text(
                    f"""
                    SELECT table_id, db_name, table_name, description, hive_comment
                    FROM staging_table_meta
                    {table_filter}
                    """
                ),
                params,
            ).fetchall()
            st_cols = conn.execute(
                text(
                    f"""
                    SELECT column_id, table_id, column_name, data_type, description,
                           hive_comment, ordinal_pos
                    FROM staging_column_meta
                    WHERE table_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
        else:
            st_tables = conn.execute(
                text(
                    """
                    SELECT table_id, db_name, table_name, description, hive_comment
                    FROM staging_table_meta
                    """
                ),
            ).fetchall()
            st_cols = conn.execute(
                text(
                    """
                    SELECT column_id, table_id, column_name, data_type, description,
                           hive_comment, ordinal_pos
                    FROM staging_column_meta
                    """
                ),
            ).fetchall()

        table_id_map: dict[str, str] = {}
        for table_id, db_name, table_name, description, hive_comment in st_tables:
            l1_table_id = _upsert_table_meta_row(
                conn,
                table_id=table_id,
                db_name=db_name,
                table_name=table_name,
                description=description,
                hive_comment=hive_comment,
                synced_at=now,
            )
            table_id_map[table_id] = l1_table_id
            tables_upserted += 1

        for col_id, table_id, col_name, data_type, description, hive_comment, ordinal in st_cols:
            l1_table_id = table_id_map.get(table_id)
            if not l1_table_id:
                row = conn.execute(
                    text("SELECT table_id FROM table_meta WHERE table_id = :tid"),
                    {"tid": table_id},
                ).fetchone()
                l1_table_id = row[0] if row else None
            if not l1_table_id:
                raise ValueError(
                    f"字段 {col_name} 所属表尚未写入元数据库（table_id={table_id}），请先同步对应表"
                )
            l1_column_id = make_column_id(l1_table_id, col_name)
            conn.execute(
                text(
                    """
                    INSERT INTO column_meta
                        (column_id, table_id, column_name, data_type, hive_comment, description,
                         is_partition, ordinal_pos, is_enabled)
                    VALUES
                        (:column_id, :table_id, :column_name, :data_type, :hive_comment, :description,
                         0, :ordinal_pos, 1)
                    ON DUPLICATE KEY UPDATE
                        data_type = VALUES(data_type),
                        hive_comment = VALUES(hive_comment),
                        description = VALUES(description),
                        ordinal_pos = VALUES(ordinal_pos),
                        is_enabled = 1
                    """
                ),
                {
                    "column_id": l1_column_id,
                    "table_id": l1_table_id,
                    "column_name": col_name,
                    "data_type": _normalize_data_type(data_type),
                    "hive_comment": hive_comment,
                    "description": description,
                    "ordinal_pos": ordinal,
                },
            )
            columns_upserted += 1

    disabled = 0
    purged: dict = {
        "tables_purged": 0,
        "columns_purged": 0,
        "purged_table_ids": [],
        "purged_column_ids": [],
    }
    if not table_ids and not column_ids and st_tables and disable_absent:
        seen = {(r[1], r[2]) for r in st_tables}
        db_name = st_tables[0][1] if st_tables else ""
        if db_name:
            _assert_full_staging_for_absent_actions(meta_engine, db_name, seen)
            if purge_missing:
                purged = purge_missing_tables(meta_engine, db_name, seen)
            else:
                disabled = disable_missing_tables(meta_engine, db_name, seen)

    if not table_ids and not column_ids and st_tables and purge_missing and not disable_absent:
        seen = {(r[1], r[2]) for r in st_tables}
        db_name = st_tables[0][1] if st_tables else ""
        if db_name:
            _assert_full_staging_for_absent_actions(meta_engine, db_name, seen)
            purged = purge_missing_tables(meta_engine, db_name, seen)

    clear_result = _clear_staging_after_commit(
        meta_engine,
        table_ids=table_ids,
        column_ids=column_ids,
    )

    return {
        "tables_upserted": tables_upserted,
        "columns_upserted": columns_upserted,
        "tables_disabled": disabled,
        "purge_missing": purge_missing,
        "disable_absent": disable_absent,
        **purged,
        "scope": scope,
        "table_ids": table_ids or [],
        "column_ids": column_ids or [],
        **clear_result,
        **get_staging_stats(meta_engine),
        "message": clear_result.get("message") or "元数据已同步到元数据库",
    }


def _clear_staging_after_commit(
    meta_engine: Engine,
    *,
    table_ids: list[str] | None = None,
    column_ids: list[str] | None = None,
) -> dict:
    """同步成功后移除已写入 L1 的暂存记录（全库 / 单表 / 单字段）。"""
    cleared_tables = 0
    cleared_columns = 0
    with meta_engine.begin() as conn:
        if column_ids:
            placeholders = ", ".join(f":c{i}" for i in range(len(column_ids)))
            params = {f"c{i}": cid for i, cid in enumerate(column_ids)}
            affected = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT table_id FROM staging_column_meta
                    WHERE column_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()
            result = conn.execute(
                text(
                    f"""
                    DELETE FROM staging_column_meta
                    WHERE column_id IN ({placeholders})
                    """
                ),
                params,
            )
            cleared_columns = int(result.rowcount or 0)
            for (tid,) in affected:
                remaining = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM staging_column_meta WHERE table_id = :tid"
                    ),
                    {"tid": tid},
                ).scalar()
                if int(remaining or 0) == 0:
                    conn.execute(
                        text("DELETE FROM staging_table_meta WHERE table_id = :tid"),
                        {"tid": tid},
                    )
                    cleared_tables += 1
        elif table_ids:
            placeholders = ", ".join(f":t{i}" for i in range(len(table_ids)))
            params = {f"t{i}": tid for i, tid in enumerate(table_ids)}
            col_res = conn.execute(
                text(
                    f"""
                    DELETE FROM staging_column_meta
                    WHERE table_id IN ({placeholders})
                    """
                ),
                params,
            )
            cleared_columns = int(col_res.rowcount or 0)
            tbl_res = conn.execute(
                text(
                    f"""
                    DELETE FROM staging_table_meta
                    WHERE table_id IN ({placeholders})
                    """
                ),
                params,
            )
            cleared_tables = int(tbl_res.rowcount or 0)
        else:
            cleared_columns = int(
                conn.execute(text("DELETE FROM staging_column_meta")).rowcount or 0
            )
            cleared_tables = int(
                conn.execute(text("DELETE FROM staging_table_meta")).rowcount or 0
            )

    scope_label = "全库" if not table_ids and not column_ids else "选定范围"
    return {
        "staging_cleared": True,
        "staging_cleared_tables": cleared_tables,
        "staging_cleared_columns": cleared_columns,
        "message": f"元数据已同步到元数据库；暂存区已清空（{scope_label}）",
    }


def clear_staging(
    meta_engine: Engine,
    table_ids: list[str] | None = None,
) -> dict:
    """用户手动清空暂存区（未同步的编辑将丢失）。"""
    if not staging_tables_exist(meta_engine):
        raise RuntimeError("staging 表不存在，请先执行「初始化元数据表」")

    stats_before = get_staging_stats(meta_engine)
    if stats_before["staging_table_count"] == 0:
        return {
            "ok": True,
            "cleared_tables": 0,
            "cleared_columns": 0,
            "message": "暂存区已是空的",
        }

    cleared = _clear_staging_after_commit(meta_engine, table_ids=table_ids)
    scope_label = "指定表" if table_ids else "全库"
    return {
        "ok": True,
        "cleared_tables": cleared["staging_cleared_tables"],
        "cleared_columns": cleared["staging_cleared_columns"],
        **get_staging_stats(meta_engine),
        "message": (
            f"已清空暂存区（{scope_label}）："
            f"{cleared['staging_cleared_tables']} 表、{cleared['staging_cleared_columns']} 字段"
        ),
    }
