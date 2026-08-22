from __future__ import annotations

"""
功能目錄同步。

目的：master spreadsheet 裡已有一份「功能目錄」工作表（人工維護），
但沒有即時更新、也沒有「最後執行時間」。這個模組讓 log_to_sheet() 在
每次任何系統/功能執行完成時，順手把該筆功能同步進功能目錄：
- 該系統/功能還沒有列在目錄裡 -> 自動新增一列
- 已經有 -> 回填「最後執行時間」「最後執行結果」，排程規則若原本空白也補上

不另外開每日排程去「確認最後更新時間」，因為每次執行都會即時回填。
"""

from typing import Any

CATALOG_SHEET_NAME = "功能目錄"

CATALOG_HEADERS = ["系統", "功能", "排程規則", "最後執行時間", "最後執行結果", "備註"]

MANUAL_RULE = "手動執行"

# 依 tools/common/log_to_sheet.py 的 SHEET_BY_SYSTEM 系統代號/中文名對照排程規則。
# 規則文字取自 .github/workflows/*.yml 的 cron 設定（台北時間）。
SCHEDULE_RULES: dict[str, str] = {
    "daily": "每日 01:00～07:00（GitHub Actions scheduled_daily.yml，多個時段）",
    "日排程系統": "每日 01:00～07:00（GitHub Actions scheduled_daily.yml，多個時段）",
    "monthly": "每月15日／月底（GitHub Actions scheduled_monthly.yml，多個時段）",
    "月排程系統": "每月15日／月底（GitHub Actions scheduled_monthly.yml，多個時段）",
    "field": "每日 07:00（GitHub Actions scheduled_field.yml）",
    "外場排程系統": "每日 07:00（GitHub Actions scheduled_field.yml）",
    "外場日排程系統": "每日 07:00（GitHub Actions scheduled_field.yml）",
    "service": "每日 06:00（GitHub Actions scheduled_service.yml）",
    "客服排程系統": "每日 06:00（GitHub Actions scheduled_service.yml）",
    "gmail_401": "每月20日 00:00（GitHub Actions Scheduled_gmail_401.yml）",
    "Gmail401歸檔": "每月20日 00:00（GitHub Actions Scheduled_gmail_401.yml）",
    "performance": "每日 00:00/08:00/12:00/18:00（GitHub Actions performance_report.yml）",
    "績效報表": "每日 00:00/08:00/12:00/18:00（GitHub Actions performance_report.yml）",
}


def schedule_rule_for(system: str, run_type: str = "") -> str:
    key = str(system).strip()
    rule = SCHEDULE_RULES.get(key)
    if rule:
        return rule
    if str(run_type).strip() == "排程":
        return "排程觸發（規則未登記於 function_catalog.SCHEDULE_RULES，請補充）"
    return MANUAL_RULE


def _col_index(headers: list[str], name: str, fallback: int) -> int:
    try:
        return headers.index(name)
    except ValueError:
        return fallback


def _col_letter(idx: int) -> str:
    idx += 1
    letters = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def ensure_catalog_sheet(service, spreadsheet_id: str) -> list[str]:
    """確保功能目錄工作表存在，且擁有這幾個欄位；不動使用者既有欄位順序。"""
    from tools.common.log_to_sheet import ensure_sheet, get_sheet_id

    sheet_id = get_sheet_id(service, spreadsheet_id, CATALOG_SHEET_NAME)
    if sheet_id is None:
        ensure_sheet(service, spreadsheet_id, CATALOG_SHEET_NAME, CATALOG_HEADERS)
        return list(CATALOG_HEADERS)

    current = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{CATALOG_SHEET_NAME}'!A1:Z1")
        .execute()
        .get("values", [])
    )

    headers = list(current[0]) if current else []
    missing = [h for h in CATALOG_HEADERS if h not in headers]
    if missing:
        headers = headers + missing
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{CATALOG_SHEET_NAME}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()

    return headers


def sync_function_catalog(
    service,
    spreadsheet_id: str,
    *,
    system: str,
    function: str,
    status: str = "",
    run_type: str = "",
    timestamp: str = "",
) -> None:
    """每次系統/功能執行完成時呼叫：沒有就新增一列，有就回填最後執行時間/結果。"""
    from tools.common.log_to_sheet import now_text

    if not system or not function:
        return

    headers = ensure_catalog_sheet(service, spreadsheet_id)

    sys_idx = _col_index(headers, "系統", 0)
    func_idx = _col_index(headers, "功能", 1)
    rule_idx = _col_index(headers, "排程規則", 2)
    time_idx = _col_index(headers, "最後執行時間", 3)
    result_idx = _col_index(headers, "最後執行結果", 4)

    rows = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{CATALOG_SHEET_NAME}'!A2:Z")
        .execute()
        .get("values", [])
    )

    target_row_number = None
    existing_row: list[str] = []
    for i, row in enumerate(rows):
        row_system = row[sys_idx] if sys_idx < len(row) else ""
        row_function = row[func_idx] if func_idx < len(row) else ""
        if row_system == system and row_function == function:
            target_row_number = i + 2  # 資料從第 2 列開始（第 1 列是表頭）
            existing_row = row
            break

    rule = schedule_rule_for(system, run_type)
    when = timestamp or now_text()

    if target_row_number is None:
        new_row = [""] * len(headers)
        new_row[sys_idx] = system
        new_row[func_idx] = function
        new_row[rule_idx] = rule
        new_row[time_idx] = when
        new_row[result_idx] = status
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{CATALOG_SHEET_NAME}'!A:Z",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [new_row]},
        ).execute()
        return

    updates: list[tuple[int, str]] = []
    existing_rule = existing_row[rule_idx] if rule_idx < len(existing_row) else ""
    if not str(existing_rule).strip():
        updates.append((rule_idx, rule))
    updates.append((time_idx, when))
    if status:
        updates.append((result_idx, status))

    data = [
        {
            "range": f"'{CATALOG_SHEET_NAME}'!{_col_letter(idx)}{target_row_number}",
            "values": [[value]],
        }
        for idx, value in updates
    ]

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
