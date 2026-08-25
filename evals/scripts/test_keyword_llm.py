#!/usr/bin/env python3
"""临时：测试 LLM 抽词是否可用。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from wenshu.services.comment_llm import _call_llm_json, _ensure_llm_env
from wenshu.services.keyword_llm import extract_keywords_llm, _build_llm_prompt
import os

_ensure_llm_env()
print("url:", os.getenv("LOCAL_LLM_URL"))
print("model:", os.getenv("LOCAL_LLM_MODEL"))

q = "年龄大于30的个人客户有多少"
prompt = _build_llm_prompt(q, "")
print("--- prompt ---")
print(prompt[:400])
print("--- raw llm ---")
data = _call_llm_json(prompt, timeout=120)
print("parsed:", data)
print("--- extract ---")
print(extract_keywords_llm(q, timeout=120))
