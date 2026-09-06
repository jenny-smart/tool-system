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
    "PART1平日加價",
    "PART1週末加價",
    "PART2開始",
    "PART2結束",
    "PART2平日加價",
    "PART2週末加價",
    "定期VIP回覆截止",
    "非定期VIP開放",
    "非定期VIP截止",
    "通知輸出試算表ID",
    "更新時間",
]

DEFAULT_SETTINGS_SPREADSHEET_ID = "1u9boCPWeQk2yWVJ4an7GH0DXNHuLDA0sjp9jKJEG1v8"
DEFAULT_NOTICE_SPREADSHEET_ID = "1AsU4YF6t8gt-lVb0p4C656CWdbveWkuHetPe1nwC7BE"
DEFAULT_MASTER_SPREADSHEET_ID = "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
SETTINGS_SHEET_NAME = "大掃除設定"
MASTER_ID_SHEET_NAME = "大掃除設定"


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


def _validate_settings(settings: DeepCleanSettings) -> None:
    if settings.phase1_start > settings.phase1_end or settings.phase2_start > settings.phase2_end:
        raise ValueError("階段開始日期不可晚於結束日期")
    if settings.phase1_end >= settings.phase2_start:
        raise ValueError("第一階段結束日期必須早於第二階段開始日期")
    if settings.booking_start > settings.booking_end:
        raise ValueError("非定期 VIP 開放預約日不可晚於截止日")
    rates = [
        settings.phase1_weekday_rate,
        settings.phase1_weekend_rate,
        settings.phase2_weekday_rate,
        settings.phase2_weekend_rate,
    ]
    if any(rate <= 0 for rate in rates):
        raise ValueError("四項年節加價尚未全部設定")


def _settings_row(settings: DeepCleanSettings) -> list[Any]:
    return [
        settings.season_year,
        settings.phase1_start.strftime("%Y-%m-%d"),
        settings.phase1_end.strftime("%Y-%m-%d"),
        settings.phase1_weekday_rate,
        settings.phase1_weekend_rate,
        settings.phase2_start.strftime("%Y-%m-%d"),
        settings.phase2_end.strftime("%Y-%m-%d"),
        settings.phase2_weekday_rate,
        settings.phase2_weekend_rate,
        settings.reply_deadline,
        settings.booking_start.strftime("%Y-%m-%d"),
        settings.booking_end.strftime("%Y-%m-%d"),
        settings.notice_spreadsheet_id,
        datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
    ]


def _settings_from_row(row: list[Any]) -> DeepCleanSettings:
    values = list(row) + [""] * max(0, len(SETTINGS_HEADERS) - len(row))
    return DeepCleanSettings(
        season_year=int(values[0]),
        phase1_start=_parse_date(str(values[1])),
        phase1_end=_parse_date(str(values[2]), end_of_day=True),
        phase1_weekday_rate=float(values[3]),
        phase1_weekend_rate=float(values[4]),
        phase2_start=_parse_date(str(values[5])),
        phase2_end=_parse_date(str(values[6]), end_of_day=True),
        phase2_weekday_rate=float(values[7]),
        phase2_weekend_rate=float(values[8]),
        reply_deadline=str(values[9] or ""),
        booking_start=_parse_date(str(values[10])),
        booking_end=_parse_date(str(values[11]), end_of_day=True),
        notice_spreadsheet_id=str(values[12] or DEFAULT_NOTICE_SPREADSHEET_ID).strip(),
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


def _service_label(row: dict[str, Any]) -> str:
    return (
        f"{row['date_str']}（週{row['weekday']}）"
        f" {row['start_str']}–{row['end_str']}"
        f"｜{row['service'] or '定期清潔'}"
    )


def _service_date_label(row: dict[str, Any]) -> str:
    return f"{row['date_str']}({row['weekday']})"


def _extra_charge(row: dict[str, Any], weekday_rate: float, weekend_rate: float) -> float:
    # 加價單位沿用年節規則：每 2 人 1 小時一個單位。
    rate = weekend_rate if row["start_dt"].weekday() >= 5 else weekday_rate
    return rate * (float(row.get("person_hrs") or 0) / 2.0)


def _price_text(start: datetime, end: datetime, weekday_rate: float, weekend_rate: float) -> str:
    return (
        f"{_date_range(start, end)}\n"
        f"平日（週一～週五）：每 2 人 1 小時酌收年節加價 {_money(weekday_rate)}（含稅）\n"
        f"週末（週六～週日）：每 2 人 1 小時酌收年節加價 {_money(weekend_rate)}（含稅）"
    )


def _build_notice_text(
    name: str,
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
    phase1_total: float,
    phase2_total: float,
    next_service: dict[str, Any] | None,
    reply_deadline: str,
) -> str:
    all_rows = phase1_rows + phase2_rows
    service_dates = ", ".join(_service_date_label(row) for row in all_rows) or "目前無排程"
    service_times = "、".join(sorted({f"{row['start_str']}–{row['end_str']}" for row in all_rows})) or "目前無排程"
    next_label = _service_date_label(next_service) if next_service else "目前尚無排程"
    deadline = f"建議您於 {reply_deadline} 前，" if reply_deadline.strip() else "如需調整，建議您儘早"
    return (
        f"❤️親愛的 {name} 您好：\n\n"
        "🎉 感謝您長期支持檸檬家事服務 🎉\n"
        f"年節大掃除期間：{_date_range(phase1_start, phase2_end)}\n"
        "✨VIP 專屬 — 優先預約年節大掃除服務正式開放✨\n\n"
        "🧽《VIP 定期客戶年節大掃除加價收費說明》\n"
        "大掃除期間，單次服務為 2 人 3 小時起\n\n"
        f"📍PART 1：{_price_text(phase1_start, phase1_end, phase1_weekday_rate, phase1_weekend_rate)}\n\n"
        f"📍PART 2：{_price_text(phase2_start, phase2_end, phase2_weekday_rate, phase2_weekend_rate)}\n\n"
        "💰「年節加價」將自動從您的「儲值金」帳戶扣除，無需現場付款；"
        "惟「車馬費」仍須於現場支付。\n\n"
        f"🕓 您原訂週期於年節期間（{_date_range(phase1_start, phase2_end)}）之服務安排如下：\n\n"
        f"年節加價服務日期：{service_dates}\n"
        f"服務時段：{service_times}\n\n"
        f"PART 1 次數／年節加價金額：{len(phase1_rows)} 次／{_money(phase1_total)}\n"
        f"PART 2 次數／年節加價金額：{len(phase2_rows)} 次／{_money(phase2_total)}\n\n"
        f"年節後第一次服務日期：{next_label}\n\n"
        "💛《VIP 定期客戶優先預約》\n"
        f"{deadline}透過官方 LINE@ 與我們聯繫。\n"
        "以上金額依目前排程估算；如改期，將依實際服務日期重新計算。\n"
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
        f"年節大掃除期間：{_date_range(phase1_start, phase2_end)}\n"
        "✨VIP 專屬 — 優先預約年節大掃除服務正式開放✨\n\n"
        "🧽《VIP 客戶年節大掃除加價收費說明》\n"
        "大掃除期間，單次服務為 2 人 3 小時起\n\n"
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
) -> list[list[Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "phase1": [], "phase2": [], "after": [], "phone": "", "email": "",
            "line": "", "name": "", "address": "",
        }
    )

    for row in rows:
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
        if not p1 and not p2:
            continue
        after = sorted(target["after"], key=lambda row: row["start_dt"])
        next_service = after[0] if after else None
        p1_total = sum(_extra_charge(row, phase1_weekday_rate, phase1_weekend_rate) for row in p1)
        p2_total = sum(_extra_charge(row, phase2_weekday_rate, phase2_weekend_rate) for row in p2)
        notice = _build_notice_text(
            target["name"],
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
            p1_total,
            p2_total,
            next_service,
            reply_deadline,
        )
        line_url = target["line"]
        line_cell = f'=HYPERLINK("{line_url}","開啟 LINE")' if re.match(r"^https?://", line_url) else line_url
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


def save_deep_clean_settings(
    settings: DeepCleanSettings,
    settings_spreadsheet_id: str = DEFAULT_SETTINGS_SPREADSHEET_ID,
) -> str:
    _validate_settings(settings)
    gc = _output_gc()
    ss = gc.open_by_key(settings_spreadsheet_id.strip() or DEFAULT_SETTINGS_SPREADSHEET_ID)
    sh = _get_or_create_sheet(ss, SETTINGS_SHEET_NAME, 100, len(SETTINGS_HEADERS))
    values = sh.get_all_values()
    if not values:
        sh.update(values=[SETTINGS_HEADERS], range_name="A1", value_input_option="USER_ENTERED")
        values = [SETTINGS_HEADERS]

    target_row = None
    for index, row in enumerate(values[1:], start=2):
        if row and str(row[0]).strip() == str(settings.season_year):
            target_row = index
            break
    row_values = _settings_row(settings)
    if target_row:
        sh.update(
            values=[row_values],
            range_name=f"A{target_row}:N{target_row}",
            value_input_option="USER_ENTERED",
        )
    else:
        sh.append_row(row_values, value_input_option="USER_ENTERED")
    sh.freeze(rows=1)
    _write_system_update_sheet(ss, settings)
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
    for row in values[1:]:
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
    sh = _get_or_create_sheet(ss, title, 100, 4)
    rows = [
        ["分類", "設定項目", "內容", "系統處理方式"],
        ["期間", "PART 1", _date_range(settings.phase1_start, settings.phase1_end), "日曆服務落在此區間者依 PART 1 計價"],
        ["期間", "PART 2", _date_range(settings.phase2_start, settings.phase2_end), "日曆服務落在此區間者依 PART 2 計價"],
        ["價格", "PART 1 平日／週末", f"{_money(settings.phase1_weekday_rate)}／{_money(settings.phase1_weekend_rate)}", "週一～週五／週六＋週日"],
        ["價格", "PART 2 平日／週末", f"{_money(settings.phase2_weekday_rate)}／{_money(settings.phase2_weekend_rate)}", "週一～週五／週六＋週日"],
        ["定期VIP", "回覆截止", settings.reply_deadline or "未設定", "從 Google Calendar 更新服務日期、次數、加價與年後首次服務"],
        ["非定期VIP", "優先預約", _date_range(settings.booking_start, settings.booking_end), "後台儲值金匯出名單－日曆定期VIP"],
        ["名單比對", "比對鍵", "電話優先，姓名輔助", "避免同名或格式差異造成誤判"],
        ["Email合併", "輸出檔", settings.notice_spreadsheet_id, "產生主旨、通知內容、LINE連結、寄送狀態與時間欄位"],
    ]
    sh.clear()
    sh.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")
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
) -> dict[str, Any]:
    if phase1_start > phase1_end or phase2_start > phase2_end:
        raise ValueError("階段開始日期不可晚於結束日期")
    if phase1_end >= phase2_start:
        raise ValueError("第一階段結束日期必須早於第二階段開始日期")
    rates = [phase1_weekday_rate, phase1_weekend_rate, phase2_weekday_rate, phase2_weekend_rate]
    if any(rate <= 0 for rate in rates):
        raise ValueError("四項年節加價尚未全部設定")

    overall_start = min(phase1_start, phase2_start)
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
    parser.add_argument("--phase2-weekday-rate", type=float)
    parser.add_argument("--phase2-weekend-rate", type=float)
    parser.add_argument("--reply-deadline", default="")
    parser.add_argument("--booking-start")
    parser.add_argument("--booking-end")
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
        )
        sheet_name = save_deep_clean_settings(settings, settings_spreadsheet_id)
        append_master_execution_log(
            args.master_spreadsheet_id,
            args.season_year,
            "年度設定",
            "全區",
            "成功",
            f"已更新 {sheet_name} 與 {args.season_year}大掃除系統更新",
        )
        print(f"{args.season_year} 年度大掃除設定已儲存：{sheet_name}")
        print(f"系統更新內容已同步：{args.season_year}大掃除系統更新")
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
    )
    print(f"大掃除通知資料完成：{result['sheet']}，共 {result['count']} 位客戶")
    for area_name, count in result["areas"].items():
        print(f"{area_name}：{count} 位")


if __name__ == "__main__":
    main()
