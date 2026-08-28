"""风险判定（对齐 icecoding M9 + sensitive_rules.yaml）。"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from wenshu.services.agent.sql_ast import SqlDialect

_RULES_PATH = Path(__file__).resolve().parent / "config" / "sensitive_rules.yaml"


@dataclass
class RiskResult:
    decision: str  # pass | approval_required | hard_block
    reasons: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _load_rules() -> dict:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        # 无 PyYAML 时用内置默认
        return {
            "sensitive_fields": [
                {"name": "idnum", "keywords": ["身份证", "证件号", "idnum"]},
                {"name": "phone_no", "keywords": ["手机号", "电话"]},
            ],
            "explain_scan": {"threshold": 1_000_000, "action": "hard_block"},
            "amount_field_keywords": ["金额", "本金", "余额", "amount", "bal"],
            "aggregation_trigger": {"enabled": True},
            "export_trigger": {"enabled": True},
        }


def assess_risk(
    *,
    sql: str,
    question: str = "",
    plan_confidence: float = 1.0,
    approval_enabled: bool | None = None,
    estimated_rows: int | None = None,
    dialect: str = "mysql",
) -> RiskResult:
    if approval_enabled is None:
        approval_enabled = os.getenv("AGENT_APPROVAL_ENABLED", "1").strip() not in (
            "0",
            "false",
            "off",
            "no",
        )
    rules = _load_rules()
    reasons: list[str] = []
    hard: list[str] = []

    upper = (sql or "").upper()
    if "INTO OUTFILE" in upper or "INTO DUMPFILE" in upper:
        hard.append("禁止 SELECT … INTO OUTFILE/DUMPFILE")

    sqlsvc = SqlDialect(dialect)
    expr = None
    try:
        expr = sqlsvc.parse(sql, dialect)
    except Exception:
        expr = None

    sensitive_fields = {str(f.get("name") or "").lower() for f in rules.get("sensitive_fields") or []}
    if expr is not None:
        for _tbl, col in sqlsvc.extract_columns(expr):
            if col.lower() in sensitive_fields:
                reasons.append(f"引用敏感字段 {col}")
    if not reasons:
        kw_hits = []
        blob = f"{question}\n{sql}"
        for f in rules.get("sensitive_fields") or []:
            for kw in f.get("keywords") or []:
                if kw and kw in blob:
                    kw_hits.append(kw)
        if kw_hits:
            reasons.append(f"查询涉及敏感信息: {', '.join(sorted(set(kw_hits)))}")

    scan = rules.get("explain_scan") or {}
    threshold = int(scan.get("threshold") or 1_000_000)
    if estimated_rows is not None and estimated_rows > threshold:
        reason = f"EXPLAIN 预估行数 {estimated_rows} 超过阈值 {threshold}"
        if scan.get("action") == "approval_required":
            reasons.append(reason)
        else:
            hard.append(reason)

    amount_kw = [str(x) for x in (rules.get("amount_field_keywords") or [])]
    if expr is not None and amount_kw:
        for _tbl, col in sqlsvc.extract_columns(expr):
            hit = any(k.lower() in col.lower() for k in amount_kw)
            if not hit:
                continue
            if (rules.get("aggregation_trigger") or {}).get("enabled", True) and sqlsvc.is_column_in_aggregate(
                expr, col
            ):
                reasons.append(f"金额字段 {col} 参与聚合")
            if (rules.get("export_trigger") or {}).get("enabled", True) and sqlsvc.is_select_column(
                expr, None, col
            ):
                reasons.append(f"导出金额字段 {col}")

    if plan_confidence < 0.45:
        reasons.append("低置信度查询,需人工确认")

    reasons = list(dict.fromkeys([*hard, *reasons]))
    if hard:
        return RiskResult(decision="hard_block", reasons=reasons, blocked_reason="; ".join(hard))
    if reasons and approval_enabled:
        return RiskResult(decision="approval_required", reasons=reasons)
    if reasons and not approval_enabled:
        return RiskResult(decision="pass", reasons=[*reasons, "审批开关关闭，软风险已放行"])
    return RiskResult(decision="pass", reasons=[])
