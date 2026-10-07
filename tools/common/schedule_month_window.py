"""客服／外場排班檔的季節性月份視窗。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable


DEFAULT_START_ENV = "SERVICE_SCHEDULE_DEEP_CLEAN_START"


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + month - 1 + delta
    return total // 12, total % 12 + 1


def resolve_schedule_months(
    base: datetime,
    start_raw: str,
    *,
    default_window: int = 2,
    warning: Callable[[str], None] | None = None,
) -> list[tuple[int, int]]:
    """回傳本次要處理的 (year, month)。

    平常維持本月＋次月。設定 YYYY-MM-DD 後，從該日期（含）起到隔年
    2 月的季節期間，視窗延伸至隔年 2 月；季節結束後回到兩個月。
    比較時會保留「日」，不會在起始日所在月份提早啟用。
    """
    if default_window < 1:
        raise ValueError("default_window 必須至少為 1")

    def default_months() -> list[tuple[int, int]]:
        return [add_months(base.year, base.month, offset) for offset in range(default_window)]

    value = (start_raw or "").strip()
    if not value:
        return default_months()

    try:
        start_date = date.fromisoformat(value)
    except ValueError:
        if warning:
            warning(f"{DEFAULT_START_ENV} 格式錯誤（應為 YYYY-MM-DD）：{value}，改用本月＋次月")
        return default_months()

    base_date = base.date()
    season_end = date(start_date.year + 1, 2, 28)
    if base_date < start_date or base_date > season_end:
        return default_months()

    months_to_february = (season_end.year - base.year) * 12 + season_end.month - base.month + 1
    window = max(default_window, months_to_february)
    return [add_months(base.year, base.month, offset) for offset in range(window)]
