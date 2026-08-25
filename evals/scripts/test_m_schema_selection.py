#!/usr/bin/env python3
"""无库单测：M-Schema 列阶段选择。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wenshu.services.m_schema import _pick_schema_columns  # noqa: E402
from wenshu.services.schema_retrieval import ColumnHit  # noqa: E402


def _retrieval(**kwargs):
    defaults = {
        "columns": [ColumnHit("t1", "a", 0.9, "vec")],
        "s1_columns": [ColumnHit("t1", "b", 0.8, "s1")],
        "s2_columns": [ColumnHit("t1", "c", 0.7, "s2")],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_pick_s2_by_default() -> None:
    cols, stage = _pick_schema_columns(_retrieval(), "s2", column_select=True)
    assert stage == "s2"
    assert len(cols) == 1
    assert cols[0].column == "c"


def test_pick_s1() -> None:
    cols, stage = _pick_schema_columns(_retrieval(), "s1", column_select=True)
    assert stage == "s1"
    assert cols[0].column == "b"


def test_pick_pool_when_no_column_select() -> None:
    cols, stage = _pick_schema_columns(_retrieval(), "s2", column_select=False)
    assert stage == "pool"
    assert cols[0].column == "a"


def test_fallback_s1_when_no_s2() -> None:
    r = _retrieval(s2_columns=[])
    cols, stage = _pick_schema_columns(r, "s2", column_select=True)
    assert stage == "s1"
    assert cols[0].column == "b"


def test_fallback_pool_when_empty() -> None:
    r = _retrieval(s1_columns=[], s2_columns=[])
    cols, stage = _pick_schema_columns(r, "s2", column_select=True)
    assert stage == "pool"
    assert cols[0].column == "a"
