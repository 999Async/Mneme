"""Embedding 抽象基类"""

from abc import ABC, abstractmethod
from typing import Awaitable, Callable


class EmbeddingBackend(ABC):
    """Embedding 后端抽象基类"""

    @abstractmethod
    async def encode(self, text: str) -> list[float] | None:
        """生成单条文本的 embedding 向量

        Returns:
            成功: list[float]
            失败: None
        """

    @abstractmethod
    async def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        """批量生成 embedding 向量

        Returns:
            成功: list[list[float]]
            失败: None
        """

    @abstractmethod
    def get_dimension(self) -> int:
        """获取 embedding 向量维度"""


class APIEmbeddingBackend(EmbeddingBackend):
    """API 方式调用 embedding 服务"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.dimension = dimension

    async def encode(self, text: str) -> list[float] | None:
        results = await self.encode_batch([text])
        return results[0] if results else None

    async def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        from app.llm.embedding_api import _encode_batch_via_api

        return await _encode_batch_via_api(
            texts,
            self.api_key,
            self.base_url,
            self.model,
            self.dimension,
        )

    def get_dimension(self) -> int:
        return self.dimension


class LocalEmbeddingBackend(EmbeddingBackend):
    """本地加载 embedding 模型"""

    _model: object | None = None
    _dimension: int | None = None

    def __init__(self, model_path: str, device: str = "cpu"):
        self.model_path = model_path
        self.device = device
        self._load_model()

    def _load_model(self) -> None:
        """加载本地模型（仅初始化时执行一次）"""
        if LocalEmbeddingBackend._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer

            LocalEmbeddingBackend._model = SentenceTransformer(
                self.model_path,
                device=self.device,
            )
            LocalEmbeddingBackend._dimension = LocalEmbeddingBackend._model.get_sentence_embedding_dimension()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Failed to load local embedding model: %s", e)
            LocalEmbeddingBackend._model = None

    async def encode(self, text: str) -> list[float] | None:
        results = await self.encode_batch([text])
        return results[0] if results else None

    async def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        if LocalEmbeddingBackend._model is None:
            return None

        try:
            import asyncio

            loop = asyncio.get_event_loop()
            embeddings = await loop.run_in_executor(
                None,
                lambda: LocalEmbeddingBackend._model.encode(
                    texts,
                    convert_to_numpy=False,
                ),
            )
            return embeddings
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Local embedding encode failed: %s", e)
            return None

    def get_dimension(self) -> int:
        return LocalEmbeddingBackend._dimension or 0


def create_embedding_backend(
    embedding_type: str,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    dimension: int = 1536,
    model_path: str = "",
    device: str = "cpu",
) -> EmbeddingBackend:
    """创建 embedding 后端实例

    Args:
        embedding_type: "api" or "local"
        api_key: API key (for api type)
        base_url: API base URL (for api type)
        model: API model name (for api type)
        dimension: Embedding dimension (for api type)
        model_path: Local model path (for local type)
        device: Device to use (for local type)

    Returns:
        EmbeddingBackend instance
    """
    if embedding_type == "local":
        if not model_path:
            raise ValueError("model_path is required for local embedding")
        return LocalEmbeddingBackend(model_path, device)
    else:
        if not api_key:
            raise ValueError("api_key is required for API embedding")
        return APIEmbeddingBackend(api_key, base_url, model, dimension)
