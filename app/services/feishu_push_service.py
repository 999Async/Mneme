"""飞书 Open API 卡片推送"""

import json
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class FeishuPushService:
    """直接调飞书 Open API 推送卡片"""

    def __init__(self):
        self.app_id = settings.feishu_app_id
        self.app_secret = settings.feishu_app_secret
        self._tenant_token: str | None = None
        self._token_expires_at: float = 0

    async def _get_tenant_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._tenant_token and time.time() < self._token_expires_at:
            return self._tenant_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()
            self._tenant_token = data["tenant_access_token"]
            expire = data.get("expire", 7200)
            self._token_expires_at = time.time() + expire - 300  # 提前 5 分钟过期
            return self._tenant_token

    async def _send_message(self, chat_id: str, msg_type: str, content: str) -> bool:
        """发送消息，401 时刷新 token 重试"""
        if not self.app_id or not self.app_secret:
            return False

        for attempt in range(2):
            token = await self._get_tenant_token(force_refresh=(attempt > 0))
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"receive_id_type": "chat_id"},
                    json={
                        "receive_id": chat_id,
                        "msg_type": msg_type,
                        "content": content,
                    },
                )
                if resp.status_code == 200:
                    return True
                if resp.status_code == 401 and attempt == 0:
                    logger.warning("tenant_token 过期，刷新重试")
                    continue
                logger.error("飞书发送失败: status=%d body=%s", resp.status_code, resp.text[:200])
                return False
        return False

    async def send_card(self, chat_id: str, card_content: dict) -> bool:
        """发送飞书卡片消息"""
        return await self._send_message(chat_id, "interactive", json.dumps(card_content))

    async def send_text(self, chat_id: str, text: str) -> bool:
        """发送飞书文本消息"""
        return await self._send_message(chat_id, "text", json.dumps({"text": text}))


feishu_push = FeishuPushService()
