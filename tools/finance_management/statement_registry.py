"""財報設定登記表查詢：讀主控試算表（Jenny's Lemonhometools）的「財報設定」分頁，
取得各地區財報試算表 ID 與共用的行銷費用檔案 ID。

分頁一律依名稱找（換年 GID 會變，不再讀 C／D／E／F／H／I 欄的 GID）：
  財報：富邦更新／元大更新／股東損益表_財務／股東損益表_現金
  行銷費用總管理：{YYYY}總表（今年）
  目標及review檔：各地區「{地區}財報」、跨地區彙整「{YYYY}review」

分頁欄位（A 起，依欄位順序讀取）：A 地區　B 試算表ID　G 行銷費用檔案ID。
另有地區＝「2026」（或今年）的列，B 欄是「目標及review檔」本身的 ID；
2026-all／2026-現金 兩列的 B 欄是獨立的「All財報」／「All財報現金缺口」試算表。
"""

from __future__ import annotations

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.common.year_roots import choose_file_id, taipei_year

REGISTRY_SHEET_NAME = "財報設定"

COL_AREA = 1
COL_SPREADSHEET_ID = 2
COL_MARKETING_ID = 7

# 財報類型 → 財報裡的分頁名稱
REPORT_SHEET_TITLES = {
    "富邦更新": "富邦更新",
    "元大更新": "元大更新",
    "財務": "股東損益表_財務",
    "現金": "股東損益表_現金",
}

ALL_REPORT_AREA = "2026-all"
CASH_GAP_AREA = "2026-現金"
REVIEW_SPREADSHEET_AREA = "2026"


def _review_row() -> list[str]:
    """年度review那一列：優先找地區＝今年（例如 2027）的列，沒有才用「2026」列。"""
    try:
        return _find_row(str(taipei_year()))
    except RuntimeError:
        return _find_row(REVIEW_SPREADSHEET_AREA)


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
        .get(spreadsheetId=get_master_spreadsheet_id(), range=f"'{REGISTRY_SHEET_NAME}'!A:I")
        .execute()
    )
    return res.get("values", [])


def _find_row(area: str) -> list[str]:
    for row in _read_registry():
        if _cell(row, COL_AREA) == area:
            return row
    raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」登記表找不到地區「{area}」")


_TITLES_CACHE: dict[str, set[str]] = {}


def require_sheet_title(service, spreadsheet_id: str, title: str) -> str:
    """確認分頁存在（依名稱，不用 GID）；找不到就報錯。同一次執行內快取分頁清單，
    避免短時間內對同一份試算表重複打 metadata API 觸發配額。"""
    if spreadsheet_id not in _TITLES_CACHE:
        meta = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
        ).execute()
        _TITLES_CACHE[spreadsheet_id] = {
            str(sheet.get("properties", {}).get("title", "")) for sheet in meta.get("sheets", [])
        }
    if title not in _TITLES_CACHE[spreadsheet_id]:
        raise RuntimeError(f"試算表 {spreadsheet_id} 找不到「{title}」分頁")
    return title


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
    if report not in REPORT_SHEET_TITLES:
        raise ValueError(f"不支援的財報類型：{report}")
    row = _find_row(area)
    spreadsheet_id = choose_file_id(
        "財報", area, _extract_spreadsheet_id(_cell(row, COL_SPREADSHEET_ID))
    )
    if not spreadsheet_id:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{area} 缺少試算表ID")
    title = require_sheet_title(get_sheets_service(), spreadsheet_id, REPORT_SHEET_TITLES[report])
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


def resolve_marketing_expense_location(area: str = "台北") -> tuple[str, str]:
    """回傳行銷費用來源的 (試算表ID, 今年的「{YYYY}總表」分頁)；檔案ID 登記在 G 欄。
    生成新年度會在同一個行銷費用檔複製出新年度工作表，依名稱找就不必每年改設定。"""
    row = _find_row(area)
    spreadsheet_id = _extract_spreadsheet_id(_cell(row, COL_MARKETING_ID))
    if not spreadsheet_id:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{area} 缺少行銷ID")
    title = require_sheet_title(get_sheets_service(), spreadsheet_id, f"{taipei_year()}總表")
    return spreadsheet_id, title


def review_spreadsheet_id() -> str:
    """2026目標及review檔本身的試算表ID：地區＝「2026」那一列的B欄。"""
    row = _review_row()
    spreadsheet_id = choose_file_id(
        "目標及review", "共用", _extract_spreadsheet_id(_cell(row, COL_SPREADSHEET_ID))
    )
    if not spreadsheet_id:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{REVIEW_SPREADSHEET_AREA} 缺少試算表ID")
    return spreadsheet_id


def resolve_master_review_location() -> tuple[str, str]:
    """回傳 (試算表ID, 分頁標題)：跨地區彙整的「{YYYY}review」分頁（例如 2026review）。"""
    spreadsheet_id = review_spreadsheet_id()
    title = require_sheet_title(get_sheets_service(), spreadsheet_id, f"{taipei_year()}review")
    return spreadsheet_id, title


def resolve_review_location(area: str) -> tuple[str, str]:
    """回傳 (試算表ID, 分頁標題)：目標及review檔裡，該地區的「{地區}財報」分頁。"""
    spreadsheet_id = review_spreadsheet_id()
    title = require_sheet_title(get_sheets_service(), spreadsheet_id, f"{area}財報")
    return spreadsheet_id, title
