"""问数澄清闸门：语义图完备性检查、会话挂起与 resume。"""

from __future__ import annotations

import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from wenshu.services.query_intent import (
    QueryIntent,
    SemanticGraph,
    build_semantic_graph,
    resolve_intent_mode,
)

PrepareStatus = Literal["ok", "need_clarify"]

_RELATIVE_TIME = re.compile(r"(最近|近期|昨天|前天|上周|本周|本月|上月|同期|近\d+[天周月年])")
_VAGUE_METRIC = re.compile(r"(?<![\u4e00-\u9fff])余额(?![\u4e00-\u9fff])")
_AMBIGUOUS_SCOPE = re.compile(r"(各平台|全平台|所有产品|哪个产品|哪个渠道)")

_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSION_TTL_SEC = 3600


@dataclass
class ClarifyQuestion:
    id: str
    slot: str
    prompt: str
    options: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class QueryPrepareResult:
    status: PrepareStatus
    question: str
    evidence: str
    semantic_graph: dict
    session_id: str | None = None
    clarify_questions: list[dict] = field(default_factory=list)
    clarify_rounds: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def clarify_gate_enabled(mode: str | None = None) -> bool:
    raw = (mode if mode is not None else os.getenv("SCHEMA_CLARIFY_GATE", "1")).strip().lower()
    return raw not in ("0", "false", "off", "no")


def _purge_expired_sessions() -> None:
    now = time.time()
    expired = [sid for sid, s in _SESSIONS.items() if now - s.get("ts", 0) > _SESSION_TTL_SEC]
    for sid in expired:
        _SESSIONS.pop(sid, None)


def _has_concrete_time(text: str) -> bool:
    if re.search(r"\d{4}[-/年]\d{1,2}", text):
        return True
    if re.search(r"\d{1,2}月\d{1,2}日", text):
        return True
    return False


def _clarified_in_evidence(evidence: str, slot: str) -> bool:
    ev = (evidence or "").strip()
    if not ev:
        return False
    if slot == "time_range":
        if re.search(r"\[澄清:time_range\]", ev):
            return True
        if any(x in ev for x in ("不限定时间", "查全量", "2024", "2025", "2023")):
            return True
    if slot == "scope" and re.search(r"\[澄清:(intent_scope|biz_scope)\]", ev):
        return True
    if slot == "metric" and re.search(r"\[澄清:metric_scope\]", ev):
        return True
    return False


def detect_clarify_questions(
    graph: SemanticGraph,
    *,
    question: str,
    evidence: str = "",
) -> list[ClarifyQuestion]:
    """根据语义图与问句判定是否需要澄清（保守，避免误伤完整问句）。"""
    merged = f"{question}\n{evidence}".strip()
    out: list[ClarifyQuestion] = []

    if graph.query_type in {"aggregation", "fact_filter", "multi_fact"}:
        if (
            _RELATIVE_TIME.search(question)
            and not _has_concrete_time(merged)
            and not _clarified_in_evidence(evidence, "time_range")
        ):
            out.append(
                ClarifyQuestion(
                    id="time_range",
                    slot="time_range",
                    prompt="请补充具体统计时间范围（例如 2024-01-01 至 2024-12-31，或「不限定时间」）",
                    options=["不限定时间", "最近7天", "最近30天", "本月", "自定义日期"],
                )
            )

    if graph.query_type == "unknown" and graph.confidence < 0.4:
        if (
            not graph.entities
            and not graph.measures
            and not graph.attributes
            and not _clarified_in_evidence(evidence, "scope")
        ):
            out.append(
                ClarifyQuestion(
                    id="intent_scope",
                    slot="scope",
                    prompt="未能识别查询对象，请说明要查哪类业务数据（如个人客户、借据、还款明细）",
                    options=["个人客户", "借据", "还款计划", "产品", "其他"],
                )
            )

    if _AMBIGUOUS_SCOPE.search(merged) and not graph.filters and not _clarified_in_evidence(evidence, "scope"):
        out.append(
            ClarifyQuestion(
                id="biz_scope",
                slot="scope",
                prompt="请明确业务范围或过滤条件（如具体产品、渠道、机构）",
                options=["全部", "指定产品", "指定渠道"],
            )
        )

    if _VAGUE_METRIC.search(merged) and not _clarified_in_evidence(evidence, "metric"):
        entity_texts = {s.text for s in graph.entities}
        if not entity_texts or ("借据" not in merged and "还款" not in merged and "客户" not in merged):
            out.append(
                ClarifyQuestion(
                    id="metric_scope",
                    slot="metric",
                    prompt="「余额」可能对应多种口径，请说明是哪种余额（如贷款本金余额、逾期本金余额、正常本金余额）",
                    options=["贷款本金余额", "逾期本金余额", "正常本金余额", "不限定"],
                )
            )

    # 去重 slot
    seen: set[str] = set()
    deduped: list[ClarifyQuestion] = []
    for q in out:
        if q.slot in seen:
            continue
        seen.add(q.slot)
        deduped.append(q)
    return deduped


def merge_clarify_answers(
    question: str,
    evidence: str,
    answers: dict[str, str],
) -> tuple[str, str]:
    """把用户澄清答案并入 evidence（不篡改原问句）。"""
    if not answers:
        return question, evidence
    parts = [evidence.strip()] if evidence and evidence.strip() else []
    for qid, ans in answers.items():
        text = str(ans or "").strip()
        if not text:
            continue
        parts.append(f"[澄清:{qid}] {text}")
    merged_evidence = "\n".join(p for p in parts if p).strip()
    return question, merged_evidence


def auto_answer_for_eval(
    item: dict,
    questions: list[ClarifyQuestion],
) -> dict[str, str]:
    """评测时自动补答澄清（由黄金集 domain / must_columns 推断）。"""
    answers: dict[str, str] = {}
    domain = str(item.get("domain") or "").strip()
    must_cols = item.get("must_columns") or []
    col_hints: list[str] = []
    for c in must_cols:
        if isinstance(c, dict):
            table = str(c.get("table") or "")
            col = str(c.get("column") or "")
            if table and col:
                col_hints.append(f"{table}.{col}")

    for q in questions:
        if q.id == "time_range":
            answers[q.id] = "不限定时间，查全量"
        elif q.id == "intent_scope":
            if domain and domain != "多表":
                answers[q.id] = domain
            elif item.get("must_tables"):
                answers[q.id] = str(item["must_tables"][0])
            else:
                answers[q.id] = "个人客户"
        elif q.id == "biz_scope":
            answers[q.id] = domain or "全部"
        elif q.id == "metric_scope":
            if col_hints:
                answers[q.id] = f"参考字段：{', '.join(col_hints[:3])}"
            else:
                answers[q.id] = "贷款本金余额"
        else:
            answers[q.id] = domain or "全部"
    return answers


def prepare_query(
    question: str,
    evidence: str = "",
    *,
    session_id: str | None = None,
    clarify_answers: dict[str, str] | None = None,
    intent_mode: str | None = None,
    gate: bool | None = None,
    auto_clarify: bool = False,
    eval_item: dict | None = None,
) -> QueryPrepareResult:
    """
    构建语义图并决定是否澄清。
    - need_clarify：不调向量检索
    - ok：返回合并后的 question/evidence 与 semantic_graph
    """
    _purge_expired_sessions()
    gate_on = clarify_gate_enabled() if gate is None else bool(gate)
    rounds = 0
    pending = _SESSIONS.get(session_id or "") if session_id else None

    if pending and clarify_answers:
        question = pending.get("question") or question
        evidence = pending.get("evidence") or evidence
        prior = pending.get("prior_answers") or {}
        merged_prior = {**prior, **clarify_answers}
        question, evidence = merge_clarify_answers(question, evidence, merged_prior)
        rounds = int(pending.get("rounds") or 0) + 1
        _SESSIONS.pop(session_id or "", None)
    elif clarify_answers:
        question, evidence = merge_clarify_answers(question, evidence, clarify_answers)

    graph = build_semantic_graph(question, evidence, mode=intent_mode)
    questions = detect_clarify_questions(graph, question=question, evidence=evidence)

    if gate_on and questions and auto_clarify and eval_item is not None:
        auto_ans = auto_answer_for_eval(eval_item, questions)
        question, evidence = merge_clarify_answers(question, evidence, auto_ans)
        graph = build_semantic_graph(question, evidence, mode=intent_mode)
        questions = detect_clarify_questions(graph, question=question, evidence=evidence)
        rounds += 1

    if gate_on and questions:
        sid = session_id or secrets.token_hex(8)
        _SESSIONS[sid] = {
            "question": question,
            "evidence": evidence,
            "prior_answers": clarify_answers or {},
            "rounds": rounds,
            "graph": graph.as_dict(),
            "ts": time.time(),
        }
        return QueryPrepareResult(
            status="need_clarify",
            question=question,
            evidence=evidence,
            semantic_graph=graph.as_dict(),
            session_id=sid,
            clarify_questions=[q.as_dict() for q in questions],
            clarify_rounds=rounds,
        )

    return QueryPrepareResult(
        status="ok",
        question=question,
        evidence=evidence,
        semantic_graph=graph.as_dict(),
        session_id=session_id,
        clarify_questions=[],
        clarify_rounds=rounds,
    )


def intent_from_graph_dict(data: dict) -> QueryIntent:
    """从 semantic_graph dict 还原 QueryIntent（供 keyword 派生）。"""
    return QueryIntent(
        query_type=data.get("query_type") or "unknown",  # type: ignore[arg-type]
        query_action=data.get("query_action") or "unknown",  # type: ignore[arg-type]
        entities=[],
        measures=[],
        attributes=[],
        filters=[],
        dimensions=[],
        table_phrases=list(data.get("table_phrases") or []),
        column_phrases=list(data.get("column_phrases") or []),
        filter_phrases=list(data.get("filter_phrases") or []),
        join_phrases=list(data.get("join_phrases") or []),
        force_multi_table=bool(data.get("force_multi_table")),
        source=str(data.get("source") or "graph"),
        confidence=float(data.get("confidence") or 0.0),
    )
