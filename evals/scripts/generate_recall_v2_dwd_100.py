#!/usr/bin/env python3
"""生成 recall_v2_dwd_100.jsonl：在现有 77 题 DWD 黄金集基础上扩展至 100 题（仅 enabled DWD 表）。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "evals" / "golden" / "recall_v2_dwd.jsonl"
OUT = ROOT / "evals" / "golden" / "recall_v2_dwd_100.jsonl"


def item(
    *,
    id: str,
    suite: str,
    question: str,
    domain: str,
    tags: list[str],
    must_tables: list[str],
    must_columns: list[tuple[str, str]],
    forbidden_tables: list[str] | None = None,
    field_slice: str | None = None,
    difficulty: str | None = None,
    notes: str = "",
) -> dict:
    if field_slice is None:
        if suite == "single":
            field_slice = "multi_field" if len(must_columns) >= 2 else "single_field"
        else:
            field_slice = "multi_table"
    if difficulty is None:
        if suite == "single":
            difficulty = "easy"
        elif len(must_tables) >= 3:
            difficulty = "hard"
        else:
            difficulty = "medium"
    return {
        "id": id,
        "suite": suite,
        "question": question,
        "domain": domain,
        "difficulty": difficulty,
        "tags": tags,
        "must_tables": must_tables,
        "nice_tables": [],
        "must_columns": [{"table": t, "column": c} for t, c in must_columns],
        "forbidden_tables": forbidden_tables or [],
        "notes": notes,
        "field_slice": field_slice,
    }


# 23 道增量题：覆盖 mapping / 代偿 / 综合授信 / 流程 / 更多字段组合
EXTRA: list[dict] = [
    # —— 单表 10 ——
    item(
        id="S041",
        suite="single",
        question="申请映射表里用信申请编号和借据号对应关系",
        domain="映射",
        tags=["单表", "单表多字段"],
        must_tables=["DWD_APP_MAPPING"],
        must_columns=[
            ("DWD_APP_MAPPING", "loan_app_no"),
            ("DWD_APP_MAPPING", "loan_no"),
        ],
        forbidden_tables=["DWD_EV_INDV_LOAN_APP"],
        notes="mapping 专表",
    ),
    item(
        id="S042",
        suite="single",
        question="代偿明细代偿本金余额合计",
        domain="代偿",
        tags=["单表", "单表单字段"],
        must_tables=["DWD_SR_CLAIM_DETAIL"],
        must_columns=[("DWD_SR_CLAIM_DETAIL", "dc_bal")],
        forbidden_tables=["DWD_AR_LOAN_INFO"],
    ),
    item(
        id="S043",
        suite="single",
        question="综合授信审批对公授信金额汇总",
        domain="授信",
        tags=["单表", "单表单字段"],
        must_tables=["DWD_EV_COMP_CRD_APP"],
        must_columns=[("DWD_EV_COMP_CRD_APP", "businesssum")],
        forbidden_tables=["DWD_EV_INDV_CRD_APP"],
    ),
    item(
        id="S044",
        suite="single",
        question="信贷流程事务申请编号和当前节点",
        domain="流程",
        tags=["单表", "单表多字段"],
        must_tables=["DWD_EV_TRAN_FLOW_INFO"],
        must_columns=[
            ("DWD_EV_TRAN_FLOW_INFO", "app_no"),
            ("DWD_EV_TRAN_FLOW_INFO", "app_node"),
        ],
        forbidden_tables=["DWD_EV_INDV_LOAN_APP"],
    ),
    item(
        id="S045",
        suite="single",
        question="个人客户居住地址分布统计",
        domain="客户",
        tags=["单表", "单表单字段"],
        must_tables=["DWD_IP_INDV_CUST_INFO"],
        must_columns=[("DWD_IP_INDV_CUST_INFO", "resiaddr")],
        forbidden_tables=["DWD_IP_CORP_CUST_INFO"],
    ),
    item(
        id="S046",
        suite="single",
        question="借据合同号和借据号列表",
        domain="借据",
        tags=["单表", "单表多字段"],
        must_tables=["DWD_AR_LOAN_INFO"],
        must_columns=[
            ("DWD_AR_LOAN_INFO", "cont_no"),
            ("DWD_AR_LOAN_INFO", "loan_no"),
        ],
    ),
    item(
        id="S047",
        suite="single",
        question="产品增信方式和授信类型分别有哪些",
        domain="产品",
        tags=["单表", "单表多字段"],
        must_tables=["DWD_PRD_INFO"],
        must_columns=[
            ("DWD_PRD_INFO", "credit_method"),
            ("DWD_PRD_INFO", "credit_type"),
        ],
    ),
    item(
        id="S048",
        suite="single",
        question="还款明细实还利息按借据汇总",
        domain="还款",
        tags=["单表", "单表多字段"],
        must_tables=["DWD_EV_REPAY_DETAIL"],
        must_columns=[
            ("DWD_EV_REPAY_DETAIL", "int"),
            ("DWD_EV_REPAY_DETAIL", "loan_no"),
        ],
        forbidden_tables=["DWD_EV_REPAY_PLAN"],
    ),
    item(
        id="S049",
        suite="single",
        question="用信申请审批用信额度和申请金额",
        domain="用信",
        tags=["单表", "单表多字段"],
        must_tables=["DWD_EV_INDV_LOAN_APP"],
        must_columns=[
            ("DWD_EV_INDV_LOAN_APP", "apprv_loan_amt"),
            ("DWD_EV_INDV_LOAN_APP", "app_loan_amt"),
        ],
        forbidden_tables=["DWD_EV_INDV_CRD_APP"],
    ),
    item(
        id="S050",
        suite="single",
        question="映射表授信申请编号和产品名称",
        domain="映射",
        tags=["单表", "单表多字段"],
        must_tables=["DWD_APP_MAPPING"],
        must_columns=[
            ("DWD_APP_MAPPING", "crd_app_no"),
            ("DWD_APP_MAPPING", "prd_name"),
        ],
        forbidden_tables=["DWD_PRD_INFO"],
        notes="mapping 自带 prd_name",
    ),
    # —— 多表 13 ——
    item(
        id="M061",
        suite="multi",
        question="用信申请编号映射到借据号及贷款金额",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_APP_MAPPING", "DWD_AR_LOAN_INFO"],
        must_columns=[
            ("DWD_APP_MAPPING", "loan_app_no"),
            ("DWD_APP_MAPPING", "loan_no"),
            ("DWD_AR_LOAN_INFO", "loan_amt"),
        ],
        forbidden_tables=["DWD_EV_INDV_LOAN_APP"],
    ),
    item(
        id="M062",
        suite="multi",
        question="代偿明细借据号及对应借据本金余额",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_SR_CLAIM_DETAIL", "DWD_AR_LOAN_INFO"],
        must_columns=[
            ("DWD_SR_CLAIM_DETAIL", "dc_bal"),
            ("DWD_SR_CLAIM_DETAIL", "loan_no"),
            ("DWD_AR_LOAN_INFO", "prin_bal"),
        ],
    ),
    item(
        id="M063",
        suite="multi",
        question="对公综合授信审批金额及企业客户名称",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_EV_COMP_CRD_APP", "DWD_IP_CORP_CUST_INFO"],
        must_columns=[
            ("DWD_EV_COMP_CRD_APP", "businesssum"),
            ("DWD_IP_CORP_CUST_INFO", "cust_name"),
        ],
        forbidden_tables=["DWD_EV_INDV_CRD_APP", "DWD_IP_INDV_CUST_INFO"],
    ),
    item(
        id="M064",
        suite="multi",
        question="信贷流程客户编号及对应个人客户手机号",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_EV_TRAN_FLOW_INFO", "DWD_IP_INDV_CUST_INFO"],
        must_columns=[
            ("DWD_EV_TRAN_FLOW_INFO", "cust_id"),
            ("DWD_IP_INDV_CUST_INFO", "phone_no"),
        ],
        forbidden_tables=["DWD_IP_CORP_CUST_INFO"],
    ),
    item(
        id="M065",
        suite="multi",
        question="借据合同号对应还款计划应还总金额",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_AR_LOAN_INFO", "DWD_EV_REPAY_PLAN"],
        must_columns=[
            ("DWD_AR_LOAN_INFO", "cont_no"),
            ("DWD_AR_LOAN_INFO", "loan_no"),
            ("DWD_EV_REPAY_PLAN", "rpy_amt"),
        ],
        forbidden_tables=["DWD_EV_REPAY_DETAIL"],
    ),
    item(
        id="M066",
        suite="multi",
        question="产品编码映射表与产品主数据编码一致性",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_APP_MAPPING", "DWD_PRD_INFO"],
        must_columns=[
            ("DWD_APP_MAPPING", "prd_code"),
            ("DWD_PRD_INFO", "prd_code"),
            ("DWD_PRD_INFO", "prd_name"),
        ],
    ),
    item(
        id="M067",
        suite="multi",
        question="放款出账借据编号及借据正常本金余额",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_EV_INDV_LOAN_PUB", "DWD_AR_LOAN_INFO"],
        must_columns=[
            ("DWD_EV_INDV_LOAN_PUB", "duebill_no"),
            ("DWD_AR_LOAN_INFO", "normal_bal"),
        ],
    ),
    item(
        id="M068",
        suite="multi",
        question="逾期追偿借据号及代偿明细代偿本金",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_EV_OVERDUE_REPAY", "DWD_SR_CLAIM_DETAIL"],
        must_columns=[
            ("DWD_EV_OVERDUE_REPAY", "loan_no"),
            ("DWD_SR_CLAIM_DETAIL", "dc_bal"),
        ],
        forbidden_tables=["DWD_EV_REPAY_PLAN"],
    ),
    item(
        id="M069",
        suite="multi",
        question="个人客户证件号及名下借据逾期本金",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_IP_INDV_CUST_INFO", "DWD_AR_LOAN_INFO"],
        must_columns=[
            ("DWD_IP_INDV_CUST_INFO", "idnum"),
            ("DWD_AR_LOAN_INFO", "ovd_bal"),
        ],
        forbidden_tables=["DWD_IP_CORP_CUST_INFO"],
    ),
    item(
        id="M070",
        suite="multi",
        question="三表：映射用信编号借据号及借据贷款金额",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_APP_MAPPING", "DWD_AR_LOAN_INFO", "DWD_EV_INDV_LOAN_APP"],
        must_columns=[
            ("DWD_APP_MAPPING", "loan_app_no"),
            ("DWD_AR_LOAN_INFO", "loan_amt"),
            ("DWD_EV_INDV_LOAN_APP", "app_loan_amt"),
        ],
        difficulty="hard",
    ),
    item(
        id="M071",
        suite="multi",
        question="三表：客户性别用信金额产品渠道",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP", "DWD_PRD_INFO"],
        must_columns=[
            ("DWD_IP_INDV_CUST_INFO", "sex"),
            ("DWD_EV_INDV_LOAN_APP", "app_loan_amt"),
            ("DWD_PRD_INFO", "channel_name"),
        ],
        difficulty="hard",
    ),
    item(
        id="M072",
        suite="multi",
        question="三表：还款计划应还罚息借据号及逾期追偿本金",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_EV_REPAY_PLAN", "DWD_AR_LOAN_INFO", "DWD_EV_OVERDUE_REPAY"],
        must_columns=[
            ("DWD_EV_REPAY_PLAN", "rpy_ovd"),
            ("DWD_AR_LOAN_INFO", "loan_no"),
            ("DWD_EV_OVERDUE_REPAY", "prin_amt"),
        ],
        difficulty="hard",
    ),
    item(
        id="M073",
        suite="multi",
        question="三表：对公客户行业借据金额产品名称",
        domain="多表",
        tags=["多表", "JOIN"],
        must_tables=["DWD_IP_CORP_CUST_INFO", "DWD_AR_LOAN_INFO", "DWD_PRD_INFO"],
        must_columns=[
            ("DWD_IP_CORP_CUST_INFO", "industry_type"),
            ("DWD_AR_LOAN_INFO", "loan_amt"),
            ("DWD_PRD_INFO", "prd_name"),
        ],
        forbidden_tables=["DWD_IP_INDV_CUST_INFO"],
        difficulty="hard",
    ),
]


def main() -> None:
    base: list[dict] = []
    if SRC.exists():
        for line in SRC.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                base.append(json.loads(line))
    else:
        raise SystemExit(f"missing base golden: {SRC}")

    seen_ids = {x["id"] for x in base}
    for row in EXTRA:
        if row["id"] in seen_ids:
            raise SystemExit(f"duplicate id: {row['id']}")
        seen_ids.add(row["id"])

    all_items = base + EXTRA
    if len(all_items) != 100:
        raise SystemExit(f"expected 100 items, got {len(all_items)} (base={len(base)}, extra={len(EXTRA)})")

    n_single = sum(1 for x in all_items if x["suite"] == "single")
    n_multi = sum(1 for x in all_items if x["suite"] == "multi")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in all_items) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(all_items)} items -> {OUT}")
    print(f"  single={n_single} multi={n_multi}")
    print(f"  base={len(base)} extra={len(EXTRA)}")


if __name__ == "__main__":
    main()
