"""Schema 召回：XiYan 式多路检索 / column 主检索 → 定表 → JOIN 邻表扩展 → 表内列补全。"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from wenshu.services.column_selection import (
    _ensure_join_keys,
    resolve_column_select,
    select_columns_s1_s2,
)
from wenshu.services.vector_search import search_collection

RetrievalStyle = Literal["xiyan", "legacy"]

DEFAULT_VECTOR_LIMIT = 50
XIYAN_KEYWORD_TOP_K = 12
XIYAN_MAX_KEYWORDS = 8
XIYAN_TABLE_SCORE_POOL = 80
XIYAN_JOIN_POOL = 15
XIYAN_TABLE_SIM_FLOOR = 0.08
# 分角色分路权重：列主路 / 过滤次之 / 表短语抬表分 / 关系短语加强 JOIN
XIYAN_ROLE_COLUMN_WEIGHT = 1.0
XIYAN_ROLE_FILTER_WEIGHT = 0.92
XIYAN_ROLE_TABLE_WEIGHT = 1.12
XIYAN_ROLE_JOIN_WEIGHT = 1.08
XIYAN_TABLE_PHRASE_TOP_K = 10
XIYAN_JOIN_PHRASE_TOP_K = 8
XIYAN_SEARCH_MAX_WORKERS = 8
DEFAULT_TOP_TABLES = 8
DEFAULT_COLUMNS_PER_TABLE = 8
DEFAULT_MAX_TABLES_AFTER_EXPAND = 12
DEFAULT_MIN_COLUMNS_PER_TABLE = 2
MULTI_TABLE_CORE_MAX = 3
MULTI_TABLE_MAX_KEYWORD_PINS = 3
COL_HIT_FRONT_SLOTS = 10
CONCEPT_COLUMN_SCORE_FACTOR = 0.96
DEFAULT_SINGLE_TABLE_CANDIDATES = 5
LEXICAL_COLUMN_WEIGHT = 0.35
LEXICAL_TABLE_WEIGHT = 0.30

_INCLUDE_TYPES_V2 = frozenset({"column", "join"})

_QUERY_STOPWORDS = frozenset(
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
        "按",
        "的",
        "了",
        "在",
        "是",
        "有",
        "和",
        "及",
        "与",
        "或",
        "等",
        "为",
        "对",
        "从",
        "到",
        "把",
        "被",
        "将",
        "请",
        "分别",
        "各",
        "每",
        "所有",
        "全部",
        "其中",
        "以及",
        "大于",
        "小于",
        "等于",
        "之间",
        "以上",
        "以下",
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
        "客户",
        "个人",
        "企业",
        "对公",
    }
)

# 召回词典只来自 L1 synonym(concept) / table_meta；空库则空词典，不回退代码内置包。
_RUNTIME_LEXICON: dict[str, Any] = {
    "gen": -1,
    "concepts": None,
    "alias_to_concept": None,
    "entity_concepts": None,
    "cross_table_attributes": None,
    "fact_need_hint": None,
}


def install_retrieval_lexicon(
    *,
    concepts: dict[str, tuple[str, ...]] | None = None,
    entity_concepts: frozenset[str] | tuple[str, ...] | None = None,
    cross_table_attributes: frozenset[str] | tuple[str, ...] | None = None,
    fact_need_hint: frozenset[str] | tuple[str, ...] | None = None,
    # 兼容旧单测参数（已忽略表/列硬绑）
    table_hints: dict[str, tuple[str, ...]] | None = None,
    column_hints: dict[str, tuple[str, ...]] | None = None,
    column_patterns: dict[str, tuple[str, ...]] | None = None,
    column_homes: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """无库单测 / 调试注入业务概念词典。生产路径走 refresh_retrieval_lexicon。"""
    from wenshu.services.business_concept import alias_to_concept_map, normalize_concepts

    norm = normalize_concepts(concepts or {})
    _RUNTIME_LEXICON["concepts"] = norm
    _RUNTIME_LEXICON["alias_to_concept"] = alias_to_concept_map(norm)
    _RUNTIME_LEXICON["entity_concepts"] = frozenset(entity_concepts or ())
    _RUNTIME_LEXICON["cross_table_attributes"] = frozenset(cross_table_attributes or ())
    _RUNTIME_LEXICON["fact_need_hint"] = frozenset(_norm(t) for t in (fact_need_hint or ()))
    _RUNTIME_LEXICON["gen"] = -2


def refresh_retrieval_lexicon(meta_engine: Engine | None = None) -> None:
    """只加载 L1 业务概念词典；库不可用或词典为空时，概念路径不生效。"""
    from wenshu.services.l1_meta import (
        ensure_table_meta_expand_flag,
        lexicon_generation,
        load_retrieval_lexicon,
    )
    from wenshu.services.retrieval_lexicon_seed import (
        SEED_CROSS_TABLE_ATTRIBUTE_KEYS,
        SEED_ENTITY_CONCEPT_KEYS,
    )

    gen = lexicon_generation()
    if _RUNTIME_LEXICON["gen"] == gen and _RUNTIME_LEXICON["concepts"] is not None:
        return
    if meta_engine is not None:
        ensure_table_meta_expand_flag(meta_engine)
    extra = load_retrieval_lexicon(meta_engine)
    _RUNTIME_LEXICON["concepts"] = dict(extra.get("concepts") or {})
    _RUNTIME_LEXICON["alias_to_concept"] = dict(extra.get("alias_to_concept") or {})
    entity = extra.get("entity_concepts") or frozenset()
    if not entity:
        entity = SEED_ENTITY_CONCEPT_KEYS
    _RUNTIME_LEXICON["entity_concepts"] = frozenset(entity)
    _RUNTIME_LEXICON["cross_table_attributes"] = frozenset(SEED_CROSS_TABLE_ATTRIBUTE_KEYS)
    _RUNTIME_LEXICON["fact_need_hint"] = frozenset(
        _norm(t) for t in (extra.get("fact_need_hint") or ())
    )
    _RUNTIME_LEXICON["gen"] = gen


def _concepts() -> dict[str, tuple[str, ...]]:
    return _RUNTIME_LEXICON["concepts"] or {}


def _alias_to_concept() -> dict[str, str]:
    return _RUNTIME_LEXICON["alias_to_concept"] or {}


def _entity_concepts() -> frozenset[str]:
    return _RUNTIME_LEXICON["entity_concepts"] or frozenset()


def _cross_table_attributes() -> frozenset[str]:
    from wenshu.services.retrieval_lexicon_seed import SEED_CROSS_TABLE_ATTRIBUTE_KEYS

    return _RUNTIME_LEXICON["cross_table_attributes"] or SEED_CROSS_TABLE_ATTRIBUTE_KEYS


def _fact_need_hint() -> frozenset[str]:
    return _RUNTIME_LEXICON["fact_need_hint"] or frozenset()


def question_matched_concepts(question: str, evidence: str = "") -> list[str]:
    from wenshu.services.business_concept import matched_concepts_from_text

    parts = [question or ""]
    if evidence and str(evidence).strip():
        parts.append(str(evidence).strip())
    text = "\n".join(parts)
    return matched_concepts_from_text(
        text,
        concepts=_concepts(),
        alias_to_concept=_alias_to_concept(),
    )


def question_matched_entity_concepts(question: str, evidence: str = "") -> list[str]:
    entities = _entity_concepts()
    return [c for c in question_matched_concepts(question, evidence) if c in entities]

_PREFERRED_COL_SOURCES = frozenset({"concept", "lexical"})

_LAYER_PREFIX_BONUS: dict[str, float] = {
    "dwd_": 0.08,
    "dwd": 0.08,
    "app_": 0.04,
    "app": 0.04,
    "dws_": -0.06,
    "dws": -0.06,
}

# 数仓分层表名：用于从词典值里挑真表名、以及明细层 vs 汇总层降权
_WAREHOUSE_LAYER_PREFIXES = ("dwd_", "dws_", "ods_", "dim_", "ads_", "app_", "dwh_", "stg_")
_DETAIL_LAYER_PREFIXES = ("dwd_", "ods_")
_SUMMARY_LAYER_PREFIXES = ("dws_", "ads_")


def _norm(name: str | None) -> str:
    return (name or "").strip().lower()


def _hit_payload(hit: Any) -> dict:
    return getattr(hit, "payload", None) or {}


def _hit_score(hit: Any) -> float:
    return float(getattr(hit, "score", 0.0) or 0.0)


def _parse_join_side(side: str | None) -> tuple[str, str, str]:
    """解析 join payload：db.table.column。"""
    if not side:
        return "", "", ""
    parts = str(side).split(".")
    if len(parts) >= 3:
        return parts[0], parts[1], ".".join(parts[2:]) if len(parts) > 3 else parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return "", parts[0], ""


@dataclass
class _VectorHit:
    """与 Qdrant ScoredPoint 兼容的轻量 hit（XiYan 多路合并用）。"""

    score: float
    payload: dict


def resolve_retrieval_style(style: str | None = None) -> RetrievalStyle:
    """默认 xiyan；可通过 SCHEMA_RETRIEVAL_STYLE=legacy 回退整句单向量检索。"""
    raw = (style or os.getenv("SCHEMA_RETRIEVAL_STYLE") or "xiyan").strip().lower()
    return "legacy" if raw == "legacy" else "xiyan"


def _embed_query_texts(texts: list[str]) -> list[list[float]]:
    import build_vector_index as bvi

    bvi._load_dotenv()
    return bvi.embed(texts, is_query=True)


def _table_scores_from_question_vector(
    client,
    collection_name: str,
    query_vector: list[float],
    *,
    db_names: list[str] | None = None,
    limit: int = XIYAN_TABLE_SCORE_POOL,
) -> dict[str, float]:
    """sim(Q, Table(c)) 代理：整句 Q 向量对 column ANN，按表取 max。"""
    hits = search_collection(
        client,
        collection_name,
        query_vector,
        limit=limit,
        db_names=db_names,
        include_types=frozenset({"column"}),
    )
    return aggregate_table_scores(hits, db_names={_norm(d) for d in db_names} if db_names else None)


def _merge_xiyan_column_hit(
    merged: dict[tuple[str, str], _VectorHit],
    *,
    table: str,
    column: str,
    score: float,
    payload: dict,
    source_kw: str = "",
) -> None:
    key = (_norm(table), _norm(column))
    pl = dict(payload)
    pl["object_type"] = pl.get("object_type") or "column"
    pl["table"] = table
    pl["column"] = column
    prev = merged.get(key)
    kws: list[str] = []
    seen_kw: set[str] = set()
    if prev is not None:
        for item in prev.payload.get("xiyan_keywords") or []:
            s = str(item or "").strip()
            if s and s not in seen_kw:
                seen_kw.add(s)
                kws.append(s)
        old = str(prev.payload.get("xiyan_keyword") or "").strip()
        if old and old not in seen_kw:
            seen_kw.add(old)
            kws.append(old)
    if source_kw and source_kw not in seen_kw:
        kws.append(source_kw)
        seen_kw.add(source_kw)
    if source_kw:
        pl["xiyan_keyword"] = source_kw
    elif prev is not None:
        pl["xiyan_keyword"] = prev.payload.get("xiyan_keyword") or ""
    pl["xiyan_keywords"] = kws
    hit = _VectorHit(score=score, payload=pl)
    if prev is None or hit.score > prev.score:
        merged[key] = hit
    else:
        prev.payload["xiyan_keywords"] = kws


def _unique_phrases(phrases: list[str]) -> list[str]:
    """去重保序（大小写不敏感）。"""
    seen: set[str] = set()
    out: list[str] = []
    for p in phrases:
        raw = (p or "").strip()
        if not raw:
            continue
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


def _xiyan_search_workers() -> int:
    try:
        n = int(os.getenv("XIYAN_SEARCH_MAX_WORKERS", str(XIYAN_SEARCH_MAX_WORKERS)))
    except ValueError:
        n = XIYAN_SEARCH_MAX_WORKERS
    return max(1, min(n, 16))


def _run_search_jobs_parallel(
    client,
    collection_name: str,
    jobs: list[dict[str, Any]],
    *,
    db_names: list[str] | None,
) -> list[dict[str, Any]]:
    """并行执行多路 Qdrant 检索；返回与 jobs 对齐的结果列表（含 hits）。"""
    if not jobs:
        return []

    workers = min(_xiyan_search_workers(), len(jobs))
    thread_local = threading.local()

    def _client_for_thread():
        if workers <= 1:
            return client
        cached = getattr(thread_local, "qdrant_client", None)
        if cached is None:
            from db_config import create_qdrant_client

            cached = create_qdrant_client()
            thread_local.qdrant_client = cached
        return cached

    def _one(job: dict[str, Any]) -> dict[str, Any]:
        hits = search_collection(
            _client_for_thread(),
            collection_name,
            job["vector"],
            limit=int(job["limit"]),
            db_names=db_names,
            include_types=job["include_types"],
        )
        return {**job, "hits": hits}

    if workers == 1 or len(jobs) == 1:
        return [_one(j) for j in jobs]

    out: list[dict[str, Any] | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_one, job): i for i, job in enumerate(jobs)}
        for fut in as_completed(future_map):
            idx = future_map[fut]
            out[idx] = fut.result()
    return [r for r in out if r is not None]


def _boost_table_scores_from_phrase_hits(
    table_q_scores: dict[str, float],
    hits: list,
    *,
    weight: float,
    allowed: set[str] | None,
) -> None:
    """表/关系短语命中：抬高对应表的 sim(Q, Table) 代理分。"""
    for hit in hits:
        payload = _hit_payload(hit)
        score = max(0.0, _hit_score(hit)) * weight
        if payload.get("object_type") == "join":
            for side in ("left", "right"):
                _db, table, _col = _parse_join_side(payload.get(side))
                if not table:
                    continue
                if allowed and _db and _norm(_db) not in allowed:
                    continue
                key = _norm(table)
                table_q_scores[key] = max(table_q_scores.get(key, 0.0), score)
            continue
        table = payload.get("table")
        db = payload.get("db")
        if not table:
            continue
        if allowed and db and _norm(db) not in allowed:
            continue
        key = _norm(str(table))
        table_q_scores[key] = max(table_q_scores.get(key, 0.0), score)


def xiyan_multipath_hits(
    client,
    collection_name: str,
    query_vector: list[float],
    *,
    question: str,
    evidence: str = "",
    db_names: list[str] | None = None,
    vector_limit: int = DEFAULT_VECTOR_LIMIT,
    keyword_top_k: int = XIYAN_KEYWORD_TOP_K,
    max_keywords: int = XIYAN_MAX_KEYWORDS,
    keyword_mode: str | None = None,
) -> tuple[list, list[str], str, dict]:
    """
    XiYan Schema Filter + 分角色分路检索（加速版）：
    ① 分角色抽词
    ② 全部短语一次 embed
    ③ Qdrant 多路并行检索
    ④ 先汇总表分再按原公式 merge（匹配逻辑不变）
    """
    from wenshu.services.keyword_llm import extract_roles_resolved

    roles = extract_roles_resolved(question, evidence, mode=keyword_mode)
    role_dict = roles.as_dict()
    keywords = (roles.keywords or [])[:max_keywords]
    keyword_source = roles.source

    table_phrases = _unique_phrases((roles.table_phrases or [])[:max_keywords])
    column_phrases = _unique_phrases((roles.column_phrases or [])[:max_keywords])
    filter_phrases = _unique_phrases((roles.filter_phrases or [])[:max_keywords])
    join_phrases = _unique_phrases(
        (roles.join_phrases or [])[: max(4, max_keywords // 2)]
    )

    if not column_phrases and not filter_phrases and keywords:
        column_phrases = _unique_phrases(list(keywords))

    from wenshu.services.business_concept import concept_search_phrases

    matched = question_matched_concepts(question, evidence)
    concept_phrases = concept_search_phrases(matched, _concepts(), limit=10)
    if concept_phrases:
        table_phrases = _unique_phrases(list(table_phrases) + concept_phrases[:6])
        column_phrases = _unique_phrases(list(column_phrases) + concept_phrases)
    role_dict["matched_concepts"] = matched

    # 一次 embed：所有角色短语去重
    phrase_embed_list = _unique_phrases(
        table_phrases + column_phrases + filter_phrases + join_phrases
    )
    phrase_vec_map: dict[str, list[float]] = {}
    if phrase_embed_list:
        vectors = _embed_query_texts(phrase_embed_list)
        for phrase, vec in zip(phrase_embed_list, vectors):
            phrase_vec_map[phrase.lower()] = vec

    def _vec_for(phrase: str) -> list[float] | None:
        return phrase_vec_map.get(phrase.lower())

    q_column_limit = max(XIYAN_TABLE_SCORE_POOL, vector_limit // 2, 24)
    # 组装并行检索任务（整句 Q→column 只搜一次，兼作表分池）
    jobs: list[dict[str, Any]] = [
        {
            "kind": "q_column",
            "phrase": "__question__",
            "vector": query_vector,
            "limit": q_column_limit,
            "include_types": frozenset({"column"}),
            "weight": 1.0,
        },
        {
            "kind": "q_join",
            "phrase": "__question__",
            "vector": query_vector,
            "limit": XIYAN_JOIN_POOL,
            "include_types": frozenset({"join"}),
            "weight": 1.0,
        },
    ]
    for phrase in table_phrases:
        vec = _vec_for(phrase)
        if not vec:
            continue
        jobs.append(
            {
                "kind": "table",
                "phrase": phrase,
                "vector": vec,
                "limit": XIYAN_TABLE_PHRASE_TOP_K,
                "include_types": frozenset({"column"}),
                "weight": XIYAN_ROLE_TABLE_WEIGHT,
            }
        )
    for phrase in column_phrases:
        vec = _vec_for(phrase)
        if not vec:
            continue
        jobs.append(
            {
                "kind": "column",
                "phrase": phrase,
                "vector": vec,
                "limit": keyword_top_k,
                "include_types": frozenset({"column"}),
                "weight": XIYAN_ROLE_COLUMN_WEIGHT,
            }
        )
    for phrase in filter_phrases:
        vec = _vec_for(phrase)
        if not vec:
            continue
        jobs.append(
            {
                "kind": "filter",
                "phrase": phrase,
                "vector": vec,
                "limit": keyword_top_k,
                "include_types": frozenset({"column"}),
                "weight": XIYAN_ROLE_FILTER_WEIGHT,
            }
        )
    for phrase in join_phrases:
        vec = _vec_for(phrase)
        if not vec:
            continue
        jobs.append(
            {
                "kind": "join",
                "phrase": phrase,
                "vector": vec,
                "limit": XIYAN_JOIN_PHRASE_TOP_K,
                "include_types": frozenset({"join"}),
                "weight": XIYAN_ROLE_JOIN_WEIGHT,
            }
        )

    results = _run_search_jobs_parallel(
        client, collection_name, jobs, db_names=db_names
    )

    allowed = {_norm(d) for d in db_names} if db_names else None
    table_q_scores: dict[str, float] = {}
    merged: dict[tuple[str, str], _VectorHit] = {}
    join_hits: list = []

    # Phase A：先定表分（整句 Q→column + 表短语 boost + join 短语 boost）
    for job in results:
        kind = job["kind"]
        hits = job.get("hits") or []
        if kind == "q_column":
            table_q_scores = aggregate_table_scores(hits, db_names=allowed)
        elif kind == "table":
            _boost_table_scores_from_phrase_hits(
                table_q_scores,
                hits,
                weight=float(job["weight"]),
                allowed=allowed,
            )
        elif kind == "join":
            _boost_table_scores_from_phrase_hits(
                table_q_scores,
                hits,
                weight=float(job["weight"]),
                allowed=allowed,
            )

    def _table_sim(table: str, db: str | None = None) -> float:
        if allowed and db and _norm(db) not in allowed:
            return 0.0
        return max(table_q_scores.get(_norm(table), 0.0), XIYAN_TABLE_SIM_FLOOR)

    def _merge_column_hits(
        hits: list,
        *,
        weight: float,
        source_kw: str,
        column_scale: float = 1.0,
    ) -> None:
        for hit in hits:
            payload = _hit_payload(hit)
            table, column = payload.get("table"), payload.get("column")
            if not table or not column:
                continue
            kw_sim = max(0.0, _hit_score(hit))
            score = (
                _table_sim(str(table), str(payload.get("db") or ""))
                * kw_sim
                * weight
                * column_scale
            )
            _merge_xiyan_column_hit(
                merged,
                table=str(table),
                column=str(column),
                score=score,
                payload=payload,
                source_kw=source_kw,
            )

    # Phase B：用最终表分合并 column / join hits（与旧逻辑同公式）
    for job in results:
        kind = job["kind"]
        hits = job.get("hits") or []
        phrase = str(job.get("phrase") or "")
        weight = float(job.get("weight") or 1.0)

        if kind == "q_column":
            _merge_column_hits(hits, weight=1.0, source_kw="__question__")
        elif kind == "table":
            _merge_column_hits(
                hits,
                weight=weight,
                source_kw=f"table:{phrase}",
                column_scale=0.85,
            )
        elif kind == "column":
            _merge_column_hits(
                hits, weight=weight, source_kw=f"column:{phrase}"
            )
        elif kind == "filter":
            _merge_column_hits(
                hits, weight=weight, source_kw=f"filter:{phrase}"
            )
        elif kind == "q_join":
            join_hits.extend(hits)
        elif kind == "join":
            for hit in hits:
                join_hits.append(
                    _VectorHit(
                        score=max(0.0, _hit_score(hit)) * weight,
                        payload={
                            **_hit_payload(hit),
                            "xiyan_keyword": f"join:{phrase}",
                        },
                    )
                )

    out: list = sorted(merged.values(), key=lambda h: -h.score)
    out.extend(join_hits)
    out.sort(key=lambda h: -_hit_score(h))
    return out[:vector_limit], keywords, keyword_source, role_dict


@dataclass
class ColumnHit:
    db: str
    table: str
    column: str
    score: float
    source: str  # vector | rerank | join_key
    payload: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (_norm(self.table), _norm(self.column))

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "type": "column",
            "db": self.db,
            "table": self.table,
            "column": self.column,
            "source": self.source,
        }


@dataclass
class SchemaRetrievalResult:
    db_name: str
    raw_hits: list
    selected_tables: list[str]
    expanded_tables: list[str]
    columns: list[ColumnHit]
    table_scores: dict[str, float] = field(default_factory=dict)
    query_keywords: list[str] = field(default_factory=list)
    keyword_source: str = "rule"
    retrieval_style: str = "xiyan"
    query_roles: dict = field(default_factory=dict)
    s1_columns: list[ColumnHit] = field(default_factory=list)
    s2_columns: list[ColumnHit] = field(default_factory=list)
    selection_meta: dict = field(default_factory=dict)

    @property
    def selected_columns(self) -> set[tuple[str, str]]:
        return {c.key for c in self.columns}

    @property
    def expanded_table_set(self) -> set[str]:
        return {_norm(t) for t in self.expanded_tables}

    def preview_hits(self, limit: int = 15) -> list[dict]:
        """供 UI / 评测展示的列级结果（按分数排序）。"""
        ordered = sorted(self.columns, key=lambda c: -c.score)
        return [c.as_dict() for c in ordered[:limit]]

    def ordered_tables(self) -> list[str]:
        return list(self.expanded_tables)


def aggregate_table_scores(hits: list, *, db_names: set[str] | None = None) -> dict[str, float]:
    """从 column / join hits 聚合表级分数（表名小写键）。"""
    scores: dict[str, float] = {}
    allowed = {_norm(d) for d in db_names} if db_names else None

    for hit in hits:
        payload = _hit_payload(hit)
        score = _hit_score(hit)
        obj_type = payload.get("object_type")

        if obj_type == "column":
            table = payload.get("table")
            db = payload.get("db")
            if not table:
                continue
            if allowed and db and _norm(db) not in allowed:
                continue
            key = _norm(table)
            scores[key] = max(scores.get(key, 0.0), score)
        elif obj_type == "join":
            for side in ("left", "right"):
                db, table, _col = _parse_join_side(payload.get(side))
                if not table:
                    continue
                if allowed and db and _norm(db) not in allowed:
                    continue
                key = _norm(table)
                scores[key] = max(scores.get(key, 0.0), score)

    return scores


def aggregate_table_scores_enhanced(hits: list, *, db_names: set[str] | None = None) -> dict[str, float]:
    """按表汇总 Top-N 列分 + 命中次数（非 max 单点）。"""
    per_table: dict[str, list[float]] = defaultdict(list)
    allowed = {_norm(d) for d in db_names} if db_names else None

    for hit in hits:
        payload = _hit_payload(hit)
        score = _hit_score(hit)
        if payload.get("object_type") != "column":
            continue
        table = payload.get("table")
        db = payload.get("db")
        if not table:
            continue
        if allowed and db and _norm(db) not in allowed:
            continue
        per_table[_norm(table)].append(score)

    out: dict[str, float] = {}
    for table, scores in per_table.items():
        scores.sort(reverse=True)
        top_sum = sum(scores[:5])
        hit_bonus = min(len(scores), 12) * 0.015
        out[table] = top_sum + hit_bonus
    return out


def _table_casing_from_hits(hits: list) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for hit in hits:
        payload = _hit_payload(hit)
        table = payload.get("table")
        if table:
            mapping[_norm(table)] = str(table)
    return mapping


def extract_query_keywords(question: str, evidence: str = "") -> list[str]:
    """从用户问题（及可选 Evidence）抽取检索关键词；规则版，对标 XiYan LLM 抽词。"""
    parts = [question or ""]
    if evidence and str(evidence).strip():
        parts.append(str(evidence).strip())
    q = "\n".join(parts).strip().lower()
    if not q:
        return []
    chunks = re.findall(r"[\u4e00-\u9fff]{2,8}", q)
    chunks += re.findall(r"[a-z][a-z0-9_]{1,}", q)
    seen: set[str] = set()
    out: list[str] = []
    for c in chunks:
        c = c.strip()
        if not c or c in _QUERY_STOPWORDS or c in seen:
            continue
        seen.add(c)
        out.append(c)
    for phrase in _alias_to_concept():
        if phrase in q and phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
    return out[:24]


def _coverage_keywords(keywords: list[str], question: str = "") -> list[str]:
    """覆盖选列优先用问句命中的业务概念。"""
    matched = question_matched_concepts(question)
    if matched:
        return matched[:8]
    alias_map = _alias_to_concept()
    picked = [kw for kw in keywords if kw in alias_map]
    if picked:
        return [alias_map[kw] for kw in picked][:8]
    return [kw for kw in keywords if len(kw) <= 6][:8]


def _column_search_text(column_name: str, meta: dict | None) -> str:
    meta = meta or {}
    syns = meta.get("synonyms") or ""
    parts = " ".join(column_name.replace("_", " ").split())
    return " ".join(
        [
            column_name.lower(),
            parts.lower(),
            str(meta.get("description") or "").lower(),
            str(meta.get("hive_comment") or "").lower(),
            syns.lower(),
        ]
    ).strip()


def _keyword_hits(keywords: list[str], text: str, column_name: str = "") -> int:
    if not keywords or not text:
        return 0
    hits = 0
    concepts = _concepts()
    for kw in keywords:
        kw_l = kw.lower()
        if kw_l in text:
            hits += 1
            continue
        if concepts and kw in concepts and _concept_in_text(kw, text, concepts):
            hits += 1
    return hits


def _concept_in_text(concept_key: str, text: str, concepts: dict[str, tuple[str, ...]] | None = None) -> bool:
    from wenshu.services.business_concept import concept_in_text

    return concept_in_text(concept_key, text, concepts or _concepts())


def column_lexical_score(keywords: list[str], column_name: str, meta: dict | None) -> float:
    if not keywords:
        return 0.0
    text = _column_search_text(column_name, meta)
    hits = _keyword_hits(keywords, text, column_name)
    return min(1.0, hits / max(len(keywords), 1))


def _table_layer_bonus(table_name: str) -> float:
    t = table_name.lower()
    for prefix, bonus in _LAYER_PREFIX_BONUS.items():
        if t.startswith(prefix):
            return bonus
    return 0.0


def _is_layer_table_name(name: str) -> bool:
    return _norm(name).startswith(_WAREHOUSE_LAYER_PREFIXES)


def _table_names_from_frags(frags: tuple[str, ...] | list[str]) -> list[str]:
    """词典值可能是全名或短别名；优先收分层全名，否则当作 L1 真表名。"""
    named: list[str] = []
    for frag in frags:
        f = _norm(frag)
        if f and _is_layer_table_name(f) and f not in named:
            named.append(f)
    if named:
        return named
    return [_norm(f) for f in frags if _norm(f)]


def _drop_less_specific_tables(tables: list[str]) -> list[str]:
    """同时命中前缀表和更长细分表时只留细分表。"""
    norms = [_norm(t) for t in tables]
    drop = {a for a in norms for b in norms if a != b and b.startswith(a) and len(b) > len(a)}
    return [t for t in tables if _norm(t) not in drop]


def _table_concept_score(question: str, table_meta: dict | None) -> float:
    from wenshu.services.business_concept import table_matches_concept

    matched = question_matched_concepts(question)
    if not matched or not table_meta:
        return 0.0
    concepts = _concepts()
    hits = sum(1 for c in matched if table_matches_concept(c, table_meta, concepts))
    return min(1.0, hits * 0.42)


def _match_phrases_longest_first(
    text: str,
    phrases: list[str] | tuple[str, ...] | dict[str, Any],
) -> list[str]:
    """最长短语优先占位，避免「产品名称」被拆成更短的「产品」。"""
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


def _canonical_hint_table(frags: tuple[str, ...]) -> str | None:
    names = _table_names_from_frags(frags)
    return names[0] if names else None


def question_hint_tables(
    question: str, *, table_hints: dict[str, tuple[str, ...]] | None = None
) -> list[str]:
    """已废弃表级硬绑：默认不再从同义词解析物理表名，定表交给向量/LLM。"""
    if table_hints:
        phrases = _match_phrases_longest_first(question or "", table_hints)
        out: list[str] = []
        seen: set[str] = set()
        for phrase in phrases:
            for canon in _table_names_from_frags(table_hints.get(phrase) or ()):
                if not canon or canon in seen:
                    continue
                seen.add(canon)
                out.append(canon)
        return _drop_less_specific_tables(out)
    return []


def _hit_xiyan_keywords(payload: dict) -> list[str]:
    raw = payload.get("xiyan_keywords") if isinstance(payload, dict) else None
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            s = str(item or "").strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    one = str((payload or {}).get("xiyan_keyword") or "").strip()
    if one and one not in seen:
        out.append(one)
    return out


def resolve_hint_tables(
    question: str,
    *,
    query_roles: dict | None = None,
    raw_hits: list | None = None,
    enabled: set[str] | None = None,
    casing_map: dict[str, str] | None = None,
    min_score: float = 0.12,
    margin: float = 1.08,
) -> list[str]:
    """向量 table:phrase 高分表 + 概念短语向量召回；不再从同义词硬绑物理表。"""
    from wenshu.services.business_concept import concept_search_phrases, concept_terms

    casing = casing_map or {}
    out: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        key = _norm(name)
        if not key or key in seen:
            return
        if enabled is not None and key not in enabled:
            return
        seen.add(key)
        out.append(casing.get(key, name))

    matched = question_matched_concepts(question)
    phrase_keys: list[str] = []
    seen_phrase: set[str] = set()
    for key in matched:
        for term in [key, *concept_terms(key, _concepts())]:
            low = term.lower()
            if low in seen_phrase:
                continue
            seen_phrase.add(low)
            phrase_keys.append(term)
    for phrase in concept_search_phrases(matched, _concepts(), limit=16):
        low = phrase.lower()
        if low not in seen_phrase:
            seen_phrase.add(low)
            phrase_keys.append(phrase)
    for phrase in (query_roles or {}).get("table_phrases") or []:
        text = str(phrase or "").strip()
        if text and text not in phrase_keys:
            phrase_keys.append(text)

    phrase_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for hit in raw_hits or []:
        payload = _hit_payload(hit)
        table = payload.get("table")
        if not table:
            continue
        tkey = _norm(str(table))
        if enabled is not None and tkey not in enabled:
            continue
        score = max(0.0, _hit_score(hit))
        for kw in _hit_xiyan_keywords(payload):
            if not kw.startswith("table:"):
                continue
            phrase_key = kw[6:].strip().lower()
            if not phrase_key:
                continue
            prev = phrase_scores[phrase_key].get(tkey, 0.0)
            if score > prev:
                phrase_scores[phrase_key][tkey] = score
        for phrase in phrase_keys:
            pk = phrase.lower()
            if pk and pk in str(payload.get("description") or "").lower():
                prev = phrase_scores[pk].get(tkey, 0.0)
                if score > prev:
                    phrase_scores[pk][tkey] = score

    for phrase in phrase_keys:
        scores = phrase_scores.get(phrase.lower(), {})
        if not scores:
            continue
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        top_table, top_score = ranked[0]
        if top_score < min_score:
            continue
        if len(ranked) >= 2 and top_score < ranked[1][1] * margin:
            continue
        _add(top_table)
    return _drop_less_specific_tables(out)


def _slot_origin_phrases(col) -> set[str]:
    """该列是被哪条检索短语召回的（column:/filter:），没有来路则空。"""
    payload = getattr(col, "payload", None) or {}
    raw: list = []
    kws = payload.get("xiyan_keywords")
    if isinstance(kws, list):
        raw.extend(kws)
    one = payload.get("xiyan_keyword")
    if one:
        raw.append(one)
    out: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if text.startswith(("column:", "filter:")):
            phrase = text.split(":", 1)[1].strip().lower()
            if phrase:
                out.add(phrase)
    return out


def hinted_column_keys_from_pool(question: str, pool: list) -> list[tuple[str, str]]:
    """每个业务概念在池内留 1 列；按列说明语义匹配，不硬绑字段名。"""
    from wenshu.services.business_concept import column_matches_concept

    concepts = question_matched_concepts(question)
    if not concepts or not pool:
        return []
    concept_map = _concepts()
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for concept in concepts:
        origin: list = []
        textual: list = []
        terms = {t.lower() for t in concept_map.get(concept, (concept,))}
        for col in pool:
            column = getattr(col, "column", "") or ""
            origins = _slot_origin_phrases(col)
            if any(
                t == o or (len(o) >= 2 and o in t) or (len(t) >= 2 and t in o)
                for o in origins
                for t in terms
            ):
                origin.append(col)
                continue
            payload = getattr(col, "payload", None) or {}
            meta = {
                "description": payload.get("description") or "",
                "hive_comment": payload.get("hive_comment") or "",
                "synonyms": payload.get("synonyms") or "",
            }
            if column_matches_concept(concept, column, meta, concept_map):
                textual.append(col)
        candidates = origin or textual
        if not candidates:
            continue
        by_table: dict[str, tuple[object, float]] = {}
        for col in candidates:
            table_key = _norm(getattr(col, "table", ""))
            score = float(getattr(col, "score", 0.0) or 0.0)
            prev = by_table.get(table_key)
            if prev is None or score > prev[1]:
                by_table[table_key] = (col, score)
        ranked = sorted(by_table.values(), key=lambda x: -x[1])
        best, best_score = ranked[0]
        if len(ranked) >= 2 and best_score < ranked[1][1] * 1.08:
            continue
        key = (_norm(best.table), _norm(best.column))
        if key in seen:
            continue
        seen.add(key)
        out.append((best.table, best.column))
    return out


def question_column_hint_phrases(question: str) -> list[str]:
    return question_matched_concepts(question)


def _column_hint_resolves_on_table(
    phrase: str,
    col_meta: dict[str, dict],
    table_name: str,
) -> bool:
    from wenshu.services.business_concept import column_matches_concept

    concept_map = _concepts()
    for col_norm, meta in col_meta.items():
        col_raw = str(meta.get("column_name") or col_norm)
        if column_matches_concept(phrase, col_raw, meta, concept_map):
            return True
    return False


def column_hints_resolved_on_table(
    question: str,
    col_meta: dict[str, dict],
    table_name: str,
) -> bool:
    """问句命中的业务概念都能在该表列说明里落地，才允许单表收口。"""
    phrases = question_matched_concepts(question)
    if not phrases:
        return True
    return all(_column_hint_resolves_on_table(p, col_meta, table_name) for p in phrases)


def is_likely_single_table_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    multi_entity = ("及其", "对应", "关联", "联查", "分别对应", "各表", "跨表", "join")
    return not any(x in q for x in multi_entity)


def table_lexical_score(keywords: list[str], table_meta: dict | None, *, question: str = "") -> float:
    if not keywords or not table_meta:
        return 0.0
    text = " ".join(
        [
            str(table_meta.get("table_name") or ""),
            str(table_meta.get("cn_name") or ""),
            str(table_meta.get("description") or ""),
            str(table_meta.get("sample_questions") or ""),
        ]
    ).lower()
    hits = _keyword_hits(keywords, text)
    base = min(1.0, hits / max(len(keywords), 1))
    table_name = str(table_meta.get("table_name") or "")
    hint = _table_concept_score(question, table_meta)
    layer = _table_layer_bonus(table_name)
    return min(1.0, base + hint + max(0.0, layer))


def _fetch_table_meta_batch(
    meta_engine: Engine,
    db_name: str,
    table_names: list[str],
) -> dict[str, dict]:
    if not db_name or not table_names:
        return {}
    norms = [_norm(t) for t in table_names]
    placeholders = ", ".join(f":t{i}" for i in range(len(norms)))
    params = {"db": db_name, **{f"t{i}": n for i, n in enumerate(norms)}}
    sql = f"""
        SELECT table_name, cn_name, description, sample_questions
        FROM table_meta
        WHERE db_name = :db AND is_enabled = 1
          AND LOWER(table_name) IN ({placeholders})
    """
    out: dict[str, dict] = {}
    with meta_engine.connect() as conn:
        for row in conn.execute(text(sql), params).fetchall():
            out[_norm(row.table_name)] = {
                "table_name": row.table_name,
                "cn_name": row.cn_name or "",
                "description": row.description or "",
                "sample_questions": row.sample_questions or "",
            }
    return out


def _fetch_column_meta_for_table(
    meta_engine: Engine,
    db_name: str,
    table_name: str,
) -> dict[str, dict]:
    if not db_name or not table_name:
        return {}
    sql = """
        SELECT c.column_name, c.description, c.hive_comment, c.synonyms
        FROM column_meta c
        JOIN table_meta t ON c.table_id = t.table_id
        WHERE t.db_name = :db AND LOWER(t.table_name) = LOWER(:table)
          AND c.is_enabled = 1 AND t.is_enabled = 1
    """
    out: dict[str, dict] = {}
    with meta_engine.connect() as conn:
        for row in conn.execute(text(sql), {"db": db_name, "table": table_name}).fetchall():
            syns = ""
            if row.synonyms:
                try:
                    syns = ", ".join(json.loads(row.synonyms))
                except Exception:
                    syns = str(row.synonyms)
            key = _norm(row.column_name)
            out[key] = {
                "column_name": row.column_name,
                "description": row.description or "",
                "hive_comment": row.hive_comment or "",
                "synonyms": syns,
            }
    return out


def _expand_focus_table_candidates(
    table_scores: dict[str, float],
    raw_hits: list,
    question: str,
    hint_tables: list[str] | None = None,
) -> list[str]:
    """单表聚焦候选：向量表分 + 首命中表 + 问句 hint 表。"""
    candidates: set[str] = set(table_scores.keys())
    for hit in raw_hits:
        payload = _hit_payload(hit)
        if payload.get("object_type") == "column" and payload.get("table"):
            candidates.add(_norm(payload["table"]))
            break
    for table in hint_tables or []:
        candidates.add(_norm(table))
    for norm in list(candidates):
        table_scores.setdefault(norm, 0.05)
    ranked = sorted(candidates, key=lambda t: -table_scores.get(t, 0.0))
    return ranked[: max(DEFAULT_SINGLE_TABLE_CANDIDATES, 8)]


def _ensure_hint_tables_in_candidates(
    table_scores: dict[str, float],
    casing_map: dict[str, str],
    question: str,
    hint_tables: list[str] | None = None,
) -> None:
    for table in hint_tables if hint_tables is not None else question_hint_tables(question):
        norm = _norm(table)
        table_scores.setdefault(norm, 0.12)
        casing_map.setdefault(norm, table)


def pick_focus_table(
    table_scores: dict[str, float],
    *,
    question: str,
    meta_engine: Engine,
    db_name: str,
    casing_map: dict[str, str],
    raw_hits: list,
    candidate_n: int = DEFAULT_SINGLE_TABLE_CANDIDATES,
    hint_tables: list[str] | None = None,
) -> tuple[str | None, bool]:
    """选出单表聚焦表。返回 (table_norm, is_single_table_focus)。"""
    if not table_scores:
        return None, False

    hint_tables = list(hint_tables) if hint_tables is not None else []
    if len(hint_tables) >= 2:
        return None, False

    _ensure_hint_tables_in_candidates(
        table_scores, casing_map, question, hint_tables=hint_tables
    )
    likely_single = is_likely_single_table_question(question)
    candidate_norms = _expand_focus_table_candidates(
        table_scores, raw_hits=raw_hits, question=question, hint_tables=hint_tables
    )[:candidate_n]
    keywords = extract_query_keywords(question)
    table_names = [casing_map.get(t, t) for t in candidate_norms]
    table_meta = _fetch_table_meta_batch(meta_engine, db_name, table_names)

    combined: list[tuple[str, float, float, float]] = []
    for table_norm in candidate_norms:
        vec_score = table_scores.get(table_norm, 0.0)
        meta = table_meta.get(table_norm, {"table_name": casing_map.get(table_norm, table_norm)})
        lex = table_lexical_score(keywords, meta, question=question)
        layer = _table_layer_bonus(str(meta.get("table_name") or table_norm))
        final = vec_score * (1 - LEXICAL_TABLE_WEIGHT) + (lex + layer) * LEXICAL_TABLE_WEIGHT * 2.5
        combined.append((table_norm, vec_score, lex, final))

    combined.sort(key=lambda x: -x[3])
    top_norm, _v1, lex1, final1 = combined[0]
    second_final = combined[1][3] if len(combined) > 1 else 0.0
    second_lex = combined[1][2] if len(combined) > 1 else 0.0

    confident = final1 >= second_final * 1.08 or (lex1 >= 0.18 and lex1 >= second_lex + 0.05)
    if not confident and len(combined) >= 2:
        v_top, v_second = combined[0][1], combined[1][1]
        if v_top >= v_second * 1.25:
            confident = True
    if likely_single and lex1 >= 0.15:
        confident = True

    # 问句只点名一张 hint 表时，聚焦到该表（由 L1 词典决定，不写死表名）
    if likely_single and len(hint_tables) == 1:
        top_norm = _norm(hint_tables[0])
        confident = True

    return (top_norm if confident else None), confident


def _rerank_with_lexical(
    columns: list[ColumnHit],
    keywords: list[str],
    col_meta: dict[str, dict],
) -> list[tuple[ColumnHit, float]]:
    scored: list[tuple[ColumnHit, float]] = []
    for ch in columns:
        meta = col_meta.get(_norm(ch.column))
        lex = column_lexical_score(keywords, ch.column, meta)
        combined = ch.score * (1.0 + LEXICAL_COLUMN_WEIGHT * lex)
        scored.append((ch, combined))
    scored.sort(key=lambda x: -x[1])
    return scored


def _select_columns_with_coverage(
    scored: list[tuple[ColumnHit, float]],
    keywords: list[str],
    col_meta: dict[str, dict],
    limit: int,
) -> list[ColumnHit]:
    """同 K 预算：先为每个问句关键词占坑，再按分数补齐；占坑列固定占前位。"""
    if limit <= 0 or not scored:
        return []

    pinned: list[ColumnHit] = []
    used: set[tuple[str, str]] = set()

    for kw in keywords:
        best: ColumnHit | None = None
        best_score = -1.0
        for ch, base in scored:
            if ch.key in used:
                continue
            meta = col_meta.get(_norm(ch.column))
            text = _column_search_text(ch.column, meta)
            if _keyword_hits([kw], text, ch.column) <= 0 and not _concept_in_text(kw, text):
                continue
            if base > best_score:
                best_score = base
                best = ch
        if best is not None:
            pinned.append(best)
            used.add(best.key)

    rest: list[ColumnHit] = []
    for ch, _base in scored:
        if len(pinned) + len(rest) >= limit:
            break
        if ch.key not in used:
            rest.append(ch)
            used.add(ch.key)

    return (pinned + rest)[:limit]


def _inject_concept_columns(
    merged: dict[tuple[str, str], ColumnHit],
    *,
    matched_concepts: list[str],
    col_meta: dict[str, dict],
    db_name: str,
    table_name: str,
    floor_score: float,
) -> None:
    """按业务概念在列说明里语义补列（不对英文字段名硬绑）。"""
    from wenshu.services.business_concept import column_matches_concept

    if not matched_concepts:
        return
    concept_map = _concepts()
    base = max(0.05, floor_score * CONCEPT_COLUMN_SCORE_FACTOR)
    step = 0.01
    n = 0
    for concept in matched_concepts:
        for col_norm, meta in col_meta.items():
            col_raw = str(meta.get("column_name") or col_norm)
            key = (_norm(table_name), col_norm)
            if not column_matches_concept(concept, col_raw, meta, concept_map):
                continue
            score = base - step * n
            prev = merged.get(key)
            if prev is not None:
                if score > prev.score:
                    merged[key] = ColumnHit(
                        db=prev.db or db_name,
                        table=prev.table or table_name,
                        column=prev.column or col_raw,
                        score=score,
                        source=prev.source if prev.source != "vector" else "concept",
                        payload={
                            **(prev.payload or {}),
                            "description": str(
                                meta.get("description") or meta.get("hive_comment") or ""
                            ),
                            "matched_concept": concept,
                        },
                    )
                n += 1
                continue
            merged[key] = ColumnHit(
                db=db_name,
                table=table_name,
                column=col_raw,
                score=score,
                source="concept",
                payload={
                    "description": str(meta.get("description") or meta.get("hive_comment") or ""),
                    "matched_concept": concept,
                },
            )
            n += 1


def _looks_multi_table(
    raw_hits: list,
    table_scores: dict[str, float],
    *,
    question: str = "",
    hint_tables: list[str] | None = None,
) -> bool:
    """多表问句不走单表聚焦。实体概念≥2、表 hint≥2、或显式多实体词触发。"""
    if not is_likely_single_table_question(question):
        return True
    if len(question_matched_entity_concepts(question)) >= 2:
        return True
    entity_hits = question_matched_entity_concepts(question)
    attr_hits = [
        c for c in question_matched_concepts(question) if c not in _entity_concepts()
    ]
    cross = _cross_table_attributes()
    if entity_hits and any(a in cross for a in attr_hits):
        return True
    hints = hint_tables if hint_tables is not None else []
    if len(hints) >= 2:
        return True

    join_tables: set[str] = set()
    join_scores: list[float] = []
    ranked = sorted(table_scores.items(), key=lambda x: -x[1])
    for hit in raw_hits[:40]:
        payload = _hit_payload(hit)
        if payload.get("object_type") != "join":
            continue
        join_scores.append(_hit_score(hit))
        for side in ("left", "right"):
            _db, table, _col = _parse_join_side(payload.get(side))
            if table:
                join_tables.add(_norm(table))
    if join_scores and max(join_scores) >= 0.50 and len(join_tables) >= 2:
        top_norms = {_norm(t) for t, _s in ranked[:4]}
        if len(join_tables & top_norms) >= 2:
            return True
    return False


def _fetch_enabled_table_casing(meta_engine: Engine, db_name: str) -> dict[str, str]:
    """L1 已启用表：norm -> 原始表名。"""
    if not db_name:
        return {}
    sql = """
        SELECT table_name FROM table_meta
        WHERE is_enabled = 1 AND db_name = :db
    """
    out: dict[str, str] = {}
    with meta_engine.connect() as conn:
        for row in conn.execute(text(sql), {"db": db_name}).fetchall():
            raw = str(row[0] or "")
            if raw:
                out[_norm(raw)] = raw
    return out


def _prune_unhinted_fact_tables(tables: list[str], hint_tables: list[str]) -> list[str]:
    """问句已有表 hint 时，丢掉未点名的竞争事实表。"""
    if not hint_tables:
        return tables
    hinted = {_norm(t) for t in hint_tables}
    out: list[str] = []
    seen: set[str] = set()
    for t in tables:
        key = _norm(t)
        if key in seen:
            continue
        if key in _fact_need_hint() and key not in hinted:
            continue
        seen.add(key)
        out.append(t)
    return out


def _merge_hint_tables(
    selected: list[str],
    hint_tables: list[str],
    casing_map: dict[str, str],
    enabled: set[str] | None,
    *,
    limit: int,
) -> list[str]:
    """hint 表置前，再接向量 Top-N；宁可多带一张。"""
    out: list[str] = []
    seen: set[str] = set()
    for t in list(hint_tables) + list(selected):
        key = _norm(t)
        if not key or key in seen:
            continue
        if enabled is not None and key not in enabled:
            continue
        seen.add(key)
        out.append(casing_map.get(key, t))
        if len(out) >= max(limit, len(hint_tables)):
            break
    return out


def _neighbor_allowed(
    table_name: str,
    *,
    hinted: set[str],
    seed: set[str],
) -> bool:
    key = _norm(table_name)
    if key in _fact_need_hint() and key not in hinted and key not in seed:
        return False
    return True


def expand_join_neighbors(
    meta_engine: Engine,
    db_name: str,
    tables: list[str],
    *,
    max_tables: int = DEFAULT_MAX_TABLES_AFTER_EXPAND,
    hops: int = 1,
    prefer_tables: list[str] | None = None,
    enabled_tables: set[str] | None = None,
    hinted_tables: list[str] | None = None,
) -> list[str]:
    """沿 L1 table_relation 做邻表扩展；prefer/hint 优先，未点名事实表不自动带入。"""
    if not tables and not prefer_tables:
        return []

    hinted = {_norm(t) for t in (hinted_tables or prefer_tables or [])}
    prefer = list(prefer_tables or [])
    seed_order = list(prefer) + [t for t in tables if _norm(t) not in {_norm(x) for x in prefer}]
    seed = {_norm(t): t for t in seed_order}
    expanded_order: list[str] = []
    seen: set[str] = set()

    def _add(table_name: str) -> bool:
        key = _norm(table_name)
        if not key or key in seen:
            return False
        if enabled_tables is not None and key not in enabled_tables:
            return False
        seen.add(key)
        expanded_order.append(seed.get(key, table_name))
        return True

    for t in seed_order:
        if len(expanded_order) >= max_tables:
            break
        _add(t)

    if not db_name:
        return expanded_order[:max_tables]

    db_norm = _norm(db_name)
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT left_db, left_table, right_db, right_table
                FROM table_relation
                WHERE is_enabled = 1
                  AND (left_db = :db OR right_db = :db)
                """
            ),
            {"db": db_name},
        ).fetchall()

    adj: dict[str, list[str]] = defaultdict(list)
    seed_keys = set(seen)
    for left_db, left_table, right_db, right_table in rows:
        left_key, right_key = _norm(left_table), _norm(right_table)
        if enabled_tables is not None:
            if left_key not in enabled_tables or right_key not in enabled_tables:
                continue
        if _norm(left_db) == db_norm or _norm(right_db) == db_norm:
            adj[left_key].append(right_table)
            adj[right_key].append(left_table)

    hop_count = max(1, int(hops))
    frontier = list(expanded_order)
    for _hop in range(hop_count):
        if len(expanded_order) >= max_tables:
            break
        nxt: list[str] = []
        current = {_norm(t) for t in frontier}
        candidates: list[str] = []
        seen_cand: set[str] = set()
        for src in current:
            for nb in adj.get(src, []):
                nb_key = _norm(nb)
                if nb_key in seen or nb_key in seen_cand:
                    continue
                if not _neighbor_allowed(nb, hinted=hinted, seed=seed_keys):
                    continue
                seen_cand.add(nb_key)
                candidates.append(nb)

        def _nb_rank(name: str) -> tuple[int, str]:
            key = _norm(name)
            if key in hinted:
                return (0, key)
            if key in _fact_need_hint():
                return (2, key)
            return (1, key)

        candidates.sort(key=_nb_rank)
        for nb in candidates:
            if len(expanded_order) >= max_tables:
                break
            if _add(nb):
                nxt.append(expanded_order[-1])
        frontier = nxt
        if not frontier:
            break

    missing = [t for t in prefer if _norm(t) not in seen]
    if missing and hop_count < 2:
        return expand_join_neighbors(
            meta_engine,
            db_name,
            expanded_order,
            max_tables=max_tables,
            hops=2,
            prefer_tables=prefer,
            enabled_tables=enabled_tables,
            hinted_tables=hinted_tables or prefer,
        )

    return expanded_order[:max_tables]


def fetch_join_relations(
    meta_engine: Engine | None,
    db_name: str,
) -> list[tuple[str, str, str, str]]:
    """L1 启用的 JOIN 边：(left_table, left_column, right_table, right_column)。"""
    if meta_engine is None or not db_name:
        return []
    with meta_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT left_table, left_column, right_table, right_column
                FROM table_relation
                WHERE is_enabled = 1
                  AND (left_db = :db OR right_db = :db)
                """
            ),
            {"db": db_name},
        ).fetchall()
    out: list[tuple[str, str, str, str]] = []
    for left_table, left_column, right_table, right_column in rows:
        lt = str(left_table or "").strip()
        lc = str(left_column or "").strip()
        rt = str(right_table or "").strip()
        rc = str(right_column or "").strip()
        if lt and lc and rt and rc:
            out.append((lt, lc, rt, rc))
    return out


def make_join_key_factory(meta_engine: Engine | None, db_name: str):
    """选完后按 L1 补键：列可以不在检索池里，但必须在 column_meta 且已启用。"""

    meta_cache: dict[str, dict[str, dict]] = {}

    def factory(table: str, column: str) -> ColumnHit | None:
        if meta_engine is None or not db_name or not table or not column:
            return None
        tnorm = _norm(table)
        if tnorm not in meta_cache:
            meta_cache[tnorm] = _fetch_column_meta_for_table(
                meta_engine, db_name, table
            )
        meta = (meta_cache[tnorm] or {}).get(_norm(column))
        if not meta:
            return None
        return ColumnHit(
            db=db_name,
            table=table,
            column=str(meta.get("column_name") or column),
            score=0.28,
            source="join_key",
            payload={
                "description": str(
                    meta.get("description") or meta.get("hive_comment") or ""
                ),
            },
        )

    return factory


def _order_table_columns(columns: list[ColumnHit]) -> list[ColumnHit]:
    preferred = [c for c in columns if c.source in _PREFERRED_COL_SOURCES]
    rest = [c for c in columns if c.source not in _PREFERRED_COL_SOURCES]
    preferred.sort(key=lambda c: -c.score)
    rest.sort(key=lambda c: -c.score)
    return preferred + rest


def _resolve_quota_core_tables(
    hint_tables: list[str],
    selected_tables: list[str],
    table_scores: dict[str, float],
    *,
    casing_map: dict[str, str],
    max_tables: int = MULTI_TABLE_CORE_MAX,
) -> list[str]:
    """多表列配额核心表：hint 优先 + 检索已选 Top 表（不再额外塞 table_scores 第四名）。"""
    del table_scores  # 保留签名兼容；避免 prd_info 等靠分挤占客户/用信核心位
    out: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        key = _norm(name)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(casing_map.get(key, name))

    for t in hint_tables or []:
        _add(t)
    for t in selected_tables or []:
        _add(t)
        if len(out) >= max_tables:
            break
    return out[:max_tables]


def _multi_table_min_per_table(n_core: int, *, front: int = COL_HIT_FRONT_SLOTS) -> int:
    """按核心表数量动态配额：3 表题每表 2 列，2 表题每表 3 列（用于 30 槽池，非前排深配额）。"""
    n = max(1, min(n_core, MULTI_TABLE_CORE_MAX))
    if n >= 3:
        return 2
    if n == 2:
        return min(3, front // 2)
    return DEFAULT_MIN_COLUMNS_PER_TABLE


def _assemble_col_hit_front(
    *,
    keyword_pinned: list[ColumnHit],
    core_tables: list[str],
    by_table: dict[str, list[ColumnHit]],
    scored: list[ColumnHit],
    front_n: int,
    min_per_table: int = 1,
) -> list[ColumnHit]:
    """Col Hit@10 前排：关键词 pin + 每核心表至少 1 列，再按分补齐（不做深 min_per 配额）。"""
    del min_per_table
    front: list[ColumnHit] = []
    used: set[tuple[str, str]] = set()

    def _take(ch: ColumnHit) -> bool:
        if ch.key in used:
            return False
        used.add(ch.key)
        return True

    for ch in keyword_pinned:
        if len(front) >= front_n:
            break
        if _take(ch):
            front.append(ch)

    core_norm = [_norm(t) for t in core_tables if t]
    # 每张核心表至少 1 列进前排，避免 Table Hit@10 因列序掉表
    for table_key in core_norm:
        if len(front) >= front_n:
            break
        for ch in by_table.get(table_key, []):
            if _take(ch):
                front.append(ch)
                break

    for ch in scored:
        if len(front) >= front_n:
            break
        if _take(ch):
            front.append(ch)

    return front


def _select_columns_with_table_quota(
    columns: list[ColumnHit],
    *,
    tables: list[str],
    core_tables: list[str],
    limit: int,
    min_per_table: int = DEFAULT_MIN_COLUMNS_PER_TABLE,
    keywords: list[str] | None = None,
    question: str = "",
    col_meta_by_table: dict[str, dict[str, dict]] | None = None,
    pin_allow_tables: set[str] | None = None,
    max_keyword_pins: int = MULTI_TABLE_MAX_KEYWORD_PINS,
    front_priority: int | None = None,
) -> list[ColumnHit]:
    """先覆盖问句词、再给核心表配额，最后按分补齐。核心表列固定占前位。"""
    if limit <= 0 or not columns:
        return []

    by_table: dict[str, list[ColumnHit]] = defaultdict(list)
    for ch in columns:
        by_table[_norm(ch.table)].append(ch)
    for key in list(by_table):
        by_table[key] = _order_table_columns(by_table[key])

    keyword_pinned: list[ColumnHit] = []
    pinned: list[ColumnHit] = []
    used: set[tuple[str, str]] = set()

    def _pin(ch: ColumnHit) -> bool:
        if ch.key in used or len(pinned) >= limit:
            return False
        pinned.append(ch)
        used.add(ch.key)
        return True

    scored = sorted(columns, key=lambda c: -c.score)
    cov_kw = _coverage_keywords(keywords or [], question or "")
    col_meta_by_table = col_meta_by_table or {}
    concept_map = _concepts()
    for kw in cov_kw[:max_keyword_pins]:
        best: ColumnHit | None = None
        best_score = -1.0
        for ch in scored:
            if ch.key in used:
                continue
            if pin_allow_tables is not None and _norm(ch.table) not in pin_allow_tables:
                continue
            meta = (col_meta_by_table.get(_norm(ch.table)) or {}).get(_norm(ch.column))
            text = _column_search_text(ch.column, meta)
            from wenshu.services.business_concept import column_matches_concept

            hit = _keyword_hits([kw], text, ch.column) > 0 or (
                concept_map
                and column_matches_concept(kw, ch.column, meta, concept_map)
            )
            if not hit:
                continue
            if ch.score > best_score:
                best_score = ch.score
                best = ch
        if best is not None and best.key not in used:
            keyword_pinned.append(best)
            pinned.append(best)
            used.add(best.key)

    core = []
    seen_core: set[str] = set()
    for t in core_tables:
        key = _norm(t)
        if not key or key in seen_core:
            continue
        seen_core.add(key)
        core.append(key)

    if front_priority is not None and front_priority > 0 and len(core) >= 2:
        front = _assemble_col_hit_front(
            keyword_pinned=keyword_pinned,
            core_tables=core,
            by_table=by_table,
            scored=scored,
            front_n=min(front_priority, limit),
        )
        used = {c.key for c in front}
        tail: list[ColumnHit] = []
        for table_key in core:
            have = sum(
                1 for c in front + tail if _norm(c.table) == table_key
            )
            need = max(0, min_per_table - have)
            for ch in by_table.get(table_key, []):
                if need <= 0 or len(front) + len(tail) >= limit:
                    break
                if ch.key in used:
                    continue
                tail.append(ch)
                used.add(ch.key)
                need -= 1
        for ch in pinned:
            if ch.key in used:
                continue
            if len(front) + len(tail) >= limit:
                break
            tail.append(ch)
            used.add(ch.key)
        for ch in scored:
            if len(front) + len(tail) >= limit:
                break
            if ch.key in used:
                continue
            tail.append(ch)
            used.add(ch.key)
        return (front + tail)[:limit]

    for table_key in core:
        have = sum(1 for c in pinned if _norm(c.table) == table_key)
        need = max(0, min_per_table - have)
        if need <= 0:
            continue
        for ch in by_table.get(table_key, []):
            if need <= 0 or len(pinned) >= limit:
                break
            if _pin(ch):
                need -= 1

    for table_key in core:
        if len(pinned) >= limit:
            break
        for ch in by_table.get(table_key, []):
            if ch.source == "join_key" and _pin(ch):
                break

    for ch in scored:
        if len(pinned) >= limit:
            break
        _pin(ch)

    return pinned[:limit]


def _collect_vector_columns(
    hits: list,
    allowed_tables: set[str],
) -> dict[tuple[str, str], ColumnHit]:
    """从向量 hits 收集 column/join 关联列。"""
    out: dict[tuple[str, str], ColumnHit] = {}

    for hit in hits:
        payload = _hit_payload(hit)
        score = _hit_score(hit)
        obj_type = payload.get("object_type")
        db = str(payload.get("db") or payload.get("left_db") or "")

        if obj_type == "column":
            table = payload.get("table")
            column = payload.get("column")
            if not table or not column or _norm(table) not in allowed_tables:
                continue
            ch = ColumnHit(
                db=db,
                table=str(table),
                column=str(column),
                score=score,
                source="vector",
                payload=payload,
            )
            prev = out.get(ch.key)
            if prev is None or ch.score > prev.score:
                out[ch.key] = ch
        elif obj_type == "join":
            for side in ("left", "right"):
                side_db, table, column = _parse_join_side(payload.get(side))
                if not table or not column or _norm(table) not in allowed_tables:
                    continue
                ch = ColumnHit(
                    db=side_db or db,
                    table=table,
                    column=column,
                    score=score,
                    source="join_key",
                    payload=payload,
                )
                prev = out.get(ch.key)
                if prev is None or ch.score > prev.score:
                    out[ch.key] = ch

    return out


def _rerank_columns_by_table_from_qdrant(
    client,
    collection_name: str,
    query_vector: list[float],
    db_name: str,
    tables: list[str],
    *,
    columns_per_table: int = DEFAULT_COLUMNS_PER_TABLE,
) -> dict[str, list[ColumnHit]]:
    """对每个候选表做 column 过滤 ANN（比全库 scroll 更快）。"""
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    if not db_name or not tables:
        return {}

    out: dict[str, list[ColumnHit]] = {}
    for table in tables:
        table_raw = str(table)
        # payload 中 table 通常小写；Qdrant MatchValue 精确匹配
        table_filter = Filter(
            must=[
                FieldCondition(key="object_type", match=MatchValue(value="column")),
                FieldCondition(key="db", match=MatchValue(value=db_name)),
                FieldCondition(key="table", match=MatchValue(value=table_raw)),
            ]
        )
        try:
            result = client.query_points(
                collection_name=collection_name,
                query=query_vector,
                limit=max(columns_per_table, 16),
                query_filter=table_filter,
            )
            points = list(result.points or [])
        except Exception:
            # 兼容 payload 小写表名
            table_filter = Filter(
                must=[
                    FieldCondition(key="object_type", match=MatchValue(value="column")),
                    FieldCondition(key="db", match=MatchValue(value=db_name)),
                    FieldCondition(key="table", match=MatchValue(value=_norm(table_raw))),
                ]
            )
            result = client.query_points(
                collection_name=collection_name,
                query=query_vector,
                limit=max(columns_per_table, 16),
                query_filter=table_filter,
            )
            points = list(result.points or [])

        hits: list[ColumnHit] = []
        for point in points:
            payload = point.payload or {}
            column = payload.get("column")
            if not column:
                continue
            hits.append(
                ColumnHit(
                    db=str(payload.get("db") or db_name),
                    table=str(payload.get("table") or table_raw),
                    column=str(column),
                    score=float(getattr(point, "score", 0.0) or 0.0),
                    source="rerank",
                    payload=dict(payload),
                )
            )
        hits.sort(key=lambda c: -c.score)
        out[table_raw] = hits[:columns_per_table]

    return out


def _cover_tables_for_selection(core: list[str], columns: list[ColumnHit]) -> list[str]:
    """S1/S2 保底：问句 hint 表 + 已经进池的表，避免精选把进槽的表砍光。"""
    out: list[str] = []
    seen: set[str] = set()
    for t in list(core or []) + [c.table for c in columns]:
        key = _norm(t)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _enrich_column_descriptions(
    columns: list[ColumnHit],
    meta_engine: Engine | None,
    db_name: str,
) -> None:
    """给 S1/S2 提示补上中文含义（向量 payload 本身不含 description）。"""
    if not columns or meta_engine is None or not db_name:
        return
    tables: list[str] = []
    seen: set[str] = set()
    for ch in columns:
        key = _norm(ch.table)
        if not key or key in seen:
            continue
        seen.add(key)
        tables.append(ch.table)
    meta_by: dict[str, dict[str, dict]] = {}
    for table in tables:
        meta_by[_norm(table)] = _fetch_column_meta_for_table(meta_engine, db_name, table)
    for ch in columns:
        payload = dict(ch.payload or {})
        if str(payload.get("description") or "").strip():
            continue
        meta = (meta_by.get(_norm(ch.table)) or {}).get(_norm(ch.column)) or {}
        desc = str(meta.get("description") or meta.get("hive_comment") or "").strip()
        if desc:
            payload["description"] = desc
            ch.payload = payload


def _with_column_selection(
    result: SchemaRetrievalResult,
    *,
    question: str,
    evidence: str,
    column_select: bool | None,
    meta_engine: Engine | None = None,
    cover_tables: list[str] | None = None,
) -> SchemaRetrievalResult:
    """对 S_rtrv（result.columns）做两轮 LLM 选列；点名表至少留槽，失败则回退池前 N 列。"""
    enabled = resolve_column_select(column_select)
    if not enabled or not result.columns:
        result.selection_meta = {
            "enabled": bool(enabled),
            "s1_count": 0,
            "s2_count": 0,
        }
        return result
    _enrich_column_descriptions(result.columns, meta_engine, result.db_name)
    cover_columns = hinted_column_keys_from_pool(question, result.columns)
    join_relations = fetch_join_relations(meta_engine, result.db_name)
    key_factory = make_join_key_factory(meta_engine, result.db_name)
    try:
        sel = select_columns_s1_s2(
            result.columns,
            question=question,
            evidence=evidence,
            cover_columns=cover_columns,
            join_relations=join_relations,
            key_factory=key_factory,
        )
        result.s1_columns = list(sel.s1_columns)
        result.s2_columns = list(sel.s2_columns)
        result.selection_meta = {
            "enabled": True,
            "s1_source": sel.s1_source,
            "s2_source": sel.s2_source,
            "rounds": sel.rounds,
            "s1_count": len(sel.s1_columns),
            "s2_count": len(sel.s2_columns),
            "cover_columns": [f"{t}.{c}" for t, c in cover_columns],
        }
    except Exception as exc:
        s1 = list(result.columns[:8])
        seen = {c.key for c in s1}
        extra = [c for c in result.columns[8:16] if c.key not in seen]
        s2 = s1 + extra
        s1 = _ensure_join_keys(
            s1,
            result.columns,
            relations=join_relations,
            key_factory=key_factory,
        )
        s2 = _ensure_join_keys(
            s2,
            result.columns,
            relations=join_relations,
            key_factory=key_factory,
        )
        result.s1_columns = s1
        result.s2_columns = s2
        result.selection_meta = {
            "enabled": True,
            "s1_source": "error_fallback",
            "s2_source": "error_fallback",
            "rounds": 0,
            "s1_count": len(result.s1_columns),
            "s2_count": len(result.s2_columns),
            "error": str(exc)[:200],
        }
    return result


def retrieve_schema(
    *,
    client,
    collection_name: str,
    meta_engine: Engine,
    query_vector: list[float],
    question: str = "",
    evidence: str = "",
    db_names: list[str] | None = None,
    vector_limit: int = DEFAULT_VECTOR_LIMIT,
    top_tables: int = DEFAULT_TOP_TABLES,
    columns_per_table: int = DEFAULT_COLUMNS_PER_TABLE,
    max_tables_after_expand: int = DEFAULT_MAX_TABLES_AFTER_EXPAND,
    max_output_columns: int | None = None,
    retrieval_style: str | None = None,
    keyword_mode: str | None = None,
    column_select: bool | None = None,
) -> SchemaRetrievalResult:
    """
    召回主入口（默认 xiyan）：
    1. xiyan：Q+Evidence 抽词 → sim(Q,Table)×sim(kw,col) 多路合并；legacy：整句单向量
    2. 只搜 column + join（table 点不参与混排）
    3. 聚合定表 Top-N；单表问句时聚焦单表并做问句词覆盖选列
    4. table_relation 邻表扩展（多表路径）
    5. 表内向量 rerank 补列；JOIN 键不在组池注入，选完后按 L1 补两端键
    """
    refresh_retrieval_lexicon(meta_engine)
    allowed_db = {_norm(d) for d in db_names} if db_names else None
    style = resolve_retrieval_style(retrieval_style)
    query_keywords: list[str] = []
    keyword_source = "rule"
    query_roles: dict = {}

    if style == "xiyan":
        raw_hits, query_keywords, keyword_source, query_roles = xiyan_multipath_hits(
            client,
            collection_name,
            query_vector,
            question=question,
            evidence=evidence,
            db_names=db_names,
            vector_limit=vector_limit,
            keyword_mode=keyword_mode,
        )
    else:
        raw_hits = search_collection(
            client,
            collection_name,
            query_vector,
            limit=vector_limit,
            db_names=db_names,
            include_types=_INCLUDE_TYPES_V2,
        )
        from wenshu.services.keyword_llm import extract_roles_resolved

        roles = extract_roles_resolved(question, evidence, mode=keyword_mode)
        query_keywords = roles.keywords
        keyword_source = roles.source
        query_roles = roles.as_dict()

    db_name = ""
    for hit in raw_hits:
        payload = _hit_payload(hit)
        if payload.get("db"):
            db_name = str(payload["db"])
            break
        if payload.get("left_db"):
            db_name = str(payload["left_db"])
            break
    if not db_name and db_names:
        db_name = db_names[0]

    casing_map = _table_casing_from_hits(raw_hits)
    enabled_casing = _fetch_enabled_table_casing(meta_engine, db_name)
    if enabled_casing:
        casing_map.update(enabled_casing)
        enabled_tables: set[str] | None = set(enabled_casing)
    else:
        enabled_tables = None

    table_scores = aggregate_table_scores_enhanced(raw_hits, db_names=allowed_db)
    if not table_scores:
        table_scores = aggregate_table_scores(raw_hits, db_names=allowed_db)
    if enabled_tables:
        table_scores = {k: v for k, v in table_scores.items() if k in enabled_tables}

    keywords = query_keywords or extract_query_keywords(question, evidence)
    hint_tables = resolve_hint_tables(
        question,
        query_roles=query_roles,
        raw_hits=raw_hits,
        enabled=enabled_tables,
        casing_map=casing_map,
    )
    multi_table = _looks_multi_table(
        raw_hits, table_scores, question=question, hint_tables=hint_tables
    )
    # 分角色：≥2 个表短语 + 关系短语 → 强制多表路径
    role_tables = query_roles.get("table_phrases") or []
    role_joins = query_roles.get("join_phrases") or []
    if len(role_tables) >= 2 and role_joins:
        multi_table = True
    # 意图层：实体与度量落在不同表时，禁止单表收口
    if query_roles.get("force_multi_table"):
        multi_table = True
    focus_norm, single_focus = (None, False)
    if not multi_table:
        focus_norm, single_focus = pick_focus_table(
            table_scores,
            question=question,
            meta_engine=meta_engine,
            db_name=db_name,
            casing_map=casing_map,
            raw_hits=raw_hits,
            hint_tables=hint_tables,
        )
        if single_focus and focus_norm:
            focus_table_chk = casing_map.get(focus_norm, focus_norm)
            col_meta_chk = _fetch_column_meta_for_table(meta_engine, db_name, focus_table_chk)
            if col_meta_chk and not column_hints_resolved_on_table(
                question, col_meta_chk, focus_norm
            ):
                single_focus = False
            elif hint_tables and focus_norm not in {_norm(t) for t in hint_tables}:
                # 向量聚焦表与问句表 hint 不一致时宁可多带
                single_focus = False

    ranked_tables = sorted(table_scores.items(), key=lambda x: -x[1])
    selected_tables = [casing_map.get(t, t) for t, _s in ranked_tables[:top_tables]]

    if single_focus and focus_norm:
        focus_table = casing_map.get(focus_norm, focus_norm)
        selected_tables = [focus_table]
        expanded_tables = [focus_table]
        pool_limit = max(
            80,
            (max_output_columns or columns_per_table) * 3,
        )
        rerank_by_table = _rerank_columns_by_table_from_qdrant(
            client,
            collection_name,
            query_vector,
            db_name,
            expanded_tables,
            columns_per_table=pool_limit,
        )
        merged: dict[tuple[str, str], ColumnHit] = {}
        for _table, reranked in rerank_by_table.items():
            for ch in reranked:
                prev = merged.get(ch.key)
                if prev is None or ch.score > prev.score:
                    merged[ch.key] = ch
        for ch in _collect_vector_columns(raw_hits, {focus_norm}).values():
            prev = merged.get(ch.key)
            if prev is None or ch.score > prev.score:
                merged[ch.key] = ch

        col_meta = _fetch_column_meta_for_table(meta_engine, db_name, focus_table)
        floor = min((c.score for c in merged.values()), default=0.2)
        matched = question_matched_concepts(question, evidence)
        _inject_concept_columns(
            merged,
            matched_concepts=matched,
            col_meta=col_meta,
            db_name=db_name,
            table_name=focus_table,
            floor_score=floor,
        )
        cov_kw = _coverage_keywords(keywords, question)
        scored = _rerank_with_lexical(list(merged.values()), cov_kw or keywords, col_meta)
        out_limit = max_output_columns if max_output_columns else columns_per_table
        if scored:
            columns = _select_columns_with_coverage(
                scored, cov_kw or keywords[:6], col_meta, out_limit
            )
        else:
            columns = [ch for ch, _s in scored[:out_limit]]

        return _with_column_selection(
            SchemaRetrievalResult(
                db_name=db_name,
                raw_hits=raw_hits,
                selected_tables=selected_tables,
                expanded_tables=expanded_tables,
                columns=columns,
                table_scores=table_scores,
                query_keywords=keywords,
                keyword_source=keyword_source,
                retrieval_style=style,
                query_roles=query_roles,
            ),
            question=question,
            evidence=evidence,
            column_select=column_select,
            meta_engine=meta_engine,
            cover_tables=[focus_table],
        )

    selected_tables = _prune_unhinted_fact_tables(selected_tables, hint_tables)
    selected_tables = _merge_hint_tables(
        selected_tables,
        hint_tables,
        casing_map,
        enabled_tables,
        limit=top_tables,
    )
    expanded_tables = expand_join_neighbors(
        meta_engine,
        db_name,
        selected_tables,
        max_tables=max_tables_after_expand,
        hops=1,
        prefer_tables=[casing_map.get(t, t) for t in hint_tables],
        enabled_tables=enabled_tables,
        hinted_tables=hint_tables,
    )
    allowed_table_set = {_norm(t) for t in expanded_tables}

    merged = _collect_vector_columns(raw_hits, allowed_table_set)

    rerank_by_table = _rerank_columns_by_table_from_qdrant(
        client,
        collection_name,
        query_vector,
        db_name,
        expanded_tables,
        columns_per_table=max(columns_per_table, 16),
    )
    for _table, reranked in rerank_by_table.items():
        for ch in reranked:
            prev = merged.get(ch.key)
            if prev is None or ch.score > prev.score:
                merged[ch.key] = ch

    col_meta_by_table: dict[str, dict[str, dict]] = {}
    for table in expanded_tables:
        col_meta_by_table[_norm(table)] = _fetch_column_meta_for_table(
            meta_engine, db_name, table
        )
    floor = min((c.score for c in merged.values()), default=0.2)
    matched = question_matched_concepts(question, evidence)
    cov_kw = _coverage_keywords(keywords, question)
    for table in expanded_tables:
        col_meta = col_meta_by_table.get(_norm(table)) or {}
        _inject_concept_columns(
            merged,
            matched_concepts=matched,
            col_meta=col_meta,
            db_name=db_name,
            table_name=table,
            floor_score=floor,
        )

    out_limit = max_output_columns if max_output_columns else columns_per_table * max(len(expanded_tables), 1)
    quota_core = _resolve_quota_core_tables(
        hint_tables,
        selected_tables,
        table_scores,
        casing_map=casing_map,
        max_tables=MULTI_TABLE_CORE_MAX,
    )
    min_per = (
        _multi_table_min_per_table(len(quota_core))
        if len(quota_core) >= 2
        else DEFAULT_MIN_COLUMNS_PER_TABLE
    )
    pin_allow = {_norm(t) for t in quota_core} if len(quota_core) >= 2 else None
    columns = _select_columns_with_table_quota(
        list(merged.values()),
        tables=expanded_tables,
        core_tables=quota_core,
        limit=out_limit,
        min_per_table=min_per,
        keywords=keywords,
        question=question,
        col_meta_by_table=col_meta_by_table,
        pin_allow_tables=pin_allow,
        max_keyword_pins=MULTI_TABLE_MAX_KEYWORD_PINS,
        front_priority=COL_HIT_FRONT_SLOTS if len(quota_core) >= 2 else None,
    )

    return _with_column_selection(
        SchemaRetrievalResult(
            db_name=db_name,
            raw_hits=raw_hits,
            selected_tables=selected_tables,
            expanded_tables=expanded_tables,
            columns=columns,
            table_scores=table_scores,
            query_keywords=keywords,
            keyword_source=keyword_source,
            retrieval_style=style,
            query_roles=query_roles,
        ),
        question=question,
        evidence=evidence,
        column_select=column_select,
        meta_engine=meta_engine,
        cover_tables=_cover_tables_for_selection(quota_core or selected_tables, columns),
    )
