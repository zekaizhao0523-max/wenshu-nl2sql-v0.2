"""LLM 结构化输出（对齐 icecoding llm.complete_json / complete_structured / complete_sql）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from wenshu.services.comment_llm import _call_llm_json, llm_available

T = TypeVar("T", bound=BaseModel)


@dataclass
class SQLResult:
    sql: str
    used_tables: list[str]


def extract_json(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]
    return raw


def complete_json(prompt: str, schema: dict, *, retries: int = 2, timeout: int = 90) -> dict:
    """要求模型输出符合 schema 的 JSON（Ollama format=json / OpenAI 兼容）。"""
    if not llm_available():
        raise RuntimeError("LLM 未配置，无法做结构化输出")
    props = schema.get("properties", {})
    keys = list(schema.get("required") or props.keys())
    field_hint = ", ".join(f'"{k}"' for k in keys)
    instruction = (
        "\n\n只输出一个 JSON 对象,不要输出任何其它文字、不要用 markdown 代码块、不要解释。\n"
        f"这个对象必须包含以下字段: {field_hint}。\n"
        "字段值必须是具体的实际内容(数据/数组/数字),不要输出字段的结构定义或示例说明。\n"
        f"JSON Schema 参考: {json.dumps(schema, ensure_ascii=False)}"
    )
    last_err: Exception | None = None
    for _ in range(retries + 1):
        try:
            data = _call_llm_json(prompt + instruction, timeout=timeout)
            if not isinstance(data, dict):
                raise ValueError("输出不是 JSON 对象")
            if not (set(keys) & set(data.keys())):
                raise ValueError(f"输出缺少目标字段 {keys}")
            return data
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise ValueError(f"LLM 结构化输出多次解析失败: {last_err}") from last_err


def complete_structured(
    prompt: str,
    model: type[T],
    *,
    retries: int = 2,
    timeout: int = 90,
) -> T:
    """Pydantic 结构化输出；校验失败带错误重试（对齐 icecoding）。"""
    schema = model.model_json_schema()
    attempt_prompt = prompt
    last_err: Exception | None = None
    previous_data: dict | None = None
    for _ in range(retries + 1):
        previous_data = None
        try:
            previous_data = complete_json(attempt_prompt, schema, retries=0, timeout=timeout)
            return model.model_validate(previous_data)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            previous_text = (
                json.dumps(previous_data, ensure_ascii=False, default=str)[:4000]
                if previous_data is not None
                else "无可解析 JSON"
            )
            attempt_prompt = (
                prompt
                + "\n\n上一轮结构化输出未通过 Pydantic 校验。"
                + "请根据错误修正字段名、必填字段和嵌套结构，不要重复原输出。\n"
                + f"上一轮输出: {previous_text}\n"
                + f"校验错误: {exc}\n"
                + "必须严格符合以下 JSON Schema:\n"
                + json.dumps(schema, ensure_ascii=False, default=str)
            )
    raise ValueError(f"LLM 结构化输出多次解析失败: {last_err}") from last_err


def complete_sql(prompt: str, *, retries: int = 2, timeout: int = 90) -> SQLResult:
    """要求同时返回 sql + used_tables（供静态校验交叉比对）。"""
    schema = {
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "used_tables": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["sql", "used_tables"],
    }
    data = complete_json(prompt, schema, retries=retries, timeout=timeout)
    return SQLResult(
        sql=str(data.get("sql") or ""),
        used_tables=[str(t) for t in (data.get("used_tables") or [])],
    )
