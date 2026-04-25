from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host: str = "0.0.0.0"
    port: int = 3001

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://mneme:mneme123@localhost:5432/mneme"

    # Feishu (Python 直接调飞书 API 推送卡片)
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    # Plugin auth
    mneme_service_token: str = ""

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
