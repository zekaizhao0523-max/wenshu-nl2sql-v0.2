# 问数（Wenshu）

面向 NL2SQL 的 **Schema 召回、元数据治理与问数 Agent** 平台：自然语言 → 表/列/JOIN 召回 → QueryPlan → SQL → 只读执行 → 结果解读。

## 能力概览

| 模块 | 能力 |
|------|------|
| **元数据治理** | scan → staging → sync → L1 编辑（JOIN/指标/同义词/知识库） |
| **向量索引** | 表/列/JOIN/指标/文档 chunk → Qdrant |
| **Schema 召回** | XiYan 式多路检索 + LLM S1/S2 选列 + 澄清闸门 |
| **问数 Agent** | LangGraph 编排；默认 **icecoding M7→M8→M10**；可选 **XiYan 执行选优** |
| **评测** | 召回评测 + SQL 联合评测（StructAcc / ResultAcc） |

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入本地 MySQL / Qdrant / 模型

python scripts/run_platform.py
```

浏览器打开 **http://127.0.0.1:8765**。

推荐首次使用流程见 **[docs/使用说明.md](docs/使用说明.md)**。

## 目录

| 路径 | 说明 |
|------|------|
| `wenshu/` | FastAPI 后端 + 静态管理台 |
| `wenshu/services/agent/` | 问数 Agent（LangGraph、SQL 生成、沙箱） |
| `scripts/` | 索引构建、本地栈、DB 配置 |
| `sql/templates/` | L1 DDL 模板 |
| `sql/demo/` | 示例业务表（非生产 DDL） |
| `evals/golden/` | 脱敏 demo 集；DWD 集本地重建 |
| `docs/` | 架构、使用说明、参数说明 |

## 问数 Agent API

```http
POST /api/agent/query      # 澄清 → 召回 → 计划 → SQL → 风险 → 沙箱 → 解读
POST /api/agent/approve    # 人工审批 resume（interrupt_before=human_review）
POST /api/search/test      # 仅 Schema 召回
POST /api/nl2sql/mschema   # 问句 → M-Schema
```

管理台 **「问数 Agent」** 页可交互调试。SQL 生成默认对齐 icecoding；开 `AGENT_SQL_XIYAN_PIPELINE=1` 可走多候选 + 执行聚类选优。

## 评测

```bash
# Schema 召回
python evals/scripts/run_recall_eval.py --golden evals/golden/recall_demo.jsonl

# 召回 + SQL（需本地 DWD 黄金集）
python evals/scripts/attach_gold_sql.py --golden evals/golden/recall_v2_dwd.jsonl
python evals/scripts/run_sql_eval.py --golden evals/golden/recall_v2_dwd.jsonl --keyword-mode rule --column-select
```

报告输出到 `evals/reports/`。

## 文档

| 文档 | 内容 |
|------|------|
| **[使用说明](docs/使用说明.md)** | 平台功能、工作流、API、常见问题 |
| **[配置与参数说明](docs/配置与参数说明.md)** | 环境变量、调参建议、评测参数 |
| **[问数Agent技术说明](docs/问数Agent技术说明.md)** | 编排节点、SQL 双路径、与 icecoding 对比 |
| [平台架构与Schema召回说明](docs/平台架构与Schema召回说明.md) | 召回算法与系统架构 |
| [召回测试实验操作手册](docs/召回测试实验操作手册.md) | 黄金集与指标解读 |
| [平台建设历程](docs/平台建设历程.md) | 项目演进 |

## 说明

- 本仓库为 **私有备份**，不含真实生产 DDL、完整 DWD 黄金集与连接凭据。
- 完整业务数据与 77/100 题 DWD 黄金集请保留在本地工作区，勿提交 Git。
