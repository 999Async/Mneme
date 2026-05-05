"""Rerank 模块 — 支持 LLM / API / 本地模型"""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Awaitable

from app.config import settings
from app.llm.client import chat
from app.models import Memory

logger = logging.getLogger(__name__)

# 全局 rerank 后端实例
_backend: object | None = None


# ─── 抽象基类 ───


class RerankBackend(ABC):
    """Rerank 后端抽象基类"""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        candidates: list[tuple[float, Memory]],
    ) -> list[tuple[float, Memory]]:
        """对候选结果进行重排序

        Args:
            query: 用户查询
            candidates: [(similarity, Memory), ...] 候选结果

        Returns:
            [(rerank_score, Memory), ...] 重排序后的结果
        """


# ─── LLM Rerank ───


class LLMRerankBackend(RerankBackend):
    """使用 LLM 进行 rerank"""

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[float, Memory]],
    ) -> list[tuple[float, Memory]]:
        """使用 LLM 对向量召回结果进行 rerank"""
        if not candidates:
            return []

        # 构建候选记忆列表
        memories_text = ""
        for idx, (_, m) in enumerate(candidates, 1):
            memories_text += f"{idx}. {m.content}\n"

        prompt = f"""你是一个搜索结果排序专家。请根据用户查询，对以下记忆内容按相关性进行排序。

用户查询：{query}

候选记忆：
{memories_text}

请返回一个 JSON 数组，包含最相关的前 10 条记忆的编号（按相关性从高到低排序）。
格式：{{"rankings": [1, 3, 2, ...]}}

只返回最相关的记忆，不要包含无关或低相关性的记忆。"""

        response = await chat([
            {"role": "user", "content": prompt}
        ], temperature=0.1, max_tokens=200)

        if not response.get("ok"):
            logger.warning("LLM rerank 失败: %s", response.get("error"))
            return candidates

        return self._parse_llm_rerank_response(response, candidates, query)

    def _parse_llm_rerank_response(
        self,
        response: dict,
        candidates: list[tuple[float, Memory]],
        query: str,
    ) -> list[tuple[float, Memory]]:
        """解析 LLM rerank 响应"""
        try:
            content = response.get("content", "").strip()

            # 尝试提取 JSON（处理可能的前后文本）
            content = re.sub(r'```(?:json)?\n?', '', content)
            content = re.sub(r'```', '', content)

            # 查找 {...} 模式
            json_match = re.search(r'\{[^{}]*"rankings"\s*:\s*\[[^\]]*\][^{}]*\}', content)
            if json_match:
                content = json_match.group(0)
            else:
                # 尝试直接解析数组
                array_match = re.search(r'\[[\d,\s]+\]', content)
                if array_match:
                    content = f'{{"rankings": {array_match.group(0)}}}'

            logger.info("LLM rerank 原始返回: %s", response.get("content", "")[:100])

            # 解析 JSON 结果
            match = json.loads(content)
            rankings = match.get("rankings", [])

            if not rankings:
                logger.warning("LLM rerank 返回空结果，使用向量排序")
                return candidates

            logger.info("LLM rerank 排序: %s", rankings)

            # 重新排序
            ranked = []
            candidates_dict = {i: (sim, m) for i, (sim, m) in enumerate(candidates)}
            for rank in rankings:
                idx = rank - 1  # 转换为 0-based 索引
                if idx in candidates_dict:
                    # rerank 分数：向量相似度 * (1 - 排名衰减)
                    # 第1名: 1.0, 第2名: 0.95, 第3名: 0.9, ...
                    sim, m = candidates_dict[idx]
                    rerank_score = sim * (1.0 - (rank - 1) * 0.05)
                    ranked.append((rerank_score, m))
                    logger.info("LLM Rerank: rank=%d, score=%.3f, content=%s",
                                rank, rerank_score, m.content[:50])

            return ranked if ranked else candidates

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("LLM rerank 解析失败: %s, 使用向量排序", e)
            logger.info("LLM 原始返回: %s", response.get("content", "")[:200])
            return candidates


# ─── API Rerank ───


class APIRerankBackend(RerankBackend):
    """使用 API 进行 rerank（如 Cohere Rerank API）"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[float, Memory]],
    ) -> list[tuple[float, Memory]]:
        """使用 API 进行 rerank"""
        if not candidates:
            return []

        try:
            import httpx

            # 准备请求
            docs = [m.content for _, m in candidates]
            url = f"{self.base_url.rstrip('/')}/rerank"

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "query": query,
                        "documents": docs,
                        "top_n": len(candidates),
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            # 解析结果
            results = data.get("results", [])
            ranked = []
            for item in results:
                idx = item.get("index")
                relevance_score = item.get("relevance_score", 0)
                if idx is not None and idx < len(candidates):
                    sim, m = candidates[idx]
                    # 结合原始相似度和 rerank 分数
                    rerank_score = sim * relevance_score
                    ranked.append((rerank_score, m))
                    logger.info("API Rerank: idx=%d, score=%.3f, content=%s",
                                idx, rerank_score, m.content[:50])

            return ranked if ranked else candidates

        except Exception as e:
            logger.warning("API rerank 失败: %s, 使用向量排序", e)
            return candidates


# ─── 本地模型 Rerank ───


class LocalRerankBackend(RerankBackend):
    """使用本地 rerank 模型"""

    _model: object | None = None

    def __init__(self, model_path: str, device: str = "cpu"):
        self.model_path = model_path
        self.device = device
        self._load_model()

    def _load_model(self) -> None:
        """加载本地模型（仅初始化时执行一次）"""
        if LocalRerankBackend._model is not None:
            return

        try:
            from sentence_transformers import CrossEncoder

            LocalRerankBackend._model = CrossEncoder(
                self.model_path,
                device=self.device,
            )
            logger.info("Local rerank model loaded: %s", self.model_path)
        except Exception as e:
            logger.error("Failed to load local rerank model: %s", e)
            LocalRerankBackend._model = None

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[float, Memory]],
    ) -> list[tuple[float, Memory]]:
        """使用本地模型进行 rerank"""
        if LocalRerankBackend._model is None or not candidates:
            return candidates

        try:
            import asyncio

            # 准备 query-doc 对
            pairs = [[query, m.content] for _, m in candidates]

            loop = asyncio.get_event_loop()
            scores = await loop.run_in_executor(
                None,
                lambda: LocalRerankBackend._model.predict(pairs),
            )

            # 结合原始相似度和 rerank 分数
            ranked = []
            for (sim, m), rerank_score in zip(candidates, scores):
                combined_score = sim * float(rerank_score)
                ranked.append((combined_score, m))
                logger.info("Local Rerank: score=%.3f, content=%s",
                            combined_score, m.content[:50])

            # 按分数排序
            return sorted(ranked, key=lambda x: x[0], reverse=True)

        except Exception as e:
            logger.warning("Local rerank 失败: %s, 使用向量排序", e)
            return candidates


# ─── 工厂函数 ───


def create_rerank_backend(
    rerank_type: str,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    model_path: str = "",
    device: str = "cpu",
) -> RerankBackend:
    """创建 rerank 后端实例

    Args:
        rerank_type: "llm", "api", or "local"
        api_key: API key (for api type)
        base_url: API base URL (for api type)
        model: API model name (for api type)
        model_path: Local model path (for local type)
        device: Device to use (for local type)

    Returns:
        RerankBackend instance
    """
    if rerank_type == "local":
        if not model_path:
            raise ValueError("model_path is required for local rerank")
        return LocalRerankBackend(model_path, device)
    elif rerank_type == "api":
        if not api_key or not base_url:
            raise ValueError("api_key and base_url are required for API rerank")
        return APIRerankBackend(api_key, base_url, model)
    else:  # llm
        return LLMRerankBackend()


def get_backend() -> RerankBackend | None:
    """获取全局 rerank 后端实例（单例模式）"""
    global _backend

    if _backend is None:
        try:
            _backend = create_rerank_backend(
                rerank_type=settings.rerank_type,
                api_key=settings.rerank_api_key,
                base_url=settings.rerank_base_url,
                model=settings.rerank_model,
                model_path=settings.rerank_model_path,
                device=settings.rerank_device,
            )
            logger.info("Rerank backend initialized: type=%s", settings.rerank_type)
        except Exception as e:
            logger.warning("Failed to initialize rerank backend: %s", e)
            _backend = None

    return _backend


async def rerank(
    query: str,
    candidates: list[tuple[float, Memory]],
) -> list[tuple[float, Memory]]:
    """对候选结果进行重排序

    Args:
        query: 用户查询
        candidates: [(similarity, Memory), ...] 候选结果

    Returns:
        [(rerank_score, Memory), ...] 重排序后的结果
    """
    backend = get_backend()
    if backend is None:
        return candidates

    return await backend.rerank(query, candidates)
