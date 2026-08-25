#!/usr/bin/env python3
"""无网单测：模型配置解析与 JSON 提取。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wenshu.services.comment_llm import _parse_json_response  # noqa: E402
from wenshu.services.model_config import (  # noqa: E402
    apply_model_settings_to_environ,
    get_llm_config,
    llm_is_configured,
    resolve_llm_provider,
    save_model_settings,
)


def test_parse_json_response_plain() -> None:
    data = _parse_json_response('{"columns":["a.b"]}')
    assert data == {"columns": ["a.b"]}


def test_parse_json_response_markdown_fence() -> None:
    raw = '```json\n{"ok": true}\n```'
    assert _parse_json_response(raw) == {"ok": True}


def test_parse_json_response_embedded() -> None:
    raw = '说明如下：{"columns":["t.c"]} 结束'
    assert _parse_json_response(raw) == {"columns": ["t.c"]}


def test_llm_provider_auto_openai() -> None:
    with patch.dict(
        os.environ,
        {
            "LLM_PROVIDER": "auto",
            "LLM_API_KEY": "sk-test",
            "LLM_MODEL": "qwen-plus",
            "LOCAL_LLM_URL": "",
        },
        clear=False,
    ):
        assert resolve_llm_provider() == "openai"
        assert llm_is_configured()


def test_llm_provider_ollama() -> None:
    with patch.dict(
        os.environ,
        {
            "LLM_PROVIDER": "ollama",
            "LOCAL_LLM_URL": "http://127.0.0.1:11434",
            "LOCAL_LLM_MODEL": "qwen3:4b",
            "LLM_API_KEY": "",
        },
        clear=False,
    ):
        assert resolve_llm_provider() == "ollama"
        cfg = get_llm_config()
        assert cfg["ollama_url"].endswith("11434")


def test_save_model_settings_persists_key(tmp_path: Path) -> None:
    store = tmp_path / "model_settings.json"
    with patch("wenshu.services.model_config.STORE_PATH", store):
        save_model_settings(
            {
                "llm": {
                    "provider": "openai",
                    "model": "qwen-plus",
                    "api_key": "sk-secret",
                    "api_base": "https://example.com/v1",
                }
            }
        )
        saved = json.loads(store.read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == "sk-secret"
        apply_model_settings_to_environ()
        assert os.getenv("LLM_MODEL") == "qwen-plus"
