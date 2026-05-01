"""意图识别服务 — 规则优先 + LLM fallback"""

import logging

from app.rules.intent_rules import Intent, identify_intent

logger = logging.getLogger(__name__)

# 规则匹配命中但置信度低时，用 LLM 二次确认
INTENT_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["store", "query", "overwrite", "flow_recover", "list_my", "auto_extract", "pass"],
        },
        "scope": {"type": "string", "enum": ["personal", "group"]},
        "confidence": {"type": "number"},
    },
    "required": ["intent", "confidence"],
}

INTENT_LLM_PROMPT = """你是一个飞书机器人的意图分类器。根据用户消息判断意图。

用户被 @了（is_mentioned=true）。

可选意图：
- store: 用户想记录/记住某条信息。scope 为 personal（个人）或 group（群组共享）
- query: 用户想查询/搜索之前记住的信息
- overwrite: 用户想覆盖/更新某条已有记忆
- flow_recover: 用户想恢复之前的对话上下文
- list_my: 用户想查看自己的记忆列表
- auto_extract: 非指令的普通对话消息（is_mentioned=false 时）
- pass: 空消息或无法理解

消息内容：{content}
是否被 @：{is_mentioned}

请判断意图并返回 JSON。"""


def identify(content: str, is_mentioned: bool) -> tuple[Intent, dict]:
    """识别用户意图（同步，规则优先）

    Args:
        content: 消息原文
        is_mentioned: 是否 @Mneme

    Returns:
        (intent, params)
    """
    return identify_intent(content, is_mentioned)


async def identify_with_llm_fallback(content: str, is_mentioned: bool) -> tuple[Intent, dict]:
    """识别意图：规则优先，低置信度时 LLM fallback

    规则匹配返回结果视为高置信度，直接使用。
    未 @ 时直接返回 AUTO_EXTRACT（无需 LLM）。
    @了但规则走到默认 QUERY 时，视为低置信度，调用 LLM。
    """
    # 规则匹配
    intent, params = identify_intent(content, is_mentioned)

    # 非 @ 消息 → 直接缓冲
    if not is_mentioned:
        return intent, params

    # 规则匹配到具体指令（非默认 QUERY）→ 高置信度
    # 默认 QUERY 意味着规则没有匹配到任何关键词，可能是模糊指令
    # 对于明确的默认查询，也保留规则结果（用户 @了但没说具体指令 → 查询是合理的）

    # 只在内容看起来像存储/覆写但规则没匹配时才调 LLM
    _store_hints = ["记住", "记录", "记下", "别忘", "帮忙记", "存一下"]
    _overwrite_hints = ["更新", "修改", "改一下", "换成", "改成", "覆盖"]
    _recover_hints = ["刚才", "之前说的", "上次", "恢复"]

    text = content.lower()
    needs_llm = False
    if intent == Intent.QUERY:  # 规则走到了默认
        for hint in _store_hints + _overwrite_hints + _recover_hints:
            if hint in text:
                needs_llm = True
                break

    if not needs_llm:
        return intent, params

    # LLM fallback
    try:
        from app.llm.client import chat_json
        prompt = INTENT_LLM_PROMPT.format(content=content, is_mentioned=is_mentioned)
        result = await chat_json(
            messages=[{"role": "user", "content": prompt}],
            schema=INTENT_LLM_SCHEMA,
            temperature=0.1,
            max_tokens=200,
        )
        if result["ok"]:
            data = result["data"]
            llm_intent_str = data.get("intent", "query")
            try:
                llm_intent = Intent(llm_intent_str)
                llm_scope = data.get("scope", "personal")
                logger.info("LLM intent override: %s → %s (scope=%s)", intent.value, llm_intent.value, llm_scope)
                return llm_intent, {"scope": llm_scope}
            except ValueError:
                pass
    except Exception as e:
        logger.warning("LLM intent fallback failed: %s", e)

    return intent, params
