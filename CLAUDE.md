# Mneme 项目 Claude Code 快速导航

环境激活：`source .venv/bin/activate`

## 📁 项目结构说明

### docs/ 文件夹内容概览

#### 1. 📋 [Mneme 全时域记忆引擎 - 产品需求文档（PRD）v0.2.md](docs/Mneme%20全时域记忆引擎%20-%20产品需求文档（PRD）v0.2.md)

**何时加载**：需要理解产品定位、业务逻辑、用户需求时

---

#### 2. 🔌 [Mneme 接口设计文档 - OpenClaw × PostgreSQL.md](docs/Mneme%20接口设计文档%20-%20OpenClaw%20×%20PostgreSQL.md)

**何时加载**：需要实现 API、集成 OpenClaw、操作数据库时

**内容概要**：
- **系统架构**：飞书消息 → OpenClaw Handler → PostgreSQL 的完整数据流
- **通用接口规范**：
  - Base URL（本地 / 生产）
  - 认证：`Authorization: Bearer <openclaw_token>` Header
  - 幂等性保证：`X-Idempotency-Key: <event_id>`
  - 通用响应格式：`{ code, message, data }`
  - 错误码定义表（9 种错误类型）
