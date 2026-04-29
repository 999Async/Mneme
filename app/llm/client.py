"""OpenAI 兼容 LLM 客户端 — 支持重试 + 降级"""

import asyncio
import json
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # 指数退避（秒）
REQUEST_TIMEOUT = 30.0


async def chat(
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 1000,
    response_format: dict | None = None,
    tools: list[dict] | None = None,
    tool_choice: dict | str | None = None,
) -> dict:
    """调用 LLM Chat Completion API

    Returns:
        成功: {"ok": True, "content": str, "tool_calls": list|None, "usage": dict}
        失败: {"ok": False, "error": str}
    """
    if not settings.llm_api_key:
        return {"ok": False, "error": "llm_not_configured"}

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    body: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        body["response_format"] = response_format
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()

            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls")
            usage = data.get("usage", {})
            return {"ok": True, "content": content, "tool_calls": tool_calls, "usage": usage}

        except httpx.TimeoutException as e:
            last_error = f"timeout: {e}"
            logger.warning("LLM timeout (attempt %d/%d)", attempt + 1, MAX_RETRIES)
        except httpx.HTTPStatusError as e:
            last_error = f"http_{e.response.status_code}: {e.response.text[:200]}"
            logger.warning("LLM HTTP error %d (attempt %d/%d)", e.response.status_code, attempt + 1, MAX_RETRIES)
            if e.response.status_code in (401, 403, 404):
                break  # 不可重试的错误
        except Exception as e:
            last_error = f"unexpected: {e}"
            logger.warning("LLM error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAYS[attempt])

    logger.error("LLM unavailable after %d retries: %s", MAX_RETRIES, last_error)
    return {"ok": False, "error": "llm_unavailable", "detail": last_error}


def _strip_markdown_json(text: str) -> str:
    """剥掉 LLM 可能返回的 ```json ... ``` 包裹"""
    text = text.strip()
    # 完整包裹: ```json\n{...}\n``` 或 ```\n{...}\n```
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 只有开头: ```json\n{...} 或 ```\n{...}
    m = re.match(r"^```(?:json)?\s*\n?(.*)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


async def chat_json(
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    schema: dict | None = None,
) -> dict:
    """调用 LLM 并解析 JSON 响应。

    优先使用 Function Calling（通过 schema 参数）强制模型输出合法 JSON；
    若 schema 为 None，回退到 response_format 模式。
    两种模式均有 JSON 解析失败重试。

    Args:
        schema: JSON Schema dict，定义期望的输出结构。
                传入后将通过 Function Calling 约束输出。

    Returns:
        成功: {"ok": True, "data": dict}
        失败: {"ok": False, "error": str}
    """
    for attempt in range(MAX_RETRIES):
        if schema:
            result = await _call_via_function_calling(messages, schema, temperature, max_tokens)
        else:
            result = await _call_via_response_format(messages, temperature, max_tokens)

        if result["ok"]:
            return result

        # HTTP 级别失败不重试（已由 chat() 内部处理）
        if result.get("error") in ("llm_not_configured", "llm_unavailable"):
            return result

        # JSON 解析失败，重试
        logger.warning("chat_json attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, result.get("error"))
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAYS[attempt])

    return result


async def _call_via_function_calling(
    messages: list[dict],
    schema: dict,
    temperature: float,
    max_tokens: int,
) -> dict:
    """通过 Function Calling 获取结构化 JSON 输出"""
    tools = [{
        "type": "function",
        "function": {
            "name": "structured_output",
            "description": "输出结构化数据",
            "parameters": schema,
        },
    }]
    tool_choice = {"type": "function", "function": {"name": "structured_output"}}

    result = await chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tools,
        tool_choice=tool_choice,
    )
    if not result["ok"]:
        return result

    tool_calls = result.get("tool_calls")
    if not tool_calls:
        # 模型没有返回 tool_calls，尝试从 content 解析（降级）
        logger.warning("LLM returned no tool_calls, falling back to content parsing")
        return await _parse_content_json(result["content"])

    # 从 tool_calls 提取 arguments
    try:
        args_str = tool_calls[0]["function"]["arguments"]
        data = json.loads(args_str)
        return {"ok": True, "data": data}
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("LLM tool_calls parse error: %s\nArguments: %s", e, args_str[:200] if 'args_str' in dir() else "N/A")
        return {"ok": False, "error": "tool_calls_parse_error", "detail": str(e)}


async def _call_via_response_format(
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> dict:
    """通过 response_format 获取 JSON 输出（降级路径）"""
    result = await chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    if not result["ok"]:
        return result

    return await _parse_content_json(result["content"])


async def _parse_content_json(content: str) -> dict:
    """解析 content 文本中的 JSON"""
    if not content:
        return {"ok": False, "error": "empty_content"}

    cleaned = _strip_markdown_json(content)
    try:
        data = json.loads(cleaned)
        return {"ok": True, "data": data}
    except json.JSONDecodeError as e:
        logger.warning("LLM JSON parse error: %s\nContent: %s", e, cleaned[:200])
        return {"ok": False, "error": "json_parse_error", "detail": str(e)}
