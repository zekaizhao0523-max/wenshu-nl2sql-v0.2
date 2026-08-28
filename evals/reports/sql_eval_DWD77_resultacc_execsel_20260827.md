# 召回评测报告

- 时间：None
- 黄金集：`evals/golden/recall_v2_dwd.jsonl`
- collection：`None` / points=None
- 库范围：current_raw None
- 检索模式：**legacy**
- 检索 limit：30；报告 K：[5, 10, 15, 30]
- 题量：单表 40 / 多表 37 / 合计正例 77
- Table MRR（全部）：0.9307

## 单表 vs 多表（核心对比 @10）

| 分区 | 题数 | Table Hit@10 | Any-Table@10 | Column Hit@10 | Forbidden@10 | MRR |
|------|------|--------------|--------------|---------------|--------------|-----|
| 单表 | 40 | 97.5% | 97.5% | 95.0% | 5.0% | 0.9042 |
| 多表 | 37 | 91.9% | 100.0% | 43.2% | 5.4% | 0.9595 |
| 合计 | 77 | 94.8% | 98.7% | 70.1% | 5.2% | 0.9307 |

## 单表 · 单字段 vs 多字段（@10）

| 分区 | 题数 | Table Hit@10 | Column Hit@10 | Forbidden@10 |
|------|------|--------------|---------------|--------------|
| 单表单字段 | 20 | 95.0% | 95.0% | 5.0% |
| 单表多字段 | 20 | 100.0% | 95.0% | 5.0% |

## 全部正例 · 主指标

| K | Table Hit | Any-Table Hit | Column Hit | Forbidden@K |
|---|-----------|---------------|------------|-------------|
| @5 | 94.8% | 98.7% | 61.0% | 5.2% |
| @10 | 94.8% | 98.7% | 70.1% | 5.2% |
| @15 | 94.8% | 98.7% | 74.0% | 5.2% |
| @30 | 94.8% | 98.7% | 87.0% | 5.2% |

## 按题型切片（主看 Table Hit@10）

| 题型 | 题数 | Table Hit@10 | Forbidden@10 |
|------|------|--------------|--------------|
| 全部正例 | 77 | 94.8% | 5.2% |
| JOIN | 37 | 91.9% | 5.4% |
| 单表 | 40 | 97.5% | 5.0% |
| 单表单字段 | 20 | 95.0% | 5.0% |
| 单表多字段 | 20 | 100.0% | 5.0% |
| 多表 | 37 | 91.9% | 5.4% |

## S1 / S2 精选（全集 Hit，非 Top-K 槽位）

S1/S2 是 LLM 从检索池 S_rtrv 中选出的精选集合，列数不固定；**不要**与历史 Hit@10 做同字段数对比。第 2 轮强制补全，空结果会启发式补列。

| 分区 | 题数 | S1 Table | S1 Column | S1 均列数 | S2 Table | S2 Column | S2 均列数 |
|------|------|----------|-----------|-----------|----------|-----------|-----------|
| 单表单字段 | 20 | 95.0% | 90.0% | 7.2 | 100.0% | 95.0% | 28.1 |
| 单表多字段 | 20 | 100.0% | 95.0% | 9.5 | 100.0% | 100.0% | 31.4 |
| 单表 | 40 | 97.5% | 92.5% | 8.4 | 100.0% | 97.5% | 29.8 |
| 多表 | 37 | 91.9% | 67.6% | 13.8 | 100.0% | 86.5% | 32.2 |
| 合计 | 77 | 94.8% | 80.5% | 11.0 | 100.0% | 92.2% | 30.9 |

- S1 来源：llm=1, llm_cover=76
- S2 来源：llm=1, llm_cover=51, llm_empty_widen_cover=25

## MVP 判定

- **达标**：Table Hit@10 = 94.8% ≥ 80%

## 失败题（Table Hit@10 未过：单表 1 / 多表 3 / 共 4）

### N_S19 [single] · 对公综合授信业务金额汇总
- must_tables：['DWD_EV_COMP_CRD_APP']
- expanded_tables：['dwd_prd_info']
- first_hit_rank（expanded）：None
- top_tables（列槽@10）：['dwd_prd_info']
- hits：column:dwd_prd_info.prd_name(0.5438), column:dwd_prd_info.prd_type(0.5426), column:dwd_prd_info.prd_amt(0.5419), column:dwd_prd_info.credit_type(0.5843), column:dwd_prd_info.start_date(0.5354), column:dwd_prd_info.credit_method(0.5486), column:dwd_prd_info.credit_name(0.5409), column:dwd_prd_info.update_date(0.5405)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M12 [multi] · 出账借据编号与借据正常本金余额
- must_tables：['DWD_EV_INDV_LOAN_PUB', 'DWD_AR_LOAN_INFO']
- expanded_tables：['dwd_ar_loan_info', 'dwd_app_mapping', 'dwd_ip_corp_cust_info', 'dwd_ip_indv_cust_info', 'dwd_prd_info', 'dwd_sr_claim_detail']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ar_loan_info', 'dwd_sr_claim_detail', 'dwd_app_mapping', 'dwd_prd_info', 'dwd_ip_indv_cust_info', 'dwd_ip_corp_cust_info']
- hits：column:dwd_ar_loan_info.loan_no(0.6858), column:dwd_ar_loan_info.normal_bal(0.7633), column:dwd_ar_loan_info.prin_bal(0.7113), column:dwd_ar_loan_info.ovd_bal(0.6777), column:dwd_ar_loan_info.idnum(0.6738), column:dwd_ar_loan_info.data_date_num(0.6617), column:dwd_ar_loan_info.cont_no(0.6558), column:dwd_ar_loan_info.prd_code(0.6535)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M13 [multi] · 计划应还利息对比明细实还利息
- must_tables：['DWD_EV_REPAY_PLAN', 'DWD_EV_REPAY_DETAIL']
- expanded_tables：['dwd_ev_repay_detail', 'dwd_ar_loan_info']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_repay_detail', 'dwd_ar_loan_info']
- hits：column:dwd_ev_repay_detail.int(0.772), column:dwd_ev_repay_detail.compound_paid(0.6923), column:dwd_ev_repay_detail.term_no(0.6891), column:dwd_ev_repay_detail.fee(0.6826), column:dwd_ev_repay_detail.princ(0.6824), column:dwd_ev_repay_detail.ovd(0.6764), column:dwd_ev_repay_detail.total_terms(0.6752), column:dwd_ev_repay_detail.repay_date(0.6736)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M22 [multi] · 逾期追偿本金与计划应还本金
- must_tables：['DWD_EV_OVERDUE_REPAY', 'DWD_EV_REPAY_PLAN']
- expanded_tables：['dwd_ev_overdue_repay', 'dwd_ar_loan_info']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_overdue_repay', 'dwd_ar_loan_info']
- hits：column:dwd_ev_overdue_repay.prin_amt(0.7912), column:dwd_ev_overdue_repay.oint_amt(0.7508), column:dwd_ev_overdue_repay.odfee_amt(0.7457), column:dwd_ev_overdue_repay.int_amt(0.7401), column:dwd_ev_overdue_repay.update_date(0.7288), column:dwd_ev_overdue_repay.data_date(0.7287), column:dwd_ev_overdue_repay.last_update_time(0.7283), column:dwd_ev_overdue_repay.input_date(0.7272)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

## 表过但字段未齐（Column Hit@10 未过，共 19）

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

> 以下 Valid / StructAcc / PredExecOK / ResultAcc 均基于 **Agent 对齐重试后的最终 SQL**；中间被丢弃的 plan/SQL 不计入。

| 分区 | 题数 | Valid | StructAcc | PredExecOK | GoldExecOK | **ResultAcc** |
|------|------|-------|-----------|------------|------------|---------------|
| ALL | 77 | 81.8% | 61.0% | 91.0% | 100.0% | **46.3%** |
| single | 40 | 87.5% | 77.5% | 89.2% | 100.0% | **56.8%** |
| multi | 37 | 75.7% | 43.2% | 93.3% | 100.0% | **33.3%** |

### 过程指标（不计入主准确率）

- 首轮 plan 通过率：26.9%；平均 plan 尝试 1.56 次；平均 SQL 尝试 0.88 次
- 最终无 SQL：plan 耗尽 3 题，SQL 耗尽/生成失败 1 题

> **ResultAcc（方案 A 主指标）**：`gold_sql` 与预测 SQL 均在只读沙箱执行成功，且结果集按值多重集相等（忽略列名顺序）。
> 无 `gold_sql` 或 gold 执行失败的题：`result_acc` 为 null，不计入 ResultAcc 分母。

### ResultAcc 失败样例（最多 25）

- `N_S08` 逾期处置追偿本金总额  pred_rows=1 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT SUM(prin_amt) AS `逾期处置追偿本金总额` FROM dwd_ev_overdue_repay WHERE alias_rpy_type = '追偿' LIMIT 100
  ```
  gold:
  ```sql
  SELECT SUM(DWD_EV_OVERDUE_REPAY.prin_amt) AS `prin_amt_sum` FROM DWD_EV_OVERDUE_REPAY LIMIT 100
  ```
- `N_S12` 借据层面宽限天数分布  pred_rows=None gold_rows=5  err=EXPLAIN 失败: (1140, "In aggregated query without GROUP BY, expression #2 of SELECT list contains nonaggregated column 'vectortest.dwd_ar_loan_info.grace_day'; this is incompatible with sql_mode=only_full_group_by")
  pred:
  ```sql
  SELECT COUNT(`grace_day`) AS `count`, `grace_day` AS `grace_day` FROM `dwd_ar_loan_info` ORDER BY `grace_day` ASC LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.grace_day AS `grace_day`, COUNT(DWD_AR_LOAN_INFO.grace_day) AS `cnt` FROM DWD_AR_LOAN_INFO GROUP BY DWD_AR_LOAN_INFO.grace_day LIMIT 100
  ```
- `N_S14` 逾期追偿场景下用户还款日期分布  pred_rows=None gold_rows=1  err=EXPLAIN 失败: (1140, "In aggregated query without GROUP BY, expression #2 of SELECT list contains nonaggregated column 'vectortest.dwd_ev_overdue_repay.practical_pay_date'; this is incompatible with sql_mode=only_full_group_by")
  pred:
  ```sql
  SELECT COUNT(`practical_pay_date`) AS `count`, `practical_pay_date` AS `practical_pay_date` FROM `dwd_ev_overdue_repay` ORDER BY `practical_pay_date` ASC LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_OVERDUE_REPAY.int_repay_date AS `int_repay_date`, COUNT(DWD_EV_OVERDUE_REPAY.int_repay_date) AS `cnt` FROM DWD_EV_OVERDUE_REPAY GROUP BY DWD_EV_OVERDUE_REPAY.int_repay_date LIMIT 100
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
- `N_S19` 对公综合授信业务金额汇总  pred_rows=1 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT SUM(prd_amt) AS `total_credit_amount` FROM dwd_prd_info WHERE credit_type = '授信' LIMIT 100
  ```
  gold:
  ```sql
  SELECT SUM(DWD_EV_COMP_CRD_APP.businesssum) AS `businesssum_sum` FROM DWD_EV_COMP_CRD_APP LIMIT 100
  ```
- `N_S20` 信贷审批流程当前节点分布  pred_rows=None gold_rows=None  err=empty pred sql
  pred:
  ```sql
  SELECT COUNT(*) AS `count`, `app_node` FROM `dwd_ev_tran_flow_info` GROUP BY `app_node` ORDER BY `app_node` ASC LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_TRAN_FLOW_INFO.app_node AS `app_node`, COUNT(DWD_EV_TRAN_FLOW_INFO.app_node) AS `cnt` FROM DWD_EV_TRAN_FLOW_INFO GROUP BY DWD_EV_TRAN_FLOW_INFO.app_node LIMIT 100
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
- `N_S25` 借据放款金额与执行年利率  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.loan_amt, dwd_ar_loan_info.year_rate FROM dwd_ar_loan_info INNER JOIN dwd_ev_indv_loan_pub ON dwd_ar_loan_info.loan_no = dwd_ev_indv_loan_pub.duebill_no LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.loan_amt AS `loan_amt`, DWD_AR_LOAN_INFO.year_rate AS `year_rate` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S27` 对公客户注册资本与企业规模  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_corp_cust_info.ent_scale, dwd_ip_corp_cust_info.register_amount, dwd_ip_corp_cust_info.register_currency FROM dwd_ip_corp_cust_info LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_IP_CORP_CUST_INFO.register_amount AS `register_amount`, DWD_IP_CORP_CUST_INFO.ent_scale AS `ent_scale` FROM DWD_IP_CORP_CUST_INFO LIMIT 100
  ```
- `N_S28` 职工人数超过100的企业名称  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT COUNT(dwd_ip_corp_cust_info.employee_number) AS `employee_number_count`, dwd_ip_corp_cust_info.cust_name AS `cust_name` FROM dwd_ip_corp_cust_info WHERE dwd_ip_corp_cust_info.employee_number > 100 GROUP BY dwd_ip_corp_cust_info.cust_name LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_IP_CORP_CUST_INFO.employee_number AS `employee_number`, DWD_IP_CORP_CUST_INFO.cust_name AS `cust_name` FROM DWD_IP_CORP_CUST_INFO WHERE DWD_IP_CORP_CUST_INFO.employee_number > 100 LIMIT 100
  ```
- `N_S30` 已结清借据的结清日与借款人姓名  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT `dwd_ar_loan_info`.`payoff_date`, `dwd_ip_indv_cust_info`.`name` FROM `dwd_ar_loan_info` INNER JOIN `dwd_ip_indv_cust_info` ON `dwd_ar_loan_info`.`cust_id` = `dwd_ip_indv_cust_info`.`cust_id` WHERE `payoff_date` IS NOT NULL LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.payoff_date AS `payoff_date`, DWD_AR_LOAN_INFO.name AS `name` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S31` 首贷标识与本次出账放款金额  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_indv_loan_pub.is_first_loan, dwd_ar_loan_info.loan_amt FROM dwd_ev_indv_loan_pub INNER JOIN dwd_ar_loan_info ON dwd_ev_indv_loan_pub.duebill_no = dwd_ar_loan_info.loan_no LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_LOAN_PUB.is_first_loan AS `is_first_loan`, DWD_EV_INDV_LOAN_PUB.business_sum AS `business_sum` FROM DWD_EV_INDV_LOAN_PUB LIMIT 100
  ```
- `N_S33` 授信申请日期与审批结果状态  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_indv_crd_app.apply_date AS `apply_date`, dwd_ev_indv_crd_app.apprv_state AS `apprv_state`, dwd_ev_indv_loan_app.crd_app_no AS `crd_app_no`, dwd_ev_indv_crd_app.app_no AS `app_no`, dwd_ev_indv_crd_app.prd_code AS `prd_code`, dwd_prd_info.prd_code AS `prd_code` FROM dwd_ev_indv_crd_app INNER JOIN dwd_prd_info ON dwd_ev_indv_crd_app.prd_
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_CRD_APP.apply_date AS `apply_date`, DWD_EV_INDV_CRD_APP.apprv_state AS `apprv_state` FROM DWD_EV_INDV_CRD_APP LIMIT 100
  ```
- `N_S34` 实还本金按借据号汇总  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT SUM(DWD_EV_REPAY_DETAIL.princ) AS `princ_sum`, DWD_EV_REPAY_DETAIL.loan_no AS `loan_no` FROM DWD_EV_REPAY_DETAIL GROUP BY DWD_EV_REPAY_DETAIL.loan_no LIMIT 100
  ```
- `N_S38` 借据合同编号与借据号清单  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.loan_no, dwd_ar_loan_info.cont_no FROM dwd_ar_loan_info INNER JOIN dwd_ev_indv_loan_pub ON dwd_ar_loan_info.loan_no = dwd_ev_indv_loan_pub.duebill_no LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.cont_no AS `cont_no`, DWD_AR_LOAN_INFO.loan_no AS `loan_no` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_M01` 35岁以上个人客户及其用信申请金额  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ip_indv_cust_info.cust_id, dwd_ev_indv_loan_app.app_loan_amt FROM dwd_ev_indv_loan_app INNER JOIN dwd_ip_indv_cust_info ON dwd_ev_indv_loan_app.cust_id = dwd_ip_indv_cust_info.cust_id WHERE dwd_ip_indv_cust_info.age > 35 LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_IP_INDV_CUST_INFO.age AS `age`, DWD_IP_INDV_CUST_INFO.cust_id AS `cust_id`, DWD_EV_INDV_LOAN_APP.app_loan_amt AS `app_loan_amt` FROM DWD_IP_INDV_CUST_INFO INNER JOIN DWD_EV_INDV_LOAN_APP ON DWD_EV_INDV_LOAN_APP.cust_id = DWD_IP_INDV_CUST_INFO.cust_id WHERE DWD_IP_INDV_CUST_INFO.age >= 35 LIMIT 100
  ```
- `N_M03` 借据金额及关联还款计划应还本金  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.loan_amt, dwd_ev_repay_plan.rpy_princ FROM dwd_ar_loan_info INNER JOIN dwd_ev_repay_plan ON dwd_ar_loan_info.loan_no = dwd_ev_repay_plan.loan_no LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.loan_no AS `loan_no`, DWD_AR_LOAN_INFO.loan_amt AS `loan_amt`, DWD_EV_REPAY_PLAN.rpy_princ AS `rpy_princ` FROM DWD_AR_LOAN_INFO INNER JOIN DWD_EV_REPAY_PLAN ON DWD_AR_LOAN_INFO.loan_no = DWD_EV_REPAY_PLAN.loan_no LIMIT 100
  ```
- `N_M04` 借据对应产品名称与本金余额  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_prd_info.prd_name, dwd_ar_loan_info.prin_bal FROM dwd_ar_loan_info INNER JOIN dwd_prd_info ON dwd_ar_loan_info.prd_code = dwd_prd_info.prd_code LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_PRD_INFO.prd_code AS `prd_code`, DWD_AR_LOAN_INFO.prin_bal AS `prin_bal`, DWD_PRD_INFO.prd_name AS `prd_name` FROM DWD_AR_LOAN_INFO INNER JOIN DWD_PRD_INFO ON DWD_AR_LOAN_INFO.prd_code = DWD_PRD_INFO.prd_code LIMIT 100
  ```
- `N_M05` 用信申请关联产品名称与申请额度  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_prd_info.prd_name, dwd_ev_indv_loan_app.app_loan_amt FROM dwd_ev_indv_loan_app INNER JOIN dwd_prd_info ON dwd_ev_indv_loan_app.prd_code = dwd_prd_info.prd_code LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_LOAN_APP.prd_code AS `prd_code`, DWD_EV_INDV_LOAN_APP.app_loan_amt AS `app_loan_amt`, DWD_PRD_INFO.prd_name AS `prd_name` FROM DWD_EV_INDV_LOAN_APP INNER JOIN DWD_PRD_INFO ON DWD_EV_INDV_LOAN_APP.prd_code = DWD_PRD_INFO.prd_code LIMIT 100
  ```
- `N_M06` 逾期追偿本金及借据客户姓名  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.name, dwd_ev_overdue_repay.prin_amt FROM dwd_ar_loan_info INNER JOIN dwd_ev_overdue_repay ON dwd_ar_loan_info.loan_no = dwd_ev_overdue_repay.loan_no LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_OVERDUE_REPAY.prin_amt AS `prin_amt`, DWD_AR_LOAN_INFO.loan_no AS `loan_no`, DWD_AR_LOAN_INFO.name AS `name` FROM DWD_EV_OVERDUE_REPAY INNER JOIN DWD_AR_LOAN_INFO ON DWD_AR_LOAN_INFO.loan_no = DWD_EV_OVERDUE_REPAY.loan_no LIMIT 100
  ```
- `N_M07` 实还本金与实还日期按借据汇总  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT SUM(dwd_ev_repay_detail.princ) AS `实还本金`, dwd_ev_repay_detail.loan_no AS `借据`, dwd_ev_repay_detail.repay_date AS `实还日期` FROM dwd_ev_repay_detail GROUP BY dwd_ev_repay_detail.loan_no, dwd_ev_repay_detail.repay_date LIMIT 100
  ```
  gold:
  ```sql
  SELECT SUM(DWD_EV_REPAY_DETAIL.princ) AS `princ_sum`, DWD_EV_REPAY_DETAIL.repay_date AS `repay_date`, DWD_AR_LOAN_INFO.loan_no AS `loan_no` FROM DWD_EV_REPAY_DETAIL INNER JOIN DWD_AR_LOAN_INFO ON DWD_AR_LOAN_INFO.loan_no = DWD_EV_REPAY_DETAIL.loan_no GROUP BY DWD_EV_REPAY_DETAIL.repay_date, DWD_AR_LOAN_INFO.loan_no LIMIT 100
  ```
- `N_M08` 出账放款金额及对应产品名称  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ev_indv_loan_pub.rmb_amount, dwd_prd_info.prd_name FROM dwd_ev_indv_loan_pub INNER JOIN dwd_prd_info ON dwd_ev_indv_loan_pub.product_id = dwd_prd_info.prd_code LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_LOAN_PUB.business_sum AS `business_sum`, DWD_EV_INDV_LOAN_PUB.customer_name AS `customer_name`, DWD_PRD_INFO.prd_name AS `prd_name` FROM DWD_EV_INDV_LOAN_PUB INNER JOIN DWD_PRD_INFO ON DWD_EV_INDV_LOAN_PUB.product_id = DWD_PRD_INFO.prd_code LIMIT 100
  ```
- `N_M11` 用信编号映射借据号及贷款金额  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_app_mapping.crd_app_no, dwd_ar_loan_info.loan_no, dwd_ar_loan_info.loan_amt FROM dwd_app_mapping INNER JOIN dwd_ar_loan_info ON dwd_app_mapping.loan_no = dwd_ar_loan_info.loan_no LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_APP_MAPPING.loan_app_no AS `loan_app_no`, DWD_APP_MAPPING.loan_no AS `loan_no`, DWD_AR_LOAN_INFO.loan_amt AS `loan_amt` FROM DWD_APP_MAPPING INNER JOIN DWD_AR_LOAN_INFO ON DWD_AR_LOAN_INFO.loan_no = DWD_APP_MAPPING.loan_no LIMIT 100
  ```
- `N_M12` 出账借据编号与借据正常本金余额  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.loan_no AS `出账借据编号`, dwd_ar_loan_info.normal_bal AS `借据正常本金余额` FROM dwd_ar_loan_info LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_LOAN_PUB.duebill_no AS `duebill_no`, DWD_AR_LOAN_INFO.normal_bal AS `normal_bal` FROM DWD_EV_INDV_LOAN_PUB INNER JOIN DWD_AR_LOAN_INFO ON DWD_AR_LOAN_INFO.loan_no = DWD_EV_INDV_LOAN_PUB.duebill_no LIMIT 100
  ```

