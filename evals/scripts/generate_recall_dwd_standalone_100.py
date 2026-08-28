#!/usr/bin/env python3
"""独立生成 100 道 DWD 召回黄金题（不沿用 recall_v2_dwd.jsonl 的 77 题）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "evals" / "golden" / "recall_dwd_standalone_100.jsonl"

# (id, question, domain, tables, columns, forbidden)
# columns: list of (table, column)

COL_HINT = {
    "age": "DWD_IP_INDV_CUST_INFO",
    "cust_id": "DWD_IP_INDV_CUST_INFO",
    "name": "DWD_IP_INDV_CUST_INFO",
    "phone_no": "DWD_IP_INDV_CUST_INFO",
    "marriage": "DWD_IP_INDV_CUST_INFO",
    "sex": "DWD_IP_INDV_CUST_INFO",
    "highest_schooling": "DWD_IP_INDV_CUST_INFO",
    "hhdist": "DWD_IP_INDV_CUST_INFO",
    "per_mon_income": "DWD_IP_INDV_CUST_INFO",
    "unit_name": "DWD_IP_INDV_CUST_INFO",
    "resiaddr": "DWD_IP_INDV_CUST_INFO",
    "nativeplace": "DWD_IP_INDV_CUST_INFO",
    "idnum": "DWD_IP_INDV_CUST_INFO",
    "cust_name": "DWD_IP_CORP_CUST_INFO",
    "employee_number": "DWD_IP_CORP_CUST_INFO",
    "register_amount": "DWD_IP_CORP_CUST_INFO",
    "ent_scale": "DWD_IP_CORP_CUST_INFO",
    "license_maturity": "DWD_IP_CORP_CUST_INFO",
    "industry_type": "DWD_IP_CORP_CUST_INFO",
    "app_loan_amt": "DWD_EV_INDV_LOAN_APP",
    "loan_app_no": "DWD_EV_INDV_LOAN_APP",
    "loan_purpose": "DWD_EV_INDV_LOAN_APP",
    "marr_status": "DWD_EV_INDV_LOAN_APP",
    "living_address": "DWD_EV_INDV_LOAN_APP",
    "apply_date": "DWD_EV_INDV_LOAN_APP",
    "prd_code": "DWD_EV_INDV_LOAN_APP",
    "apprv_loan_amt": "DWD_EV_INDV_LOAN_APP",
    "phone_no_la": ("DWD_EV_INDV_LOAN_APP", "phone_no"),
    "apprv_cred_amt": "DWD_EV_INDV_CRD_APP",
    "app_cred_amt": "DWD_EV_INDV_CRD_APP",
    "hit_reasoncode_set": "DWD_EV_INDV_CRD_APP",
    "apprv_state": "DWD_EV_INDV_CRD_APP",
    "app_no": "DWD_EV_INDV_CRD_APP",
    "ethnic": "DWD_EV_INDV_CRD_APP",
    "loan_no": "DWD_AR_LOAN_INFO",
    "loan_amt": "DWD_AR_LOAN_INFO",
    "prin_bal": "DWD_AR_LOAN_INFO",
    "payoff_date": "DWD_AR_LOAN_INFO",
    "year_rate": "DWD_AR_LOAN_INFO",
    "ovd_bal": "DWD_AR_LOAN_INFO",
    "grace_day_ar": ("DWD_AR_LOAN_INFO", "grace_day"),
    "name_ar": ("DWD_AR_LOAN_INFO", "name"),
    "normal_bal": "DWD_AR_LOAN_INFO",
    "end_date": "DWD_AR_LOAN_INFO",
    "cont_no": "DWD_AR_LOAN_INFO",
    "prd_name": "DWD_PRD_INFO",
    "prd_code_p": ("DWD_PRD_INFO", "prd_code"),
    "prd_type": "DWD_PRD_INFO",
    "channel_name": "DWD_PRD_INFO",
    "grace_day_p": ("DWD_PRD_INFO", "grace_day"),
    "loan_limit": "DWD_PRD_INFO",
    "credit_method": "DWD_PRD_INFO",
    "credit_type": "DWD_PRD_INFO",
    "producttype": "DWD_PRD_INFO",
    "rpy_princ": "DWD_EV_REPAY_PLAN",
    "rpy_int": "DWD_EV_REPAY_PLAN",
    "rpy_amt": "DWD_EV_REPAY_PLAN",
    "rpy_ovd": "DWD_EV_REPAY_PLAN",
    "term_no": "DWD_EV_REPAY_PLAN",
    "loan_no_rp": ("DWD_EV_REPAY_PLAN", "loan_no"),
    "princ": "DWD_EV_REPAY_DETAIL",
    "int_d": "DWD_EV_REPAY_DETAIL",  # column int
    "repay_date": "DWD_EV_REPAY_DETAIL",
    "loan_no_rd": ("DWD_EV_REPAY_DETAIL", "loan_no"),
    "ovd": "DWD_EV_REPAY_DETAIL",
    "prin_amt": "DWD_EV_OVERDUE_REPAY",
    "int_amt": "DWD_EV_OVERDUE_REPAY",
    "int_repay_date": "DWD_EV_OVERDUE_REPAY",
    "business_sum": "DWD_EV_INDV_LOAN_PUB",
    "duebill_no": "DWD_EV_INDV_LOAN_PUB",
    "contract_serial_no": "DWD_EV_INDV_LOAN_PUB",
    "executerate": "DWD_EV_INDV_LOAN_PUB",
    "maturity": "DWD_EV_INDV_LOAN_PUB",
    "is_first_loan": "DWD_EV_INDV_LOAN_PUB",
    "customer_name": "DWD_EV_INDV_LOAN_PUB",
    "loan_app_no_map": ("DWD_APP_MAPPING", "loan_app_no"),
    "loan_no_map": ("DWD_APP_MAPPING", "loan_no"),
    "crd_app_no_map": ("DWD_APP_MAPPING", "crd_app_no"),
    "prd_name_map": ("DWD_APP_MAPPING", "prd_name"),
    "prd_code_map": ("DWD_APP_MAPPING", "prd_code"),
    "dc_bal": "DWD_SR_CLAIM_DETAIL",
    "loan_no_sc": ("DWD_SR_CLAIM_DETAIL", "loan_no"),
    "businesssum": "DWD_EV_COMP_CRD_APP",
    "customername": "DWD_EV_COMP_CRD_APP",
    "app_no_tf": ("DWD_EV_TRAN_FLOW_INFO", "app_no"),
    "app_node": "DWD_EV_TRAN_FLOW_INFO",
    "cust_id_tf": ("DWD_EV_TRAN_FLOW_INFO", "cust_id"),
}


def _resolve_col(key: str, tables: list[str]) -> tuple[str, str]:
    hint = COL_HINT[key]
    if isinstance(hint, tuple):
        return hint
    if hint in tables:
        col = key.split("_")[0] if key.endswith("_ar") or key.endswith("_p") else key
        if key == "grace_day_ar":
            col = "grace_day"
        elif key == "grace_day_p":
            col = "grace_day"
        elif key == "name_ar":
            col = "name"
        elif key == "prd_code_p":
            col = "prd_code"
        elif key == "phone_no_la":
            col = "phone_no"
        elif key.endswith("_map"):
            col = key.replace("_map", "").replace("loan_app_no", "loan_app_no")
            mapping = {
                "loan_app_no": "loan_app_no",
                "loan_no": "loan_no",
                "crd_app_no": "crd_app_no",
                "prd_name": "prd_name",
                "prd_code": "prd_code",
            }
            base = key.replace("_map", "")
            col = base
        elif key.endswith("_rp") or key.endswith("_rd") or key.endswith("_sc"):
            col = "loan_no"
        elif key == "int_d":
            col = "int"
        elif key.endswith("_tf"):
            col = key.replace("_tf", "")
        else:
            col = key
        return hint, col
    return tables[0], key


def _row(
    qid: str,
    question: str,
    domain: str,
    tables: list[str],
    col_keys: list[str],
    forbidden: list[str] | None = None,
) -> dict:
    suite = "single" if len(tables) == 1 else "multi"
    must_columns = [{"table": _resolve_col(k, tables)[0], "column": _resolve_col(k, tables)[1]} for k in col_keys]
    if suite == "single":
        tags = ["单表", "单表多字段" if len(col_keys) >= 2 else "单表单字段"]
        field_slice = "multi_field" if len(col_keys) >= 2 else "single_field"
        difficulty = "easy"
    else:
        tags = ["多表", "JOIN"]
        field_slice = "multi_table"
        difficulty = "hard" if len(tables) >= 3 else "medium"
    return {
        "id": qid,
        "suite": suite,
        "question": question,
        "domain": domain,
        "difficulty": difficulty,
        "tags": tags,
        "must_tables": tables,
        "nice_tables": [],
        "must_columns": must_columns,
        "forbidden_tables": forbidden or [],
        "notes": "standalone_gen",
        "field_slice": field_slice,
    }


# fmt: off
SINGLE = [
    ("N_S01", "统计个人客户平均年龄", "客户", ["DWD_IP_INDV_CUST_INFO"], ["age"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_S02", "对公客户家数统计", "客户", ["DWD_IP_CORP_CUST_INFO"], ["cust_id"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_S03", "本科以上学历个人客户人数", "客户", ["DWD_IP_INDV_CUST_INFO"], ["highest_schooling"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_S04", "全部用信申请金额求和", "用信", ["DWD_EV_INDV_LOAN_APP"], ["app_loan_amt"], []),
    ("N_S05", "授信审批额度总计", "授信", ["DWD_EV_INDV_CRD_APP"], ["apprv_cred_amt"], ["DWD_EV_INDV_LOAN_APP"]),
    ("N_S06", "借据本金余额汇总", "借据", ["DWD_AR_LOAN_INFO"], ["prin_bal"], []),
    ("N_S07", "存在逾期的借据有多少", "借据", ["DWD_AR_LOAN_INFO"], ["ovd_bal"], []),
    ("N_S08", "逾期处置追偿本金总额", "逾期", ["DWD_EV_OVERDUE_REPAY"], ["prin_amt"], ["DWD_EV_REPAY_PLAN"]),
    ("N_S09", "各信贷产品宽限期天数", "产品", ["DWD_PRD_INFO"], ["grace_day_p"], []),
    ("N_S10", "实还罚息金额合计", "还款", ["DWD_EV_REPAY_DETAIL"], ["ovd"], ["DWD_EV_REPAY_PLAN"]),
    ("N_S11", "个人客户按性别分组统计", "客户", ["DWD_IP_INDV_CUST_INFO"], ["sex"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_S12", "借据层面宽限天数分布", "借据", ["DWD_AR_LOAN_INFO"], ["grace_day_ar"], []),
    ("N_S13", "还款计划应还利息总额", "还款", ["DWD_EV_REPAY_PLAN"], ["rpy_int"], ["DWD_EV_REPAY_DETAIL"]),
    ("N_S14", "逾期追偿场景下用户还款日期分布", "逾期", ["DWD_EV_OVERDUE_REPAY"], ["int_repay_date"], ["DWD_EV_REPAY_PLAN"]),
    ("N_S15", "对公客户营业执照失效日期", "客户", ["DWD_IP_CORP_CUST_INFO"], ["license_maturity"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_S16", "查询个人客户身份证件号码", "客户", ["DWD_IP_INDV_CUST_INFO"], ["idnum"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_S17", "个人客户月收入汇总分析", "客户", ["DWD_IP_INDV_CUST_INFO"], ["per_mon_income"], []),
    ("N_S18", "代偿记录中的代偿本金余额", "代偿", ["DWD_SR_CLAIM_DETAIL"], ["dc_bal"], []),
    ("N_S19", "对公综合授信业务金额汇总", "授信", ["DWD_EV_COMP_CRD_APP"], ["businesssum"], ["DWD_EV_INDV_CRD_APP"]),
    ("N_S20", "信贷审批流程当前节点分布", "流程", ["DWD_EV_TRAN_FLOW_INFO"], ["app_node"], ["DWD_EV_INDV_LOAN_APP"]),
    ("N_S21", "个人客户婚姻与性别交叉统计", "客户", ["DWD_IP_INDV_CUST_INFO"], ["marriage", "sex"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_S22", "授信拒绝码与审批状态明细", "授信", ["DWD_EV_INDV_CRD_APP"], ["hit_reasoncode_set", "apprv_state"], []),
    ("N_S23", "还款计划每期应还本金与应还利息", "还款", ["DWD_EV_REPAY_PLAN"], ["rpy_princ", "rpy_int"], ["DWD_EV_OVERDUE_REPAY"]),
    ("N_S24", "产品目录中的名称与编码清单", "产品", ["DWD_PRD_INFO"], ["prd_name", "prd_code_p"], []),
    ("N_S25", "借据放款金额与执行年利率", "借据", ["DWD_AR_LOAN_INFO"], ["loan_amt", "year_rate"], []),
    ("N_S26", "个人客户联系电话与户籍省份", "客户", ["DWD_IP_INDV_CUST_INFO"], ["phone_no", "hhdist"], []),
    ("N_S27", "对公客户注册资本与企业规模", "客户", ["DWD_IP_CORP_CUST_INFO"], ["register_amount", "ent_scale"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_S28", "职工人数超过100的企业名称", "客户", ["DWD_IP_CORP_CUST_INFO"], ["employee_number", "cust_name"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_S29", "渠道维度产品单笔贷款上限", "产品", ["DWD_PRD_INFO"], ["channel_name", "loan_limit"], []),
    ("N_S30", "已结清借据的结清日与借款人姓名", "借据", ["DWD_AR_LOAN_INFO"], ["payoff_date", "name_ar"], []),
    ("N_S31", "首贷标识与本次出账放款金额", "放款", ["DWD_EV_INDV_LOAN_PUB"], ["is_first_loan", "business_sum"], []),
    ("N_S32", "用信流水号及申请用信额度", "用信", ["DWD_EV_INDV_LOAN_APP"], ["loan_app_no", "app_loan_amt"], []),
    ("N_S33", "授信申请日期与审批结果状态", "授信", ["DWD_EV_INDV_CRD_APP"], ["apply_date", "apprv_state"], ["DWD_EV_INDV_LOAN_APP"]),
    ("N_S34", "实还本金按借据号汇总", "还款", ["DWD_EV_REPAY_DETAIL"], ["princ", "loan_no_rd"], ["DWD_EV_REPAY_PLAN"]),
    ("N_S35", "产品编码与产品类别对照", "产品", ["DWD_PRD_INFO"], ["prd_code_p", "prd_type"], []),
    ("N_S36", "申请映射表用信编号与借据号", "映射", ["DWD_APP_MAPPING"], ["loan_app_no_map", "loan_no_map"], []),
    ("N_S37", "映射表授信编号与产品名称", "映射", ["DWD_APP_MAPPING"], ["crd_app_no_map", "prd_name_map"], ["DWD_PRD_INFO"]),
    ("N_S38", "借据合同编号与借据号清单", "借据", ["DWD_AR_LOAN_INFO"], ["cont_no", "loan_no"], []),
    ("N_S39", "产品增信方式与授信类型", "产品", ["DWD_PRD_INFO"], ["credit_method", "credit_type"], []),
    ("N_S40", "实还利息及对应借据号", "还款", ["DWD_EV_REPAY_DETAIL"], ["int_d", "loan_no_rd"], ["DWD_EV_REPAY_PLAN"]),
]

MULTI = [
    ("N_M01", "35岁以上个人客户及其用信申请金额", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP"], ["age", "cust_id", "app_loan_amt"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_M02", "个人客户主档姓名手机与授信审批额度", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_CRD_APP"], ["name", "phone_no", "apprv_cred_amt"], ["DWD_EV_INDV_LOAN_APP"]),
    ("N_M03", "借据金额及关联还款计划应还本金", "多表", ["DWD_AR_LOAN_INFO", "DWD_EV_REPAY_PLAN"], ["loan_no", "loan_amt", "rpy_princ"], ["DWD_EV_REPAY_DETAIL"]),
    ("N_M04", "借据对应产品名称与本金余额", "多表", ["DWD_AR_LOAN_INFO", "DWD_PRD_INFO"], ["prd_code_p", "prin_bal", "prd_name"], []),
    ("N_M05", "用信申请关联产品名称与申请额度", "多表", ["DWD_EV_INDV_LOAN_APP", "DWD_PRD_INFO"], ["prd_code", "app_loan_amt", "prd_name"], []),
    ("N_M06", "逾期追偿本金及借据客户姓名", "多表", ["DWD_EV_OVERDUE_REPAY", "DWD_AR_LOAN_INFO"], ["prin_amt", "loan_no", "name_ar"], ["DWD_EV_REPAY_PLAN"]),
    ("N_M07", "实还本金与实还日期按借据汇总", "多表", ["DWD_EV_REPAY_DETAIL", "DWD_AR_LOAN_INFO"], ["princ", "repay_date", "loan_no"], ["DWD_EV_REPAY_PLAN"]),
    ("N_M08", "出账放款金额及对应产品名称", "多表", ["DWD_EV_INDV_LOAN_PUB", "DWD_PRD_INFO"], ["business_sum", "customer_name", "prd_name"], []),
    ("N_M09", "40岁以上已婚客户授信额度与用信额度", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_CRD_APP", "DWD_EV_INDV_LOAN_APP"], ["age", "marriage", "app_cred_amt", "app_loan_amt"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_M10", "产品宽限期及下属借据逾期本金", "多表", ["DWD_PRD_INFO", "DWD_AR_LOAN_INFO"], ["grace_day_p", "prd_code_p", "ovd_bal"], []),
    ("N_M11", "用信编号映射借据号及贷款金额", "多表", ["DWD_APP_MAPPING", "DWD_AR_LOAN_INFO"], ["loan_app_no_map", "loan_no_map", "loan_amt"], ["DWD_EV_INDV_LOAN_APP"]),
    ("N_M12", "出账借据编号与借据正常本金余额", "多表", ["DWD_EV_INDV_LOAN_PUB", "DWD_AR_LOAN_INFO"], ["duebill_no", "normal_bal"], []),
    ("N_M13", "计划应还利息对比明细实还利息", "多表", ["DWD_EV_REPAY_PLAN", "DWD_EV_REPAY_DETAIL"], ["rpy_int", "int_d", "term_no"], ["DWD_EV_OVERDUE_REPAY"]),
    ("N_M14", "已婚客户分产品统计用信笔数金额", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP", "DWD_PRD_INFO"], ["marriage", "app_loan_amt", "prd_name"], []),
    ("N_M15", "客户年收入单位及用信贷款用途", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP"], ["per_mon_income", "unit_name", "loan_purpose"], []),
    ("N_M16", "户籍省份与名下借据逾期本金", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_AR_LOAN_INFO"], ["hhdist", "cust_id", "ovd_bal"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_M17", "应还总额与实还本息按期次对齐", "多表", ["DWD_EV_REPAY_PLAN", "DWD_EV_REPAY_DETAIL"], ["rpy_amt", "princ", "int_d"], []),
    ("N_M18", "授信审批额度与后续用信申请金额", "多表", ["DWD_EV_INDV_CRD_APP", "DWD_EV_INDV_LOAN_APP"], ["apprv_cred_amt", "app_loan_amt", "cust_id"], []),
    ("N_M19", "客户性别分布及用信申请量", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP"], ["sex", "cust_id"], []),
    ("N_M20", "对公客户名称规模及授信产品名", "多表", ["DWD_IP_CORP_CUST_INFO", "DWD_PRD_INFO"], ["cust_name", "ent_scale", "prd_name"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_M21", "借据年利率与产品官方宽限期", "多表", ["DWD_AR_LOAN_INFO", "DWD_PRD_INFO"], ["year_rate", "grace_day_ar", "prd_code_p"], []),
    ("N_M22", "逾期追偿本金与计划应还本金", "多表", ["DWD_EV_OVERDUE_REPAY", "DWD_EV_REPAY_PLAN"], ["prin_amt", "rpy_princ", "loan_no_rp"], ["DWD_EV_REPAY_DETAIL"]),
    ("N_M23", "用信预留手机号与授信审批状态", "多表", ["DWD_EV_INDV_LOAN_APP", "DWD_EV_INDV_CRD_APP"], ["phone_no_la", "apprv_state"], []),
    ("N_M24", "出账执行利率与借据年利率", "多表", ["DWD_EV_INDV_LOAN_PUB", "DWD_AR_LOAN_INFO"], ["executerate", "year_rate"], []),
    ("N_M25", "客户主档学历与授信审批额度", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_CRD_APP"], ["highest_schooling", "apprv_cred_amt"], []),
    ("N_M26", "渠道名称及各渠道用信金额", "多表", ["DWD_PRD_INFO", "DWD_EV_INDV_LOAN_APP"], ["channel_name", "app_loan_amt", "prd_code"], []),
    ("N_M27", "借据结清日与明细实还日期", "多表", ["DWD_AR_LOAN_INFO", "DWD_EV_REPAY_DETAIL"], ["payoff_date", "repay_date", "loan_no"], []),
    ("N_M28", "对公注册资本与关联借据贷款金额", "多表", ["DWD_IP_CORP_CUST_INFO", "DWD_AR_LOAN_INFO"], ["register_amount", "loan_amt"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_M29", "客户婚姻与用信申请婚姻字段", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP"], ["marriage", "marr_status"], []),
    ("N_M30", "计划应还本金与借据正常本金余额", "多表", ["DWD_EV_REPAY_PLAN", "DWD_AR_LOAN_INFO"], ["rpy_princ", "normal_bal", "loan_no"], []),
    ("N_M31", "授信拒绝码与用信申请流水号", "多表", ["DWD_EV_INDV_CRD_APP", "DWD_EV_INDV_LOAN_APP"], ["hit_reasoncode_set", "loan_app_no"], []),
    ("N_M32", "借据产品编码与产品增信方式", "多表", ["DWD_AR_LOAN_INFO", "DWD_PRD_INFO"], ["prd_code_p", "credit_method"], []),
    ("N_M33", "客户籍贯与授信申请民族", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_CRD_APP"], ["nativeplace", "ethnic"], []),
    ("N_M34", "追偿利息与计划应还罚息", "多表", ["DWD_EV_OVERDUE_REPAY", "DWD_EV_REPAY_PLAN"], ["int_amt", "rpy_ovd"], []),
    ("N_M35", "用信与借据产品编码一致性", "多表", ["DWD_EV_INDV_LOAN_APP", "DWD_AR_LOAN_INFO"], ["prd_code", "loan_no"], []),
    ("N_M36", "客户与用信申请居住地址", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP"], ["resiaddr", "living_address"], []),
    ("N_M37", "对公行业分类与产品行业类别", "多表", ["DWD_IP_CORP_CUST_INFO", "DWD_PRD_INFO"], ["industry_type", "producttype"], []),
    ("N_M38", "出账到期日与借据结束日期", "多表", ["DWD_EV_INDV_LOAN_PUB", "DWD_AR_LOAN_INFO"], ["maturity", "end_date"], []),
    ("N_M39", "代偿借据号及借据本金余额", "多表", ["DWD_SR_CLAIM_DETAIL", "DWD_AR_LOAN_INFO"], ["dc_bal", "loan_no_sc", "prin_bal"], []),
    ("N_M40", "流程客户号与个人客户手机号", "多表", ["DWD_EV_TRAN_FLOW_INFO", "DWD_IP_INDV_CUST_INFO"], ["cust_id_tf", "phone_no"], ["DWD_IP_CORP_CUST_INFO"]),
    ("N_M41", "对公综合授信金额与企业客户名", "多表", ["DWD_EV_COMP_CRD_APP", "DWD_IP_CORP_CUST_INFO"], ["businesssum", "cust_name"], ["DWD_EV_INDV_CRD_APP", "DWD_IP_INDV_CUST_INFO"]),
    ("N_M42", "映射产品编码与产品主数据编码", "多表", ["DWD_APP_MAPPING", "DWD_PRD_INFO"], ["prd_code_map", "prd_code_p", "prd_name"], []),
    ("N_M43", "三表：客户年龄授信额度用信金额", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_CRD_APP", "DWD_EV_INDV_LOAN_APP"], ["age", "apprv_cred_amt", "app_loan_amt", "cust_id"], []),
    ("N_M44", "三表：借据产品名与计划应还本金", "多表", ["DWD_AR_LOAN_INFO", "DWD_PRD_INFO", "DWD_EV_REPAY_PLAN"], ["loan_no", "prd_name", "rpy_princ"], []),
    ("N_M45", "三表：婚姻状况用信金额产品名", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP", "DWD_PRD_INFO"], ["marriage", "app_loan_amt", "prd_name"], []),
    ("N_M46", "三表：追偿本金借据号客户姓名", "多表", ["DWD_EV_OVERDUE_REPAY", "DWD_AR_LOAN_INFO", "DWD_IP_INDV_CUST_INFO"], ["prin_amt", "loan_no", "name_ar"], []),
    ("N_M47", "三表：授信用信与出账放款金额", "多表", ["DWD_EV_INDV_CRD_APP", "DWD_EV_INDV_LOAN_APP", "DWD_EV_INDV_LOAN_PUB"], ["apprv_cred_amt", "app_loan_amt", "business_sum"], []),
    ("N_M48", "三表：计划明细借据应还实还本金", "多表", ["DWD_EV_REPAY_PLAN", "DWD_EV_REPAY_DETAIL", "DWD_AR_LOAN_INFO"], ["rpy_princ", "princ", "loan_no"], []),
    ("N_M49", "三表：对公名规模借据贷款金额", "多表", ["DWD_IP_CORP_CUST_INFO", "DWD_AR_LOAN_INFO", "DWD_PRD_INFO"], ["cust_name", "ent_scale", "loan_amt"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_M50", "三表：学历拒绝码与用信申请金额", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_CRD_APP", "DWD_EV_INDV_LOAN_APP"], ["highest_schooling", "hit_reasoncode_set", "app_loan_amt"], []),
    ("N_M51", "三表：映射用信号借据号及贷款额", "多表", ["DWD_APP_MAPPING", "DWD_AR_LOAN_INFO", "DWD_EV_INDV_LOAN_APP"], ["loan_app_no_map", "loan_amt", "app_loan_amt"], []),
    ("N_M52", "三表：性别用信金额产品渠道", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP", "DWD_PRD_INFO"], ["sex", "app_loan_amt", "channel_name"], []),
    ("N_M53", "三表：应还罚息借据号追偿本金", "多表", ["DWD_EV_REPAY_PLAN", "DWD_AR_LOAN_INFO", "DWD_EV_OVERDUE_REPAY"], ["rpy_ovd", "loan_no", "prin_amt"], []),
    ("N_M54", "三表：对公行业借据金额产品名", "多表", ["DWD_IP_CORP_CUST_INFO", "DWD_AR_LOAN_INFO", "DWD_PRD_INFO"], ["industry_type", "loan_amt", "prd_name"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_M55", "三表：证件号借据逾期与客户主档", "多表", ["DWD_IP_INDV_CUST_INFO", "DWD_AR_LOAN_INFO", "DWD_EV_OVERDUE_REPAY"], ["idnum", "ovd_bal", "prin_amt"], []),
    ("N_M56", "三表：出账借据号计划应还总金额", "多表", ["DWD_EV_INDV_LOAN_PUB", "DWD_AR_LOAN_INFO", "DWD_EV_REPAY_PLAN"], ["duebill_no", "loan_no", "rpy_amt"], []),
    ("N_M57", "三表：代偿本金借据号追偿本金", "多表", ["DWD_SR_CLAIM_DETAIL", "DWD_AR_LOAN_INFO", "DWD_EV_OVERDUE_REPAY"], ["dc_bal", "loan_no_sc", "prin_amt"], []),
    ("N_M58", "三表：流程申请号客户手机与用信号", "多表", ["DWD_EV_TRAN_FLOW_INFO", "DWD_IP_INDV_CUST_INFO", "DWD_EV_INDV_LOAN_APP"], ["app_no_tf", "phone_no", "loan_app_no"], []),
    ("N_M59", "三表：对公综合授信名与产品编码", "多表", ["DWD_EV_COMP_CRD_APP", "DWD_IP_CORP_CUST_INFO", "DWD_PRD_INFO"], ["businesssum", "customername", "prd_code_p"], ["DWD_IP_INDV_CUST_INFO"]),
    ("N_M60", "三表：结清日实还日及借据号", "多表", ["DWD_AR_LOAN_INFO", "DWD_EV_REPAY_DETAIL", "DWD_EV_REPAY_PLAN"], ["payoff_date", "repay_date", "loan_no_rp"], []),
]
# fmt: on


def _validate(items: list[dict]) -> None:
    from sqlalchemy import text
    from db_config import get_meta_mysql_engine

    eng = get_meta_mysql_engine()
    cols: set[tuple[str, str]] = set()
    with eng.connect() as c:
        for t, col in c.execute(
            text(
                """
                SELECT lower(t.table_name), lower(c.column_name)
                FROM column_meta c
                JOIN table_meta t ON c.table_id = t.table_id
                WHERE t.is_enabled = 1
                """
            )
        ):
            cols.add((t, col))
    missing = []
    for row in items:
        for mc in row["must_columns"]:
            key = (mc["table"].lower(), mc["column"].lower())
            if key not in cols:
                missing.append((row["id"], key))
    if missing:
        raise SystemExit(f"invalid columns: {missing[:20]} ... total={len(missing)}")


def main() -> None:
    items = [_row(*spec) for spec in SINGLE] + [_row(*spec) for spec in MULTI]
    if len(items) != 100:
        raise SystemExit(f"expected 100, got {len(items)}")
    ids = [x["id"] for x in items]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate ids")
    _validate(items)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n",
        encoding="utf-8",
    )
    n_s = sum(1 for x in items if x["suite"] == "single")
    n_m = sum(1 for x in items if x["suite"] == "multi")
    print(f"Wrote {len(items)} standalone items -> {OUT}")
    print(f"  single={n_s} multi={n_m}")


if __name__ == "__main__":
    main()
