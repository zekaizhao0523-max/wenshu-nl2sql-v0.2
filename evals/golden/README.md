# 黄金集说明

仓库内默认提交 **脱敏 demo 集** `recall_demo.jsonl`（6 题，示例表 `DEMO_*`）。

本地可重建 DWD 评测集（已 gitignore，勿提交）：

| 文件 | 题量 | 生成方式 |
|------|------|----------|
| `recall_dwd_standalone_100.jsonl` | 100 | `python evals/scripts/generate_recall_dwd_standalone_100.py` |
| `recall_v2_dwd.jsonl` | 77 | `python evals/scripts/rebuild_dwd_golden_sets.py`（40 单表 + 37 多表） |
| `recall_v2_dwd_100.jsonl` | 100 | 同上（standalone 全集） |

## 联合评测（召回 + SQL）

```bash
# 可选：预先写入 gold_sql（确定性编译 + 题意增强）
python evals/scripts/attach_gold_sql.py --golden evals/golden/recall_v2_dwd.jsonl
# 强制覆盖已有 gold_sql
python evals/scripts/attach_gold_sql.py --golden evals/golden/recall_v2_dwd.jsonl --force

python evals/scripts/run_sql_eval.py --golden evals/golden/recall_v2_dwd.jsonl --keyword-mode rule --column-select
python evals/scripts/run_sql_eval.py --golden evals/golden/recall_v2_dwd_100.jsonl --keyword-mode rule --column-select
```

### 指标说明

| 指标 | 含义 |
|------|------|
| Table@K | `must_tables` 是否出现在 expanded_tables Top-K |
| Column@K | `must_columns` 是否出现在列池 Top-K |
| S1 / S2 Column | LLM 选列后 must 列命中率 |
| **StructAcc** | 预测 SQL 与 `gold_sql` 结构一致（表/列/聚合等） |
| **ResultAcc** | `gold_sql` 与预测 SQL **双跑只读沙箱**，结果集一致（主 SQL 指标，方案 A） |

可用 `--no-with-result-acc` 关闭 ResultAcc（仅看结构与召回）。

### SQL 生成路径与评测

默认进程环境为 **icecoding 路径**（`AGENT_SQL_MULTI_CANDIDATE=1`，XiYan 关）。对比 XiYan 执行选优：

```bash
# PowerShell 示例
$env:AGENT_SQL_XIYAN_PIPELINE="1"
python evals/scripts/run_sql_eval.py --golden evals/golden/recall_v2_dwd.jsonl --keyword-mode rule --column-select
```

详见 [docs/配置与参数说明.md](../docs/配置与参数说明.md)、[docs/问数Agent技术说明.md](../docs/问数Agent技术说明.md)。

### 冒烟

```bash
python evals/scripts/run_sql_eval.py --golden evals/golden/recall_demo.jsonl --max-items 3
```

## 说明

- 原业务 77 题题面在脱敏时已删除；当前 77/100 为同域 DWD 表上的可复现重建集。
- `gold_sql` 与 Agent 默认 `QueryPlan.limit=100` 对齐；attach 脚本会按题意生成 SUM/GROUP BY/WHERE 等。
- 报告输出：`evals/reports/sql_eval_*.md`（已在 `.gitignore` 忽略部分 json 明细）。
