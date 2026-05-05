"""意图识别关键词库 — PRD 4.5.3"""

import re
from enum import Enum


class Intent(str, Enum):
    STORE = "store"
    QUERY = "query"
    OVERWRITE = "overwrite"
    FLOW_RECOVER = "flow_recover"
    LIST_MY = "list_my"
    AUTO_EXTRACT = "auto_extract"
    CONFIG_FOLDER = "config_folder"
    PASS = "pass"


# @Mneme 指令匹配规则（优先级从高到低）
INTENT_RULES: list[tuple[list[str], Intent, dict]] = [
    # (匹配关键词, 意图, 额外参数)
    (["大家记住", "大家记一下"], Intent.STORE, {"scope": "group"}),
    (["记住", "记一下", "帮我记", "记下来"], Intent.STORE, {"scope": "personal"}),
    (["使用机器人空间", "机器人空间"], Intent.CONFIG_FOLDER, {"use_bot_space": True}),
    (["配置文件夹", "设置文件夹", "文件夹位置"], Intent.CONFIG_FOLDER, {}),
    (["查询", "搜索", "搜一下", "查一下", "有没有"], Intent.QUERY, {}),
    (["覆盖", "改一下", "更新记忆"], Intent.OVERWRITE, {}),
    (["回到刚才", "恢复刚才", "刚才在做什么"], Intent.FLOW_RECOVER, {}),
    (["我的记忆", "我记住的", "记忆列表", "所有记忆"], Intent.LIST_MY, {}),
    (["删除记忆", "忘掉"], Intent.OVERWRITE, {}),
]


def identify_intent(content: str, is_mentioned: bool) -> tuple[Intent, dict]:
    """识别意图

    Returns:
        (intent, params) — intent 为意图枚举，params 为额外参数
    """
    if not is_mentioned:
        return Intent.AUTO_EXTRACT, {}

    text = content.lower()
    # 移除 @xxx 提及（兼容各种写法：@Mneme / @Meneme / @用户名）
    text = re.sub(r"@\S+\s*", "", text).strip()

    if not text:
        return Intent.PASS, {}

    # 检测 "folder:" 前缀（用户配置文件夹的回复）
    original_text = re.sub(r"@\S+\s*", "", content).strip()
    if original_text.startswith("folder:"):
        folder_token = original_text[7:].strip()
        return Intent.CONFIG_FOLDER, {"folder_token": folder_token}

    for keywords, intent, params in INTENT_RULES:
        for kw in keywords:
            if kw in text:
                return intent, params

    # 默认：有 @Mneme 但无指令 → 当作查询
    return Intent.QUERY, {}
