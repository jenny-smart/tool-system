"""財報資料年度的小工具：日期區間跨年時，列出要處理的財報年度，並合併各年度結果。
不依賴 Google API，方便各功能與測試直接引用。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def taipei_year() -> int:
    return datetime.now(ZoneInfo("Asia/Taipei")).year


def statement_years(start_date=None, end_date=None) -> list[int | None]:
    """日期區間涵蓋的財報年度（舊→新）。沒給區間或只涵蓋今年 → [None]（＝今年，行為不變）；
    例如 2027 年 1 月處理 2026/12/20～2027/01/05 → [2026, 2027]，各年的列各自寫回該年財報。"""
    if not start_date and not end_date:
        return [None]
    current = taipei_year()
    first = (start_date or end_date).year
    last = end_date.year if end_date else current
    if first > last:
        first, last = last, first
    years = [year for year in range(first, last + 1)]
    return [None if year == current else year for year in years]


def merge_year_results(results: list[dict]) -> dict:
    """多個年度的套用結果相加；文字欄（例如 diagnostics）用「｜」接起來。"""
    if len(results) == 1:
        return results[0]
    merged: dict = {}
    for result in results:
        for key, value in result.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                merged[key] = merged.get(key, 0) + value
            elif value not in (None, ""):
                merged[key] = f"{merged[key]}｜{value}" if merged.get(key) else value
    return merged
