"""XiYan 式 Column Selection：从 S_rtrv 迭代选出 S1/S2，选完后按 L1 JOIN 边补两端键。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from wenshu.services.comment_llm import _call_llm_json, llm_available
from wenshu.services.keyword_llm import llm_keyword_available

_SELECT_CACHE: dict[str, dict] = {}
_SELECT_CACHE_MAX = 256


@dataclass
class ColumnSelectionResult:
    s1_columns: list = field(default_factory=list)
    s2_columns: list = field(default_factory=list)
    s1_source: str = "skip"
    s2_source: str = "skip"
    rounds: int = 0

    def as_dict(self) -> dict:
        def _dump(cols: list) -> list[dict]:
            out = []
            for c in cols:
                if hasattr(c, "as_dict"):
                    out.append(c.as_dict())
                else:
                    out.append(
                        {
                            "table": getattr(c, "table", ""),
                            "column": getattr(c, "column", ""),
                        }
                    )
            return out

        return {
            "s1_columns": _dump(self.s1_columns),
            "s2_columns": _dump(self.s2_columns),
            "s1_source": self.s1_source,
            "s2_source": self.s2_source,
            "rounds": self.rounds,
            "s1_count": len(self.s1_columns),
            "s2_count": len(self.s2_columns),
        }


def resolve_column_select(flag: bool | None = None) -> bool:
    if flag is not None:
        return bool(flag)
    raw = (os.getenv("SCHEMA_COLUMN_SELECT") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _timeout() -> int:
    try:
        return int(os.getenv("SCHEMA_COLUMN_SELECT_TIMEOUT", "25"))
    except ValueError:
        return 25


def _col_id(table: str, column: str) -> str:
    return f"{(table or '').strip()}.{(column or '').strip()}"


def _norm_id(table: str, column: str) -> str:
    return _col_id(table, column).lower()


def _parse_selected_ids(data: dict | None, allowed: dict[str, object]) -> list[str]:
    if not isinstance(data, dict):
        return []
    raw = data.get("columns")
    if raw is None:
        for alt in ("selected", "selected_columns", "cols"):
            if isinstance(data.get(alt), list):
                raw = data[alt]
                break
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            table = str(item.get("table") or "").strip()
            column = str(item.get("column") or "").strip()
            text = _col_id(table, column) if table and column else ""
        else:
            text = str(item or "").strip()
        if not text:
            continue
        text = text.replace(" ", "")
        key = text.lower()
        hit = allowed.get(key)
        if hit is None and "." in text:
            col_only = text.split(".")[-1].lower()
            matches = [k for k in allowed if k.endswith("." + col_only)]
            if len(matches) == 1:
                key = matches[0]
                hit = allowed[key]
        if hit is None or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _build_prompt(
    question: str,
    evidence: str,
    pool: list,
    *,
    round_idx: int,
    already: list | None = None,
) -> str:
    from wenshu.services.business_concept import build_concept_hint_block
    from wenshu.services.schema_retrieval import _concepts, question_matched_concepts

    ev = (evidence or "").strip()
    ev_block = f"\nEvidence：\n{ev}\n" if ev else ""
    concept_block = build_concept_hint_block(
        question_matched_concepts(question, evidence),
        _concepts(),
        question=question,
    )
    concept_section = f"\n{concept_block}\n" if concept_block else ""
    lines = []
    for i, c in enumerate(pool, 1):
        payload = getattr(c, "payload", None) or {}
        desc = str(payload.get("description") or payload.get("hive_comment") or "").strip()
        extra = f"  含义：{desc}" if desc else ""
        lines.append(f"{i}. {_col_id(c.table, c.column)}{extra}")
    catalog = "\n".join(lines) if lines else "(无候选列)"
    if round_idx == 1:
        extra_rule = (
            "第 1 轮侧重召回，不要过早精简："
            "选出写 SQL 需要的列（SELECT / WHERE / 聚合 / GROUP BY）；"
            "问句里每个槽位（实体、度量、过滤）只要候选里已有对应列，至少选 1 列。"
        )
        already_block = ""
    else:
        already_ids = [_col_id(c.table, c.column) for c in (already or [])]
        already_block = "\n已选列（不要重复）：\n" + (
            "\n".join(f"- {x}" for x in already_ids) if already_ids else "- （无）"
        )
        extra_rule = (
            "第 2 轮必须补全，禁止交空（有多表候选时尤其禁止）："
            "优先补 ① 尚未覆盖的槽位列（对照【术语说明】与【易混淆提示】）；"
            "② 已选表之间的关联键；"
            "③ WHERE/GROUP BY 过滤列。不要重复已选列，不要为未选中的邻表摊列。"
        )
    return f"""从候选列中选出 Text-to-SQL 所需字段。

用户问题：
{(question or "").strip()}
{ev_block}{concept_section}
候选列（只能从中选择）：
{catalog}
{already_block}

规则：
- {extra_rule}
- 输出必须是候选中出现的 table.column
- 不要编造列名

【重要】只输出一行 JSON：
{{"columns": ["table.column", "..."]}}"""


def _llm_pick(prompt: str, allowed: dict[str, object]) -> list[str] | None:
    if not llm_keyword_available() and not llm_available():
        return None
    data = _call_llm_json(prompt, timeout=_timeout())
    if data is None:
        return None
    return _parse_selected_ids(data, allowed)


def _fallback_pick(pool: list, n: int) -> list:
    if n <= 0 or not pool:
        return []
    return list(pool[:n])


_LINK_COL_MARKERS = (
    "_id",
    "_no",
    "_code",
    "idnum",
    "custid",
    "clno",
    "prd_code",
    "loan_no",
    "app_no",
)


def _is_link_column(column: str) -> bool:
    name = (column or "").strip().lower()
    if not name:
        return False
    if name in {"id", "cust_id", "loan_no", "prd_code", "app_no", "idnum"}:
        return True
    return any(m in name for m in _LINK_COL_MARKERS)


def _table_key(col) -> str:
    return (getattr(col, "table", "") or "").strip().lower()


def _join_peers(col) -> list[tuple[str, str]]:
    payload = getattr(col, "payload", None) or {}
    raw = payload.get("join_peers") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        table = str(item.get("table") or "").strip()
        column = str(item.get("column") or "").strip()
        if not table or not column:
            continue
        key = (table.lower(), column.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append((table, column))
    return out


def _is_join_endpoint(col) -> bool:
    if getattr(col, "source", "") == "join_key":
        return True
    return bool(_join_peers(col))


def _ensure_join_keys(
    selected: list,
    pool: list | None = None,
    *,
    relations: list[tuple[str, str, str, str]] | None = None,
    key_factory: Callable[[str, str], object | None] | None = None,
) -> list:
    """XiYan PFKey：已选 1 张表也跑。

    选中 a.a、边是 a.c=b.c 时补 a.c 和 b.c（只补键，不摊度量列）。
    有 relations 时从 L1 边补，列不必事先在检索池里；没有 relations 时回退为池内 join_key。
    只走已选表的 1 跳，不沿新补进来的邻表再扩。
    """
    if not selected:
        return selected
    have = {_table_key(c) for c in selected if _table_key(c)}
    if not have:
        return selected
    pool = pool or []
    pool_map: dict[str, object] = {}
    for c in list(pool) + list(selected):
        key = _norm_id(getattr(c, "table", ""), getattr(c, "column", ""))
        if key and key not in pool_map:
            pool_map[key] = c

    out = list(selected)
    used = {_norm_id(getattr(c, "table", ""), getattr(c, "column", "")) for c in out}

    def _add_col(col) -> None:
        if col is None:
            return
        key = _norm_id(getattr(col, "table", ""), getattr(col, "column", ""))
        if not key or key in used:
            return
        out.append(col)
        used.add(key)
        pool_map[key] = col

    def _add_tc(table: str, column: str) -> None:
        table = (table or "").strip()
        column = (column or "").strip()
        if not table or not column:
            return
        key = _norm_id(table, column)
        if key in used:
            return
        hit = pool_map.get(key)
        if hit is None and key_factory is not None:
            hit = key_factory(table, column)
        _add_col(hit)

    if relations:
        for left_table, left_column, right_table, right_column in relations:
            left_key = (left_table or "").strip().lower()
            right_key = (right_table or "").strip().lower()
            if left_key not in have and right_key not in have:
                continue
            _add_tc(left_table, left_column)
            _add_tc(right_table, right_column)
        return out

    pool_tables = {_table_key(c) for c in pool if _table_key(c)}
    for c in pool:
        if _table_key(c) in have and _is_join_endpoint(c):
            _add_col(c)
    for c in list(out):
        if _table_key(c) not in have:
            continue
        for peer_table, peer_column in _join_peers(c):
            if peer_table.strip().lower() not in pool_tables:
                continue
            _add_col(pool_map.get(_norm_id(peer_table, peer_column)))
    return out


def _ensure_column_keys(
    selected: list,
    pool: list,
    cover_columns: list[tuple[str, str]] | None,
) -> list:
    """L1 已点名且已在池里的列，精选后至少留 1 列；不编造列。"""
    if not cover_columns or not pool:
        return selected
    pool_map = {
        _norm_id(getattr(c, "table", ""), getattr(c, "column", "")): c for c in pool
    }
    out = list(selected)
    used = {_norm_id(getattr(c, "table", ""), getattr(c, "column", "")) for c in out}
    for table, column in cover_columns:
        key = _norm_id(table, column)
        if not key or key in used:
            continue
        hit = pool_map.get(key)
        if hit is None:
            continue
        out.append(hit)
        used.add(key)
    return out


def _widen_round2(
    remaining: list,
    already: list,
    *,
    question: str = "",
    max_extra: int = 12,
) -> list:
    """第 2 轮 LLM 交空时：已选表上补概念匹配列、关联键，再补同表剩余列。"""
    if not remaining or max_extra <= 0:
        return []
    have_tables = {_table_key(c) for c in already if _table_key(c)}
    concept_cols: list = []
    join_cols: list = []
    rest: list = []
    concept_keys: list[str] = []
    concept_map = None
    if (question or "").strip():
        from wenshu.services.business_concept import column_matches_concept
        from wenshu.services.schema_retrieval import _concepts, question_matched_concepts

        concept_keys = question_matched_concepts(question)
        concept_map = _concepts()
    for c in remaining:
        if _table_key(c) not in have_tables:
            continue
        col = getattr(c, "column", "") or ""
        payload = getattr(c, "payload", None) or {}
        meta = {
            "description": payload.get("description") or "",
            "hive_comment": payload.get("hive_comment") or "",
            "synonyms": payload.get("synonyms") or "",
        }
        if concept_keys and concept_map and any(
            column_matches_concept(k, col, meta, concept_map) for k in concept_keys
        ):
                concept_cols.append(c)
                continue
        if getattr(c, "source", "") == "join_key" or _is_link_column(col):
            join_cols.append(c)
        else:
            rest.append(c)
    out: list = []
    seen: set[str] = set()

    def _take(col) -> None:
        key = _norm_id(getattr(col, "table", ""), getattr(col, "column", ""))
        if not key or key in seen:
            return
        seen.add(key)
        out.append(col)

    for group in (concept_cols, join_cols, rest):
        for c in group:
            if len(out) >= max_extra:
                return out
            _take(c)
    return out


def select_columns_s1_s2(
    pool: list,
    *,
    question: str,
    evidence: str = "",
    fallback_s1: int = 8,
    fallback_s2_extra: int = 8,
    cover_tables: list[str] | None = None,
    cover_columns: list[tuple[str, str]] | None = None,
    join_relations: list[tuple[str, str, str, str]] | None = None,
    key_factory: Callable[[str, str], object | None] | None = None,
) -> ColumnSelectionResult:
    """
    对标 XiYan Algorithm 1（p_s=2）。
    pool 视为 S_rtrv；S2 = S1 ∪ 第二轮增量 ∪ PFKey 两端键 ∪ 槽位来路列。
    """
    if not pool:
        return ColumnSelectionResult()

    cover_col_key = "|".join(
        f"{(t or '').lower()}.{(c or '').lower()}" for t, c in (cover_columns or [])
    )
    rel_key = "|".join(
        f"{a}.{b}={c}.{d}".lower() for a, b, c, d in (join_relations or [])
    )
    cache_key = hashlib.sha256(
        (
            f"s2recall-v5\n{question}\n{evidence}\n"
            + "|".join(_norm_id(c.table, c.column) for c in pool)
            + "\ncovercol:"
            + cover_col_key
            + "\nrel:"
            + rel_key
        ).encode("utf-8")
    ).hexdigest()

    def _apply_pfkey(cols: list) -> list:
        cols = _ensure_join_keys(
            cols,
            pool,
            relations=join_relations,
            key_factory=key_factory,
        )
        return _ensure_column_keys(cols, pool, cover_columns)

    if cache_key in _SELECT_CACHE:
        cached = _SELECT_CACHE[cache_key]
        id_map = {_norm_id(c.table, c.column): c for c in pool}

        def _rehydrate(ids: list[str]) -> list:
            return [id_map[i] for i in ids if i in id_map]

        return ColumnSelectionResult(
            s1_columns=_apply_pfkey(_rehydrate(cached.get("s1_ids") or [])),
            s2_columns=_apply_pfkey(_rehydrate(cached.get("s2_ids") or [])),
            s1_source=cached.get("s1_source") or "cache",
            s2_source=cached.get("s2_source") or "cache",
            rounds=int(cached.get("rounds") or 0),
        )

    allowed = {_norm_id(c.table, c.column): c for c in pool}

    s1_ids = _llm_pick(
        _build_prompt(question, evidence, pool, round_idx=1),
        allowed,
    )
    if s1_ids is None:
        s1_cols = _fallback_pick(pool, fallback_s1)
        s1_source = "fallback"
    elif not s1_ids:
        s1_cols = _fallback_pick(pool, fallback_s1)
        s1_source = "llm_empty_fallback"
    else:
        s1_cols = [allowed[i] for i in s1_ids if i in allowed]
        s1_source = "llm"

    s1_picked = list(s1_cols)
    before_s1 = len(s1_cols)
    s1_cols = _apply_pfkey(s1_cols)
    if s1_source == "llm" and len(s1_cols) > before_s1:
        s1_source = "llm_cover"

    s1_set = {_norm_id(c.table, c.column) for c in s1_cols}
    remaining = [c for c in pool if _norm_id(c.table, c.column) not in s1_set]

    s2_extra: list = []
    s2_source = "skip"
    if remaining:
        extra_ids = _llm_pick(
            _build_prompt(question, evidence, remaining, round_idx=2, already=s1_cols),
            {_norm_id(c.table, c.column): c for c in remaining},
        )
        if extra_ids is None:
            s2_extra = _fallback_pick(remaining, fallback_s2_extra)
            s2_source = "fallback"
        elif not extra_ids:
            s2_extra = _widen_round2(remaining, s1_picked, question=question)
            s2_source = "llm_empty_widen" if s2_extra else "llm"
        else:
            rem_map = {_norm_id(c.table, c.column): c for c in remaining}
            s2_extra = [rem_map[i] for i in extra_ids if i in rem_map]
            s2_source = "llm"

    s2_cols: list = []
    seen: set[str] = set()
    for c in list(s1_cols) + list(s2_extra):
        k = _norm_id(c.table, c.column)
        if k in seen:
            continue
        seen.add(k)
        s2_cols.append(c)
    before_s2 = len(s2_cols)
    s2_cols = _apply_pfkey(s2_cols)
    if s2_source in {"llm", "llm_empty_widen"} and len(s2_cols) > before_s2:
        s2_source = f"{s2_source}_cover"

    result = ColumnSelectionResult(
        s1_columns=s1_cols,
        s2_columns=s2_cols,
        s1_source=s1_source,
        s2_source=s2_source,
        rounds=2 if remaining else 1,
    )
    if len(_SELECT_CACHE) >= _SELECT_CACHE_MAX:
        _SELECT_CACHE.pop(next(iter(_SELECT_CACHE)))
    _SELECT_CACHE[cache_key] = {
        "s1_ids": [_norm_id(c.table, c.column) for c in s1_cols],
        "s2_ids": [_norm_id(c.table, c.column) for c in s2_cols],
        "s1_source": s1_source,
        "s2_source": s2_source,
        "rounds": result.rounds,
    }
    return result
