"""Schema 召回关键词抽取：分角色 JSON（LLM）+ 规则回退。"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

from wenshu.services.comment_llm import _call_llm_json, llm_available

KeywordMode = Literal["auto", "llm", "rule"]

_KEYWORD_CACHE: dict[str, "RoleKeywords"] = {}
_KEYWORD_CACHE_MAX = 512

_LLM_STOPWORDS = frozenset(
    {
        "多少",
        "哪些",
        "什么",
        "怎么",
        "如何",
        "查询",
        "查",
        "统计",
        "列出",
        "显示",
        "获取",
        "分别",
        "各",
        "每",
        "所有",
        "全部",
        "汇总",
        "分布",
        "情况",
        "信息",
        "数据",
        "记录",
        "明细",
        "列表",
        "报表",
        "报告",
        "请问",
        "帮我",
        "一下",
    }
)

_JOIN_PHRASE_MARKERS = (
    "及其",
    "对应",
    "关联",
    "联查",
    "分别对应",
    "各表",
    "跨表",
    "join",
    "以及",
)


@dataclass
class RoleKeywords:
    """分角色检索词：表 / 列 / 过滤 / 关系 + 扁平 keywords（兼容旧逻辑）。"""

    table_phrases: list[str] = field(default_factory=list)
    column_phrases: list[str] = field(default_factory=list)
    filter_phrases: list[str] = field(default_factory=list)
    join_phrases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    source: str = "rule"
    intent: dict = field(default_factory=dict)
    semantic_graph: dict = field(default_factory=dict)
    force_multi_table: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def resolve_keyword_mode(mode: str | None = None) -> KeywordMode:
    raw = (mode or os.getenv("SCHEMA_KEYWORD_MODE") or "auto").strip().lower()
    if raw in ("llm", "rule"):
        return raw  # type: ignore[return-value]
    return "auto"


def llm_keyword_available() -> bool:
    return llm_available()


def _cache_key(question: str, evidence: str, mode: KeywordMode) -> str:
    raw = f"roles_v2\n{mode}\n{question}\n{evidence}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize_keyword_list(raw: list | None, *, limit: int = 24) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        kw = item.strip()
        if not kw:
            continue
        key = kw.lower()
        if key in seen or key in _LLM_STOPWORDS:
            continue
        if len(kw) == 1 and not re.match(r"[a-z0-9]", kw, re.I):
            continue
        seen.add(key)
        out.append(kw)
    return out[:limit]


def _merge_keyword_lists(primary: list[str], secondary: list[str], *, limit: int = 24) -> list[str]:
    """LLM 结果为主，规则侧较长短语补漏。"""
    seen = {k.lower() for k in primary}
    out = list(primary)
    for kw in secondary:
        kl = kw.lower()
        if kl in seen:
            continue
        if len(kw) >= 3 or re.match(r"[a-z0-9_]{2,}", kw, re.I):
            seen.add(kl)
            out.append(kw)
    return out[:limit]


def _flatten_roles(
    table_phrases: list[str],
    column_phrases: list[str],
    filter_phrases: list[str],
    join_phrases: list[str],
    *,
    extra: list[str] | None = None,
) -> list[str]:
    """扁平 keywords：列 > 表 > 过滤 > 关系，去重。"""
    ordered: list[str] = []
    for bucket in (column_phrases, table_phrases, filter_phrases, join_phrases, extra or []):
        ordered.extend(bucket)
    return _normalize_keyword_list(ordered, limit=24)


def _build_llm_prompt(question: str, evidence: str) -> str:
    ev_block = ""
    if evidence and evidence.strip():
        ev_block = f"\nEvidence（业务补充说明）：\n{evidence.strip()}\n"
    return f"""从下列 Text-to-SQL 问题中按语义角色拆解，用于检索数据库表/字段/JOIN。

用户问题：
{question.strip()}
{ev_block}
角色说明：
- table_phrases：业务实体/表语义（如个人客户、借据、还款明细、迁徙率）
- column_phrases：指标与维度字段语义（如实还本金、性别、逾期天数、mob3）
- filter_phrases：过滤条件值或条件词（如本科、2024年、大于30、拒绝）
- join_phrases：多表关联描述（如对应、及其、关联）；无多表则空数组
- keywords：上述合并去重后的检索词（列优先）

规则：
- 保留完整短语，勿拆成无意义单字
- 多表题每个实体分开写入 table_phrases
- 不要输出「多少、查询、统计、哪些、列出」等泛化词
- 英文术语保留（如 vintage30、mob3、cust_id）

【重要】只输出一行 JSON，字段必须齐全：
{{"table_phrases":[],"column_phrases":[],"filter_phrases":[],"join_phrases":[],"keywords":[]}}"""


def _parse_role_lists(data: dict) -> RoleKeywords | None:
    if not isinstance(data, dict):
        return None

    table = _normalize_keyword_list(data.get("table_phrases"), limit=12)
    column = _normalize_keyword_list(data.get("column_phrases"), limit=16)
    filters = _normalize_keyword_list(data.get("filter_phrases"), limit=12)
    joins = _normalize_keyword_list(data.get("join_phrases"), limit=8)

    flat_raw = data.get("keywords")
    if flat_raw is None:
        for alt in ("entities", "terms", "keys", "keyword"):
            if isinstance(data.get(alt), list):
                flat_raw = data[alt]
                break
    flat = _normalize_keyword_list(flat_raw if isinstance(flat_raw, list) else None)

    # 若只有 flat keywords（旧模型），全部当作 column 路兜底
    if not (table or column or filters or joins):
        if flat:
            return RoleKeywords(
                table_phrases=[],
                column_phrases=flat,
                filter_phrases=[],
                join_phrases=[],
                keywords=flat,
                source="llm",
            )
        # 再兜底：任意字符串列表字段
        for val in data.values():
            if isinstance(val, list) and val and all(isinstance(x, str) for x in val):
                flat2 = _normalize_keyword_list(val)
                if flat2:
                    return RoleKeywords(
                        column_phrases=flat2,
                        keywords=flat2,
                        source="llm",
                    )
        return None

    keywords = flat or _flatten_roles(table, column, filters, joins)
    return RoleKeywords(
        table_phrases=table,
        column_phrases=column,
        filter_phrases=filters,
        join_phrases=joins,
        keywords=keywords,
        source="llm",
    )


def extract_roles_llm(
    question: str,
    evidence: str = "",
    *,
    timeout: int | None = None,
) -> RoleKeywords | None:
    """LLM 分角色抽词；失败返回 None。"""
    q = (question or "").strip()
    if not q:
        return RoleKeywords(source="llm")
    if not llm_keyword_available():
        return None

    if timeout is None:
        try:
            timeout = int(os.getenv("SCHEMA_KEYWORD_LLM_TIMEOUT", "60"))
        except ValueError:
            timeout = 60

    data = _call_llm_json(_build_llm_prompt(q, evidence), timeout=timeout)
    if not data:
        return None
    return _parse_role_lists(data)


def extract_roles_rule(question: str, evidence: str = "") -> RoleKeywords:
    """规则分角色：基于业务概念词典 + 关联词 + 通用切词。"""
    from wenshu.services.business_concept import concept_search_phrases, concept_terms
    from wenshu.services.schema_retrieval import (
        _concepts,
        _entity_concepts,
        extract_query_keywords,
        question_matched_concepts,
        question_matched_entity_concepts,
    )

    parts = [question or ""]
    if evidence and str(evidence).strip():
        parts.append(str(evidence).strip())
    q = "\n".join(parts).strip().lower()

    concept_map = _concepts()
    matched = question_matched_concepts(question, evidence)
    entity_matched = question_matched_entity_concepts(question, evidence)

    table_phrases: list[str] = []
    column_phrases: list[str] = []
    for concept in entity_matched:
        table_phrases.extend(concept_terms(concept, concept_map)[:4])
    for concept in matched:
        column_phrases.extend(concept_terms(concept, concept_map)[:4])
    column_phrases.extend(
        concept_search_phrases(matched, concept_map, limit=10)
    )

    join_phrases: list[str] = []
    for marker in _JOIN_PHRASE_MARKERS:
        if marker in q:
            join_phrases.append(marker)

    filter_phrases: list[str] = []
    for m in re.findall(r"\d{4}\s*年|\d+\s*期|大于\s*\d+|小于\s*\d+|超过\s*\d+", q):
        filter_phrases.append(m.replace(" ", ""))

    flat = extract_query_keywords(question, evidence)
    hinted = {x.lower() for x in table_phrases + column_phrases + join_phrases + filter_phrases}
    flat_clean: list[str] = []
    alias_map = {a.lower(): c for c, aliases in concept_map.items() for a in aliases}
    for kw in flat:
        kl = kw.lower()
        if kl in hinted or kw in _LLM_STOPWORDS:
            continue
        if any(m in kw for m in _JOIN_PHRASE_MARKERS):
            continue
        if kw in alias_map:
            concept = alias_map[kw]
            if concept in _entity_concepts():
                table_phrases.append(kw)
            else:
                column_phrases.append(kw)
            flat_clean.append(kw)
            continue
        if re.match(r"^[a-z][a-z0-9_]{1,}$", kl) or 2 <= len(kw) <= 6:
            column_phrases.append(kw)
            flat_clean.append(kw)

    table_phrases = _normalize_keyword_list(table_phrases, limit=12)
    column_phrases = _normalize_keyword_list(column_phrases, limit=16)
    filter_phrases = _normalize_keyword_list(filter_phrases, limit=12)
    join_phrases = _normalize_keyword_list(join_phrases, limit=8)
    keywords = _flatten_roles(
        table_phrases, column_phrases, filter_phrases, join_phrases, extra=flat_clean
    )

    return RoleKeywords(
        table_phrases=table_phrases,
        column_phrases=column_phrases,
        filter_phrases=filter_phrases,
        join_phrases=join_phrases,
        keywords=keywords,
        source="rule",
    )


def _merge_roles(primary: RoleKeywords, secondary: RoleKeywords) -> RoleKeywords:
    table = _merge_keyword_lists(primary.table_phrases, secondary.table_phrases, limit=12)
    column = _merge_keyword_lists(primary.column_phrases, secondary.column_phrases, limit=16)
    filters = _merge_keyword_lists(primary.filter_phrases, secondary.filter_phrases, limit=12)
    joins = _merge_keyword_lists(primary.join_phrases, secondary.join_phrases, limit=8)
    keywords = _merge_keyword_lists(primary.keywords, secondary.keywords, limit=24)
    if not keywords:
        keywords = _flatten_roles(table, column, filters, joins)
    src = primary.source
    if secondary.source == "rule" and primary.source.startswith("llm"):
        src = "llm+rule"
    return RoleKeywords(
        table_phrases=table,
        column_phrases=column,
        filter_phrases=filters,
        join_phrases=joins,
        keywords=keywords,
        source=src,
    )


def extract_roles_resolved(
    question: str,
    evidence: str = "",
    *,
    mode: str | None = None,
    use_cache: bool = True,
) -> RoleKeywords:
    """
    按模式抽取分角色关键词。
    source: llm | rule | llm+rule
    """
    resolved_mode = resolve_keyword_mode(mode)
    cache_key = _cache_key(question, evidence, resolved_mode)
    if use_cache and cache_key in _KEYWORD_CACHE:
        return _KEYWORD_CACHE[cache_key]

    rule_roles = extract_roles_rule(question, evidence)

    if resolved_mode == "rule":
        result = rule_roles
    else:
        llm_roles = extract_roles_llm(question, evidence)
        if llm_roles and (
            llm_roles.keywords
            or llm_roles.table_phrases
            or llm_roles.column_phrases
            or llm_roles.filter_phrases
            or llm_roles.join_phrases
        ):
            result = _merge_roles(llm_roles, rule_roles)
        else:
            result = rule_roles

    from wenshu.services.query_intent import apply_intent_to_roles, build_semantic_graph

    graph = build_semantic_graph(question, evidence, mode=mode, use_cache=use_cache)
    result = apply_intent_to_roles(result, graph)

    if use_cache:
        if len(_KEYWORD_CACHE) >= _KEYWORD_CACHE_MAX:
            _KEYWORD_CACHE.pop(next(iter(_KEYWORD_CACHE)))
        _KEYWORD_CACHE[cache_key] = result
    return result


def extract_keywords_llm(
    question: str,
    evidence: str = "",
    *,
    timeout: int | None = None,
) -> list[str] | None:
    """兼容旧接口：仅返回扁平 keywords。"""
    roles = extract_roles_llm(question, evidence, timeout=timeout)
    if roles is None:
        return None
    return roles.keywords or None


def extract_keywords_resolved(
    question: str,
    evidence: str = "",
    *,
    mode: str | None = None,
    use_cache: bool = True,
) -> tuple[list[str], str]:
    """
    兼容旧接口。
    返回 (keywords, source)，source 为 llm | rule | llm+rule。
    """
    roles = extract_roles_resolved(
        question, evidence, mode=mode, use_cache=use_cache
    )
    return roles.keywords, roles.source
