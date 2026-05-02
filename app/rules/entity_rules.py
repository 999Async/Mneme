"""Entity 提取正则/模式 — PRD 附录 C（已扩展）"""

import re

# 人员名模式：中文 1-4 字 + "同学/总/老师/经理" 等后缀（支持 "张同学"、"王总"）
PERSON_PATTERN = re.compile(r"[\u4e00-\u9fff]{1,4}(同学|总|老师|经理|主管|姐|哥|领导)")

# @提及模式：@张三, @Mike_Smith
MENTION_PATTERN = re.compile(r"@[\u4e00-\u9fffa-zA-Z0-9_]+")

# 英文名模式：Mike, Mike Smith（需排除 MongoDB/MySQL 等技术词）
# 使用负向前瞻/后瞻避免匹配到技术术语内部
ENGLISH_NAME_BLACKLIST = {
    "Mongo", "MongoDB", "MySQL", "Redis", "PostgreSQL", "SQLite",
    "Kafka", "RabbitMQ", "Elasticsearch", "Kubernetes", "Docker",
    "Github", "Gitlab", "Bitbucket", "Jenkins", "Terraform",
    "Ansible", "Nginx", "Apache", "Linux", "MacOS", "Windows",
}
ENGLISH_NAME_PATTERN = re.compile(
    r"(?<![a-zA-Z])([A-Z][a-z]+(?: [A-Z][a-z]+)?)(?![a-zA-Z])"
)

# 昵称模式：老王, 小李
NICKNAME_PATTERN = re.compile(r"[老小][\u4e00-\u9fff]")

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

# 敏感信息（用于脱敏）— 扩展版
SENSITIVE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9]{10,}|"
    r"(?:password|passwd|pwd)\s*[=:]\s*\S+|"
    r"(?:token|secret|key)\s*[=:]\s*[a-zA-Z0-9\-_]{10,}|"
    r"eyJ[A-Za-z0-9_-]{10,}|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"[\w-]+\.(internal|local|staging|test)\b|"
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)


def extract_entities(content: str) -> dict[str, list[str] | dict]:
    """从消息中提取实体（扩展版：支持@提及、英文名、昵称）

    Returns:
        {"person": [...], "project": [...], "date": [...], "config": [...]}
    """
    entities: dict[str, list[str]] = {}

    # 人员名提取（多模式合并）
    persons = set()

    # 1. 中文 + 后缀
    for match in PERSON_PATTERN.finditer(content):
        persons.add(match.group(0))

    # 2. @提及
    for match in MENTION_PATTERN.finditer(content):
        persons.add(match.group(0))

    # 3. 昵称（老王, 小李）
    for match in NICKNAME_PATTERN.finditer(content):
        persons.add(match.group(0))

    # 4. 英文名（排除技术术语）
    for match in ENGLISH_NAME_PATTERN.finditer(content):
        name = match.group(1)
        if name not in ENGLISH_NAME_BLACKLIST and len(name) >= 2:
            persons.add(name)

    if persons:
        entities["person"] = list(persons)

    # 项目名
    projects = list(set(PROJECT_PATTERN.findall(content) or []))
    if projects:
        entities["project"] = projects

    # 日期
    dates = list(set(DATE_PATTERN.findall(content) or []))
    if dates:
        entities["date"] = dates

    # 配置/密钥
    configs = list(set(CONFIG_PATTERN.findall(content) or []))
    if configs:
        entities["config"] = configs

    return entities


def extract_keywords(content: str) -> list[str]:
    """jieba 分词 + 停用词过滤提取关键词"""
    import jieba

    stopwords = {
        "的", "了", "是", "在", "和", "与", "或", "把", "被", "到", "从", "向", "对",
        "这", "那", "我", "你", "他", "她", "它", "我们", "你们", "他们",
        "不", "也", "都", "就", "会", "能", "要", "有", "没", "可以", "需要", "应该",
        "一个", "这个", "那个", "进行", "正在", "已经", "通过", "使用", "为了",
        "而且", "因为", "所以", "但是", "如果", "虽然", "就是", "还是", "或者",
    }
    words = jieba.cut(content)
    keywords = [w for w in words if len(w) >= 2 and w not in stopwords]
    return keywords[:10]


def desensitize(content: str) -> str:
    """敏感信息脱敏（扩展版：JWT/IP/内部域名/Email）"""
    def mask(m):
        val = m.group(0)
        if len(val) <= 8:
            return "***"
        return val[:3] + "***" + val[-3:]
    return SENSITIVE_PATTERN.sub(mask, content)
