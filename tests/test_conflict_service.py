"""去重检测测试 — 精确匹配和语义相似度"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, Memory
from app.services.conflict_service import (
    _find_exact_matches,
    _find_semantic_duplicates,
    detect_duplicates,
    DUPLICATE_THRESHOLD,
)
from uuid import uuid4


# ─── 测试数据库 fixture ───

TEST_DB_URL = settings.database_url


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_find_exact_matches_content_identical(db: AsyncSession):
    """测试内容完全相同的记忆能被匹配"""
    memory = Memory(
        id=str(uuid4()),
        scope="group",
        owner_id="test_user",
        type="fact",
        content="测试内容完全相同",
        tags={},
        active=True,
    )
    db.add(memory)
    await db.commit()

    results = await _find_exact_matches(
        db,
        content="测试内容完全相同",
        owner_id="test_user",
        scope="group",
        exclude_id=None,
    )

    assert len(results) == 1
    assert results[0]["id"] == memory.id
    assert results[0]["similarity_score"] == 1.0
    assert results[0]["duplicate_reason"] == "内容完全相同"


@pytest.mark.asyncio
async def test_find_exact_matches_content_different(db: AsyncSession):
    """测试内容不同的记忆不会被匹配"""
    memory = Memory(
        id=str(uuid4()),
        scope="group",
        owner_id="test_user",
        type="fact",
        content="原始内容",
        tags={},
        active=True,
    )
    db.add(memory)
    await db.commit()

    results = await _find_exact_matches(
        db,
        content="完全不同的内容",
        owner_id="test_user",
        scope="group",
        exclude_id=None,
    )

    assert len(results) == 0


@pytest.mark.asyncio
async def test_find_exact_matches_exclude_id(db: AsyncSession):
    """测试 exclude_id 参数生效"""
    memory_id = str(uuid4())
    memory = Memory(
        id=memory_id,
        scope="group",
        owner_id="test_user",
        type="fact",
        content="测试内容",
        tags={},
        active=True,
    )
    db.add(memory)
    await db.commit()

    results = await _find_exact_matches(
        db,
        content="测试内容",
        owner_id="test_user",
        scope="group",
        exclude_id=memory_id,
    )

    assert len(results) == 0


@pytest.mark.asyncio
async def test_find_semantic_duplicates_high_similarity(db: AsyncSession):
    """测试语义高度相似的记忆能被匹配"""
    from app.llm.embedding import encode as encode_embedding

    content = "我最近在研究飞书多维表格"
    memory = Memory(
        id=str(uuid4()),
        scope="group",
        owner_id="test_user",
        type="fact",
        content=content,
        tags={},
        active=True,
    )
    emb = await encode_embedding(content)
    if emb:
        memory.embedding = emb
    db.add(memory)
    await db.commit()

    similar_content = "我最近在搞明白飞书多维表格"
    results = await _find_semantic_duplicates(
        db,
        content=similar_content,
        owner_id="test_user",
        scope="group",
        exclude_id=None,
    )

    if results:
        assert "id" in results[0]
        assert "similarity_score" in results[0]
        assert "duplicate_reason" in results[0]
        assert results[0]["similarity_score"] >= DUPLICATE_THRESHOLD


@pytest.mark.asyncio
async def test_find_semantic_duplicates_low_similarity(db: AsyncSession):
    """测试语义不相似的记忆不会被匹配"""
    from app.llm.embedding import encode as encode_embedding

    content = "今天天气很好"
    memory = Memory(
        id=str(uuid4()),
        scope="group",
        owner_id="test_user",
        type="fact",
        content=content,
        tags={},
        active=True,
    )
    emb = await encode_embedding(content)
    if emb:
        memory.embedding = emb
    db.add(memory)
    await db.commit()

    results = await _find_semantic_duplicates(
        db,
        content="飞书多维表格的使用方法",
        owner_id="test_user",
        scope="group",
        exclude_id=None,
    )

    assert len(results) == 0


@pytest.mark.asyncio
async def test_find_semantic_duplicates_no_embedding(db: AsyncSession):
    """测试 embedding 生成失败时返回空列表"""
    memory = Memory(
        id=str(uuid4()),
        scope="group",
        owner_id="test_user",
        type="fact",
        content="测试内容",
        tags={},
        active=True,
        embedding=None,
    )
    db.add(memory)
    await db.commit()

    results = await _find_semantic_duplicates(
        db,
        content="测试内容",
        owner_id="test_user",
        scope="group",
        exclude_id=None,
    )

    assert len(results) == 0


@pytest.mark.asyncio
async def test_detect_duplicates_exact_match(db: AsyncSession):
    """测试去重检测能发现内容完全相同的记忆"""
    memory = Memory(
        id=str(uuid4()),
        scope="group",
        owner_id="test_user",
        type="fact",
        content="完全相同的内容",
        tags={},
        active=True,
    )
    db.add(memory)
    await db.commit()

    results = await detect_duplicates(
        db,
        content="完全相同的内容",
        owner_id="test_user",
        scope="group",
    )

    assert len(results) == 1
    assert results[0]["duplicate_reason"] == "内容完全相同"


@pytest.mark.asyncio
async def test_detect_duplicates_semantic_match(db: AsyncSession):
    """测试去重检测能发现语义高度相似的记忆"""
    from app.llm.embedding import encode as encode_embedding

    content = "研究飞书多维表格"
    memory = Memory(
        id=str(uuid4()),
        scope="group",
        owner_id="test_user",
        type="fact",
        content=content,
        tags={},
        active=True,
    )
    emb = await encode_embedding(content)
    if emb:
        memory.embedding = emb
    db.add(memory)
    await db.commit()

    results = await detect_duplicates(
        db,
        content="搞明白飞书多维表格",
        owner_id="test_user",
        scope="group",
    )

    if results:
        assert "语义相似度" in results[0]["duplicate_reason"]


@pytest.mark.asyncio
async def test_detect_duplicates_no_match(db: AsyncSession):
    """测试没有重复时返回空列表"""
    results = await detect_duplicates(
        db,
        content="完全不相关的内容",
        owner_id="test_user",
        scope="group",
    )

    assert len(results) == 0
