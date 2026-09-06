from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import gspread

from tools.service_management.stored_value import (
    TZ_TAIPEI,
    _calendar_service,
    _fetch_calendar_events,
    _gc,
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
    gc = _gc()
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
    gc = _gc()
    target_id = target_spreadsheet_id.strip() or _load_target_file_id()
    if not target_id:
        raise EnvironmentError("尚未設定客服排程系統目標試算表 ID")

    cal = _calendar_service()
    all_rows: list[list[Any]] = []
    counts: dict[str, int] = {}
    for area_cfg in load_area_config(gc, filter_area=area):
        area_name = area_cfg["name"]
        calendar_id = area_cfg.get("calendar_id", "")
        if not calendar_id:
            continue
        events = _fetch_calendar_events(cal, calendar_id, overall_start, overall_end, area_name)
        rows = _process_events(events, area_name)
        stored_info = _load_stored_value_info(gc, area_name, area_cfg.get("target_spreadsheet_id", ""))
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
    sheet_name = _write_notice_sheet(gc, target_id, year, all_rows)
    return {"sheet": sheet_name, "count": len(all_rows), "areas": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="建立大掃除 VIP 通知資料")
    parser.add_argument("--area", choices=["全區", "台北", "台中"], default="全區")
    parser.add_argument("--phase1-start", required=True)
    parser.add_argument("--phase1-end", required=True)
    parser.add_argument("--phase2-start", required=True)
    parser.add_argument("--phase2-end", required=True)
    parser.add_argument("--phase1-weekday-rate", required=True, type=float)
    parser.add_argument("--phase1-weekend-rate", required=True, type=float)
    parser.add_argument("--phase2-weekday-rate", required=True, type=float)
    parser.add_argument("--phase2-weekend-rate", required=True, type=float)
    parser.add_argument("--reply-deadline", default="")
    parser.add_argument("--target-spreadsheet-id", default="")
    args = parser.parse_args()

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
        args.target_spreadsheet_id,
    )
    print(f"大掃除通知資料完成：{result['sheet']}，共 {result['count']} 位客戶")
    for area_name, count in result["areas"].items():
        print(f"{area_name}：{count} 位")


if __name__ == "__main__":
    main()
