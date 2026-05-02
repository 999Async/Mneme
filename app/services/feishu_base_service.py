"""飞书多维表格同步 — 通过 lark-cli 管理 Mneme 记忆可视化面板"""

import asyncio
import json
import logging
from typing import Any

from app.cache import redis_client
from app.config import settings

logger = logging.getLogger(__name__)

# Redis key 前缀
_BASE_KEY_PREFIX = "mneme:base"
_RECORD_KEY_PREFIX = "mneme:base:record"
# TTL: 365 days for base config
_PERMANENT_TTL = 365 * 24 * 3600


async def _lark_cli(*args: str) -> dict | None:
    """执行 lark-cli 命令，返回 JSON 结果或 None"""
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


async def _lark_cli_with_retry(*args: str, retries: int = 3) -> dict | None:
    """带指数退避重试的 lark-cli 调用"""
    for attempt in range(retries):
        result = await _lark_cli(*args)
        if result is not None:
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


# ── 字段定义 ────────────────────────────────────────────────────────────

FIELDS: list[dict] = [
    {"type": "text", "name": "记忆ID"},
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
    {
        "type": "number",
        "name": "强度",
        "style": {"type": "progress", "color": "Blue", "precision": 2},
    },
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
    {"type": "datetime", "name": "下次提醒", "style": {"format": "yyyy-MM-dd HH:mm"}},
    {"type": "text", "name": "标签"},
    {"type": "text", "name": "来源"},
]


# ── 核心 API ────────────────────────────────────────────────────────────


async def ensure_base(chat_id: str) -> dict | None:
    """确保多维表格存在。返回 {app_token, table_id, url} 或 None。"""
    if not settings.feishu_base_enabled:
        return None

    # 检查 Redis 缓存
    cached = await redis_client.get(_base_redis_key(chat_id))
    if cached:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, ValueError):
            pass

    # 创建新 Base
    result = await _lark_cli_with_retry(
        "base", "+base-create",
        "--name", "Mneme 记忆管理",
        "--time-zone", settings.timezone,
        "--as", "bot",
    )
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
    # lark-cli 响应: {"data": {"tables": [{"id": "tblxxx", "name": "数据表"}]}}
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

    # 先删除默认字段，再创建自定义字段
    await _delete_default_fields(app_token, table_id)
    await _create_fields(app_token, table_id)

    # 给群聊成员授予编辑权限
    await _grant_edit_access(app_token, chat_id)

    # 存入 Redis
    base_config = {
        "app_token": app_token,
        "table_id": table_id,
        "url": base_url,
    }
    await redis_client.set(
        _base_redis_key(chat_id),
        json.dumps(base_config, ensure_ascii=False),
        ttl_seconds=_PERMANENT_TTL,
    )

    logger.info("多维表格创建成功: app_token=%s table_id=%s url=%s", app_token, table_id, base_url)
    return base_config


async def _grant_edit_access(app_token: str, chat_id: str) -> None:
    """给群聊成员授予多维表格的编辑权限

    通过飞书 Drive 权限 API 添加群聊为协作者。
    API: POST /open-apis/drive/v1/permissions/{token}/members?type=bitable
    """
    import httpx
    from app.services.feishu_push_service import feishu_push

    token = await feishu_push._get_tenant_token()
    try:
        async with httpx.AsyncClient() as client:
            # 添加群聊为协作者（full_access）
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/drive/v1/permissions/{app_token}/members",
                headers={"Authorization": f"Bearer {token}"},
                params={"type": "bitable"},
                json={
                    "member_type": "chatid",
                    "member_id": chat_id,
                    "perm": "full_access",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    logger.info("已授予群聊 %s 编辑权限", chat_id)
                else:
                    logger.warning("授权群聊失败: %s", data.get("msg", ""))
            else:
                logger.warning("授权群聊 API 返回 %d: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("授权群聊异常: %s", e)


async def _delete_default_fields(app_token: str, table_id: str) -> None:
    """删除飞书默认创建的字段（文本、单选、日期、附件等）

    主字段（首列）不可删除，改为重命名为我们的第一个字段"记忆ID"。
    """
    our_names = {f["name"] for f in FIELDS}
    first_field_name = FIELDS[0]["name"] if FIELDS else "记忆ID"

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
        if not field_id:
            continue

        if name not in our_names:
            # 主字段不可删除，改为重命名
            if field == fields[0]:
                await _lark_cli(
                    "base", "+field-update",
                    "--base-token", app_token,
                    "--table-id", table_id,
                    "--field-id", field_id,
                    "--json", json.dumps({"name": first_field_name}, ensure_ascii=False),
                    "--as", "bot",
                )
                logger.debug("已重命名主字段: %s → %s", name, first_field_name)
            else:
                await _lark_cli(
                    "base", "+field-delete",
                    "--base-token", app_token,
                    "--table-id", table_id,
                    "--field-id", field_id,
                    "--yes",
                    "--as", "bot",
                )
                logger.debug("已删除默认字段: %s (%s)", name, field_id)
            await asyncio.sleep(0.2)


async def _create_fields(app_token: str, table_id: str) -> None:
    """逐个创建字段（串行，避免并发冲突）。

    跳过第一个字段（主字段已通过重命名处理）。
    """
    for field_def in FIELDS[1:]:
        result = await _lark_cli(
            "base", "+field-create",
            "--base-token", app_token,
            "--table-id", table_id,
            "--json", json.dumps(field_def, ensure_ascii=False),
            "--as", "bot",
        )
        if result:
            ok_data = result.get("data", result)
            if ok_data.get("created"):
                logger.debug("字段创建成功: %s", field_def["name"])
            else:
                logger.warning("字段创建失败: %s → %s", field_def["name"], result)
        else:
            logger.warning("字段创建失败: %s → None", field_def["name"])
        # 间隔 0.3s 避免并发限制
        await asyncio.sleep(0.3)


async def upsert_record(memory: Any) -> bool:
    """新建/更新多维表格记录。memory 需要有 id, type, content, strength, version, active, created_at, tags 等属性。"""
    if not settings.feishu_base_enabled:
        return False

    # 获取 base 配置
    # 从 memory.source_chat_id 获取 chat_id
    chat_id = getattr(memory, "source_chat_id", "")
    if not chat_id:
        return False

    base_config = await _get_base_config(chat_id)
    if not base_config:
        return False

    app_token = base_config["app_token"]
    table_id = base_config["table_id"]

    # 检查是否有已有 record_id
    record_id = await redis_client.get(_record_redis_key(memory.id))

    # 构建记录值
    from app.utils.datetime import ms_to_strftime

    tags = getattr(memory, "tags", {}) or {}
    keywords = tags.get("keywords", [])
    tags_str = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)

    status = "active"
    if not getattr(memory, "active", True):
        status = "superseded" if getattr(memory, "superseded_by", None) else "deleted"

    created_str = ms_to_strftime(getattr(memory, "created_at", 0), "%Y-%m-%d %H:%M") if getattr(memory, "created_at", 0) else ""

    # 计算下次提醒时间（基于遗忘曲线）
    next_review_str = _estimate_next_review(memory)

    fields = {
        "记忆ID": memory.id,
        "类型": getattr(memory, "type", "fact"),
        "内容": getattr(memory, "content", "")[:500],  # 截断过长内容
        "强度": round(getattr(memory, "strength", 1.0), 2),
        "版本": getattr(memory, "version", 1),
        "状态": status,
        "创建时间": created_str,
        "下次提醒": next_review_str,
        "标签": tags_str,
        "来源": chat_id,
    }

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


def _estimate_next_review(memory: Any) -> str:
    """基于遗忘曲线估算下次提醒时间"""
    from app.utils.datetime import ms_now, ms_to_strftime

    strength = getattr(memory, "strength", 1.0)
    decay_params = getattr(memory, "decay_params", {}) or {}
    rate = decay_params.get("base_decay_rate", 0.5)
    sensitivity = decay_params.get("personal_sensitivity", 1.0)

    # 计算强度降到阈值(0.3)需要多少天
    import math
    threshold = 0.3
    if strength <= threshold:
        return ""  # 已经低于阈值，无需计算

    try:
        days_until_threshold = math.log(strength / threshold) / (rate * sensitivity)
    except (ValueError, ZeroDivisionError):
        return ""

    # 转为毫秒时间戳
    now_ms = ms_now()
    next_review_ms = now_ms + int(days_until_threshold * 86400 * 1000)
    return ms_to_strftime(next_review_ms, "%Y-%m-%d %H:%M")
