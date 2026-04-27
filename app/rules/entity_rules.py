"""Entity 提取正则/模式 — PRD 附录 C"""

import re

# 人员名模式：中文 2-4 字 + "同学/总/老师/经理" 等后缀
PERSON_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,4}(同学|总|老师|经理|主管|姐|哥|领导)")

# 项目名模式：含 "项目" 或大写字母+数字组合
PROJECT_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,8}项目|[A-Z][A-Z0-9\-]{1,20})")

# 日期表达
DATE_PATTERN = re.compile(
    r"(今天|明天|后天|大后天|"
    r"下?(?:周一|周二|周三|周四|周五|周六|周日|星期[一二三四五六日天])|"
    r"\d{1,2}月\d{1,2}[日号]|"
    r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
    r"下?(?:周一|周二|周三|周四|周五|周六|周日)之前?|"
    r"下个?月|这?周末)"
)

# 配置/密钥类
CONFIG_PATTERN = re.compile(r"(sk-[a-zA-Z0-9]{10,}|api[_-]?key|token|secret|password|密码|密钥)", re.IGNORECASE)

# 敏感信息（用于脱敏）
SENSITIVE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9]{10,}|"
    r"(?:password|passwd|pwd)\s*[=:]\s*\S+|"
    r"(?:token|secret|key)\s*[=:]\s*[a-zA-Z0-9\-_]{10,})",
    re.IGNORECASE,
)


def extract_entities(content: str) -> dict[str, list[str] | dict]:
    """从消息中提取实体

    Returns:
        {"person": [...], "project": [...], "date": [...], "config": [...]}
    """
    entities: dict[str, list[str] | dict] = {}

    persons = list(set(PERSON_PATTERN.findall(content) or []))
    if persons:
        entities["person"] = persons

    projects = list(set(PROJECT_PATTERN.findall(content) or []))
    if projects:
        entities["project"] = projects

    dates = list(set(DATE_PATTERN.findall(content) or []))
    if dates:
        entities["date"] = dates

    configs = list(set(CONFIG_PATTERN.findall(content) or []))
    if configs:
        entities["config"] = configs

    return entities


def extract_keywords(content: str) -> list[str]:
    """简单关键词提取（基于停用词过滤）"""
    # 简单实现：移除常见虚词后按长度切分
    stopwords = {"的", "了", "是", "在", "和", "与", "或", "把", "被", "到", "从", "向", "对", "这", "那", "我", "你", "他", "她", "它", "我们", "你们", "他们", "不", "也", "都", "就", "会", "能", "要", "有", "没", "可以", "需要", "应该", "一个", "这个", "那个"}
    # 简单分词：按标点和空格分割
    words = re.split(r"[，。！？、；：""''（）\[\]{}【】\s,\.!?;:\"]+", content)
    keywords = [w for w in words if len(w) >= 2 and w not in stopwords]
    return keywords[:10]


def desensitize(content: str) -> str:
    """敏感信息脱敏"""
    def mask(m):
        val = m.group(0)
        if len(val) <= 8:
            return "***"
        return val[:3] + "***" + val[-3:]
    return SENSITIVE_PATTERN.sub(mask, content)
