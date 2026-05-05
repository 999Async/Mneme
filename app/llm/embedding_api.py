"""Embedding API 实现 — OpenAI 兼容接口"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]
REQUEST_TIMEOUT = 30.0


async def _encode_batch_via_api(
    texts: list[str],
    api_key: str,
    base_url: str,
    model: str,
    dimension: int,
) -> list[list[float]] | None:
    """通过 API 批量生成 embedding 向量

    Args:
        texts: 待编码的文本列表
        api_key: API key
        base_url: API base URL
        model: 模型名称
        dimension: 向量维度

    Returns:
        成功: list[list[float]]
        失败: None
    """
    if not texts:
        return []

    url = f"{base_url.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "input": texts,
        "dimensions": dimension,
    }

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()

            # 按 index 排序确保顺序正确
            embeddings_data = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in embeddings_data]

        except httpx.TimeoutException as e:
            last_error = f"timeout: {e}"
            logger.warning("Embedding timeout (attempt %d/%d)", attempt + 1, MAX_RETRIES)
        except httpx.HTTPStatusError as e:
            last_error = f"http_{e.response.status_code}: {e.response.text[:200]}"
            logger.warning("Embedding HTTP error %d (attempt %d/%d)", e.response.status_code, attempt + 1, MAX_RETRIES)
            if e.response.status_code in (401, 403, 404):
                break
        except Exception as e:
            last_error = f"unexpected: {e}"
            logger.warning("Embedding error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, e)

        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAYS[attempt])

    logger.error("Embedding unavailable after %d retries: %s", MAX_RETRIES, last_error)
    return None
