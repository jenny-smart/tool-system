"""內部請款處理（請款記錄／工具包押金退款／清潔用品採購）的登記表查詢。

登記表放在主控試算表（Jenny's Lemonhometools）的「財務帳務設定」分頁，
欄位為：類型、地區、試算表ID、GID、轉入帳號名稱。
每個「類型＋地區」對應一列，指向實際報表所在的試算表與分頁 GID；
工具包押金退款另外用「轉入帳號名稱」記錄固定的收款人，避免把真實姓名寫進程式碼。
"""

from __future__ import annotations

from tools.common.config_loader import (
    get_master_spreadsheet_id,
    get_sheets_service,
    read_sheet_records,
)
from tools.common.year_roots import choose_file_id

REGISTRY_SHEET_NAME = "財務帳務設定"

PAYMENT_REQUEST_TYPE = "請款記錄"
DEPOSIT_REFUND_TYPE = "工具包押金退款"
SUPPLY_PURCHASE_TYPE = "清潔用品採購"
DEPOSIT_REPORT_TYPE = "工具包押金財報"
NEWEBPAY_EXPRESS_TYPE = "藍新快速收款查帳"
CLEANING_CHANGE_TYPE = "清潔異動設定"

# 各類型對應「年度根目錄設定」的類別與檔名序號（同一列有多個檔名時用分號分隔）
YEAR_ROOT_CATEGORY = {
    PAYMENT_REQUEST_TYPE: ("財報", 0),
    DEPOSIT_REPORT_TYPE: ("財報", 0),
    DEPOSIT_REFUND_TYPE: ("名冊薪資", 1),
    SUPPLY_PURCHASE_TYPE: ("清潔用品", 0),
    NEWEBPAY_EXPRESS_TYPE: ("內勤工作表單", 0),
    CLEANING_CHANGE_TYPE: ("內勤工作表單", 0),
}
SERVICE_CHANGE_AREAS = {"桃園", "新竹"}


def _sheet_title_for_gid(service, spreadsheet_id: str, gid: str) -> str:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if str(props.get("sheetId", "")) == str(gid).strip():
            return str(props.get("title", ""))
    raise RuntimeError(f"試算表 {spreadsheet_id} 找不到 GID={gid} 對應的分頁")


def find_registry_row(report_type: str, area: str) -> dict[str, str]:
    records = read_sheet_records(REGISTRY_SHEET_NAME, get_master_spreadsheet_id())
    for record in records:
        if record.get("類型", "").strip() != report_type:
            continue
        if record.get("地區", "").strip() != area:
            continue
        return _with_year_root(report_type, area, record)
    raise RuntimeError(
        f"「{REGISTRY_SHEET_NAME}」登記表找不到「{report_type}／{area}」，"
        f"請先在 Jenny's Lemonhometools 的「{REGISTRY_SHEET_NAME}」分頁新增一列"
    )


def _with_year_root(report_type: str, area: str, record: dict[str, str]) -> dict[str, str]:
    """依「年度根目錄設定」的模式決定試算表ID（原設定／比對時就是登記表上的 ID）。"""
    category, index = YEAR_ROOT_CATEGORY.get(report_type, ("", 0))
    if not category:
        return record
    if report_type == CLEANING_CHANGE_TYPE and area in SERVICE_CHANGE_AREAS:
        category = "服務異動"
    record = dict(record)
    record["試算表ID"] = choose_file_id(category, area, record.get("試算表ID", ""), file_index=index)
    return record


def read_report_values(report_type: str, area: str) -> list[list[str]]:
    """依登記表找到指定類型／地區的報表，回傳該分頁的原始儲存格內容。"""
    record = find_registry_row(report_type, area)
    spreadsheet_id = record.get("試算表ID", "").strip()
    gid = record.get("GID", "").strip()
    if not spreadsheet_id or not gid:
        raise RuntimeError(
            f"「{REGISTRY_SHEET_NAME}」{report_type}／{area} 缺少試算表ID或GID"
        )

    service = get_sheets_service()
    title = _sheet_title_for_gid(service, spreadsheet_id, gid)
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!A:Z"
    ).execute()
    return res.get("values", [])


def resolve_report_location(report_type: str, area: str) -> tuple[str, str]:
    """依登記表找到指定類型／地區的報表，回傳 (試算表ID, 分頁標題)。

    給需要直接用 Sheets API 讀寫（而非只讀 values）的呼叫方使用，
    例如財報統計／打勾／異常標記需要寫回原分頁。
    """
    record = find_registry_row(report_type, area)
    spreadsheet_id = record.get("試算表ID", "").strip()
    gid = record.get("GID", "").strip()
    if not spreadsheet_id or not gid:
        raise RuntimeError(
            f"「{REGISTRY_SHEET_NAME}」{report_type}／{area} 缺少試算表ID或GID"
        )

    service = get_sheets_service()
    title = _sheet_title_for_gid(service, spreadsheet_id, gid)
    return spreadsheet_id, title


def read_destination_name(report_type: str, area: str) -> str:
    """讀取登記表中固定的轉入帳號名稱（目前只有工具包押金退款會用到）。"""
    record = find_registry_row(report_type, area)
    name = record.get("轉入帳號名稱", "").strip()
    if not name:
        raise RuntimeError(
            f"「{REGISTRY_SHEET_NAME}」{report_type}／{area} 缺少轉入帳號名稱"
        )
    return name
