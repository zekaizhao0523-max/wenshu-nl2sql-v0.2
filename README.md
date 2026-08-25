# 问数（Wenshu）

面向 NL2SQL 的 **Schema 召回与元数据治理** 平台：自然语言 → 表/列/JOIN 召回 → M-Schema →（下游）SQL 生成。

## 能力

- L1 元数据治理：scan → staging → sync → 向量索引
- XiYan 式多路召回 + LLM S1/S2 选列
- 语义图 + 澄清闸门（缺槽时暂停，resume 后继续）
- 召回评测框架（`evals/scripts/run_recall_eval.py`）

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入本地 MySQL / Qdrant / 模型路径

# 初始化元数据表 + 启动 Web
python scripts/run_platform.py
```

浏览器打开 `http://127.0.0.1:8765`。

## 目录

| 路径 | 说明 |
|------|------|
| `wenshu/` | FastAPI 后端 + 静态管理台 |
| `scripts/` | 索引构建、本地栈、DB 配置 |
| `sql/templates/` | L1 DDL 模板 |
| `sql/demo/` | **示例业务表**（非生产 DDL） |
| `evals/golden/recall_demo.jsonl` | **脱敏评测集** |
| `docs/` | 架构与操作手册 |

## 评测

```bash
python evals/scripts/run_recall_eval.py --golden evals/golden/recall_demo.jsonl
```

报告输出到 `evals/reports/`（已在 `.gitignore` 忽略 json 明细）。

## 说明

- 本仓库为 **私有备份**，不含真实生产 DDL、完整黄金集与连接凭据。
- 完整业务数据与 77 题 DWD 黄金集请保留在本地工作区，勿提交 Git。
