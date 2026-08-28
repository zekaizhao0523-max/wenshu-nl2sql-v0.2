#!/usr/bin/env python3
"""召回 + SQL 联合评测。

输出：
  - Table Hit@10 / Column Hit@10（与 run_recall_eval 同口径）
  - S1 / S2 table/column hit
  - SQL：Valid / StructAcc / PredExecOK / ResultAcc（均基于 Agent 对齐重试后的最终 SQL）
  - 过程指标：首轮 plan 通过率、plan/SQL 尝试次数（不计入主准确率）

用法:
  python evals/scripts/attach_gold_sql.py --golden evals/golden/recall_v2_dwd.jsonl
  python evals/scripts/run_sql_eval.py --golden evals/golden/recall_v2_dwd.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from evals.scripts.run_recall_eval import (  # noqa: E402
    aggregate,
    eval_one_v2,
    load_golden,
    render_report,
)
from wenshu.services.agent.final_sql import produce_final_sql  # noqa: E402
from wenshu.services.agent.sql_ast import SqlDialect  # noqa: E402
from wenshu.services.agent.sql_result_match import (  # noqa: E402
    build_gold_sql,
    evaluate_result_accuracy,
)
from wenshu.services.agent.sql_validate import validate_sql_ast  # noqa: E402


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _normalize_sql(sql: str) -> str:
    text = (sql or "").strip().rstrip(";")
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def score_sql(item: dict, sql: str, *, used_tables: list[str] | None = None) -> dict:
    must_tables = {_norm(t) for t in (item.get("must_tables") or [])}
    must_cols = {
        (_norm(c.get("table")), _norm(c.get("column")))
        for c in (item.get("must_columns") or [])
        if c.get("table") and c.get("column")
    }
    out = {
        "sql": sql,
        "sql_valid": False,
        "sql_table_acc": False,
        "sql_column_acc": False,
        "sql_struct_acc": False,
        "sql_exact": False,
        "blocked_reason": None,
        "errors": [],
        "ast_tables": [],
        "ast_columns": [],
        "source": None,
        "gold_sql": item.get("gold_sql"),
        "result_acc": None,
        "pred_exec_ok": None,
        "gold_exec_ok": None,
    }
    if not (sql or "").strip():
        out["errors"] = ["empty sql"]
        return out

    dialect = SqlDialect("mysql")
    v = validate_sql_ast(sql, dialect="mysql", generation_source="model")
    if v.blocked_reason:
        out["blocked_reason"] = v.blocked_reason
        out["errors"] = v.errors
        return out
    if not v.ok and v.errors and "语法" in (v.errors[0] or ""):
        out["errors"] = v.errors
        return out

    try:
        expr = dialect.parse(sql, "mysql")
    except Exception as exc:  # noqa: BLE001
        out["errors"] = [f"parse: {exc}"]
        return out

    ast_tables = {_norm(t) for t in dialect.extract_tables(expr)}
    ast_cols = {(_norm(t), _norm(c)) for t, c in dialect.extract_columns(expr)}
    col_names = {c for _t, c in ast_cols}
    out["ast_tables"] = sorted(ast_tables)
    out["ast_columns"] = sorted(f"{t}.{c}" if t else c for t, c in ast_cols)
    out["sql_valid"] = True

    table_ok = (not must_tables) or must_tables.issubset(ast_tables)
    if used_tables:
        table_ok = table_ok or must_tables.issubset({_norm(t) for t in used_tables})

    col_ok = True
    if must_cols:
        for mt, mc in must_cols:
            if (mt, mc) in ast_cols or mc in col_names:
                continue
            col_ok = False
            break

    out["sql_table_acc"] = table_ok
    out["sql_column_acc"] = col_ok
    out["sql_struct_acc"] = bool(table_ok and col_ok)

    ref = item.get("gold_sql")
    if ref:
        out["sql_exact"] = _normalize_sql(sql) == _normalize_sql(str(ref))
        out["gold_sql"] = ref
    return out


def _pct(vals: list[bool | None]) -> str:
    xs = [v for v in vals if v is not None]
    if not xs:
        return "—"
    return f"{100.0 * sum(1 for v in xs if v) / len(xs):.1f}%"


def render_sql_section(rows: list[dict]) -> list[str]:
    lines = ["", "## SQL 准确率（含方案 A）", ""]
    lines.append(
        "> 以下 Valid / StructAcc / PredExecOK / ResultAcc 均基于 **Agent 对齐重试后的最终 SQL**；"
        "中间被丢弃的 plan/SQL 不计入。"
    )
    lines.append("")
    lines.append(
        "| 分区 | 题数 | Valid | StructAcc | PredExecOK | GoldExecOK | **ResultAcc** |"
    )
    lines.append(
        "|------|------|-------|-----------|------------|------------|---------------|"
    )
    groups = {
        "ALL": rows,
        "single": [r for r in rows if r.get("suite") == "single"],
        "multi": [r for r in rows if r.get("suite") == "multi"],
    }
    for name, rs in groups.items():
        if not rs:
            continue
        sqls = [r.get("sql_metrics") or {} for r in rs]
        lines.append(
            "| {name} | {n} | {v} | {s} | {p} | {g} | **{r}** |".format(
                name=name,
                n=len(rs),
                v=_pct([s.get("sql_valid") for s in sqls]),
                s=_pct([s.get("sql_struct_acc") for s in sqls]),
                p=_pct([s.get("pred_exec_ok") for s in sqls]),
                g=_pct([s.get("gold_exec_ok") for s in sqls]),
                r=_pct([s.get("result_acc") for s in sqls]),
            )
        )
    lines.append("")
    # 过程指标
    sqls_all = [r.get("sql_metrics") or {} for r in rows]
    first_pass = [s.get("plan_first_pass") for s in sqls_all if s.get("plan_attempts")]
    exhausted = sum(1 for s in sqls_all if s.get("terminal") == "plan_exhausted")
    sql_ex = sum(1 for s in sqls_all if s.get("terminal") in {"sql_exhausted", "generate_error"})
    avg_plan = (
        sum(int(s.get("plan_attempts") or 0) for s in sqls_all) / max(1, len(sqls_all))
    )
    avg_sql = (
        sum(int(s.get("sql_attempts") or 0) for s in sqls_all) / max(1, len(sqls_all))
    )
    lines.append("### 过程指标（不计入主准确率）")
    lines.append("")
    lines.append(
        f"- 首轮 plan 通过率：{_pct(first_pass)}；平均 plan 尝试 {avg_plan:.2f} 次；"
        f"平均 SQL 尝试 {avg_sql:.2f} 次"
    )
    lines.append(
        f"- 最终无 SQL：plan 耗尽 {exhausted} 题，SQL 耗尽/生成失败 {sql_ex} 题"
    )
    lines.append("")
    lines.append(
        "> **ResultAcc（方案 A 主指标）**：`gold_sql` 与预测 SQL 均在只读沙箱执行成功，"
        "且结果集按值多重集相等（忽略列名顺序）。"
    )
    lines.append(
        "> 无 `gold_sql` 或 gold 执行失败的题：`result_acc` 为 null，不计入 ResultAcc 分母。"
    )
    lines.append("")

    fails = [r for r in rows if (r.get("sql_metrics") or {}).get("result_acc") is False][:25]
    if fails:
        lines.append("### ResultAcc 失败样例（最多 25）")
        lines.append("")
        for r in fails:
            sm = r.get("sql_metrics") or {}
            lines.append(
                f"- `{r.get('id')}` {r.get('question', '')[:40]}  "
                f"pred_rows={sm.get('pred_row_count')} gold_rows={sm.get('gold_row_count')}  "
                f"err={sm.get('pred_error') or sm.get('gold_error') or 'result_mismatch'}"
            )
            if sm.get("sql"):
                lines.append(f"  pred:\n  ```sql\n  {sm['sql'][:350]}\n  ```")
            if sm.get("gold_sql"):
                lines.append(f"  gold:\n  ```sql\n  {sm['gold_sql'][:350]}\n  ```")
        lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="召回 + SQL 联合评测")
    parser.add_argument("--golden", required=True)
    parser.add_argument("--ks", default="5,10,15,30")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--retrieval-style", choices=("xiyan", "legacy"), default="xiyan")
    parser.add_argument("--keyword-mode", choices=("auto", "llm", "rule"), default="rule")
    parser.add_argument(
        "--column-select",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--clarify-gate",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--with-result-acc",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="方案 A：末段双跑 gold/pred 并比对结果集（默认开）",
    )
    parser.add_argument("--force-llm-sql", action="store_true")
    parser.add_argument("--max-plan-retries", type=int, default=2)
    parser.add_argument("--max-sql-retries", type=int, default=2)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    ks = sorted({int(x.strip()) for x in args.ks.split(",") if x.strip()})
    golden_path = Path(args.golden)
    if not golden_path.is_absolute():
        golden_path = ROOT / golden_path
    items = load_golden(golden_path)
    if args.max_items and args.max_items > 0:
        items = items[: args.max_items]
    print(f"加载黄金集 {len(items)} 题：{golden_path}")

    import build_vector_index as bvi
    from db_config import create_qdrant_client, get_meta_mysql_engine, get_raw_database_name
    from wenshu.services.agent.sandbox import execute_readonly
    from wenshu.services.query_clarify import prepare_query
    from wenshu.services.schema_retrieval import retrieve_schema
    from wenshu.services.vector_search import resolve_search_db_names, search_collection

    if args.force_llm_sql:
        os.environ["AGENT_SQL_LLM"] = "1"

    bvi._load_dotenv()
    client = create_qdrant_client()
    collection = bvi.QDRANT_COLLECTION
    if not client.collection_exists(collection):
        raise SystemExit(f"集合不存在：{collection}")
    try:
        from qdrant_client.http.models import PayloadSchemaType

        for key in ("object_type", "db", "left_db", "right_db", "table"):
            try:
                client.create_payload_index(
                    collection_name=collection,
                    field_name=key,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass
    except Exception:
        pass

    meta_engine = get_meta_mysql_engine()
    filter_dbs, filter_mode = resolve_search_db_names(
        db_names=None,
        all_databases=False,
        default_raw_db=get_raw_database_name(),
    )
    info = client.get_collection(collection)
    vector_limit = max(args.limit, 50)
    print(
        f"collection={collection} points={info.points_count} filter={filter_mode} "
        f"dbs={filter_dbs} keyword={args.keyword_mode} column_select={args.column_select} "
        f"result_acc={args.with_result_acc}"
    )

    n_gold = 0
    for it in items:
        if not it.get("gold_sql"):
            g = build_gold_sql(it, meta_engine)
            if g:
                it["gold_sql"] = g
        if it.get("gold_sql"):
            n_gold += 1
    print(f"gold_sql 可用 {n_gold}/{len(items)}")

    questions = [it["question"] for it in items]
    evidences = [str(it.get("evidence") or "") for it in items]
    embed_inputs = [f"{q}\n{e}".strip() if e else q for q, e in zip(questions, evidences)]
    print("加载 embedding ...")
    vectors = bvi.embed(embed_inputs, is_query=True)
    print(f"已编码 {len(vectors)} 条，开始召回+SQL+ResultAcc ...")

    results: list[dict] = []
    t0 = time.perf_counter()
    for idx, (item, vector, evidence) in enumerate(zip(items, vectors, evidences), 1):
        print(f"  [{idx}/{len(items)}] {item['id']} {item['question'][:36]}", flush=True)
        prepared = prepare_query(
            item["question"],
            evidence,
            gate=args.clarify_gate,
            auto_clarify=args.clarify_gate,
            eval_item=item,
        )
        q_text = prepared.question
        ev_text = prepared.evidence
        if ev_text and ev_text != evidence:
            vector = bvi.embed([f"{q_text}\n{ev_text}"], is_query=True)[0]

        legacy_hits = search_collection(
            client, collection, vector, limit=args.limit, db_names=filter_dbs
        )
        retrieval = retrieve_schema(
            client=client,
            collection_name=collection,
            meta_engine=meta_engine,
            query_vector=vector,
            question=q_text,
            evidence=ev_text,
            db_names=filter_dbs,
            vector_limit=vector_limit,
            max_output_columns=args.limit,
            retrieval_style=args.retrieval_style,
            keyword_mode=args.keyword_mode,
            column_select=args.column_select,
        )
        row = eval_one_v2(
            item, retrieval, ks, legacy_hits=legacy_hits, fair_output_limit=args.limit
        )
        row["id"] = item["id"]
        row["question"] = item["question"]
        row["suite"] = item.get("suite")
        row["field_slice"] = item.get("field_slice")
        row["must_tables"] = item.get("must_tables")
        row["must_columns"] = item.get("must_columns")
        row["semantic_graph"] = prepared.semantic_graph
        row["query_keywords"] = retrieval.query_keywords
        row["keyword_source"] = retrieval.keyword_source

        sql_metrics: dict = {
            "sql_valid": False,
            "sql_struct_acc": False,
            "result_acc": None,
            "pred_exec_ok": None,
            "gold_exec_ok": None,
            "sql": "",
            "gold_sql": item.get("gold_sql"),
            "errors": [],
            "terminal": "",
            "plan_attempts": 0,
            "sql_attempts": 0,
            "plan_first_pass": None,
        }
        try:
            final = produce_final_sql(
                question=q_text,
                evidence=ev_text or "",
                semantic_graph=prepared.semantic_graph,
                retrieval=retrieval,
                meta_engine=meta_engine,
                dialect="mysql",
                max_plan_retries=args.max_plan_retries,
                max_sql_retries=args.max_sql_retries,
                force_llm_sql=args.force_llm_sql,
            )
            sql_metrics["terminal"] = final.terminal
            sql_metrics["plan_attempts"] = final.plan_attempts
            sql_metrics["sql_attempts"] = final.sql_attempts
            sql_metrics["plan_retry_count"] = final.plan_retry_count
            sql_metrics["sql_retry_count"] = final.sql_retry_count
            sql_metrics["plan_first_pass"] = final.plan_first_pass
            sql_metrics["source"] = final.source
            if final.plan is not None:
                sql_metrics["query_plan"] = final.plan.as_dict()

            if final.ok and final.sql:
                # 只对最终 SQL 计 StructAcc / Valid
                sql_metrics = {
                    **sql_metrics,
                    **score_sql(item, final.sql, used_tables=final.used_tables),
                    "source": final.source,
                    "terminal": final.terminal,
                    "plan_attempts": final.plan_attempts,
                    "sql_attempts": final.sql_attempts,
                    "plan_retry_count": final.plan_retry_count,
                    "sql_retry_count": final.sql_retry_count,
                    "plan_first_pass": final.plan_first_pass,
                }
                if final.plan is not None:
                    sql_metrics["query_plan"] = final.plan.as_dict()
            else:
                errs = list(final.plan_errors or []) + list(final.sql_errors or [])
                if final.blocked_reason:
                    errs = [f"blocked:{final.blocked_reason}", *errs]
                sql_metrics["errors"] = [f"{final.terminal}: {e}" for e in errs[:5]] or [
                    final.terminal or "no_final_sql"
                ]
                sql_metrics["sql"] = final.sql or ""
                sql_metrics["blocked_reason"] = final.blocked_reason
                # 无最终可评分 SQL：StructAcc/Valid 记 False（表示最终未产出合格 SQL）
                sql_metrics["sql_valid"] = False
                sql_metrics["sql_struct_acc"] = False

            # 方案 A：仅对最终 pred SQL 双跑
            if args.with_result_acc:
                ra = evaluate_result_accuracy(
                    pred_sql=sql_metrics.get("sql") if final.ok else None,
                    gold_sql=item.get("gold_sql") or sql_metrics.get("gold_sql"),
                    execute_fn=execute_readonly,
                )
                sql_metrics.update(ra)
                sql_metrics["sql_exec_ok"] = ra.get("pred_exec_ok")
        except Exception as exc:  # noqa: BLE001
            sql_metrics["errors"] = [f"sql_pipeline: {exc}"]
            sql_metrics["terminal"] = "pipeline_exception"

        row["sql_metrics"] = sql_metrics
        results.append(row)

    elapsed = round(time.perf_counter() - t0, 1)
    summary = aggregate(results, ks)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else ROOT / "evals" / "reports" / f"sql_eval_{stamp}.md"
    if not out.is_absolute():
        out = ROOT / out
    if out.suffix.lower() not in {".md", ".markdown"}:
        out = out.with_suffix(".md")
    out.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "golden": str(golden_path.relative_to(ROOT)).replace("\\", "/"),
        "n": len(results),
        "ks": ks,
        "limit": args.limit,
        "retrieval_style": args.retrieval_style,
        "keyword_mode": args.keyword_mode,
        "column_select": args.column_select,
        "with_result_acc": args.with_result_acc,
        "gold_sql_available": n_gold,
        "force_llm_sql": args.force_llm_sql,
        "max_plan_retries": args.max_plan_retries,
        "max_sql_retries": args.max_sql_retries,
        "eval_mode": "final_sql_after_retries",
        "elapsed_sec": elapsed,
        "points": info.points_count,
        "filter_mode": filter_mode,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    md = render_report(summary=summary, results=results, ks=ks, meta=meta)
    body = md if isinstance(md, str) else "\n".join(md)
    full = body.rstrip() + "\n" + "\n".join(render_sql_section(results)) + "\n"
    out.write_text(full, encoding="utf-8")
    json_out = out.with_suffix(".json")
    json_out.write_text(
        json.dumps({"meta": meta, "summary": summary, "rows": results}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    def rate_at10(field: str) -> str:
        vals = [
            r["metrics"].get("@10", {}).get(field)
            for r in results
            if r.get("metrics", {}).get("@10", {}).get(field) is not None
        ]
        return _pct(vals)

    def sel(stage: str, field: str) -> str:
        vals = [
            r.get("selection", {}).get(stage, {}).get(field)
            for r in results
            if r.get("selection", {}).get(stage, {}).get(field) is not None
        ]
        return _pct(vals)

    sqls = [r.get("sql_metrics") or {} for r in results]
    print("\n======== 评测摘要 ========")
    print(f"题数={len(results)} 耗时={elapsed}s gold_sql={n_gold}/{len(results)}")
    print(f"Table Hit@10 = {rate_at10('table_hit')}")
    print(f"Column Hit@10 = {rate_at10('column_hit')}")
    print(f"S1 Table={sel('s1','table_hit')}  S1 Column={sel('s1','column_hit')}")
    print(f"S2 Table={sel('s2','table_hit')}  S2 Column={sel('s2','column_hit')}")
    print(
        f"StructAcc={_pct([s.get('sql_struct_acc') for s in sqls])}  "
        f"PredExecOK={_pct([s.get('pred_exec_ok') for s in sqls])}  "
        f"GoldExecOK={_pct([s.get('gold_exec_ok') for s in sqls])}  "
        f"ResultAcc={_pct([s.get('result_acc') for s in sqls])}"
    )
    first_pass = [s.get("plan_first_pass") for s in sqls if s.get("plan_attempts")]
    print(
        f"过程: 首轮plan通过={_pct(first_pass)}  "
        f"plan耗尽={sum(1 for s in sqls if s.get('terminal')=='plan_exhausted')}  "
        f"SQL耗尽={sum(1 for s in sqls if s.get('terminal') in {'sql_exhausted','generate_error'})}"
    )
    print(f"报告: {out}")
    print(f"JSON: {json_out}")


if __name__ == "__main__":
    main()
