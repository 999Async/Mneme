"""同义词词典 — 中英文同义词映射 + 反向索引"""

# 主键 → 同义词列表
SYNONYMS: dict[str, list[str]] = {
    # 技术术语
    "api密钥": ["api_key", "apikey", "接口密钥", "密钥", "api secret"],
    "截止": ["deadline", "期限", "完成时间", "到期", "到期日", "截止日期"],
    "周报": ["weekly report", "周总结", "weekly", "工作周报"],
    "数据库": ["database", "db", "mysql", "postgresql", "mongo", "redis", "sqlite"],
    "服务器": ["server", "机器", "节点", "host", "实例"],
    "部署": ["deploy", "发布", "上线", "release", "上线部署"],
    "接口": ["api", "endpoint", "接口地址", "路由"],
    "环境": ["environment", "env", "环境配置", "配置环境"],
    "测试": ["test", "testing", "qa", "测试环境", "单元测试"],
    "生产": ["production", "prod", "线上", "生产环境"],
    "开发": ["development", "dev", "开发环境", "研发"],
    "版本": ["version", "ver", "版本号", "v"],
    "文档": ["document", "doc", "文档说明", "说明文档"],
    "代码": ["code", "源码", "source", "代码库"],
    "仓库": ["repo", "repository", "代码仓库", "代码库"],
    "分支": ["branch", "feature", "feature branch"],
    "合并": ["merge", "pr", "pull request", "合并请求"],
    "配置": ["config", "configuration", "设置", "配置项"],
    "密钥": ["secret", "key", "token", "凭证", "credential"],
    "密码": ["password", "passwd", "pwd", "口令"],
    "权限": ["permission", "auth", "authorization", "访问控制", "rbac"],
    "日志": ["log", "logs", "日志记录", "log record"],
    "监控": ["monitor", "monitoring", "观测", "可观测性", "告警"],
    # 项目管理
    "项目": ["project", "proj", "项目组"],
    "需求": ["requirement", "req", "需求文档", "prd"],
    "排期": ["schedule", "timeline", "计划", "时间表", "里程碑"],
    "迭代": ["sprint", "iteration", "周期", "sprint周期"],
    "评审": ["review", "评审会议", "code review", "设计评审"],
    "上线": ["release", "go live", "发布上线", "deploy"],
    # 人员
    "负责人": ["owner", "接口人", "负责人", "对接人", "pm", "产品经理"],
    "领导": ["领导", "老板", "manager", "主管"],
    # 时间
    "下周": ["next week", "下个星期", "下星期"],
    "本周": ["this week", "这个星期", "这星期"],
    "今天": ["today", "今日", "本日"],
    "明天": ["tomorrow", "明日"],
    "每月": ["monthly", "每个月", "月度"],
    "每周": ["weekly", "每星期", "每星期"],
    # 决策
    "决定": ["decide", "decision", "确定", "拍板", "定下来"],
    "同意": ["agree", "approve", "批准", "通过", "认可"],
    "拒绝": ["reject", "deny", "否决", "不同意"],
    "修改": ["modify", "change", "update", "更新", "变更", "改动"],
    "取消": ["cancel", "abort", "撤销", "撤回"],
    "增加": ["add", "新增", "添加", "补充"],
    "删除": ["delete", "remove", "移除", "清理"],
}


def _build_reverse_index(synonyms: dict[str, list[str]]) -> dict[str, str]:
    """构建反向索引：任意同义词 → 主键"""
    index: dict[str, str] = {}
    for main_key, synonym_list in synonyms.items():
        # 主键本身也映射到自己（小写）
        index[main_key.lower()] = main_key
        for syn in synonym_list:
            index[syn.lower()] = main_key
    return index


# 反向索引：任意词 → 主键
SYNONYM_INDEX: dict[str, str] = _build_reverse_index(SYNONYMS)


def expand_query(query: str) -> list[str]:
    """查询扩展：返回原始查询 + 所有匹配的同义词

    示例：
        "API密钥" → ["API密钥", "api_key", "apikey", "接口密钥", "密钥", "api secret"]
    """
    query_lower = query.lower().strip()
    results = [query]  # 保留原始查询

    # 检查查询是否匹配某个同义词组
    main_key = SYNONYM_INDEX.get(query_lower)
    if main_key:
        synonyms = SYNONYMS[main_key]
        results.extend(synonyms)
    else:
        # 尝试子串匹配：查询包含在某个同义词中
        for main_key, synonyms in SYNONYMS.items():
            if query_lower in main_key or main_key in query_lower:
                results.extend(synonyms)
                break

    # 去重保持顺序
    seen = set()
    unique = []
    for r in results:
        r_lower = r.lower()
        if r_lower not in seen:
            seen.add(r_lower)
            unique.append(r)

    return unique


def are_synonyms(word_a: str, word_b: str) -> bool:
    """判断两个词是否为同义词"""
    key_a = SYNONYM_INDEX.get(word_a.lower())
    key_b = SYNONYM_INDEX.get(word_b.lower())
    return key_a is not None and key_a == key_b
