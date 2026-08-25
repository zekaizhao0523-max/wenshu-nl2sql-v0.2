"""业务概念层：同义词只映射到概念名，字段/表对齐交给向量与 LLM。"""

from __future__ import annotations

from typing import Any


def normalize_concepts(raw: dict[str, tuple[str, ...] | list[str]] | None) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for key, vals in (raw or {}).items():
        concept = str(key or "").strip()
        if not concept:
            continue
        items: list[str] = []
        seen: set[str] = set()
        for item in [concept, *(vals or ())]:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        if items:
            out[concept] = tuple(items)
    return out


def alias_to_concept_map(concepts: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """更长别名优先，避免「借据号」被「借据」抢先占用。"""
    pairs: list[tuple[str, str]] = []
    for concept, aliases in concepts.items():
        for alias in aliases:
            text = str(alias or "").strip()
            if text:
                pairs.append((text, concept))
    pairs.sort(key=lambda x: (-len(x[0]), x[0]))
    out: dict[str, str] = {}
    for alias, concept in pairs:
        if alias not in out:
            out[alias] = concept
    return out


def match_phrases_longest_first(
    text: str,
    phrases: list[str] | tuple[str, ...] | dict[str, Any],
) -> list[str]:
    q = text or ""
    if not q:
        return []
    occupied = [False] * len(q)
    hits: list[str] = []
    for phrase in sorted(phrases, key=len, reverse=True):
        start = 0
        while True:
            i = q.find(phrase, start)
            if i < 0:
                break
            j = i + len(phrase)
            if any(occupied[i:j]):
                start = i + 1
                continue
            for k in range(i, j):
                occupied[k] = True
            hits.append(phrase)
            break
    return hits


def matched_concepts_from_text(
    text: str,
    *,
    concepts: dict[str, tuple[str, ...]],
    alias_to_concept: dict[str, str] | None = None,
) -> list[str]:
    alias_map = alias_to_concept or alias_to_concept_map(concepts)
    if not text or not alias_map:
        return []
    matched_aliases = match_phrases_longest_first(text, alias_map)
    out: list[str] = []
    seen: set[str] = set()
    for alias in matched_aliases:
        concept = alias_map.get(alias)
        if not concept or concept in seen:
            continue
        seen.add(concept)
        out.append(concept)
    return out


def concept_terms(concept_key: str, concepts: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    return concepts.get(concept_key) or (concept_key,)


def concept_search_phrases(
    concept_keys: list[str],
    concepts: dict[str, tuple[str, ...]],
    *,
    limit: int = 12,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for key in concept_keys:
        for term in concept_terms(key, concepts):
            low = term.lower()
            if low in seen:
                continue
            seen.add(low)
            out.append(term)
            if len(out) >= limit:
                return out
    return out


def concept_in_text(concept_key: str, text: str, concepts: dict[str, tuple[str, ...]]) -> bool:
    blob = (text or "").lower()
    if not blob:
        return False
    for term in concept_terms(concept_key, concepts):
        if term.lower() in blob:
            return True
    return False


def column_meta_search_text(column_name: str, meta: dict | None) -> str:
    meta = meta or {}
    syns = meta.get("synonyms") or ""
    parts = " ".join(str(column_name or "").replace("_", " ").split())
    return " ".join(
        [
            str(column_name or "").lower(),
            parts.lower(),
            str(meta.get("description") or "").lower(),
            str(meta.get("hive_comment") or "").lower(),
            str(syns).lower(),
        ]
    ).strip()


def column_matches_concept(
    concept_key: str,
    column_name: str,
    meta: dict | None,
    concepts: dict[str, tuple[str, ...]],
) -> bool:
    """只在列说明/注释/同义词 JSON 里匹配概念，不对英文字段名做硬绑。"""
    text = column_meta_search_text(column_name, meta)
    return concept_in_text(concept_key, text, concepts)


def table_meta_search_text(table_meta: dict | None) -> str:
    meta = table_meta or {}
    return " ".join(
        [
            str(meta.get("table_name") or ""),
            str(meta.get("cn_name") or ""),
            str(meta.get("description") or ""),
            str(meta.get("sample_questions") or ""),
        ]
    ).lower()


def table_matches_concept(
    concept_key: str,
    table_meta: dict | None,
    concepts: dict[str, tuple[str, ...]],
) -> bool:
    return concept_in_text(concept_key, table_meta_search_text(table_meta), concepts)


def build_concept_hint_block(
    concept_keys: list[str],
    concepts: dict[str, tuple[str, ...]],
    *,
    question: str = "",
) -> str:
    if not concept_keys:
        return ""
    lines = ["【术语说明】以下词汇在本系统中指同一业务概念，请结合列含义选择字段："]
    for key in concept_keys:
        aliases = concept_terms(key, concepts)
        others = [a for a in aliases if a != key]
        if others:
            lines.append(f"- {key}：{', '.join(others)}")
        else:
            lines.append(f"- {key}")
    q = (question or "").strip()
    disambig: list[str] = []
    if any(k in concept_keys for k in ("逾期追偿", "逾期还款", "追偿")) and "还款计划" not in q:
        disambig.append(
            "「逾期追偿/追偿本金」选逾期追偿表字段，不要选还款计划应还字段。"
        )
    if any(k in concept_keys for k in ("应还本金", "应还利息", "应还总金额")) and not any(
        k in concept_keys for k in ("逾期追偿", "追偿")
    ):
        disambig.append(
            "「应还/计划」类字段优先还款计划表；实还类字段在还款明细表。"
        )
    if "对公客户" in concept_keys or (
        q and any(x in q for x in ("对公", "企业客户", "职工人数"))
    ):
        disambig.append("对公/企业客户字段在对公客户表，不要选个人客户表。")
    if "个人客户" in concept_keys and any(x in q for x in ("对公", "企业", "职工人数")):
        disambig.append("问句明确对公/企业时，不要选个人客户表字段。")
    if "产品名称" in concept_keys:
        disambig.append("「产品名称」选产品表 prd_name，不是产品编码 prd_code。")
    if "审批授信额度" in concept_keys or "申请用信额度" in concept_keys:
        disambig.append(
            "授信额度在授信申请表，用信/申请金额在用信申请表；客户主档属性在个人客户表。"
        )
    if disambig:
        lines.append("【易混淆提示】")
        lines.extend(f"- {item}" for item in disambig)
    lines.append(
        "⚠️ 不要强行把问句改写成近义词；有具体日期/对象时，代词应绑定问句里已出现的实体。"
    )
    return "\n".join(lines)
