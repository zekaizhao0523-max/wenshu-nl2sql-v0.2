"""问数意图识别：先定查询结构，再派生召回短语。

不替代 keyword_llm 抽词。本模块只做两件事：
1. 判定 query_type / 槽位（实体、度量、过滤、输出属性）
2. 把槽位变成表/列/过滤/关系短语，并在跨表槽位时禁止单表收口
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from wenshu.services.comment_llm import _call_llm_json, llm_available

IntentMode = Literal["auto", "llm", "rule"]
QueryType = Literal[
    "attribute_lookup",
    "fact_filter",
    "aggregation",
    "event_detail",
    "multi_fact",
    "existence",
    "unknown",
]
QueryAction = Literal["lookup", "detail", "aggregate", "rank", "unknown"]

_INTENT_CACHE: dict[str, "QueryIntent"] = {}
_INTENT_CACHE_MAX = 512

_GENERIC_ENTITIES = (
    "个人客户",
    "对公客户",
    "企业客户",
    "客户",
    "借据",
    "合同",
    "产品",
    "还款计划",
    "还款明细",
    "还款",
    "代偿",
    "追偿",
    "授信",
    "用信",
    "逾期",
)

_AGG_WORDS = ("统计", "合计", "汇总", "平均", "总计", "数量", "多少", "占比", "分布", "排名")
_RANK_WORDS = ("排名", "前", "top", "最高", "最低")
_DETAIL_WORDS = ("明细", "清单", "逐笔", "列表")
_EXIST_WORDS = ("有没有", "是否存在", "有无", "不存在", "未发生", "没有发生", "发生过")
_LOOKUP_MARKERS = ("基本信息", "详细信息", "联系方式", "是谁", "叫什么")
_JOIN_MARKERS = ("及其", "对应", "关联", "联查", "分别对应", "各表", "跨表", "join")

_COMPARISON_RE = re.compile(
    r"(?P<field>[\u4e00-\u9fffA-Za-z0-9_]{2,20}?)"
    r"(?P<word>超过|大于等于|小于等于|不低于|不高于|不少于|不多于|大于|小于|高于|低于|至少|至多|等于)"
    r"(?P<value>\d+(?:\.\d+)?)"
)
_TEXT_FILTER_RE = re.compile(
    r"(?P<field>[\u4e00-\u9fff]{2,12}?)(?:为|是|等于)(?P<value>[\u4e00-\u9fffA-Za-z0-9]{2,12})"
)
_YEAR_RE = re.compile(r"\d{4}\s*年")
_NUM_FILTER_RE = re.compile(r"(?:大于|小于|超过|满)\s*\d+")

_COMPARISONS = {
    "超过": ">",
    "大于": ">",
    "高于": ">",
    "大于等于": ">=",
    "不低于": ">=",
    "不少于": ">=",
    "至少": ">=",
    "小于": "<",
    "低于": "<",
    "小于等于": "<=",
    "不高于": "<=",
    "不多于": "<=",
    "至多": "<=",
    "等于": "=",
}

_QUERY_TYPE_LABELS = {
    "attribute_lookup": "查属性",
    "fact_filter": "条件筛选",
    "aggregation": "统计汇总",
    "event_detail": "查明细",
    "multi_fact": "多指标",
    "existence": "是否存在",
    "unknown": "未判定",
}


@dataclass
class IntentSlot:
    text: str
    role: Literal["entity", "measure", "attribute", "dimension", "filter"]
    operator: str | None = None
    value: Any = None


@dataclass
class QueryIntent:
    query_type: QueryType = "unknown"
    query_action: QueryAction = "unknown"
    entities: list[IntentSlot] = field(default_factory=list)
    measures: list[IntentSlot] = field(default_factory=list)
    attributes: list[IntentSlot] = field(default_factory=list)
    filters: list[IntentSlot] = field(default_factory=list)
    dimensions: list[IntentSlot] = field(default_factory=list)
    table_phrases: list[str] = field(default_factory=list)
    column_phrases: list[str] = field(default_factory=list)
    filter_phrases: list[str] = field(default_factory=list)
    join_phrases: list[str] = field(default_factory=list)
    force_multi_table: bool = False
    source: str = "rule"
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def type_label(self) -> str:
        return _QUERY_TYPE_LABELS.get(self.query_type, self.query_type)


@dataclass
class SemanticGraph:
    """结构化业务语义：召回与 SQL 下游应遵守的槽位事实。"""

    query_type: QueryType = "unknown"
    query_action: QueryAction = "unknown"
    entities: list[IntentSlot] = field(default_factory=list)
    measures: list[IntentSlot] = field(default_factory=list)
    attributes: list[IntentSlot] = field(default_factory=list)
    filters: list[IntentSlot] = field(default_factory=list)
    dimensions: list[IntentSlot] = field(default_factory=list)
    table_phrases: list[str] = field(default_factory=list)
    column_phrases: list[str] = field(default_factory=list)
    filter_phrases: list[str] = field(default_factory=list)
    join_phrases: list[str] = field(default_factory=list)
    force_multi_table: bool = False
    source: str = "rule"
    confidence: float = 0.0
    version: str = "semantic_v1"

    @classmethod
    def from_intent(cls, intent: QueryIntent) -> SemanticGraph:
        return cls(
            query_type=intent.query_type,
            query_action=intent.query_action,
            entities=list(intent.entities),
            measures=list(intent.measures),
            attributes=list(intent.attributes),
            filters=list(intent.filters),
            dimensions=list(intent.dimensions),
            table_phrases=list(intent.table_phrases),
            column_phrases=list(intent.column_phrases),
            filter_phrases=list(intent.filter_phrases),
            join_phrases=list(intent.join_phrases),
            force_multi_table=bool(intent.force_multi_table),
            source=intent.source,
            confidence=float(intent.confidence),
        )

    def as_dict(self) -> dict:
        d = asdict(self)
        d["type_label"] = _QUERY_TYPE_LABELS.get(self.query_type, self.query_type)
        return d

    @property
    def type_label(self) -> str:
        return _QUERY_TYPE_LABELS.get(self.query_type, self.query_type)


def build_semantic_graph(
    question: str,
    evidence: str = "",
    *,
    mode: str | None = None,
    use_cache: bool = True,
) -> SemanticGraph:
    """问句 → 语义图（意图识别 + 槽位派生短语）。"""
    intent = resolve_query_intent(question, evidence, mode=mode, use_cache=use_cache)
    graph = SemanticGraph.from_intent(intent)
    graph.table_phrases = _unique(
        [s.text for s in graph.entities] + graph.table_phrases, limit=12
    )
    graph.column_phrases = _unique(
        [s.text for s in graph.measures]
        + [s.text for s in graph.attributes]
        + [s.text for s in graph.dimensions]
        + graph.column_phrases,
        limit=16,
    )
    graph.filter_phrases = _unique(
        [
            *(f"{s.text}{s.operator}{s.value}" if s.operator and s.value is not None else s.text for s in graph.filters),
            *graph.filter_phrases,
        ],
        limit=12,
    )
    return graph


def derive_role_phrases_from_graph(graph: SemanticGraph | QueryIntent) -> dict[str, list[str]]:
    """槽位 → 分路检索短语（语义图优先）。"""
    return {
        "table_phrases": list(getattr(graph, "table_phrases", []) or []),
        "column_phrases": list(getattr(graph, "column_phrases", []) or []),
        "filter_phrases": list(getattr(graph, "filter_phrases", []) or []),
        "join_phrases": list(getattr(graph, "join_phrases", []) or []),
        "force_multi_table": bool(getattr(graph, "force_multi_table", False)),
    }


def resolve_intent_mode(mode: str | None = None) -> IntentMode:
    raw = (mode or os.getenv("SCHEMA_INTENT_MODE") or "rule").strip().lower()
    if raw in ("llm", "auto", "rule"):
        return raw  # type: ignore[return-value]
    return "rule"


def _unique(items: list[str], *, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _lexicon():
    from wenshu.services.business_concept import concept_terms
    from wenshu.services.schema_retrieval import (
        _alias_to_concept,
        _concepts,
        _entity_concepts,
        _match_phrases_longest_first,
        question_matched_concepts,
        question_matched_entity_concepts,
    )

    return {
        "concepts": _concepts(),
        "alias_to_concept": _alias_to_concept(),
        "entity_concepts": _entity_concepts(),
        "match": _match_phrases_longest_first,
        "matched_concepts": question_matched_concepts,
        "matched_entity_concepts": question_matched_entity_concepts,
        "concept_terms": concept_terms,
    }


def _expand_entity_phrases(entity: str, lex: dict) -> list[str]:
    text = (entity or "").strip()
    if not text:
        return []
    alias_map = lex["alias_to_concept"]
    if text in alias_map:
        concept = alias_map[text]
        return list(lex["concept_terms"](concept, lex["concepts"])[:4])
    hits: list[str] = []
    for phrase in sorted(alias_map, key=len, reverse=True):
        if phrase == text or phrase.endswith(text) or (len(text) >= 2 and phrase.startswith(text)):
            hits.append(phrase)
        if len(hits) >= 4:
            break
    return hits or [text]


def parse_query_intent_rule(question: str, evidence: str = "") -> QueryIntent:
    parts = [question or ""]
    if evidence and str(evidence).strip():
        parts.append(str(evidence).strip())
    q = "\n".join(parts).strip()
    if not q:
        return QueryIntent(source="rule", confidence=0.0)

    lex = _lexicon()
    alias_map = lex["alias_to_concept"]
    match = lex["match"]

    alias_hits = match(q, alias_map)
    entity_hits = [
        a for a in alias_hits if lex["alias_to_concept"].get(a) in lex["entity_concepts"]
    ]
    column_hits = alias_hits

    entities: list[IntentSlot] = []
    seen_ent: set[str] = set()
    for phrase in entity_hits:
        if phrase in seen_ent:
            continue
        seen_ent.add(phrase)
        entities.append(IntentSlot(text=phrase, role="entity"))
    for word in _GENERIC_ENTITIES:
        if word not in q:
            continue
        if any(word in item.text or item.text in word for item in entities):
            continue
        if word in seen_ent:
            continue
        seen_ent.add(word)
        entities.append(IntentSlot(text=word, role="entity"))

    filters: list[IntentSlot] = []
    measures: list[IntentSlot] = []
    for m in _COMPARISON_RE.finditer(q):
        field = m.group("field").strip()
        for prefix in ("统计", "筛选", "查询", "列出", "计算", "汇总"):
            if field.startswith(prefix):
                field = field[len(prefix):]
                break
        if field.endswith("的"):
            field = field[:-1]
        field = field.lstrip("的")
        if not field:
            continue
        op = _COMPARISONS.get(m.group("word"), m.group("word"))
        raw = m.group("value")
        value: Any = float(raw) if "." in raw else int(raw)
        filters.append(IntentSlot(text=field, role="filter", operator=op, value=value))
        if field and field not in {x.text for x in measures}:
            measures.append(IntentSlot(text=field, role="measure"))

    for m in _TEXT_FILTER_RE.finditer(q):
        field = m.group("field").strip()
        if any(x.text == field for x in filters):
            continue
        filters.append(
            IntentSlot(text=field, role="filter", operator="=", value=m.group("value"))
        )

    for phrase in column_hits:
        if phrase in {x.text for x in measures}:
            continue
        if phrase in {x.text for x in entities}:
            continue
        measures.append(IntentSlot(text=phrase, role="measure"))

    attributes: list[IntentSlot] = []
    for marker in _LOOKUP_MARKERS:
        if marker in q:
            attributes.append(IntentSlot(text=marker, role="attribute"))
    for phrase in column_hits:
        if any(tag in phrase for tag in ("名称", "姓名", "编码", "状态", "性别", "婚姻")):
            if phrase not in {x.text for x in attributes}:
                attributes.append(IntentSlot(text=phrase, role="attribute"))

    dimensions: list[IntentSlot] = []
    dim = re.search(r"按(.{1,12}?)(?:统计|汇总|分组|计算)", q)
    if dim is None:
        dim = re.search(r"各(.{1,8}?)(?:客户|产品|机构|借据)", q)
    if dim:
        dimensions.append(IntentSlot(text=dim.group(1), role="dimension"))

    exist_hit = any(w in q for w in _EXIST_WORDS)
    agg_hit = any(w in q for w in _AGG_WORDS)
    detail_hit = any(w in q for w in _DETAIL_WORDS)
    rank_hit = any(w.lower() in q.lower() for w in _RANK_WORDS)

    if exist_hit and (entities or measures):
        query_type: QueryType = "existence"
        query_action: QueryAction = "lookup"
    elif len([x for x in measures if x.text not in {a.text for a in attributes}]) >= 2 and agg_hit:
        query_type = "multi_fact"
        query_action = "aggregate"
    elif filters:
        query_type = "fact_filter"
        query_action = "aggregate" if agg_hit else "detail"
    elif agg_hit or rank_hit:
        query_type = "aggregation"
        query_action = "rank" if rank_hit else "aggregate"
    elif detail_hit:
        query_type = "event_detail"
        query_action = "detail"
    elif entities or attributes:
        query_type = "attribute_lookup"
        query_action = "lookup"
    else:
        query_type = "unknown"
        query_action = "unknown"

    table_phrases: list[str] = []
    for slot in entities:
        table_phrases.extend(_expand_entity_phrases(slot.text, lex))
    table_phrases = _unique(table_phrases + entity_hits, limit=12)

    column_phrases = _unique(
        [x.text for x in measures]
        + [x.text for x in attributes]
        + [x.text for x in dimensions]
        + column_hits,
        limit=16,
    )
    filter_phrases = _unique(
        [
            *(f"{x.text}{x.operator}{x.value}" if x.operator and x.value is not None else x.text for x in filters),
            *(_YEAR_RE.findall(q)),
            *[m.replace(" ", "") for m in _NUM_FILTER_RE.findall(q)],
        ],
        limit=12,
    )
    join_phrases = _unique([m for m in _JOIN_MARKERS if m in q.lower()], limit=8)

    matched = lex["matched_concepts"](question, evidence)
    entity_matched = lex["matched_entity_concepts"](question, evidence)
    from wenshu.services.schema_retrieval import _cross_table_attributes

    measure_concepts = [c for c in matched if c not in entity_matched]
    cross = _cross_table_attributes()

    force_multi = False
    if len(entity_matched) >= 2:
        force_multi = True
    if len(table_phrases) >= 2 and (join_phrases or exist_hit or bool(filters)):
        force_multi = True
    if query_type == "existence" and len(entities) >= 2:
        force_multi = True
        if not join_phrases:
            join_phrases = ["对应"]
    if entity_matched and any(c in cross for c in measure_concepts):
        force_multi = True
        if not join_phrases and query_type in {"fact_filter", "multi_fact", "attribute_lookup", "aggregation"}:
            join_phrases = join_phrases or ["对应"]

    filled = int(bool(entities)) + int(bool(measures or attributes)) + int(query_type != "unknown")
    confidence = min(1.0, 0.35 + 0.2 * filled)
    if filters:
        confidence = min(1.0, confidence + 0.1)

    return QueryIntent(
        query_type=query_type,
        query_action=query_action,
        entities=entities,
        measures=measures,
        attributes=attributes,
        filters=filters,
        dimensions=dimensions,
        table_phrases=table_phrases,
        column_phrases=column_phrases,
        filter_phrases=filter_phrases,
        join_phrases=join_phrases,
        force_multi_table=force_multi,
        source="rule",
        confidence=confidence,
    )


def _intent_prompt(question: str, evidence: str) -> str:
    ev = f"\n业务补充：{evidence.strip()}\n" if evidence and evidence.strip() else ""
    return f"""把问数问题理解成查询意图，不要选物理表名或英文字段名。

用户问题：
{question.strip()}
{ev}
只输出一行 JSON：
{{"query_type":"attribute_lookup|fact_filter|aggregation|event_detail|multi_fact|existence|unknown","query_action":"lookup|detail|aggregate|rank|unknown","entities":[],"measures":[],"attributes":[],"filters":[{{"text":"","operator":">","value":""}}],"dimensions":[]}}

规则：
- query_type 看结构：查属性 / 条件筛选 / 统计 / 明细 / 多指标 / 是否存在
- entities 是业务对象（客户、借据、产品），不是「统计」「查询」
- measures 是金额、余额、率、天数等指标短语
- 统计/列出等动作词不要放进 entities 或 measures
"""


def parse_query_intent_llm(question: str, evidence: str = "") -> QueryIntent | None:
    if not llm_available():
        return None
    q = (question or "").strip()
    if not q:
        return None
    try:
        timeout = int(os.getenv("SCHEMA_INTENT_LLM_TIMEOUT", "45"))
    except ValueError:
        timeout = 45
    data = _call_llm_json(_intent_prompt(q, evidence), timeout=timeout)
    if not isinstance(data, dict):
        return None

    def _texts(key: str) -> list[str]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict) and str(item.get("text") or "").strip():
                out.append(str(item.get("text")).strip())
        return out

    allowed_types = {
        "attribute_lookup",
        "fact_filter",
        "aggregation",
        "event_detail",
        "multi_fact",
        "existence",
        "unknown",
    }
    qtype = str(data.get("query_type") or "unknown")
    if qtype not in allowed_types:
        qtype = "unknown"
    action = str(data.get("query_action") or "unknown")
    if action not in {"lookup", "detail", "aggregate", "rank", "unknown"}:
        action = "unknown"

    filters: list[IntentSlot] = []
    for item in data.get("filters") or []:
        if isinstance(item, str) and item.strip():
            filters.append(IntentSlot(text=item.strip(), role="filter"))
        elif isinstance(item, dict) and str(item.get("text") or "").strip():
            filters.append(
                IntentSlot(
                    text=str(item.get("text")).strip(),
                    role="filter",
                    operator=str(item.get("operator") or "") or None,
                    value=item.get("value"),
                )
            )

    fallback = parse_query_intent_rule(question, evidence)
    llm_intent = QueryIntent(
        query_type=qtype,  # type: ignore[arg-type]
        query_action=action,  # type: ignore[arg-type]
        entities=[IntentSlot(text=t, role="entity") for t in _texts("entities")],
        measures=[IntentSlot(text=t, role="measure") for t in _texts("measures")],
        attributes=[IntentSlot(text=t, role="attribute") for t in _texts("attributes")],
        filters=filters or fallback.filters,
        dimensions=[IntentSlot(text=t, role="dimension") for t in _texts("dimensions")],
        source="llm",
        confidence=0.7 if qtype != "unknown" else 0.4,
    )
    if llm_intent.query_type == "unknown":
        llm_intent.query_type = fallback.query_type
        llm_intent.query_action = fallback.query_action
    if not llm_intent.entities:
        llm_intent.entities = fallback.entities
    if not llm_intent.measures:
        llm_intent.measures = fallback.measures
    if not llm_intent.attributes:
        llm_intent.attributes = fallback.attributes

    merged_q = " ".join(
        [
            question or "",
            *[x.text for x in llm_intent.entities],
            *[x.text for x in llm_intent.measures],
            *[x.text for x in llm_intent.attributes],
        ]
    )
    shaped = parse_query_intent_rule(merged_q, evidence)
    shaped.query_type = llm_intent.query_type
    shaped.query_action = llm_intent.query_action
    shaped.entities = llm_intent.entities or shaped.entities
    shaped.measures = llm_intent.measures or shaped.measures
    shaped.attributes = llm_intent.attributes or shaped.attributes
    shaped.filters = llm_intent.filters or shaped.filters
    shaped.source = "llm+rule"
    shaped.confidence = max(llm_intent.confidence, fallback.confidence)
    return shaped


def resolve_query_intent(
    question: str,
    evidence: str = "",
    *,
    mode: str | None = None,
    use_cache: bool = True,
) -> QueryIntent:
    resolved = resolve_intent_mode(mode)
    cache_key = hashlib.sha256(
        f"intent_v1\n{resolved}\n{question}\n{evidence}".encode("utf-8")
    ).hexdigest()
    if use_cache and cache_key in _INTENT_CACHE:
        return _INTENT_CACHE[cache_key]

    rule_intent = parse_query_intent_rule(question, evidence)
    if resolved == "rule":
        result = rule_intent
    else:
        need_llm = resolved == "llm" or (
            rule_intent.query_type == "unknown"
            or rule_intent.confidence < 0.55
            or (len(rule_intent.entities) >= 2 and not rule_intent.force_multi_table)
        )
        result = rule_intent
        if need_llm:
            llm_intent = parse_query_intent_llm(question, evidence)
            if llm_intent is not None:
                result = llm_intent

    if use_cache:
        if len(_INTENT_CACHE) >= _INTENT_CACHE_MAX:
            _INTENT_CACHE.pop(next(iter(_INTENT_CACHE)))
        _INTENT_CACHE[cache_key] = result
    return result


def apply_intent_to_roles(roles: Any, intent: QueryIntent | SemanticGraph) -> Any:
    """语义图槽位优先并入 RoleKeywords，供 XiYan 分路检索使用。"""
    from wenshu.services.keyword_llm import RoleKeywords, _flatten_roles, _merge_keyword_lists

    if not isinstance(roles, RoleKeywords):
        return roles
    if isinstance(intent, SemanticGraph):
        intent_dict = intent.as_dict()
    else:
        intent_dict = intent.as_dict()
    derived = derive_role_phrases_from_graph(intent)
    roles.table_phrases = _merge_keyword_lists(
        derived["table_phrases"], roles.table_phrases, limit=12
    )
    roles.column_phrases = _merge_keyword_lists(
        derived["column_phrases"], roles.column_phrases, limit=16
    )
    roles.filter_phrases = _merge_keyword_lists(
        derived["filter_phrases"], roles.filter_phrases, limit=12
    )
    roles.join_phrases = _merge_keyword_lists(
        derived["join_phrases"], roles.join_phrases, limit=8
    )
    roles.keywords = _flatten_roles(
        roles.table_phrases,
        roles.column_phrases,
        roles.filter_phrases,
        roles.join_phrases,
        extra=roles.keywords,
    )
    roles.intent = intent_dict
    roles.semantic_graph = intent_dict
    roles.force_multi_table = bool(derived.get("force_multi_table")) or bool(roles.force_multi_table)
    if roles.force_multi_table and "intent" not in roles.source:
        roles.source = f"{roles.source}+intent"
    return roles
