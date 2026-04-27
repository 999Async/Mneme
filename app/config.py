from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host: str = "0.0.0.0"
    port: int = 3001

    # Timezone
    timezone: str = "Asia/Shanghai"

    # PostgreSQL (本地测试用 sqlite+aiosqlite)
    database_url: str = "sqlite+aiosqlite:///./test.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Feishu (Python 直接调飞书 API 推送卡片)
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    # Plugin auth
    mneme_service_token: str = ""

    # LLM (OpenAI 兼容接口，支持 OpenAI / DeepSeek / 本地模型)
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Embedding (OpenAI 兼容接口)
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Decay params
    decay_base_rate: float = 0.5
    decay_sensitivity: float = 1.0

    # Push params
    push_l1_threshold: float = 0.3
    push_l1_daily_limit: int = 3
    push_window_start: int = 9
    push_window_end: int = 19

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
