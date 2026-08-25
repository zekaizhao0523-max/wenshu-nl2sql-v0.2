#!/usr/bin/env python3
"""把领域召回口语词典写入 L1 synonym 表。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from db_config import get_meta_mysql_engine
from wenshu.services.l1_meta import seed_retrieval_synonyms
from wenshu.services.schema_retrieval import refresh_retrieval_lexicon


def main() -> None:
    eng = get_meta_mysql_engine()
    result = seed_retrieval_synonyms(eng)
    refresh_retrieval_lexicon(eng)
    print(
        "导入完成 "
        f"inserted={result['inserted']} reused={result['reused']} "
        f"skipped={result['skipped']} fact_flags={result.get('fact_flags', 0)}"
    )


if __name__ == "__main__":
    main()
