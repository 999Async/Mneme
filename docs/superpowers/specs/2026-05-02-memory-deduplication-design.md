# 记忆去重机制设计

**日期**: 2026-05-02
**状态**: 已批准
**作者**: Claude Code

## 问题背景

当前 Mneme 系统将"冲突"和"重复"混为一谈，导致：
- 内容完全相同的记忆被存储为多个版本
- 语义高度相似的记忆（如"研究飞书多维表格" vs "搞明白飞书多维表格"）被当作冲突处理

这造成了数据冗余和用户体验问题。

## 设计目标

1. **精确去重**：内容完全相同的记忆不创建新记录
2. **语义去重**：embedding 相似度 >= 0.95 的记忆不创建新记录
3. **职责分离**：去重检测与冲突检测分离，便于独立调整

## 架构设计

### 处理流程

```
消息 → 去重检测 → 冲突检测 → 创建记忆
         ↓ (重复)    ↓ (冲突)      ↓ (无冲突)
      跳过返回    supersede    独立创建
```

### 阈值划分

| 相似度范围 | 判定类型 | 处理方式 |
|-----------|---------|---------|
| 内容完全相同 | 重复 | 跳过，返回现有记忆 ID |
| embedding >= 0.95 | 重复 | 跳过，返回现有记忆 ID |
| 0.7 <= embedding < 0.95 | 冲突 | 创建新记忆，supersede 旧记忆 |
| embedding < 0.7 | 无关联 | 创建独立记忆 |

## 实现方案

### 1. conflict_service.py 新增函数

**`detect_duplicates()`** - 去重检测入口

```python
DUPLICATE_THRESHOLD = 0.95

async def detect_duplicates(
    db: AsyncSession,
    *,
    content: str,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> list[dict]:
    """检测重复记忆

    Returns:
        [{"id", "content", "similarity_score", "duplicate_reason"}]
    """
    # 1. 精确匹配
    exact = await _find_exact_matches(db, content, owner_id, scope, exclude_id)
    if exact:
        return exact

    # 2. 语义匹配
    return await _find_semantic_duplicates(db, content, owner_id, scope, exclude_id)
```

**`_find_exact_matches()`** - 精确匹配

```python
async def _find_exact_matches(
    db: AsyncSession, content: str, owner_id: str, scope: str, exclude_id: str | None
) -> list[dict]:
    """查找内容完全相同的记忆"""
    stmt = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.scope == scope,
        Memory.active == True,
        Memory.content == content,
    )
    if exclude_id:
        stmt = stmt.where(Memory.id != exclude_id)

    result = await db.execute(stmt.limit(1))
    memory = result.scalar_one_or_none()

    if memory:
        return [{
            "id": memory.id,
            "content": memory.content,
            "similarity_score": 1.0,
            "duplicate_reason": "内容完全相同",
        }]
    return []
```

**`_find_semantic_duplicates()`** - 语义匹配

```python
async def _find_semantic_duplicates(
    db: AsyncSession, content: str, owner_id: str, scope: str, exclude_id: str | None
) -> list[dict]:
    """查找语义高度相似的记忆（embedding >= 0.95）"""
    from app.llm.embedding import encode as encode_embedding

    query_vec = await encode_embedding(content)
    if not query_vec:
        return []

    stmt = (
        select(Memory, (1 - Memory.embedding.cosine_distance(query_vec)).label("similarity"))
        .where(
            Memory.owner_id == owner_id,
            Memory.scope == scope,
            Memory.active == True,
            Memory.embedding != None,
        )
        .order_by(Memory.embedding.cosine_distance(query_vec))
        .limit(1)
    )
    if exclude_id:
        stmt = stmt.where(Memory.id != exclude_id)

    result = await db.execute(stmt)
    row = result.first()

    if row:
        memory, sim = row
        if sim >= DUPLICATE_THRESHOLD:
            return [{
                "id": memory.id,
                "content": memory.content,
                "similarity_score": round(sim, 3),
                "duplicate_reason": f"语义相似度 {sim:.2f}",
            }]
    return []
```

### 2. 调用方修改

**app/api/memories.py** - 显式创建 API

```python
@router.post("")
async def create_memory_api(body: CreateMemoryReq, db: AsyncSession = Depends(get_db)):
    # 去重检测（优先于冲突检测）
    from app.services.conflict_service import detect_duplicates
    dupes = await detect_duplicates(
        db, content=body.content, owner_id=body.owner_id, scope=body.scope,
    )
    if dupes:
        return ok({
            "id": dupes[0]["id"],
            "duplicate": True,
            "duplicate_reason": dupes[0]["duplicate_reason"],
            "message": "记忆已存在，未创建新记录",
        })

    # 原有冲突检测 + 创建逻辑...
```

**app/services/message_buffer_service.py** - 批量提取

```python
# 在 extract_from_buffer() 中，创建记忆前：
dupes = await detect_duplicates(
    db, content=safe_content, owner_id=owner_id, scope="group",
)
if dupes:
    continue  # 跳过重复记忆，不创建
```

## 错误处理

- embedding 生成失败时，仅依赖精确匹配去重
- 数据库查询失败时，记录日志并降级到无去重模式

## 测试计划

1. **单元测试**：
   - 精确匹配：内容完全相同
   - 语义匹配：相似度刚好在 0.95 边界
   - 无重复：正常创建记忆

2. **集成测试**：
   - API 创建记忆的去重响应
   - 批量提取时的去重逻辑

## 影响范围

- 修改文件：`app/services/conflict_service.py`
- 修改文件：`app/api/memories.py`
- 修改文件：`app/services/message_buffer_service.py`

## 后续优化

- 考虑添加"合并"选项（用户可选择是否合并重复记忆的标签）
- 考虑为不同记忆类型设置不同的去重阈值
