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
PROJECT_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,6}项目)")

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


def extract_entities(content: str) -> dict[str, list[str]]:
    """jieba 词性标注 + 正则补齐 提取实体

    主力：jieba.posseg（nr=人名, ns=地名, nt=机构, eng=英文, t=时间词）
    补齐：PERSON_PATTERN（中文+后缀如"张三同学"）、PROJECT_PATTERN、@提及、昵称
    过滤：英文 blacklist + stopwords、时间误匹配、类型冲突去重
    """
    import jieba.posseg as pseg

    persons, locations, orgs, techs, dates = set(), set(), set(), set(), set()

    # 英文 stopwords：非技术实体的常见词
    ENG_STOPWORDS = {
        # URL 片段
        "http", "https", "com", "org", "net", "www", "api", "io", "dev",
        # 英文虚词
        "the", "is", "at", "of", "in", "on", "to", "for", "and", "or",
        "not", "with", "by", "from", "as", "this", "that", "it",
        "can", "use", "set", "get", "run", "all", "new", "key",
        "true", "false", "null", "type", "name", "id", "no", "yes",
        # 非技术英文词
        "example", "test", "local", "admin", "user", "data", "time",
        "env", "db", "sql", "url", "app",
    }

    # jieba 词性标注
    for word, flag in pseg.cut(content):
        if flag == "nr" and len(word) >= 2:
            persons.add(word)
        elif flag == "ns":
            locations.add(word)
        elif flag == "nt":
            orgs.add(word)
        elif flag == "eng" and len(word) >= 2:
            lower = word.lower()
            # blacklist 过滤（技术术语不是实体）
            if lower in ENGLISH_NAME_BLACKLIST:
                continue
            # stopwords 过滤
            if lower in ENG_STOPWORDS:
                continue
            techs.add(word)
        elif flag == "t" and word not in ("正在",):
            dates.add(word)

    # 正则补齐：中文+后缀人名（"张三同学"、"王总"）
    for match in PERSON_PATTERN.finditer(content):
        persons.add(match.group(0))

    # @提及（jieba 不识别 @ 模式）
    for match in MENTION_PATTERN.finditer(content):
        persons.add(match.group(0))

    # 昵称（老王, 小李）
    for match in NICKNAME_PATTERN.finditer(content):
        persons.add(match.group(0))

    # 项目名正则补齐（只取中文+项目 的模式，英文项目名靠 jieba eng 识别）
    for match in PROJECT_PATTERN.finditer(content):
        proj = match.group(1)
        # 只保留中文项目名（如"XX项目"），跳过英文子串误匹配
        if proj and any('\u4e00' <= c <= '\u9fff' for c in proj):
            techs.add(proj)

    # 日期正则补齐（jieba 可能漏掉组合日期如"下周五"）
    for d in DATE_PATTERN.findall(content):
        dates.add(d)

    # 类型冲突去重：同一段文字不应同时出现在多个类别
    # 优先级：person > date > tech > org > location
    _used = set()
    entities: dict[str, list[str]] = {}

    for word_list, key in [
        (persons, "person"),
        (dates, "date"),
        (techs, "tech"),
        (orgs, "org"),
        (locations, "location"),
    ]:
        unique = []
        for w in word_list:
            if w not in _used:
                unique.append(w)
                _used.add(w)
        if unique:
            entities[key] = unique

    # 配置/密钥（安全敏感，保持正则）
    configs = list(set(CONFIG_PATTERN.findall(content)))
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
