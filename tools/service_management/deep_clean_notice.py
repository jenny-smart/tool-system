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
    "姓名",
    "電話",
    "地址",
    "LINE連結",
    "第一階段服務",
    "第二階段服務",
    "第一階段加價",
    "第二階段加價",
    "通知內容",
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


def _service_label(row: dict[str, Any]) -> str:
    return (
        f"{row['date_str']}（週{row['weekday']}）"
        f" {row['start_str']}–{row['end_str']}"
        f"｜{row['service'] or '定期清潔'}"
    )


def _extra_charge(row: dict[str, Any], rate_per_two_person_hour: float) -> float:
    # 加價單位沿用年節規則：每 2 人 1 小時一個單位。
    return rate_per_two_person_hour * (float(row.get("person_hrs") or 0) / 2.0)


def _build_notice_text(
    name: str,
    phase1_start: datetime,
    phase1_end: datetime,
    phase2_start: datetime,
    phase2_end: datetime,
    phase1_rate: float,
    phase2_rate: float,
    phase1_rows: list[dict[str, Any]],
    phase2_rows: list[dict[str, Any]],
    phase1_total: float,
    phase2_total: float,
) -> str:
    p1_services = "\n".join(f"・{_service_label(row)}" for row in phase1_rows) or "・此階段目前無排程"
    p2_services = "\n".join(f"・{_service_label(row)}" for row in phase2_rows) or "・此階段目前無排程"
    return (
        f"❤️親愛的 {name} 您好：\n\n"
        "感謝您長期支持檸檬家事服務。以下為您的年節大掃除服務安排：\n\n"
        f"📍第一階段 {phase1_start:%Y/%m/%d}～{phase1_end:%Y/%m/%d}\n"
        f"每 2 人 1 小時加價 {_money(phase1_rate)}\n"
        f"{p1_services}\n"
        f"本階段依目前排程加價合計：{_money(phase1_total)}\n\n"
        f"📍第二階段 {phase2_start:%Y/%m/%d}～{phase2_end:%Y/%m/%d}\n"
        f"每 2 人 1 小時加價 {_money(phase2_rate)}\n"
        f"{p2_services}\n"
        f"本階段依目前排程加價合計：{_money(phase2_total)}\n\n"
        "若上述時間需要調整，請直接於 LINE 告知客服，謝謝您。"
    )


def build_notice_rows(
    area_name: str,
    rows: list[dict[str, Any]],
    stored_info: dict[str, dict],
    phase1_start: datetime,
    phase1_end: datetime,
    phase2_start: datetime,
    phase2_end: datetime,
    phase1_rate: float,
    phase2_rate: float,
) -> list[list[Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"phase1": [], "phase2": [], "phone": "", "line": "", "name": "", "address": ""}
    )

    for row in rows:
        service_dt = row["start_dt"]
        if not (phase1_start <= service_dt <= phase1_end or phase2_start <= service_dt <= phase2_end):
            continue

        name = str(row.get("name") or "").strip()
        address = str(row.get("address") or "").strip()
        key = (normalize_name_for_compare(name), normalize_address(address))
        target = grouped[key]
        target["name"] = name
        target["address"] = address
        target["phone"] = target["phone"] or str(row.get("phone") or "")

        stored = stored_info.get(normalize_name_for_compare(name), {})
        target["line"] = target["line"] or str(stored.get("lineValue") or "")

        if phase1_start <= service_dt <= phase1_end:
            target["phase1"].append(row)
        if phase2_start <= service_dt <= phase2_end:
            target["phase2"].append(row)

    output: list[list[Any]] = []
    for target in grouped.values():
        p1 = sorted(target["phase1"], key=lambda row: row["start_dt"])
        p2 = sorted(target["phase2"], key=lambda row: row["start_dt"])
        p1_total = sum(_extra_charge(row, phase1_rate) for row in p1)
        p2_total = sum(_extra_charge(row, phase2_rate) for row in p2)
        notice = _build_notice_text(
            target["name"],
            phase1_start,
            phase1_end,
            phase2_start,
            phase2_end,
            phase1_rate,
            phase2_rate,
            p1,
            p2,
            p1_total,
            p2_total,
        )
        line_url = target["line"]
        line_cell = f'=HYPERLINK("{line_url}","開啟 LINE")' if re.match(r"^https?://", line_url) else line_url
        output.append([
            area_name,
            target["name"],
            target["phone"],
            target["address"],
            line_cell,
            "\n".join(_service_label(row) for row in p1),
            "\n".join(_service_label(row) for row in p2),
            p1_total,
            p2_total,
            notice,
        ])

    return sorted(output, key=lambda row: (str(row[1]), str(row[3])))


def _write_notice_sheet(
    gc: gspread.Client,
    target_id: str,
    year: int,
    rows: list[list[Any]],
) -> str:
    ss = gc.open_by_key(target_id)
    sheet_name = f"{year}大掃除通知資料"
    try:
        sh = ss.worksheet(sheet_name)
        sh.clear()
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=sheet_name, rows=max(len(rows) + 20, 200), cols=len(NOTICE_HEADERS))

    values = [NOTICE_HEADERS] + rows
    sh.update(values=values, range_name="A1", value_input_option="USER_ENTERED")
    if rows:
        phones = [[str(row[2])] for row in rows]
        sh.update(values=phones, range_name=f"C2:C{len(rows) + 1}", value_input_option="RAW")
    sh.freeze(rows=1)
    return sheet_name


def generate_notice_data(
    area: str,
    phase1_start: datetime,
    phase1_end: datetime,
    phase2_start: datetime,
    phase2_end: datetime,
    phase1_rate: float,
    phase2_rate: float,
) -> dict[str, Any]:
    if phase1_start > phase1_end or phase2_start > phase2_end:
        raise ValueError("階段開始日期不可晚於結束日期")
    if phase1_rate < 0 or phase2_rate < 0:
        raise ValueError("加價金額不可為負數")

    overall_start = min(phase1_start, phase2_start)
    overall_end = max(phase1_end, phase2_end)
    gc = _gc()
    target_id = _load_target_file_id()
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
            phase1_rate,
            phase2_rate,
        )
        counts[area_name] = len(notice_rows)
        all_rows.extend(notice_rows)

    year = phase2_end.year
    sheet_name = _write_notice_sheet(gc, target_id, year, all_rows)
    return {"sheet": sheet_name, "count": len(all_rows), "areas": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="建立大掃除 VIP 通知資料")
    parser.add_argument("--area", choices=["全區", "台北", "台中"], default="全區")
    parser.add_argument("--phase1-start", required=True)
    parser.add_argument("--phase1-end", required=True)
    parser.add_argument("--phase2-start", required=True)
    parser.add_argument("--phase2-end", required=True)
    parser.add_argument("--phase1-rate", required=True, type=float)
    parser.add_argument("--phase2-rate", required=True, type=float)
    args = parser.parse_args()

    result = generate_notice_data(
        args.area,
        _parse_date(args.phase1_start),
        _parse_date(args.phase1_end, end_of_day=True),
        _parse_date(args.phase2_start),
        _parse_date(args.phase2_end, end_of_day=True),
        args.phase1_rate,
        args.phase2_rate,
    )
    print(f"大掃除通知資料完成：{result['sheet']}，共 {result['count']} 位客戶")
    for area_name, count in result["areas"].items():
        print(f"{area_name}：{count} 位")


if __name__ == "__main__":
    main()
