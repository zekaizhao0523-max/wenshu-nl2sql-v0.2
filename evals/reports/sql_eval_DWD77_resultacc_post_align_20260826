# 召回评测报告

- 时间：None
- 黄金集：`evals/golden/recall_v2_dwd.jsonl`
- collection：`None` / points=None
- 库范围：current_raw None
- 检索模式：**legacy**
- 检索 limit：30；报告 K：[5, 10, 15, 30]
- 题量：单表 40 / 多表 37 / 合计正例 77
- Table MRR（全部）：0.961

## 单表 vs 多表（核心对比 @10）

| 分区 | 题数 | Table Hit@10 | Any-Table@10 | Column Hit@10 | Forbidden@10 | MRR |
|------|------|--------------|--------------|---------------|--------------|-----|
| 单表 | 40 | 100.0% | 100.0% | 95.0% | 7.5% | 0.975 |
| 多表 | 37 | 81.1% | 97.3% | 24.3% | 16.2% | 0.9459 |
| 合计 | 77 | 90.9% | 98.7% | 61.0% | 11.7% | 0.961 |

## 单表 · 单字段 vs 多字段（@10）

| 分区 | 题数 | Table Hit@10 | Column Hit@10 | Forbidden@10 |
|------|------|--------------|---------------|--------------|
| 单表单字段 | 20 | 100.0% | 100.0% | 10.0% |
| 单表多字段 | 20 | 100.0% | 90.0% | 5.0% |

## 全部正例 · 主指标

| K | Table Hit | Any-Table Hit | Column Hit | Forbidden@K |
|---|-----------|---------------|------------|-------------|
| @5 | 90.9% | 98.7% | 51.9% | 6.5% |
| @10 | 90.9% | 98.7% | 61.0% | 11.7% |
| @15 | 90.9% | 98.7% | 64.9% | 11.7% |
| @30 | 90.9% | 98.7% | 75.3% | 13.0% |

## 按题型切片（主看 Table Hit@10）

| 题型 | 题数 | Table Hit@10 | Forbidden@10 |
|------|------|--------------|--------------|
| 全部正例 | 77 | 90.9% | 11.7% |
| JOIN | 37 | 81.1% | 16.2% |
| 单表 | 40 | 100.0% | 7.5% |
| 单表单字段 | 20 | 100.0% | 10.0% |
| 单表多字段 | 20 | 100.0% | 5.0% |
| 多表 | 37 | 81.1% | 16.2% |

## S1 / S2 精选（全集 Hit，非 Top-K 槽位）

S1/S2 是 LLM 从检索池 S_rtrv 中选出的精选集合，列数不固定；**不要**与历史 Hit@10 做同字段数对比。第 2 轮强制补全，空结果会启发式补列。

| 分区 | 题数 | S1 Table | S1 Column | S1 均列数 | S2 Table | S2 Column | S2 均列数 |
|------|------|----------|-----------|-----------|----------|-----------|-----------|
| 单表单字段 | 20 | 100.0% | 95.0% | 7.0 | 100.0% | 100.0% | 27.9 |
| 单表多字段 | 20 | 100.0% | 95.0% | 9.3 | 100.0% | 100.0% | 29.8 |
| 单表 | 40 | 100.0% | 95.0% | 8.1 | 100.0% | 100.0% | 28.8 |
| 多表 | 37 | 94.6% | 48.6% | 13.2 | 100.0% | 67.6% | 30.5 |
| 合计 | 77 | 97.4% | 72.7% | 10.6 | 100.0% | 84.4% | 29.6 |

- S1 来源：llm=1, llm_cover=76
- S2 来源：llm=1, llm_cover=51, llm_empty_widen_cover=25

## MVP 判定

- **达标**：Table Hit@10 = 90.9% ≥ 80%

## 失败题（Table Hit@10 未过：单表 0 / 多表 7 / 共 7）

### N_M09 [multi] · 40岁以上已婚客户授信额度与用信额度
- must_tables：['DWD_IP_INDV_CUST_INFO', 'DWD_EV_INDV_CRD_APP', 'DWD_EV_INDV_LOAN_APP']
- expanded_tables：['dwd_ev_indv_crd_app']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_indv_crd_app']
- hits：column:dwd_ev_indv_crd_app.app_cred_amt(0.5459), column:dwd_ev_indv_crd_app.apprv_cred_amt(0.5411), column:dwd_ev_indv_crd_app.marr_status(0.6112), column:dwd_ev_indv_crd_app.age(0.5831), column:dwd_ev_indv_crd_app.cust_rate(0.5394), column:dwd_ev_indv_crd_app.account_manager(0.538), column:dwd_ev_indv_crd_app.cred_line_type(0.5366), column:dwd_ev_indv_crd_app.loan_purpose(0.5344)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M18 [multi] · 授信审批额度与后续用信申请金额
- must_tables：['DWD_EV_INDV_CRD_APP', 'DWD_EV_INDV_LOAN_APP']
- expanded_tables：['dwd_ev_indv_crd_app']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_indv_crd_app']
- hits：column:dwd_ev_indv_crd_app.app_cred_amt(0.6675), column:dwd_ev_indv_crd_app.apprv_cred_amt(0.6656), column:dwd_ev_indv_crd_app.apply_date(0.6212), column:dwd_ev_indv_crd_app.last_update_time(0.6193), column:dwd_ev_indv_crd_app.last_update_date(0.6179), column:dwd_ev_indv_crd_app.app_no(0.617), column:dwd_ev_indv_crd_app.loan_purpose(0.617), column:dwd_ev_indv_crd_app.cred_date_start(0.6111)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M19 [multi] · 客户性别分布及用信申请量
- must_tables：['DWD_IP_INDV_CUST_INFO', 'DWD_EV_INDV_LOAN_APP']
- expanded_tables：['dwd_ev_indv_loan_app']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_indv_loan_app']
- hits：column:dwd_ev_indv_loan_app.marr_status(0.6205), column:dwd_ev_indv_loan_app.sex(0.7046), column:dwd_ev_indv_loan_app.app_loan_amt(0.6197), column:dwd_ev_indv_loan_app.cost_date_start(0.5947), column:dwd_ev_indv_loan_app.apprv_loan_amt(0.5945), column:dwd_ev_indv_loan_app.loan_app_no(0.5942), column:dwd_ev_indv_loan_app.loan_purpose(0.592), column:dwd_ev_indv_loan_app.phone_no(0.5911)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M20 [multi] · 对公客户名称规模及授信产品名
- must_tables：['DWD_IP_CORP_CUST_INFO', 'DWD_PRD_INFO']
- expanded_tables：['dwd_ev_comp_crd_app']
- first_hit_rank（expanded）：None
- top_tables（列槽@10）：['dwd_ev_comp_crd_app']
- hits：column:dwd_ev_comp_crd_app.customername(0.6633), column:dwd_ev_comp_crd_app.productid(0.6598), column:dwd_ev_comp_crd_app.baseproduct(0.643), column:dwd_ev_comp_crd_app.cusindustrytype(0.6325), column:dwd_ev_comp_crd_app.credittype(0.6313), column:dwd_ev_comp_crd_app.loantype(0.6313), column:dwd_ev_comp_crd_app.businesssum(0.6311), column:dwd_ev_comp_crd_app.customerid(0.6268)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M25 [multi] · 客户主档学历与授信审批额度
- must_tables：['DWD_IP_INDV_CUST_INFO', 'DWD_EV_INDV_CRD_APP']
- expanded_tables：['dwd_ev_indv_crd_app']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_indv_crd_app']
- hits：column:dwd_ev_indv_crd_app.apprv_cred_amt(0.6335), column:dwd_ev_indv_crd_app.diploma(0.7638), column:dwd_ev_indv_crd_app.app_cred_amt(0.626), column:dwd_ev_indv_crd_app.career(0.6178), column:dwd_ev_indv_crd_app.age(0.615), column:dwd_ev_indv_crd_app.cust_id(0.6142), column:dwd_ev_indv_crd_app.year_income(0.6091), column:dwd_ev_indv_crd_app.cust_rate(0.608)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M29 [multi] · 客户婚姻与用信申请婚姻字段
- must_tables：['DWD_IP_INDV_CUST_INFO', 'DWD_EV_INDV_LOAN_APP']
- expanded_tables：['dwd_ev_indv_loan_app']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_indv_loan_app']
- hits：column:dwd_ev_indv_loan_app.marr_status(0.7776), column:dwd_ev_indv_loan_app.loan_app_no(0.6368), column:dwd_ev_indv_loan_app.loan_purpose(0.6356), column:dwd_ev_indv_loan_app.cost_date_start(0.6352), column:dwd_ev_indv_loan_app.phone_no(0.6207), column:dwd_ev_indv_loan_app.app_loan_amt(0.6181), column:dwd_ev_indv_loan_app.prd_code(0.6178), column:dwd_ev_indv_loan_app.cost_date_end(0.6166)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

### N_M36 [multi] · 客户与用信申请居住地址
- must_tables：['DWD_IP_INDV_CUST_INFO', 'DWD_EV_INDV_LOAN_APP']
- expanded_tables：['dwd_ev_indv_loan_app']
- first_hit_rank（expanded）：1
- first_hit_rank（列槽）：1
- top_tables（列槽@10）：['dwd_ev_indv_loan_app']
- hits：column:dwd_ev_indv_loan_app.loan_app_no(0.6383), column:dwd_ev_indv_loan_app.app_loan_amt(0.6295), column:dwd_ev_indv_loan_app.cost_date_start(0.6273), column:dwd_ev_indv_loan_app.phone_no(0.6252), column:dwd_ev_indv_loan_app.loan_purpose(0.6228), column:dwd_ev_indv_loan_app.apprv_loan_amt(0.6181), column:dwd_ev_indv_loan_app.marr_status(0.6162), column:dwd_ev_indv_loan_app.cost_date_end(0.6116)
- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`

## 表过但字段未齐（Column Hit@10 未过，共 23）

- **N_S25** 借据放款金额与执行年利率 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'year_rate'}]
- **N_S39** 产品增信方式与授信类型 → 期望字段：[{'table': 'DWD_PRD_INFO', 'column': 'credit_method'}, {'table': 'DWD_PRD_INFO', 'column': 'credit_type'}]
- **N_M01** 35岁以上个人客户及其用信申请金额 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'age'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'cust_id'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}]
- **N_M02** 个人客户主档姓名手机与授信审批额度 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'name'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'phone_no'}, {'table': 'DWD_EV_INDV_CRD_APP', 'column': 'apprv_cred_amt'}]
- **N_M03** 借据金额及关联还款计划应还本金 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}, {'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_princ'}]
- **N_M04** 借据对应产品名称与本金余额 → 期望字段：[{'table': 'DWD_PRD_INFO', 'column': 'prd_code'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'prin_bal'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M05** 用信申请关联产品名称与申请额度 → 期望字段：[{'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'prd_code'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M06** 逾期追偿本金及借据客户姓名 → 期望字段：[{'table': 'DWD_EV_OVERDUE_REPAY', 'column': 'prin_amt'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'name'}]
- **N_M07** 实还本金与实还日期按借据汇总 → 期望字段：[{'table': 'DWD_EV_REPAY_DETAIL', 'column': 'princ'}, {'table': 'DWD_EV_REPAY_DETAIL', 'column': 'repay_date'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]
- **N_M10** 产品宽限期及下属借据逾期本金 → 期望字段：[{'table': 'DWD_PRD_INFO', 'column': 'grace_day'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_code'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'ovd_bal'}]
- **N_M11** 用信编号映射借据号及贷款金额 → 期望字段：[{'table': 'DWD_APP_MAPPING', 'column': 'loan_app_no'}, {'table': 'DWD_APP_MAPPING', 'column': 'loan_no'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}]
- **N_M13** 计划应还利息对比明细实还利息 → 期望字段：[{'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_int'}, {'table': 'DWD_EV_REPAY_DETAIL', 'column': 'int'}, {'table': 'DWD_EV_REPAY_PLAN', 'column': 'term_no'}]
- **N_M14** 已婚客户分产品统计用信笔数金额 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'marriage'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_name'}]
- **N_M15** 客户年收入单位及用信贷款用途 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'per_mon_income'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'unit_name'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'loan_purpose'}]
- **N_M16** 户籍省份与名下借据逾期本金 → 期望字段：[{'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'hhdist'}, {'table': 'DWD_IP_INDV_CUST_INFO', 'column': 'cust_id'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'ovd_bal'}]
- **N_M21** 借据年利率与产品官方宽限期 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'year_rate'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'grace_day'}, {'table': 'DWD_PRD_INFO', 'column': 'prd_code'}]
- **N_M22** 逾期追偿本金与计划应还本金 → 期望字段：[{'table': 'DWD_EV_OVERDUE_REPAY', 'column': 'prin_amt'}, {'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_princ'}, {'table': 'DWD_EV_REPAY_PLAN', 'column': 'loan_no'}]
- **N_M26** 渠道名称及各渠道用信金额 → 期望字段：[{'table': 'DWD_PRD_INFO', 'column': 'channel_name'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'app_loan_amt'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'prd_code'}]
- **N_M27** 借据结清日与明细实还日期 → 期望字段：[{'table': 'DWD_AR_LOAN_INFO', 'column': 'payoff_date'}, {'table': 'DWD_EV_REPAY_DETAIL', 'column': 'repay_date'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]
- **N_M28** 对公注册资本与关联借据贷款金额 → 期望字段：[{'table': 'DWD_IP_CORP_CUST_INFO', 'column': 'register_amount'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_amt'}]
- **N_M30** 计划应还本金与借据正常本金余额 → 期望字段：[{'table': 'DWD_EV_REPAY_PLAN', 'column': 'rpy_princ'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'normal_bal'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]
- **N_M31** 授信拒绝码与用信申请流水号 → 期望字段：[{'table': 'DWD_EV_INDV_CRD_APP', 'column': 'hit_reasoncode_set'}, {'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'loan_app_no'}]
- **N_M35** 用信与借据产品编码一致性 → 期望字段：[{'table': 'DWD_EV_INDV_LOAN_APP', 'column': 'prd_code'}, {'table': 'DWD_AR_LOAN_INFO', 'column': 'loan_no'}]

## 易混淆污染（Forbidden@10，共 9）

- **N_S08** 逾期处置追偿本金总额 → forbidden=['dwd_ev_repay_plan']；top=['dwd_ev_overdue_repay', 'dwd_ev_repay_plan', 'dwd_ar_loan_info']
- **N_S13** 还款计划应还利息总额 → forbidden=['dwd_ev_repay_detail']；top=['dwd_ev_repay_plan', 'dwd_ev_repay_detail', 'dwd_ev_overdue_repay']
- **N_S34** 实还本金按借据号汇总 → forbidden=['dwd_ev_repay_plan']；top=['dwd_ev_repay_detail', 'dwd_ar_loan_info', 'dwd_ev_indv_loan_pub', 'dwd_ev_repay_plan']
- **N_M02** 个人客户主档姓名手机与授信审批额度 → forbidden=['dwd_ev_indv_loan_app']；top=['dwd_ev_indv_crd_app', 'dwd_ev_indv_loan_app', 'dwd_ip_indv_cust_info']
- **N_M03** 借据金额及关联还款计划应还本金 → forbidden=['dwd_ev_repay_detail']；top=['dwd_ev_repay_plan', 'dwd_ar_loan_info', 'dwd_ev_repay_detail', 'dwd_ev_overdue_repay', 'dwd_ev_indv_loan_pub']
- **N_M06** 逾期追偿本金及借据客户姓名 → forbidden=['dwd_ev_repay_plan']；top=['dwd_ar_loan_info', 'dwd_ev_overdue_repay', 'dwd_ev_repay_plan']
- **N_M07** 实还本金与实还日期按借据汇总 → forbidden=['dwd_ev_repay_plan']；top=['dwd_ev_repay_detail', 'dwd_ar_loan_info', 'dwd_ev_repay_plan', 'dwd_ev_indv_loan_pub']
- **N_M13** 计划应还利息对比明细实还利息 → forbidden=['dwd_ev_overdue_repay']；top=['dwd_ev_repay_plan', 'dwd_ev_repay_detail', 'dwd_ev_overdue_repay']
- **N_M22** 逾期追偿本金与计划应还本金 → forbidden=['dwd_ev_repay_detail']；top=['dwd_ev_repay_plan', 'dwd_ev_overdue_repay', 'dwd_ev_repay_detail']

## 下一步（今天下午）

1. 给失败题打标签（手册 §3.3）
2. 优先改 3～5 张核心表的 L1 description，增量同步向量
3. 再跑一次本脚本对比 Table@10

## SQL 准确率（含方案 A）

| 分区 | 题数 | Valid | StructAcc | PredExecOK | GoldExecOK | **ResultAcc** |
|------|------|-------|-----------|------------|------------|---------------|
| ALL | 77 | 28.6% | 24.7% | 27.1% | 100.0% | **8.6%** |
| single | 40 | 42.5% | 40.0% | 35.0% | 100.0% | **15.0%** |
| multi | 37 | 13.5% | 8.1% | 16.7% | 100.0% | **0.0%** |

> **ResultAcc（方案 A 主指标）**：`gold_sql` 与预测 SQL 均在只读沙箱执行成功，且结果集按值多重集相等（忽略列名顺序）。
> 无 `gold_sql` 或 gold 执行失败的题：`result_acc` 为 null，不计入 ResultAcc 分母。

### ResultAcc 失败样例（最多 25）

- `N_S03` 本科以上学历个人客户人数  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT COUNT(DWD_IP_INDV_CUST_INFO.highest_schooling) AS `highest_schooling_count` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S04` 全部用信申请金额求和  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_EV_INDV_LOAN_APP.app_loan_amt AS `app_loan_amt` FROM DWD_EV_INDV_LOAN_APP LIMIT 100
  ```
- `N_S05` 授信审批额度总计  pred_rows=1 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT SUM(dwd_ev_indv_crd_app.apprv_cred_amt) FROM dwd_ev_indv_crd_app LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_INDV_CRD_APP.apprv_cred_amt AS `apprv_cred_amt` FROM DWD_EV_INDV_CRD_APP LIMIT 100
  ```
- `N_S06` 借据本金余额汇总  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT SUM(DWD_AR_LOAN_INFO.prin_bal) AS `prin_bal_sum` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S07` 存在逾期的借据有多少  pred_rows=1 gold_rows=1  err=result_mismatch
  pred:
  ```sql
  SELECT COUNT(dwd_ar_loan_info.loan_no) FROM dwd_ar_loan_info WHERE dwd_ar_loan_info.ovd_bal > 0 LIMIT 100
  ```
  gold:
  ```sql
  SELECT COUNT(DWD_AR_LOAN_INFO.ovd_bal) AS `ovd_bal_count` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S09` 各信贷产品宽限期天数  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_PRD_INFO.grace_day AS `grace_day` FROM DWD_PRD_INFO LIMIT 100
  ```
- `N_S11` 个人客户按性别分组统计  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT COUNT(DWD_IP_INDV_CUST_INFO.sex) AS `sex_count` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S12` 借据层面宽限天数分布  pred_rows=None gold_rows=100  err=EXPLAIN 失败: (1140, "In aggregated query without GROUP BY, expression #2 of SELECT list contains nonaggregated column 'vectortest.dwd_ar_loan_info.grace_day'; this is incompatible with sql_mode=only_full_group_by")
  pred:
  ```sql
  SELECT COUNT(grace_day) AS `count`, grace_day AS `grace_day` FROM dwd_ar_loan_info ORDER BY CASE WHEN grace_day IS NULL THEN 1 ELSE 0 END, grace_day ASC LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.grace_day AS `grace_day` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S13` 还款计划应还利息总额  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT SUM(DWD_EV_REPAY_PLAN.rpy_int) AS `rpy_int_sum` FROM DWD_EV_REPAY_PLAN LIMIT 100
  ```
- `N_S14` 逾期追偿场景下用户还款日期分布  pred_rows=None gold_rows=100  err=EXPLAIN 失败: (1140, "In aggregated query without GROUP BY, expression #2 of SELECT list contains nonaggregated column 'vectortest.dwd_ev_overdue_repay.practical_pay_date'; this is incompatible with sql_mode=only_full_group_by")
  pred:
  ```sql
  SELECT COUNT(practical_pay_date) AS `count`, practical_pay_date AS `practical_pay_date` FROM dwd_ev_overdue_repay ORDER BY CASE WHEN practical_pay_date IS NULL THEN 1 ELSE 0 END, practical_pay_date ASC LIMIT 100
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
- `N_S18` 代偿记录中的代偿本金余额  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_SR_CLAIM_DETAIL.dc_bal AS `dc_bal` FROM DWD_SR_CLAIM_DETAIL LIMIT 100
  ```
- `N_S20` 信贷审批流程当前节点分布  pred_rows=None gold_rows=0  err=EXPLAIN 失败: (1140, "In aggregated query without GROUP BY, expression #2 of SELECT list contains nonaggregated column 'vectortest.dwd_ev_tran_flow_info.app_node'; this is incompatible with sql_mode=only_full_group_by")
  pred:
  ```sql
  SELECT COUNT(app_node), app_node FROM dwd_ev_tran_flow_info ORDER BY CASE WHEN app_node IS NULL THEN 1 ELSE 0 END, app_node ASC LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_EV_TRAN_FLOW_INFO.app_node AS `app_node` FROM DWD_EV_TRAN_FLOW_INFO LIMIT 100
  ```
- `N_S21` 个人客户婚姻与性别交叉统计  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_IP_INDV_CUST_INFO.marriage AS `marriage`, DWD_IP_INDV_CUST_INFO.sex AS `sex` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S22` 授信拒绝码与审批状态明细  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_EV_INDV_CRD_APP.hit_reasoncode_set AS `hit_reasoncode_set`, DWD_EV_INDV_CRD_APP.apprv_state AS `apprv_state` FROM DWD_EV_INDV_CRD_APP LIMIT 100
  ```
- `N_S23` 还款计划每期应还本金与应还利息  pred_rows=None gold_rows=None  err=empty pred sql
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
- `N_S25` 借据放款金额与执行年利率  pred_rows=100 gold_rows=100  err=result_mismatch
  pred:
  ```sql
  SELECT dwd_ar_loan_info.loan_amt AS `放款金额`, dwd_ar_loan_info.year_rate AS `执行年利率` FROM dwd_ar_loan_info INNER JOIN dwd_ev_indv_loan_pub ON dwd_ar_loan_info.loan_no = dwd_ev_indv_loan_pub.duebill_no LIMIT 100
  ```
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.loan_amt AS `loan_amt`, DWD_AR_LOAN_INFO.year_rate AS `year_rate` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S26` 个人客户联系电话与户籍省份  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_IP_INDV_CUST_INFO.phone_no AS `phone_no`, DWD_IP_INDV_CUST_INFO.hhdist AS `hhdist` FROM DWD_IP_INDV_CUST_INFO LIMIT 100
  ```
- `N_S27` 对公客户注册资本与企业规模  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_IP_CORP_CUST_INFO.register_amount AS `register_amount`, DWD_IP_CORP_CUST_INFO.ent_scale AS `ent_scale` FROM DWD_IP_CORP_CUST_INFO LIMIT 100
  ```
- `N_S28` 职工人数超过100的企业名称  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_IP_CORP_CUST_INFO.employee_number AS `employee_number`, DWD_IP_CORP_CUST_INFO.cust_name AS `cust_name` FROM DWD_IP_CORP_CUST_INFO LIMIT 100
  ```
- `N_S29` 渠道维度产品单笔贷款上限  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_PRD_INFO.channel_name AS `channel_name`, DWD_PRD_INFO.loan_limit AS `loan_limit` FROM DWD_PRD_INFO LIMIT 100
  ```
- `N_S30` 已结清借据的结清日与借款人姓名  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_AR_LOAN_INFO.payoff_date AS `payoff_date`, DWD_AR_LOAN_INFO.name AS `name` FROM DWD_AR_LOAN_INFO LIMIT 100
  ```
- `N_S31` 首贷标识与本次出账放款金额  pred_rows=None gold_rows=None  err=empty pred sql
  gold:
  ```sql
  SELECT DWD_EV_INDV_LOAN_PUB.is_first_loan AS `is_first_loan`, DWD_EV_INDV_LOAN_PUB.business_sum AS `business_sum` FROM DWD_EV_INDV_LOAN_PUB LIMIT 100
  ```

