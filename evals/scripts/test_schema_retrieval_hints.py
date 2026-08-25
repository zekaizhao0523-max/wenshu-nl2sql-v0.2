#!/usr/bin/env python3
"""无库单测：业务概念匹配 / 多表判定 / 概念槽位 / JOIN 键。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wenshu.services.retrieval_lexicon_seed import (  # noqa: E402
    SEED_BUSINESS_CONCEPTS,
    SEED_CROSS_TABLE_ATTRIBUTE_KEYS,
    SEED_ENTITY_CONCEPT_KEYS,
    SEED_FACT_TABLES_REQUIRE_HINT,
)
from wenshu.services.column_selection import (  # noqa: E402
    _ensure_column_keys,
    _ensure_join_keys,
)
from wenshu.services.schema_retrieval import (  # noqa: E402
    ColumnHit,
    _VectorHit,
    _looks_multi_table,
    _multi_table_min_per_table,
    _resolve_quota_core_tables,
    _select_columns_with_table_quota,
    column_hints_resolved_on_table,
    hinted_column_keys_from_pool,
    install_retrieval_lexicon,
    is_likely_single_table_question,
    question_hint_tables,
    question_matched_concepts,
    question_matched_entity_concepts,
    resolve_hint_tables,
)

install_retrieval_lexicon(
    concepts=SEED_BUSINESS_CONCEPTS,
    entity_concepts=SEED_ENTITY_CONCEPT_KEYS,
    cross_table_attributes=SEED_CROSS_TABLE_ATTRIBUTE_KEYS,
    fact_need_hint=SEED_FACT_TABLES_REQUIRE_HINT,
)


def test_empty_lexicon_no_concepts() -> None:
    from wenshu.services.schema_retrieval import install_retrieval_lexicon as install

    install(concepts={}, entity_concepts=frozenset())
    assert question_matched_concepts("各借据的产品名称") == []
    install(
        concepts=SEED_BUSINESS_CONCEPTS,
        entity_concepts=SEED_ENTITY_CONCEPT_KEYS,
        fact_need_hint=SEED_FACT_TABLES_REQUIRE_HINT,
    )


def test_longest_phrase_product_name_concept() -> None:
    concepts = question_matched_concepts("产品列表里有哪些产品名称和编码")
    assert "产品名称" in concepts
    assert "产品" in concepts


def test_multi_loan_and_product_name_concepts() -> None:
    q = "各借据的产品名称和贷款本金余额"
    concepts = question_matched_concepts(q)
    assert "借据" in concepts
    assert "产品名称" in concepts
    assert "本金余额" in concepts
    assert _looks_multi_table([], {}, question=q) is True


def test_and_does_not_force_multi() -> None:
    q = "个人客户婚姻状况和性别分布"
    assert is_likely_single_table_question(q) is True
    assert question_hint_tables(q) == []
    assert _looks_multi_table([], {}, question=q) is False


def test_question_hint_tables_no_hard_bind() -> None:
    assert question_hint_tables("各借据的产品名称") == []


def test_column_concept_not_on_loan_table_without_description() -> None:
    loan_meta = {
        "prin_bal": {"column_name": "prin_bal", "description": "贷款本金余额"},
        "prd_code": {"column_name": "prd_code", "description": "产品编码"},
    }
    assert column_hints_resolved_on_table(
        "各借据的产品名称和贷款本金余额", loan_meta, "dwd_ar_loan_info"
    ) is False
    prd_meta = {
        "prd_name": {"column_name": "prd_name", "description": "产品名称"},
        "prd_code": {"column_name": "prd_code", "description": "产品编码"},
    }
    assert column_hints_resolved_on_table(
        "产品列表里有哪些产品名称和编码", prd_meta, "dwd_prd_info"
    ) is True


def test_resolve_hint_tables_vector_only() -> None:
    q = "已婚个人客户各产品用信申请笔数和金额"
    assert question_hint_tables(q) == []
    hits = [
        _VectorHit(
            score=0.41,
            payload={
                "table": "dwd_prd_info",
                "column": "prd_name",
                "xiyan_keyword": "__question__",
                "xiyan_keywords": ["__question__", "table:产品"],
            },
        ),
    ]
    tables = resolve_hint_tables(q, raw_hits=hits)
    assert "dwd_prd_info" in tables
    assert _looks_multi_table(hits, {}, question=q, hint_tables=tables) is True


def test_resolve_hint_tables_skips_ambiguous_vector() -> None:
    hits = [
        _VectorHit(
            score=0.40,
            payload={
                "table": "dwd_prd_info",
                "column": "prd_name",
                "xiyan_keywords": ["table:产品"],
            },
        ),
        _VectorHit(
            score=0.39,
            payload={
                "table": "dwd_ar_loan_info",
                "column": "prd_code",
                "xiyan_keywords": ["table:产品"],
            },
        ),
    ]
    tables = resolve_hint_tables("看看产品", raw_hits=hits)
    assert "dwd_prd_info" not in tables
    assert "dwd_ar_loan_info" not in tables


def test_hinted_column_cover_by_concept_description() -> None:
    pool = [
        ColumnHit(
            db="vectortest",
            table="dwd_ar_loan_info",
            column="loan_no",
            score=0.7,
            source="vector",
            payload={"description": "借据号"},
        ),
        ColumnHit(
            db="vectortest",
            table="dwd_prd_info",
            column="prd_name",
            score=0.4,
            source="vector",
            payload={"description": "产品名称。信贷产品的名称，不是产品编码。"},
        ),
    ]
    keys = hinted_column_keys_from_pool("各借据的产品名称", pool)
    assert ("dwd_prd_info", "prd_name") in keys
    selected = [pool[0]]
    covered = _ensure_column_keys(selected, pool, keys)
    assert any(c.column == "prd_name" for c in covered)


def test_ambiguous_name_not_covered() -> None:
    pool = [
        ColumnHit(
            db="vectortest",
            table="dwd_ip_indv_cust_info",
            column="name",
            score=0.50,
            source="vector",
            payload={"description": "个人客户姓名"},
        ),
        ColumnHit(
            db="vectortest",
            table="dwd_ip_corp_cust_info",
            column="cust_name",
            score=0.49,
            source="vector",
            payload={"description": "企业客户名称"},
        ),
    ]
    keys = hinted_column_keys_from_pool("客户名称", pool)
    assert keys == []


def test_origin_phrase_breaks_name_tie() -> None:
    pool = [
        ColumnHit(
            db="vectortest",
            table="dwd_ip_indv_cust_info",
            column="name",
            score=0.40,
            source="vector",
            payload={
                "description": "个人客户姓名",
                "xiyan_keywords": ["column:姓名"],
            },
        ),
        ColumnHit(
            db="vectortest",
            table="dwd_ip_corp_cust_info",
            column="cust_name",
            score=0.55,
            source="vector",
            payload={"description": "企业客户名称"},
        ),
    ]
    keys = hinted_column_keys_from_pool("客户姓名", pool)
    assert keys == [("dwd_ip_indv_cust_info", "name")]


def test_join_keys_only_on_selected_tables() -> None:
    selected = [
        ColumnHit("vectortest", "dwd_ar_loan_info", "prin_bal", 0.6, "vector"),
        ColumnHit("vectortest", "dwd_ip_indv_cust_info", "name", 0.5, "vector"),
    ]
    pool = selected + [
        ColumnHit("vectortest", "dwd_ar_loan_info", "cust_id", 0.3, "join_key"),
        ColumnHit("vectortest", "dwd_prd_info", "prd_code", 0.3, "join_key"),
    ]
    out = _ensure_join_keys(selected, pool)
    cols = {(c.table, c.column) for c in out}
    assert ("dwd_ar_loan_info", "cust_id") in cols
    assert ("dwd_prd_info", "prd_code") not in cols


def test_resolve_quota_core_tables_merges_hint_and_scores() -> None:
    casing = {
        "dwd_ip_indv_cust_info": "dwd_ip_indv_cust_info",
        "dwd_ev_indv_loan_app": "dwd_ev_indv_loan_app",
        "dwd_prd_info": "dwd_prd_info",
    }
    core = _resolve_quota_core_tables(
        ["dwd_ev_indv_loan_app"],
        ["dwd_ip_indv_cust_info", "dwd_prd_info", "dwd_ar_loan_info"],
        {"dwd_prd_info": 0.99},
        casing_map=casing,
        max_tables=3,
    )
    assert core == [
        "dwd_ev_indv_loan_app",
        "dwd_ip_indv_cust_info",
        "dwd_prd_info",
    ]


def test_multi_table_quota_pins_keywords_within_top_tables() -> None:
    cols = [
        ColumnHit("db", "dwd_ip_indv_cust_info", "name", 0.95, "vector"),
        ColumnHit("db", "dwd_ip_indv_cust_info", "sex", 0.94, "vector"),
        ColumnHit("db", "dwd_ip_indv_cust_info", "age", 0.93, "vector"),
        ColumnHit("db", "dwd_ev_indv_loan_app", "app_loan_amt", 0.40, "vector"),
        ColumnHit("db", "dwd_prd_info", "prd_name", 0.35, "vector"),
    ]
    meta = {
        "dwd_ev_indv_loan_app": {
            "app_loan_amt": {
                "description": "申请用信额度。用信申请金额。",
                "synonyms": '["用信额度"]',
            }
        },
        "dwd_prd_info": {
            "prd_name": {
                "description": "产品名称。信贷产品名称。",
                "synonyms": '["产品名称"]',
            }
        },
    }
    q = "已婚个人客户各产品用信申请笔数和金额"
    out = _select_columns_with_table_quota(
        cols,
        tables=["dwd_ip_indv_cust_info", "dwd_ev_indv_loan_app", "dwd_prd_info"],
        core_tables=[
            "dwd_ip_indv_cust_info",
            "dwd_ev_indv_loan_app",
            "dwd_prd_info",
        ],
        limit=12,
        min_per_table=_multi_table_min_per_table(3),
        keywords=["婚姻", "申请额度", "产品名称"],
        question=q,
        col_meta_by_table=meta,
        pin_allow_tables={
            "dwd_ip_indv_cust_info",
            "dwd_ev_indv_loan_app",
            "dwd_prd_info",
        },
        max_keyword_pins=5,
        front_priority=10,
    )
    front = out[:10]
    front_keys = {(c.table, c.column) for c in front}
    assert ("dwd_ev_indv_loan_app", "app_loan_amt") in front_keys
    assert ("dwd_prd_info", "prd_name") in front_keys
    tables_in_front = {c.table.lower() for c in front}
    assert "dwd_ev_indv_loan_app" in tables_in_front
    assert "dwd_prd_info" in tables_in_front


def test_join_keys_from_relation_not_in_pool() -> None:
    selected = [
        ColumnHit("vectortest", "dwd_ev_repay_detail", "act_prin_amt", 0.7, "vector"),
    ]

    def factory(table: str, column: str):
        return ColumnHit("vectortest", table, column, 0.28, "join_key")

    out = _ensure_join_keys(
        selected,
        selected,
        relations=[
            ("dwd_ev_repay_detail", "loan_no", "dwd_ar_loan_info", "loan_no"),
        ],
        key_factory=factory,
    )
    cols = {(c.table, c.column) for c in out}
    assert ("dwd_ev_repay_detail", "loan_no") in cols
    assert ("dwd_ar_loan_info", "loan_no") in cols


def main() -> None:
    tests = [
        test_empty_lexicon_no_concepts,
        test_longest_phrase_product_name_concept,
        test_multi_loan_and_product_name_concepts,
        test_and_does_not_force_multi,
        test_question_hint_tables_no_hard_bind,
        test_column_concept_not_on_loan_table_without_description,
        test_resolve_hint_tables_vector_only,
        test_resolve_hint_tables_skips_ambiguous_vector,
        test_hinted_column_cover_by_concept_description,
        test_ambiguous_name_not_covered,
        test_origin_phrase_breaks_name_tie,
        test_resolve_quota_core_tables_merges_hint_and_scores,
        test_multi_table_quota_pins_keywords_within_top_tables,
        test_join_keys_only_on_selected_tables,
        test_join_keys_from_relation_not_in_pool,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print(f"passed {len(tests)}")


if __name__ == "__main__":
    main()
