"""LangGraph 编排（对齐 icecoding graph.py）。

字段召回仍调用既有 retrieve_schema，不改召回算法。
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Literal, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from wenshu.services.agent.plan_builder import build_query_plan, validate_query_plan
from wenshu.services.agent.logical_plan import build_logical_plan
from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.agent.result_interpretation import interpret_result
from wenshu.services.agent.risk import assess_risk
from wenshu.services.agent.sandbox import execute_readonly
from wenshu.services.agent.sql_generation import generate_sql
from wenshu.services.agent.sql_validate import validate_sql_ast
from wenshu.services.query_clarify import prepare_query

AgentStatus = Literal[
    "running",
    "need_clarify",
    "need_approval",
    "completed",
    "blocked",
    "failed",
]


class AgentGraphState(TypedDict, total=False):
    trace_id: str
    status: AgentStatus
    question: str
    evidence: str
    semantic_graph: dict
    clarify_questions: list
    selected_tables: list
    expanded_tables: list
    query_plan: Optional[dict]
    logical_plan: Optional[dict]
    plan_validation_errors: list
    plan_normalizations: list
    generated_sql: Optional[str]
    used_tables: list
    sql_generation_source: str
    sql_candidates: list
    validation_errors: list
    failed_sql_hashes: list
    risk_decision: str
    sensitive_reasons: list
    blocked_reason: Optional[str]
    human_approved: Optional[bool]
    execution_result: list
    execution_error: Optional[str]
    estimated_rows: Optional[int]
    result_summary: Optional[dict]
    final_answer: Optional[str]
    trace_steps: list
    node_latencies: dict
    plan_retry_count: int
    retry_count: int
    max_plan_retries: int
    max_retries: int
    row_count: int
    truncated: bool
    # 运行参数（可序列化）
    keyword_mode: Optional[str]
    column_select: Optional[bool]
    skip_sandbox: bool
    clarify_answers: Optional[dict]


# 不可序列化对象：召回结果 / meta_engine
_RUNTIME: dict[str, dict[str, Any]] = {}
_CHECKPOINTER = MemorySaver()


def _rt(trace_id: str) -> dict[str, Any]:
    return _RUNTIME.setdefault(trace_id, {})


def _sql_hash(sql: str) -> str:
    return hashlib.sha256((sql or "").encode("utf-8")).hexdigest()


def _timed(state: AgentGraphState, name: str, fn):
    t0 = time.perf_counter()
    out = fn()
    ms = round((time.perf_counter() - t0) * 1000, 2)
    lat = dict(state.get("node_latencies") or {})
    lat[name] = round(float(lat.get(name, 0)) + ms, 2)
    steps = list(state.get("trace_steps") or [])
    steps.append(name)
    return out, {"node_latencies": lat, "trace_steps": steps}


def _retrieve(question: str, evidence: str, *, keyword_mode, column_select):
    """字段召回层：保持调用既有 retrieve_schema，算法不变。"""
    import build_vector_index as bvi
    from db_config import create_qdrant_client, get_meta_mysql_engine, get_raw_database_name
    from wenshu.services.schema_retrieval import retrieve_schema
    from wenshu.services.vector_search import resolve_search_db_names

    bvi._load_dotenv()
    client = create_qdrant_client()
    collection = bvi.QDRANT_COLLECTION
    if not client.collection_exists(collection):
        raise RuntimeError("向量集合尚未创建，请先构建向量索引")
    embed_text = f"{question}\n{evidence}".strip() if evidence else question
    vector = bvi.embed([embed_text], is_query=True)[0]
    filter_dbs, _ = resolve_search_db_names(
        db_names=None,
        all_databases=False,
        default_raw_db=get_raw_database_name(),
    )
    meta_engine = get_meta_mysql_engine()
    retrieval = retrieve_schema(
        client=client,
        collection_name=collection,
        meta_engine=meta_engine,
        query_vector=vector,
        question=question,
        evidence=evidence or "",
        db_names=filter_dbs,
        vector_limit=50,
        keyword_mode=keyword_mode,
        column_select=column_select,
    )
    return retrieval, meta_engine


def node_query_resolution(state: AgentGraphState) -> dict:
    prepared, timing = _timed(
        state,
        "query_resolution",
        lambda: prepare_query(
            state.get("question") or "",
            state.get("evidence") or "",
            session_id=state.get("trace_id"),
            clarify_answers=state.get("clarify_answers"),
            gate=True,
        ),
    )
    out = {
        **timing,
        "question": prepared.question,
        "evidence": prepared.evidence,
        "semantic_graph": prepared.semantic_graph or {},
        "trace_id": prepared.session_id or state.get("trace_id"),
    }
    if prepared.status == "need_clarify":
        out.update(
            {
                "status": "need_clarify",
                "clarify_questions": prepared.clarify_questions or [],
                "final_answer": "需要补充业务信息后再继续。",
            }
        )
    else:
        out["status"] = "running"
    return out


def node_schema_retrieval(state: AgentGraphState) -> dict:
    try:
        (retrieval, meta_engine), timing = _timed(
            state,
            "schema_retrieval",
            lambda: _retrieve(
                state.get("question") or "",
                state.get("evidence") or "",
                keyword_mode=state.get("keyword_mode"),
                column_select=state.get("column_select"),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "final_answer": f"Schema 召回失败: {exc}",
            "trace_steps": [*(state.get("trace_steps") or []), "schema_retrieval"],
        }
    tid = state.get("trace_id") or ""
    _rt(tid).update({"retrieval": retrieval, "meta_engine": meta_engine})
    return {
        **timing,
        "status": "running",
        "selected_tables": list(retrieval.selected_tables or []),
        "expanded_tables": list(retrieval.expanded_tables or []),
    }


def node_plan_generation(state: AgentGraphState) -> dict:
    tid = state.get("trace_id") or ""
    bag = _rt(tid)
    retrieval = bag.get("retrieval")
    meta_engine = bag.get("meta_engine")
    prev_errs = list(state.get("plan_validation_errors") or []) or None
    if state.get("plan_retry_count", 0) == 0:
        prev_errs = None
    prev_plan = state.get("query_plan")
    try:
        def _build():
            plan = build_query_plan(
                question=state.get("question") or "",
                evidence=state.get("evidence") or "",
                semantic_graph=state.get("semantic_graph") or {},
                retrieval=retrieval,
                meta_engine=meta_engine,
                previous_errors=prev_errs,
                previous_plan=prev_plan,
            )
            logical = build_logical_plan(
                plan,
                question=state.get("question") or "",
                semantic_graph=state.get("semantic_graph") or {},
            )
            return plan, logical

        (plan, logical), timing = _timed(state, "plan_generation", _build)
        bag["plan"] = plan
        bag["logical_plan"] = logical
        return {
            **timing,
            "query_plan": plan.as_dict(),
            "logical_plan": logical.as_dict(),
            "plan_validation_errors": [],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "plan_validation_errors": [*(state.get("plan_validation_errors") or []), str(exc)],
            "query_plan": None,
            "logical_plan": None,
            "trace_steps": [*(state.get("trace_steps") or []), "plan_generation"],
        }


def node_plan_validation(state: AgentGraphState) -> dict:
    tid = state.get("trace_id") or ""
    bag = _rt(tid)
    plan_dict = state.get("query_plan")
    if not plan_dict:
        errs = list(state.get("plan_validation_errors") or []) or ["计划为空"]
        retry = int(state.get("plan_retry_count") or 0) + 1
        out: dict[str, Any] = {
            "plan_validation_errors": errs,
            "plan_retry_count": retry,
            "trace_steps": [*(state.get("trace_steps") or []), "plan_validation"],
        }
        if retry > int(state.get("max_plan_retries") or 2):
            out.update(
                {
                    "status": "failed",
                    "final_answer": "查询计划多次校验失败：" + "；".join(errs[:5]),
                }
            )
        return out
    plan = QueryPlan.model_validate(plan_dict)
    logical = bag.get("logical_plan")
    if logical is None and state.get("logical_plan"):
        from wenshu.services.agent.logical_plan import LogicalPlan

        logical = LogicalPlan.model_validate(state["logical_plan"])

    def _validate():
        return validate_query_plan(
            plan,
            question=state.get("question") or "",
            semantic_graph=state.get("semantic_graph") or {},
            retrieval=bag.get("retrieval"),
            meta_engine=bag.get("meta_engine"),
            logical_plan=logical,
        )

    errs, timing = _timed(state, "plan_validation", _validate)
    out = {**timing, "plan_validation_errors": errs}
    if errs:
        retry = int(state.get("plan_retry_count") or 0) + 1
        out["plan_retry_count"] = retry
        if retry > int(state.get("max_plan_retries") or 2):
            out.update(
                {
                    "status": "failed",
                    "final_answer": "查询计划多次校验失败：" + "；".join(errs[:5]),
                }
            )
    return out


def node_sql_generation(state: AgentGraphState) -> dict:
    tid = state.get("trace_id") or ""
    bag = _rt(tid)
    retrieval = bag.get("retrieval")
    plan = bag.get("plan")
    if plan is None and state.get("query_plan"):
        plan = QueryPlan.model_validate(state["query_plan"])
        bag["plan"] = plan
    force_llm_sql = os.getenv("AGENT_SQL_LLM", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    retry = int(state.get("retry_count") or 0)
    from wenshu.services.agent.sql_ensemble import run_pipeline_sql_select
    from wenshu.services.agent.sql_exec_select import xiyan_pipeline_enabled

    use_xiyan = xiyan_pipeline_enabled() and not state.get("skip_sandbox")
    if not use_xiyan and os.getenv("AGENT_SQL_EXEC_SELECT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        use_xiyan = True

    if use_xiyan and plan is not None:

        def _pipe():
            return run_pipeline_sql_select(
                plan=plan,
                question=state.get("question") or "",
                retrieval=retrieval,
                dialect="mysql",
                prefer_deterministic=not force_llm_sql and retry == 0,
                use_exec_select=True,
                skip_sandbox=False,
                max_refine=2,
            )

        pipe, timing = _timed(state, "sql_generation", _pipe)
        out = {
            **timing,
            "execution_error": None,
            "validation_errors": [],
            "generated_sql": pipe.get("sql") or "",
            "used_tables": list(pipe.get("used_tables") or []),
            "sql_generation_source": str(pipe.get("source") or ""),
            "sql_candidates": list(pipe.get("candidates") or []),
        }
        if not pipe.get("ok"):
            err = pipe.get("error") or pipe.get("blocked_reason") or "sql ensemble failed"
            out["validation_errors"] = [str(err)]
            out["retry_count"] = retry + 1
            if pipe.get("blocked_reason"):
                out["status"] = "blocked"
                out["blocked_reason"] = pipe["blocked_reason"]
                out["final_answer"] = f"危险 SQL 已阻断：{pipe['blocked_reason']}"
        return out

    def _gen():
        return generate_sql(
            plan=plan,
            question=state.get("question") or "",
            retrieval=retrieval,
            dialect="mysql",
            previous_sql=state.get("generated_sql"),
            validation_errors=state.get("validation_errors") or None,
            execution_error=state.get("execution_error"),
            prefer_deterministic=not force_llm_sql and retry == 0,
            allow_llm=True,
            multi_candidate=(retry == 0),
            pending_candidates=state.get("sql_candidates") if retry > 0 else None,
        )

    gen, timing = _timed(state, "sql_generation", _gen)
    out = {
        **timing,
        "execution_error": None,
        "validation_errors": [],
        "generated_sql": gen.sql,
        "used_tables": list(gen.used_tables),
        "sql_generation_source": gen.source,
        "sql_candidates": list(gen.candidates),
    }
    if gen.error and not gen.sql:
        out["validation_errors"] = [gen.error]
        out["retry_count"] = retry + 1
    return out


def node_static_validation(state: AgentGraphState) -> dict:
    tid = state.get("trace_id") or ""
    bag = _rt(tid)
    plan = bag.get("plan")
    if plan is None and state.get("query_plan"):
        plan = QueryPlan.model_validate(state["query_plan"])
    sql = state.get("generated_sql") or ""
    v, timing = _timed(
        state,
        "static_validation",
        lambda: validate_sql_ast(
            sql,
            plan=plan,
            used_tables=state.get("used_tables") or [],
            retrieval=bag.get("retrieval"),
            dialect="mysql",
            generation_source=state.get("sql_generation_source") or "model",
            failed_sql_hashes=state.get("failed_sql_hashes") or [],
        ),
    )
    out = {**timing}
    if v.blocked_reason:
        out.update(
            {
                "status": "blocked",
                "blocked_reason": v.blocked_reason,
                "validation_errors": v.errors,
                "final_answer": f"危险 SQL 已阻断：{v.blocked_reason}",
            }
        )
        return out
    if not v.ok:
        hashes = list(state.get("failed_sql_hashes") or [])
        h = _sql_hash(sql)
        if sql and h not in hashes:
            hashes.append(h)
        retry = int(state.get("retry_count") or 0)
        if v.non_retryable:
            retry = int(state.get("max_retries") or 2) + 1
        else:
            retry += 1
        out.update(
            {
                "validation_errors": list(v.errors),
                "failed_sql_hashes": hashes,
                "retry_count": retry,
            }
        )
        if retry > int(state.get("max_retries") or 2):
            reason = (
                "确定性 SQL 编译结果未通过校验"
                if state.get("sql_generation_source") == "deterministic"
                else "SQL 生成多次校验失败"
            )
            out.update(
                {
                    "status": "failed",
                    "final_answer": reason + "：" + "；".join((v.errors or [])[:5]),
                }
            )
        return out
    out["validation_errors"] = []
    return out


def node_sensitive_check(state: AgentGraphState) -> dict:
    plan_conf = 0.5
    if state.get("query_plan"):
        plan_conf = float(state["query_plan"].get("confidence") or 0.5)
    risk, timing = _timed(
        state,
        "sensitive_check",
        lambda: assess_risk(
            sql=state.get("generated_sql") or "",
            question=state.get("question") or "",
            plan_confidence=plan_conf,
        ),
    )
    approval = os.getenv("AGENT_APPROVAL_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )
    out = {
        **timing,
        "risk_decision": risk.decision,
        "sensitive_reasons": risk.reasons,
    }
    if risk.decision == "hard_block":
        out.update(
            {
                "status": "blocked",
                "blocked_reason": risk.blocked_reason,
                "final_answer": "查询因安全策略被阻断：" + "；".join(risk.reasons),
            }
        )
    elif risk.decision == "approval_required" and approval:
        out.update(
            {
                "status": "need_approval",
                "final_answer": "查询需要人工审批后才能执行。",
            }
        )
    return out


def node_human_review(state: AgentGraphState) -> dict:
    steps = [*(state.get("trace_steps") or []), "human_review"]
    if state.get("human_approved"):
        return {"status": "running", "trace_steps": steps}
    return {
        "status": "failed",
        "final_answer": "人工审批已拒绝。",
        "trace_steps": steps,
    }


def node_sandbox_execution(state: AgentGraphState) -> dict:
    if state.get("skip_sandbox"):
        return {
            "trace_steps": [*(state.get("trace_steps") or []), "sandbox_execution"],
            "execution_result": [],
            "row_count": 0,
            "execution_error": None,
            "final_answer": (
                f"已跳过沙箱执行（AGENT_SKIP_SANDBOX=1）。"
                f"SQL 来源={state.get('sql_generation_source')}。"
            ),
            "status": "completed",
        }

    result, timing = _timed(
        state,
        "sandbox_execution",
        lambda: execute_readonly(state.get("generated_sql") or ""),
    )
    if not result.ok:
        retry = int(state.get("retry_count") or 0) + 1
        hashes = list(state.get("failed_sql_hashes") or [])
        h = _sql_hash(state.get("generated_sql") or "")
        if h and h not in hashes:
            hashes.append(h)
        out = {
            **timing,
            "execution_error": result.error,
            "estimated_rows": result.estimated_rows,
            "retry_count": retry,
            "failed_sql_hashes": hashes,
        }
        if retry > int(state.get("max_retries") or 2):
            out.update(
                {
                    "status": "failed",
                    "final_answer": f"沙箱执行失败：{result.error}",
                }
            )
        return out
    return {
        **timing,
        "execution_error": None,
        "execution_result": result.rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "estimated_rows": result.estimated_rows,
        "generated_sql": result.sql_executed or state.get("generated_sql"),
    }


def node_result_interpretation(state: AgentGraphState) -> dict:
    if state.get("skip_sandbox") and state.get("status") == "completed":
        return {
            "trace_steps": [*(state.get("trace_steps") or []), "result_interpretation"],
            "status": "completed",
        }
    plan = None
    if state.get("query_plan"):
        plan = QueryPlan.model_validate(state["query_plan"])

    def _interpret():
        return interpret_result(
            question=state.get("question") or "",
            rows=state.get("execution_result"),
            plan=plan,
            truncated=bool(state.get("truncated")),
            blocked_reason=state.get("blocked_reason"),
            execution_error=state.get("execution_error"),
        )

    (answer, summary), timing = _timed(state, "result_interpretation", _interpret)
    out: dict[str, Any] = {
        **timing,
        "final_answer": answer,
        "status": "completed",
    }
    if summary is not None:
        out["result_summary"] = summary.model_dump()
    return out


# ----- routes -----

def route_after_clarify(state: AgentGraphState) -> str:
    return "need_info" if state.get("status") == "need_clarify" else "proceed"


def route_after_retrieval(state: AgentGraphState) -> str:
    return "failed" if state.get("status") == "failed" else "ok"


def route_plan_validation(state: AgentGraphState) -> str:
    if not state.get("plan_validation_errors"):
        return "pass"
    if state.get("status") == "failed" or state.get("final_answer"):
        return "give_up"
    if int(state.get("plan_retry_count") or 0) <= int(state.get("max_plan_retries") or 2):
        return "retry"
    return "give_up"


def route_static_validation(state: AgentGraphState) -> str:
    if state.get("blocked_reason") or state.get("status") == "blocked":
        return "blocked"
    if state.get("status") == "failed" or state.get("final_answer"):
        return "give_up"
    if not state.get("validation_errors"):
        return "pass"
    if int(state.get("retry_count") or 0) <= int(state.get("max_retries") or 2):
        return "retry"
    return "give_up"


def route_sensitive(state: AgentGraphState) -> str:
    approval = os.getenv("AGENT_APPROVAL_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )
    if state.get("status") == "blocked" or state.get("risk_decision") == "hard_block":
        return "hard_block"
    if state.get("risk_decision") == "approval_required":
        return "approval_required" if approval else "pass"
    return "pass"


def route_human_review(state: AgentGraphState) -> str:
    return "approved" if state.get("human_approved") else "rejected"


def route_sandbox(state: AgentGraphState) -> str:
    if state.get("status") == "completed" and state.get("skip_sandbox"):
        return "success"
    if not state.get("execution_error"):
        return "success"
    if state.get("status") == "failed":
        return "give_up"
    if int(state.get("retry_count") or 0) <= int(state.get("max_retries") or 2):
        return "retry"
    return "give_up"


def build_agent_graph(approval_enabled: bool = True):
    g = StateGraph(AgentGraphState)
    g.add_node("query_resolution", node_query_resolution)
    g.add_node("schema_retrieval", node_schema_retrieval)
    g.add_node("plan_generation", node_plan_generation)
    g.add_node("plan_validation", node_plan_validation)
    g.add_node("sql_generation", node_sql_generation)
    g.add_node("static_validation", node_static_validation)
    g.add_node("sensitive_check", node_sensitive_check)
    g.add_node("human_review", node_human_review)
    g.add_node("sandbox_execution", node_sandbox_execution)
    g.add_node("result_interpretation", node_result_interpretation)

    g.add_edge(START, "query_resolution")
    g.add_conditional_edges(
        "query_resolution",
        route_after_clarify,
        {"need_info": END, "proceed": "schema_retrieval"},
    )
    g.add_conditional_edges(
        "schema_retrieval",
        route_after_retrieval,
        {"failed": END, "ok": "plan_generation"},
    )
    g.add_edge("plan_generation", "plan_validation")
    g.add_conditional_edges(
        "plan_validation",
        route_plan_validation,
        {"pass": "sql_generation", "retry": "plan_generation", "give_up": END},
    )
    g.add_edge("sql_generation", "static_validation")
    g.add_conditional_edges(
        "static_validation",
        route_static_validation,
        {
            "pass": "sensitive_check",
            "retry": "sql_generation",
            "blocked": END,
            "give_up": END,
        },
    )
    g.add_conditional_edges(
        "sensitive_check",
        route_sensitive,
        {
            "approval_required": "human_review",
            "hard_block": END,
            "pass": "sandbox_execution",
        },
    )
    g.add_conditional_edges(
        "human_review",
        route_human_review,
        {"approved": "sandbox_execution", "rejected": END},
    )
    g.add_conditional_edges(
        "sandbox_execution",
        route_sandbox,
        {
            "success": "result_interpretation",
            "retry": "sql_generation",
            "give_up": END,
        },
    )
    g.add_edge("result_interpretation", END)

    interrupt = ["human_review"] if approval_enabled else []
    return g.compile(checkpointer=_CHECKPOINTER, interrupt_before=interrupt)


def clear_runtime(trace_id: str | None = None) -> None:
    """清理不可序列化运行时袋（召回 / meta_engine）。"""
    if trace_id:
        _RUNTIME.pop(trace_id, None)
    else:
        _RUNTIME.clear()


def reset_thread(thread_id: str) -> None:
    """澄清续跑 / 新问题复用同一 thread_id 时清掉旧 checkpoint。"""
    cp = _CHECKPOINTER
    if hasattr(cp, "delete_thread"):
        try:
            cp.delete_thread(thread_id)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    storage = getattr(cp, "storage", None)
    if isinstance(storage, dict):
        for k in list(storage.keys()):
            if k == thread_id or (isinstance(k, tuple) and k and k[0] == thread_id):
                storage.pop(k, None)
    writes = getattr(cp, "writes", None)
    if isinstance(writes, dict):
        for k in list(writes.keys()):
            if isinstance(k, tuple) and k and k[0] == thread_id:
                writes.pop(k, None)


_GRAPH = None


def get_agent_graph():
    global _GRAPH
    approval = os.getenv("AGENT_APPROVAL_ENABLED", "1").strip() not in (
        "0",
        "false",
        "off",
        "no",
    )
    # 审批开关变化时重建
    key = f"approval={approval}"
    if _GRAPH is None or getattr(get_agent_graph, "_key", None) != key:
        _GRAPH = build_agent_graph(approval_enabled=approval)
        get_agent_graph._key = key  # type: ignore[attr-defined]
    return _GRAPH
