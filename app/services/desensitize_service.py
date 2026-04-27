"""敏感信息识别与脱敏"""

from app.rules.entity_rules import SENSITIVE_PATTERN, desensitize as _desensitize


def mask(content: str) -> str:
    """脱敏：将密钥/密码替换为 ***"""
    return _desensitize(content)


def contains_sensitive(content: str) -> bool:
    """检测是否包含敏感信息"""
    return bool(SENSITIVE_PATTERN.search(content))
