"""飞书 Open API 卡片推送"""

import json

import httpx

from app.config import settings


class FeishuPushService:
    """直接调飞书 Open API 推送卡片"""

    def __init__(self):
        self.app_id = settings.feishu_app_id
        self.app_secret = settings.feishu_app_secret
        self._tenant_token: str | None = None

    async def _get_tenant_token(self) -> str:
        if self._tenant_token:
            return self._tenant_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()
            self._tenant_token = data["tenant_access_token"]
            return self._tenant_token

    async def send_card(self, chat_id: str, card_content: dict) -> bool:
        """发送飞书卡片消息"""
        if not self.app_id or not self.app_secret:
            return False  # Demo 阶段凭证未配置时静默跳过

        token = await self._get_tenant_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"receive_id_type": "chat_id"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card_content),
                },
            )
            return resp.status_code == 200

    async def send_text(self, chat_id: str, text: str) -> bool:
        """发送飞书文本消息"""
        if not self.app_id or not self.app_secret:
            return False

        token = await self._get_tenant_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"receive_id_type": "chat_id"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
            )
            return resp.status_code == 200


feishu_push = FeishuPushService()
