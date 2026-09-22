from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import gspread

from tools.lemon_backend.members import export_stored_value_members, normalize_member_phone

from tools.service_management.stored_value import (
    TZ_TAIPEI,
    _calendar_service,
    _fetch_calendar_events,
    _gc,
    _get_credentials,
    _load_stored_value_info,
    _load_target_file_id,
    _process_events,
    load_area_config,
    normalize_address,
    normalize_name_for_compare,
    parse_service_people,
)


NOTICE_HEADERS = [
    "地區",
    "客戶類型",
    "姓名",
    "電話",
    "Email",
    "地址",
    "LINE連結",
    "第一階段服務",
    "第二階段服務",
    "年後第一次服務",
    "第一階段次數",
    "第二階段次數",
    "第一階段加價",
    "第二階段加價",
    "Email主旨",
    "通知內容",
    "寄送狀態",
    "寄送時間",
    "非2人服務",
    "日曆備註",
    "服務狀態",
    "服務日期提醒內容",
]

NONROUTINE_NOTICE_HEADERS = [
    "地區",
    "客戶類型",
    "姓名",
    "電話",
    "Email",
    "地址",
    "LINE連結",
    "會員等級",
    "剩餘儲值金",
    "Email主旨",
    "通知內容",
    "寄送狀態",
    "寄送時間",
]

SETTINGS_HEADERS = [
    "年度",
    "PART1開始",
    "PART1結束",
    "PART1 VIP平日加價",
    "PART1 VIP週末加價",
    "PART1 非VIP平日加價",
    "PART1 非VIP週末加價",
    "PART2開始",
    "PART2結束",
    "PART2 VIP平日加價",
    "PART2 VIP週末加價",
    "PART2 非VIP平日加價",
    "PART2 非VIP週末加價",
    "定期VIP回覆截止",
    "非定期VIP開放",
    "非定期VIP截止",
    "通知輸出試算表ID",
    "農曆年休假開始",
    "農曆年休假結束",
    "更新時間",
]

DEFAULT_SETTINGS_SPREADSHEET_ID = "1u9boCPWeQk2yWVJ4an7GH0DXNHuLDA0sjp9jKJEG1v8"
DEFAULT_NOTICE_SPREADSHEET_ID = "1AsU4YF6t8gt-lVb0p4C656CWdbveWkuHetPe1nwC7BE"
DEFAULT_MASTER_SPREADSHEET_ID = "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
SETTINGS_SHEET_NAME = "大掃除設定"
MASTER_ID_SHEET_NAME = "大掃除設定"
BASE_WEEKDAY_RATE = 1200
# 年節大掃除總額試算不套用一般週末基準價；兩階段週末均維持 NT$1,200。
BASE_WEEKEND_RATE = 1200
MINIMUM_SERVICE_HOURS = 3
COMPARISON_HOURS = (2, 3, 4, 6, 8)


def _output_gc() -> gspread.Client:
    """年度大掃除檔案屬於檸檬 Google 帳號，優先使用同帳號 OAuth。"""
    try:
        from services.google_auth import get_gspread_client

        return get_gspread_client()
    except Exception:
        return _gc()


def resolve_deep_clean_sheet_ids(
    season_year: int,
    master_spreadsheet_id: str = DEFAULT_MASTER_SPREADSHEET_ID,
) -> tuple[str, str]:
    gc = _gc()
    ss = gc.open_by_key(master_spreadsheet_id.strip() or DEFAULT_MASTER_SPREADSHEET_ID)
    try:
        rows = ss.worksheet(MASTER_ID_SHEET_NAME).get("A1:B20")
    except gspread.WorksheetNotFound as exc:
        raise ValueError(f"主控表尚未建立「{MASTER_ID_SHEET_NAME}」工作表") from exc
    values = {
        str(row[0]).strip(): str(row[1]).strip()
        for row in rows[1:]
        if len(row) >= 2 and str(row[0]).strip() and str(row[1]).strip()
    }
    root_folder_id = values.get("大掃除根目錄資料夾 ID", "")
    if not root_folder_id:
        raise ValueError("主控表「大掃除設定」尚未填寫大掃除根目錄資料夾 ID")

    from services.google_drive import DriveService

    try:
        from services.google_auth import get_drive_service

        drive_api = get_drive_service()
    except Exception:
        from googleapiclient.discovery import build

        drive_api = build("drive", "v3", credentials=_get_credentials(), cache_discovery=False)
    drive = DriveService(drive_api)
    year_folder = drive.find_folder(root_folder_id, str(season_year))
    if not year_folder:
        raise ValueError(f"大掃除根目錄下找不到 {season_year} 資料夾")

    settings_name = f"{season_year}年終大掃除系統調整"
    notice_name = f"{season_year}年終大掃除VIP"
    settings_files = drive.find_google_sheet_by_name(year_folder["id"], settings_name)
    notice_files = drive.find_google_sheet_by_name(year_folder["id"], notice_name)
    if len(settings_files) != 1:
        raise ValueError(f"{season_year} 資料夾內需且只能有一份「{settings_name}」Google Sheet")
    if len(notice_files) != 1:
        raise ValueError(f"{season_year} 資料夾內需且只能有一份「{notice_name}」Google Sheet")
    return settings_files[0]["id"], notice_files[0]["id"]


@dataclass(frozen=True)
class DeepCleanSettings:
    season_year: int
    phase1_start: datetime
    phase1_end: datetime
    phase1_weekday_rate: float
    phase1_weekend_rate: float
    phase2_start: datetime
    phase2_end: datetime
    phase2_weekday_rate: float
    phase2_weekend_rate: float
    reply_deadline: str
    booking_start: datetime
    booking_end: datetime
    notice_spreadsheet_id: str = DEFAULT_NOTICE_SPREADSHEET_ID
    phase1_nonvip_weekday_rate: float = 0
    phase1_nonvip_weekend_rate: float = 0
    phase2_nonvip_weekday_rate: float = 0
    phase2_nonvip_weekend_rate: float = 0
    lunar_new_year_start: datetime | None = None
    lunar_new_year_end: datetime | None = None


def deep_clean_settings_form_values(settings: DeepCleanSettings) -> dict[str, Any]:
    """Convert persisted settings into stable Streamlit widget defaults."""
    return {
        "phase1_start": settings.phase1_start.date(),
        "phase1_end": settings.phase1_end.date(),
        "phase1_weekday_rate": int(round(settings.phase1_weekday_rate)),
        "phase1_weekend_rate": int(round(settings.phase1_weekend_rate)),
        "phase1_nonvip_weekday_rate": int(round(settings.phase1_nonvip_weekday_rate)),
        "phase1_nonvip_weekend_rate": int(round(settings.phase1_nonvip_weekend_rate)),
        "phase2_start": settings.phase2_start.date(),
        "phase2_end": settings.phase2_end.date(),
        "phase2_weekday_rate": int(round(settings.phase2_weekday_rate)),
        "phase2_weekend_rate": int(round(settings.phase2_weekend_rate)),
        "phase2_nonvip_weekday_rate": int(round(settings.phase2_nonvip_weekday_rate)),
        "phase2_nonvip_weekend_rate": int(round(settings.phase2_nonvip_weekend_rate)),
        "reply_deadline": settings.reply_deadline,
        "booking_start": settings.booking_start.date(),
        "booking_end": settings.booking_end.date(),
        "lunar_new_year_start": settings.lunar_new_year_start.date() if settings.lunar_new_year_start else None,
        "lunar_new_year_end": settings.lunar_new_year_end.date() if settings.lunar_new_year_end else None,
    }


def _validate_settings(settings: DeepCleanSettings) -> None:
    if settings.phase1_start > settings.phase1_end or settings.phase2_start > settings.phase2_end:
        raise ValueError("階段開始日期不可晚於結束日期")
    if settings.phase1_end >= settings.phase2_start:
        raise ValueError("第一階段結束日期必須早於第二階段開始日期")
    if settings.booking_start > settings.booking_end:
        raise ValueError("非定期 VIP 開放預約日不可晚於截止日")
    if bool(settings.lunar_new_year_start) != bool(settings.lunar_new_year_end):
        raise ValueError("農曆年休假開始與結束日期必須同時設定")
    if settings.lunar_new_year_start and settings.lunar_new_year_start > settings.lunar_new_year_end:
        raise ValueError("農曆年休假開始日期不可晚於結束日期")
    rates = [
        settings.phase1_weekday_rate,
        settings.phase1_weekend_rate,
        settings.phase1_nonvip_weekday_rate,
        settings.phase1_nonvip_weekend_rate,
        settings.phase2_weekday_rate,
        settings.phase2_weekend_rate,
        settings.phase2_nonvip_weekday_rate,
        settings.phase2_nonvip_weekend_rate,
    ]
    if any(rate < 0 for rate in rates):
        raise ValueError("年節加價不可為負數")


def _settings_row(settings: DeepCleanSettings) -> list[Any]:
    return [
        settings.season_year,
        settings.phase1_start.strftime("%Y-%m-%d"),
        settings.phase1_end.strftime("%Y-%m-%d"),
        settings.phase1_weekday_rate,
        settings.phase1_weekend_rate,
        settings.phase1_nonvip_weekday_rate,
        settings.phase1_nonvip_weekend_rate,
        settings.phase2_start.strftime("%Y-%m-%d"),
        settings.phase2_end.strftime("%Y-%m-%d"),
        settings.phase2_weekday_rate,
        settings.phase2_weekend_rate,
        settings.phase2_nonvip_weekday_rate,
        settings.phase2_nonvip_weekend_rate,
        settings.reply_deadline,
        settings.booking_start.strftime("%Y-%m-%d"),
        settings.booking_end.strftime("%Y-%m-%d"),
        settings.notice_spreadsheet_id,
        settings.lunar_new_year_start.strftime("%Y-%m-%d") if settings.lunar_new_year_start else "",
        settings.lunar_new_year_end.strftime("%Y-%m-%d") if settings.lunar_new_year_end else "",
        datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
    ]


def _settings_from_row(row: list[Any]) -> DeepCleanSettings:
    values = list(row)
    is_new_format = len(values) >= 18
    has_lunar_dates = len(values) >= len(SETTINGS_HEADERS)
    values += [""] * max(0, len(SETTINGS_HEADERS) - len(values))
    if is_new_format:
        p1_nonvip_weekday, p1_nonvip_weekend = float(values[5] or 0), float(values[6] or 0)
        p2_start_index = 7
        p2_nonvip_weekday, p2_nonvip_weekend = float(values[11] or 0), float(values[12] or 0)
        tail_index = 13
    else:
        # 舊版 14 欄只有 VIP 四項價格；升級時保留原價，非 VIP 預設為 0。
        p1_nonvip_weekday = p1_nonvip_weekend = 0
        p2_start_index = 5
        p2_nonvip_weekday = p2_nonvip_weekend = 0
        tail_index = 9
    return DeepCleanSettings(
        season_year=int(values[0]),
        phase1_start=_parse_date(str(values[1])),
        phase1_end=_parse_date(str(values[2]), end_of_day=True),
        phase1_weekday_rate=float(values[3]),
        phase1_weekend_rate=float(values[4]),
        phase2_start=_parse_date(str(values[p2_start_index])),
        phase2_end=_parse_date(str(values[p2_start_index + 1]), end_of_day=True),
        phase2_weekday_rate=float(values[p2_start_index + 2]),
        phase2_weekend_rate=float(values[p2_start_index + 3]),
        reply_deadline=str(values[tail_index] or ""),
        booking_start=_parse_date(str(values[tail_index + 1])),
        booking_end=_parse_date(str(values[tail_index + 2]), end_of_day=True),
        notice_spreadsheet_id=str(values[tail_index + 3] or DEFAULT_NOTICE_SPREADSHEET_ID).strip(),
        phase1_nonvip_weekday_rate=p1_nonvip_weekday,
        phase1_nonvip_weekend_rate=p1_nonvip_weekend,
        phase2_nonvip_weekday_rate=p2_nonvip_weekday,
        phase2_nonvip_weekend_rate=p2_nonvip_weekend,
        lunar_new_year_start=_parse_date(str(values[17])) if has_lunar_dates and values[17] else None,
        lunar_new_year_end=_parse_date(str(values[18]), end_of_day=True) if has_lunar_dates and values[18] else None,
    )


def _parse_date(value: str, end_of_day: bool = False) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.replace(
        hour=23 if end_of_day else 0,
        minute=59 if end_of_day else 0,
        second=59 if end_of_day else 0,
        microsecond=0,
        tzinfo=TZ_TAIPEI,
    )


def _money(value: float) -> str:
    return f"NT${int(round(value)):,}"


def _date_range(start: datetime, end: datetime) -> str:
    return f"{start:%Y/%m/%d}～{end:%Y/%m/%d}"


def _month_range(start: datetime, end: datetime) -> str:
    return f"{start:%Y/%m}～{end:%Y/%m}"


def _month_bounds(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    month_start = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if end.month == 12:
        next_month = end.replace(year=end.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month = end.replace(month=end.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start, next_month


def _service_label(row: dict[str, Any]) -> str:
    label = (
        f"{row['date_str']}（週{row['weekday']}）"
        f" {row['start_str']}–{row['end_str']}"
        f"｜{row['service'] or '定期清潔'}"
    )
    note = str(row.get("note") or "").strip()
    status = str(row.get("status") or "").strip()
    if note:
        label += f"｜備註：{note}"
    if status:
        label += f"｜狀態：{status}"
    return label


def _dated_values(rows: list[dict[str, Any]], field: str) -> str:
    return "\n".join(
        f"{row['date_str']}：{value}"
        for row in rows
        if (value := str(row.get(field) or "").strip())
    )


def _non_two_person_services(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{row['date_str']}：{service}"
        for row in rows
        if (service := str(row.get("service") or "").strip())
        and parse_service_people(service) != 2
    )


def _service_date_label(row: dict[str, Any]) -> str:
    return f"{row['date_str']}({row['weekday']})"


def _service_datetime_line(row: dict[str, Any]) -> str:
    return f"{row['date_str']}（週{row['weekday']}）{row['start_str']}–{row['end_str']}"


def _is_hidden_reminder_service(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "")
    return "待確認" in status or "暫停" in status


def _holiday_pause_block(
    holiday_rows: list[dict[str, Any]],
    lunar_new_year_start: datetime | None,
    lunar_new_year_end: datetime | None,
) -> str:
    if not lunar_new_year_start or not lunar_new_year_end:
        return ""
    original_services = "\n".join(_service_datetime_line(row) for row in holiday_rows) or "無"
    return (
        f"農曆年休假暫停服務日期：{lunar_new_year_start:%Y/%m/%d}-{lunar_new_year_end:%Y/%m/%d}\n"
        f"該地址原訂服務日期：\n{original_services}\n\n"
    )


def _service_reminder_text(
    name: str,
    address: str,
    month_rows: list[dict[str, Any]],
    next_service: dict[str, Any] | None,
    phase1_start: datetime,
    phase2_end: datetime,
    holiday_rows: list[dict[str, Any]],
    lunar_new_year_start: datetime | None,
    lunar_new_year_end: datetime | None,
) -> str:
    visible_rows = [row for row in month_rows if not _is_hidden_reminder_service(row)]
    if not visible_rows and not holiday_rows:
        return ""
    schedule = "\n".join(_service_datetime_line(row) for row in visible_rows) or "目前無排程"
    next_label = _service_date_label(next_service) if next_service else "目前尚無排程"
    holiday_block = _holiday_pause_block(
        holiday_rows, lunar_new_year_start, lunar_new_year_end,
    )
    return (
        f"❤️親愛的 {name} 您好：\n\n"
        f"服務地址：{address or '未提供'}\n"
        f"提醒您目前該服務地址{_month_range(phase1_start, phase2_end)}的服務日期/時段如下：\n"
        f"{schedule}\n\n"
        f"{holiday_block}"
        f"年節後第一次服務日期：{next_label}\n\n"
        "請您協助確認，若需要調整，請連繫我們～"
    )


def _is_monthly_confirmation(row: dict[str, Any]) -> bool:
    return "每月確認" in str(row.get("note") or "")


def _reply_deadline_date(reply_deadline: str) -> str:
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", reply_deadline)
    if not match:
        return ""
    year, month, day = (int(value) for value in match.groups())
    return f"{year:04d}/{month:02d}/{day:02d}"


def _billable_person_hours(row: dict[str, Any]) -> float:
    """依服務人數與起訖時間重算人時，不信任可能已失真的彙整欄位。"""
    people = float(row.get("people") or parse_service_people(str(row.get("service") or "")))
    hours = float(row.get("hours") or 0)
    if hours <= 0:
        match_start = re.fullmatch(r"(\d{1,2}):(\d{2})", str(row.get("start_str") or "").strip())
        match_end = re.fullmatch(r"(\d{1,2}):(\d{2})", str(row.get("end_str") or "").strip())
        if match_start and match_end:
            start_minutes = int(match_start.group(1)) * 60 + int(match_start.group(2))
            end_minutes = int(match_end.group(1)) * 60 + int(match_end.group(2))
            hours = max((end_minutes - start_minutes) / 60.0, 0.0)
    if people > 0 and hours > 0:
        return people * hours
    return float(row.get("person_hrs") or 0)


def _extra_charge(row: dict[str, Any], weekday_rate: float, weekend_rate: float) -> float:
    # 加價單位沿用年節規則：每 2 人 1 小時一個單位。
    rate = weekend_rate if row["start_dt"].weekday() >= 5 else weekday_rate
    return rate * (_billable_person_hours(row) / 2.0)


def _price_text(start: datetime, end: datetime, weekday_rate: float, weekend_rate: float) -> str:
    return (
        f"{_date_range(start, end)}\n"
        f"平日（週一～週五）：每 2 人 1 小時酌收年節加價 {_money(weekday_rate)}（含稅）\n"
        f"週末（週六～週日）：每 2 人 1 小時酌收年節加價 {_money(weekend_rate)}（含稅）"
    )


def _service_intro(phase1_start: datetime, phase2_end: datetime) -> str:
    return (
        f"{phase1_start.year}年節大掃除期間：{_date_range(phase1_start, phase2_end)}\n"
        f"基本時數為 2 人 {MINIMUM_SERVICE_HOURS} 小時起\n"
        "大掃除服務主要為方便需求6–8小時客戶，服務內容同居家清潔，以人時計價。"
    )


def _comparison_values(settings: DeepCleanSettings, hours: int, include_base: bool) -> list[float]:
    rates = (
        settings.phase1_nonvip_weekday_rate,
        settings.phase1_weekday_rate,
        settings.phase1_nonvip_weekend_rate,
        settings.phase1_weekend_rate,
        settings.phase2_nonvip_weekday_rate,
        settings.phase2_weekday_rate,
        settings.phase2_nonvip_weekend_rate,
        settings.phase2_weekend_rate,
    )
    bases = (
        BASE_WEEKDAY_RATE,
        BASE_WEEKDAY_RATE,
        BASE_WEEKEND_RATE,
        BASE_WEEKEND_RATE,
        BASE_WEEKDAY_RATE,
        BASE_WEEKDAY_RATE,
        BASE_WEEKEND_RATE,
        BASE_WEEKEND_RATE,
    )
    return [hours * (rate + (base if include_base else 0)) for rate, base in zip(rates, bases)]


def _engineer_price_text(settings: DeepCleanSettings, vip: bool) -> str:
    if vip:
        rates = (
            settings.phase1_weekday_rate,
            settings.phase1_weekend_rate,
            settings.phase2_weekday_rate,
            settings.phase2_weekend_rate,
        )
    else:
        rates = (
            settings.phase1_nonvip_weekday_rate,
            settings.phase1_nonvip_weekend_rate,
            settings.phase2_nonvip_weekday_rate,
            settings.phase2_nonvip_weekend_rate,
        )
    return (
        f"PART 1 {_date_range(settings.phase1_start, settings.phase1_end)}\n"
        f"平日每2人1小時年節加價{_money(rates[0])}（含稅）\n"
        f"週末每2人1小時年節加價{_money(rates[1])}（含稅）\n"
        f"PART 2 {_date_range(settings.phase2_start, settings.phase2_end)}\n"
        f"平日每2人1小時年節加價{_money(rates[2])}（含稅）\n"
        f"週末每2人1小時年節加價{_money(rates[3])}（含稅）"
    )


def _build_notice_text(
    name: str,
    address: str,
    phase1_start: datetime,
    phase1_end: datetime,
    phase2_start: datetime,
    phase2_end: datetime,
    phase1_weekday_rate: float,
    phase1_weekend_rate: float,
    phase2_weekday_rate: float,
    phase2_weekend_rate: float,
    phase1_rows: list[dict[str, Any]],
    phase2_rows: list[dict[str, Any]],
    month_rows: list[dict[str, Any]],
    holiday_rows: list[dict[str, Any]],
    phase1_total: float,
    phase2_total: float,
    next_service: dict[str, Any] | None,
    reply_deadline: str,
    lunar_new_year_start: datetime | None,
    lunar_new_year_end: datetime | None,
) -> str:
    all_rows = phase1_rows + phase2_rows
    monthly_confirmation = any(_is_monthly_confirmation(row) for row in all_rows)
    service_dates = ", ".join(_service_date_label(row) for row in all_rows) or "目前無排程"
    service_times = "、".join(sorted({f"{row['start_str']}–{row['end_str']}" for row in all_rows})) or "目前無排程"
    next_label = _service_date_label(next_service) if next_service else "目前尚無排程"
    deadline = f"建議您於 {reply_deadline} 前，" if reply_deadline.strip() else "如需調整，建議您儘早"
    deadline_date = _reply_deadline_date(reply_deadline)
    holiday_block = _holiday_pause_block(
        holiday_rows, lunar_new_year_start, lunar_new_year_end,
    )
    if monthly_confirmation:
        arrangement = (
            f"服務地址：{address or '未提供'}\n"
            f"🕓 請於 {deadline_date} 前告知欲安排的日期。\n\n"
            if deadline_date
            else f"服務地址：{address or '未提供'}\n🕓 請儘早告知欲安排的日期。\n\n"
        )
        arrangement += holiday_block
        contact = "請透過官方 LINE@ 與我們聯繫。\n"
        estimate_note = ""
    else:
        month_dates = "\n".join(_service_date_label(row) for row in month_rows) or "目前無排程"
        month_times = "、".join(
            sorted({f"{row['start_str']}–{row['end_str']}" for row in month_rows})
        ) or "目前無排程"
        arrangement = (
            f"服務地址：{address or '未提供'}\n"
            f"🕓 您原訂週期於（{_month_range(phase1_start, phase2_end)}）之服務安排如下：\n\n"
            f"{month_dates}\n"
            f"服務時段：{month_times}\n\n"
            f"🕓 您原訂週期於年節期間（{_date_range(phase1_start, phase2_end)}）之服務安排如下：\n\n"
            f"年節加價服務日期：{service_dates}\n"
            f"服務時段：{service_times}\n\n"
            f"PART 1 次數／年節加價金額：{len(phase1_rows)} 次／{_money(phase1_total)}\n"
            f"PART 2 次數／年節加價金額：{len(phase2_rows)} 次／{_money(phase2_total)}\n\n"
            f"{holiday_block}"
            f"年節後第一次服務日期：{next_label}\n\n"
        )
        contact = f"{deadline}透過官方 LINE@ 與我們聯繫。\n"
        estimate_note = "以上金額依目前排程估算；如改期，將依實際服務日期重新計算。\n"
    return (
        f"❤️親愛的 {name} 您好：\n\n"
        "🎉 感謝您長期支持檸檬家事服務 🎉\n"
        f"{_service_intro(phase1_start, phase2_end)}\n"
        "✨VIP 專屬 — 優先預約年節大掃除服務正式開放✨\n\n"
        "🧽《VIP 定期客戶年節大掃除加價收費說明》\n"
        "\n"
        f"📍PART 1：{_price_text(phase1_start, phase1_end, phase1_weekday_rate, phase1_weekend_rate)}\n\n"
        f"📍PART 2：{_price_text(phase2_start, phase2_end, phase2_weekday_rate, phase2_weekend_rate)}\n\n"
        "💰「年節加價」將自動從您的「儲值金」帳戶扣除，無需現場付款；"
        "惟「車馬費」仍須於現場支付。\n\n"
        f"{arrangement}"
        "💛《VIP 定期客戶優先預約》\n"
        f"{contact}"
        f"{estimate_note}"
        "我們將優先為您安排大掃除服務，謝謝您！\n\n"
        "檸檬家事服務 🍋\n陪您一起迎新年、好運滿滿過好年 🌟"
    )


def build_nonroutine_notice(
    name: str,
    phase1_start: datetime,
    phase1_end: datetime,
    phase2_start: datetime,
    phase2_end: datetime,
    phase1_weekday_rate: float,
    phase1_weekend_rate: float,
    phase2_weekday_rate: float,
    phase2_weekend_rate: float,
    booking_start: datetime,
    booking_end: datetime,
) -> str:
    return (
        f"❤️親愛的 {name} 您好：\n\n"
        "🎉 感謝您長期支持檸檬家事服務 🎉\n"
        f"{_service_intro(phase1_start, phase2_end)}\n"
        "✨VIP 專屬 — 優先預約年節大掃除服務正式開放✨\n\n"
        "🧽《VIP 客戶年節大掃除加價收費說明》\n"
        "\n"
        f"📍PART 1：{_price_text(phase1_start, phase1_end, phase1_weekday_rate, phase1_weekend_rate)}\n\n"
        f"📍PART 2：{_price_text(phase2_start, phase2_end, phase2_weekday_rate, phase2_weekend_rate)}\n\n"
        "💰「年節加價」將自動從您的「儲值金」帳戶扣除，無需現場付款；"
        "惟「車馬費」仍須於現場支付。\n\n"
        "💛《VIP 客戶優先預約》\n"
        f"VIP 開放預約時間：{_date_range(booking_start, booking_end)}\n"
        "建議您於開放期間內，透過官方 LINE@ 與我們聯繫預約。\n"
        "我們將優先為您安排大掃除服務，謝謝您！\n\n"
        "檸檬家事服務 🍋\n陪您一起迎新年、好運滿滿過好年 🌟"
    )


def build_notice_rows(
    area_name: str,
    rows: list[dict[str, Any]],
    stored_info: dict[str, dict],
    phase1_start: datetime,
    phase1_end: datetime,
    phase2_start: datetime,
    phase2_end: datetime,
    phase1_weekday_rate: float,
    phase1_weekend_rate: float,
    phase2_weekday_rate: float,
    phase2_weekend_rate: float,
    reply_deadline: str = "",
    lunar_new_year_start: datetime | None = None,
    lunar_new_year_end: datetime | None = None,
) -> list[list[Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "phase1": [], "phase2": [], "months": [], "holiday": [], "after": [], "phone": "", "email": "",
            "line": "", "name": "", "address": "",
        }
    )
    month_start, month_end_exclusive = _month_bounds(phase1_start, phase2_end)

    for row in rows:
        row = dict(row)
        if _is_monthly_confirmation(row):
            row["status"] = "待確認"
        service_dt = row["start_dt"]
        name = str(row.get("name") or "").strip()
        address = str(row.get("address") or "").strip()
        key = (normalize_name_for_compare(name), normalize_address(address))
        target = grouped[key]
        target["name"] = name
        target["address"] = address
        target["phone"] = target["phone"] or str(row.get("phone") or "")

        stored = stored_info.get(normalize_name_for_compare(name), {})
        target["line"] = target["line"] or str(stored.get("lineValue") or "")
        target["email"] = target["email"] or str(stored.get("email") or "")

        in_lunar_holiday = bool(
            lunar_new_year_start
            and lunar_new_year_end
            and lunar_new_year_start <= service_dt <= lunar_new_year_end
        )
        if in_lunar_holiday:
            row["status"] = "暫停服務"
            target["holiday"].append(row)
            continue

        if month_start <= service_dt < month_end_exclusive:
            target["months"].append(row)

        if phase1_start <= service_dt <= phase1_end:
            target["phase1"].append(row)
        elif phase2_start <= service_dt <= phase2_end:
            target["phase2"].append(row)
        elif service_dt > phase2_end:
            target["after"].append(row)

    output: list[list[Any]] = []
    for target in grouped.values():
        p1 = sorted(target["phase1"], key=lambda row: row["start_dt"])
        p2 = sorted(target["phase2"], key=lambda row: row["start_dt"])
        holiday = sorted(target["holiday"], key=lambda row: row["start_dt"])
        if not p1 and not p2 and not holiday:
            continue
        after_all = sorted(target["after"], key=lambda row: row["start_dt"])
        after = [row for row in after_all if not _is_hidden_reminder_service(row)]
        month_rows = sorted(target["months"], key=lambda row: row["start_dt"])
        next_service = after[0] if after else None
        p1_total = sum(_extra_charge(row, phase1_weekday_rate, phase1_weekend_rate) for row in p1)
        p2_total = sum(_extra_charge(row, phase2_weekday_rate, phase2_weekend_rate) for row in p2)
        notice = _build_notice_text(
            target["name"],
            target["address"],
            phase1_start,
            phase1_end,
            phase2_start,
            phase2_end,
            phase1_weekday_rate,
            phase1_weekend_rate,
            phase2_weekday_rate,
            phase2_weekend_rate,
            p1,
            p2,
            month_rows,
            holiday,
            p1_total,
            p2_total,
            next_service,
            reply_deadline,
            lunar_new_year_start,
            lunar_new_year_end,
        )
        line_url = target["line"]
        line_cell = f'=HYPERLINK("{line_url}","開啟 LINE")' if re.match(r"^https?://", line_url) else line_url
        detail_rows = p1 + p2 + holiday + (after_all[:1] if after_all else [])
        output.append([
            area_name,
            "定期VIP",
            target["name"],
            target["phone"],
            target["email"],
            target["address"],
            line_cell,
            "\n".join(_service_label(row) for row in p1),
            "\n".join(_service_label(row) for row in p2),
            _service_label(next_service) if next_service else "",
            len(p1),
            len(p2),
            p1_total,
            p2_total,
            f"【檸檬家事服務】{phase1_start.year}年節大掃除－VIP定期客戶通知",
            notice,
            "待寄送" if target["email"] else "缺Email",
            "",
            _non_two_person_services(detail_rows),
            _dated_values(detail_rows, "note"),
            _dated_values(detail_rows, "status"),
            _service_reminder_text(
                target["name"], target["address"], month_rows, next_service,
                phase1_start, phase2_end, holiday,
                lunar_new_year_start, lunar_new_year_end,
            ),
        ])

    return sorted(output, key=lambda row: (str(row[2]), str(row[5])))


def _write_notice_sheet(
    gc: gspread.Client,
    target_id: str,
    year: int,
    rows: list[list[Any]],
) -> str:
    ss = gc.open_by_key(target_id)
    sheet_name = f"{year}大掃除定期VIP通知"
    try:
        sh = ss.worksheet(sheet_name)
        sh.clear()
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=sheet_name, rows=max(len(rows) + 20, 200), cols=len(NOTICE_HEADERS))

    sh.resize(rows=max(len(rows) + 20, 200), cols=len(NOTICE_HEADERS))
    values = [NOTICE_HEADERS] + rows
    sh.update(values=values, range_name="A1", value_input_option="USER_ENTERED")
    if rows:
        phones = [[str(row[3])] for row in rows]
        sh.update(values=phones, range_name=f"D2:D{len(rows) + 1}", value_input_option="RAW")
    sh.freeze(rows=1)
    return sheet_name


def save_nonroutine_notice(
    name: str,
    email: str,
    line_url: str,
    notice: str,
    year: int,
    target_spreadsheet_id: str = "",
) -> str:
    if not name.strip() or not email.strip():
        raise ValueError("姓名與 Email 為必填")
    gc = _output_gc()
    target_id = target_spreadsheet_id.strip() or _load_target_file_id()
    if not target_id:
        raise EnvironmentError("尚未設定客服排程系統目標試算表 ID")
    ss = gc.open_by_key(target_id)
    sheet_name = f"{year}大掃除非定期VIP通知"
    try:
        sh = ss.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=sheet_name, rows=500, cols=8)
        sh.append_row(["客戶類型", "姓名", "Email", "Email主旨", "通知內容", "LINE連結", "寄送狀態", "寄送時間"])
        sh.freeze(rows=1)
    subject = f"【檸檬家事服務】{year}年節大掃除－VIP優先預約通知"
    sh.append_row(
        ["非定期VIP", name.strip(), email.strip(), subject, notice, line_url.strip(), "待寄送", ""],
        value_input_option="USER_ENTERED",
    )
    return sheet_name


def _get_or_create_sheet(
    ss: Any,
    title: str,
    rows: int,
    cols: int,
):
    try:
        return ss.worksheet(title)
    except gspread.WorksheetNotFound:
        return ss.add_worksheet(title=title, rows=rows, cols=cols)


def _settings_data_rows(values: list[list[Any]]) -> list[list[Any]]:
    if values and list(values[0][: len(SETTINGS_HEADERS)]) == SETTINGS_HEADERS:
        return values[1:]
    return values


def _canonical_settings_values(
    values: list[list[Any]],
    settings: DeepCleanSettings,
) -> list[list[Any]]:
    """Restore the header and keep only the latest row for each season year."""
    rows_by_year: dict[int, list[Any]] = {}
    for row in _settings_data_rows(values):
        if not row:
            continue
        try:
            year = int(str(row[0]).strip())
        except (TypeError, ValueError):
            continue
        try:
            rows_by_year[year] = _settings_row(_settings_from_row(row))
        except (TypeError, ValueError):
            rows_by_year[year] = (list(row) + [""] * len(SETTINGS_HEADERS))[: len(SETTINGS_HEADERS)]
    rows_by_year[settings.season_year] = _settings_row(settings)
    return [SETTINGS_HEADERS] + [rows_by_year[year] for year in sorted(rows_by_year)]


def save_deep_clean_settings(
    settings: DeepCleanSettings,
    settings_spreadsheet_id: str = DEFAULT_SETTINGS_SPREADSHEET_ID,
) -> str:
    _validate_settings(settings)
    gc = _output_gc()
    ss = gc.open_by_key(settings_spreadsheet_id.strip() or DEFAULT_SETTINGS_SPREADSHEET_ID)
    sh = _get_or_create_sheet(ss, SETTINGS_SHEET_NAME, 100, len(SETTINGS_HEADERS))
    values = sh.get_all_values()
    canonical_values = _canonical_settings_values(values, settings)
    sh.clear()
    sh.update(values=canonical_values, range_name="A1", value_input_option="USER_ENTERED")
    sh.format(
        "A1:T1",
        {
            "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93},
            "textFormat": {"bold": True},
            "wrapStrategy": "WRAP",
        },
    )
    sh.freeze(rows=1)
    _write_system_update_sheet(ss, settings)
    _write_engineer_system_sheet(ss, settings)
    return SETTINGS_SHEET_NAME


def load_deep_clean_settings(
    season_year: int,
    settings_spreadsheet_id: str = DEFAULT_SETTINGS_SPREADSHEET_ID,
) -> DeepCleanSettings:
    gc = _output_gc()
    ss = gc.open_by_key(settings_spreadsheet_id.strip() or DEFAULT_SETTINGS_SPREADSHEET_ID)
    try:
        values = ss.worksheet(SETTINGS_SHEET_NAME).get_all_values()
    except gspread.WorksheetNotFound as exc:
        raise ValueError(f"尚未建立 {season_year} 年度大掃除設定") from exc
    for row in _settings_data_rows(values):
        if row and str(row[0]).strip() == str(season_year):
            settings = _settings_from_row(row)
            _validate_settings(settings)
            return settings
    raise ValueError(
        f"找不到 {season_year} 年度大掃除設定；"
        "請先回到客服排程系統，執行「【大掃除】年度設定」"
    )


def _write_system_update_sheet(ss: Any, settings: DeepCleanSettings) -> str:
    title = f"{settings.season_year}大掃除系統更新"
    sh = _get_or_create_sheet(ss, title, 100, 9)
    if getattr(sh, "col_count", 9) < 9:
        sh.resize(cols=9)
    comparison_headers = [
        "時數",
        "PART1 非VIP平日",
        "PART1 VIP平日",
        "PART1 非VIP週末",
        "PART1 VIP週末",
        "PART2 非VIP平日",
        "PART2 VIP平日",
        "PART2 非VIP週末",
        "PART2 VIP週末",
    ]
    rows = [
        ["分類", "設定項目", "內容", "系統處理方式"],
        ["說明", "完整大掃除期間", _date_range(settings.phase1_start, settings.phase2_end), "未特別註明 PART 時，一律顯示完整區間"],
        ["說明", "基本時數", f"2 人 {MINIMUM_SERVICE_HOURS} 小時起", "最低 6 人時"],
        ["說明", "服務內容", "大掃除服務主要為方便需求6–8小時客戶，服務內容同居家清潔，以人時計價。", "依實際人數 × 時數計價"],
        ["基準價", "非大掃除期間平日／週末", f"{_money(BASE_WEEKDAY_RATE)}／{_money(BASE_WEEKEND_RATE)}", "每 2 人 1 小時"],
        ["期間", "PART 1", _date_range(settings.phase1_start, settings.phase1_end), "日曆服務落在此區間者依 PART 1 計價"],
        ["期間", "PART 2", _date_range(settings.phase2_start, settings.phase2_end), "日曆服務落在此區間者依 PART 2 計價"],
        ["期間", "農曆年休假暫停服務", _date_range(settings.lunar_new_year_start, settings.lunar_new_year_end) if settings.lunar_new_year_start and settings.lunar_new_year_end else "未設定", "休假區間內服務一律標示暫停且不計費"],
        ["價格", "PART 1 VIP 平日／週末", f"{_money(settings.phase1_weekday_rate)}／{_money(settings.phase1_weekend_rate)}", "週一～週五／週六＋週日"],
        ["價格", "PART 1 非VIP 平日／週末", f"{_money(settings.phase1_nonvip_weekday_rate)}／{_money(settings.phase1_nonvip_weekend_rate)}", "週一～週五／週六＋週日"],
        ["價格", "PART 2 VIP 平日／週末", f"{_money(settings.phase2_weekday_rate)}／{_money(settings.phase2_weekend_rate)}", "週一～週五／週六＋週日"],
        ["價格", "PART 2 非VIP 平日／週末", f"{_money(settings.phase2_nonvip_weekday_rate)}／{_money(settings.phase2_nonvip_weekend_rate)}", "週一～週五／週六＋週日"],
        ["定期VIP", "回覆截止", settings.reply_deadline or "未設定", "從 Google Calendar 更新服務日期、次數、加價與年後首次服務"],
        ["非定期VIP", "優先預約", _date_range(settings.booking_start, settings.booking_end), "後台儲值金匯出名單－日曆定期VIP"],
        ["名單比對", "比對鍵", "電話優先，姓名輔助", "避免同名或格式差異造成誤判"],
        ["Email合併", "輸出檔", settings.notice_spreadsheet_id, "產生主旨、通知內容、LINE連結、寄送狀態與時間欄位"],
        [],
        ["年節加價比較表"] + [""] * 8,
        comparison_headers,
        *[[f"2人{hours}小時", *_comparison_values(settings, hours, False)] for hours in COMPARISON_HOURS],
        [],
        ["大掃除服務總額比較表（基準價＋年節加價）"] + [""] * 8,
        comparison_headers,
        *[[f"2人{hours}小時", *_comparison_values(settings, hours, True)] for hours in COMPARISON_HOURS],
    ]
    sh.clear()
    sh.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")
    sh.freeze(rows=1)
    return title


def _write_engineer_system_sheet(ss: Any, settings: DeepCleanSettings) -> str:
    """Generate the values an engineer should apply to the existing 「系統」 sheet."""
    title = f"{settings.season_year}系統工作表修改資料"
    sh = _get_or_create_sheet(ss, title, 100, 6)
    if getattr(sh, "col_count", 6) < 6:
        sh.resize(cols=6)
    tax = lambda value: f"{int(round(value)):,}元(含稅)"
    rows = [[""] * 6 for _ in range(14)]
    rows[0] = ["工程師修改『系統』工作表資料", "項目", "設定內容", "客戶／階段", "平日", "週末"]
    rows[1][1:3] = ["大掃除期間", _date_range(settings.phase1_start, settings.phase2_end)]
    rows[2][1:3] = ["大掃除PART1期間", _date_range(settings.phase1_start, settings.phase1_end)]
    rows[3][1:3] = ["大掃除PART2期間", _date_range(settings.phase2_start, settings.phase2_end)]
    rows[4][3:6] = ["VIP PART1", tax(settings.phase1_weekday_rate), tax(settings.phase1_weekend_rate)]
    rows[5][3:6] = ["VIP PART2", tax(settings.phase2_weekday_rate), tax(settings.phase2_weekend_rate)]
    rows[7][3:6] = ["一般 PART1", tax(settings.phase1_nonvip_weekday_rate), tax(settings.phase1_nonvip_weekend_rate)]
    rows[8][3:6] = ["一般 PART2", tax(settings.phase2_nonvip_weekday_rate), tax(settings.phase2_nonvip_weekend_rate)]
    rows[9][1:3] = [
        "農曆年休假暫停服務日期",
        _date_range(settings.lunar_new_year_start, settings.lunar_new_year_end)
        if settings.lunar_new_year_start and settings.lunar_new_year_end else "尚未設定",
    ]
    rows[12][1:3] = ["VIP開放期間", _date_range(settings.booking_start, settings.booking_end)]
    rows[13][1:3] = ["一般客開放期間", "尚未設定"]
    sh.clear()
    sh.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")
    sh.format(
        "A1:F1",
        {
            "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93},
            "textFormat": {"bold": True},
            "wrapStrategy": "WRAP",
        },
    )
    sh.freeze(rows=1)
    return title


def append_master_execution_log(
    master_spreadsheet_id: str,
    season_year: int,
    action: str,
    area: str,
    status: str,
    detail: str,
) -> None:
    try:
        ss = _gc().open_by_key(master_spreadsheet_id.strip() or DEFAULT_MASTER_SPREADSHEET_ID)
        sh = ss.worksheet("客服排程執行Log")
        sh.append_row([
            datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
            "客服排程系統",
            f"大掃除／{action}",
            "手動",
            area,
            str(season_year),
            "年度大掃除試算表",
            "",
            status,
            detail,
        ])
    except Exception:
        # 主功能成功時，Log 寫入失敗不應破壞已產出的名單。
        pass


def generate_notice_data(
    area: str,
    phase1_start: datetime,
    phase1_end: datetime,
    phase2_start: datetime,
    phase2_end: datetime,
    phase1_weekday_rate: float,
    phase1_weekend_rate: float,
    phase2_weekday_rate: float,
    phase2_weekend_rate: float,
    reply_deadline: str = "",
    target_spreadsheet_id: str = "",
    lunar_new_year_start: datetime | None = None,
    lunar_new_year_end: datetime | None = None,
) -> dict[str, Any]:
    if phase1_start > phase1_end or phase2_start > phase2_end:
        raise ValueError("階段開始日期不可晚於結束日期")
    if phase1_end >= phase2_start:
        raise ValueError("第一階段結束日期必須早於第二階段開始日期")
    rates = [phase1_weekday_rate, phase1_weekend_rate, phase2_weekday_rate, phase2_weekend_rate]
    if any(rate < 0 for rate in rates):
        raise ValueError("年節加價不可為負數")

    overall_start, _ = _month_bounds(phase1_start, phase2_end)
    overall_end = max(phase1_end, phase2_end) + timedelta(days=90)
    source_gc = _gc()
    output_gc = _output_gc()
    target_id = target_spreadsheet_id.strip() or _load_target_file_id()
    if not target_id:
        raise EnvironmentError("尚未設定客服排程系統目標試算表 ID")

    cal = _calendar_service()
    all_rows: list[list[Any]] = []
    counts: dict[str, int] = {}
    for area_cfg in load_area_config(source_gc, filter_area=area):
        area_name = area_cfg["name"]
        calendar_id = area_cfg.get("calendar_id", "")
        if not calendar_id:
            continue
        events = _fetch_calendar_events(cal, calendar_id, overall_start, overall_end, area_name)
        rows = _process_events(events, area_name)
        stored_info = _load_stored_value_info(source_gc, area_name, area_cfg.get("target_spreadsheet_id", ""))
        notice_rows = build_notice_rows(
            area_name,
            rows,
            stored_info,
            phase1_start,
            phase1_end,
            phase2_start,
            phase2_end,
            phase1_weekday_rate,
            phase1_weekend_rate,
            phase2_weekday_rate,
            phase2_weekend_rate,
            reply_deadline,
            lunar_new_year_start,
            lunar_new_year_end,
        )
        counts[area_name] = len(notice_rows)
        all_rows.extend(notice_rows)

    year = phase1_start.year
    sheet_name = _write_notice_sheet(output_gc, target_id, year, all_rows)
    return {"sheet": sheet_name, "count": len(all_rows), "areas": counts, "rows": all_rows}


def _regular_identity_sets(regular_rows: list[list[Any]]) -> tuple[set[str], set[str]]:
    phones = {
        normalize_member_phone(row[3])
        for row in regular_rows
        if len(row) > 3 and normalize_member_phone(row[3])
    }
    names = {
        normalize_name_for_compare(str(row[2]))
        for row in regular_rows
        if (
            len(row) > 3
            and not normalize_member_phone(row[3])
            and normalize_name_for_compare(str(row[2]))
        )
    }
    return phones, names


def build_nonroutine_notice_rows(
    members: list[dict[str, Any]],
    regular_rows: list[list[Any]],
    settings: DeepCleanSettings,
) -> list[list[Any]]:
    regular_phones, regular_names = _regular_identity_sets(regular_rows)
    output: list[list[Any]] = []
    seen: set[str] = set()
    for member in members:
        phone = normalize_member_phone(member.get("phone"))
        name = str(member.get("name") or "").strip()
        normalized_name = normalize_name_for_compare(name)
        if (phone and phone in regular_phones) or (normalized_name and normalized_name in regular_names):
            continue
        unique_key = str(member.get("member_id") or "").strip() or phone or str(member.get("email") or "").lower()
        if not unique_key or unique_key in seen:
            continue
        seen.add(unique_key)
        notice = build_nonroutine_notice(
            name,
            settings.phase1_start,
            settings.phase1_end,
            settings.phase2_start,
            settings.phase2_end,
            settings.phase1_weekday_rate,
            settings.phase1_weekend_rate,
            settings.phase2_weekday_rate,
            settings.phase2_weekend_rate,
            settings.booking_start,
            settings.booking_end,
        )
        line_url = str(member.get("line_url") or "").strip()
        line_cell = f'=HYPERLINK("{line_url}","開啟 LINE")' if re.match(r"^https?://", line_url) else line_url
        email = str(member.get("email") or "").strip()
        output.append([
            str(member.get("area") or ""),
            "非定期VIP",
            name,
            phone,
            email,
            str(member.get("address") or ""),
            line_cell,
            str(member.get("member_level") or ""),
            float(member.get("stored_value") or 0),
            f"【檸檬家事服務】{settings.season_year}年節大掃除－VIP優先預約通知",
            notice,
            "待寄送" if email else "缺Email",
            "",
        ])
    return sorted(output, key=lambda row: (str(row[0]), str(row[2]), str(row[3])))


def _write_nonroutine_notice_sheet(
    gc: gspread.Client,
    target_id: str,
    year: int,
    rows: list[list[Any]],
) -> str:
    ss = gc.open_by_key(target_id)
    sheet_name = f"{year}大掃除非定期VIP通知"
    sh = _get_or_create_sheet(ss, sheet_name, max(len(rows) + 20, 200), len(NONROUTINE_NOTICE_HEADERS))
    sh.clear()
    sh.update(
        values=[NONROUTINE_NOTICE_HEADERS] + rows,
        range_name="A1",
        value_input_option="USER_ENTERED",
    )
    if rows:
        phones = [[str(row[3])] for row in rows]
        sh.update(values=phones, range_name=f"D2:D{len(rows) + 1}", value_input_option="RAW")
    sh.freeze(rows=1)
    return sheet_name


def update_all_vip_notices(
    season_year: int,
    area: str = "全區",
    settings_spreadsheet_id: str = DEFAULT_SETTINGS_SPREADSHEET_ID,
    notice_spreadsheet_id: str = "",
) -> dict[str, Any]:
    settings = load_deep_clean_settings(season_year, settings_spreadsheet_id)
    target_notice_id = notice_spreadsheet_id.strip() or settings.notice_spreadsheet_id
    regular = generate_notice_data(
        area,
        settings.phase1_start,
        settings.phase1_end,
        settings.phase2_start,
        settings.phase2_end,
        settings.phase1_weekday_rate,
        settings.phase1_weekend_rate,
        settings.phase2_weekday_rate,
        settings.phase2_weekend_rate,
        settings.reply_deadline,
        target_notice_id,
        settings.lunar_new_year_start,
        settings.lunar_new_year_end,
    )

    target_areas = list(regular["areas"].keys())
    if area != "全區" and area not in target_areas:
        target_areas = [area]
    members: list[dict[str, Any]] = []
    backend_counts: dict[str, int] = {}
    for area_name in target_areas:
        area_members = export_stored_value_members(area_name)
        backend_counts[area_name] = len(area_members)
        members.extend(area_members)

    nonroutine_rows = build_nonroutine_notice_rows(members, regular["rows"], settings)
    gc = _output_gc()
    nonroutine_sheet = _write_nonroutine_notice_sheet(
        gc,
        target_notice_id,
        settings.season_year,
        nonroutine_rows,
    )
    return {
        "regular_sheet": regular["sheet"],
        "regular_count": regular["count"],
        "nonroutine_sheet": nonroutine_sheet,
        "nonroutine_count": len(nonroutine_rows),
        "backend_counts": backend_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="建立大掃除 VIP 通知資料")
    parser.add_argument("--mode", choices=["regular", "save-settings", "update-all"], default="regular")
    parser.add_argument("--area", choices=["全區", "台北", "台中"], default="全區")
    parser.add_argument("--season-year", type=int)
    parser.add_argument("--phase1-start")
    parser.add_argument("--phase1-end")
    parser.add_argument("--phase2-start")
    parser.add_argument("--phase2-end")
    parser.add_argument("--phase1-weekday-rate", type=float)
    parser.add_argument("--phase1-weekend-rate", type=float)
    parser.add_argument("--phase1-nonvip-weekday-rate", type=float, default=0)
    parser.add_argument("--phase1-nonvip-weekend-rate", type=float, default=0)
    parser.add_argument("--phase2-weekday-rate", type=float)
    parser.add_argument("--phase2-weekend-rate", type=float)
    parser.add_argument("--phase2-nonvip-weekday-rate", type=float, default=0)
    parser.add_argument("--phase2-nonvip-weekend-rate", type=float, default=0)
    parser.add_argument("--reply-deadline", default="")
    parser.add_argument("--booking-start")
    parser.add_argument("--booking-end")
    parser.add_argument("--lunar-new-year-start")
    parser.add_argument("--lunar-new-year-end")
    parser.add_argument("--master-spreadsheet-id", default=DEFAULT_MASTER_SPREADSHEET_ID)
    parser.add_argument("--settings-spreadsheet-id", default="")
    parser.add_argument("--notice-spreadsheet-id", default="")
    parser.add_argument("--target-spreadsheet-id", default="")
    args = parser.parse_args()

    if args.mode in {"save-settings", "update-all"}:
        if not args.season_year:
            parser.error("--season-year 為必填")
        master_settings_id, master_notice_id = resolve_deep_clean_sheet_ids(
            args.season_year,
            args.master_spreadsheet_id,
        )
    else:
        master_settings_id = DEFAULT_SETTINGS_SPREADSHEET_ID
        master_notice_id = DEFAULT_NOTICE_SPREADSHEET_ID
    settings_spreadsheet_id = args.settings_spreadsheet_id.strip() or master_settings_id
    notice_spreadsheet_id = args.notice_spreadsheet_id.strip() or master_notice_id

    if args.mode == "update-all":
        result = update_all_vip_notices(
            args.season_year,
            args.area,
            settings_spreadsheet_id,
            notice_spreadsheet_id,
        )
        append_master_execution_log(
            args.master_spreadsheet_id,
            args.season_year,
            "更新VIP通知清單",
            args.area,
            "成功",
            f"定期 {result['regular_count']} 位／非定期 {result['nonroutine_count']} 位",
        )
        print(
            f"大掃除 VIP 通知更新完成："
            f"定期 {result['regular_count']} 位／非定期 {result['nonroutine_count']} 位"
        )
        print(f"定期清單：{result['regular_sheet']}")
        print(f"非定期清單：{result['nonroutine_sheet']}")
        return

    required_values = {
        "--phase1-start": args.phase1_start,
        "--phase1-end": args.phase1_end,
        "--phase2-start": args.phase2_start,
        "--phase2-end": args.phase2_end,
        "--phase1-weekday-rate": args.phase1_weekday_rate,
        "--phase1-weekend-rate": args.phase1_weekend_rate,
        "--phase2-weekday-rate": args.phase2_weekday_rate,
        "--phase2-weekend-rate": args.phase2_weekend_rate,
    }
    missing = [name for name, value in required_values.items() if value is None or value == ""]
    if missing:
        parser.error(f"缺少參數：{', '.join(missing)}")

    if args.mode == "save-settings":
        if not args.season_year or not args.booking_start or not args.booking_end:
            parser.error("save-settings 需要 --season-year、--booking-start、--booking-end")
        settings = DeepCleanSettings(
            season_year=args.season_year,
            phase1_start=_parse_date(args.phase1_start),
            phase1_end=_parse_date(args.phase1_end, end_of_day=True),
            phase1_weekday_rate=args.phase1_weekday_rate,
            phase1_weekend_rate=args.phase1_weekend_rate,
            phase2_start=_parse_date(args.phase2_start),
            phase2_end=_parse_date(args.phase2_end, end_of_day=True),
            phase2_weekday_rate=args.phase2_weekday_rate,
            phase2_weekend_rate=args.phase2_weekend_rate,
            reply_deadline=args.reply_deadline,
            booking_start=_parse_date(args.booking_start),
            booking_end=_parse_date(args.booking_end, end_of_day=True),
            notice_spreadsheet_id=notice_spreadsheet_id,
            phase1_nonvip_weekday_rate=args.phase1_nonvip_weekday_rate,
            phase1_nonvip_weekend_rate=args.phase1_nonvip_weekend_rate,
            phase2_nonvip_weekday_rate=args.phase2_nonvip_weekday_rate,
            phase2_nonvip_weekend_rate=args.phase2_nonvip_weekend_rate,
            lunar_new_year_start=_parse_date(args.lunar_new_year_start) if args.lunar_new_year_start else None,
            lunar_new_year_end=_parse_date(args.lunar_new_year_end, end_of_day=True) if args.lunar_new_year_end else None,
        )
        sheet_name = save_deep_clean_settings(settings, settings_spreadsheet_id)
        append_master_execution_log(
            args.master_spreadsheet_id,
            args.season_year,
            "年度設定",
            "全區",
            "成功",
            f"已更新 {sheet_name}、{args.season_year}大掃除系統更新與系統工作表修改資料",
        )
        print(f"{args.season_year} 年度大掃除設定已儲存：{sheet_name}")
        print(f"系統更新內容已同步：{args.season_year}大掃除系統更新")
        print(f"工程師修改資料已同步：{args.season_year}系統工作表修改資料")
        return

    result = generate_notice_data(
        args.area,
        _parse_date(args.phase1_start),
        _parse_date(args.phase1_end, end_of_day=True),
        _parse_date(args.phase2_start),
        _parse_date(args.phase2_end, end_of_day=True),
        args.phase1_weekday_rate,
        args.phase1_weekend_rate,
        args.phase2_weekday_rate,
        args.phase2_weekend_rate,
        args.reply_deadline,
        args.target_spreadsheet_id or notice_spreadsheet_id,
        _parse_date(args.lunar_new_year_start) if args.lunar_new_year_start else None,
        _parse_date(args.lunar_new_year_end, end_of_day=True) if args.lunar_new_year_end else None,
    )
    print(f"大掃除通知資料完成：{result['sheet']}，共 {result['count']} 位客戶")
    for area_name, count in result["areas"].items():
        print(f"{area_name}：{count} 位")


if __name__ == "__main__":
    main()
