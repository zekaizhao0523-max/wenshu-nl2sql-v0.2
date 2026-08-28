# 召回评测报告

- 时间：None
- 黄金集：`evals/golden/recall_v2_dwd.jsonl`
- collection：`None` / points=None
- 库范围：current_raw None
- 检索模式：**legacy**
- 检索 limit：30；报告 K：[5, 10, 15, 30]
- 题量：单表 5 / 多表 0 / 合计正例 5
- Table MRR（全部）：1.0

## 单表 vs 多表（核心对比 @10）

| 分区 | 题数 | Table Hit@10 | Any-Table@10 | Column Hit@10 | Forbidden@10 | MRR |
|------|------|--------------|--------------|---------------|--------------|-----|
| 单表 | 5 | 100.0% | 100.0% | 100.0% | 0.0% | 1.0 |
| 多表 | 0 | — | — | — | — | None |
| 合计 | 5 | 100.0% | 100.0% | 100.0% | 0.0% | 1.0 |

## 单表 · 单字段 vs 多字段（@10）

| 分区 | 题数 | Table Hit@10 | Column Hit@10 | Forbidden@10 |
|------|------|--------------|---------------|--------------|
| 单表单字段 | 5 | 100.0% | 100.0% | 0.0% |
| 单表多字段 | 0 | — | — | — |

## 全部正例 · 主指标

| K | Table Hit | Any-Table Hit | Column Hit | Forbidden@K |
|---|-----------|---------------|------------|-------------|
| @5 | 100.0% | 100.0% | 100.0% | 0.0% |
| @10 | 100.0% | 100.0% | 100.0% | 0.0% |
| @15 | 100.0% | 100.0% | 100.0% | 0.0% |
| @30 | 100.0% | 100.0% | 100.0% | 0.0% |

## 按题型切片（主看 Table Hit@10）

| 题型 | 题数 | Table Hit@10 | Forbidden@10 |
|------|------|--------------|--------------|
| 全部正例 | 5 | 100.0% | 0.0% |
| 单表 | 5 | 100.0% | 0.0% |
| 单表单字段 | 5 | 100.0% | 0.0% |

## S1 / S2 精选（全集 Hit，非 Top-K 槽位）

S1/S2 是 LLM 从检索池 S_rtrv 中选出的精选集合，列数不固定；**不要**与历史 Hit@10 做同字段数对比。第 2 轮强制补全，空结果会启发式补列。

| 分区 | 题数 | S1 Table | S1 Column | S1 均列数 | S2 Table | S2 Column | S2 均列数 |
|------|------|----------|-----------|-----------|----------|-----------|-----------|
| 单表单字段 | 5 | 100.0% | 100.0% | 7.0 | 100.0% | 100.0% | 23.8 |
| 单表 | 5 | 100.0% | 100.0% | 7.0 | 100.0% | 100.0% | 23.8 |
| 合计 | 5 | 100.0% | 100.0% | 7.0 | 100.0% | 100.0% | 23.8 |

- S1 来源：llm_cover=5
- S2 来源：llm_cover=5

## MVP 判定

- **达标**：Table Hit@10 = 100.0% ≥ 80%

## 失败题（Table Hit@10 未过：单表 0 / 多表 0 / 共 0）

无
## 表过但字段未齐（Column Hit@10 未过，共 0）

无
## 易混淆污染（Forbidden@10，共 0）

无
## 下一步（今天下午）

1. 给失败题打标签（手册 §3.3）
2. 优先改 3～5 张核心表的 L1 description，增量同步向量
3. 再跑一次本脚本对比 Table@10

## SQL 准确率（含方案 A）

| 分区 | 题数 | Valid | StructAcc | PredExecOK | GoldExecOK | **ResultAcc** |
|------|------|-------|-----------|------------|------------|---------------|
| ALL | 5 | 100.0% | 100.0% | 100.0% | 100.0% | **0.0%** |
| single | 5 | 100.0% | 100.0% | 100.0% | 100.0% | **0.0%** |

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

