#!/usr/bin/env python3
"""
召回评测：对黄金集跑向量检索，输出 Hit@K / MRR / 失败清单。

用法（在仓库根目录）:
  python evals/scripts/run_recall_eval.py
  python evals/scripts/run_recall_eval.py --ks 5,10,15,30 --limit 30
  python evals/scripts/run_recall_eval.py --all-databases
  python evals/scripts/run_recall_eval.py --golden evals/golden/recall_v1.jsonl

下午速通默认会写报告到 evals/reports/。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))


def load_golden(path: Path) -> list[dict]:
    items = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"黄金集 JSON 无效 L{line_no}: {exc}") from exc
        if "id" not in obj or "question" not in obj:
            raise SystemExit(f"黄金集 L{line_no} 缺少 id/question")
        obj.setdefault("must_tables", [])
        obj.setdefault("nice_tables", [])
        obj.setdefault("must_columns", [])
        obj.setdefault("forbidden_tables", [])
        obj.setdefault("tags", [])
        obj.setdefault("negative", False)
        obj.setdefault("suite", _infer_suite(obj))
        items.append(obj)
    return items


def _infer_suite(obj: dict) -> str:
    if obj.get("negative"):
        return "negative"
    if obj.get("suite") in ("single", "multi"):
        return obj["suite"]
    must = obj.get("must_tables") or []
    if len(must) <= 1:
        return "single"
    return "multi"


def _norm(name: str | None) -> str:
    """表/字段名统一小写比较（向量库 payload 多为小写）。"""
    return (name or "").strip().lower()


def extract_hit_tables_columns(hits: list) -> tuple[list[str], list[tuple[str, str]], list[dict]]:
    tables: list[str] = []
    columns: list[tuple[str, str]] = []
    compact: list[dict] = []
    seen_t: set[str] = set()
    seen_c: set[tuple[str, str]] = set()

    for h in hits:
        payload = getattr(h, "payload", None) or {}
        score = round(float(getattr(h, "score", 0.0) or 0.0), 4)
        obj_type = payload.get("object_type")
        table_raw = payload.get("table") or ""
        column_raw = payload.get("column") or ""
        table = _norm(table_raw)
        column = _norm(column_raw)
        compact.append(
            {
                "score": score,
                "type": obj_type,
                "table": table_raw,
                "column": column_raw,
                "table_norm": table,
                "column_norm": column,
                "db": payload.get("db"),
            }
        )
        if table and table not in seen_t:
            seen_t.add(table)
            tables.append(table)
        if table and column:
            key = (table, column)
            if key not in seen_c:
                seen_c.add(key)
                columns.append(key)
    return tables, columns, compact


def first_rank(ordered_tables: list[str], must_tables: list[str]) -> int | None:
    must = {_norm(t) for t in must_tables}
    for i, t in enumerate(ordered_tables, 1):
        if _norm(t) in must:
            return i
    return None


def _tables_from_top_columns(columns: list, k: int) -> list[str]:
    """按列分数顺序去重得到表序（用于 Top-K 公平表召回）。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for ch in columns[:k]:
        t = _norm(getattr(ch, "table", None))
        if t and t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _count_raw_columns_in_topk(hits: list, k: int) -> int:
    n = 0
    for h in hits[:k]:
        payload = getattr(h, "payload", None) or {}
        if payload.get("object_type") == "column" and payload.get("table") and payload.get("column"):
            n += 1
    return n


def _selection_set_metrics(
    cols: list,
    *,
    must_tables: list[str],
    must_cols: list[tuple[str, str]],
    forbidden: set[str],
    is_neg: bool,
) -> dict:
    """S1/S2 全集 Hit（不是 Top-K 槽位）。"""
    col_set = {c.key for c in cols} if cols else set()
    table_set: set[str] = set()
    tables: list[str] = []
    for c in cols or []:
        t = _norm(getattr(c, "table", None))
        if t and t not in table_set:
            table_set.add(t)
            tables.append(getattr(c, "table", t))
    table_hit = (not must_tables) or set(must_tables).issubset(table_set)
    any_table = (not must_tables) or bool(set(must_tables) & table_set)
    col_hit = (not must_cols) or set(must_cols).issubset(col_set)
    forbidden_hit = bool(forbidden & table_set)
    return {
        "table_hit": None if is_neg else table_hit,
        "any_table_hit": None if is_neg else any_table,
        "column_hit": None if is_neg else col_hit,
        "forbidden_hit": forbidden_hit,
        "column_count": len(cols or []),
        "tables": tables,
    }


def eval_one_v2(
    item: dict,
    retrieval,
    ks: list[int],
    *,
    legacy_hits: list,
    fair_output_limit: int | None = None,
) -> dict:
    """V2 检索评测：主指标与 Legacy 对齐为 Top-K 字段槽位；扩展指标单独记录。"""
    legacy = eval_one(item, legacy_hits, ks)
    must_tables = [_norm(t) for t in (item.get("must_tables") or [])]
    must_cols = [
        (_norm(c["table"]), _norm(c["column"]))
        for c in (item.get("must_columns") or [])
        if c.get("table") and c.get("column")
    ]
    forbidden = {_norm(t) for t in (item.get("forbidden_tables") or [])}
    is_neg = bool(item.get("negative"))

    expanded_set = retrieval.expanded_table_set
    selected_set = {_norm(t) for t in retrieval.selected_tables}
    all_col_set = retrieval.selected_columns
    # 保留 retrieve_schema 输出顺序（含单表多字段覆盖选列），勿再按分数重排
    ranked_cols = list(retrieval.columns)
    ordered_cols = [c.key for c in ranked_cols]
    ordered_tables_fair = _tables_from_top_columns(ranked_cols, max(ks) if ks else 15)
    expanded_tables_ordered = list(getattr(retrieval, "expanded_tables", []) or [])
    preview = [c.as_dict() for c in ranked_cols[:15]]
    output_column_count = len(ranked_cols)

    result = {
        **legacy,
        "retrieval_mode": "v2",
        "selected_tables": retrieval.selected_tables,
        "expanded_tables": expanded_tables_ordered,
        "output_column_count": output_column_count,
        "fair_output_limit": fair_output_limit,
        "top_tables": ordered_tables_fair[:15],
        "hits_preview": preview,
        "first_hit_rank": first_rank(expanded_tables_ordered, must_tables) if must_tables else None,
        "slot_first_hit_rank": first_rank(ordered_tables_fair, must_tables) if must_tables else None,
        "selection": {
            "s1": _selection_set_metrics(
                getattr(retrieval, "s1_columns", None) or [],
                must_tables=must_tables,
                must_cols=must_cols,
                forbidden=forbidden,
                is_neg=is_neg,
            ),
            "s2": _selection_set_metrics(
                getattr(retrieval, "s2_columns", None) or [],
                must_tables=must_tables,
                must_cols=must_cols,
                forbidden=forbidden,
                is_neg=is_neg,
            ),
            "meta": dict(getattr(retrieval, "selection_meta", None) or {}),
            "s1_preview": [c.as_dict() for c in (getattr(retrieval, "s1_columns", None) or [])[:20]],
            "s2_preview": [c.as_dict() for c in (getattr(retrieval, "s2_columns", None) or [])[:20]],
        },
    }

    for k in ks:
        legacy_m = legacy["metrics"][f"@{k}"]
        top_col_k = set(ordered_cols[:k])
        top_t_fair = {_norm(c.table) for c in ranked_cols[:k]}
        top_tables_fair_k = _tables_from_top_columns(ranked_cols, k)

        expanded_hit = (not must_tables) or set(must_tables).issubset(expanded_set)
        expanded_any = (not must_tables) or bool(set(must_tables) & expanded_set)
        selected_hit = (not must_tables) or set(must_tables).issubset(selected_set)
        supplemented_col = (not must_cols) or set(must_cols).issubset(all_col_set)
        topk_col = (not must_cols) or set(must_cols).issubset(top_col_k)
        slot_table_hit = (not must_tables) or set(must_tables).issubset(top_t_fair)
        any_table_slot = (not must_tables) or bool(set(must_tables) & top_t_fair)
        forbidden_fair = bool(forbidden & top_t_fair)
        forbidden_exp = bool(forbidden & expanded_set)

        result["metrics"][f"@{k}"] = {
            # 主指标（解耦）：Table=expanded 池；Column=Top-K 列槽
            "table_hit": expanded_hit if not is_neg else None,
            "any_table_hit": expanded_any if not is_neg else None,
            "column_hit": topk_col if not is_neg else None,
            "forbidden_hit": forbidden_fair,
            "top_tables": top_tables_fair_k,
            "v2_column_count": min(k, output_column_count),
            "legacy_column_count": _count_raw_columns_in_topk(legacy_hits, k),
            # 对照：Top-K 列槽反推表（旧口径）
            "slot_table_hit": slot_table_hit if not is_neg else None,
            "slot_any_table_hit": any_table_slot if not is_neg else None,
            "raw_table_hit": legacy_m.get("table_hit"),
            "raw_column_hit": legacy_m.get("column_hit"),
            "raw_forbidden_hit": legacy_m.get("forbidden_hit"),
            # 附录：V2 扩展能力
            "expanded_table_hit": expanded_hit if not is_neg else None,
            "supplemented_column_hit": supplemented_col if not is_neg else None,
            "selected_table_hit": selected_hit if not is_neg else None,
        }

    return result


def eval_one(item: dict, hits: list, ks: list[int]) -> dict:
    ordered_tables, ordered_cols, compact = extract_hit_tables_columns(hits)
    must_tables = [_norm(t) for t in (item.get("must_tables") or [])]
    must_cols = [
        (_norm(c["table"]), _norm(c["column"]))
        for c in (item.get("must_columns") or [])
        if c.get("table") and c.get("column")
    ]
    forbidden = {_norm(t) for t in (item.get("forbidden_tables") or [])}
    is_neg = bool(item.get("negative"))

    result = {
        "id": item["id"],
        "suite": item.get("suite") or _infer_suite(item),
        "question": item["question"],
        "tags": item.get("tags") or [],
        "negative": is_neg,
        "must_tables": must_tables,
        "must_columns": [f"{t}.{c}" for t, c in must_cols],
        "forbidden_tables": sorted(forbidden),
        "top_tables": ordered_tables[:15],
        "hits_preview": compact[:15],
        "first_hit_rank": first_rank(ordered_tables, must_tables) if must_tables else None,
        "metrics": {},
    }

    for k in ks:
        top_c: set[tuple[str, str]] = set()
        top_t: set[str] = set()
        for row in compact[:k]:
            if row.get("table_norm"):
                top_t.add(row["table_norm"])
            if row.get("table_norm") and row.get("column_norm"):
                top_c.add((row["table_norm"], row["column_norm"]))

        table_hit = (not must_tables) or set(must_tables).issubset(top_t)
        any_table = (not must_tables) or bool(set(must_tables) & top_t)
        col_hit = (not must_cols) or set(must_cols).issubset(top_c)
        forbidden_hit = bool(forbidden & top_t)

        result["metrics"][f"@{k}"] = {
            "table_hit": table_hit if not is_neg else None,
            "any_table_hit": any_table if not is_neg else None,
            "column_hit": col_hit if not is_neg else None,
            "forbidden_hit": forbidden_hit,
            "top_tables": sorted(top_t),
        }

    return result


def _summarize_subset(rows: list[dict], ks: list[int]) -> dict:
    out: dict = {"n": len(rows), "by_k": {}, "mrr": None}
    if not rows:
        return out
    for k in ks:
        key = f"@{k}"
        th = [r["metrics"][key]["table_hit"] for r in rows if r["metrics"][key]["table_hit"] is not None]
        ah = [r["metrics"][key]["any_table_hit"] for r in rows if r["metrics"][key]["any_table_hit"] is not None]
        ch = [r["metrics"][key]["column_hit"] for r in rows if r["metrics"][key]["column_hit"] is not None]
        fh = [r["metrics"][key]["forbidden_hit"] for r in rows]
        out["by_k"][key] = {
            "table_hit": _rate(th),
            "any_table_hit": _rate(ah),
            "column_hit": _rate(ch),
            "forbidden_rate": _rate(fh),
        }
    ranks = [r["first_hit_rank"] for r in rows if r["must_tables"]]
    mrr_vals = [1.0 / r for r in ranks if r]
    out["mrr"] = round(sum(mrr_vals) / len(ranks), 4) if ranks else None
    return out


def aggregate(results: list[dict], ks: list[int]) -> dict:
    positives = [r for r in results if not r["negative"]]
    negatives = [r for r in results if r["negative"]]
    singles = [r for r in positives if r.get("suite") == "single"]
    multis = [r for r in positives if r.get("suite") == "multi"]

    summary: dict = {
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "n_single": len(singles),
        "n_multi": len(multis),
        "by_k": {},
        "by_suite": {
            "single": _summarize_subset(singles, ks),
            "multi": _summarize_subset(multis, ks),
            "all": _summarize_subset(positives, ks),
        },
        "by_tag": {},
    }

    summary["by_k"] = summary["by_suite"]["all"]["by_k"]
    summary["mrr"] = summary["by_suite"]["all"]["mrr"]

    # 按 tag 切片（正例）
    tag_buckets: dict[str, list[dict]] = defaultdict(list)
    for r in positives:
        tags = r["tags"] or ["未标注"]
        for t in tags:
            tag_buckets[t].append(r)
        tag_buckets["全部正例"].append(r)

    primary_k = 10 if 10 in ks else ks[0]
    pk = f"@{primary_k}"
    for tag, rows in sorted(tag_buckets.items(), key=lambda x: (0 if x[0] == "全部正例" else 1, x[0])):
        th = [r["metrics"][pk]["table_hit"] for r in rows]
        summary["by_tag"][tag] = {
            "n": len(rows),
            f"table_hit{pk}": _rate(th),
            f"forbidden{pk}": _rate([r["metrics"][pk]["forbidden_hit"] for r in rows]),
        }

    return summary


def _rate(flags: list) -> float | None:
    if not flags:
        return None
    return round(sum(1 for x in flags if x) / len(flags), 4)


def render_report(
    *,
    summary: dict,
    results: list[dict],
    ks: list[int],
    meta: dict,
) -> str:
    lines: list[str] = []
    lines.append("# 召回评测报告")
    lines.append("")
    lines.append(f"- 时间：{meta.get('time')}")
    lines.append(f"- 黄金集：`{meta.get('golden')}`")
    lines.append(f"- collection：`{meta.get('collection')}` / points={meta.get('points_count')}")
    lines.append(f"- 库范围：{meta.get('filter_mode')} {meta.get('filter_databases')}")
    lines.append(f"- 检索模式：**{meta.get('retrieval_mode', 'legacy')}**")
    lines.append(f"- 检索 limit：{meta.get('limit')}；报告 K：{ks}")
    if meta.get("retrieval_mode") == "v2":
        lines.append(
            f"- **公平对比口径**：V2 最终候选字段 capped 至 **{meta.get('fair_output_limit')}** 列；"
            f"**Table Hit = expanded_tables 定表**，**Column Hit = Top-K 列槽**（与旧「列槽反推表」解耦）"
        )
    lines.append(
        f"- 题量：单表 {summary.get('n_single', '—')} / 多表 {summary.get('n_multi', '—')} "
        f"/ 合计正例 {summary['n_positive']}"
    )
    lines.append(f"- Table MRR（全部）：{summary.get('mrr')}")
    lines.append("")
    lines.append("## 单表 vs 多表（核心对比 @10）")
    lines.append("")
    lines.append("| 分区 | 题数 | Table Hit@10 | Any-Table@10 | Column Hit@10 | Forbidden@10 | MRR |")
    lines.append("|------|------|--------------|--------------|---------------|--------------|-----|")
    for label, key in [("单表", "single"), ("多表", "multi"), ("合计", "all")]:
        block = summary.get("by_suite", {}).get(key, {})
        n = block.get("n", 0)
        m = block.get("by_k", {}).get("@10", {})
        lines.append(
            f"| {label} | {n} | {_pct(m.get('table_hit'))} | {_pct(m.get('any_table_hit'))} | "
            f"{_pct(m.get('column_hit'))} | {_pct(m.get('forbidden_rate'))} | {block.get('mrr', '—')} |"
        )
    lines.append("")
    lines.append("## 单表 · 单字段 vs 多字段（@10）")
    lines.append("")
    lines.append("| 分区 | 题数 | Table Hit@10 | Column Hit@10 | Forbidden@10 |")
    lines.append("|------|------|--------------|---------------|--------------|")
    singles = [r for r in results if not r.get("negative") and r.get("suite") == "single"]
    for label, tag in [("单表单字段", "单表单字段"), ("单表多字段", "单表多字段")]:
        rows = [r for r in singles if tag in (r.get("tags") or [])]
        n = len(rows)
        if not n:
            lines.append(f"| {label} | 0 | — | — | — |")
            continue
        th = _rate([r["metrics"]["@10"]["table_hit"] for r in rows])
        ch = _rate([r["metrics"]["@10"]["column_hit"] for r in rows])
        fh = _rate([r["metrics"]["@10"]["forbidden_hit"] for r in rows])
        lines.append(f"| {label} | {n} | {_pct(th)} | {_pct(ch)} | {_pct(fh)} |")
    lines.append("")
    lines.append("## 全部正例 · 主指标")
    lines.append("")
    if meta.get("retrieval_mode") == "v2":
        lines.append(
            "（V2 主指标：Table=expanded_tables 定表，Column=Top-K 列槽；"
            "slot_table_hit 为旧「前 K 列反推表」对照，见文末附录）"
        )
        lines.append("")
    lines.append("| K | Table Hit | Any-Table Hit | Column Hit | Forbidden@K |")
    lines.append("|---|-----------|---------------|------------|-------------|")
    for k in ks:
        m = summary["by_k"][f"@{k}"]
        lines.append(
            f"| @{k} | {_pct(m['table_hit'])} | {_pct(m['any_table_hit'])} | "
            f"{_pct(m['column_hit'])} | {_pct(m['forbidden_rate'])} |"
        )
    lines.append("")
    lines.append("## 按题型切片（主看 Table Hit@10）")
    lines.append("")
    lines.append("| 题型 | 题数 | Table Hit@10 | Forbidden@10 |")
    lines.append("|------|------|--------------|--------------|")
    for tag, row in summary["by_tag"].items():
        # find keys dynamically
        th = next((v for kk, v in row.items() if kk.startswith("table_hit")), None)
        fh = next((v for kk, v in row.items() if kk.startswith("forbidden")), None)
        lines.append(f"| {tag} | {row['n']} | {_pct(th)} | {_pct(fh)} |")
    lines.append("")

    if meta.get("retrieval_mode") == "v2":
        pos = [r for r in results if not r.get("negative")]
        avg_raw_cols = _avg_metric(pos, "@10", "legacy_column_count")
        avg_v2_cols = _avg_metric(pos, "@10", "v2_column_count")
        avg_out = round(
            sum(r.get("output_column_count", 0) for r in pos) / len(pos), 1
        ) if pos else None

        lines.append("## Legacy vs V2 公平对比（同 Top-K 字段槽位）")
        lines.append("")
        lines.append(
            f"- V2 输出池上限：**{meta.get('fair_output_limit')}** 列（实测均值 **{avg_out}** 列）"
        )
        lines.append(
            f"- @10 槽位内实际 column 条数：Legacy 均值 **{avg_raw_cols}** / V2 均值 **{avg_v2_cols}**"
        )
        lines.append("")
        lines.append("| K | 分区 | 题数 | Legacy Table | V2 Table | Legacy Column | V2 Column | Δ Column |")
        lines.append("|---|------|------|--------------|----------|---------------|-----------|----------|")
        for k in [x for x in ks if x in (10, 30)] or [ks[-1]]:
            for label, pred in [
                ("单表", lambda r: r.get("suite") == "single"),
                ("多表", lambda r: r.get("suite") == "multi"),
                ("合计", lambda r: True),
            ]:
                rows = [r for r in pos if pred(r)]
                if not rows:
                    continue
                rt = _rate([r["metrics"][f"@{k}"]["raw_table_hit"] for r in rows if r["metrics"][f"@{k}"].get("raw_table_hit") is not None])
                vt = _rate([r["metrics"][f"@{k}"]["table_hit"] for r in rows if r["metrics"][f"@{k}"].get("table_hit") is not None])
                rc = _rate([r["metrics"][f"@{k}"]["raw_column_hit"] for r in rows if r["metrics"][f"@{k}"].get("raw_column_hit") is not None])
                vc = _rate([r["metrics"][f"@{k}"]["column_hit"] for r in rows if r["metrics"][f"@{k}"].get("column_hit") is not None])
                delta = None if rc is None or vc is None else round((vc - rc) * 100, 1)
                delta_s = f"{delta:+.1f}pp" if delta is not None else "—"
                lines.append(
                    f"| @{k} | {label} | {len(rows)} | {_pct(rt)} | {_pct(vt)} | "
                    f"{_pct(rc)} | {_pct(vc)} | {delta_s} |"
                )
        lines.append("")
        lines.append("## 附录 · V2 对照（列槽反推表 vs expanded 定表）")
        lines.append("")
        lines.append("| K | Expanded Table | Slot Table | Supplemented Column | Selected Table |")
        lines.append("|---|----------------|------------|---------------------|----------------|")
        for k in [10] if 10 in ks else [ks[0]]:
            et = _rate([r["metrics"][f"@{k}"]["expanded_table_hit"] for r in pos if r["metrics"][f"@{k}"].get("expanded_table_hit") is not None])
            st_slot = _rate([r["metrics"][f"@{k}"]["slot_table_hit"] for r in pos if r["metrics"][f"@{k}"].get("slot_table_hit") is not None])
            sc = _rate([r["metrics"][f"@{k}"]["supplemented_column_hit"] for r in pos if r["metrics"][f"@{k}"].get("supplemented_column_hit") is not None])
            st = _rate([r["metrics"][f"@{k}"]["selected_table_hit"] for r in pos if r["metrics"][f"@{k}"].get("selected_table_hit") is not None])
            lines.append(f"| @{k} | {_pct(et)} | {_pct(st_slot)} | {_pct(sc)} | {_pct(st)} |")
        lines.append("")

    pos_sel = [r for r in results if not r.get("negative") and r.get("selection")]
    if pos_sel and any((r.get("selection") or {}).get("meta", {}).get("enabled") for r in pos_sel):
        lines.append("## S1 / S2 精选（全集 Hit，非 Top-K 槽位）")
        lines.append("")
        lines.append(
            "S1/S2 是 LLM 从检索池 S_rtrv 中选出的精选集合，列数不固定；"
            "**不要**与历史 Hit@10 做同字段数对比。第 2 轮强制补全，空结果会启发式补列。"
        )
        lines.append("")
        lines.append(
            "| 分区 | 题数 | S1 Table | S1 Column | S1 均列数 | S2 Table | S2 Column | S2 均列数 |"
        )
        lines.append("|------|------|----------|-----------|-----------|----------|-----------|-----------|")
        slices = [
            ("单表单字段", lambda r: r.get("suite") == "single" and "单表单字段" in (r.get("tags") or [])),
            ("单表多字段", lambda r: r.get("suite") == "single" and "单表多字段" in (r.get("tags") or [])),
            ("单表", lambda r: r.get("suite") == "single"),
            ("多表", lambda r: r.get("suite") == "multi"),
            ("合计", lambda r: True),
        ]
        for label, pred in slices:
            rows = [r for r in pos_sel if pred(r)]
            if not rows:
                continue
            lines.append(
                f"| {label} | {len(rows)} | "
                f"{_pct(_sel_rate(rows, 's1', 'table_hit'))} | "
                f"{_pct(_sel_rate(rows, 's1', 'column_hit'))} | "
                f"{_sel_avg_cols(rows, 's1')} | "
                f"{_pct(_sel_rate(rows, 's2', 'table_hit'))} | "
                f"{_pct(_sel_rate(rows, 's2', 'column_hit'))} | "
                f"{_sel_avg_cols(rows, 's2')} |"
            )
        src1 = {}
        src2 = {}
        for r in pos_sel:
            meta = (r.get("selection") or {}).get("meta") or {}
            s1s = meta.get("s1_source") or "unknown"
            s2s = meta.get("s2_source") or "unknown"
            src1[s1s] = src1.get(s1s, 0) + 1
            src2[s2s] = src2.get(s2s, 0) + 1
        lines.append("")
        lines.append(
            f"- S1 来源：{', '.join(f'{k}={v}' for k, v in sorted(src1.items()))}"
        )
        lines.append(
            f"- S2 来源：{', '.join(f'{k}={v}' for k, v in sorted(src2.items()))}"
        )
        lines.append("")

    # MVP 判定
    t10 = summary["by_k"].get("@10", {}).get("table_hit")
    lines.append("## MVP 判定")
    lines.append("")
    if t10 is None:
        lines.append("- 无 @10 结果")
    elif t10 >= 0.8:
        lines.append(f"- **达标**：Table Hit@10 = {_pct(t10)} ≥ 80%")
    else:
        lines.append(f"- **未达标**：Table Hit@10 = {_pct(t10)} < 80% → 先补 description/同义词，再复测")
    lines.append("")

    fails = [
        r
        for r in results
        if not r["negative"] and r["metrics"].get("@10", {}).get("table_hit") is False
    ]
    fail_single = [r for r in fails if r.get("suite") == "single"]
    fail_multi = [r for r in fails if r.get("suite") == "multi"]
    lines.append(
        f"## 失败题（Table Hit@10 未过：单表 {len(fail_single)} / 多表 {len(fail_multi)} / 共 {len(fails)}）"
    )
    lines.append("")
    if not fails:
        lines.append("无")
    else:
        for r in fails:
            lines.append(f"### {r['id']} [{r.get('suite', '?')}] · {r['question']}")
            lines.append(f"- must_tables：{r['must_tables']}")
            if r.get("expanded_tables"):
                lines.append(f"- expanded_tables：{r['expanded_tables'][:10]}")
            lines.append(f"- first_hit_rank（expanded）：{r['first_hit_rank']}")
            if r.get("slot_first_hit_rank") is not None:
                lines.append(f"- first_hit_rank（列槽）：{r['slot_first_hit_rank']}")
            lines.append(f"- top_tables（列槽@10）：{r['top_tables'][:10]}")
            preview = ", ".join(
                f"{h['type']}:{h['table']}.{h['column']}({h['score']})" for h in r["hits_preview"][:8]
            )
            lines.append(f"- hits：{preview}")
            lines.append(f"- 建议标签：`DESC_WEAK` / `SYNONYM` / `CONFUSION` / `MULTI_HOP` / `INDEX_GAP`")
            lines.append("")

    col_fails = [
        r
        for r in results
        if not r["negative"]
        and r["metrics"].get("@10", {}).get("table_hit") is True
        and r["metrics"].get("@10", {}).get("column_hit") is False
    ]
    lines.append(f"## 表过但字段未齐（Column Hit@10 未过，共 {len(col_fails)}）")
    lines.append("")
    if not col_fails:
        lines.append("无")
    else:
        for r in col_fails:
            lines.append(f"- **{r['id']}** {r['question']} → 期望字段：{r['must_columns']}")
        lines.append("")

    forb = [
        r
        for r in results
        if not r["negative"] and r["metrics"].get("@10", {}).get("forbidden_hit")
    ]
    lines.append(f"## 易混淆污染（Forbidden@10，共 {len(forb)}）")
    lines.append("")
    if not forb:
        lines.append("无")
    else:
        for r in forb:
            lines.append(
                f"- **{r['id']}** {r['question']} → forbidden={r['forbidden_tables']}；"
                f"top={r['top_tables'][:6]}"
            )
        lines.append("")

    negs = [r for r in results if r["negative"]]
    if negs:
        lines.append("## 负例观察")
        lines.append("")
        for r in negs:
            top = ", ".join(r["top_tables"][:5]) or "(空)"
            scores = [h["score"] for h in r["hits_preview"][:3]]
            lines.append(f"- {r['id']} top表：{top}；top分数：{scores}")
        lines.append("")

    lines.append("## 下一步（今天下午）")
    lines.append("")
    lines.append("1. 给失败题打标签（手册 §3.3）")
    lines.append("2. 优先改 3～5 张核心表的 L1 description，增量同步向量")
    lines.append("3. 再跑一次本脚本对比 Table@10")
    lines.append("")
    return "\n".join(lines)


def _pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.1f}%"


def _avg_metric(rows: list[dict], k_key: str, field: str) -> str:
    vals = [r["metrics"][k_key].get(field) for r in rows if r["metrics"].get(k_key, {}).get(field) is not None]
    if not vals:
        return "—"
    return f"{sum(vals) / len(vals):.1f}"


def _sel_rate(rows: list[dict], stage: str, field: str) -> float | None:
    vals = [
        r.get("selection", {}).get(stage, {}).get(field)
        for r in rows
        if r.get("selection", {}).get(stage, {}).get(field) is not None
    ]
    return _rate(vals)


def _sel_avg_cols(rows: list[dict], stage: str) -> str:
    vals = [
        r.get("selection", {}).get(stage, {}).get("column_count")
        for r in rows
        if r.get("selection", {}).get(stage, {}).get("column_count") is not None
    ]
    if not vals:
        return "—"
    return f"{sum(vals) / len(vals):.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="问数 Schema 召回评测")
    parser.add_argument(
        "--golden",
        default=str(ROOT / "evals" / "golden" / "recall_demo.jsonl"),
        help="黄金集 JSONL 路径",
    )
    parser.add_argument("--ks", default="5,10,15,30", help="评估 K 列表")
    parser.add_argument("--limit", type=int, default=30, help="每次检索返回条数（应 ≥ max K）")
    parser.add_argument("--all-databases", action="store_true", help="跨全部已索引库")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="使用旧版 table+column 混排检索（不走 retrieve_schema）",
    )
    parser.add_argument(
        "--retrieval-style",
        choices=("xiyan", "legacy"),
        default="xiyan",
        help="retrieve_schema 检索风格：xiyan=关键词多路（默认）；legacy=整句单向量",
    )
    parser.add_argument(
        "--keyword-mode",
        choices=("auto", "llm", "rule"),
        default="auto",
        help="关键词抽取：auto=LLM 优先并回退规则（默认）；llm；rule",
    )
    parser.add_argument(
        "--column-select",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="对检索池做 LLM 两轮选列 S1/S2（默认开）；--no-column-select 关闭",
    )
    parser.add_argument(
        "--clarify-gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="启用语义图澄清闸门（默认开）；评测时自动补答后继续检索",
    )
    parser.add_argument(
        "--out",
        default="",
        help="报告输出路径；默认 evals/reports/recall_E0_时间戳.md",
    )
    args = parser.parse_args()

    ks = sorted({int(x.strip()) for x in args.ks.split(",") if x.strip()})
    if not ks:
        raise SystemExit("--ks 无效")
    if args.limit < max(ks):
        raise SystemExit(f"--limit={args.limit} 必须 ≥ max(ks)={max(ks)}")

    golden_path = Path(args.golden)
    if not golden_path.is_absolute():
        golden_path = ROOT / golden_path
    items = load_golden(golden_path)
    print(f"加载黄金集 {len(items)} 题：{golden_path}")

    import build_vector_index as bvi
    from db_config import create_qdrant_client, get_meta_mysql_engine, get_raw_database_name
    from wenshu.services.query_clarify import prepare_query
    from wenshu.services.schema_retrieval import retrieve_schema
    from wenshu.services.vector_search import resolve_search_db_names, search_collection

    bvi._load_dotenv()
    client = create_qdrant_client()
    collection = bvi.QDRANT_COLLECTION
    if not client.collection_exists(collection):
        raise SystemExit(f"集合不存在：{collection}，请先构建向量索引")

    # 库过滤依赖 payload index；缺失时自动补建，避免 400
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

    info = client.get_collection(collection)
    filter_dbs, filter_mode = resolve_search_db_names(
        db_names=None,
        all_databases=args.all_databases,
        default_raw_db=get_raw_database_name(),
    )
    print(
        f"collection={collection} points={info.points_count} "
        f"filter={filter_mode} dbs={filter_dbs} limit={args.limit}"
    )
    print("加载 embedding 模型（首次可能较慢）...")

    retrieval_mode = "legacy" if args.legacy else args.retrieval_style
    meta_engine = get_meta_mysql_engine()
    vector_limit = max(args.limit, 50) if not args.legacy else args.limit
    print(
        f"retrieval_mode={retrieval_mode} keyword_mode={args.keyword_mode} "
        f"column_select={args.column_select} clarify_gate={args.clarify_gate} vector_limit={vector_limit}"
    )

    clarify_stats = {"triggered": 0, "auto_answered": 0, "rounds_total": 0}

    questions = [it["question"] for it in items]
    evidences = [str(it.get("evidence") or "") for it in items]
    embed_inputs = [
        f"{q}\n{e}".strip() if e else q for q, e in zip(questions, evidences)
    ]
    # 批量 embed 更快（Q||E 用于表级权重向量）
    vectors = bvi.embed(embed_inputs, is_query=True)
    print(f"已编码 {len(vectors)} 条 query，开始检索...")

    results = []
    for item, vector, evidence in zip(items, vectors, evidences):
        print(f"  ... {item['id']} {item['question'][:40]}", flush=True)
        prepared = prepare_query(
            item["question"],
            evidence,
            gate=args.clarify_gate,
            auto_clarify=args.clarify_gate,
            eval_item=item,
        )
        if prepared.clarify_rounds:
            clarify_stats["auto_answered"] += 1
            clarify_stats["rounds_total"] += prepared.clarify_rounds
        elif prepared.status == "need_clarify":
            clarify_stats["triggered"] += 1
            raise SystemExit(
                f"题 {item['id']} 需澄清但未自动补答：{prepared.clarify_questions}"
            )
        q_text = prepared.question
        ev_text = prepared.evidence
        if ev_text:
            embed_text = f"{q_text}\n{ev_text}"
            vector = bvi.embed([embed_text], is_query=True)[0]
        if not args.legacy:
            legacy_hits = search_collection(
                client,
                collection,
                vector,
                limit=args.limit,
                db_names=filter_dbs,
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
            row["retrieval_mode"] = retrieval.retrieval_style
            row["query_keywords"] = retrieval.query_keywords
            row["query_roles"] = retrieval.query_roles
            row["keyword_source"] = retrieval.keyword_source
            row["semantic_graph"] = prepared.semantic_graph
            row["clarify_rounds"] = prepared.clarify_rounds
        else:
            hits = search_collection(
                client,
                collection,
                vector,
                limit=args.limit,
                db_names=filter_dbs,
            )
            row = eval_one(item, hits, ks)
        results.append(row)
        flag = ""
        if not item.get("negative"):
            ok = results[-1]["metrics"].get("@10", {}).get("table_hit")
            col_ok = results[-1]["metrics"].get("@10", {}).get("column_hit")
            flag = "PASS" if ok and col_ok else ("TBL" if ok else "FAIL")
        else:
            flag = "NEG"
        print(f"  [{flag}] {item['id']} {item['question'][:40]}", flush=True)

    summary = aggregate(results, ks)
    meta = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "golden": str(golden_path.relative_to(ROOT)).replace("\\", "/"),
        "collection": collection,
        "points_count": info.points_count,
        "filter_mode": filter_mode,
        "filter_databases": filter_dbs or [],
        "limit": args.limit,
        "vector_limit": vector_limit if not args.legacy else args.limit,
        "fair_output_limit": args.limit if not args.legacy else None,
        "retrieval_mode": retrieval_mode,
        "keyword_mode": args.keyword_mode,
        "column_select": args.column_select,
        "clarify_gate": args.clarify_gate,
        "clarify_stats": clarify_stats,
    }
    report = render_report(summary=summary, results=results, ks=ks, meta=meta)

    tag = "E0" if args.legacy else retrieval_mode.upper()
    out = Path(args.out) if args.out else ROOT / "evals" / "reports" / f"recall_{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    # 同步写一份 json 便于二次分析
    json_out = out.with_suffix(".json")
    json_out.write_text(
        json.dumps({"meta": meta, "summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print(report)
    print()
    print(f"报告已写：{out}")
    print(f"明细 JSON：{json_out}")


if __name__ == "__main__":
    main()
