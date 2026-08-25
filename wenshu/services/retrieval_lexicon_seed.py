"""领域召回词典种子包。

业务概念种子写入 L1 synonym（target_type=concept），不绑定物理表/列名。
召回运行时不得 import 本模块；换库后应改 L1，而不是改检索算法。

旧版 SEED_TABLE_HINTS / SEED_COLUMN_* 仅保留供对照与单测 fixture，生产导入走 SEED_BUSINESS_CONCEPTS。
"""

from __future__ import annotations


def _merge_phrase_map(
    base: dict[str, tuple[str, ...]], extra: dict[str, tuple[str, ...]]
) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {k: tuple(v) for k, v in base.items()}
    for key, vals in extra.items():
        prev = list(out.get(key) or ())
        for item in vals:
            if item not in prev:
                prev.insert(0, item)
        out[key] = tuple(prev)
    return out


# 问句片段 → 字段名片段（补充向量未对齐的口语表达）
SEED_COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "职工人数": ("employee", "staff", "emp_num"),
    "客户名称": ("cust_name", "name", "customer_name"),
    "客户姓名": ("cust_name", "name", "customer_name"),
    "姓名": ("name", "cust_name"),
    "结清日期": ("payoff_date", "settle_date", "clear_date"),
    "放款金额": ("business_sum", "loan_amt", "disburse"),
    "首贷": ("first_loan", "is_first_loan"),
    "借据": ("loan_no", "duebill"),
    "授信余额": ("cred_balance", "cust_cred_balance"),
    "逾期": ("duedays", "overdue", "delq"),
    "历史逾期": ("his_duedays", "his_overdue"),
    "分箱": ("box", "bin"),
    "余额": ("bal", "balance"),
    "cm版本": ("_cm", "cm_type"),
    "迁徙率": ("migration", "migr"),
    "产品名称": ("prd_name", "product_name"),
    "产品编码": ("prd_code", "product_code"),
    "申请额度": ("app_loan_amt", "app_cred_amt"),
    "申请金额": ("app_loan_amt",),
    "用信额度": ("app_loan_amt",),
    "用信申请金额": ("app_loan_amt",),
    "申请用信额度": ("app_loan_amt",),
    "审批额度": ("apprv_cred_amt", "apprv_loan_amt"),
    "授信审批额度": ("apprv_cred_amt",),
    "审批授信额度": ("apprv_cred_amt",),
    "授信额度": ("apprv_cred_amt", "app_cred_amt"),
    "渠道名称": ("channel_name",),
    "客户名": ("cust_name", "name", "customer_name"),
    "应还总金额": ("rpy_amt",),
    "拒绝码": ("hit_reason", "reasoncode", "reject"),
    "授信拒绝": ("hit_reason", "reasoncode", "reject"),
    "学历": ("schooling", "diploma", "education"),
    "工作单位": ("unit_name", "company"),
    "年收入": ("income", "per_mon_income"),
    "到期日": ("maturity", "end_date"),
    "期数": ("term_no",),
    "贷款上限": ("loan_limit",),
    "金额上限": ("loan_limit",),
    "产品宽限期": ("grace_day",),
    "官方宽限期": ("grace_day",),
    "逾期宽限期": ("grace_day",),
    "本金余额": ("prin_bal",),
    "贷款本金余额": ("prin_bal",),
    "渠道": ("channel",),
    "审批状态": ("apprv_state", "approve_state", "status"),
    "拒绝原因": ("reason", "hit_reason", "reject"),
    "应还本金": ("rpy_princ", "princ", "principal"),
    "应还利息": ("rpy_int", "interest", "int"),
    "实还本金": ("princ", "paid_princ"),
    "注册资本": ("register_amount", "reg_capital"),
    "企业规模": ("ent_scale", "enterprise_scale"),
    "可用额度": ("avail_amount", "cust_avail"),
    "无抵押": ("unsecured",),
    "婚姻": ("marriage", "marital"),
    "性别": ("sex", "gender"),
    "手机号": ("phone", "mobile"),
    "户籍": ("hhdist", "household", "census"),
    "年利率": ("year_rate", "annual_rate", "rate"),
    "贷款金额": ("loan_amt", "loan_amount"),
    "申请编号": ("app_no", "loan_app_no", "apply_no"),
    "申请时间": ("apply_date", "app_date"),
    "产品类别": ("prd_type", "product_type"),
}

# 问句关键词 → 列名子串（优先精确映射，再回退向量分）
SEED_COLUMN_NAME_PATTERNS: dict[str, tuple[str, ...]] = {
    "职工人数": ("employee_number", "employee_num", "staff_num"),
    "客户名称": ("cust_name", "customer_name"),
    "客户姓名": ("cust_name", "name", "customer_name"),
    "结清日期": ("payoff_date", "settle_date"),
    "放款金额": ("business_sum", "loan_amt", "disburse_amt"),
    "首贷": ("is_first_loan", "first_loan"),
    "借据": ("loan_no", "duebill_no"),
    "实还本金": ("princ", "paid_princ", "rpy_princ"),
    "授信余额": ("cust_cred_balance", "cred_balance"),
    "历史逾期": ("his_duedays_max", "his_overdue"),
    "逾期": ("duedays", "overdue", "delq"),
    "分箱": ("box", "bin"),
    "余额": ("bal", "balance"),
    "产品名称": ("prd_name",),
    "产品编码": ("prd_code",),
    "申请额度": ("app_loan_amt", "app_cred_amt"),
    "申请金额": ("app_loan_amt",),
    "用信额度": ("app_loan_amt",),
    "用信申请金额": ("app_loan_amt",),
    "申请用信额度": ("app_loan_amt",),
    "审批额度": ("apprv_cred_amt", "apprv_loan_amt"),
    "授信审批额度": ("apprv_cred_amt",),
    "审批授信额度": ("apprv_cred_amt",),
    "授信额度": ("apprv_cred_amt", "app_cred_amt"),
    "渠道名称": ("channel_name",),
    "客户名": ("cust_name", "name"),
    "应还总金额": ("rpy_amt",),
    "拒绝码": ("hit_reasoncode", "reasoncode"),
    "授信拒绝": ("hit_reasoncode", "reasoncode"),
    "学历": ("highest_schooling", "diploma"),
    "工作单位": ("unit_name",),
    "年收入": ("per_mon_income",),
    "到期日": ("maturity",),
    "期数": ("term_no",),
    "贷款上限": ("loan_limit",),
    "金额上限": ("loan_limit",),
    "产品宽限期": ("grace_day",),
    "官方宽限期": ("grace_day",),
    "逾期宽限期": ("grace_day",),
    "本金余额": ("prin_bal",),
    "贷款本金余额": ("prin_bal",),
    "渠道": ("channel_name", "channel"),
    "审批状态": ("apprv_state", "approve_state"),
    "拒绝原因": ("hit_reason", "reject_reason", "reasoncode"),
    "应还本金": ("rpy_princ",),
    "应还利息": ("rpy_int",),
    "注册资本": ("register_amount",),
    "企业规模": ("ent_scale",),
    "可用额度": ("cust_avail_amount", "avail_amount"),
    "无抵押": ("unsecured_balance", "unsecured"),
    "婚姻": ("marriage",),
    "性别": ("sex", "gender"),
    "手机号": ("phone_no", "mobile"),
    "户籍": ("hhdist", "household"),
    "年利率": ("year_rate", "annual_rate"),
    "贷款金额": ("loan_amt",),
    "申请编号": ("loan_app_no", "app_no"),
    "申请时间": ("apply_date",),
    "产品类别": ("prd_type",),
}

# 问句片段 → 表名（示例库 demo_*；生产环境请写入 L1 synonym）
SEED_TABLE_HINTS: dict[str, tuple[str, ...]] = {
    "客户": ("demo_customers",),
    "个人客户": ("demo_customers",),
    "客户名": ("demo_customers",),
    "客户姓名": ("demo_customers",),
    "订单": ("demo_orders",),
    "借据": ("demo_orders",),
    "产品": ("demo_products",),
    "产品列表": ("demo_products",),
    "产品名称": ("demo_products",),
}

# 这些列短语只在指定表上算落地；对不上当前聚焦表则禁止单表收口
SEED_COLUMN_HINT_HOME_TABLES: dict[str, tuple[str, ...]] = {
    "产品名称": ("demo_products",),
    "订单金额": ("demo_orders",),
    "客户姓名": ("demo_customers",),
}

SEED_FACT_TABLES_REQUIRE_HINT: tuple[str, ...] = (
    "demo_orders",
)


def seed_column_patterns() -> dict[str, tuple[str, ...]]:
    """列口语 + 列名模式合并，导入时尽量命中真实字段。"""
    return _merge_phrase_map(SEED_COLUMN_HINTS, SEED_COLUMN_NAME_PATTERNS)


# 业务概念 v2：实体（定表）与属性（定列）拆分；仅真正可互换的说法共概念。
# concept_key → 用户可能说法（含 concept_key 自身）
SEED_BUSINESS_CONCEPTS: dict[str, tuple[str, ...]] = {
    # --- 实体：业务对象 / 主题表 ---
    "借据": ("借据", "结清借据", "单笔贷款"),
    "还款明细": ("还款明细", "实还"),
    "还款计划": ("还款计划", "应还"),
    "首贷放款": ("首贷", "放款"),
    "对公客户": ("对公客户", "企业客户"),
    "个人客户": ("个人客户",),
    "产品": ("产品", "产品列表", "授信产品", "产品行业", "产品类别"),
    "用信申请": ("用信申请", "用信"),
    "授信申请": ("授信申请", "授信审批"),
    "逾期追偿": ("追偿", "逾期还款", "逾期"),
    "迁徙率": ("迁徙率", "cm版本"),
    # --- 属性：字段语义 / 度量维度 ---
    "借据号": ("借据号", "贷款编号", "借据编号"),
    "产品名称": ("产品名称", "品名"),
    "产品编码": ("产品编码",),
    "产品宽限期": ("产品宽限期", "官方宽限期", "逾期宽限期"),
    "本金余额": ("本金余额", "贷款本金余额"),
    "贷款金额": ("贷款金额", "放款金额"),
    "应还总金额": ("应还总金额",),
    "应还本金": ("应还本金",),
    "应还利息": ("应还利息",),
    "实还本金": ("实还本金",),
    "授信余额": ("授信余额", "可用额度"),
    "客户名称": ("客户名称", "客户名", "客户姓名", "姓名"),
    "企业规模": ("企业规模",),
    "注册资本": ("注册资本",),
    "婚姻状况": ("婚姻", "婚姻状况"),
    "性别": ("性别",),
    "学历": ("学历",),
    "手机号": ("手机号",),
    "户籍": ("户籍",),
    "职工人数": ("职工人数",),
    "申请编号": ("申请编号", "用信申请编号"),
    "申请时间": ("申请时间",),
    "申请额度": ("申请额度", "申请金额", "用信额度", "用信申请金额", "申请用信额度"),
    "审批额度": ("审批额度", "授信审批额度", "审批授信额度", "授信额度"),
    "审批状态": ("审批状态",),
    "拒绝原因": ("拒绝原因", "授信拒绝"),
    "拒绝码": ("拒绝码",),
    "渠道名称": ("渠道名称", "渠道"),
    "结清日期": ("结清日期",),
    "年利率": ("年利率",),
    "期数": ("期数",),
    "贷款上限": ("贷款上限", "金额上限"),
    "工作单位": ("工作单位",),
    "年收入": ("年收入",),
    "到期日": ("到期日",),
    "无抵押": ("无抵押",),
    "历史逾期": ("历史逾期",),
    "分箱": ("分箱",),
}

# 实体类概念：用于多表判定与 table 分路（不含纯属性概念）
SEED_ENTITY_CONCEPT_KEYS: frozenset[str] = frozenset(
    {
        "借据",
        "还款明细",
        "还款计划",
        "首贷放款",
        "对公客户",
        "个人客户",
        "产品",
        "用信申请",
        "授信申请",
        "逾期追偿",
    }
)

# 属性概念：一旦出现通常需跨表 JOIN（非单表客户/借据上的属性）
SEED_CROSS_TABLE_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {
        "产品名称",
        "产品编码",
        "产品宽限期",
        "本金余额",
        "贷款金额",
        "应还总金额",
        "应还本金",
        "应还利息",
        "实还本金",
        "申请编号",
        "申请额度",
        "审批额度",
        "审批状态",
        "拒绝原因",
        "拒绝码",
        "渠道名称",
        "授信余额",
        "结清日期",
        "年利率",
        "职工人数",
    }
)
