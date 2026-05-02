"""LLM 提示词模板"""

CONFLICT_DETECTION_PROMPT = """你是一个记忆冲突检测助手。判断新记忆与已有记忆的关系。

新记忆：{content}

已有记忆：
{memories_text}

请判断每条已有记忆与新记忆的关系类型：
- duplicate: 重复（语义完全相同，无新信息，不应创建新记忆）
- update: 更新（有新信息或修正，应创建新版本替代旧记忆）
- supplement: 补充（新增信息，应保留两者）
- unrelated: 无关（完全不同的话题）

返回 JSON 格式。"""

MEMORY_EXTRACTION_PROMPT = """以下是一段团队群聊记录，请提取其中值得长期记住的关键信息。

群聊记录：
{messages}

提取规则：
1. 只提取决策、约定、截止日期、负责人变更等重要信息
2. 忽略闲聊、问候、表情包、确认收到等无关内容
3. 每条记忆标注类型：
   - decision: 团队决策（技术选型、方案确定等）
   - fact: 事实信息（配置、密钥、负责人等）
   - intent: 意图/计划（待办、即将执行的操作）
   - command: 指令（明确的规则或约定）
4. 给每条记忆一个 0-1 的置信度评分

返回 JSON：
{{
  "memories": [
    {{
      "content": "精简后的记忆内容",
      "type": "decision|fact|intent|command",
      "confidence": 0.9
    }}
  ]
}}

如果没有值得记住的信息，返回 {{"memories": []}}"""

# ─── JSON Schema（用于 Function Calling）───

CONFLICT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "has_conflict": {"type": "boolean"},
                    "type": {
                        "type": "string",
                        "enum": ["duplicate", "update", "supplement", "unrelated"]
                    },
                    "reason": {"type": "string"}
                },
                "required": ["index", "has_conflict", "type", "reason"]
            },
        },
    },
    "required": ["items"]
}

MEMORY_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "精简后的记忆内容"},
                    "type": {"type": "string", "enum": ["decision", "fact", "intent", "command"]},
                    "confidence": {"type": "number", "description": "0-1 置信度评分"},
                },
                "required": ["content", "type", "confidence"],
            },
        },
    },
    "required": ["memories"],
}
