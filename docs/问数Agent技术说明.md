# 问数 Agent 技术说明

| 属性 | 内容 |
|------|------|
| 版本 | v1.0 |
| 更新日期 | 2026-08-28 |
| 代码入口 | `wenshu/services/agent/` |

本文描述问数 Agent 的 **编排拓扑**、**SQL 生成两条路径**，以及与内部参考实现 **icecoding（NL2SQL M1–M11）** 的对照关系。

---

## 1. 定位

问数 Agent = **Schema 召回（既有平台能力）** + **结构化 QueryPlan** + **SQL 生成/校验/执行** + **风险与结果解读**。

- **线上/API**：`orchestrator.run_agent` → LangGraph `graph.py`
- **评测**：`final_sql.produce_final_sql`（与图逻辑对齐，便于批量跑分）

字段召回 **不修改算法**，统一调用 `retrieve_schema()`。

---

## 2. LangGraph 节点

```text
START
  → query_resolution      # 澄清闸门（prepare_query）
  → schema_retrieval      # retrieve_schema
  → plan_generation       # build_query_plan（规则 + 可选 LLM）
  → plan_validation       # validate_query_plan / normalize
  → sql_generation        # M7：icecoding 或 XiYan 分支
  → static_validation     # M8：AST 校验
  → sensitive_check       # 敏感规则
  → human_review          # 可 interrupt，需 approve API resume
  → sandbox_execution     # 只读执行
  → result_interpretation # M11 自然语言解读
  → END
```

**重试边**（对齐 icecoding）：

- `plan_validation` 失败 → 回 `plan_generation`（`max_plan_retries`）
- `static_validation` / `sandbox_execution` 失败 → 回 `sql_generation`（`max_retries`）

---

## 3. 与 icecoding 模块对照

参考仓库：`icecoding0305/NL2SQL`（内部）。问数实现映射如下：

| icecoding | 问数模块 | 说明 |
|-----------|----------|------|
| 澄清 / 语义图 | `query_clarify.prepare_query` | `SCHEMA_CLARIFY_GATE` |
| 字段召回 | `schema_retrieval.retrieve_schema` | XiYan 式多路 + S1/S2 |
| M6 QueryPlan | `plan_builder.build_query_plan` | 简单题确定性；复杂题 LLM Structured |
| 计划规范化 | `plan_normalize` / `plan_validate` | JOIN、列、limit 修正 |
| LogicalPlan | `logical_plan.build_logical_plan` | 中间表示 |
| **M7 SQL 生成** | `sql_generation.generate_sql` | **默认路径** |
| M8 静态校验 | `sql_validate.validate_sql_ast` | 方言、禁写、scope |
| 敏感 / 审批 | `risk.assess_risk` + `human_review` | `sensitive_rules.yaml` |
| M10 沙箱 | `sandbox.execute_readonly` | 只读、超时、行数上限 |
| M11 解读 | `result_interpretation.interpret_result` | 可选 LLM 摘要 |

---

## 4. SQL 生成：两条路径

### 4.1 默认路径 — 对齐 icecoding M7→M8→M10

开关：`AGENT_SQL_XIYAN_PIPELINE=0`（默认）。

实现：`wenshu/services/agent/sql_generation.py`

```text
QueryPlan + S2 schema（execution M-Schema）
    │
    ├─ 确定性编译成功 ──────────────────→ 直接返回 SQL
    │
    └─ 失败 / 不支持
           ├─ 首轮：AGENT_SQL_MULTI_CANDIDATE
           │         → 2~3 条 LLM 候选
           │         → sql_candidate_rank（AST 打分）选优
           │
           └─ 重试轮：复用 pending 未选候选
                     或 单条 LLM + sql_retry.txt 反馈
```

**与 icecoding M7 一致的设计点**：

| 点 | icecoding M7 | 问数默认 |
|----|--------------|----------|
| Schema 喂给 SQL | execution M-Schema（精选列） | S2 列（`s2_columns` 优先） |
| 确定性优先 | 成功即返回 | ✅ 相同 |
| 多候选 | 首轮 2 条 + AST 打分 | ✅ `AGENT_SQL_CANDIDATE_COUNT` |
| 重试 | M8/M10 回 M7，候选回退 | ✅ LangGraph 边 + `pending_candidates` |
| 执行选优 | 无 | 无 |

### 4.2 可选路径 — XiYan 流程级复刻

开关：`AGENT_SQL_XIYAN_PIPELINE=1` 或 `AGENT_SQL_EXEC_SELECT=1`。

实现：`sql_ensemble.run_pipeline_sql_select` + `sql_exec_select.select_sql_by_execution`

```text
QueryPlan
    │
    ├─ 确定性编译 → 候选池
    ├─ S1 schema × LLM 策略 → 候选池
    ├─ S2 schema × LLM 策略 → 候选池
    ├─ 静态校验过滤
    ├─ 执行失败 → self-refine 再生成
    └─ 沙箱执行多条 → 结果聚类 → 选最优 SQL
```

**与 XiYan-SQL 论文/工程的差异**：

| 能力 | XiYan 完整方案 | 问数 XiYan 可选路径 |
|------|----------------|---------------------|
| 多 schema 生成 | ✅ | ✅ S1 + S2 |
| 专用 SQL 微调模型 | QwenCoder 等 | ❌ 使用通用 LLM |
| Selection 学习排序 | 有 | ❌ 用执行结果聚类代替 |
| 字段召回 | 自有 | **不改**，仍用问数 `retrieve_schema` |

本地 DWD77 参考（`AGENT_SQL_XIYAN_PIPELINE=1`）：ResultAcc **47.0%**，StructAcc **59.7%**（2026-08-27 报告）。

---

## 5. icecoding vs 问数：SQL 生成专项对比

| 维度 | icecoding M7 | 问数（默认） | 问数（XiYan 开关） |
|------|--------------|--------------|-------------------|
| **编排** | 自研 graph | LangGraph | 同左，SQL 节点换 pipeline |
| **计划** | QueryPlan + LogicalPlan | 同构实现 | 相同 |
| **确定性编译** | 有，成功直接返回 | ✅ | 进候选池，可被聚类淘汰 |
| **LLM 候选数** | 首轮 2 | 可配 1–3 | S1/S2 × 多策略，池更大 |
| **候选打分** | AST 静态特征 | ✅ `sql_candidate_rank` | 静态过滤 + **执行聚类** |
| **重试反馈** | 校验/执行错误注入 prompt | ✅ `sql_retry.txt` | self-refine |
| **Schema 来源** | execution M-Schema | S2 精选列 | S1 **与** S2 双路 |
| **执行选优** | 无 | 无 | ✅ |
| **专用 SQL 模型** | 视部署而定 | 通用 LLM | 通用 LLM |
| **召回** | 外部接入 | **内置 XiYan 式召回** | 相同 |

### 5.1 何时用哪条路径

| 场景 | 建议 |
|------|------|
| 生产默认、与 icecoding 行为对齐 | **默认 icecoding 路径** |
| 召回已较好但 SQL 执行结果不稳定 | 试 **XiYan 流程**（多候选 + 执行选优） |
| 低延迟、省 token | 关 `AGENT_SQL_MULTI_CANDIDATE` 或 `CANDIDATE_COUNT=1` |
| 调试计划结构（StructAcc） | `AGENT_PLAN_LLM=1`；与 SQL 路径正交 |
| 只关心 SQL 文本 | `AGENT_SKIP_SANDBOX=1` |

### 5.2 共同瓶颈（两条路径都无法单独解决）

1. **StructAcc**：多表选错表、少列 → 根因在 **召回 + QueryPlan**，非 SQL 候选数
2. **LIMIT 语义**：`QueryPlan.limit` 默认 100，与 gold 对齐；题面无「前 N 条」时不应乱加 LIMIT
3. **JOIN 路径**：依赖 L1 `table_relation` 与召回 expanded_tables

---

## 6. QueryPlan 要点

- 模型：`plan_models.QueryPlan`（表、列、过滤、聚合、排序、JOIN、`limit`）
- 生成：`plan_builder` — 规则覆盖简单单表计数/过滤；`AGENT_PLAN_LLM=1` 强制 LLM
- 校验：`plan_validate` — 列是否落在召回池、JOIN 是否合法
- 规范化：`plan_normalize` — 去重、补默认 limit 等

---

## 7. 评测挂钩

`evals/scripts/run_sql_eval.py` 调用 `produce_final_sql`，指标：

| 指标 | 含义 |
|------|------|
| Table@K / Column@K | 召回阶段 |
| S1/S2 Column | 选列质量 |
| **StructAcc** | 预测 SQL 与 gold_sql 结构匹配（表/列/聚合等） |
| **ResultAcc** | gold 与 pred **双跑沙箱**，结果集一致（方案 A） |

`gold_sql` 由 `attach_gold_sql.py` 从 `QueryPlan` 确定性编译或标注写入。

---

## 8. 代码索引

| 文件 | 职责 |
|------|------|
| `graph.py` | LangGraph 拓扑与节点 |
| `orchestrator.py` | API 入口、trace、approve |
| `plan_builder.py` / `plan_validate.py` | M6 |
| `sql_generation.py` | M7 默认 |
| `sql_ensemble.py` | XiYan 多候选 pipeline |
| `sql_exec_select.py` | 执行聚类选优 |
| `sql_candidate_rank.py` | AST 打分 |
| `sql_compiler.py` | 确定性 SQL |
| `final_sql.py` | 评测用终态 SQL |
| `sandbox.py` | 只读执行 |
| `risk.py` | 敏感与审批 |

---

## 9. 相关文档

- [使用说明](./使用说明.md)
- [配置与参数说明](./配置与参数说明.md)
- [平台架构与Schema召回说明](./平台架构与Schema召回说明.md)
- [PRD-问数Agent](./PRD-问数Agent.md)
