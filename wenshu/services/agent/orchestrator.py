"""问数 Agent 编排入口：LangGraph 驱动（对齐 icecoding graph 路由）。

字段召回仍走既有 retrieve_schema；本模块只负责 invoke / interrupt resume。
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from wenshu.services.agent.graph import clear_runtime, get_agent_graph, reset_thread

AgentStatus = Literal[
    "running",
    "need_clarify",
    "need_approval",
    "completed",
    "blocked",
    "failed",
]

_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSION_TTL = 3600


@dataclass
class AgentState:
    trace_id: str
    status: AgentStatus = "running"
    question: str = ""
    evidence: str = ""
    semantic_graph: dict = field(default_factory=dict)
    clarify_questions: list[dict] = field(default_factory=list)
    selected_tables: list[str] = field(default_factory=list)
    expanded_tables: list[str] = field(default_factory=list)
    query_plan: dict | None = None
    logical_plan: dict | None = None
    plan_validation_errors: list[str] = field(default_factory=list)
    plan_normalizations: list[str] = field(default_factory=list)
    generated_sql: str | None = None
    used_tables: list[str] = field(default_factory=list)
    sql_generation_source: str = "deterministic"
    sql_candidates: list[dict] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    failed_sql_hashes: list[str] = field(default_factory=list)
    risk_decision: str = "pass"
    sensitive_reasons: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    human_approved: bool | None = None
    execution_result: list[dict] = field(default_factory=list)
    execution_error: str | None = None
    estimated_rows: int | None = None
    result_summary: dict | None = None
    final_answer: str | None = None
    trace_steps: list[str] = field(default_factory=list)
    node_latencies: dict[str, float] = field(default_factory=dict)
    plan_retry_count: int = 0
    retry_count: int = 0
    max_plan_retries: int = 2
    max_retries: int = 2
    row_count: int = 0
    truncated: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _purge() -> None:
    now = time.time()
    dead = [k for k, v in _SESSIONS.items() if now - v.get("ts", 0) > _SESSION_TTL]
    for k in dead:
        _SESSIONS.pop(k, None)
        clear_runtime(k)


def _save(state: AgentState, *, waiting: str | None = None) -> None:
    _SESSIONS[state.trace_id] = {
        "state": state.as_dict(),
        "waiting": waiting,
        "ts": time.time(),
    }


def _from_graph_values(values: dict[str, Any], *, fallback_trace: str) -> AgentState:
    kwargs: dict[str, Any] = {"trace_id": values.get("trace_id") or fallback_trace}
    for key in AgentState.__dataclass_fields__:
        if key == "trace_id":
            continue
        if key in values and values[key] is not None:
            kwargs[key] = values[key]
    return AgentState(**kwargs)


def _interrupted_for_approval(graph, config: dict) -> bool:
    snap = graph.get_state(config)
    nxt = tuple(snap.next or ())
    return "human_review" in nxt


def run_agent(
    question: str,
    evidence: str = "",
    *,
    trace_id: str | None = None,
    clarify_answers: dict[str, str] | None = None,
    human_approved: bool | None = None,
    keyword_mode: str | None = None,
    column_select: bool | None = False,
    skip_sandbox: bool | None = None,
) -> AgentState:
    """跑 LangGraph 主链路；澄清 END 返回；审批 interrupt 后 resume。"""
    _purge()
    skip_sandbox = (
        skip_sandbox
        if skip_sandbox is not None
        else os.getenv("AGENT_SKIP_SANDBOX", "0").strip().lower() in ("1", "true", "yes")
    )
    graph = get_agent_graph()

    # ---- resume: 审批 ----
    if trace_id and human_approved is not None:
        config = {"configurable": {"thread_id": trace_id}}
        snap = graph.get_state(config)
        if not snap.values:
            # fallback：进程重启丢 checkpoint 时用会话快照（仅沙箱，无 SQL 重试）
            raw = _SESSIONS.get(trace_id)
            if not raw:
                raise ValueError(f"会话不存在或已过期: {trace_id}")
            state = _from_graph_values(raw["state"], fallback_trace=trace_id)
            state.human_approved = bool(human_approved)
            state.trace_steps = [*(state.trace_steps or []), "human_review"]
            if not state.human_approved:
                state.status = "failed"
                state.final_answer = "人工审批已拒绝。"
                _save(state)
                return state
            from wenshu.services.agent.sandbox import execute_readonly

            if skip_sandbox:
                state.status = "completed"
                state.final_answer = (
                    f"已跳过沙箱执行（AGENT_SKIP_SANDBOX=1）。"
                    f"SQL 来源={state.sql_generation_source}。"
                )
                state.trace_steps.append("sandbox_execution")
                state.trace_steps.append("result_interpretation")
                _save(state)
                return state
            result = execute_readonly(state.generated_sql or "")
            if not result.ok:
                state.status = "failed"
                state.execution_error = result.error
                state.final_answer = f"沙箱执行失败：{result.error}"
                _save(state)
                return state
            state.execution_result = result.rows
            state.row_count = result.row_count
            state.truncated = result.truncated
            state.generated_sql = result.sql_executed or state.generated_sql
            tip = f"查询成功，返回 {state.row_count} 行"
            if state.truncated:
                tip += "（已截断）"
            state.final_answer = tip + "。"
            state.status = "completed"
            state.trace_steps.append("sandbox_execution")
            state.trace_steps.append("result_interpretation")
            _save(state)
            return state

        graph.update_state(config, {"human_approved": bool(human_approved)})
        result = graph.invoke(None, config)
        state = _from_graph_values(result or snap.values, fallback_trace=trace_id)
        if state.status == "need_approval" and state.human_approved:
            state.status = "completed" if not state.execution_error else state.status
        waiting = "approval" if state.status == "need_approval" else None
        _save(state, waiting=waiting)
        return state

    # ---- 新跑 / 澄清续跑 ----
    tid = trace_id or secrets.token_hex(8)
    # 复用 trace_id（澄清）时必须清掉上一轮已 END 的 checkpoint，否则无法重新从 START 跑
    reset_thread(tid)
    clear_runtime(tid)
    config = {"configurable": {"thread_id": tid}}
    initial: dict[str, Any] = {
        "trace_id": tid,
        "status": "running",
        "question": question or "",
        "evidence": evidence or "",
        "clarify_answers": clarify_answers,
        "keyword_mode": keyword_mode,
        "column_select": column_select,
        "skip_sandbox": skip_sandbox,
        "max_plan_retries": 2,
        "max_retries": 2,
        "plan_retry_count": 0,
        "retry_count": 0,
        "trace_steps": [],
        "node_latencies": {},
        "failed_sql_hashes": [],
        "plan_validation_errors": [],
        "validation_errors": [],
    }
    result = graph.invoke(initial, config)

    if _interrupted_for_approval(graph, config):
        snap = graph.get_state(config)
        values = dict(snap.values or {})
        values["status"] = "need_approval"
        values.setdefault("final_answer", "查询需要人工审批后才能执行。")
        state = _from_graph_values(values, fallback_trace=tid)
        _save(state, waiting="approval")
        return state

    state = _from_graph_values(result or {}, fallback_trace=tid)
    if state.status == "need_clarify":
        _save(state, waiting="clarify")
        return state
    if state.status == "failed" and not state.final_answer:
        errs = state.plan_validation_errors or state.validation_errors
        state.final_answer = "执行失败：" + "；".join((errs or ["unknown"])[:5])
    _save(state)
    return state
