"""记忆抽取服务：从消息中提取记忆 + entity + tags"""

from app.rules.entity_rules import desensitize, extract_entities, extract_keywords
from app.rules.extraction_rules import match_extraction_rules

# 抽取置信度阈值（PRD BR-001-01）
CONFIDENCE_THRESHOLD = 0.7


class ExtractedMemory:
    """抽取结果"""
    __slots__ = ("content", "type", "tags", "confidence", "context_snapshot")

    def __init__(self, content: str, mem_type: str, tags: dict,
                 confidence: float, context_snapshot: str | None = None):
        self.content = content
        self.type = mem_type
        self.tags = tags
        self.confidence = confidence
        self.context_snapshot = context_snapshot

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "type": self.type,
            "tags": self.tags,
            "confidence": self.confidence,
            "context_snapshot": self.context_snapshot,
        }


def extract(content: str, context_messages: list[str] | None = None) -> ExtractedMemory | None:
    """从消息中抽取记忆

    Returns:
        ExtractedMemory 或 None（置信度低于阈值时不存储）
    """
    matches = match_extraction_rules(content)
    if not matches:
        return None

    mem_type, base_conf = matches[0]

    # 提取 entity + keywords
    entities = extract_entities(content)
    keywords = extract_keywords(content)
    tags = {"keywords": keywords, "entities": entities}

    # 脱敏
    safe_content = desensitize(content)

    # 置信度调整：有实体提取加分
    confidence = base_conf
    if entities:
        confidence = min(confidence + 0.05, 1.0)

    if confidence < CONFIDENCE_THRESHOLD:
        return None

    # context_snapshot: 最近消息摘要
    context_snapshot = None
    if context_messages:
        context_snapshot = "|".join(context_messages[-3:])

    return ExtractedMemory(
        content=safe_content,
        mem_type=mem_type,
        tags=tags,
        confidence=confidence,
        context_snapshot=context_snapshot,
    )


def extract_from_command(content: str, scope: str) -> ExtractedMemory:
    """从显式指令中提取记忆（记住/大家记住）

    显式存储的置信度固定 1.0
    """
    # 移除指令关键词
    clean = content
    for prefix in ["大家记住", "大家记一下", "记住", "记一下", "帮我记", "记下来"]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
            break

    entities = extract_entities(clean)
    keywords = extract_keywords(clean)
    tags = {"keywords": keywords, "entities": entities}
    safe_content = desensitize(clean)

    return ExtractedMemory(
        content=safe_content,
        mem_type="command" if scope == "group" else "fact",
        tags=tags,
        confidence=1.0,
    )
