"""结果解读（对齐 icecoding M11）：结构化摘要 + 可选 LLM 叙事。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from wenshu.services.agent.llm_structured import complete_structured
from wenshu.services.agent.plan_models import QueryPlan
from wenshu.services.comment_llm import llm_available

_PROMPTS = Path(__file__).resolve().parent / "prompts"


class ResultSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["success", "empty", "partial"] = "success"
    headline: str = ""
    overview: str = ""
    key_findings: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    row_count: int = 0
    summarized_row_count: int = 0
    truncated: bool = False


def render_result_summary(summary: ResultSummary) -> str:
    sections = [summary.headline, summary.overview]
    if summary.key_findings:
        sections.append("关键发现：\n" + "\n".join(f"- {item}" for item in summary.key_findings))
    if summary.caveats:
        sections.append("说明：\n" + "\n".join(f"- {item}" for item in summary.caveats))
    return "\n\n".join(section for section in sections if section)


def sanitize_rows_for_llm(
    rows: list[dict],
    *,
    sensitive_names: set[str] | None = None,
    max_rows: int = 50,
    max_text_length: int = 200,
) -> list[dict]:
    sensitive = {s.lower() for s in (sensitive_names or set())}
    # 默认敏感列关键词
    sensitive.update({"idnum", "phone_no", "mobile", "id_card", "身份证", "手机号"})
    out: list[dict] = []
    for row in rows[:max_rows]:
        clean: dict = {}
        for key, value in row.items():
            if str(key).lower() in sensitive:
                clean[key] = "[已脱敏]"
            elif isinstance(value, str) and len(value) > max_text_length:
                clean[key] = value[:max_text_length] + "…[已截断]"
            else:
                clean[key] = value
        out.append(clean)
    return out


def deterministic_result_summary(
    query: str,
    rows: list[dict],
    *,
    plan: QueryPlan | None = None,
    truncated: bool = False,
) -> ResultSummary:
    if not rows:
        return ResultSummary(
            status="empty",
            headline="未找到符合条件的数据",
            overview=f"按照“{query}”所描述的条件查询后，本次没有返回记录。",
            key_findings=["当前结果集为空，不代表相关业务数据一定从未存在。"],
            caveats=["可以检查筛选条件、时间范围和当前数据权限是否符合预期。"],
            row_count=0,
            summarized_row_count=0,
        )

    grain = plan.output_grain if plan else None
    entity = (grain.entity if grain and grain.entity else None) or "目标对象"
    if grain and grain.level == "entity":
        overview = f"本次返回 {len(rows)} 行符合条件的{entity}结果，每行代表一个{entity}。"
    elif grain and grain.level in {"aggregate", "global"}:
        overview = f"已完成查询所要求的汇总计算，本次返回 {len(rows)} 行汇总结果。"
    elif grain and grain.level == "record":
        overview = f"本次返回 {len(rows)} 行符合条件的业务明细。"
    else:
        overview = f"本次查询共返回 {len(rows)} 行结果。"

    findings: list[str] = []
    output_labels = list(
        dict.fromkeys(
            field.concept or field.alias or field.column or "返回值"
            for field in (plan.output_fields if plan else [])
        )
    )
    if output_labels:
        findings.append(f"结果包含：{'、'.join(output_labels)}。")
    if plan and plan.group_by:
        findings.append(f"结果共形成 {len(rows)} 个分组。")

    if len(rows) == 1 and plan and grain and grain.level in {"aggregate", "global"}:
        row = sanitize_rows_for_llm(rows, max_rows=1)[0]
        values: list[str] = []
        for field in plan.output_fields:
            key = field.alias or field.column
            if not key or key not in row:
                continue
            value = row[key]
            label = field.concept or key
            values.append(f"{label}为 {value if value is not None else '空值'}")
        if values:
            findings.append("；".join(values[:5]) + "。")

    caveats = ["详细记录可在下方结果数据中查看。"]
    if truncated:
        caveats.append("本次结果可能已截断，可能仍有更多符合条件的数据。")

    return ResultSummary(
        status="partial" if truncated else "success",
        headline=f"已完成查询，共返回 {len(rows)} 行结果",
        overview=overview,
        key_findings=findings[:5],
        caveats=list(dict.fromkeys(caveats))[:5],
        row_count=len(rows),
        summarized_row_count=len(rows),
        truncated=truncated,
    )


def _should_use_llm_summary(plan: QueryPlan | None, rows: list[dict]) -> bool:
    mode = os.getenv("AGENT_RESULT_SUMMARY_MODE", "auto").strip().lower()
    if mode in {"always", "true", "1", "on"}:
        return True
    if mode in {"never", "false", "0", "off"}:
        return False
    if plan is None or not rows:
        return False
    # auto：仅单行全局聚合做 LLM 叙事（对齐 icecoding）
    return bool(
        len(rows) == 1
        and plan.output_grain.level == "global"
        and any(field.aggregation for field in plan.output_fields)
    )


def interpret_result(
    *,
    question: str,
    rows: list[dict] | None,
    plan: QueryPlan | None = None,
    truncated: bool = False,
    column_meanings: dict[str, str] | None = None,
    blocked_reason: str | None = None,
    execution_error: str | None = None,
) -> tuple[str, ResultSummary | None]:
    if blocked_reason:
        return f"查询被安全策略阻断：{blocked_reason}", None
    if execution_error:
        return f"执行失败：{execution_error}", None
    if rows is None:
        return "未获得查询结果。", None

    fallback = deterministic_result_summary(
        question or "", rows, plan=plan, truncated=truncated
    )
    if not rows or not _should_use_llm_summary(plan, rows) or not llm_available():
        return render_result_summary(fallback), fallback

    try:
        tpl = (_PROMPTS / "result_summary.txt").read_text(encoding="utf-8")
        safe_rows = sanitize_rows_for_llm(rows)
        plan_context = {
            "output_fields": [
                {"concept": f.concept, "alias": f.alias, "column": f.column}
                for f in (plan.output_fields if plan else [])
            ],
            "output_grain": plan.output_grain.model_dump() if plan else None,
            "group_by": plan.group_by if plan else [],
        }
        prompt = tpl.format(
            user_query=json.dumps(question or "", ensure_ascii=False),
            column_meanings=json.dumps(column_meanings or {}, ensure_ascii=False),
            plan_context=json.dumps(plan_context, ensure_ascii=False),
            total_row_count=len(rows),
            row_count=len(safe_rows),
            rows_truncated=str(len(rows) > len(safe_rows)).lower(),
            rows=json.dumps(safe_rows, ensure_ascii=False, default=str),
        )
        generated = complete_structured(prompt, ResultSummary, retries=0, timeout=60)
        summary = generated.model_copy(
            update={
                "status": fallback.status,
                "row_count": len(rows),
                "summarized_row_count": len(safe_rows),
                "truncated": len(rows) > len(safe_rows) or fallback.truncated,
                "caveats": list(dict.fromkeys([*generated.caveats, *fallback.caveats]))[:5],
            }
        )
        return render_result_summary(summary), summary
    except Exception:  # noqa: BLE001
        return render_result_summary(fallback), fallback
