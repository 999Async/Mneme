"""飞书多维表格同步 — 通过 lark-cli 管理 Mneme 记忆可视化面板"""

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from app.cache import redis_client
from app.config import settings

logger = logging.getLogger(__name__)

# Redis key 前缀
_BASE_KEY_PREFIX = "mneme:base"
_RECORD_KEY_PREFIX = "mneme:base:record"
# TTL: 365 days for base config
_PERMANENT_TTL = 365 * 24 * 3600

# 可重试的并发冲突错误码
_RETRYABLE_ERRORS = {1254291, 1254607}


# ── lark-cli 执行层 ─────────────────────────────────────────────────────


async def _lark_cli(*args: str) -> dict | None:
    """执行 lark-cli 命令，返回 JSON 结果或 None（失败时）"""
    cmd = ["lark-cli", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            err = stderr.decode().strip()[:300]
            out = stdout.decode().strip()[:300]
            logger.error("lark-cli 失败 (rc=%d) stderr=%s stdout=%s", proc.returncode, err, out)
            return None
        text = stdout.decode().strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("lark-cli 返回非 JSON: %s", text[:200])
            return None
    except asyncio.TimeoutError:
        logger.error("lark-cli 超时: %s", " ".join(cmd[:4]))
        return None
    except Exception as e:
        logger.error("lark-cli 异常: %s", e)
        return None


def _parse_error_code(result: dict | None) -> int | None:
    """从 lark-cli 返回中提取飞书错误码"""
    if not result:
        return None
    error = result.get("error", {})
    if isinstance(error, dict):
        return error.get("code")
    return None


async def _lark_cli_with_retry(*args: str, retries: int = 3) -> dict | None:
    """带指数退避重试的 lark-cli 调用，自动处理并发冲突"""
    for attempt in range(retries):
        result = await _lark_cli(*args)
        if result is not None:
            err_code = _parse_error_code(result)
            if err_code in _RETRYABLE_ERRORS and attempt < retries - 1:
                delay = 0.5 * (2 ** attempt)
                logger.warning("lark-cli 并发冲突 (%d)，重试 (%d/%d) 等待 %.1fs", err_code, attempt + 1, retries, delay)
                await asyncio.sleep(delay)
                continue
            return result
        if attempt < retries - 1:
            delay = 2 ** attempt  # 1s, 2s, 4s
            logger.warning("lark-cli 重试 (%d/%d)，等待 %ds", attempt + 1, retries, delay)
            await asyncio.sleep(delay)
    return None


def _base_redis_key(chat_id: str) -> str:
    return f"{_BASE_KEY_PREFIX}:{chat_id}"


def _record_redis_key(memory_id: str) -> str:
    return f"{_RECORD_KEY_PREFIX}:{memory_id}"


def _sync_lock_key(memory_id: str) -> str:
    """Base 同步锁，防止重复同步"""
    return f"{_RECORD_KEY_PREFIX}:sync_lock:{memory_id}"


# ── 字段定义 ────────────────────────────────────────────────────────────

# 飞书支持的字段类型白名单
_SUPPORTED_FIELD_TYPES = {"text", "number", "select", "datetime", "user", "group_chat",
                          "checkbox", "url", "phone", "email", "location", "link", "attachment"}

FIELDS: list[dict] = [
    # "记忆ID" 不在此列表中 — 直接使用飞书默认主字段 "文本" 存储
    {
        "type": "select",
        "name": "类型",
        "multiple": False,
        "options": [
            {"name": "decision", "hue": "Blue", "lightness": "Lighter"},
            {"name": "fact", "hue": "Green", "lightness": "Lighter"},
            {"name": "intent", "hue": "Orange", "lightness": "Lighter"},
            {"name": "command", "hue": "Purple", "lightness": "Lighter"},
        ],
    },
    {"type": "text", "name": "内容"},
    {"type": "number", "name": "版本"},
    {
        "type": "select",
        "name": "状态",
        "multiple": False,
        "options": [
            {"name": "active", "hue": "Green", "lightness": "Light"},
            {"name": "superseded", "hue": "Yellow", "lightness": "Light"},
            {"name": "deleted", "hue": "Gray", "lightness": "Standard"},
        ],
    },
    {"type": "datetime", "name": "创建时间", "style": {"format": "yyyy-MM-dd HH:mm"}},
    {"type": "text", "name": "标签"},
    {"type": "text", "name": "来源"},
    {"type": "text", "name": "父记忆ID"},
    {"type": "text", "name": "附件文档", "style": {"type": "url"}},
]

# 飞书默认主字段名（不可删除/重命名），用于存储记忆 ID
# 不硬编码，在 ensure_base 时动态检测
_PRIMARY_FIELD = ""


async def _detect_primary_field(app_token: str, table_id: str) -> str:
    """从字段列表中检测主字段名称。

    检测策略（按优先级）：
    1. 找 is_primary=true 的字段（支持多种字段名变体）
    2. 按名称特征匹配：飞书默认主字段通常是"文本"或"标题"
    3. 按字段特征匹配：text 类型 + 无特殊样式 + 排在最前面
    4. 兜底：取字段列表第一个
    """
    result = await _lark_cli_with_retry(
        "base", "+field-list",
        "--base-token", app_token,
        "--table-id", table_id,
        "--limit", "50",
        "--as", "bot",
    )
    if not result:
        return ""
    data = result.get("data", result)
    fields = data.get("fields", data.get("items", []))
    if not fields:
        return ""

    # 调试：记录字段结构
    logger.info("字段列表 (前3个): %s", json.dumps(fields[:3], ensure_ascii=False, indent=2))

    # 策略1: 找 is_primary=true（支持多种可能的字段名）
    for f in fields:
        is_primary = f.get("is_primary", f.get("isPrimary", f.get("primary", False)))
        if is_primary:
            field_id = f.get("id", f.get("field_id", ""))
            name = f.get("name", "")
            logger.info("✓ 策略1: 找到 is_primary 字段: name=%s id=%s", name, field_id)
            return name

    # 策略2: 按名称特征匹配（飞书默认主字段名）
    default_primary_names = {"文本", "标题", "Title", "title", "Text", "text", "名称", "Name", "name"}
    for f in fields:
        name = f.get("name", "")
        if name in default_primary_names:
            field_id = f.get("id", f.get("field_id", ""))
            logger.info("✓ 策略2: 按名称匹配找到主字段: name=%s id=%s", name, field_id)
            return name

    # 策略3: 按字段特征匹配（text 类型 + 无特殊样式 + 排在最前）
    for i, f in enumerate(fields):
        ftype = f.get("type", "")
        style = f.get("style", {})
        # 主字段通常是纯文本类型，无特殊样式
        if ftype == "text" and not style:
            name = f.get("name", "")
            field_id = f.get("id", f.get("field_id", ""))
            # 优先选择排在前面的 text 字段（前3个）
            if i < 3:
                logger.info("✓ 策略3: 按特征匹配找到主字段: name=%s id=%s (位置=%d)", name, field_id, i)
                return name

    # 策略4: 兜底，首列即主键
    logger.warning("✗ 策略4: 未找到明确主字段，使用首列作为主键 (可能不准确)")
    first_name = fields[0].get("name", "")
    first_id = fields[0].get("id", fields[0].get("field_id", ""))
    logger.warning("首列字段: name=%s id=%s", first_name, first_id)
    return first_name


async def _rename_primary_field(app_token: str, table_id: str, primary_name: str) -> str | None:
    """尝试将默认主字段重命名为"记忆ID"。

    成功返回 "记忆ID"，失败返回原主字段名，无法检测返回 None。
    """
    result = await _lark_cli_with_retry(
        "base", "+field-update",
        "--base-token", app_token,
        "--table-id", table_id,
        "--field-id", primary_name,
        "--json", json.dumps({"name": "记忆ID", "type": "text"}, ensure_ascii=False),
        "--as", "bot",
    )
    if result:
        logger.info("主字段已重命名: %s → 记忆ID", primary_name)
        return "记忆ID"
    else:
        logger.warning("主字段重命名失败（%s → 记忆ID），保留原名", primary_name)
        return primary_name


# ── 核心 API ────────────────────────────────────────────────────────────


async def ensure_base(chat_id: str) -> dict | None:
    """确保多维表格存在。返回 {app_token, table_id, url, is_new} 或 None。"""
    if not settings.feishu_base_enabled:
        return None

    # 检查 Redis 缓存
    cached = await redis_client.get(_base_redis_key(chat_id))
    if cached:
        try:
            config = json.loads(cached)
            config["is_new"] = False
            return config
        except (json.JSONDecodeError, ValueError):
            pass

    # 创建新 Base（使用配置的共享文件夹，否则默认空间）
    folder_token = getattr(settings, "feishu_base_folder_token", "") or ""
    create_cmd = [
        "base", "+base-create",
        "--name", "Mneme 记忆管理",
        "--time-zone", settings.timezone,
        "--as", "bot",
    ]
    if folder_token:
        create_cmd.extend(["--folder-token", folder_token])
    result = await _lark_cli_with_retry(*create_cmd)
    if not result:
        logger.error("创建多维表格失败")
        return None

    # lark-cli 响应格式: {"ok": true, "data": {"base": {"base_token": "...", "url": "..."}}}
    base_info = result.get("data", result).get("base", {})
    app_token = base_info.get("base_token", "")
    base_url = base_info.get("url", "")

    if not app_token:
        logger.error("创建多维表格返回缺少 app_token: %s", result)
        return None

    # 获取默认 table_id
    tables_result = await _lark_cli_with_retry(
        "base", "+table-list",
        "--base-token", app_token,
        "--limit", "10",
        "--as", "bot",
    )
    table_id = ""
    if tables_result:
        data = tables_result.get("data", tables_result)
        tables = data.get("tables", [])
        if tables:
            table_id = tables[0].get("id", "")

    if not table_id:
        logger.error("无法获取默认 table_id")
        return None

    # 检测默认主字段名称（动态，不硬编码）
    primary_name = await _detect_primary_field(app_token, table_id)
    if not primary_name:
        logger.error("无法检测主字段名称")
        return None
    logger.info("检测到主字段: %s", primary_name)

    # 尝试将主字段重命名为"记忆ID"（失败则保留原名）
    renamed = await _rename_primary_field(app_token, table_id, primary_name)
    if renamed:
        primary_name = renamed

    # 先删除默认非主字段，再创建自定义字段（串行，避免并发冲突）
    await _delete_default_fields(app_token, table_id, primary_name)
    await _create_fields(app_token, table_id)

    # 验证字段
    if not await _verify_fields(app_token, table_id, primary_name):
        logger.error("字段创建验证失败，不缓存配置")
        return None

    # 清理多余的默认字段（在字段创建后再次尝试删除）
    await _cleanup_extra_fields(app_token, table_id, primary_name)

    # 配置默认视图按"状态"字段分组
    await _configure_default_view_group(app_token, table_id)

    # 给群聊成员授予编辑权限
    await _grant_edit_access(app_token, chat_id)

    # 存入 Redis（含 primary_name，不含 is_new）
    base_config = {
        "app_token": app_token,
        "table_id": table_id,
        "url": base_url,
        "primary_name": primary_name,
    }
    await redis_client.set(
        _base_redis_key(chat_id),
        json.dumps(base_config, ensure_ascii=False),
        ttl_seconds=_PERMANENT_TTL,
    )

    logger.info("多维表格创建成功: app_token=%s table_id=%s url=%s", app_token, table_id, base_url)
    base_config["is_new"] = True

    # Add chat tab (best-effort, silent failure)
    if base_url:
        await add_chat_tab(chat_id, base_url)

    return base_config


async def _grant_edit_access(app_token: str, chat_id: str) -> None:
    """给群聊成员授予多维表格的可管理权限

    通过飞书 Drive 权限 API 添加群聊为协作者。
    API: POST /open-apis/drive/v1/permissions/{token}/members?type=bitable
    """
    import httpx
    from app.services.feishu_push_service import feishu_push

    token = await feishu_push._get_tenant_token()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/drive/v1/permissions/{app_token}/members",
                headers={"Authorization": f"Bearer {token}"},
                params={"type": "bitable"},
                json={
                    "member_type": "openchat",
                    "member_id": chat_id,
                    "perm": "full_access",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    logger.info("已授予群聊 %s 可管理权限", chat_id)
                else:
                    logger.warning("授权群聊失败: code=%s msg=%s", data.get("code"), data.get("msg"))
            else:
                logger.warning("授权群聊 API 返回 %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("授权群聊异常: %s", e)


async def _delete_default_fields(app_token: str, table_id: str, primary_name: str) -> None:
    """删除飞书默认创建的非主字段（单选、日期、附件等）。

    主字段（索引列）不可删除，保留用于存储记忆ID。
    注意：此函数在创建自定义字段之前调用，应删除所有非主字段的默认字段。
    """
    our_names = {f["name"] for f in FIELDS}

    result = await _lark_cli_with_retry(
        "base", "+field-list",
        "--base-token", app_token,
        "--table-id", table_id,
        "--limit", "50",
        "--as", "bot",
    )
    if not result:
        return

    data = result.get("data", result)
    fields = data.get("fields", data.get("items", []))
    for field in fields:
        name = field.get("name", "")
        field_id = field.get("id", field.get("field_id", ""))
        # 支持多种可能的 is_primary 字段名
        is_primary = field.get("is_primary", field.get("isPrimary", field.get("primary", False)))
        if not field_id:
            continue

        # 只保护主字段：is_primary 标记 + 名称匹配 + 首字段兜底
        # 不检查 name in our_names，因为此时尚未创建自定义字段
        if is_primary:
            logger.info("保护主字段 (is_primary=True): %s (%s)", name, field_id)
            continue
        if name in our_names or name == primary_name or field == fields[0]:
            logger.info("保护字段: %s (%s) - reason: our_names=%s primary_name=%s is_first=%s",
                       name, field_id, name in our_names, name == primary_name, field == fields[0])
            continue

        # 删除其他默认字段（包括可能名为"附件"、"单选"等的默认字段）
        logger.info("尝试删除默认字段: %s (%s)", name, field_id)
        ok = await _lark_cli_with_retry(
            "base", "+field-delete",
            "--base-token", app_token,
            "--table-id", table_id,
            "--field-id", field_id,
            "--yes",
            "--as", "bot",
        )
        if ok:
            logger.info("✓ 已删除默认字段: %s (%s)", name, field_id)
        else:
            logger.warning("✗ 删除默认字段失败: %s (%s)", name, field_id)
        await asyncio.sleep(0.5)


async def _cleanup_extra_fields(app_token: str, table_id: str, primary_name: str) -> None:
    """在字段创建后再次清理多余的默认字段（如"日期"等飞书自动创建的字段）"""
    our_names = {f["name"] for f in FIELDS} | {primary_name}

    result = await _lark_cli_with_retry(
        "base", "+field-list",
        "--base-token", app_token,
        "--table-id", table_id,
        "--limit", "50",
        "--as", "bot",
    )
    if not result:
        return

    data = result.get("data", result)
    fields = data.get("fields", data.get("items", []))

    for field in fields:
        name = field.get("name", "")
        field_id = field.get("id", field.get("field_id", ""))
        # 支持多种可能的 is_primary 字段名
        is_primary = field.get("is_primary", field.get("isPrimary", field.get("primary", False)))

        if not field_id:
            continue

        # 保护主字段和我们的字段
        if is_primary or name in our_names or field == fields[0]:
            continue

        # 删除多余字段（如"日期"、"单选"、"附件"等飞书默认字段）
        ok = await _lark_cli_with_retry(
            "base", "+field-delete",
            "--base-token", app_token,
            "--table-id", table_id,
            "--field-id", field_id,
            "--yes",
            "--as", "bot",
        )
        if ok:
            logger.info("✓ 清理多余字段: %s (%s)", name, field_id)
        else:
            logger.warning("✗ 清理多余字段失败: %s (%s)", name, field_id)
        await asyncio.sleep(0.5)


async def _create_fields(app_token: str, table_id: str) -> None:
    """逐个创建字段（串行，带重试，避免并发冲突）。"""
    for field_def in FIELDS:
        ftype = field_def.get("type", "")
        fname = field_def.get("name", "")

        # 字段类型合法性校验
        if ftype not in _SUPPORTED_FIELD_TYPES:
            logger.error("不支持的字段类型: type=%s name=%s，跳过", ftype, fname)
            continue

        result = await _lark_cli_with_retry(
            "base", "+field-create",
            "--base-token", app_token,
            "--table-id", table_id,
            "--json", json.dumps(field_def, ensure_ascii=False),
            "--as", "bot",
        )
        if result:
            ok_data = result.get("data", result)
            if ok_data.get("created"):
                logger.info("字段创建成功: %s", fname)
            else:
                logger.warning("字段创建可能失败: %s → %s", fname, result)
        else:
            logger.warning("字段创建失败: %s", fname)
        # 间隔 0.5s 避免并发写冲突 (1254291)
        await asyncio.sleep(0.5)


async def _verify_fields(app_token: str, table_id: str, primary_name: str) -> bool:
    """验证所有必需字段是否已存在（含主字段检查）"""
    result = await _lark_cli_with_retry(
        "base", "+field-list",
        "--base-token", app_token,
        "--table-id", table_id,
        "--limit", "50",
        "--as", "bot",
    )
    if not result:
        return False
    data = result.get("data", result)
    raw_fields = data.get("fields", data.get("items", []))
    existing = {f.get("name") for f in raw_fields}

    # 检查主字段存在
    if primary_name not in existing:
        logger.error("主字段缺失: %s (现有字段: %s)", primary_name, existing)
        return False

    # 检查所有自定义字段
    required = {f["name"] for f in FIELDS}
    missing = required - existing
    if missing:
        logger.error("字段缺失: %s (现有字段: %s)", missing, existing)
        return False
    return True


async def _configure_default_view_group(app_token: str, table_id: str) -> None:
    """配置默认视图按"状态"字段分组"""
    # 获取视图列表
    views_result = await _lark_cli_with_retry(
        "base", "+view-list",
        "--base-token", app_token,
        "--table-id", table_id,
        "--limit", "50",
        "--as", "bot",
    )
    if not views_result:
        logger.warning("获取视图列表失败，跳过分组配置")
        return

    data = views_result.get("data", views_result)
    views = data.get("views", data.get("items", []))
    if not views:
        logger.warning("没有找到视图，跳过分组配置")
        return

    # 找到默认视图（通常是第一个 grid 视图，或者第一个视图）
    default_view = None
    for view in views:
        view_type = view.get("type", "")
        if view_type == "grid":
            default_view = view
            break

    if not default_view:
        default_view = views[0]

    view_id = default_view.get("id", "")
    view_name = default_view.get("name", "")
    view_type = default_view.get("type", "")

    if not view_id:
        logger.warning("无法获取默认视图 ID")
        return

    # 只有 grid/kanban/gantt 视图支持分组
    if view_type not in ("grid", "kanban", "gantt"):
        logger.info("视图类型 %s 不支持分组，跳过配置", view_type)
        return

    # 配置按"状态"字段分组
    group_config = {
        "group_config": [
            {"field": "状态", "desc": False}
        ]
    }

    result = await _lark_cli_with_retry(
        "base", "+view-set-group",
        "--base-token", app_token,
        "--table-id", table_id,
        "--view-id", view_id,
        "--json", json.dumps(group_config, ensure_ascii=False),
        "--as", "bot",
    )

    if result:
        logger.info("默认视图已配置按'状态'分组: %s (%s)", view_name, view_id)
    else:
        logger.warning("配置视图分组失败: %s", view_name)


async def upsert_record(memory: Any) -> bool:
    """新建/更新多维表格记录。memory 需要有 id, type, content, strength, version, active, created_at, tags 等属性。"""
    if not settings.feishu_base_enabled:
        return False

    # 检查同步锁，防止重复同步（10秒内的重复同步会被跳过）
    sync_lock_key = _sync_lock_key(memory.id)
    existing_lock = await redis_client.get(sync_lock_key)
    if existing_lock:
        logger.debug("跳过重复同步: memory_id=%s", memory.id)
        return True
    # 设置同步锁（10秒TTL）
    await redis_client.set(sync_lock_key, "1", ttl_seconds=10)

    # 获取 base 配置
    # 从 memory.source_chat_id 获取 chat_id
    chat_id = getattr(memory, "source_chat_id", "")
    owner_id = getattr(memory, "owner_id", "")
    if not chat_id:
        return False

    base_config = await _get_base_config(chat_id)
    if not base_config:
        return False

    app_token = base_config["app_token"]
    table_id = base_config["table_id"]
    primary_name = base_config.get("primary_name", "")

    # 检查是否有已有 record_id
    record_id = await redis_client.get(_record_redis_key(memory.id))

    # 如果没有缓存的 record_id，尝试通过主字段（记忆ID）查找现有记录
    if not record_id:
        logger.info("未找到缓存的 record_id (记忆ID: %s)，通过飞书 API 查找", memory.id)

        # 直接调用飞书 API 查找 record_id（lark-cli 的 record-list 不返回 record_id）
        record_id = await _find_record_id_by_api(
            app_token, table_id, primary_name, memory.id
        )

        if record_id:
            logger.info("✓ 找到匹配记录: memory_id=%s, record_id=%s", memory.id, record_id)
            # 缓存 record_id
            await redis_client.set(
                _record_redis_key(memory.id),
                record_id,
                ttl_seconds=_PERMANENT_TTL,
            )
        else:
            logger.info("✗ 未找到匹配记录，将创建新记录。记忆ID=%s", memory.id)

    # 构建记录值
    from app.utils.datetime import ms_to_strftime

    tags = getattr(memory, "tags", {}) or {}
    entities = tags.get("entities", [])
    tags_str = ", ".join(entities) if isinstance(entities, list) else str(entities)

    status = "active"
    if not getattr(memory, "active", True):
        status = "superseded" if getattr(memory, "superseded_by", None) else "deleted"

    created_str = ms_to_strftime(getattr(memory, "created_at", 0), "%Y-%m-%d %H:%M:%S") if getattr(memory, "created_at", 0) else ""

    # 处理附件字段：取第一个链接或合并多个链接
    attachments = getattr(memory, "attachments", {}) or {}
    urls = attachments.get("urls", []) if isinstance(attachments, dict) else []
    attachment_str = ""
    if urls:
        # 飞书 URL 字段格式：如果有多个链接，用换行符分隔
        attachment_str = "\n".join(urls) if isinstance(urls, list) else str(urls)

    fields = {
        primary_name: memory.id,  # 动态检测的主字段存储记忆ID
        "类型": getattr(memory, "type", "fact"),
        "内容": getattr(memory, "content", "")[:500],  # 截断过长内容
        "版本": getattr(memory, "version", 1),
        "状态": status,
        "创建时间": created_str,
        "标签": tags_str,
        "来源": owner_id,
        "父记忆ID": getattr(memory, "parent_id", "") or "",
        "附件文档": attachment_str

    }

    # # 只有当有附件时才添加"附件"字段
    # if attachment_str:
        # fields["附件"] = attachment_str

    cmd = [
        "base", "+record-upsert",
        "--base-token", app_token,
        "--table-id", table_id,
        "--json", json.dumps(fields, ensure_ascii=False),
        "--as", "bot",
    ]

    if record_id:
        cmd.extend(["--record-id", record_id])

    result = await _lark_cli_with_retry(*cmd)
    if not result:
        logger.warning("upsert_record 失败: memory_id=%s", memory.id)
        return False

    # 缓存 record_id
    new_record_id = result.get("record", {}).get("record_id", "")
    if new_record_id:
        await redis_client.set(
            _record_redis_key(memory.id),
            new_record_id,
            ttl_seconds=_PERMANENT_TTL,
        )

    return True


async def delete_record(memory_id: str, chat_id: str) -> bool:
    """标记记录状态为 deleted（通过 upsert 更新状态字段）"""
    if not settings.feishu_base_enabled:
        return False

    base_config = await _get_base_config(chat_id)
    if not base_config:
        return False

    record_id = await redis_client.get(_record_redis_key(memory_id))
    if not record_id:
        return False

    result = await _lark_cli_with_retry(
        "base", "+record-upsert",
        "--base-token", base_config["app_token"],
        "--table-id", base_config["table_id"],
        "--record-id", record_id,
        "--json", json.dumps({"状态": "deleted"}, ensure_ascii=False),
        "--as", "bot",
    )
    return result is not None


async def get_base_url(chat_id: str) -> str | None:
    """返回多维表格链接"""
    config = await _get_base_config(chat_id)
    return config.get("url") if config else None


async def _get_base_config(chat_id: str) -> dict | None:
    """从 Redis 获取 base 配置"""
    cached = await redis_client.get(_base_redis_key(chat_id))
    if cached:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


async def invalidate_cache(chat_id: str) -> None:
    """清除指定 chat 的 base 配置缓存"""
    await redis_client.delete(_base_redis_key(chat_id))
    logger.info("已清除 base 缓存: chat_id=%s", chat_id)


async def _get_tenant_access_token() -> str | None:
    """获取飞书 API 的 tenant_access_token"""
    app_id = getattr(settings, "feishu_app_id", "")
    app_secret = getattr(settings, "feishu_app_secret", "")
    if not app_id or not app_secret:
        return None

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        data = resp.json()
        if data.get("code") == 0:
            return data.get("tenant_access_token")
        return None


async def _find_record_id_by_api(
    app_token: str,
    table_id: str,
    primary_name: str,
    memory_id: str,
) -> str | None:
    """直接调用飞书 API 查找 record_id

    使用 record-search API 按主字段（记忆ID）过滤查找。
    返回 record_id 或 None。
    """
    token = await _get_tenant_access_token()
    if not token:
        logger.warning("无法获取 tenant_access_token")
        return None

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"

    payload = {
        "filter": {
            "conditions": [
                {
                    "field_name": primary_name,
                    "operator": "is",
                    "value": [memory_id]
                }
            ],
            "conjunction": "and"
        },
        "limit": 10
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10.0,
            )
            result = resp.json()
            logger.info("record-search API 返回: %s", json.dumps(result, ensure_ascii=False)[:500])

            if result.get("code") == 0:
                items = result.get("data", {}).get("items", [])
                for item in items:
                    # 验证主字段值匹配
                    fields = item.get("fields", {})
                    primary_value = fields.get(primary_name)
                    if primary_value and isinstance(primary_value, list) and len(primary_value) > 0:
                        if primary_value[0].get("text") == memory_id:
                            return item.get("record_id")
            return None
    except Exception as e:
        logger.warning("record-search API 调用异常: %s", e)
        return None


async def add_chat_tab(chat_id: str, base_url: str) -> bool:
    """Add Feishu Base as a group chat tab.

    Best-effort operation: logs errors but returns False on failure.
    Base creation continues regardless of chat tab success/failure.

    Args:
        chat_id: Group chat ID (e.g., "oc_xxx")
        base_url: Feishu Base URL

    Returns:
        True if successful, False otherwise
    """
    token = await _get_tenant_access_token()
    if not token:
        logger.warning("无法获取 tenant_access_token，跳过添加会话标签页")
        return False

    url = f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/chat_tabs"
    payload = {
        "chat_tabs": [
            {
                "tab_name": "团队记忆",
                "tab_type": "doc",
                "tab_content": {"doc": base_url}
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
            data = resp.json()
            if data.get("code") == 0:
                logger.info("已添加会话标签页: chat_id=%s url=%s", chat_id, base_url)
                return True
            else:
                logger.warning("添加会话标签页失败: code=%s msg=%s", data.get("code"), data.get("msg"))
                return False
    except Exception as e:
        logger.warning("添加会话标签页异常: %s", e)
        return False
