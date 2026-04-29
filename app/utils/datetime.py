"""统一时间工具 — 全局使用 unix 毫秒时间戳"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

MS_24H = 86_400_000  # 24 小时毫秒数


def ms_now() -> int:
    """当前 unix 毫秒时间戳"""
    return int(time.time() * 1000)


def ms_to_iso(ms: int | None) -> str | None:
    """ms → ISO 8601 字符串（配置时区）"""
    if ms is None:
        return None
    tz = ZoneInfo(settings.timezone)
    return datetime.fromtimestamp(ms / 1000, tz=tz).isoformat()


def ms_to_strftime(ms: int | None, fmt: str) -> str:
    """ms → 格式化字符串（配置时区）"""
    if ms is None:
        return ""
    tz = ZoneInfo(settings.timezone)
    return datetime.fromtimestamp(ms / 1000, tz=tz).strftime(fmt)


def ms_to_hour(ms: int) -> int:
    """ms → 配置时区的小时数（0-23）"""
    tz = ZoneInfo(settings.timezone)
    return datetime.fromtimestamp(ms / 1000, tz=tz).hour


def ms_day_start(ms: int) -> int:
    """ms → 当天 0 点的 ms（配置时区）"""
    tz = ZoneInfo(settings.timezone)
    dt = datetime.fromtimestamp(ms / 1000, tz=tz)
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(day_start.timestamp() * 1000)
