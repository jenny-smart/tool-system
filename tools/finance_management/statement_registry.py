"""財報設定登記表查詢：讀主控試算表（Jenny's Lemonhometools）的「財報設定」分頁，
取得各地區財報試算表、各分頁 GID，以及共用的行銷費用檔案ID。

分頁欄位（A 起，依欄位順序讀取，不靠標題文字比對）：
  A 地區　B 試算表ID　C 富邦更新gid　D 元大更新gid　E 財務gid　F 現金gid
  G 行銷費用檔案ID（各地區列共用同一個值，指向「行銷費用總管理」試算表）
  H 2026review gid（該地區在「2026目標及review檔」裡對應的分頁）

地區列：台北／台中／桃園／新竹／高雄，每列的 B 欄是「該地區財報」這一份
試算表，C–F 欄則是同一份試算表裡各分頁（富邦更新／元大更新／財務／現金）
的 GID。H欄則是另一份共用試算表（「2026目標及review檔」，見下）裡，該
地區自己的分頁 GID。

另外兩列 2026-all／2026-現金，只有 B 欄（試算表ID），對應獨立的
「All財報」／「All財報現金缺口」試算表本身。

還有一列地區＝「2026」，B 欄是「2026目標及review檔」這份試算表本身的ID
（各地區共用同一份試算表，只是分頁不同——各地區自己的分頁 GID 記在各地區
列的 H欄，不是這一列）。這一列自己的 H欄，登記的是「2026review工作表」
這個跨地區彙整分頁的 GID。
"""

from __future__ import annotations

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

REGISTRY_SHEET_NAME = "財報設定"

COL_AREA = 1
COL_SPREADSHEET_ID = 2
COL_FUBON_GID = 3
COL_YUANTA_GID = 4
COL_FINANCE_GID = 5
COL_CASH_GID = 6
COL_MARKETING_ID = 7
COL_REVIEW_GID = 8

REPORT_GID_COLUMN = {
    "富邦更新": COL_FUBON_GID,
    "元大更新": COL_YUANTA_GID,
    "財務": COL_FINANCE_GID,
    "現金": COL_CASH_GID,
}

ALL_REPORT_AREA = "2026-all"
CASH_GAP_AREA = "2026-現金"
REVIEW_SPREADSHEET_AREA = "2026"


def _cell(row: list[str], col: int) -> str:
    idx = col - 1
    return str(row[idx]).strip() if idx < len(row) else ""


def _extract_spreadsheet_id(raw: str) -> str:
    """容錯：欄位裡貼成完整網址、或「ID,gid=...」這種格式時，只取出試算表ID本身。"""
    text = raw.strip()
    if "/d/" in text:
        text = text.split("/d/", 1)[1]
        text = text.split("/", 1)[0]
    text = text.split(",", 1)[0]
    text = text.split("#", 1)[0]
    text = text.split("?", 1)[0]
    return text.strip()


def _read_registry() -> list[list[str]]:
    service = get_sheets_service()
    res = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=get_master_spreadsheet_id(), range=f"'{REGISTRY_SHEET_NAME}'!A:H")
        .execute()
    )
    return res.get("values", [])


def _find_row(area: str) -> list[str]:
    for row in _read_registry():
        if _cell(row, COL_AREA) == area:
            return row
    raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」登記表找不到地區「{area}」")


_SHEET_TITLE_CACHE: dict[tuple[str, str], str] = {}


def sheet_title_for_gid(service, spreadsheet_id: str, gid: str) -> str:
    """GID→分頁標題；同一次執行內快取，避免短時間內對同一份試算表重複打
    metadata API，觸發 Sheets 每分鐘讀取配額（例如現金缺口試算跑全區時，
    每個地區都要查同一份行銷費用檔案的分頁標題）。"""
    cache_key = (spreadsheet_id, str(gid).strip())
    if cache_key in _SHEET_TITLE_CACHE:
        return _SHEET_TITLE_CACHE[cache_key]

    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if str(props.get("sheetId", "")) == cache_key[1]:
            title = str(props.get("title", ""))
            _SHEET_TITLE_CACHE[cache_key] = title
            return title
    raise RuntimeError(f"試算表 {spreadsheet_id} 找不到 GID={gid} 對應的分頁")


def resolve_statement_location(area: str, report: str) -> tuple[str, str]:
    """回傳 (試算表ID, 分頁標題)。report 為 富邦更新／元大更新／財務／現金。"""
    if report not in REPORT_GID_COLUMN:
        raise ValueError(f"不支援的財報類型：{report}")
    row = _find_row(area)
    spreadsheet_id = _extract_spreadsheet_id(_cell(row, COL_SPREADSHEET_ID))
    gid = _cell(row, REPORT_GID_COLUMN[report])
    if not spreadsheet_id or not gid:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{area}／{report} 缺少試算表ID或GID")
    service = get_sheets_service()
    title = sheet_title_for_gid(service, spreadsheet_id, gid)
    return spreadsheet_id, title


def resolve_standalone_spreadsheet_id(area: str) -> str:
    """回傳 2026-all／2026-現金 這類獨立試算表本身的ID（不含分頁 GID）。"""
    row = _find_row(area)
    spreadsheet_id = _extract_spreadsheet_id(_cell(row, COL_SPREADSHEET_ID))
    if not spreadsheet_id:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{area} 缺少試算表ID")
    return spreadsheet_id


def marketing_expense_spreadsheet_id(area: str = "台北") -> str:
    """行銷費用檔案ID：各地區列共用同一個值，預設抓台北那一列。"""
    row = _find_row(area)
    spreadsheet_id = _extract_spreadsheet_id(_cell(row, COL_MARKETING_ID))
    if not spreadsheet_id:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{area} 缺少行銷費用檔案ID")
    return spreadsheet_id


def review_spreadsheet_id() -> str:
    """2026目標及review檔本身的試算表ID：地區＝「2026」那一列的B欄。"""
    row = _find_row(REVIEW_SPREADSHEET_AREA)
    spreadsheet_id = _extract_spreadsheet_id(_cell(row, COL_SPREADSHEET_ID))
    if not spreadsheet_id:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{REVIEW_SPREADSHEET_AREA} 缺少試算表ID")
    return spreadsheet_id


def resolve_master_review_location() -> tuple[str, str]:
    """回傳 (試算表ID, 分頁標題)：「2026review工作表」這個跨地區彙整的固定分頁。
    GID 登記在地區＝「2026」那一列的 H欄（跟各地區列的「2026review gid」共用
    同一欄，只是這一列代表的不是某個地區自己的分頁，而是彙整分頁本身）。"""
    row = _find_row(REVIEW_SPREADSHEET_AREA)
    gid = _cell(row, COL_REVIEW_GID)
    if not gid:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{REVIEW_SPREADSHEET_AREA} 缺少 2026review工作表 gid")
    spreadsheet_id = review_spreadsheet_id()
    service = get_sheets_service()
    title = sheet_title_for_gid(service, spreadsheet_id, gid)
    return spreadsheet_id, title


def resolve_review_location(area: str) -> tuple[str, str]:
    """回傳 (試算表ID, 分頁標題)：2026目標及review檔裡，該地區對應的分頁。"""
    row = _find_row(area)
    gid = _cell(row, COL_REVIEW_GID)
    if not gid:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{area} 缺少 2026review gid")
    spreadsheet_id = review_spreadsheet_id()
    service = get_sheets_service()
    title = sheet_title_for_gid(service, spreadsheet_id, gid)
    return spreadsheet_id, title
