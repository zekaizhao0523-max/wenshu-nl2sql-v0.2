#!/usr/bin/env python3
"""无库单测：语义图、澄清闸门、槽位派生检索。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wenshu.services.keyword_llm import extract_roles_resolved  # noqa: E402
from wenshu.services.query_clarify import (  # noqa: E402
    detect_clarify_questions,
    merge_clarify_answers,
    prepare_query,
)
from wenshu.services.query_intent import (  # noqa: E402
    SemanticGraph,
    build_semantic_graph,
    parse_query_intent_rule,
    resolve_query_intent,
)
from wenshu.services.retrieval_lexicon_seed import (  # noqa: E402
    SEED_BUSINESS_CONCEPTS,
    SEED_CROSS_TABLE_ATTRIBUTE_KEYS,
    SEED_ENTITY_CONCEPT_KEYS,
    SEED_FACT_TABLES_REQUIRE_HINT,
)
from wenshu.services.schema_retrieval import install_retrieval_lexicon  # noqa: E402

install_retrieval_lexicon(
    concepts=SEED_BUSINESS_CONCEPTS,
    entity_concepts=SEED_ENTITY_CONCEPT_KEYS,
    cross_table_attributes=SEED_CROSS_TABLE_ATTRIBUTE_KEYS,
    fact_need_hint=SEED_FACT_TABLES_REQUIRE_HINT,
)


def test_claim_customer_is_fact_filter_multi() -> None:
    q = "统计逾期追偿本金超过10000的个人客户姓名"
    intent = parse_query_intent_rule(q)
    assert intent.query_type == "fact_filter", intent
    assert any("个人客户" in s.text or s.text == "客户" for s in intent.entities)
    assert any("追偿" in s.text or "本金" in s.text for s in intent.measures) or intent.filters
    assert intent.force_multi_table is True
    assert "个人客户" in intent.table_phrases or any("客户" in p for p in intent.table_phrases)


def test_single_entity_attributes_not_forced_multi() -> None:
    q = "个人客户婚姻状况和性别分布"
    intent = parse_query_intent_rule(q)
    assert intent.force_multi_table is False
    assert intent.query_type in {"attribute_lookup", "aggregation"}
    assert "个人客户" in intent.table_phrases


def test_loan_and_product_force_multi() -> None:
    q = "各借据的产品名称和贷款本金余额"
    intent = parse_query_intent_rule(q)
    assert intent.force_multi_table is True
    assert any("借据" in p for p in intent.table_phrases)
    assert "产品名称" in intent.column_phrases


def test_existence_adds_join_phrase() -> None:
    q = "有没有发生过逾期还款的个人客户"
    intent = parse_query_intent_rule(q)
    assert intent.query_type == "existence"
    assert intent.force_multi_table is True
    assert intent.join_phrases


def test_comparison_becomes_filter_phrase() -> None:
    q = "年龄大于30的个人客户有多少"
    intent = parse_query_intent_rule(q)
    assert intent.query_type in {"fact_filter", "aggregation"}
    assert intent.filters
    assert intent.filters[0].operator == ">"
    assert intent.filters[0].value == 30
    assert any("30" in p for p in intent.filter_phrases)


def test_roles_absorb_intent_slots() -> None:
    q = "统计逾期追偿本金超过10000的个人客户姓名"
    roles = extract_roles_resolved(q, mode="rule", use_cache=False)
    assert roles.intent.get("query_type") == "fact_filter"
    assert roles.semantic_graph.get("query_type") == "fact_filter"
    assert roles.force_multi_table is True
    assert any("个人客户" in p or "客户" in p for p in roles.table_phrases)
    assert roles.filter_phrases


def test_resolve_default_is_rule() -> None:
    intent = resolve_query_intent("列出产品编码", mode="rule", use_cache=False)
    assert intent.source == "rule"
    assert intent.query_type in {"attribute_lookup", "event_detail", "unknown"}


def test_semantic_graph_stable_structure() -> None:
    graph = build_semantic_graph("年龄大于30的个人客户有多少", mode="rule", use_cache=False)
    assert isinstance(graph, SemanticGraph)
    d = graph.as_dict()
    for key in (
        "query_type",
        "query_action",
        "entities",
        "measures",
        "table_phrases",
        "column_phrases",
        "filter_phrases",
        "version",
        "type_label",
    ):
        assert key in d


def test_relative_time_triggers_clarify() -> None:
    graph = build_semantic_graph("最近个人客户数量统计", mode="rule", use_cache=False)
    qs = detect_clarify_questions(graph, question="最近个人客户数量统计")
    assert any(q.slot == "time_range" for q in qs)


def test_complete_question_no_clarify() -> None:
    q = "年龄大于30的个人客户有多少"
    prepared = prepare_query(q, gate=True)
    assert prepared.status == "ok"


def test_clarify_gate_blocks_until_answered() -> None:
    q = "最近个人客户数量统计"
    first = prepare_query(q, gate=True)
    assert first.status == "need_clarify"
    assert first.session_id
    second = prepare_query(
        q,
        gate=True,
        session_id=first.session_id,
        clarify_answers={"time_range": "不限定时间，查全量"},
    )
    assert second.status == "ok"


def test_merge_clarify_into_evidence() -> None:
    q, ev = merge_clarify_answers("统计GMV", "", {"time_range": "2024年全年"})
    assert q == "统计GMV"
    assert "2024年全年" in ev


if __name__ == "__main__":
    tests = [
        test_claim_customer_is_fact_filter_multi,
        test_single_entity_attributes_not_forced_multi,
        test_loan_and_product_force_multi,
        test_existence_adds_join_phrase,
        test_comparison_becomes_filter_phrase,
        test_roles_absorb_intent_slots,
        test_resolve_default_is_rule,
        test_semantic_graph_stable_structure,
        test_relative_time_triggers_clarify,
        test_complete_question_no_clarify,
        test_clarify_gate_blocks_until_answered,
        test_merge_clarify_into_evidence,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
