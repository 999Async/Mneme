# Mneme - OpenClaw × PostgreSQL 接口设计文档

***

## 一、接口概述

### 1.1 整体架构

```plain&#x20;text
飞书消息 / @bot 指令
        │
        ▼
  OpenClaw Handler
        │
        ├──► 记忆创建  ──────────────────► PostgreSQL
        ├──► 记忆查询  ◄──────────────────  PostgreSQL
        ├──► 记忆更新（复习/衰减）────────► PostgreSQL
        ├──► 冲突检测与覆写 ─────────────► PostgreSQL
        ├──► Context Snapshot 保存/读取 ─► PostgreSQL
        │
        └──► 定时 Cron（每30分钟）────────► PostgreSQL ──► 飞书推送

```

### 1.2 Base URL

* **OpenClaw 内部服务**：`http://localhost:3001`（本地开发）

* **生产环境**：由架构侧提供 service URL

### 1.3 认证

* 所有请求需携带 `Authorization: Bearer <openclaw_token>` Header

* 幂等性通过 `X-Idempotency-Key: <event_id>` 保证

### 1.4 通用响应格式

```json
// 成功
{
  "code": 0,
  "message": "success",
  "data": { ... }
}

// 失败
{
  "code": <error_code>,
  "message": "<error_message>",
  "data": null
}

```

### 1.5 错误码定义

| code | 说明            |
| ---- | ------------- |
| 0    | 成功            |
| 1001 | 参数校验失败        |
| 1002 | 记忆不存在         |
| 1003 | 冲突检测失败        |
| 1004 | 数据库写入失败       |
| 1005 | 幂等重复（已处理过该事件） |
| 2001 | 推送失败（超出时间窗口）  |
| 2002 | 推送已达日上限（3条/天） |

***

## 二、接口清单

### 2.1 记忆模块

#### POST /api/memories

**创建记忆**（自动抽取 + 显式存储共用）

| 参数                  | 类型     | 必填 | 说明                                         |
| ------------------- | ------ | -- | ------------------------------------------ |
| scope               | string | ✅  | `personal` / `group`                       |
| owner\_id           | string | ✅  | 用户 open\_id 或群 chat\_id                    |
| type                | string | ✅  | `decision` / `fact` / `intent` / `command` |
| content             | string | ✅  | 记忆内容原文                                     |
| context\_snapshot   | string | ✅  | 提取时的上下文摘要（JSON 字符串）                        |
| tags                | object | ✅  | 关键词 + 实体结构（见下方）                            |
| source\_message\_id | string | ✅  | 原始飞书消息 ID                                  |
| source\_chat\_id    | string | ✅  | 原始群聊 ID                                    |
| confidence          | float  | ✅  | 置信度 0\~1，显式存储固定 1.0                        |
| idempotency\_key    | string | ✅  | event\_id，用于幂等去重                           |

**tags 结构**（扩展自 PRD 附录 C）：

```json
{
  "keywords": ["API", "密钥", "更新"],
  "entities": {
    "person": ["张总", "李总"],
    "project": ["A项目"],
    "role": { "A项目": "接口人" },
    "date": ["下周五"],
    "config": ["API密钥"]
  }
}

```

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "mem_xxxxxxxxxxxx",
    "scope": "group",
    "owner_id": "oc_xxxxx",
    "type": "decision",
    "content": "周报现在发给 B",
    "tags": { "keywords": ["周报", "B"], "entities": { "person": ["B"] } },
    "strength": 1.0,
    "active": true,
    "version": 1,
    "parent_id": null,
    "created_at": "2026-04-25T10:00:00+08:00",
    "updated_at": "2026-04-25T10:00:00+08:00",
    "conflict_detected": true,
    "overwritten_id": "mem_yyyyyyyyyyyy"
  }
}

```

***

#### GET /api/memories

**查询记忆**

| 参数            | 类型      | 必填 | 说明                             |
| ------------- | ------- | -- | ------------------------------ |
| owner\_id     | string  | ✅  | 用户 open\_id 或群 chat\_id        |
| scope         | string  | ❌  | `personal` / `group` / 空（返回两者） |
| type          | string  | ❌  | 记忆类型过滤                         |
| keywords      | string  | ❌  | 关键词搜索（逗号分隔）                    |
| entity\_type  | string  | ❌  | 实体类型（如 `person`）               |
| entity\_value | string  | ❌  | 实体值（如 `张总`）                    |
| active\_only  | boolean | ❌  | 默认 true，只返回 active=true 的记忆    |
| page          | int     | ❌  | 默认 1                           |
| page\_size    | int     | ❌  | 默认 20，最大 50                    |

**召回逻辑**（三路召回，PRD 确认）：

* **实体锚定召回**：entity\_type + entity\_value 精确匹配

* **话题共现召回**：keywords 交集匹配

* **语义相似兜底**：content 文本匹配（Demo 阶段为关键词子串匹配）

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 2,
    "page": 1,
    "page_size": 20,
    "items": [
      {
        "id": "mem_xxxxxxxxxxxx",
        "scope": "group",
        "type": "decision",
        "content": "周报现在发给 B",
        "tags": { "keywords": ["周报", "B"], "entities": { "person": ["B"] } },
        "strength": 0.65,
        "active": true,
        "version": 2,
        "created_at": "2026-04-25T10:00:00+08:00",
        "updated_at": "2026-04-25T14:00:00+08:00",
        "source_message_id": "om_xxxxx"
      }
    ]
  }
}

```

***

#### GET /api/memories/:id

**获取单条记忆详情**

| 参数 | 类型     | 必填 | 说明    |
| -- | ------ | -- | ----- |
| id | string | ✅  | 记忆 ID |

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "mem_xxxxxxxxxxxx",
    "scope": "group",
    "owner_id": "oc_xxxxx",
    "type": "decision",
    "content": "周报现在发给 B",
    "context_snapshot": "{\"topic\":\"周报发送对象变更\",\"messages\":[...]}",
    "tags": { "keywords": ["周报"], "entities": { "person": ["B"] } },
    "strength": 0.65,
    "active": true,
    "version": 2,
    "parent_id": "mem_yyyyyyyyyyyy",
    "last_reviewed_at": "2026-04-25T14:00:00+08:00",
    "created_at": "2026-04-25T10:00:00+08:00",
    "updated_at": "2026-04-25T14:00:00+08:00"
  }
}

```

***

#### GET /api/memories/:id/history

**获取记忆版本历史**

| 参数 | 类型     | 必填 | 说明      |
| -- | ------ | -- | ------- |
| id | string | ✅  | 当前记忆 ID |

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "current": {
      "id": "mem_zzzzzzzzzzzz",
      "content": "周报现在发给 B",
      "version": 2,
      "active": true,
      "created_at": "2026-04-25T14:00:00+08:00"
    },
    "history": [
      {
        "id": "mem_yyyyyyyyyyyy",
        "content": "周报发给 A",
        "version": 1,
        "active": false,
        "created_at": "2026-04-01T10:00:00+08:00"
      }
    ]
  }
}

```

***

#### POST /api/memories/:id/review

**复习记忆（强化 strength）**

| 参数       | 类型     | 必填 | 说明                                              |
| -------- | ------ | -- | ----------------------------------------------- |
| id       | string | ✅  | 记忆 ID                                           |
| action   | string | ✅  | 复习动作：`review`（强化）/ `dismiss`（不再提醒）/ `done`（已处理） |
| user\_id | string | ✅  | 操作用户 open\_id                                   |

**复习强化规则**：

* `review`：strength 恢复至 1.0，last\_reviewed\_at 更新

* `dismiss`：active=false，进入休眠，不计入日推送上限

* `done`：strength × 1.5（上限 1.0），并标记为已处理

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "mem_xxxxxxxxxxxx",
    "strength": 1.0,
    "last_reviewed_at": "2026-04-25T15:00:00+08:00",
    "active": true
  }
}

```

***

#### DELETE /api/memories/:id

**软删除记忆**（仅 owner 可操作）

| 参数       | 类型     | 必填 | 说明         |
| -------- | ------ | -- | ---------- |
| id       | string | ✅  | 记忆 ID      |
| user\_id | string | ✅  | 请求删除的用户 ID |

***

### 2.2 冲突检测模块

#### POST /api/memories/conflicts/detect

**冲突检测**（创建记忆时自动调用，也供手动覆写触发）

| 参数          | 类型     | 必填 | 说明                   |
| ----------- | ------ | -- | -------------------- |
| content     | string | ✅  | 待检测的记忆内容             |
| tags        | object | ✅  | 关键词 + 实体结构           |
| owner\_id   | string | ✅  | 归属者 ID               |
| scope       | string | ✅  | `personal` / `group` |
| exclude\_id | string | ❌  | 排除的记忆 ID（如当前更新的是哪条）  |

**冲突判断规则**（PRD 确认）：

* **语义相似度 > 0.75**：粗召回（实体 > keywords > content 子串）

* **时间戳判断权威版本**：新注入力争覆盖旧版本

**响应示例**（检测到冲突）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "has_conflict": true,
    "conflicts": [
      {
        "id": "mem_yyyyyyyyyyyy",
        "content": "周报发给 A",
        "similarity_score": 0.82,
        "conflict_reason": "实体 person 重复（A → B 变更）",
        "created_at": "2026-04-01T10:00:00+08:00"
      }
    ]
  }
}

```

**响应示例**（无冲突）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "has_conflict": false,
    "conflicts": []
  }
}

```

***

### 2.3 心流恢复模块（L0）

#### POST /api/context-snapshots

**保存上下文快照**

| 参数                   | 类型     | 必填 | 说明                |
| -------------------- | ------ | -- | ----------------- |
| user\_id             | string | ✅  | 用户 open\_id       |
| chat\_id             | string | ✅  | 所在群 chat\_id      |
| snapshot             | string | ✅  | 上下文快照内容（JSON 字符串） |
| expires\_in\_minutes | int    | ❌  | 过期时间，默认 30 分钟     |

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "snap_xxxxxxxxxxxx",
    "user_id": "ou_xxxxx",
    "expires_at": "2026-04-25T15:30:00+08:00"
  }
}

```

***

#### GET /api/context-snapshots/latest

**获取最新的上下文快照**

| 参数       | 类型     | 必填 | 说明          |
| -------- | ------ | -- | ----------- |
| user\_id | string | ✅  | 用户 open\_id |

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "snap_xxxxxxxxxxxx",
    "user_id": "ou_xxxxx",
    "chat_id": "oc_xxxxx",
    "snapshot": "{\"topic\":\"API对接\",\"last_message\":\"这个接口要传 user_id\"}",
    "created_at": "2026-04-25T15:00:00+08:00",
    "expires_at": "2026-04-25T15:30:00+08:00"
  }
}

```

***

#### DELETE /api/context-snapshots/latest

**清除最新的上下文快照**（用户点击"开始新任务"时调用）

| 参数       | 类型     | 必填 | 说明          |
| -------- | ------ | -- | ----------- |
| user\_id | string | ✅  | 用户 open\_id |

***

### 2.4 定时任务接口（OpenClaw Cron 调用）

#### POST /api/cron/decay

**遗忘曲线衰减扫描**（每 30 分钟调用一次）

| 参数          | 类型      | 必填 | 说明                          |
| ----------- | ------- | -- | --------------------------- |
| batch\_size | int     | ❌  | 每批处理量，默认 100                |
| dry\_run    | boolean | ❌  | 默认 false；true 时只返回扫描结果不实际更新 |

**扫描逻辑**：

* 查询 `last_reviewed_at` 或 `updated_at` 距今超过预设衰减间隔的记忆

* 按衰减公式更新 strength：参考 PRD F2 遗忘曲线节点

* strength ≤ 0.3 的记忆进入 L1 推送候选队列

**推送时间窗口校验**（BR-007）：

* 工作日 09:00-19:00 可推送

* 每日 L1 上限 3 条（按 strength 从低到高排序）

* 周末压缩为每日一次合并推送

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "scanned": 156,
    "decayed": 12,
    "triggered_push_candidates": 3,
    "skipped_off_hours": 5,
    "skipped_daily_limit_reached": 0
  }
}

```

***

#### POST /api/cron/push-l1

**L1 推送执行**（扫描 strength ≤ 0.3 的记忆，生成飞书卡片推送）

| 参数          | 类型  | 必填 | 说明                |
| ----------- | --- | -- | ----------------- |
| batch\_size | int | ❌  | 每次推送量，默认 3（符合日上限） |

**推送卡片格式**（参考 PRD 4.2.4）：

```plain&#x20;text
📌 你可能快忘了：

**决策**：项目数据库决定使用 MongoDB（存储用户数据）

💡 来源：群聊「技术方案讨论」| 4月20日
🔗 查看上下文：[点击查看]

———
[✅ 已复习] [🙋 已处理] [❌ 不再提醒]

```

***

#### GET /api/cron/push-l2/candidates

**获取 L2 候选记忆**（对话语义相似度触发时调用）

| 参数                | 类型     | 必填 | 说明            |
| ----------------- | ------ | -- | ------------- |
| current\_message  | string | ✅  | 当前飞书消息内容      |
| current\_snapshot | string | ✅  | 当前上下文摘要       |
| chat\_id          | string | ✅  | 当前群 chat\_id  |
| threshold         | float  | ❌  | 相似度阈值，默认 0.75 |

***

### 2.5 推送记录模块

#### POST /api/push-logs

**记录推送历史**（用于防抖和日上限控制）

| 参数               | 类型     | 必填 | 说明                        |
| ---------------- | ------ | -- | ------------------------- |
| memory\_id       | string | ✅  | 关联记忆 ID                   |
| push\_type       | string | ✅  | `L1` / `L2` / `L0`        |
| target\_id       | string | ✅  | 推送目标（user\_id 或 chat\_id） |
| idempotency\_key | string | ✅  | event\_id，幂等去重            |

***

#### GET /api/push-logs/check

**检查推送防抖状态**

| 参数         | 类型     | 必填 | 说明          |
| ---------- | ------ | -- | ----------- |
| memory\_id | string | ✅  | 记忆 ID       |
| target\_id | string | ✅  | 推送目标        |
| push\_type | string | ✅  | `L1` / `L2` |

**L2 防抖规则**：同一记忆对同一用户/群 24 小时内最多一次。**L1 防抖规则**：每日上限 3 条（按 strength 从低到高推送）。

***

### 2.6 日志记录模块

#### POST /api/memory-logs

**记录记忆操作日志**（写 memory\_logs 表）

| 参数         | 类型     | 必填 | 说明                                                     |
| ---------- | ------ | -- | ------------------------------------------------------ |
| memory\_id | string | ✅  | 关联记忆 ID                                                |
| action     | string | ✅  | `create` / `review` / `decay` / `overwrite` / `forget` |
| detail     | object | ✅  | 操作详情（JSON）                                             |

**detail 示例**：

```json
// create
{ "source_message_id": "om_xxxxx", "confidence": 0.85 }

// overwrite
{ "overwritten_by": "mem_zzzzzzzzzzzz", "reason": "实体 person 变更" }

// decay
{ "strength_before": 0.65, "strength_after": 0.42, "interval_hours": 24 }

```

***

## 三、数据库表结构（供参考）

### memories 主表

| 字段                 | 来源接口                               | 操作                         |
| ------------------ | ---------------------------------- | -------------------------- |
| id                 | 全部                                 | 主键                         |
| scope              | POST /memories                     | 创建时写入                      |
| owner\_id          | 全部                                 | 索引列                        |
| type               | POST /memories                     | 创建时写入                      |
| content            | 全部                                 | 全文索引（可选）                   |
| context\_snapshot  | POST /memories, /context-snapshots | JSON 存储                    |
| tags               | POST /memories                     | JSON 存储，含 entities 结构      |
| strength           | /memories/review, /cron/decay      | 更新操作                       |
| version            | 全部                                 | 乐观锁，覆写时递增                  |
| parent\_id         | 冲突覆写时                              | 指向旧版本记忆 ID                 |
| active             | 全部                                 | 默认 true，覆写/dismiss 时 false |
| last\_reviewed\_at | /memories/review                   | 复习操作更新                     |
| created\_at        | 全部                                 | 创建时写入                      |
| updated\_at        | 全部                                 | 每次更新写入                     |

### memory\_logs 历史表

| 字段          | 来源接口              |
| ----------- | ----------------- |
| id          | POST /memory-logs |
| memory\_id  | POST /memory-logs |
| action      | POST /memory-logs |
| detail      | POST /memory-logs |
| created\_at | 创建时写入             |

***

## 四、OpenClaw Handler 层设计

### 4.1 消息处理流程

```plain&#x20;text
飞书消息事件
    │
    ▼
event_id 幂等检查（内存 Set / Redis）
    │ 已存在 → 直接返回 200
    ▼
意图识别（规则 + 关键词）
    │
    ├─ 显式存储指令（记住/大家记住）──► 构建 tags ──► POST /api/memories
    │
    ├─ 查询指令 ────────────────────► 解析 query ──► GET /api/memories
    │
    ├─ 覆盖指令 ────────────────────► 构建新记忆 ──► POST /api/memories/conflicts/detect
    │
    ├─ 回到刚才 ────────────────────► GET /api/context-snapshots/latest
    │
    ├─ 自动抽取（F1）───────────────► 规则匹配 ──► POST /api/memories
    │
    └─ L2 触发（对话中）─────────────► 语义相似度检测 ──► GET /api/cron/push-l2/candidates

```

### 4.2 定时任务注册

```javascript
// OpenClaw cron 配置（每 30 分钟）
{
  name: 'mneme-decay-scan',
  schedule: '*/30 * * * *',
  handler: () => callInternalAPI('/api/cron/decay')
}

```

***

## 五、待确认事项

| # | 问题                     | 优先级 | 状态                 |
| - | ---------------------- | --- | ------------------ |
| 1 | L2 静默推送发群里还是发私聊（Q6）    | 高   | ⏳ 待确认              |
| 2 | entity 提取规则（正则还是 LLM？） | 高   | ⏳ 关联意图识别关键词库（T0-4） |
| 3 | 生产环境 PostgreSQL 连接参数   | 中   | ⏳ 架构侧提供            |
| 4 | push\_l1 推送时间窗口的时区     | 中   | 默认北京时间（+08:00）     |

***

## 六、接口版本

| 版本   | 日期         | 变更说明          |
| ---- | ---------- | ------------- |
| v0.1 | 2026-04-25 | 初始版本，覆盖全部模块接口 |

***

*文档版本：v0.1 | 如有疑问请联系飞书集成开发工程师*
