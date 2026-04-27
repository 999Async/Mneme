# Mneme 全时域记忆引擎 - 项目实现文档

## 项目概述

Mneme 是一个企业级飞书记忆引擎，通过异步抽取、遗忘曲线主动复习和时序敏感覆写，帮助团队保存关键信息。

### 技术栈

| 组件 | 技术选型 | 说明 |
|------|---------|------|
| 后端框架 | FastAPI 0.115+ | 异步 Web 框架 |
| 数据库 | PostgreSQL (生产) / SQLite (本地) | 两者兼容设计 |
| ORM | SQLAlchemy 2.0 (async) | 数据库抽象层 |
| 数据迁移 | Alembic | 数据库版本管理 |
| 飞书集成 | HTTP API 直接调用 | Python 侧调用飞书 API |
| 插件架构 | OpenClaw Hook | 消息事件转发 |

### 项目结构

```
app/
├── api/                    # API 路由层
│   ├── events.py           # 消息事件处理入口（/api/events/message）
│   ├── memories.py         # 记忆 CRUD API
│   ├── memory_logs.py      # 记忆日志 API
│   ├── context_snapshots.py # 上下文快照 API
│   ├── conflicts.py        # 冲突检测 API
│   ├── push_logs.py        # 推送记录 API
│   └── cron.py             # 定时任务入口（/api/cron/decay）
│
├── models/                 # 数据库模型
│   ├── memory.py           # Memory 主表
│   ├── memory_log.py       # MemoryLog 历史表
│   ├── context_snapshot.py # ContextSnapshot 快照表
│   └── push_log.py         # PushLog 推送防抖表
│
├── schemas/                # Pydantic 数据模型
│   ├── memory.py           # 记忆相关 Schema
│   ├── event.py            # 事件 Schema
│   ├── conflict.py         # 冲突检测 Schema
│   └── ...
│
├── services/               # 业务逻辑层
│   ├── memory_service.py   # 记忆 CRUD + 版本链管理
│   ├── extraction_service.py # 记忆抽取服务
│   ├── conflict_service.py # 冲突检测服务（三路评分）
│   ├── search_service.py   # 搜索服务（三路召回）
│   ├── decay_service.py    # 遗忘曲线衰减计算
│   ├── push_service.py     # L1/L2 推送候选筛选
│   ├── intent_service.py   # 意图识别服务
│   ├── context_service.py  # 上下文快照服务
│   ├── desensitize_service.py # 敏感信息脱敏
│   └── feishu_push_service.py # 飞书推送服务
│
├── rules/                  # 业务规则（非 LLM）
│   ├── intent_rules.py     # 意图识别规则
│   ├── extraction_rules.py # 记忆抽取触发词
│   └── entity_rules.py     # 实体提取 + 关键词 + 脱敏
│
├── db/                     # 数据库配置
│   └── session.py          # AsyncSession 工厂
│
├── config.py               # 配置管理（环境变量）
└── main.py                 # FastAPI 应用入口 + lifespan

tests/                      # 测试目录
migrations/                 # Alembic 迁移文件
```

## 核心数据模型

### Memory 表（主表）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String(36) | UUID 主键 |
| `scope` | String(10) | `personal` / `group` |
| `owner_id` | String(100) | 记忆归属人 open_id 或群 chat_id |
| `type` | String(20) | `decision` / `fact` / `intent` / `command` |
| `content` | Text | 记忆内容（已脱敏） |
| `context_snapshot` | JSON | 提取时的上下文摘要 |
| `tags` | JSON | `{"keywords": [], "entities": {}}` |
| `strength` | Float | 当前记忆强度 (0~1)，初始 1.0 |
| `decay_params` | JSON | 衰减参数配置 |
| `version` | Integer | 乐观锁版本号 |
| `parent_id` | String(36) | 上一版本记忆 ID（版本链） |
| `active` | Boolean | 是否当前有效版本 |
| `superseded_by` | String(36) | 覆写此记忆的新版本 ID |
| `last_reviewed_at` | DateTime | 最近一次复习时间 |
| `source_message_id` | String(100) | 原始飞书消息 ID |
| `source_chat_id` | String(100) | 来源群聊 ID |
| `confidence` | Float | 抽取置信度 (0~1) |
| `created_at` | DateTime | 注入时间 |
| `updated_at` | DateTime | 最后修改时间 |

### MemoryLog 表（审计日志）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String(36) | UUID 主键 |
| `memory_id` | String(36) | 关联 Memory.id |
| `action` | String(20) | `create` / `review` / `decay` / `overwrite` / `forget` |
| `detail` | JSON | 操作详情 |
| `created_at` | DateTime | 操作时间 |

### PushLog 表（推送防抖）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String(36) | UUID 主键 |
| `memory_id` | String(36) | 关联 Memory.id |
| `push_type` | String(10) | `L1` / `L2` / `L0` |
| `target_id` | String(100) | 推送目标（群或个人） |
| `created_at` | DateTime | 推送时间 |

### ContextSnapshot 表（心流恢复）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String(36) | UUID 主键 |
| `user_id` | String(100) | 用户 open_id |
| `snapshot` | Text | 上下文快照内容 |
| `created_at` | DateTime | 创建时间 |

## 核心 API 端点

### 消息事件处理

**`POST /api/events/message`** - OpenClaw Plugin 调用入口

处理飞书消息，识别意图并分发处理：

- `STORE`: 显式存储（"记住 X" / "大家记住 X"）
- `QUERY`: 查询记忆（"查询 X"）
- `FLOW_RECOVER`: 心流恢复（"回到刚才"）
- `LIST_MY`: 查看我的记忆
- `AUTO_EXTRACT`: 非提及消息自动抽取（静默）
- `PASS`: 不处理

**`POST /api/events/conversation-end`** - 对话结束后抽取

### 定时任务

**`POST /api/cron/decay`** - 执行遗忘曲线衰减

计算 `strength = e^(-rate × sensitivity × days)` 并更新记忆强度。

### 记忆管理

- `GET /api/memories` - 分页查询记忆
- `GET /api/memories/{id}` - 获取单条记忆
- `GET /api/memories/{id}/history` - 获取版本历史
- `POST /api/memories` - 创建记忆
- `PATCH /api/memories/{id}` - 更新记忆

### 复习操作

- `POST /api/memories/{id}/review` - 复习（重置 strength=1.0）
- `POST /api/memories/{id}/dismiss` - 忽略（active=False）
- `POST /api/memories/{id}/done` - 已处理（strength×1.5）

### 冲突与推送

- `POST /api/conflicts/detect` - 检测冲突
- `POST /api/push-logs/l1` - L1 推送候选筛选
- `GET /api/push-logs` - 查询推送记录

## 核心业务逻辑

### 1. 意图识别 ([`intent_service.py`](app/services/intent_service.py))

基于规则的关键词匹配，无需 LLM：

```python
Intent.STORE:     # "记住", "大家记住", "记一下"
Intent.QUERY:     # "查询", "搜索", "搜一下"
Intent.FLOW_RECOVER:  # "回到刚才", "继续刚才"
Intent.LIST_MY:   # "我的记忆", "查看记忆"
Intent.AUTO_EXTRACT:  # 非 @mention 消息，尝试抽取
```

### 2. 记忆抽取 ([`extraction_service.py`](app/services/extraction_service.py))

**触发词规则** ([`extraction_rules.py`](app/rules/extraction_rules.py))：

| 类别 | 关键词 | 示例 |
|------|--------|------|
| 决策 | "决定是", "改用", "更新为" | "决定用 MongoDB" |
| 截止 | "截止", "deadline", "需要在...前" | "周五前上线" |
| 负责人 | "转给", "交接给", "负责" | "转给 C 同学" |
| 配置 | "密钥", "密码", "token", "API" | "API Key 是 sk-xxx" |

**实体提取** ([`entity_rules.py`](app/rules/entity_rules.py))：

- 人员：`person` ("张总", "李总")
- 项目：`project` ("A项目", "周五发布")
- 配置：`config` ("API密钥", "密码")

**敏感信息脱敏**：密码、密钥替换为 `***`

### 3. 冲突检测 ([`conflict_service.py`](app/services/conflict_service.py))

**三路评分算法**（与搜索共享）：

```python
similarity = entity_overlap × 0.5 + keyword_overlap × 0.3 + substring_match × 0.2
```

| 评分维度 | 权重 | 说明 |
|----------|------|------|
| 实体锚定 | 0.5 | tags["entities"] 重叠 |
| 关键词共现 | 0.3 | tags["keywords"] Jaccard 相似度 |
| 子串匹配 | 0.2 | content 包含关系 |

当 `similarity >= 0.75` 时触发冲突，旧记忆标记 `active=False`。

### 4. 遗忘曲线 ([`decay_service.py`](app/services/decay_service.py))

**衰减公式**：

```
strength(t) = e^(-base_decay_rate × personal_sensitivity × t_days)
```

**参数**：

- `base_decay_rate`: 0.5（默认）
- `personal_sensitivity`: 1.0（Demo 阶段固定）

**复习节点**（参考）：

| 复习节点 | 距注入时间 | strength 阈值 |
|----------|------------|---------------|
| 第 1 次 | ~1 天 | ≤ 0.61 |
| 第 2 次 | ~3 天 | ≤ 0.37 |
| 第 3 次 | ~7 天 | ≤ 0.14 |
| 第 4 次 | ~14 天 | ≤ 0.05 |
| 第 5 次 | ~30 天 | ≤ 0.018 |

### 5. L1/L2 推送 ([`push_service.py`](app/services/push_service.py))

**L1 衰减预警**：

- 筛选 `strength <= 0.3` 的记忆
- 按 strength 升序排列（最弱的先推）
- 每目标每天上限 3 条
- 工作日 09:00-19:00 推送
- 24 小时防抖

**L2 相似上下文**（简化实现）：

- 消息与记忆 content 子串匹配（替代语义相似度）
- 24 小时防抖
- 静默卡片到群聊

### 6. 搜索 ([`search_service.py`](app/services/search_service.py))

**三路召回**（与冲突检测共享评分）：

1. 实体锚定：`tags["entities"]` 匹配
2. 关键词共现：`tags["keywords"]` 交集
3. 子串兜底：`content ILIKE`

---

## 记忆 CRUD 实现逻辑

### 0. 版本链与覆写机制

Mneme 的记忆通过 `parent_id` 和 `superseded_by` 字段实现版本链追踪：

```
Version 1: id=A, parent_id=null, active=false, superseded_by=B
              ↓
Version 2: id=B, parent_id=A, active=true,  superseded_by=null
```

**核心设计原则**：
- 新记忆总是 `active=True`
- 被覆写的旧记忆标记 `active=False`，并设置 `superseded_by`
- 通过 `parent_id` 可以追溯完整的历史版本链
- `version` 字段用于乐观锁，每次覆写递增

### 1. 创建记忆 ([`memory_service.py:create_memory`](app/services/memory_service.py))

**函数签名**：
```python
async def create_memory(
    db: AsyncSession,
    *,
    scope: str,              # "personal" 或 "group"
    owner_id: str,           # 用户 open_id 或群 chat_id
    type: str,               # decision / fact / intent / command
    content: str,            # 记忆内容（已脱敏）
    tags: dict,              # {"keywords": [...], "entities": {...}}
    confidence: float = 1.0, # 抽取置信度
    context_snapshot: str | None = None,  # 上下文快照
    source_message_id: str | None = None, # 原始飞书消息 ID
    source_chat_id: str | None = None,    # 来源群聊 ID
    parent_id: str | None = None,         # 父版本 ID（覆写场景）
) -> Memory
```

**实现步骤**：

1. **生成 UUID**：使用 `uuid4()` 生成主键
2. **创建 Memory 对象**：填充所有字段，默认 `active=True`，`strength=1.0`
3. **写入 MemoryLog**：记录 `action="create"`，包含 `source_message_id` 和 `confidence`
4. **提交事务**：同时写入 Memory 和 MemoryLog
5. **返回对象**：刷新后返回完整的 Memory 对象

**代码示例**：
```python
memory = Memory(
    id=str(uuid4()),
    scope=scope,
    owner_id=owner_id,
    type=type,
    content=content,
    tags=tags,
    confidence=confidence,
    context_snapshot=context_snapshot,
    source_message_id=source_message_id,
    source_chat_id=source_chat_id,
    parent_id=parent_id,
)
db.add(memory)

log = MemoryLog(
    memory_id=memory.id,
    action="create",
    detail={"source_message_id": source_message_id, "confidence": confidence},
)
db.add(log)
await db.commit()
await db.refresh(memory)
return memory
```

### 2. 获取记忆

#### 2.1 单条查询 ([`get_memory`](app/services/memory_service.py))

```python
async def get_memory(db: AsyncSession, memory_id: str) -> Memory | None
```

直接通过主键查询，返回 `Memory` 对象或 `None`。

#### 2.2 分页列表查询 ([`list_memories`](app/services/memory_service.py))

**函数签名**：
```python
async def list_memories(
    db: AsyncSession,
    *,
    owner_id: str,
    scope: str | None = None,     # 过滤 personal/group
    type: str | None = None,      # 过滤记忆类型
    active_only: bool = True,     # 仅查询活跃记忆
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Memory], int]  # (记忆列表, 总数)
```

**实现步骤**：

1. **构建查询**：基于 `owner_id` 基础过滤
2. **添加条件**：可选 `scope`、`type`、`active` 过滤
3. **统计总数**：使用 `func.count()` 获取匹配记录数
4. **分页查询**：按 `created_at DESC` 排序，应用 offset/limit
5. **返回结果**：元组 `(items, total)`

**查询示例**：
```python
# 基础查询
query = select(Memory).where(Memory.owner_id == owner_id)

# 条件过滤
if scope:
    query = query.where(Memory.scope == scope)
if type:
    query = query.where(Memory.type == type)
if active_only:
    query = query.where(Memory.active == True)

# 统计总数
count_q = select(func.count()).select_from(query.subquery())
total = (await db.execute(count_q)).scalar() or 0

# 分页
query = query.order_by(Memory.created_at.desc()) \
            .offset((page - 1) * page_size) \
            .limit(page_size)
result = await db.execute(query)
items = list(result.scalars().all())
```

#### 2.3 版本历史查询 ([`get_history`](app/services/memory_service.py))

**函数签名**：
```python
async def get_history(db: AsyncSession, memory_id: str) -> list[Memory]
```

**实现逻辑**：

1. **获取当前记忆**：从 `memory_id` 开始
2. **沿 `parent_id` 链追溯**：循环查找父版本
3. **防环检测**：使用 `visited` 集合避免无限循环
4. **返回历史列表**：按时间倒序（旧→新）

**追溯示例**：
```python
history = []
current = await db.get(Memory, memory_id)
if not current:
    return history

visited = set()
node = current
while node and node.parent_id and node.parent_id not in visited:
    visited.add(node.parent_id)
    parent = await db.get(Memory, node.parent_id)
    if parent:
        history.append(parent)
        node = parent
    else:
        break

return history  # [Version N-1, Version N-2, ...]
```

### 3. 覆写记忆 ([`supersede_memory`](app/services/memory_service.py))

**函数签名**：
```python
async def supersede_memory(
    db: AsyncSession,
    old_memory: Memory,    # 被覆写的旧记忆
    new_memory_id: str,    # 新记忆的 ID
) -> None
```

**实现步骤**：

1. **标记旧记忆为无效**：
   - `active = False`
   - `superseded_by = new_memory_id`
   - `updated_at = now`

2. **写入 MemoryLog**：
   - `action = "overwrite"`
   - `detail = {"overwritten_by": new_memory_id, "reason": "conflict detected"}`

3. **提交事务**

**代码示例**：
```python
old_memory.active = False
old_memory.superseded_by = new_memory_id
old_memory.updated_at = datetime.utcnow()

log = MemoryLog(
    memory_id=old_memory.id,
    action="overwrite",
    detail={"overwritten_by": new_memory_id, "reason": "conflict detected"},
)
db.add(log)
await db.commit()
```

**调用时机**（在 [`memories.py:14-64`](app/api/memories.py)）：

```python
# 1. 检测冲突
conflicts = await detect_conflicts(db, content, tags, owner_id, scope)

# 2. 确定版本链
parent_id = None
version = 1
if conflicts:
    old = await get_memory(db, conflicts[0]["id"])
    if old:
        parent_id = old.id
        version = old.version + 1

# 3. 创建新记忆
memory = await create_memory(..., parent_id=parent_id)
memory.version = version

# 4. 覆写旧记忆
if conflicts:
    old = await get_memory(db, conflicts[0]["id"])
    if old:
        await supersede_memory(db, old, memory.id)
```

### 4. 软删除 ([`soft_delete`](app/services/memory_service.py))

**函数签名**：
```python
async def soft_delete(db: AsyncSession, memory_id: str) -> bool
```

**实现逻辑**：

1. **查询记忆**：通过主键获取
2. **标记删除**：`active = False`，更新 `updated_at`
3. **写入日志**：`action = "forget"`
4. **返回结果**：成功返回 `True`，不存在返回 `False`

**代码示例**：
```python
memory = await db.get(Memory, memory_id)
if not memory:
    return False

memory.active = False
memory.updated_at = datetime.utcnow()

log = MemoryLog(
    memory_id=memory_id,
    action="forget",
    detail={"reason": "user delete"},
)
db.add(log)
await db.commit()
return True
```

### 5. 复习操作 ([`review_memory`](app/services/memory_service.py))

**函数签名**：
```python
async def review_memory(
    db: AsyncSession,
    memory_id: str,
    action: str,    # "review" / "dismiss" / "done"
    user_id: str,
) -> Memory | None
```

**三种复习动作**：

| Action | 效果 | 用途 |
|--------|------|------|
| `review` | `strength = 1.0`，更新 `last_reviewed_at` | 用户点击"已复习" |
| `dismiss` | `active = False` | 用户点击"不再提醒" |
| `done` | `strength = min(strength × 1.5, 1.0)` | 用户点击"已处理" |

**实现逻辑**：

```python
memory = await db.get(Memory, memory_id)
if not memory:
    return None

now = datetime.utcnow()

if action == "review":
    memory.strength = 1.0
    memory.last_reviewed_at = now
    log_detail = {"action": "review", "user_id": user_id}

elif action == "dismiss":
    memory.active = False
    log_detail = {"action": "dismiss", "user_id": user_id}

elif action == "done":
    memory.strength = min(memory.strength * 1.5, 1.0)
    memory.last_reviewed_at = now
    log_detail = {"action": "done", "user_id": user_id}

memory.updated_at = now
log = MemoryLog(memory_id=memory_id, action="review", detail=log_detail)
db.add(log)
await db.commit()
return memory
```

### 6. REST API 端点

#### 6.1 POST /api/memories - 创建记忆

**请求**：
```json
{
  "scope": "personal",
  "owner_id": "ou_xxx",
  "type": "decision",
  "content": "决定用 MongoDB 存用户数据",
  "tags": {"keywords": ["MongoDB", "用户数据"], "entities": {}},
  "confidence": 0.85,
  "source_message_id": "om_xxx",
  "source_chat_id": "oc_xxx"
}
```

**响应**：
```json
{
  "ok": true,
  "data": {
    "id": "mem_xxx",
    "scope": "personal",
    "type": "decision",
    "content": "决定用 MongoDB 存用户数据",
    "strength": 1.0,
    "active": true,
    "version": 1,
    "parent_id": null,
    "created_at": "2026-04-27T10:00:00Z",
    "conflict_detected": false,
    "overwritten_id": null
  }
}
```

**内部流程**：
1. 调用 `detect_conflicts()` 检测冲突
2. 如有冲突，设置 `parent_id` 和 `version`
3. 调用 `create_memory()` 创建新记忆
4. 如有冲突，调用 `supersede_memory()` 覆写旧记忆

#### 6.2 GET /api/memories - 查询记忆

**查询参数**：
- `owner_id`（必填）：用户或群 ID
- `scope`：过滤 personal/group
- `type`：过滤记忆类型
- `keywords`：关键词搜索（触发三路召回）
- `active_only`：是否仅活跃记忆
- `page` / `page_size`：分页参数

**关键词搜索流程**：
```python
if keywords:
    # 使用 search_service 三路召回
    results = await search_memories(db, query=keywords, owner_id=owner_id, limit=page_size)
else:
    # 使用 list_memories 分页查询
    items, total = await list_memories(db, owner_id=owner_id, ...)
```

#### 6.3 DELETE /api/memories/{id} - 软删除

**响应**：
```json
{
  "ok": true,
  "data": null
}
```

内部调用 `soft_delete()`。

### 7. 数据一致性保证

#### 7.1 版本链完整性

- **创建时**：新记忆的 `parent_id` 必须指向存在的记忆
- **覆写时**：旧记忆的 `superseded_by` 必须指向新记忆
- **查询历史**：`get_history()` 使用 `visited` 防环

#### 7.2 审计日志

每个写操作都写入 `MemoryLog`：

| 操作 | action | detail 内容 |
|------|--------|-------------|
| 创建 | `create` | `source_message_id`, `confidence` |
| 覆写 | `overwrite` | `overwritten_by`, `reason` |
| 复习 | `review` | `action`, `user_id` |
| 衰减 | `decay` | `strength_before`, `strength_after`, `interval_days` |
| 删除 | `forget` | `reason` |

#### 7.3 并发控制

- **乐观锁**：`version` 字段在覆写时递增
- **事务隔离**：所有写操作在事务中完成
- **防重复**：未来可通过 `idempotency_key` 实现幂等

---

## 配置说明 ([`config.py`](app/config.py))

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `database_url` | `sqlite+aiosqlite:///./test.db` | 数据库连接串 |
| `feishu_app_id` | `""` | 飞书应用 ID |
| `feishu_app_secret` | `""` | 飞书应用密钥 |
| `mneme_service_token` | `""` | Plugin 认证 Token |
| `decay_base_rate` | `0.5` | 衰减基数率 |
| `decay_sensitivity` | `1.0` | 个人敏感度 |
| `push_l1_threshold` | `0.3` | L1 推送阈值 |
| `push_l1_daily_limit` | `3` | L1 每日上限 |
| `push_window_start` | `9` | 推送窗口开始（小时） |
| `push_window_end` | `19` | 推送窗口结束（小时） |

## 运行命令

```bash
# 激活环境
source .venv/bin/activate

# 本地开发（SQLite）
.venv/bin/python -m uvicorn app.main:app --port 3001 --reload

# 运行测试
.venv/bin/python -m pytest tests/ -v

# 语法检查
find app -name "*.py" -not -name "__init__.py" -exec .venv/bin/python -m py_compile {} \;

# 数据库迁移
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic revision --autogenerate -m "description"

# Docker 生产模式（PostgreSQL）
docker-compose up -d
```

## 架构设计亮点

### 1. 双数据库兼容设计

使用 `String(36)` 存储 UUID、`JSON` 类型替代 PostgreSQL 特定的 `UUID` / `JSONB`，实现 SQLite 和 PostgreSQL 无缝切换。

### 2. 规则优于 LLM

意图识别、实体提取、冲突检测均使用规则引擎，避免 LLM 调用开销和不确定性。

### 3. 版本链追踪

通过 `parent_id` 和 `superseded_by` 实现记忆的版本追溯，支持查询"某主题的历史版本"。

### 4. 防抖与限流

`PushLog` 表实现 L1/L2 推送的防抖（24h）和日上限（3条/天），避免信息过载。

### 5. 审计日志

所有操作写入 `MemoryLog`，支持追溯和抗干扰测试。

## Sprint 进度

| Sprint | 状态 | 说明 |
|--------|------|------|
| Sprint 1-3 (Python 服务) | ✅ 完成 | 核心 API、服务层、数据模型 |
| Sprint 3 (OpenClaw Plugin + Cron) | ⏳ 待实现 | Plugin 集成、定时任务 |

---

*文档版本：v1.0 | 最后更新：2026-04-27*
