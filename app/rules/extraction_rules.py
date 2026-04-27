"""记忆抽取触发模式 — PRD 4.1.2"""

import re

# 每个规则：(模式列表, 记忆类型, 基础置信度)
EXTRACTION_RULES: list[tuple[list[str | re.Pattern], str, float]] = [
    # 决策声明
    (["决定是", "决定用", "改用", "更新为", "改为", "统一用"], "decision", 0.85),
    # 截止时间
    ([re.compile(r"截止|deadline|需要在.{1,10}前完成|之前完成|之前上线")], "fact", 0.80),
    # 负责人变更
    ([re.compile(r"转给|交接给|交给|分配给")], "command", 0.85),
    # 配置/密钥
    ([re.compile(r"密钥|密码|token|api.?key|配置|secret|凭证")], "fact", 0.80),
    # 外部承诺
    (["我们已经", "向客户承诺", "承诺", "答应"], "fact", 0.75),
    # 竞品/市场
    (["竞品更新了", "对方说", "竞品"], "fact", 0.70),
    # 心流相关
    ([re.compile(r"等下要|回头要|稍后处理|之后要|待会儿|别忘了")], "intent", 0.75),
    # 指令性内容
    ([re.compile(r"以后都?|从现在起|以后每周|以后每次")], "command", 0.80),
]


def match_extraction_rules(content: str) -> list[tuple[str, float]]:
    """匹配抽取规则

    Returns:
        [(type, confidence), ...] 按置信度降序
    """
    matches = []
    for patterns, mem_type, base_conf in EXTRACTION_RULES:
        for pattern in patterns:
            if isinstance(pattern, re.Pattern):
                if pattern.search(content):
                    matches.append((mem_type, base_conf))
                    break
            elif pattern in content:
                matches.append((mem_type, base_conf))
                break
    return sorted(matches, key=lambda x: x[1], reverse=True)
