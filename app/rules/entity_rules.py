"""Entity 提取正则/模式 — PRD 附录 C（已扩展）"""
import re

# =========================
# 人名相关
# =========================

# 中文姓名 + 职称（限制更严格，避免误匹配普通词）
PERSON_PATTERN = re.compile(
    r"(?<![\u4e00-\u9fff])"              # 左边不是汉字
    r"([\u4e00-\u9fff]{1,3}"            # 姓名（1-3字更合理）
    r"(?:同学|总|老师|经理|主管|姐|哥|领导))"
    r"(?![\u4e00-\u9fff])"              # 右边不是汉字
)

# @提及（增加边界，避免拼接污染）
MENTION_PATTERN = re.compile(
    r"(?<!\w)@([\u4e00-\u9fffa-zA-Z0-9_]{1,30})"
)

# 昵称（避免匹配“老板”等词）
NICKNAME_PATTERN = re.compile(
    r"(?<![\u4e00-\u9fff])([老小][\u4e00-\u9fff])"
)

# =========================
# 英文人名
# =========================

ENGLISH_NAME_BLACKLIST = {
    "Mongo", "MongoDB", "MySQL", "Redis", "PostgreSQL", "SQLite",
    "Kafka", "RabbitMQ", "Elasticsearch", "Kubernetes", "Docker",
    "Github", "Gitlab", "Bitbucket", "Jenkins", "Terraform",
    "Ansible", "Nginx", "Apache", "Linux", "MacOS", "Windows",
    "HTTP", "HTTPS", "API", "CPU", "GPU", "SQL"
}

# 常见英文词（防误判为人名）
ENGLISH_COMMON_WORDS = {
    "Error", "Warning", "Info", "Request", "Response",
    "System", "Manager", "Service", "Config", "Data"
}

ENGLISH_NAME_PATTERN = re.compile(
    r"(?<![A-Za-z])"
    r"([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?)"
    r"(?![A-Za-z])"
)

# =========================
# 项目名
# =========================

PROJECT_PATTERN = re.compile(
    r"(?<![\u4e00-\u9fff])"
    r"([\u4e00-\u9fff]{2,6}项目)"
)

# =========================
# 事件名
# =========================

EVENT_TRIGGER = re.compile(
    r"(?:开|参加|组织|安排)([\u4e00-\u9fff]{2,10})"
)

# =========================
# 日期（修复之前? bug + 去重）
# =========================

DATE_PATTERN = re.compile(
    r"(今天|明天|后天|大后天|"
    r"(?:下周|本周)?(?:一|二|三|四|五|六|日|天)|"
    r"(?:星期|周)[一二三四五六日天]|"
    r"\d{1,2}月\d{1,2}[日号]|"
    r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
    r"(?:下周|本周)?(?:一|二|三|四|五|六|日|天)(?:之前)?|"
    r"下个?月|这?周末)"
)

# =========================
# 配置 / 密钥
# =========================

CONFIG_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9]{16,}|"
    r"api[_-]?key|token|secret|password|passwd|pwd|密码|密钥)",
    re.IGNORECASE,
)

# =========================
# 敏感信息（更严格）
# =========================

SENSITIVE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9]{16,}|"
    r"(?:password|passwd|pwd)\s*[=:]\s*\S+|"
    r"(?:token|secret|key)\s*[=:]\s*[a-zA-Z0-9\-_]{16,}|"
    r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"  # 完整 JWT
    r"(?:\b\d{1,3}(?:\.\d{1,3}){3}\b)|"
    r"[\w-]+\.(?:internal|local|staging|test)\b|"
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)


def extract_entities(content: str) -> dict[str, list[str]]:
    """jieba 词性标注 + 正则补齐 提取实体（优化版）"""
    import jieba.posseg as pseg

    persons, locations, orgs, techs, events, dates = set(), set(), set(), set(), set(), set()

    ENG_STOPWORDS = {
        "http", "https", "com", "org", "net", "www", "api", "io", "dev",
        "the", "is", "at", "of", "in", "on", "to", "for", "and", "or",
        "not", "with", "by", "from", "as", "this", "that", "it",
        "can", "use", "set", "get", "run", "all", "new", "key",
        "true", "false", "null", "type", "name", "id", "no", "yes",
        "example", "test", "local", "admin", "user", "data", "time",
        "env", "db", "sql", "url", "app",
    }

    # ========= 1. jieba 主干 =========
    for word, flag in pseg.cut(content):
        w = word.strip()
        if not w:
            continue

        if flag == "nr" and 2 <= len(w) <= 4:
            persons.add(w)

        elif flag == "ns":
            locations.add(w)

        elif flag == "nt":
            orgs.add(w)

        elif flag == "eng" and len(w) >= 2:
            lower = w.lower()

            # blacklist（注意统一大小写）
            if lower in {x.lower() for x in ENGLISH_NAME_BLACKLIST}:
                continue

            if lower in ENG_STOPWORDS:
                continue

            # 过滤纯数字/版本号
            if w.replace(".", "").isdigit():
                continue

            techs.add(w)

        elif flag == "t" and w not in {"正在"}:
            dates.add(w)

    # ========= 2. 正则补齐 =========

    # 中文+后缀人名
    for m in PERSON_PATTERN.finditer(content):
        persons.add(m.group(1))  # ⚠️ 用 group(1)，不是 group(0)

    # @提及（只保留用户名）
    for m in MENTION_PATTERN.finditer(content):
        persons.add(m.group(1))

    # 昵称
    for m in NICKNAME_PATTERN.finditer(content):
        persons.add(m.group(1))

    # 项目名
    for m in PROJECT_PATTERN.finditer(content):
        proj = m.group(1)
        if proj:
            techs.add(proj)

    # 事件名
    for m in EVENT_TRIGGER.finditer(content):
        event = m.group(1)
        if event:
            events.add(event)

    # 日期补齐
    for d in DATE_PATTERN.findall(content):
        if isinstance(d, tuple):
            d = next(filter(None, d), "")
        if d:
            dates.add(d)

    # ========= 3. 后处理（关键优化） =========

    # （1）长度过滤（避免单字污染）
    persons = {p for p in persons if len(p) >= 2}
    techs = {t for t in techs if len(t) >= 2}

    # （2）子串去重（保留更长的）
    def dedup_by_substring(items: set[str]) -> list[str]:
        items = sorted(items, key=len, reverse=True)
        result = []
        for w in items:
            if not any(w in x for x in result):
                result.append(w)
        return result

    persons = dedup_by_substring(persons)
    techs = dedup_by_substring(techs)
    orgs = dedup_by_substring(orgs)
    locations = dedup_by_substring(locations)
    dates = dedup_by_substring(dates)
    events = dedup_by_substring(events)

    # （3）类型冲突去重（改进策略）
    # 原策略问题：顺序依赖 + 不稳定
    entities: dict[str, list[str]] = {}

    # 优先级：更具体优先（长度 + 类型）
    def assign(key, items):
        if items:
            entities[key] = items

    assign("person", persons)
    assign("date", dates)
    assign("event", events)

    # tech 去掉已被识别为 person 的
    techs = [t for t in techs if t not in persons]
    assign("tech", techs)

    # org 去掉 tech/person
    orgs = [o for o in orgs if o not in techs and o not in persons]
    assign("org", orgs)

    # location 最后
    locations = [l for l in locations if l not in orgs and l not in techs]
    assign("location", locations)

    # ========= 4. 配置/敏感 =========
    configs = list(set(CONFIG_PATTERN.findall(content)))
    if configs:
        entities["config"] = configs

    return entities


def extract_keywords(content: str) -> list[str]:
    """jieba 分词 + 停用词过滤提取关键词"""
    import jieba

    stopwords = {
        # 基础虚词
        "的", "了", "是", "在", "和", "与", "或", "把", "被", "到", "从", "向", "对", "要",
        "这", "那", "这些", "那些", "这里", "那里", "这样", "那样",
        "我", "你", "他", "她", "它", "我们", "你们", "他们",
        # 语气/情态
        "不", "也", "都", "就", "会", "能", "要", "有", "没", "可以", "需要", "应该",
        # 常见结构词
        "一个", "这个", "那个", "进行", "正在", "已经", "通过", "使用", "为了",
        # 连接词
        "而且", "因为", "所以", "但是", "如果", "虽然", "就是", "还是", "或者",
        # 泛化名词（低信息量）
        "问题", "情况", "事情", "东西", "内容", "方面", "结果", "原因", "方式",
        # 常见动词（低区分度）
        "处理", "完成", "实现", "支持", "提供", "增加", "减少", "修改", "优化",
        # 技术泛词（按需保留/删除）
        "系统", "模块", "功能", "服务", "接口", "代码", "数据", "逻辑", "配置",
    }
    words = jieba.cut(content)

    keywords = [
        w for w in words
        if len(w) >= 2
        and w not in stopwords
        and not w.isdigit()
    ]

    return keywords[:10]


def desensitize(content: str) -> str:
    """敏感信息脱敏（扩展版：JWT/IP/内部域名/Email）"""
    def mask(m):
        val = m.group(0)
        if len(val) <= 8:
            return "***"
        return val[:3] + "***" + val[-3:]
    return SENSITIVE_PATTERN.sub(mask, content)
