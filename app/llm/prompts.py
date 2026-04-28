"""LLM 提示词模板"""

CONFLICT_DETECTION_PROMPT = """你是一个记忆冲突检测助手。判断新信息是否与已有记忆冲突。

新信息：{content}

已有记忆：
{memories_text}

判断标准：
1. 是否同一主题的更新？（如 "周报发给A" → "周报发给B" 是 update）
2. 是否相互矛盾？（如 "用MySQL" vs "用MongoDB" 是 update）
3. 是否时间上有先后关系？（新信息替代旧信息）
4. 是否完全不相关？（不同项目/不同话题的相似信息不算冲突）

返回 JSON：
{{
  "items": [
    {{
      "index": 0,
      "has_conflict": true/false,
      "type": "update|cancel|supplement|none",
      "reason": "简要说明原因"
    }}
  ]
}}

注意：
- 不同项目的相同技术话题不算冲突
- 补充信息（supplement）不算冲突
- 只有明确的更新/取消/矛盾才算冲突"""

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
