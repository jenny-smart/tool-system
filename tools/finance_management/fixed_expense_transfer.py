"""各區固定費用匯款：由行銷費用總表產生台北／分店富邦分錄。"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable

from googleapiclient.errors import HttpError

from tools.common.config_loader import get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.statement_registry import (
    resolve_marketing_expense_location,
    resolve_statement_location,
)

TOOL_NAME = "各區固定費用匯款"
AREAS = ("台中", "桃園", "新竹", "高雄")
TAOYUAN_SOURCE_AREAS = ("桃園", "家電")
REPORT_AREA_NAMES = {"家電": "桃園"}


def _cell(row: list[object], col: int) -> object:
    return row[col - 1] if col - 1 < len(row) else ""


def _amount(value: object) -> int:
    if value in (None, "") or isinstance(value, bool):
        return 0
    text = str(value).replace(",", "").replace("NT$", "").strip()
    if not text or text in ("-", "—"):
        return 0
    try:
        return int(Decimal(text).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise RuntimeError(f"固定費用金額不是數字：{value}") from exc


def _normalize_month(value: object) -> str | None:
    match = re.search(r"(20\d{2})[./-]?(0?[1-9]|1[0-2])", str(value or "").strip())
    return f"{match.group(1)}{int(match.group(2)):02d}" if match else None


def _find_source_column(values: list[list[object]], period: str, area: str) -> int:
    active_month = None
    width = max((len(row) for row in values[:2]), default=0)
    for col in range(1, width + 1):
        active_month = _normalize_month(_cell(values[0], col)) or active_month
        if active_month == period and str(_cell(values[1], col) or "").strip() == area:
            return col
    raise RuntimeError(f"行銷費用來源找不到 {period[:4]}.{period[4:]}／{area} 的交叉欄")


def _account(source: str, *, taipei: bool) -> str:
    if source == "2%":
        return "分店收入2%" if taipei else "分店支出2%"
    return source


def _label_subject(source: str) -> str:
    return "google服務費" if source in ("2%", "稅捐") else source


def _posting(source: str, amount: int, *, taipei: bool) -> tuple[int, int]:
    if source == "2%":
        return (0, amount) if taipei else (amount, 0)
    if source == "稅捐":
        return (amount, 0) if taipei else (-amount, 0)
    return (-amount, 0) if taipei else (amount, 0)


def _make_plan(
    period: str,
    area: str,
    total: int,
    transfer_mark: str,
    source_items: list[tuple[int, str, int]],
    source_areas: list[str],
) -> dict[str, object]:
    if not source_items:
        raise RuntimeError(f"{period[:4]}.{period[4:]}／{area} 第72列有金額但找不到固定費用明細")

    period_label = f"{period[:4]}.{period[4:]}"
    taipei_rows, area_rows = [], []
    for _row_number, source, amount in source_items:
        label = f"{period_label}-{_label_subject(source)}{area}"
        for is_taipei, target in ((True, taipei_rows), (False, area_rows)):
            e_value, f_value = _posting(source, amount, taipei=is_taipei)
            target.append([False, "", "", "", e_value or "", f_value or "", "", "", _account(source, taipei=is_taipei), "", "", label])

    signed_total = sum(int(row[5] or 0) - int(row[4] or 0) for row in taipei_rows)
    tolerance = max(2, len(taipei_rows))
    if abs(signed_total - total) > tolerance:
        raise RuntimeError(
            f"{period_label}／{area} 固定費用明細 {signed_total:,} 與第72列 {total:,} 不符，未寫入"
        )

    descriptions = [source for _, source, _ in source_items if source != "2%"]
    memo = f"{period[:4]}/{period[4:]}" + "＋".join(descriptions)
    return {
        "area": area,
        "report_area": REPORT_AREA_NAMES.get(area, area),
        "source_areas": source_areas,
        "total": total,
        "transfer_mark": transfer_mark,
        "eligible": bool(transfer_mark),
        "memo": memo,
        "items": [
            {"row_number": row_number, "subject": source, "amount": amount}
            for row_number, source, amount in source_items
        ],
        "taipei_rows": taipei_rows,
        "area_rows": area_rows,
    }


def _build_plan(
    values: list[list[object]], period: str, area: str, *, require_ready: bool = True
) -> dict[str, object] | None:
    col = _find_source_column(values, period, area)
    total = _amount(_cell(values[71], col)) if len(values) >= 72 else 0
    transfer_mark = str(_cell(values[73], col) or "").strip() if len(values) >= 74 else ""
    if total <= 0:
        return None

    source_items: list[tuple[int, str, int]] = []
    for row_number in range(41, 72):
        row = values[row_number - 1] if row_number <= len(values) else []
        source = str(_cell(row, 2) or "").strip()
        amount = _amount(_cell(row, col))
        if source and amount > 0:
            source_items.append((row_number, source, amount))
    if not source_items:
        raise RuntimeError(f"{period[:4]}.{period[4:]}／{area} 第72列有金額但找不到固定費用明細")
    return _make_plan(period, area, total, transfer_mark, source_items, [area])


def _build_target_plan(values: list[list[object]], period: str, area: str) -> dict[str, object] | None:
    """桃園固定費用合併桃園與家電欄位，其餘地區維持單一來源。"""
    area = "桃園" if area == "家電" else area
    if area != "桃園":
        return _build_plan(values, period, area, require_ready=False)

    parts = [
        plan
        for source_area in TAOYUAN_SOURCE_AREAS
        if (plan := _build_plan(values, period, source_area, require_ready=False))
    ]
    if not parts:
        return None
    marks = [
        f"{plan['area']}:{plan['transfer_mark']}"
        for plan in parts
        if str(plan.get("transfer_mark") or "").strip()
    ]
    items = [dict(item, source_area=plan["area"]) for plan in parts for item in plan["items"]]
    descriptions = [str(item["subject"]) for item in items if item["subject"] != "2%"]
    return {
        "area": "桃園",
        "report_area": "桃園",
        "source_areas": list(TAOYUAN_SOURCE_AREAS),
        "total": sum(int(plan["total"]) for plan in parts),
        "transfer_mark": "；".join(marks),
        "eligible": bool(marks),
        "memo": f"{period[:4]}/{period[4:]}" + "＋".join(descriptions),
        "items": items,
        "taipei_rows": [row for plan in parts for row in plan["taipei_rows"]],
        "area_rows": [row for plan in parts for row in plan["area_rows"]],
    }


def select_plan_rows(
    plan: dict[str, object], row_numbers: set[int] | list[int] | tuple[int, ...]
) -> dict[str, object]:
    """只保留使用者勾選的來源列，並以分錄正負號重算實際匯款金額。"""
    selected = {int(row) for row in row_numbers}
    indexes = [
        index
        for index, item in enumerate(plan["items"])
        if int(item["row_number"]) in selected
    ]
    if not indexes:
        raise ValueError("請至少勾選一個固定費用科目")

    result = dict(plan)
    result["items"] = [plan["items"][index] for index in indexes]
    result["taipei_rows"] = [plan["taipei_rows"][index] for index in indexes]
    result["area_rows"] = [plan["area_rows"][index] for index in indexes]
    selected_total = sum(
        int(row[5] or 0) - int(row[4] or 0) for row in result["taipei_rows"]
    )
    if selected_total <= 0:
        raise ValueError("勾選科目計算後的匯款金額必須大於 0")
    result["total"] = selected_total
    descriptions = [
        str(item["subject"])
        for item in result["items"]
        if item["subject"] != "2%"
    ]
    result["memo"] = f"{str(plan['memo'])[:7]}" + "＋".join(descriptions)
    return result


def require_fubon_not_transferred(
    plan: dict[str, object], period: str, area: str
) -> None:
    """富邦任務建立前確認第 74 列仍空白，避免重複匯款。"""
    transfer_mark = str(plan.get("transfer_mark") or "").strip()
    if transfer_mark:
        raise ValueError(
            f"{period}／{area} 第74列已有匯款記錄 {transfer_mark}，為避免重複匯款已停止"
        )


def _read_values(spreadsheet_id: str, title: str, range_a1: str) -> list[list[object]]:
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!{range_a1}",
        valueRenderOption="FORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return result.get("values", [])


def _marketing_source_location() -> tuple[str, str]:
    """固定由 Jenny's Lemonhometools「財報設定」取得來源檔案及分頁。"""
    return resolve_marketing_expense_location("台北")


def _column_letter(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def write_transfer_date(period: str, area: str, payment_date: str) -> str:
    """把富邦實際匯款日期寫回來源總表該月／該地區的第 74 列。"""
    spreadsheet_id, title = _marketing_source_location()
    values = _read_values(spreadsheet_id, title, "A1:ZZ74")
    source_areas = TAOYUAN_SOURCE_AREAS if area in ("桃園", "家電") else (area,)
    cells = [
        f"{_column_letter(_find_source_column(values, period, source_area))}74"
        for source_area in source_areas
    ]
    def update(service) -> None:
        for cell in cells:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{title}'!{cell}",
                valueInputOption="USER_ENTERED",
                body={"values": [[payment_date]]},
            ).execute()

    try:
        update(get_sheets_service())
    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 403:
            raise
        # 行銷費用總表可能只授權 Jenny、未分享給服務帳號；改用既有 OAuth。
        from services.google_auth import get_sheets_service as get_oauth_sheets_service

        update(get_oauth_sheets_service())
    return "、".join(cells)


def _sheet_properties(service, spreadsheet_id: str, title: str) -> dict[str, object]:
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties"
    ).execute()
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == title:
            return props
    raise RuntimeError(f"試算表 {spreadsheet_id} 找不到分頁「{title}」")


def _sheet_id(service, spreadsheet_id: str, title: str) -> int:
    return int(_sheet_properties(service, spreadsheet_id, title)["sheetId"])


def _fingerprints(rows: list[list[object]]) -> Counter:
    return Counter(
        (
            str(_cell(row, 9)).strip(),
            str(_cell(row, 12)).strip(),
            _amount(_cell(row, 5)),
            _amount(_cell(row, 6)),
        )
        for row in rows
        if str(_cell(row, 9)).strip() or str(_cell(row, 12)).strip()
    )


def _missing_statement_rows(existing: list[list[object]], planned: list[list[object]], location: str) -> list[list[object]]:
    have = _fingerprints(existing)
    missing: list[list[object]] = []
    for row in planned:
        key = next(iter(_fingerprints([row])))
        if have[key] > 0:
            have[key] -= 1
        else:
            missing.append(row)
    return missing


def assert_plan_not_posted(plan: dict[str, object]) -> None:
    """銀行匯款前確認同批固定費用分錄尚未寫入，避免重複匯款。"""
    target = str(plan["area"])
    report_area = str(plan["report_area"])
    locations = [
        ("台北", plan["taipei_rows"]),
        (report_area, plan["area_rows"]),
    ]
    for area, planned_rows in locations:
        spreadsheet_id, title = resolve_statement_location(area, "富邦更新")
        existing = _read_values(spreadsheet_id, title, "A:L")
        if _fingerprints(existing) & _fingerprints(planned_rows):
            label = f"{target}（{report_area}財報）" if report_area != target else target
            raise ValueError(f"{label} 已有相同固定費用分錄，未進行銀行匯款")


def _append_statement_rows(spreadsheet_id: str, title: str, existing: list[list[object]], rows: list[list[object]]) -> int:
    if not rows:
        return 0
    # A 欄整欄可能預先放核取方塊；依需求以 B 欄最後非空白列為資料尾端。
    last_data = max(
        (
            idx
            for idx, row in enumerate(existing, start=1)
            if str(_cell(row, 2)).strip()
        ),
        default=1,
    )
    start_row, end_row = last_data + 1, last_data + len(rows)
    service = get_sheets_service()
    properties = _sheet_properties(service, spreadsheet_id, title)
    sheet_id = int(properties["sheetId"])
    row_count = int((properties.get("gridProperties") or {}).get("rowCount", 0))
    source = {"sheetId": sheet_id, "startRowIndex": last_data - 1, "endRowIndex": last_data, "startColumnIndex": 0, "endColumnIndex": 12}
    destination = {"sheetId": sheet_id, "startRowIndex": start_row - 1, "endRowIndex": end_row, "startColumnIndex": 0, "endColumnIndex": 12}
    requests = []
    if end_row > row_count:
        requests.append({"appendDimension": {
            "sheetId": sheet_id,
            "dimension": "ROWS",
            "length": end_row - row_count,
        }})
    requests.extend([
        {"copyPaste": {"source": source, "destination": destination, "pasteType": "PASTE_FORMAT", "pasteOrientation": "NORMAL"}},
        {"copyPaste": {"source": source, "destination": destination, "pasteType": "PASTE_DATA_VALIDATION", "pasteOrientation": "NORMAL"}},
    ])
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A{start_row}:L{end_row}",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    return len(rows)


def preview_fixed_expenses(period: str, area: str = "全區") -> dict[str, object]:
    """唯讀預覽符合條件的地區、來源科目、金額與第 72 列匯款總額。"""
    period = (period or "").strip()
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202608")
    area = "桃園" if area == "家電" else area
    targets = list(AREAS) if area == "全區" else [area]
    if any(target not in AREAS for target in targets):
        raise ValueError("請選擇台中／桃園／新竹／高雄／全區")

    marketing_id, marketing_title = _marketing_source_location()
    values = _read_values(marketing_id, marketing_title, "A1:ZZ74")
    plans = [
        plan
        for target in targets
        if (plan := _build_target_plan(values, period, target))
    ]
    processed = {str(plan["area"]) for plan in plans}
    return {
        "period": period,
        "plans": plans,
        "skipped_areas": [target for target in targets if target not in processed],
    }


def transfer_fixed_expenses(
    period: str,
    area: str = "全區",
    on_progress: Callable | None = None,
    *,
    allow_unmarked: bool = False,
    payment_date: str = "",
    selected_rows: dict[str, list[int]] | list[int] | set[int] | None = None,
) -> dict[str, object]:
    period = (period or "").strip()
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202608")
    area = "桃園" if area == "家電" else area
    targets = list(AREAS) if area == "全區" else [area]
    if any(target not in AREAS for target in targets):
        raise ValueError("請選擇台中／桃園／新竹／高雄／全區")

    log_execution(TOOL_NAME, area, "開始", f"期別：{period}")
    try:
        marketing_id, marketing_title = _marketing_source_location()
        values = _read_values(marketing_id, marketing_title, "A1:ZZ74")
        plans = [
            plan
            for target in targets
            if (
                plan := _build_target_plan(values, period, target)
            )
        ]
        if selected_rows is not None:
            plans = [
                select_plan_rows(
                    plan,
                    selected_rows.get(str(plan["area"]), [])
                    if isinstance(selected_rows, dict)
                    else selected_rows,
                )
                for plan in plans
            ]
        written_rows = 0
        summaries = []
        for plan in plans:
            target = str(plan["area"])
            report_area = str(plan["report_area"])
            tp_id, tp_title = resolve_statement_location("台北", "富邦更新")
            ar_id, ar_title = resolve_statement_location(report_area, "富邦更新")
            tp_existing = _read_values(tp_id, tp_title, "A:L")
            ar_existing = _read_values(ar_id, ar_title, "A:L")
            tp_rows = _missing_statement_rows(tp_existing, plan["taipei_rows"], f"台北／{tp_title}")
            ar_rows = _missing_statement_rows(ar_existing, plan["area_rows"], f"{report_area}／{ar_title}")
            written_rows += _append_statement_rows(tp_id, tp_title, tp_existing, tp_rows)
            written_rows += _append_statement_rows(ar_id, ar_title, ar_existing, ar_rows)

            destination = f"（{report_area}財報）" if report_area != target else ""
            summary = f"{target}{destination}：匯款 {int(plan['total']):,}，富邦分錄新增 {len(tp_rows) + len(ar_rows)} 列"
            summaries.append(summary)
            if on_progress:
                on_progress(summary, "success")
        skipped = [target for target in targets if target not in {str(plan["area"]) for plan in plans}]
        result = {"period": period, "processed_areas": len(plans), "statement_rows": written_rows, "request_rows": 0, "skipped_areas": skipped, "summaries": summaries}
        log_execution(TOOL_NAME, area, "完成", f"富邦分錄新增 {written_rows} 列；略過：{'、'.join(skipped) or '無'}")
        return result
    except Exception as exc:
        log_execution(TOOL_NAME, area, "失敗", str(exc))
        raise
