"""財報富邦更新：依 L 欄文字批次設定 I 欄／搬移 LC客訴 金額。

操作對象是登記表「財報富邦更新」（地區＝台北／台中／桃園／新竹／高雄）指到的
財報分頁，欄位沿用財報既有配置：
  E=5  收入　F=6  支出　I=9  收支類型（下拉選單）　L=12  摘要／單號（含 LC 單號）

規則：
  1. L 欄文字包含「LC」的列，I 欄設為「匯款收入」。
  2. L 欄文字包含「LC客訴」的列（已符合規則 1），再把 F 欄金額搬到 E 欄，
     且轉為負數（E = -F），比照客訴退費從支出改記為負的收入。

試算表位置查詢走主控試算表（Jenny's Lemonhometools）的「財報設定」分頁
（見 tools/finance_management/statement_registry.py），不需要另外設定。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.statement_registry import resolve_statement_location

TW_TZ = ZoneInfo("Asia/Taipei")

EXECUTION_LOG_SHEET = "財報富邦更新執行記錄"

COL_E, COL_F, COL_I, COL_L = 5, 6, 9, 12

REMITTANCE_INCOME_LABEL = "匯款收入"
LC_MARK = "LC"
LC_COMPLAINT_MARK = "LC客訴"


def _ensure_execution_log_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if EXECUTION_LOG_SHEET in titles:
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": EXECUTION_LOG_SHEET}}}]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{EXECUTION_LOG_SHEET}'!A1:E1",
        valueInputOption="RAW",
        body={"values": [["時間", "地區", "步驟", "狀態", "訊息"]]},
    ).execute()


def _log_execution(area: str, step: str, status: str, message: str) -> None:
    """打卡記錄：寫進 Jenny's Lemonhometools 的「財報富邦更新執行記錄」分頁。"""
    service = get_sheets_service()
    master_id = get_master_spreadsheet_id()
    _ensure_execution_log_sheet(service, master_id)

    now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
    service.spreadsheets().values().append(
        spreadsheetId=master_id,
        range=f"'{EXECUTION_LOG_SHEET}'!A:E",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [[now_text, area, step, status, message]]},
    ).execute()


def _read_values(spreadsheet_id: str, title: str) -> list[list[object]]:
    service = get_sheets_service()
    res = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A:Z",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        .execute()
    )
    return res.get("values", [])


def _cell(row: list[object], col: int) -> object:
    idx = col - 1
    return row[idx] if idx < len(row) else ""


def _to_number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def apply_lc_remittance_filter(area: str) -> dict[str, int]:
    """套用財報富邦更新的 LC 規則，回傳 {'remittance_marked', 'complaint_moved'}。"""
    _log_execution(area, "LC 匯款收入／客訴金額搬移", "開始", "")
    try:
        result = _apply_lc_remittance_filter(area)
    except Exception as exc:
        _log_execution(area, "LC 匯款收入／客訴金額搬移", "失敗", str(exc))
        raise
    _log_execution(
        area,
        "LC 匯款收入／客訴金額搬移",
        "完成",
        f"I 欄設為匯款收入 {result['remittance_marked']} 筆，"
        f"LC客訴搬移金額 {result['complaint_moved']} 筆",
    )
    return result


def _apply_lc_remittance_filter(area: str) -> dict[str, int]:
    spreadsheet_id, title = resolve_statement_location(area, "富邦更新")
    values = _read_values(spreadsheet_id, title)
    if len(values) < 2:
        return {"remittance_marked": 0, "complaint_moved": 0}

    i_updates: list[dict[str, object]] = []
    e_updates: list[dict[str, object]] = []

    for row_idx, row in enumerate(values[1:], start=2):
        l_text = str(_cell(row, COL_L) or "")
        if LC_MARK not in l_text:
            continue

        i_updates.append(
            {"range": f"'{title}'!I{row_idx}", "values": [[REMITTANCE_INCOME_LABEL]]}
        )

        if LC_COMPLAINT_MARK in l_text:
            f_value = _to_number(_cell(row, COL_F))
            e_updates.append(
                {"range": f"'{title}'!E{row_idx}", "values": [[-f_value]]}
            )

    data = i_updates + e_updates
    if data:
        service = get_sheets_service()
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    return {
        "remittance_marked": len(i_updates),
        "complaint_moved": len(e_updates),
    }
