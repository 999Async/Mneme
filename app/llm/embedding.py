"""Embedding 客户端 — 统一入口，支持 API 和本地模型"""

import logging

from app.config import settings
from app.llm.embedding_base import create_embedding_backend

logger = logging.getLogger(__name__)

# 全局 embedding 后端实例
_backend: object | None = None


def get_backend() -> object:
    """获取全局 embedding 后端实例（单例模式）"""
    global _backend

    if _backend is None:
        try:
            _backend = create_embedding_backend(
                embedding_type=settings.embedding_type,
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_base_url,
                model=settings.embedding_model,
                dimension=settings.embedding_dimensions,
                model_path=settings.embedding_model_path,
                device=settings.embedding_device,
            )
            logger.info("Embedding backend initialized: type=%s", settings.embedding_type)
        except Exception as e:
            logger.error("Failed to initialize embedding backend: %s", e)
            _backend = None

    return _backend


async def encode(text: str) -> list[float] | None:
    """生成单条文本的 embedding 向量

    Returns:
        成功: list[float]
        失败: None（降级为纯规则搜索）
    """
    backend = get_backend()
    if backend is None:
        return None

    return await backend.encode(text)


async def encode_batch(texts: list[str]) -> list[list[float]] | None:
    """批量生成 embedding 向量

    Returns:
        成功: list[list[float]]
        失败: None
    """
    backend = get_backend()
    if backend is None:
        return None

    if not texts:
        return []

    return await backend.encode_batch(texts)


def get_dimension() -> int:
    """获取 embedding 向量维度"""
    backend = get_backend()
    if backend is None:
        return 0
    return backend.get_dimension()
