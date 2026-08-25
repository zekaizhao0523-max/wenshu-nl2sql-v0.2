#!/usr/bin/env python3
"""将 vectortest 中的 L1/L2 元数据与暂存表迁移到 metadata_vector，并清理源库中的系统表。

用法:
  python scripts/migrate_meta_db.py              # 执行迁移
  python scripts/migrate_meta_db.py --dry-run    # 仅统计与导出，不写目标库、不删源表
  python scripts/migrate_meta_db.py --export-only sql/migration/meta_export.sql

注意: 不删除 vectortest 中的原始业务表。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wenshu.services.schema_templates import EXPECTED_TABLES, apply_schema_templates  # noqa: E402

# 与 sync_mysql.METADATA_TABLES + staging 一致
META_TABLES = list(EXPECTED_TABLES)


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
        except ImportError:
            pass


def _build_dsn(host: str, port: str, database: str, user: str, password: str) -> str:
    from urllib.parse import quote_plus

    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )


def _engine_from_env(prefix: str, default_db: str) -> Engine:
    host = os.getenv(f"{prefix}_HOST") or os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv(f"{prefix}_PORT") or os.getenv("MYSQL_PORT", "3306")
    database = os.getenv(f"{prefix}_DATABASE") or default_db
    user = os.getenv(f"{prefix}_USER") or os.getenv("MYSQL_USER", "root")
    password = os.getenv(f"{prefix}_PASSWORD") or os.getenv("MYSQL_PASSWORD", "")
    dsn = os.getenv(f"{prefix}_DSN") or _build_dsn(host, port, database, user, password)
    return create_engine(dsn, pool_pre_ping=True)


def _count_rows(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar() or 0)


def _list_meta_tables(engine: Engine) -> list[str]:
    placeholders = ", ".join(f":t{i}" for i in range(len(META_TABLES)))
    params = {f"t{i}": name for i, name in enumerate(META_TABLES)}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name IN ({placeholders})
                ORDER BY table_name
                """
            ),
            params,
        ).fetchall()
    return [r[0] for r in rows]


def _fetch_columns(engine: Engine, table: str) -> list[str]:
    insp = inspect(engine)
    return [c["name"] for c in insp.get_columns(table)]


def _copy_table(src: Engine, dst: Engine, table: str) -> int:
    cols = _fetch_columns(src, table)
    if not cols:
        return 0
    col_list = ", ".join(f"`{c}`" for c in cols)
    placeholders = ", ".join(f":{c}" for c in cols)

    with src.connect() as sconn:
        rows = sconn.execute(text(f"SELECT {col_list} FROM `{table}`")).mappings().all()

    with dst.begin() as dconn:
        dconn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        dconn.execute(text(f"DELETE FROM `{table}`"))
        if rows:
            dconn.execute(
                text(f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders})"),
                [dict(r) for r in rows],
            )
        dconn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    return len(rows)


def _export_sql(src: Engine, out_path: Path) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "-- 问数元数据迁移导出（请在 metadata_vector 库执行）",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS=0;",
    ]
    stats: dict[str, int] = {}
    for table in META_TABLES:
        if table not in _list_meta_tables(src):
            continue
        cols = _fetch_columns(src, table)
        col_list = ", ".join(f"`{c}`" for c in cols)
        with src.connect() as conn:
            rows = conn.execute(text(f"SELECT {col_list} FROM `{table}`")).fetchall()
        stats[table] = len(rows)
        lines.append(f"DELETE FROM `{table}`;")
        for row in rows:
            vals = []
            for v in row:
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    s = str(v).replace("\\", "\\\\").replace("'", "''")
                    vals.append(f"'{s}'")
            lines.append(f"INSERT INTO `{table}` ({col_list}) VALUES ({', '.join(vals)});")
    lines.append("SET FOREIGN_KEY_CHECKS=1;")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return stats


def _drop_source_meta(src: Engine, tables: list[str]) -> None:
    with src.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in reversed(tables):
            conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _update_connections_meta(host: str, port: str, database: str, user: str, password: str) -> None:
    store_path = ROOT / ".wenshu" / "connections.json"
    if not store_path.exists():
        return
    data = json.loads(store_path.read_text(encoding="utf-8"))
    data.setdefault("roles", {})["meta"] = {
        "engine": "mysql",
        "values": {
            "host": host,
            "port": str(port),
            "database": database,
            "user": user,
            "password": password,
        },
    }
    store_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _verify(src: Engine, dst: Engine, tables: list[str]) -> list[str]:
    errors = []
    for table in tables:
        sc = _count_rows(src, table)
        dc = _count_rows(dst, table)
        if sc != dc:
            errors.append(f"{table}: source={sc} target={dc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate metadata tables from vectortest to metadata_vector")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写目标库、不删源表")
    parser.add_argument("--export-only", metavar="PATH", help="导出 SQL 文件后退出")
    parser.add_argument("--skip-drop", action="store_true", help="迁移后保留源库系统表")
    parser.add_argument(
        "--drop-source-only",
        action="store_true",
        help="仅删除 vectortest 中的元数据/暂存表（确认已导入 metadata_vector 后使用）",
    )
    parser.add_argument("--skip-connections", action="store_true", help="不更新 .wenshu/connections.json")
    args = parser.parse_args()

    _load_dotenv()

    # 源：业务库 vectortest（当前 meta 也在这里）
    src = _engine_from_env("MYSQL", "vectortest")
    # 目标：.env 中 META_MYSQL_*
    dst = _engine_from_env("META_MYSQL", "metadata_vector")

    with src.connect() as conn:
        src_db = conn.execute(text("SELECT DATABASE()")).scalar()
    print(f"[source] {src_db}")

    if args.drop_source_only:
        present = [t for t in META_TABLES if t in _list_meta_tables(src)]
        if not present:
            print("源库未发现元数据表。")
            return 0
        print("[source] 仅删除元数据/暂存系统表…")
        _drop_source_meta(src, present)
        print("已删除:", ", ".join(present))
        print("原始业务表未改动。")
        return 0

    try:
        with dst.connect() as conn:
            dst_db = conn.execute(text("SELECT DATABASE()")).scalar()
        print(f"[target] {dst_db}")
    except Exception as exc:
        print(f"[target] 连接失败: {exc}")
        export_path = ROOT / "sql" / "migration" / "meta_export.sql"
        stats = _export_sql(src, export_path)
        print("\n已导出 SQL（目标库不可连时备用）:", export_path)
        for t, n in stats.items():
            print(f"  {t}: {n} rows")
        print(
            "\n请在 SQLPub 控制台确认 metadata_vector 账号密码，"
            "请确认元数据库账号具备 metadata 库读写权限后重试。"
        )
        return 2

    present = [t for t in META_TABLES if t in _list_meta_tables(src)]
    if not present:
        print("源库未发现元数据表，无需迁移。")
        return 0

    print("\n源库行数:")
    src_counts = {t: _count_rows(src, t) for t in present}
    for t in present:
        print(f"  {t}: {src_counts[t]}")

    if args.export_only:
        stats = _export_sql(src, Path(args.export_only))
        print("已导出:", args.export_only, stats)
        return 0

    if args.dry_run:
        print("\n[dry-run] 跳过写入与删除。")
        return 0

    print("\n[target] 初始化/补全元数据表结构…")
    apply_schema_templates(dst)

    dst_tables = [t for t in META_TABLES if t in _list_meta_tables(dst)]
    copy_order = [t for t in META_TABLES if t in dst_tables]
    print("[target] 复制数据…")
    copied = {}
    for table in copy_order:
        n = _copy_table(src, dst, table)
        copied[table] = n
        print(f"  {table}: {n}")

    errors = _verify(src, dst, copy_order)
    if errors:
        print("\n校验失败，未删除源库系统表:")
        for e in errors:
            print(" ", e)
        return 1

    print("\n校验通过。")

    if not args.skip_drop:
        print("[source] 删除元数据/暂存系统表（保留业务表）…")
        _drop_source_meta(src, copy_order)
        remaining = _list_meta_tables(src)
        print(f"  源库剩余系统表: {remaining or '(无)'}")

    if not args.skip_connections:
        host = os.getenv("META_MYSQL_HOST", "")
        port = os.getenv("META_MYSQL_PORT", "3311")
        database = os.getenv("META_MYSQL_DATABASE", "metadata_vector")
        user = os.getenv("META_MYSQL_USER", "")
        password = os.getenv("META_MYSQL_PASSWORD", "")
        _update_connections_meta(host, port, database, user, password)
        print("[config] 已更新 .wenshu/connections.json → meta = metadata_vector")

    try:
        from db_config import reset_mysql_engines

        reset_mysql_engines()
    except Exception:
        pass

    print("\n迁移完成。请重启问数平台并刷新页面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
