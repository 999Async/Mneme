from pydantic import BaseModel


class IncomingEvent(BaseModel):
    """Plugin before_agent_start POST body"""
    session_key: str | None = None
    content: str
    is_mentioned: bool = False
    message_id: str | None = None  # 飞书消息 ID，用于溯源


class ConversationEndEvent(BaseModel):
    """Plugin agent_end POST body"""
    messages: list[dict] = []  # 每条 dict 可包含 message_id
    session_key: str | None = None


class EventResponse(BaseModel):
    """Python 返回给 Plugin 的格式"""
    reply_text: str | None = None
    relevant_memories: list[dict] = []
    action: str = "none"  # reply / react / push / none
    reaction_emoji: str = "THUMBSUP"  # 飞书内置表情类型（react action 时使用）
    push_card: dict | None = None
