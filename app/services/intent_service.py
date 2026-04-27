"""意图识别服务"""

from app.rules.intent_rules import Intent, identify_intent


def identify(content: str, is_mentioned: bool) -> tuple[Intent, dict]:
    """识别用户意图

    Args:
        content: 消息原文
        is_mentioned: 是否 @Mneme

    Returns:
        (intent, params)
    """
    return identify_intent(content, is_mentioned)
