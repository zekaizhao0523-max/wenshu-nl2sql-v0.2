#!/usr/bin/env python3
"""用本机元库重建本机 Qdrant。不改项目 .env / 平台连接配置。

读取 F:\\wenshu-local\\local.env 与 .wenshu/local.env，覆盖本次进程的 META/QDRANT。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _load_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def apply_local_targets() -> None:
    cfg: dict[str, str] = {}
    cfg.update(_load_kv(ROOT / ".wenshu" / "local.env"))
    cfg.update(_load_kv(Path(r"F:\wenshu-local\local.env")))
    os.environ["META_MYSQL_HOST"] = cfg.get("LOCAL_MYSQL_HOST", "127.0.0.1")
    os.environ["META_MYSQL_PORT"] = cfg.get("LOCAL_MYSQL_PORT", "3307")
    os.environ["META_MYSQL_USER"] = cfg.get("LOCAL_MYSQL_USER", "root")
    os.environ["META_MYSQL_PASSWORD"] = cfg.get("LOCAL_MYSQL_PASSWORD", "")
    os.environ["META_MYSQL_DATABASE"] = "metadata_vector"
    os.environ.pop("META_MYSQL_DSN", None)
    os.environ["QDRANT_URL"] = cfg.get("LOCAL_QDRANT_URL", "http://127.0.0.1:6333")
    os.environ["QDRANT_COLLECTION"] = cfg.get("LOCAL_QDRANT_COLLECTION", "wenshu_knowledge")
    os.environ.pop("QDRANT_API_KEY", None)


def main() -> None:
    apply_local_targets()
    from wenshu.services import connections

    connections.apply_overlay_to_environ = lambda: None  # 本次不吃平台远程连接
    apply_local_targets()
    sys.argv = ["build_vector_index.py", "--full"]
    import build_vector_index as bvi

    bvi.main()


if __name__ == "__main__":
    main()
