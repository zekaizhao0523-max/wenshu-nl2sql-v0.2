#!/usr/bin/env python3
"""写入示例 JOIN 到 L1 table_relation（demo 表）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wenshu.services.l1_meta import save_relation

JOINS: list[dict] = [
    {
        "left_table": "demo_orders",
        "left_column": "cust_id",
        "right_table": "demo_customers",
        "right_column": "cust_id",
        "description": "订单通过客户编号关联客户",
    },
    {
        "left_table": "demo_orders",
        "left_column": "product_id",
        "right_table": "demo_products",
        "right_column": "product_id",
        "description": "订单通过产品编号关联产品",
    },
]


def main() -> None:
    from db_config import get_meta_mysql_engine

    engine = get_meta_mysql_engine()
    for j in JOINS:
        save_relation(engine, **j)
    print(f"写入 {len(JOINS)} 条 demo JOIN")


if __name__ == "__main__":
    main()
