# 召回评测报告

- 时间：None
- 黄金集：`evals/golden/recall_v2_dwd_100.jsonl`
- collection：`None` / points=None
- 库范围：current_raw None
- 检索模式：**legacy**
- 检索 limit：30；报告 K：[5, 10, 15, 30]
- 题量：单表 40 / 多表 60 / 合计正例 100
- Table MRR（全部）：0.9367

## 单表 vs 多表（核心对比 @10）

| 分区 | 题数 | Table Hit@10 | Any-Table@10 | Column Hit@10 | Forbidden@10 | MRR |
|------|------|--------------|--------------|---------------|--------------|-----|
| 单表 | 40 | 97.5% | 97.5% | 95.0% | 5.0% | 0.9042 |
| 多表 | 60 | 86.7% | 100.0% | 33.3% | 3.3% | 0.9583 |
| 合计 | 100 | 91.0% | 99.0% | 58.0% | 4.0% | 0.9367 |

## 单表 · 单字段 vs 多字段（@10）

| 分区 | 题数 | Table Hit@10 | Column Hit@10 | Forbidden@10 |
|------|------|--------------|---------------|--------------|
| 单表单字段 | 20 | 95.0% | 95.0% | 5.0% |
| 单表多字段 | 20 | 100.0% | 95.0% | 5.0% |

## 全部正例 · 主指标

| K | Table Hit | Any-Table Hit | Column Hit | Forbidden@K |
|---|-----------|---------------|------------|-------------|
| @5 | 91.0% | 99.0% | 50.0% | 4.0% |
| @10 | 91.0% | 99.0% | 58.0% | 4.0% |
| @15 | 91.0% | 99.0% | 63.0% | 4.0% |
| @30 | 91.0% | 99.0% | 76.0% | 4.0% |

## 按题型切片（主看 Table Hit@10）

| 题型 | 题数 | Table Hit@10 | Forbidden@10 |
|------|------|--------------|--------------|
| 全部正例 | 100 | 91.0% | 4.0% |
| JOIN | 60 | 86.7% | 3.3% |
| 单表 | 40 | 97.5% | 5.0% |
| 单表单字段 | 20 | 95.0% | 5.0% |
| 单表多字段 | 20 | 100.0% | 5.0% |
| 多表 | 60 | 86.7% | 3.3% |

## S1 / S2 精选（全集 Hit，非 Top-K 槽位）

S1/S2 是 LLM 从检索池 S_rtrv 中选出的精选集合，列数不固定；**不要**与历史 Hit@10 做同字段数对比。第 2 轮强制补全，空结果会启发式补列。

| 分区 | 题数 | S1 Table | S1 Column | S1 均列数 | S2 Table | S2 Column | S2 均列数 |
|------|------|----------|-----------|-----------|----------|-----------|-----------|
| 单表单字段 | 20 | 95.0% | 90.0% | 7.2 | 100.0% | 95.0% | 28.1 |
| 单表多字段 | 20 | 100.0% | 95.0% | 9.5 | 100.0% | 100.0% | 31.4 |
| 单表 | 40 | 97.5% | 92.5% | 8.4 | 100.0% | 97.5% | 29.8 |
| 多表 | 60 | 85.0% | 55.0% | 13.6 | 96.7% | 75.0% | 31.6 |
| 合计 | 100 | 90.0% | 70.0% | 11.5 | 98.0% | 84.0% | 30.9 |

- S1 来源：llm=1, llm_cover=99
- S2 来源：llm=1, llm_cover=69, llm_empty_widen_cover=30

## MVP 判定

- **达标**：Table Hit@10 = 91.0% ≥ 80%

## 失败题（Table Hit@10 未过：单表 1 / 多表 8 / 共 9）

### N_S19 [single] · 对公综合授信业务金额汇总
- must_tables：['DWD_EV_COMP_CRD_APP']
- expanded_tables：['dwd_prd_info']
- first_hit_rank（expanded）：None
- top_tables（列槽@10）：['dwd_prd_info']
- hits：column:dwd_prd_info.prd_name(0.5438), column:dwd_prd_info.prd_type(0.5427), column:dwd_prd_info.prd_amt(0.5419), column:dwd_prd_info.credit_type(0.5843), column:dwd_prd_info.start_date(0.5354), column:dwd_prd_info.credit_method(0.5486), column:dwd_prd_info.credit_name(0.541), column:dwd_prd_info.update_date(0.5405)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M12 [multi] · 出账借据编号与借据正常本金余额
- must_tables：['DWD_EV_INDV_LOAN_PUB', 'DWD_AR_LOAN_INFO']
- expanded_tables：['dwd_ar_loan_info', 'dwd_app_mapping', 'dwd_ip_corp_cust_info', 'dwd_ip_indv_cust_info', 'dwd_prd_info', 'dwd_sr_claim_detail']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ar_loan_info', 'dwd_sr_claim_detail', 'dwd_app_mapping', 'dwd_prd_info', 'dwd_ip_indv_cust_info', 'dwd_ip_corp_cust_info']
- hits：column:dwd_ar_loan_info.loan_no(0.6858), column:dwd_ar_loan_info.normal_bal(0.7633), column:dwd_ar_loan_info.prin_bal(0.7112), column:dwd_ar_loan_info.ovd_bal(0.6777), column:dwd_ar_loan_info.idnum(0.6739), column:dwd_ar_loan_info.data_date_num(0.6618), column:dwd_ar_loan_info.cont_no(0.6558), column:dwd_ar_loan_info.prd_code(0.6535)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M13 [multi] · 计划应还利息对比明细实还利息
- must_tables：['DWD_EV_REPAY_PLAN', 'DWD_EV_REPAY_DETAIL']
- expanded_tables：['dwd_ev_repay_detail', 'dwd_ar_loan_info']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_repay_detail', 'dwd_ar_loan_info']
- hits：column:dwd_ev_repay_detail.int(0.772), column:dwd_ev_repay_detail.compound_paid(0.6924), column:dwd_ev_repay_detail.term_no(0.6892), column:dwd_ev_repay_detail.fee(0.6827), column:dwd_ev_repay_detail.princ(0.6824), column:dwd_ev_repay_detail.ovd(0.6764), column:dwd_ev_repay_detail.total_terms(0.6753), column:dwd_ev_repay_detail.repay_date(0.6736)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M22 [multi] · 逾期追偿本金与计划应还本金
- must_tables：['DWD_EV_OVERDUE_REPAY', 'DWD_EV_REPAY_PLAN']
- expanded_tables：['dwd_ev_overdue_repay', 'dwd_ar_loan_info']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_overdue_repay', 'dwd_ar_loan_info']
- hits：column:dwd_ev_overdue_repay.prin_amt(0.7912), column:dwd_ev_overdue_repay.oint_amt(0.7509), column:dwd_ev_overdue_repay.odfee_amt(0.7457), column:dwd_ev_overdue_repay.int_amt(0.7402), column:dwd_ev_overdue_repay.update_date(0.7288), column:dwd_ev_overdue_repay.data_date(0.7287), column:dwd_ev_overdue_repay.last_update_time(0.7283), column:dwd_ev_overdue_repay.input_date(0.7273)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M38 [multi] · 出账到期日与借据结束日期
- must_tables：['DWD_EV_INDV_LOAN_PUB', 'DWD_AR_LOAN_INFO']
- expanded_tables：['dwd_ar_loan_info', 'dwd_ev_comp_crd_app', 'dwd_app_mapping', 'dwd_ip_corp_cust_info', 'dwd_ip_indv_cust_info', 'dwd_prd_info', 'dwd_sr_claim_detail']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ar_loan_info', 'dwd_ev_comp_crd_app', 'dwd_prd_info', 'dwd_ip_corp_cust_info', 'dwd_sr_claim_detail', 'dwd_ip_indv_cust_info']
- hits：column:dwd_ar_loan_info.end_date(0.7168), column:dwd_ar_loan_info.payoff_date(0.6493), column:dwd_ar_loan_info.loan_amt(0.3148), column:dwd_ev_comp_crd_app.maturity(0.5468), column:dwd_ar_loan_info.is_debt_tr_date(0.6169), column:dwd_ar_loan_info.data_date(0.6135), column:dwd_ar_loan_info.last_update_date(0.613), column:dwd_ar_loan_info.end_sign(0.6084)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M40 [multi] · 流程客户号与个人客户手机号
- must_tables：['DWD_EV_TRAN_FLOW_INFO', 'DWD_IP_INDV_CUST_INFO']
- expanded_tables：['dwd_ip_indv_cust_info']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ip_indv_cust_info']
- hits：column:dwd_ip_indv_cust_info.phone_no(0.6487), column:dwd_ip_indv_cust_info.spotel(0.5924), column:dwd_ip_indv_cust_info.idnum(0.5936), column:dwd_ip_indv_cust_info.cust_id(0.5899), column:dwd_ip_indv_cust_info.name(0.5561), column:dwd_ip_indv_cust_info.hhdist(0.5423), column:dwd_ip_indv_cust_info.unit_name(0.5399), column:dwd_ip_indv_cust_info.age(0.5346)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M56 [multi] · 三表：出账借据号计划应还总金额
- must_tables：['DWD_EV_INDV_LOAN_PUB', 'DWD_AR_LOAN_INFO', 'DWD_EV_REPAY_PLAN']
- expanded_tables：['dwd_ev_repay_plan', 'dwd_ar_loan_info', 'dwd_app_mapping', 'dwd_ip_corp_cust_info', 'dwd_ip_indv_cust_info', 'dwd_prd_info', 'dwd_sr_claim_detail']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_repay_plan', 'dwd_ar_loan_info']
- hits：column:dwd_ev_repay_plan.rpy_amt(0.718), column:dwd_ev_repay_plan.loan_no(0.7364), column:dwd_ev_repay_plan.loan_amt(0.6973), column:dwd_ar_loan_info.loan_no(0.6427), column:dwd_ev_repay_plan.term_no(0.6913), column:dwd_ev_repay_plan.trans_amt(0.6885), column:dwd_ev_repay_plan.total_terms(0.683), column:dwd_ev_repay_plan.rpy_princ(0.681)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M58 [multi] · 三表：流程申请号客户手机与用信号
- must_tables：['DWD_EV_TRAN_FLOW_INFO', 'DWD_IP_INDV_CUST_INFO', 'DWD_EV_INDV_LOAN_APP']
- expanded_tables：['dwd_ev_indv_loan_app', 'dwd_ip_indv_cust_info', 'dwd_ip_corp_cust_info', 'dwd_app_mapping', 'dwd_ar_loan_info', 'dwd_ev_comp_crd_app', 'dwd_prd_info']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_indv_loan_app', 'dwd_ip_indv_cust_info', 'dwd_ip_corp_cust_info', 'dwd_ev_comp_crd_app', 'dwd_app_mapping']
- hits：column:dwd_ev_indv_loan_app.phone_no(0.6283), column:dwd_ev_indv_loan_app.apprv_loan_amt(0.4096), column:dwd_ip_indv_cust_info.phone_no(0.5606), column:dwd_ip_corp_cust_info.loancard_flag(0.5004), column:dwd_ev_indv_loan_app.app_no(0.6016), column:dwd_ev_indv_loan_app.compa_phone(0.5938), column:dwd_ev_indv_loan_app.contact_phone(0.5907), column:dwd_ev_indv_loan_app.loan_app_no(0.5873)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M60 [multi] · 三表：结清日实还日及借据号
- must_tables：['DWD_AR_LOAN_INFO', 'DWD_EV_REPAY_DETAIL', 'DWD_EV_REPAY_PLAN']
- expanded_tables：['dwd_ar_loan_info', 'dwd_ev_repay_detail', 'dwd_app_mapping', 'dwd_ip_corp_cust_info', 'dwd_ip_indv_cust_info', 'dwd_prd_info', 'dwd_sr_claim_detail']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_repay_detail', 'dwd_ar_loan_info', 'dwd_sr_claim_detail']
- hits：column:dwd_ev_repay_detail.loan_no(0.7187), column:dwd_ev_repay_detail.repay_date(0.708), column:dwd_ar_loan_info.payoff_date(0.7326), column:dwd_ev_repay_detail.settle_date(0.7621), column:dwd_ar_loan_info.data_date(0.6884), column:dwd_ev_repay_detail.update_date(0.6869), column:dwd_ar_loan_info.data_date_num(0.6861), column:dwd_ar_loan_info.last_update_date(0.6855)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

## 表过但字段未齐（Column Hit@10 未过，共 33）

- **N_S25** 借据放款金额与执行年利率 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'year_rate'}]
- **N_M01** 35岁以上个人客户及其用信申请金额 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'age'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'cust_id'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}]
- **N_M02** 个人客户主档姓名手机与授信审批额度 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'name'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'phone_no'}, {'table': 'DWD_EV_INDV_CRD_APP', 'column': 'apprv_cred_amt'}]
- **N_M03** 借据金额及关联还款计划应还本金 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}, {'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_princ'}]
- **N_M06** 逾期追偿本金及借据客户姓名 → 期望字段：[{'table': 'DWD_EV_OVERDUE_REPAY', 'column': 'prin_amt'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'name'}]
- **N_M07** 实还本金与实还日期按借据汇总 → 期望字段：[{'table': 'DWD_EV_REPAY_DETAIL', 'column': 'princ'}, {'table': 'DWD_EV_REPAY_DETAIL', 'column': 'repay_date'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]
- **N_M10** 产品宽限期及下属借据逾期本金 → 期望字段：[{'table': 'DWD_PRD_INFO', 'column': 'grace_day'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_code'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'ovd_bal'}]
- **N_M14** 已婚客户分产品统计用信笔数金额 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'marriage'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M15** 客户年收入单位及用信贷款用途 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'per_mon_income'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'unit_name'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'loan_purpose'}]
- **N_M16** 户籍省份与名下借据逾期本金 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'hhdist'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'cust_id'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'ovd_bal'}]
- **N_M18** 授信审批额度与后续用信申请金额 → 期望字段：[{'table': 'DWD_EV_INDV_CRD_APP', 'column': 'apprv_cred_amt'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_EV_INDV_CRD_APP', 'column': 'cust_id'}]
- **N_M19** 客户性别分布及用信申请量 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'sex'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'cust_id'}]
- **N_M20** 对公客户名称规模及授信产品名 → 期望字段：[{'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'cust_name'}, {'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'ent_scale'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M21** 借据年利率与产品官方宽限期 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'year_rate'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'grace_day'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_code'}]
- **N_M26** 渠道名称及各渠道用信金额 → 期望字段：[{'table': 'DWD_PRD_INFO', 'column': 'channel_name'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'prd_code'}]
- **N_M27** 借据结清日与明细实还日期 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'payoff_date'}, {'table': 'DWD_EV_REPAY_DETAIL', 'column': 'repay_date'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]
- **N_M28** 对公注册资本与关联借据贷款金额 → 期望字段：[{'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'register_amount'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}]
- **N_M30** 计划应还本金与借据正常本金余额 → 期望字段：[{'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_princ'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'normal_bal'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]
- **N_M31** 授信拒绝码与用信申请流水号 → 期望字段：[{'table': 'DWD_EV_INDV_CRD_APP', 'column': 'hit_reasoncode_set'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'loan_app_no'}]
- **N_M41** 对公综合授信金额与企业客户名 → 期望字段：[{'table': 'DWD_EV_COMP_CRD_APP', 'column': 'businesssum'}, {'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'cust_name'}]
- **N_M42** 映射产品编码与产品主数据编码 → 期望字段：[{'table': 'DWD_APP_MAPPING', 'column': 'prd_code'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_code'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M43** 三表：客户年龄授信额度用信金额 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'age'}, {'table': 'DWD_EV_INDV_CRD_APP', 'column': 'apprv_cred_amt'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'cust_id'}]
- **N_M44** 三表：借据产品名与计划应还本金 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}, {'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_princ'}]
- **N_M45** 三表：婚姻状况用信金额产品名 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'marriage'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M47** 三表：授信用信与出账放款金额 → 期望字段：[{'table': 'DWD_EV_INDV_CRD_APP', 'column': 'apprv_cred_amt'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_EV_INDV_LOAN_PUB', 'column': 'business_sum'}]
- **N_M48** 三表：计划明细借据应还实还本金 → 期望字段：[{'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_princ'}, {'table': 'DWD_EV_REPAY_DETAIL', 'column': 'princ'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]
- **N_M49** 三表：对公名规模借据贷款金额 → 期望字段：[{'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'cust_name'}, {'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'ent_scale'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}]
- **N_M50** 三表：学历拒绝码与用信申请金额 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'highest_schooling'}, {'table': 'DWD_EV_INDV_CRD_APP', 'column': 'hit_reasoncode_set'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}]
- **N_M51** 三表：映射用信号借据号及贷款额 → 期望字段：[{'table': 'DWD_APP_MAPPING', 'column': 'loan_app_no'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}]
- **N_M52** 三表：性别用信金额产品渠道 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'sex'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_PRD_INFO', 'column': 'channel_name'}]
- **N_M53** 三表：应还罚息借据号追偿本金 → 期望字段：[{'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_ovd'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}, {'table': 'DWD_EV_OVERDUE_REPAY', 'column': 'prin_amt'}]
- **N_M54** 三表：对公行业借据金额产品名 → 期望字段：[{'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'industry_type'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M55** 三表：证件号借据逾期与客户主档 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'idnum'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'ovd_bal'}, {'table': 'DWD_EV_OVERDUE_REPAY', 'column': 'prin_amt'}]

## 易混淆污染（Forbidden@10，共 4）

- **N_S14** 逾期追偿场景下用户还款日期分布 → forbidden=['dwd_ev_repay_plan']；top=['dwd_ev_overdue_repay', 'dwd_ev_repay_detail', 'dwd_ev_repay_plan']
- **N_S37** 映射表授信编号与产品名称 → forbidden=['dwd_prd_info']；top=['dwd_app_mapping', 'dwd_prd_info', 'dwd_ev_indv_crd_app', 'dwd_ev_comp_crd_app']
- **N_M11** 用信编号映射借据号及贷款金额 → forbidden=['dwd_ev_indv_loan_app']；top=['dwd_ar_loan_info', 'dwd_app_mapping', 'dwd_ev_indv_loan_app', 'dwd_ip_indv_cust_info']
- **N_M20** 对公客户名称规模及授信产品名 → forbidden=['dwd_ip_indv_cust_info']；top=['dwd_ip_corp_cust_info', 'dwd_ip_indv_cust_info', 'dwd_prd_info', 'dwd_ev_comp_crd_app']

## 下一步（今天下午）

1. 给失败题打标签（手册 §3.3）
2. 优先改 3～5 张核心表的 L1 description，增量同步向量
3. 再跑一次本脚本对比 Table@10

## SQL 准确率（含方案 A）

| 分区 | 题数 | Valid | StructAcc | PredExecOK | GoldExecOK | **ResultAcc** |
|------|------|-------|-----------|------------|------------|---------------|
| ALL | 100 | 100.0% | 74.0% | 93.2% | 97.8% | **1.1%** |
| single | 40 | 100.0% | 92.5% | 95.0% | 100.0% | **2.5%** |
| multi | 60 | 100.0% | 61.7% | 91.7% | 96.0% | **0.0%** |

> **ResultAcc（方案 A 主指标）**：`gold_sql` 与预测 SQL 均在只读沙箱执行成功，且结果集按值多重集相等（忽略列名顺序）。
> 无 `gold_sql` 或 gold 执行失败的题：`result_acc` 为 null，不计入 ResultAcc 分母。

### ResultAcc 失败样例（最多 25）

- `N_S01` 统计个人客户平均年龄  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.age AS `age`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_crd_app.cust_id AS `cust_id`, dwd_ar_loan_info.idnum AS `idnum` FROM dwd_ip_indv_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.cust_id = dwd_ip_indv_cust_inf
  ```
  gold:
  ```sql
  SELECT AVG(DWD_IP_INDV_CUST_INFO.age) AS `age_avg` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S02` 对公客户家数统计  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_corp_cust_info.cust_id AS `cust_id`, dwd_ev_comp_crd_app.customerid AS `customerid`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_loan_pub.customer_id AS `customer_id`, dwd_ip_corp_cust_info.cust_name AS `cust_name`, dwd_ar_loan_info.loan_no AS `loan_no` FROM dwd_ip_corp_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.
  ```
  gold:
  ```sql
  SELECT COUNT(DWD_IP_CORP_CUST_INFO.cust_id) AS `cust_id_count` FROM DWD_IP_CORP_CUST_INFO LIMIT 100
  ```
- `N_S03` 本科以上学历个人客户人数  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.highest_schooling AS `highest_schooling`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_crd_app.cust_id AS `cust_id`, dwd_ar_loan_info.idnum AS `idnum` FROM dwd_ip_indv_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.cu
  ```
  gold:
  ```sql
  SELECT COUNT(DWD_IP_INDV_CUST_INFO.highest_schooling) AS `highest_schooling_count` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S04` 全部用信申请金额求和  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_indv_loan_app.app_loan_amt AS `app_loan_amt`, dwd_ev_indv_loan_app.crd_app_no AS `crd_app_no`, dwd_ev_indv_crd_app.app_no AS `app_no`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ev_indv_loan_app.prd_code AS `prd_code` FROM dwd_ev_indv_loan_app INNER JOIN dwd_ev_indv_crd_app ON dwd_ev_ind
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_LOAN_APP.app_loan_amt AS `app_loan_amt` FROM DWD_EV_INDV_LOAN_APP LIMIT 100
  ```
- `N_S05` 授信审批额度总计  pred_rows=0 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_indv_crd_app.apprv_cred_amt AS `apprv_cred_amt`, dwd_ev_indv_loan_app.crd_app_no AS `crd_app_no`, dwd_ev_indv_crd_app.app_no AS `app_no`, dwd_ev_indv_crd_app.prd_code AS `prd_code`, dwd_prd_info.prd_code AS `prd_code`, dwd_ev_indv_crd_app.cust_id AS `cust_id` FROM dwd_ev_indv_crd_app INNER JOIN dwd_app_mapping ON dwd_app_mapping.crd_a
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_CRD_APP.apprv_cred_amt AS `apprv_cred_amt` FROM DWD_EV_INDV_CRD_APP LIMIT 100
  ```
- `N_S06` 借据本金余额汇总  pred_rows=0 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.prin_bal AS `prin_bal`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_repay_plan.loan_no AS `loan_no`, dwd_app_mapping.loan_no AS `loan_no`, dwd_ev_indv_loan_pub.duebill_no AS `duebill_no`, dwd_ar_loan_info.cust_id AS `cust_id` FROM dwd_ar_loan_info INNER JOIN dwd_app_mapping ON dwd_ar_loan_info.loan_no = dwd_app_mapping.loa
  ```
  gold:
  ```sql
  SELECT SUM(DWD_AR_LOAN_INFO.prin_bal) AS `prin_bal_sum` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S07` 存在逾期的借据有多少  pred_rows=1 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT COUNT(dwd_ar_loan_info.ovd_bal) AS `cnt` FROM dwd_ar_loan_info INNER JOIN dwd_app_mapping ON dwd_ar_loan_info.loan_no = dwd_app_mapping.loan_no INNER JOIN dwd_ev_overdue_repay ON dwd_ar_loan_info.loan_no = dwd_ev_overdue_repay.loan_no INNER JOIN dwd_ip_corp_cust_info ON dwd_ar_loan_info.cust_id = dwd_ip_corp_cust_info.cust_id INNER JOIN dwd_
  ```
  gold:
  ```sql
  SELECT COUNT(DWD_AR_LOAN_INFO.ovd_bal) AS `ovd_bal_count` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S08` 逾期处置追偿本金总额  pred_rows=0 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_overdue_repay.prin_amt AS `prin_amt`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_overdue_repay.loan_no AS `loan_no`, dwd_ev_overdue_repay.alias_rpy_type AS `alias_rpy_type`, dwd_ar_loan_info.ovd_bal AS `ovd_bal`, dwd_ev_repay_plan.loan_no AS `loan_no` FROM dwd_ev_overdue_repay INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.loan_no
  ```
  gold:
  ```sql
  SELECT SUM(DWD_EV_OVERDUE_REPAY.prin_amt) AS `prin_amt_sum` FROM DWD_EV_OVERDUE_REPAY LIMIT 100
  ```
- `N_S09` 各信贷产品宽限期天数  pred_rows=100 gold_rows=10  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_prd_info.grace_day AS `grace_day`, dwd_prd_info.prd_name AS `prd_name`, dwd_ev_indv_crd_app.prd_code AS `prd_code`, dwd_prd_info.prd_code AS `prd_code`, dwd_ev_indv_loan_pub.product_id AS `product_id`, dwd_ar_loan_info.prd_code AS `prd_code` FROM dwd_prd_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.prd_code = dwd_prd_info.prd_cod
  ```
  gold:
  ```sql
  SELECT DWD_PRD_INFO.grace_day AS `grace_day` FROM DWD_PRD_INFO LIMIT 100
  ```
- `N_S10` 实还罚息金额合计  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_repay_detail.ovd AS `ovd`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_repay_detail.loan_no AS `loan_no`, dwd_ev_repay_detail.update_date AS `update_date`, dwd_ev_repay_detail.compound_paid AS `compound_paid`, dwd_ev_repay_plan.loan_no AS `loan_no` FROM dwd_ev_repay_detail INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.loan_no = dw
  ```
  gold:
  ```sql
  SELECT SUM(DWD_EV_REPAY_DETAIL.ovd) AS `ovd_sum` FROM DWD_EV_REPAY_DETAIL LIMIT 100
  ```
- `N_S11` 个人客户按性别分组统计  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.sex AS `sex`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_crd_app.cust_id AS `cust_id`, dwd_ar_loan_info.idnum AS `idnum` FROM dwd_ip_indv_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.cust_id = dwd_ip_indv_cust_inf
  ```
  gold:
  ```sql
  SELECT COUNT(DWD_IP_INDV_CUST_INFO.sex) AS `sex_count` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S12` 借据层面宽限天数分布  pred_rows=0 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.grace_day AS `grace_day`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_repay_plan.loan_no AS `loan_no`, dwd_app_mapping.loan_no AS `loan_no`, dwd_ev_indv_loan_pub.duebill_no AS `duebill_no`, dwd_ar_loan_info.cust_id AS `cust_id` FROM dwd_ar_loan_info INNER JOIN dwd_app_mapping ON dwd_ar_loan_info.loan_no = dwd_app_mapping.l
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.grace_day AS `grace_day` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S13` 还款计划应还利息总额  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_repay_plan.rpy_int AS `rpy_int`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_repay_plan.loan_no AS `loan_no`, dwd_ev_repay_plan.rpy_amt AS `rpy_amt`, dwd_ev_repay_plan.term_no AS `term_no`, dwd_ev_repay_plan.rpy_ovd AS `rpy_ovd` FROM dwd_ev_repay_plan INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.loan_no = dwd_ev_repay_plan.loan_n
  ```
  gold:
  ```sql
  SELECT SUM(DWD_EV_REPAY_PLAN.rpy_int) AS `rpy_int_sum` FROM DWD_EV_REPAY_PLAN LIMIT 100
  ```
- `N_S14` 逾期追偿场景下用户还款日期分布  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_overdue_repay.practical_pay_date AS `practical_pay_date`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_overdue_repay.loan_no AS `loan_no`, dwd_ev_overdue_repay.int_amt AS `int_amt`, dwd_ev_overdue_repay.oint_amt AS `oint_amt`, dwd_ev_overdue_repay.odfee_amt AS `odfee_amt` FROM dwd_ev_overdue_repay INNER JOIN dwd_ar_loan_info ON dwd_a
  ```
  gold:
  ```sql
  SELECT DWD_EV_OVERDUE_REPAY.int_repay_date AS `int_repay_date` FROM DWD_EV_OVERDUE_REPAY LIMIT 100
  ```
- `N_S15` 对公客户营业执照失效日期  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_corp_cust_info.license_maturity AS `license_maturity`, dwd_ev_comp_crd_app.customerid AS `customerid`, dwd_ip_corp_cust_info.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_loan_pub.customer_id AS `customer_id`, dwd_ip_corp_cust_info.cust_name AS `cust_name` FROM dwd_ip_corp_cust_info INNER JOIN dwd_ar_loan_in
  ```
  gold:
  ```sql
  SELECT DWD_IP_CORP_CUST_INFO.license_maturity AS `license_maturity` FROM DWD_IP_CORP_CUST_INFO LIMIT 100
  ```
- `N_S16` 查询个人客户身份证件号码  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.idnum AS `idnum`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_crd_app.cust_id AS `cust_id`, dwd_ar_loan_info.idnum AS `idnum` FROM dwd_ip_indv_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.cust_id = dwd_ip_indv_cust
  ```
  gold:
  ```sql
  SELECT DWD_IP_INDV_CUST_INFO.idnum AS `idnum` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S17` 个人客户月收入汇总分析  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.per_mon_income AS `per_mon_income`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_crd_app.cust_id AS `cust_id`, dwd_ar_loan_info.idnum AS `idnum` FROM dwd_ip_indv_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.cust_id 
  ```
  gold:
  ```sql
  SELECT SUM(DWD_IP_INDV_CUST_INFO.per_mon_income) AS `per_mon_income_sum` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S18` 代偿记录中的代偿本金余额  pred_rows=0 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_sr_claim_detail.dc_bal AS `dc_bal`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_sr_claim_detail.loan_no AS `loan_no`, dwd_ar_loan_info.prin_bal AS `prin_bal`, dwd_sr_claim_detail.prd_code AS `prd_code`, dwd_sr_claim_detail.platform_code AS `platform_code` FROM dwd_sr_claim_detail INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.loan_no = d
  ```
  gold:
  ```sql
  SELECT DWD_SR_CLAIM_DETAIL.dc_bal AS `dc_bal` FROM DWD_SR_CLAIM_DETAIL LIMIT 100
  ```
- `N_S19` 对公综合授信业务金额汇总  pred_rows=100 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_prd_info.prd_name AS `prd_name`, dwd_prd_info.prd_amt AS `prd_amt`, dwd_prd_info.credit_type AS `credit_type`, dwd_ev_indv_crd_app.prd_code AS `prd_code`, dwd_prd_info.prd_code AS `prd_code`, dwd_ev_indv_loan_pub.product_id AS `product_id` FROM dwd_prd_info INNER JOIN dwd_ev_indv_crd_app ON dwd_ev_indv_crd_app.prd_code = dwd_prd_info.prd
  ```
  gold:
  ```sql
  SELECT SUM(DWD_EV_COMP_CRD_APP.businesssum) AS `businesssum_sum` FROM DWD_EV_COMP_CRD_APP LIMIT 100
  ```
- `N_S21` 个人客户婚姻与性别交叉统计  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.marriage AS `marriage`, dwd_ip_indv_cust_info.sex AS `sex`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_crd_app.cust_id AS `cust_id` FROM dwd_ip_indv_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.cust_id = dwd_ip_in
  ```
  gold:
  ```sql
  SELECT DWD_IP_INDV_CUST_INFO.marriage AS `marriage`, DWD_IP_INDV_CUST_INFO.sex AS `sex` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S22` 授信拒绝码与审批状态明细  pred_rows=0 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_indv_crd_app.apprv_state AS `apprv_state`, dwd_ev_indv_crd_app.hit_reasoncode_set AS `hit_reasoncode_set`, dwd_ev_indv_loan_app.crd_app_no AS `crd_app_no`, dwd_ev_indv_crd_app.app_no AS `app_no`, dwd_ev_indv_crd_app.prd_code AS `prd_code`, dwd_prd_info.prd_code AS `prd_code` FROM dwd_ev_indv_crd_app INNER JOIN dwd_app_mapping ON dwd_a
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_CRD_APP.hit_reasoncode_set AS `hit_reasoncode_set`, DWD_EV_INDV_CRD_APP.apprv_state AS `apprv_state` FROM DWD_EV_INDV_CRD_APP LIMIT 100
  ```
- `N_S23` 还款计划每期应还本金与应还利息  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_repay_plan.rpy_int AS `rpy_int`, dwd_ev_repay_plan.rpy_princ AS `rpy_princ`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_repay_plan.loan_no AS `loan_no`, dwd_ev_repay_plan.term_no AS `term_no`, dwd_ev_repay_plan.rpy_amt AS `rpy_amt` FROM dwd_ev_repay_plan INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.loan_no = dwd_ev_repay_plan.lo
  ```
  gold:
  ```sql
  SELECT DWD_EV_REPAY_PLAN.rpy_princ AS `rpy_princ`, DWD_EV_REPAY_PLAN.rpy_int AS `rpy_int` FROM DWD_EV_REPAY_PLAN LIMIT 100
  ```
- `N_S24` 产品目录中的名称与编码清单  pred_rows=100 gold_rows=10  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_prd_info.prd_name AS `prd_name`, dwd_prd_info.prd_code AS `prd_code`, dwd_ev_indv_crd_app.prd_code AS `prd_code`, dwd_ev_indv_loan_pub.product_id AS `product_id`, dwd_ar_loan_info.prd_code AS `prd_code`, dwd_ev_indv_loan_app.prd_code AS `prd_code` FROM dwd_prd_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.prd_code = dwd_prd_info.p
  ```
  gold:
  ```sql
  SELECT DWD_PRD_INFO.prd_name AS `prd_name`, DWD_PRD_INFO.prd_code AS `prd_code` FROM DWD_PRD_INFO LIMIT 100
  ```
- `N_S25` 借据放款金额与执行年利率  pred_rows=0 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_indv_loan_pub.business_sum AS `business_sum`, dwd_ar_loan_info.year_rate AS `year_rate`, dwd_ar_loan_info.loan_no AS `loan_no`, dwd_ev_repay_plan.loan_no AS `loan_no`, dwd_app_mapping.loan_no AS `loan_no`, dwd_ev_indv_loan_pub.duebill_no AS `duebill_no` FROM dwd_ev_indv_loan_pub INNER JOIN dwd_app_mapping ON dwd_app_mapping.loan_app_n
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.loan_amt AS `loan_amt`, DWD_AR_LOAN_INFO.year_rate AS `year_rate` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S26` 个人客户联系电话与户籍省份  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.hhdist AS `hhdist`, dwd_ip_indv_cust_info.phone_no AS `phone_no`, dwd_ev_indv_loan_app.cust_id AS `cust_id`, dwd_ip_indv_cust_info.cust_id AS `cust_id`, dwd_ar_loan_info.cust_id AS `cust_id`, dwd_ev_indv_crd_app.cust_id AS `cust_id` FROM dwd_ip_indv_cust_info INNER JOIN dwd_ar_loan_info ON dwd_ar_loan_info.cust_id = dwd
  ```
  gold:
  ```sql
  SELECT DWD_IP_INDV_CUST_INFO.phone_no AS `phone_no`, DWD_IP_INDV_CUST_INFO.hhdist AS `hhdist` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```

