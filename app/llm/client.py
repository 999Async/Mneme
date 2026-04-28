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
) -> dict:
    """调用 LLM Chat Completion API

    Returns:
        成功: {"ok": True, "content": str, "usage": dict}
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

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()

            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return {"ok": True, "content": content, "usage": usage}

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
) -> dict:
    """调用 LLM 并解析 JSON 响应，JSON 解析失败时重试

    Returns:
        成功: {"ok": True, "data": dict}
        失败: {"ok": False, "error": str}
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        result = await chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        if not result["ok"]:
            return result

        content = _strip_markdown_json(result["content"])

        try:
            data = json.loads(content)
            return {"ok": True, "data": data}
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(
                "LLM JSON parse error (attempt %d/%d): %s\nContent: %s",
                attempt + 1, MAX_RETRIES, e, content[:200],
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])

    logger.error("LLM JSON parse failed after %d retries: %s", MAX_RETRIES, last_error)
    return {"ok": False, "error": "json_parse_error", "detail": str(last_error)}
