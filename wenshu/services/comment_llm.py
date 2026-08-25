"""使用本地 Ollama 或线上 OpenAI 兼容 API 为元数据/召回生成 JSON。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from wenshu.services.model_config import (
    apply_model_settings_to_environ,
    get_llm_config,
    llm_is_configured,
)

# 表级与字段级采用不同输出格式
_TABLE_OUTPUT_FORMAT_RULES = """
表说明输出格式（必须遵守，与字段说明不同）：
- 采用固定句式：「{中文表名}表，用于{业务用途/使用场景}；包含{主要内容、关键维度或数据粒度}。」
- 中文表名：由英文表名推断（2~8 字，勿照抄英文）。
- 「用于」：说明这张表支撑什么分析、什么业务流程、解决什么问题。
- 「包含」：概括主要实体、时间粒度、核心指标或字段主题（可引用关键字段名）。
- 禁止以「该表存储了…」开头；禁止只写一句空泛描述；须优先融合原表 COMMENT 的语义。
- 示例：「产品月还款表，用于跟踪放款后各期还款表现；包含借据、期次、应还/实还金额及逾期状态等。」
"""

_COLUMN_OUTPUT_FORMAT_RULES = """
字段说明输出格式（必须遵守）：
- 每条采用「{中文名}：{中文解释}」，中间用中文冒号「：」分隔。
- 中文名：根据字段名推断的简短中文名称。
- 中文解释：结合字段类型、原表 COMMENT、同表字段，写清含义、取值/单位、关联关系；字数不限。
- 须综合参考原表 COMMENT，禁止只重复英文名或空泛套话。
"""

_SOURCE_COMMENT_RULES = """
参考原表 COMMENT（必须遵守）：
- 「原表 COMMENT」来自业务库建表语句或 information_schema，是字段/表含义的权威参考，须优先阅读并融入输出。
- 若存在原表 COMMENT，不得忽略、不得与之矛盾；表说明融入「用于/包含」句，字段说明融入「中文名：中文解释」。
- 若无原表 COMMENT，再结合字段名、类型、表名及同表其它字段推断。
- 「当前说明」若与原表 COMMENT 不同，表示人工或历史 AI 修改；扩写时仍须兼顾原表 COMMENT 的语义。
"""


def _ensure_llm_env() -> None:
    """确保进程内能读到 .env 与平台 model_settings。"""
    apply_model_settings_to_environ()
    if llm_is_configured():
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        apply_model_settings_to_environ()
    except ImportError:
        pass


def _open_llm_request(req: urllib.request.Request, timeout: int):
    """直连 Ollama，绕过系统 HTTP 代理。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


def _parse_json_response(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def _call_ollama_json(prompt: str, *, model: str, base: str, timeout: int) -> dict | None:
    req_body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    if "qwen3" in model.lower():
        req_body["think"] = False

    payload = json.dumps(req_body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _open_llm_request(req, timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    raw = (body.get("response") or "").strip()
    if not raw:
        raw = (body.get("thinking") or "").strip()
    return _parse_json_response(raw)


def _call_openai_json(prompt: str, *, model: str, timeout: int) -> dict | None:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("缺少 openai 包，请运行: pip install openai") from exc

    base_url = (os.getenv("LLM_API_BASE") or "").strip() or None
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    messages = [
        {
            "role": "system",
            "content": "你是结构化助手。必须只输出一个 JSON 对象，不要 markdown，不要解释。",
        },
        {"role": "user", "content": prompt},
    ]
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    try:
        resp = client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except Exception:
        resp = client.chat.completions.create(**kwargs)

    choice = resp.choices[0].message if resp.choices else None
    raw = (choice.content or "").strip() if choice else ""
    if not raw and choice is not None:
        raw = (getattr(choice, "reasoning_content", None) or "").strip()
    return _parse_json_response(raw)


def _resolve_call_target(*, metadata: bool = False) -> tuple[str, str, str]:
    cfg = get_llm_config(metadata=metadata)
    if cfg["provider"] == "openai":
        return "openai", cfg["model"], ""
    return "ollama", cfg["ollama_model"], cfg["ollama_url"]


def _llm_not_configured_error() -> RuntimeError:
    return RuntimeError(
        "未配置 LLM。请在「向量库同步 → 模型配置」或 .env 中设置："
        "本地 Ollama（LLM_PROVIDER=ollama + LOCAL_LLM_URL），"
        "或线上 API（LLM_PROVIDER=openai + LLM_API_KEY + LLM_API_BASE + LLM_MODEL）。"
    )


def _call_llm_json(prompt: str, timeout: int = 120, *, metadata: bool = False) -> dict | None:
    _ensure_llm_env()
    if not llm_is_configured():
        return None

    provider, model, base = _resolve_call_target(metadata=metadata)
    try:
        if provider == "openai":
            return _call_openai_json(prompt, model=model, timeout=timeout)
        if not base:
            return None
        return _call_ollama_json(prompt, model=model, base=base, timeout=timeout)
    except Exception:
        return None


def _call_llm_json_ex(prompt: str, timeout: int = 120, *, metadata: bool = False) -> dict:
    _ensure_llm_env()
    if not llm_is_configured():
        raise _llm_not_configured_error()

    provider, model, base = _resolve_call_target(metadata=metadata)
    try:
        if provider == "openai":
            data = _call_openai_json(prompt, model=model, timeout=timeout)
        else:
            if not base:
                raise _llm_not_configured_error()
            data = _call_ollama_json(prompt, model=model, base=base, timeout=timeout)
    except RuntimeError:
        raise
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"无法连接 LLM 服务（{provider} / {model}）：{exc}。"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"LLM 请求异常（{provider} / {model}）：{exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"LLM 未返回有效 JSON（{provider} / {model}）。")
    return data


def _comment_base(column: dict) -> str:
    """字段/表已有说明或源注释，作为 AI 扩写基准。"""
    return ((column.get("description") or column.get("hive_comment") or "")).strip()


def _ensure_preserves_base(base: str, generated: str) -> str:
    """确保生成结果包含与源注释相同的核心表述。"""
    base = (base or "").strip()
    gen = (generated or "").strip()
    if not base:
        return gen
    if not gen:
        return base
    if base in gen:
        return gen
    return f"{base}；{gen}"


def _preserve_instruction(
    base: str,
    *,
    hive_comment: str | None = None,
) -> str:
    parts = []
    hc = (hive_comment or "").strip()
    base = (base or "").strip()
    if hc:
        parts.append(f"原表 COMMENT：{hc}")
    if base and base != hc:
        parts.append(f"当前说明：{base}")
    elif base and not hc:
        parts.append(f"当前说明：{base}")
    if not parts:
        return ""
    joined = "\n".join(parts)
    return (
        f"\n{joined}\n"
        "表说明请按「{中文表名}表，用于…；包含…。」扩写，优先保留原表 COMMENT 的核心含义。"
    )


def _format_schema_block(
    db_name: str,
    table_name: str,
    columns: list[dict],
    *,
    table_hive_comment: str | None = None,
    table_description: str | None = None,
    mark_targets: set[str] | None = None,
) -> str:
    """格式化完整表结构供 LLM 参考（区分原表 COMMENT 与当前说明）。"""
    lines = [f"库名: {db_name}", f"表名: {table_name}"]
    thc = (table_hive_comment or "").strip()
    tdesc = (table_description or "").strip()
    if thc:
        lines.append(f"原表 COMMENT（表级）: {thc}")
    if tdesc and tdesc != thc:
        lines.append(f"当前表说明: {tdesc}")
    lines.extend(["", "表结构（字段序号 · 字段名 · 类型 · 原表 COMMENT）:"])
    for i, c in enumerate(columns, 1):
        name = c.get("column_name", "")
        dtype = c.get("data_type", "") or "unknown"
        hive = (c.get("hive_comment") or "").strip()
        desc = (c.get("description") or "").strip()
        tag = " ← 待生成" if mark_targets and name in mark_targets else ""
        line = f"  {i}. {name} ({dtype}){tag}"
        if hive:
            line += f"  原表 COMMENT: {hive}"
        elif not mark_targets or name in (mark_targets or set()):
            line += "  原表 COMMENT: （无）"
        if desc and desc != hive:
            line += f"  当前说明: {desc}"
        lines.append(line)
    return "\n".join(lines)


def _generate_table_comments_once(
    db_name: str,
    table_name: str,
    columns: list[dict],
    *,
    schema_columns: list[dict] | None = None,
    table_hive_comment: str | None = None,
    table_description: str | None = None,
    need_table_desc: bool,
    timeout: int,
) -> dict:
    target_names = {c["column_name"] for c in columns}
    schema = schema_columns or columns
    schema_block = _format_schema_block(
        db_name,
        table_name,
        schema,
        table_hive_comment=table_hive_comment,
        table_description=table_description,
        mark_targets=target_names,
    )
    has_source = bool((table_hive_comment or "").strip()) or any(
        (c.get("hive_comment") or "").strip() for c in schema
    )
    table_hint = (
        "若表也缺说明则填 table_description（格式：{中文表名}表，用于…；包含…。）；否则 table_description 用空字符串。"
        if need_table_desc
        else "table_description 必须用空字符串。"
    )
    source_rule = (
        "\n待生成字段若存在原表 COMMENT，输出必须以其语义为基础扩写，不可丢弃或改写原意。"
        if has_source
        else ""
    )
    prompt = (
        "你是数据仓库元数据专家。请根据表结构、表名、字段名、字段类型、"
        "**原表 COMMENT（业务库自带注释）**及同表字段关系进行推断，"
        "为待生成字段（标记为 ← 待生成）编写中文元数据说明。\n\n"
        f"{schema_block}\n"
        f"{_SOURCE_COMMENT_RULES}\n"
        f"{_COLUMN_OUTPUT_FORMAT_RULES}\n"
        "输出 JSON，格式:\n"
        '{"table_description":"{中文表名}表，用于…；包含…。 或空字符串",'
        '"columns":{"字段名":"中文名：中文解释"}}\n'
        "columns 的键必须与待生成字段的字段名完全一致（区分大小写）。\n"
        f"{table_hint}{source_rule}\n"
        f"待生成字段: {', '.join(sorted(target_names)) or '无'}"
    )
    data = _call_llm_json_ex(prompt, timeout=timeout, metadata=True)
    out_cols = data.get("columns") or {}
    if not isinstance(out_cols, dict):
        out_cols = {}
    merged: dict[str, str] = {}
    for c in columns:
        name = c["column_name"]
        raw = out_cols.get(name)
        if raw is None:
            lower = name.lower()
            for key, val in out_cols.items():
                if str(key).lower() == lower and val:
                    raw = val
                    break
        if raw:
            hive = (c.get("hive_comment") or "").strip()
            merged[name] = _ensure_preserves_base(hive or _comment_base(c), str(raw).strip())
    table_raw = (data.get("table_description") or "").strip()
    return {
        "table_description": table_raw,
        "columns": merged,
    }


def generate_table_description_only(
    db_name: str,
    table_name: str,
    columns: list[dict],
    timeout: int | None = None,
    *,
    existing_desc: str | None = None,
    hive_comment: str | None = None,
) -> str:
    """仅生成表说明；字段列表作完整表结构上下文。"""
    if timeout is None:
        timeout = int(os.getenv("LOCAL_LLM_TIMEOUT", "300"))
    base = ((existing_desc or hive_comment or "")).strip()
    hc = (hive_comment or "").strip()
    schema_block = _format_schema_block(
        db_name,
        table_name,
        columns,
        table_hive_comment=hc,
        table_description=(existing_desc or "").strip() or None,
    )
    prompt = (
        "你是数据仓库元数据专家。请根据库名、表名、完整字段结构及**原表 COMMENT**"
        "推断该表的业务用途，生成表级中文说明。\n\n"
        f"{schema_block}\n"
        f"{_SOURCE_COMMENT_RULES}\n"
        f"{_TABLE_OUTPUT_FORMAT_RULES}\n"
        f"{_preserve_instruction(base, hive_comment=hc)}"
        "\n只输出 JSON，格式:\n"
        '{"table_description":"{中文表名}表，用于…；包含…。","columns":{}}\n'
        "columns 必须为空对象 {}，不要生成字段说明。"
    )
    data = _call_llm_json_ex(prompt, timeout=timeout, metadata=True)
    desc = (data.get("table_description") or "").strip()
    if not desc:
        raise RuntimeError("AI 未返回表说明，请重试或手动填写")
    return _ensure_preserves_base(hc or base, desc)


def generate_table_comments(
    db_name: str,
    table_name: str,
    columns: list[dict],
    *,
    schema_columns: list[dict] | None = None,
    need_table_desc: bool = False,
    timeout: int | None = None,
    batch_size: int = 25,
    existing_table_desc: str | None = None,
    table_hive_comment: str | None = None,
) -> dict:
    """
    为缺注释的表/字段生成说明。
    返回 {table_description, columns: {column_name: description}}。
    columns: 待生成的字段；schema_columns: 完整表结构（供 LLM 参考，默认与 columns 相同）。
    """
    if timeout is None:
        timeout = int(os.getenv("LOCAL_LLM_TIMEOUT", "300"))

    context_cols = schema_columns or columns
    targets = list(columns)

    if need_table_desc:
        table_description = generate_table_description_only(
            db_name,
            table_name,
            context_cols,
            timeout=timeout,
            existing_desc=existing_table_desc,
            hive_comment=table_hive_comment,
        )
        if not targets:
            return {"table_description": table_description, "columns": {}}
    else:
        table_description = ""

    if not targets and not need_table_desc:
        return {"table_description": "", "columns": {}}

    merged_cols: dict[str, str] = {}
    chunks: list[list[dict]] = []
    for i in range(0, len(targets), batch_size):
        chunks.append(targets[i : i + batch_size])

    for chunk in chunks:
        part = _generate_table_comments_once(
            db_name,
            table_name,
            chunk,
            schema_columns=context_cols,
            table_hive_comment=table_hive_comment,
            table_description=existing_table_desc,
            need_table_desc=False,
            timeout=timeout,
        )
        merged_cols.update(part.get("columns") or {})

    return {"table_description": table_description, "columns": merged_cols}


def llm_available() -> bool:
    _ensure_llm_env()
    return llm_is_configured()
