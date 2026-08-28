"""多候选 SQL：沙箱执行聚类选优（对齐 XiYan Candidate Selection 思路）。

不改字段召回。流程：
1. 静态校验通过的候选去重
2. 只读沙箱执行
3. 按结果集指纹聚类（多数一致优先）
4. 组内用 AST/plan 分数打平

无执行成功候选时回退 AST 最高分。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.agent.sql_candidate_rank import score_sql_candidate
from wenshu.services.agent.sql_result_match import normalize_result_rows


def exec_select_enabled() -> bool:
    """仅 XiYan 流程使用；默认关（icecoding 主路径不用执行聚类选优）。"""
    return os.getenv("AGENT_SQL_EXEC_SELECT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def xiyan_pipeline_enabled() -> bool:
    """S1/S2 多候选 + 执行聚类选优（可选，非 icecoding 默认）。"""
    return os.getenv("AGENT_SQL_XIYAN_PIPELINE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _norm_sql(sql: str) -> str:
    return " ".join((sql or "").strip().lower().split())


def result_fingerprint(rows: list[dict] | None) -> str:
    bag = Counter(normalize_result_rows(rows))
    payload = json.dumps(sorted(bag.items()), ensure_ascii=False, default=str, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass
class ExecSelectItem:
    sql: str
    used_tables: list[str] = field(default_factory=list)
    source: str = "model"
    ast_score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
    static_ok: bool = False
    static_errors: list[str] = field(default_factory=list)
    exec_ok: bool = False
    exec_error: str | None = None
    row_count: int = 0
    result_fp: str | None = None
    cluster_size: int = 0
    selected: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecSelectResult:
    ok: bool
    sql: str = ""
    used_tables: list[str] = field(default_factory=list)
    source: str = ""
    reason: str = ""  # majority_exec | ast_fallback | none
    items: list[ExecSelectItem] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "sql": self.sql,
            "used_tables": self.used_tables,
            "source": self.source,
            "reason": self.reason,
            "items": [i.as_dict() for i in self.items],
        }


def select_sql_by_execution(
    candidates: list[dict[str, Any]],
    *,
    plan: QueryPlan,
    dialect: str = "mysql",
    execute_fn: Callable[[str], Any] | None = None,
    max_exec: int = 5,
) -> ExecSelectResult:
    """从候选中按执行一致性选优。

    candidates 项字段：sql, used_tables?, source?, score?, score_reasons?
    execute_fn(sql) -> object with .ok, .rows, .error, .row_count
    """
    # 去重保序
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for c in candidates:
        sql = str(c.get("sql") or "").strip()
        if not sql:
            continue
        key = _norm_sql(sql)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
        if len(uniq) >= max_exec:
            break

    items: list[ExecSelectItem] = []
    for c in uniq:
        sql = str(c.get("sql") or "").strip()
        used = [str(t) for t in (c.get("used_tables") or [])]
        source = str(c.get("source") or "model")
        if "score" in c and c.get("score") is not None:
            sc = float(c["score"])
            reasons = list(c.get("score_reasons") or [])
        else:
            sc, reasons = score_sql_candidate(
                sql, plan=plan, used_tables=used, dialect=dialect
            )
        items.append(
            ExecSelectItem(
                sql=sql,
                used_tables=used,
                source=source,
                ast_score=sc,
                score_reasons=reasons,
                static_ok=True,
            )
        )

    if not items:
        return ExecSelectResult(ok=False, reason="none")

    if execute_fn is None:
        from wenshu.services.agent.sandbox import execute_readonly

        execute_fn = execute_readonly

    for it in items:
        try:
            ex = execute_fn(it.sql)
        except Exception as exc:  # noqa: BLE001
            it.exec_ok = False
            it.exec_error = str(exc)
            continue
        it.exec_ok = bool(getattr(ex, "ok", False))
        if not it.exec_ok:
            it.exec_error = getattr(ex, "error", None)
            continue
        it.row_count = int(getattr(ex, "row_count", None) or len(getattr(ex, "rows", None) or []))
        it.result_fp = result_fingerprint(getattr(ex, "rows", None))

    by_fp: dict[str, list[ExecSelectItem]] = defaultdict(list)
    for it in items:
        if it.exec_ok and it.result_fp:
            by_fp[it.result_fp].append(it)

    if by_fp:
        # 组大小降序；同大小取组内最高 AST；再偏好 deterministic
        def cluster_key(fp: str) -> tuple:
            group = by_fp[fp]
            best = max(
                group,
                key=lambda x: (
                    x.ast_score,
                    1 if x.source == "deterministic" else 0,
                ),
            )
            return (len(group), best.ast_score, 1 if best.source == "deterministic" else 0)

        winner_fp = max(by_fp.keys(), key=cluster_key)
        for it in items:
            it.cluster_size = len(by_fp.get(it.result_fp or "", []))
        group = by_fp[winner_fp]
        chosen = max(
            group,
            key=lambda x: (x.ast_score, 1 if x.source == "deterministic" else 0),
        )
        chosen.selected = True
        return ExecSelectResult(
            ok=True,
            sql=chosen.sql,
            used_tables=list(chosen.used_tables),
            source=chosen.source,
            reason="majority_exec",
            items=items,
        )

    # 全部执行失败 → AST 回退
    chosen = max(
        items,
        key=lambda x: (x.ast_score, 1 if x.source == "deterministic" else 0),
    )
    chosen.selected = True
    return ExecSelectResult(
        ok=True,
        sql=chosen.sql,
        used_tables=list(chosen.used_tables),
        source=chosen.source,
        reason="ast_fallback",
        items=items,
    )
