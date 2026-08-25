#!/usr/bin/env python3
"""启动问数元数据管理平台。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    import os

    import uvicorn

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    host = os.getenv("PLATFORM_HOST", "127.0.0.1")
    port = int(os.getenv("PLATFORM_PORT", "8765"))
    uvicorn.run("wenshu.app:app", host=host, port=port, reload=False)
