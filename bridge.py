"""Mneme × 飞书 Bridge — 通过 lark-cli 接收事件并转发到 Mneme API

用法:
    # 正常模式
    python bridge.py

    # 测试模式（额外轮询机器人消息）
    MNEME_TEST_MODE=true MNEME_TEST_CHAT_IDS=oc_xxx,oc_yyy python bridge.py

环境变量:
    MNEME_URL                   Mneme 服务地址 (默认 http://localhost:3001)
    MNEME_SERVICE_TOKEN         API 认证 token
    MNEME_TEST_MODE             启用测试模式 (true/false)
    MNEME_TEST_CHAT_IDS         测试模式轮询的群聊ID (逗号分隔)
    MNEME_TEST_POLL_INTERVAL    轮询间隔秒数 (默认 10)
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

# ── 配置 ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")

MNEME_URL = os.environ.get("MNEME_URL", "http://localhost:3001")
MNEME_TOKEN = os.environ.get("MNEME_SERVICE_TOKEN", "")

# Cron 间隔（秒）
CRON_DECAY_INTERVAL = int(os.environ.get("CRON_DECAY_INTERVAL", "1800"))      # 30 min
CRON_EXTRACT_INTERVAL = int(os.environ.get("CRON_EXTRACT_INTERVAL", "300"))   # 5 min

# 测试模式
TEST_MODE = os.environ.get("MNEME_TEST_MODE", "false").lower() == "true"
TEST_CHAT_IDS = [c.strip() for c in os.environ.get("MNEME_TEST_CHAT_IDS", "").split(",") if c.strip()]
TEST_POLL_INTERVAL = int(os.environ.get("MNEME_TEST_POLL_INTERVAL", "10"))
# Mneme 自己的 app_id，用于过滤自己的回复避免循环（从 lark-cli config 读取）
_MNEME_APP_ID = ""


# ── 飞书操作 ──────────────────────────────────────────────────────────
async def send_reply(chat_id: str, text: str) -> bool:
    """通过 lark-cli 发送文本回复（独立消息，无引用，回退用）"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "im", "+messages-send",
            "--chat-id", chat_id,
            "--text", text,
            "--as", "bot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            log.error("lark-cli 回复失败 (rc=%d): %s", proc.returncode, stderr.decode().strip())
            return False
        return True
    except Exception as e:
        log.error("lark-cli 回复异常: %s", e)
        return False


async def send_reply_message(message_id: str, text: str) -> bool:
    """通过飞书回复 API 以引用形式回复文本消息"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "api", "POST",
            f"/open-apis/im/v1/messages/{message_id}/reply",
            "--data", json.dumps({
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            }),
            "--as", "bot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            log.error("飞书回复 API 失败 (rc=%d): %s", proc.returncode, stderr.decode().strip())
            return False
        return True
    except Exception as e:
        log.error("飞书回复 API 异常: %s", e)
        return False


async def send_card_reply(message_id: str, card_json: dict) -> bool:
    """通过飞书回复 API 以引用形式回复卡片消息"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "api", "POST",
            f"/open-apis/im/v1/messages/{message_id}/reply",
            "--data", json.dumps({
                "msg_type": "interactive",
                "content": json.dumps(card_json),
            }),
            "--as", "bot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            log.error("飞书卡片回复 API 失败 (rc=%d): %s", proc.returncode, stderr.decode().strip())
            return False
        return True
    except Exception as e:
        log.error("飞书卡片回复 API 异常: %s", e)
        return False


async def send_card(chat_id: str, card_json: dict) -> bool:
    """通过 lark-cli 发送交互卡片（独立消息，主动推送用）"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "api", "POST",
            "/open-apis/im/v1/messages",
            "--params", json.dumps({"receive_id_type": "chat_id"}),
            "--data", json.dumps({
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card_json),
            }),
            "--as", "bot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            log.error("lark-cli 卡片发送失败 (rc=%d): %s", proc.returncode, stderr.decode().strip())
            return False
        return True
    except Exception as e:
        log.error("lark-cli 卡片发送异常: %s", e)
        return False


async def add_reaction(message_id: str, emoji: str = "DONE") -> bool:
    """对消息添加飞书表情回复

    API: POST /open-apis/im/v1/messages/:message_id/reactions
    请求体: {"reaction_type": {"emoji_type": "DONE"}}
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "api", "POST",
            f"/open-apis/im/v1/messages/{message_id}/reactions",
            "--data", json.dumps({"reaction_type": {"emoji_type": emoji}}),
            "--as", "bot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            err = stderr.decode().strip()[:200]
            log.error("添加表情失败 (rc=%d): %s", proc.returncode, err)
            return False
        return True
    except Exception as e:
        log.error("添加表情异常: %s", e)
        return False


# ── 卡片构建 ──────────────────────────────────────────────────────────
def build_reply_card(title: str, body_text: str, template: str = "blue") -> dict:
    """构建飞书卡片 JSON 2.0"""
    return {
        "schema": "2.0",
        "config": {"width_mode": "fill"},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": body_text,
                },
            ],
        },
    }


# ── Mneme API 客户端 ──────────────────────────────────────────────────
async def forward_to_mneme(client: httpx.AsyncClient, session_key: str, content: str, is_mentioned: bool, message_id: str = "") -> dict | None:
    """POST 到 Mneme /api/events/message"""
    payload = {
        "session_key": session_key,
        "content": content,
        "is_mentioned": is_mentioned,
    }
    if message_id:
        payload["message_id"] = message_id
    try:
        resp = await client.post(
            f"{MNEME_URL}/api/events/message",
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        log.error("Mneme 返回 %d: %s", e.response.status_code, e.response.text[:200])
        return None
    except httpx.HTTPError as e:
        log.error("Mneme 请求失败: %s", type(e).__name__)
        return None


# ── 事件解析 ──────────────────────────────────────────────────────────
def parse_event(line: str) -> dict | None:
    """解析 lark-cli compact NDJSON 行"""
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if event.get("type") != "im.message.receive_v1":
        return None
    return event


def build_session_key(event: dict) -> str:
    """构建 Mneme session_key: feishu:{chat_type}:{chat_id}"""
    chat_type = event.get("chat_type", "group")
    prefix = "p2p" if chat_type == "p2p" else "chat"
    return f"feishu:{prefix}:{event['chat_id']}"


def extract_content(event: dict) -> str:
    """提取消息文本内容"""
    content = event.get("content", "")
    if not content:
        return ""
    if content.startswith("{"):
        try:
            parsed = json.loads(content)
            # 文本消息: {"text": "..."}
            if "text" in parsed:
                return parsed["text"]
            # 富文本消息 (lark-cli compact): {"title":"","content":[[{"tag":"text","text":"xxx"}]]}
            if "content" in parsed and isinstance(parsed["content"], list):
                texts = []
                for para in parsed["content"]:
                    if isinstance(para, list):
                        for seg in para:
                            if isinstance(seg, dict) and seg.get("text"):
                                texts.append(seg["text"])
                return "".join(texts).strip()
            return content
        except (json.JSONDecodeError, ValueError):
            pass
    return content


def detect_mention(content: str, chat_type: str, event: dict) -> bool:
    """检测是否 @了机器人"""
    if chat_type == "p2p":
        return True
    mentions = event.get("mentions") or []
    if mentions:
        return True
    lower = content.lower()
    for pattern in ("@mneme", "@meneme", "@记忆", "@助手"):
        if pattern in lower:
            return True
    return False


async def process_event(client: httpx.AsyncClient, event: dict):
    """处理单个事件（WebSocket 或轮询）"""
    content = extract_content(event)
    if not content:
        log.debug("跳过空消息 id=%s", event.get("message_id", "?"))
        return

    session_key = build_session_key(event)
    chat_type = event.get("chat_type", "group")
    is_mentioned = detect_mention(content, chat_type, event)
    chat_id = event["chat_id"]
    message_id = event.get("message_id", "")

    log.info("收到消息 [%s] %s (mention=%s): %s", chat_type, chat_id, is_mentioned, content[:80])

    result = await forward_to_mneme(client, session_key, content, is_mentioned, message_id)
    if not result or not result.get("ok"):
        log.warning("Mneme 返回异常: %s", result)
        return

    data = result.get("data", {})
    action = data.get("action")
    log.info("Mneme 响应: action=%s", action)

    if action == "react":
        # Emoji 反应（STORE 成功）
        emoji = data.get("reaction_emoji", "DONE")
        if message_id:
            log.info("表情反应 [%s]: %s", chat_id, emoji)
            await add_reaction(message_id, emoji)

    elif action == "reply":
        reply_text = data.get("reply_text", "")
        if not reply_text:
            return
        card = build_reply_card("Mneme", reply_text, "blue")
        if message_id:
            # 有 message_id 时用飞书回复 API（引用形式）
            log.info("引用卡片回复 [%s] → msg %s", chat_id, message_id)
            ok = await send_card_reply(message_id, card)
            if not ok:
                # 回复 API 失败，回退到独立消息
                log.warning("回复 API 失败，回退独立卡片 [%s]", chat_id)
                await send_card(chat_id, card)
        else:
            # 无 message_id（如测试模式），用独立卡片
            log.info("独立卡片回复 [%s]", chat_id)
            await send_card(chat_id, card)

    elif action == "push":
        log.info("推送 [%s]", chat_id)


async def process_event_line(client: httpx.AsyncClient, line: str):
    """处理 lark-cli NDJSON 行（WebSocket 模式）"""
    event = parse_event(line)
    if event:
        await process_event(client, event)


# ── Cron 定时任务 ─────────────────────────────────────────────────────
async def cron_loop(client: httpx.AsyncClient):
    """定时触发 extract-buffers（每 5 分钟）"""
    while True:
        await asyncio.sleep(CRON_EXTRACT_INTERVAL)
        try:
            resp = await client.post(f"{MNEME_URL}/api/cron/extract-buffers", timeout=60)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if data.get("extracted", 0) > 0:
                    log.info("cron extract-buffers: extracted=%d", data["extracted"])
        except Exception as e:
            log.error("cron extract-buffers 失败: %s", e)


async def cron_decay_loop(client: httpx.AsyncClient):
    """独立的 decay + push-l1 定时器（每 30 分钟）"""
    while True:
        await asyncio.sleep(CRON_DECAY_INTERVAL)
        try:
            resp = await client.post(f"{MNEME_URL}/api/cron/decay", timeout=60)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                log.info("cron decay: scanned=%d decayed=%d deactivated=%d",
                         data.get("scanned", 0), data.get("decayed", 0), data.get("deactivated", 0))
        except Exception as e:
            log.error("cron decay 失败: %s", e)

        try:
            resp = await client.post(f"{MNEME_URL}/api/cron/push-l1", timeout=60)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if data.get("pushed", 0) > 0:
                    log.info("cron push-l1: pushed=%d", data["pushed"])
        except Exception as e:
            log.error("cron push-l1 失败: %s", e)


# ── 测试模式：消息轮询 ────────────────────────────────────────────────
_seen_message_ids: set[str] = set()


async def poll_chat_messages(client: httpx.AsyncClient, chat_id: str):
    """轮询单个群聊的消息历史（测试模式，可接收机器人消息）"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "api", "GET",
            "/open-apis/im/v1/messages",
            "--params", json.dumps({
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": 20,
                "sort_type": "ByCreateTimeDesc",
            }),
            "--as", "bot", "--format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            log.error("轮询消息失败 [%s]: %s", chat_id, stderr.decode().strip()[:200])
            return

        data = json.loads(stdout.decode())
        items = data.get("data", {}).get("items", [])
        if not items:
            return

        # 按时间正序处理（旧→新）
        new_events = []
        for msg in reversed(items):
            msg_id = msg.get("message_id", "")
            if msg_id in _seen_message_ids:
                continue
            _seen_message_ids.add(msg_id)

            # 跳过 Mneme 自己发的消息（避免循环）和被撤回的消息
            sender = msg.get("sender", {})
            sender_id = sender.get("id", "")
            if sender.get("sender_type") == "app" and _MNEME_APP_ID and sender_id == _MNEME_APP_ID:
                continue
            if msg.get("deleted", False):
                continue

            # 组装为与 lark-cli compact 格式兼容的事件
            sender_id = sender.get("id", "")
            # 消息内容：body.content 是飞书原始 JSON
            raw_content = msg.get("body", {}).get("content", "")

            event = {
                "type": "im.message.receive_v1",
                "message_id": msg_id,
                "chat_id": msg.get("chat_id", chat_id),
                "chat_type": msg.get("chat_type", "group"),
                "content": raw_content,
                "sender_id": sender_id,
                "sender_type": sender.get("sender_type", "user"),
            }
            new_events.append(event)

        for event in new_events:
            await process_event(client, event)

    except Exception as e:
        log.error("轮询异常 [%s]: %s", chat_id, e)


async def poll_loop(client: httpx.AsyncClient):
    """测试模式轮询循环"""
    if not TEST_CHAT_IDS:
        log.error("测试模式未设置 MNEME_TEST_CHAT_IDS")
        return

    log.info("测试模式: 轮询群聊 %s (间隔 %ds)", TEST_CHAT_IDS, TEST_POLL_INTERVAL)
    while True:
        for chat_id in TEST_CHAT_IDS:
            await poll_chat_messages(client, chat_id)
        await asyncio.sleep(TEST_POLL_INTERVAL)


# ── 事件监听 ──────────────────────────────────────────────────────────
async def event_bridge(client: httpx.AsyncClient):
    """监听 lark-cli 事件流并转发"""
    backoff = 1
    max_backoff = 60

    while True:
        try:
            log.info("启动 lark-cli 事件订阅...")
            proc = await asyncio.create_subprocess_exec(
                "lark-cli", "event", "+subscribe",
                "--event-types", "im.message.receive_v1",
                "--compact", "--quiet",
                "--as", "bot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async for raw_line in proc.stdout:
                line = raw_line.decode().strip()
                if line:
                    await process_event_line(client, line)

            returncode = await proc.wait()
            log.warning("lark-cli 进程退出 (rc=%d)", returncode)

            stderr = await proc.stderr.read()
            if stderr:
                log.warning("lark-cli stderr: %s", stderr.decode().strip()[:200])

        except Exception as e:
            log.error("事件监听异常: %s", e)

        log.info("重连等待 %ds...", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


# ── 入口 ──────────────────────────────────────────────────────────────
async def main():
    if not MNEME_TOKEN:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key == "MNEME_SERVICE_TOKEN" and value:
                        os.environ["MNEME_SERVICE_TOKEN"] = value
                        break

    token = os.environ.get("MNEME_SERVICE_TOKEN", "")
    if not token:
        log.warning("MNEME_SERVICE_TOKEN 未设置，Mneme API 认证可能失败")

    # 读取 Mneme 自己的 app_id（用于测试模式过滤自己的消息）
    global _MNEME_APP_ID
    try:
        proc = await asyncio.create_subprocess_exec(
            "lark-cli", "auth", "status", "--format", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            auth_data = json.loads(stdout.decode())
            _MNEME_APP_ID = auth_data.get("appId", "")
            log.info("Mneme app_id: %s", _MNEME_APP_ID)
    except Exception:
        pass

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    ) as client:
        try:
            resp = await client.get(f"{MNEME_URL}/health", timeout=5)
            if resp.status_code == 200:
                log.info("Mneme 服务就绪 (%s)", MNEME_URL)
            else:
                log.warning("Mneme 返回 %d，继续尝试...", resp.status_code)
        except Exception as e:
            log.error("Mneme 不可达 (%s): %s", MNEME_URL, e)
            log.error("请先启动 Mneme: docker compose up -d")
            sys.exit(1)

        tasks = [
            event_bridge(client),
            cron_loop(client),
            cron_decay_loop(client),
        ]
        if TEST_MODE:
            tasks.append(poll_loop(client))

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bridge 已停止")
