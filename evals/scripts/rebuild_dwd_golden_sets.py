#!/usr/bin/env python3
"""从独立 100 题集切出「77 题等价子集」：全部 40 单表 + 前 37 多表。

说明：原 recall_v2_dwd.jsonl（业务 77 题）已在脱敏时删除且无备份；
本脚本用同域、同表范围的 standalone 题面重建可复现评测基线。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "evals" / "golden" / "recall_dwd_standalone_100.jsonl"
OUT77 = ROOT / "evals" / "golden" / "recall_v2_dwd.jsonl"
OUT100 = ROOT / "evals" / "golden" / "recall_v2_dwd_100.jsonl"


def main() -> None:
    rows = [
        json.loads(line)
        for line in SRC.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    singles = [r for r in rows if r.get("suite") == "single"]
    multis = [r for r in rows if r.get("suite") == "multi"]
    if len(singles) < 40 or len(multis) < 37:
        raise SystemExit(f"unexpected split: single={len(singles)} multi={len(multis)}")

    set77 = singles[:40] + multis[:37]
    for i, r in enumerate(set77, 1):
        r = dict(r)
        r["notes"] = (r.get("notes") or "") + "|reconstructed_77_from_standalone"
        set77[i - 1] = r

    OUT77.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in set77) + "\n",
        encoding="utf-8",
    )
    # 100 题全集：直接使用 standalone（与历史「77+23」题量对齐，题面为独立生成版）
    OUT100.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT77} n={len(set77)} (single={40} multi={37})")
    print(f"wrote {OUT100} n={len(rows)} (single={len(singles)} multi={len(multis)})")


if __name__ == "__main__":
    main()
