#!/usr/bin/env python3
"""为黄金集写入 gold_sql（方案 A 标准答案）。

用法:
  python evals/scripts/attach_gold_sql.py --golden evals/golden/recall_v2_dwd.jsonl
  python evals/scripts/attach_gold_sql.py --golden evals/golden/recall_v2_dwd_100.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wenshu.services.agent.sql_result_match import build_gold_sql  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", required=True)
    parser.add_argument("--inplace", action="store_true", default=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已有 gold_sql（默认跳过已有项）",
    )
    args = parser.parse_args()

    path = Path(args.golden)
    if not path.is_absolute():
        path = ROOT / path

    from db_config import get_meta_mysql_engine
    import build_vector_index as bvi

    bvi._load_dotenv()
    meta = get_meta_mysql_engine()

    rows = []
    ok = 0
    miss = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        sql = build_gold_sql(item, meta, limit=args.limit, force=args.force)
        if sql:
            item["gold_sql"] = sql
            ok += 1
        else:
            item.pop("gold_sql", None)
            miss += 1
        rows.append(item)

    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {path}: gold_sql ok={ok} miss={miss} total={len(rows)}")


if __name__ == "__main__":
    main()
