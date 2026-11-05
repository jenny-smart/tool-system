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
AREAS = ("台中", "桃園", "新竹", "高雄", "學院")
# 2026-11 Jenny：高雄、學院不看行銷費用總表，每月固定匯給台北（台北內勤薪資的減項）。
# (金額, 科目, 是否有財報)：高雄財報記內勤薪資加項；學院沒有財報，只記台北。
FIXED_AMOUNT_AREAS: dict[str, tuple[int, str, bool]] = {
    "高雄": (40000, "內勤薪資", True),
    "學院": (47911, "內勤薪資", False),
}
MARKETING_AREAS = tuple(a for a in AREAS if a not in FIXED_AMOUNT_AREAS)
# 2026-11-05 Jenny：學院自己轉帳給台北（不經我們的富邦匯款、不需要學院帳號），
# 台北只記入帳分錄，47911 拆成四個科目（說明欄不加地區）。
SELF_REMIT_AREAS = ("學院",)
ACADEMY_ITEMS: tuple[tuple[str, int], ...] = (
    ("內勤薪資", 38376),
    ("內勤勞保費", 4611),
    ("內勤健保費", 2518),
    ("內勤退休金", 2406),
)
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
    match = re.search(r"(20\d{2})[./-]?(1[0-2]|0?[1-9])(?!\d)", str(value or "").strip())
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
    return "google代理商服務費" if source == "google服務費" else source


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
        output_subject = _label_subject(source)
        label = f"{period_label}-{output_subject}-{area}"
        for is_taipei, target in ((True, taipei_rows), (False, area_rows)):
            e_value, f_value = _posting(source, amount, taipei=is_taipei)
            account = _account(source, taipei=is_taipei)
            if account == "google服務費":
                account = "google代理商服務費"
            target.append([False, "", "", "", e_value or "", f_value or "", "", "", account, "", "", label])

    signed_total = sum(int(row[5] or 0) - int(row[4] or 0) for row in taipei_rows)
    tolerance = max(2, len(taipei_rows))
    if abs(signed_total - total) > tolerance:
        raise RuntimeError(
            f"{period_label}／{area} 固定費用明細 {signed_total:,} 與第72列 {total:,} 不符，未寫入"
        )

    descriptions = [_label_subject(source) for _, source, _ in source_items if source != "2%"]
    memo = f"{period[:4]}/{period[4:]}" + "＋".join(descriptions)
    return {
        "period": period,
        "area": area,
        "report_area": REPORT_AREA_NAMES.get(area, area),
        "source_areas": source_areas,
        "total": total,
        "transfer_mark": transfer_mark,
        "eligible": bool(transfer_mark),
        "memo": memo,
        "items": [
            {"row_number": row_number, "subject": _label_subject(source), "amount": amount}
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


def fixed_amount_plan(period: str, area: str) -> dict[str, object]:
    """高雄／學院：每月固定金額匯給台北。台北富邦更新 E 欄記 −金額（內勤薪資減項）；
    高雄富邦更新 E 欄記 +金額（內勤薪資加項）；學院沒有財報，不記地區分錄。"""
    amount, subject, has_report = FIXED_AMOUNT_AREAS[area]
    ym = f"{period[:4]}.{period[4:]}"
    if area == "學院":
        items = [(name, value) for name, value in ACADEMY_ITEMS]
        taipei_rows = [[False, "", "", "", -value, "", "", "", name, "", "", f"{ym}-{name}"] for name, value in items]
        area_rows: list = []
    else:
        items = [(subject, amount)]
        label = f"{ym}-{subject}-{area}"
        taipei_rows = [[False, "", "", "", -amount, "", "", "", subject, "", "", label]]
        area_rows = [[False, "", "", "", amount, "", "", "", subject, "", "", label]] if has_report else []
    return {
        "period": period,
        "area": area,
        "report_area": area if has_report else "",
        "source_areas": [area],
        "total": amount,
        "transfer_mark": "",
        "eligible": True,
        "fixed_amount": True,
        "self_remit": area in SELF_REMIT_AREAS,
        "memo": f"{period[:4]}/{period[4:]}{subject}",
        "items": [{"row_number": 0, "subject": name, "amount": value} for name, value in items],
        "taipei_rows": taipei_rows,
        "area_rows": area_rows,
    }


def _build_target_plan(values: list[list[object]], period: str, area: str) -> dict[str, object] | None:
    """桃園固定費用合併桃園與家電欄位，其餘地區維持單一來源；高雄／學院是固定金額。"""
    area = "桃園" if area == "家電" else area
    if area in FIXED_AMOUNT_AREAS:
        return fixed_amount_plan(period, area)
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
        "period": period,
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
    """只保留使用者勾選的來源列，並以分錄正負號重算實際匯款金額。固定金額地區不分科目，原樣回傳。"""
    if plan.get("fixed_amount"):
        return plan
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


def _marketing_source_location(period: str | None = None) -> tuple[str, str]:
    """固定由 Jenny's Lemonhometools「財報設定」取得來源檔案及分頁；
    分頁用期別所屬年度的「{YYYY}總表」（1 月處理 12 月仍讀去年總表）。"""
    year = int(period[:4]) if period and str(period)[:4].isdigit() else None
    return resolve_marketing_expense_location("台北", year=year)


def _marketing_values(period: str, targets: list[str]) -> list[list[object]]:
    """只有要跑行銷費用總表的地區（台中／桃園／新竹）才讀來源總表。"""
    if not any(t in MARKETING_AREAS for t in targets):
        return []
    marketing_id, marketing_title = _marketing_source_location(period)
    return _read_values(marketing_id, marketing_title, "A1:ZZ74")


def _column_letter(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def write_transfer_date(period: str, area: str, payment_date: str) -> str:
    """把富邦實際匯款日期寫回來源總表該月／該地區的第 74 列。"""
    spreadsheet_id, title = _marketing_source_location(period)
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


def _raw_fingerprint(row: list[object], *, tolerant: bool = False) -> tuple[str, str, int, int]:
    try:
        e_amount = _amount(_cell(row, 5))
        f_amount = _amount(_cell(row, 6))
    except RuntimeError:
        if not tolerant:
            raise
        return ("__非分錄列__", str(_cell(row, 5)), 0, 0)
    return (str(_cell(row, 9)).strip(), str(_cell(row, 12)).strip(), e_amount, f_amount)


def _fingerprint(row: list[object], *, tolerant: bool = False) -> tuple[str, str, int, int]:
    account, label, e_amount, f_amount = _raw_fingerprint(row, tolerant=tolerant)
    if account == "google服務費":
        account = "google代理商服務費"
    # 相容舊版錯把 2%／稅捐標成 google服務費的資料，讓重跑時原列就地修正。
    if account in ("分店收入2%", "分店支出2%"):
        label = label.replace("-google服務費", "-2%")
    elif account == "稅捐":
        label = label.replace("-google服務費", "-稅捐")
    else:
        label = label.replace("-google服務費", "-google代理商服務費")
    for area in ("台北", "台中", "桃園", "家電", "新竹", "高雄"):
        if label.endswith(area) and not label.endswith(f"-{area}"):
            label = f"{label[:-len(area)]}-{area}"
            break
    return account, label, e_amount, f_amount


def _fingerprints(rows: list[list[object]]) -> Counter:
    return Counter(
        _fingerprint(row, tolerant=True)
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


def _matching_statement_row_numbers(
    existing: list[list[object]], planned: list[list[object]]
) -> list[int]:
    """找出屬於本批分錄的實際列號；重複科目依預計出現次數限制。"""
    needed = _fingerprints(planned)
    matches: list[int] = []
    for row_number, row in enumerate(existing, start=1):
        key = _fingerprint(row, tolerant=True)
        if needed[key] > 0:
            matches.append(row_number)
            needed[key] -= 1
    return matches


def _reorder_statement_rows(
    spreadsheet_id: str,
    title: str,
    existing: list[list[object]],
    planned: list[list[object]],
) -> None:
    """把同一批分錄依預覽順序排列；桃園完整列完後才列家電。"""
    row_numbers = _matching_statement_row_numbers(existing, planned)
    if len(row_numbers) != len(planned):
        return
    current = [_raw_fingerprint(existing[row_number - 1], tolerant=True) for row_number in row_numbers]
    expected = [_raw_fingerprint(row) for row in planned]
    if current == expected:
        return
    get_sheets_service().spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": f"'{title}'!A{row_number}:L{row_number}", "values": [row]}
                for row_number, row in zip(row_numbers, planned)
            ],
        },
    ).execute()


def _plan_year(plan: dict[str, object]) -> int | None:
    """分錄寫進期別所屬年度的財報（2026 年 12 月的固定費用在 1 月才轉帳，仍寫 2026 年財報）。"""
    period = str(plan.get("period") or "").strip()
    return int(period[:4]) if period[:4].isdigit() else None


def assert_plan_not_posted(plan: dict[str, object]) -> None:
    """銀行匯款前確認同批固定費用分錄尚未寫入，避免重複匯款。"""
    target = str(plan["area"])
    report_area = str(plan["report_area"])
    locations = [("台北", plan["taipei_rows"])]
    if report_area and plan["area_rows"]:
        locations.append((report_area, plan["area_rows"]))
    year = _plan_year(plan)
    for area, planned_rows in locations:
        spreadsheet_id, title = resolve_statement_location(area, "富邦更新", year=year)
        existing = _read_values(spreadsheet_id, title, "A:L")
        if _fingerprints(existing) & _fingerprints(planned_rows):
            label = f"{target}（{report_area}財報）" if report_area and report_area != target else target
            raise ValueError(f"{label} 已有相同固定費用分錄，未進行銀行匯款")


def _append_statement_rows(spreadsheet_id: str, title: str, existing: list[list[object]], rows: list[list[object]]) -> int:
    if not rows:
        return 0
    # A 欄整欄可能預先放核取方塊；B 是一般交易資料，固定費用分錄則會寫 E/F/I。
    # 因此尾端不能只看 B，否則下一批會覆蓋上一批固定費用。
    last_data = max(
        (
            idx
            for idx, row in enumerate(existing, start=1)
            if any(str(_cell(row, col)).strip() for col in (2, 5, 6, 9))
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
        raise ValueError("請選擇台中／桃園／新竹／高雄／學院／全區")

    values = _marketing_values(period, targets)
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


def remit_status(period: str) -> list[dict[str, object]]:
    """每月 10 日盤點：各區（台中／桃園／新竹 行銷費用總表；高雄／學院 固定金額）是否已匯款給台北。
    已匯款＝行銷總表第 74 列有日期，或台北富邦更新已有同批分錄。"""
    preview = preview_fixed_expenses(period, "全區")
    result = []
    for plan in preview.get("plans") or []:
        mark = str(plan.get("transfer_mark") or "").strip()
        posted = ""
        if not mark:
            try:
                assert_plan_not_posted(plan)
            except ValueError:
                posted = "財報已有分錄"
        result.append({"area": plan["area"], "total": int(plan["total"]), "memo": plan.get("memo", ""),
                       "fixed": bool(plan.get("fixed_amount")), "self_remit": bool(plan.get("self_remit")),
                       "done": bool(mark or posted),
                       "detail": mark or posted or "待匯款", "plan": plan})
    for area in preview.get("skipped_areas") or []:
        result.append({"area": area, "total": 0, "memo": "", "fixed": False, "self_remit": False, "done": True,
                       "detail": "第72列沒有金額", "plan": None})
    return result


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
        raise ValueError("請選擇台中／桃園／新竹／高雄／學院／全區")

    log_execution(TOOL_NAME, area, "開始", f"期別：{period}")
    try:
        values = _marketing_values(period, targets)
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
            year = _plan_year(plan) or int(period[:4])
            tp_id, tp_title = resolve_statement_location("台北", "富邦更新", year=year)
            tp_existing = _read_values(tp_id, tp_title, "A:L")
            tp_rows = _missing_statement_rows(tp_existing, plan["taipei_rows"], f"台北／{tp_title}")
            written_rows += _append_statement_rows(tp_id, tp_title, tp_existing, tp_rows)
            tp_after = _read_values(tp_id, tp_title, "A:L")
            _reorder_statement_rows(tp_id, tp_title, tp_after, plan["taipei_rows"])
            ar_rows: list = []
            if report_area and plan["area_rows"]:  # 學院沒有財報，只記台北
                ar_id, ar_title = resolve_statement_location(report_area, "富邦更新", year=year)
                ar_existing = _read_values(ar_id, ar_title, "A:L")
                ar_rows = _missing_statement_rows(ar_existing, plan["area_rows"], f"{report_area}／{ar_title}")
                written_rows += _append_statement_rows(ar_id, ar_title, ar_existing, ar_rows)
                ar_after = _read_values(ar_id, ar_title, "A:L")
                _reorder_statement_rows(ar_id, ar_title, ar_after, plan["area_rows"])

            destination = f"（{report_area}財報）" if report_area and report_area != target else ""
            verb = "入帳" if plan.get("self_remit") else "匯款"
            summary = f"{target}{destination}：{verb} {int(plan['total']):,}，富邦分錄新增 {len(tp_rows) + len(ar_rows)} 列"
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
