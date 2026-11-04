"""【財報】營收總表→財報：各地區「{YYYY}{地區}營業額總表」的「{YYYY}財報總表」數字，
貼進該區財報「股東損益表_財務／股東損益表_現金」當月「YYYY.MM預估」欄（2026-11 Jenny）。

各區營業額總表排法不同，依 A～C 欄（基礎／服務／項目）找來源列、依財報 A 欄標題找目標列，
不使用固定列號：依 A～C 欄項目名稱及財報 A 欄列名動態對應，插列或重排不會上下錯置。
來源格子空白的不貼（保留財報原值）；「-」＝0。

"""
from __future__ import annotations

from typing import Callable, Optional

AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
SOURCE_SHEET = "{year}財報總表"
SOURCE_RANGE = "1:40"  # 整列讀：各區欄數不同（台北只到 BX），超出格線會報錯
TARGET_LABEL_RANGE = "A1:A40"
HOLD_FILL = {"red": 0.988, "green": 0.898, "blue": 0.804}  # 淺橘：先不更新、待確認

# 各區營業額總表排法不同（台北／台中／高雄的月份從 2020～2025 年排起、列順序也不一樣），
# 一律依 A～C 欄（基礎／服務／項目）找來源列，依財報 A 欄標題找目標列（2026-11）。
# (服務, 項目, 財報 A 欄可能的標題, 先不更新只標底色)
TARGETS = {
    "財務": {"sheet": "股東損益表_財務", "basis": "服務基礎", "items": [
        ("專業清潔", "當月服務上月預收", ("一般服務預收",), False),
        ("專業清潔", "當月服務收現", ("一般服務本月", "一般服務收入"), False),
        ("專業清潔", "VIP服務", ("VIP券收入",), False),
        ("專業清潔", "儲值金", ("儲值金收入",), False),
        ("家電清潔", "總計", ("家電清潔收入",), False),
        ("家電服務", "總計", ("家電清潔收入",), False),
        ("收納服務", "總計", ("整理收納收入", "茉莉家事收入"), False),
        ("水洗服務", "總計", ("水洗清潔收入",), False),
        ("消毒服務", "總計", ("環境消毒收入",), False),
    ]},
    "現金": {"sheet": "股東損益表_現金", "basis": "現金基礎", "items": [
        ("專業清潔", "當月服務收現", ("一般服務本月", "一般服務收入"), False),
        ("專業清潔", "次月服務預收", ("一般服務預收",), False),
        ("專業清潔", "VIP預收收現", ("VIP券預收", "VIP券收入"), False),
        ("專業清潔", "儲值金預收", ("儲值金收入",), False),
        ("家電清潔", "總計", ("家電清潔收入",), False),
        ("家電服務", "總計", ("家電清潔收入",), False),
        ("收納服務", "總計", ("整理收納收入", "茉莉家事收入"), False),
        ("水洗服務", "總計", ("水洗清潔收入",), False),
        ("消毒服務", "總計", ("環境消毒收入",), False),
    ]},
}
NAME_PATTERNS = ("{year}{area}營業額總表", "{year}營業額總表-{area}")


def column_letter(index: int) -> str:
    """0-based → A1 欄位字母"""
    letters, index = "", index + 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _clean(value) -> str:
    return "".join(str(value or "").split())


def _number(value):
    """營業額總表的格子：數字照用；「-」＝0；空白、#REF!／#N-A 回傳 None（不貼）。"""
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip().replace(",", "")
    if not text or text.startswith("#"):
        return None
    if text.strip("- ") == "":
        return 0
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _month_column(source: list[list], basis: str, label: str) -> int:
    """月份欄：先找這個基礎自己的標題列（新竹／桃園現金基礎在第 14 列），沒有就用第 1 列。"""
    candidates = [row for row in source if row and _clean(row[0]) == basis and label in [_clean(v) for v in row]]
    candidates.append(source[0] if source else [])
    for row in candidates:
        cells = [_clean(v) for v in row]
        if label in cells:
            return cells.index(label)
    return -1


def plan_updates(source: list[list], target_labels: dict[str, list], target_headers: dict[str, list],
                 period: str) -> tuple[dict[str, list], list[str]]:
    """純邏輯：source＝營業額總表「財報總表」（未格式化值）；target_labels＝{財務／現金: 財報 A 欄}；
    target_headers＝{財務／現金: 財報第 1 列}。回傳 ({財務／現金: [(列, 欄字母, 值, 先不更新只標底色)]}, 說明)。"""
    label = f"{period[:4]}.{period[4:]}"
    plans: dict[str, list] = {}
    notes: list[str] = []
    for kind, spec in TARGETS.items():
        src_col = _month_column(source, spec["basis"], label)
        if src_col < 0:
            notes.append(f"{kind}：營業額總表找不到 {label} 欄")
            continue
        header = [_clean(v) for v in target_headers.get(kind, [])]
        if f"{label}預估" not in header:
            notes.append(f"{kind}：「{spec['sheet']}」第 1 列找不到 {label}預估")
            continue
        dst_col = column_letter(header.index(f"{label}預估"))
        titles = [_clean(r[0]) if r else "" for r in target_labels.get(kind, [])]
        plans[kind] = []
        used = set()
        for service, item, names, hold in spec["items"]:
            row = next((r for r in source if len(r) > 2 and _clean(r[0]) == spec["basis"]
                        and _clean(r[1]) == service and _clean(r[2]) == item), None)
            if row is None:
                continue
            value = _number(row[src_col] if src_col < len(row) else "")
            dst_row = next((titles.index(_clean(n)) + 1 for n in names if _clean(n) in titles), 0)
            if value is None or not dst_row or dst_row in used:
                continue
            used.add(dst_row)
            plans[kind].append((dst_row, dst_col, value, hold))
    return plans, notes


def find_revenue_file(area: str, year: int, drive=None) -> str:
    """找「{YYYY}{地區}營業額總表」（2027 起「{YYYY}營業額總表-{地區}」）：先依年度根目錄，找不到再依檔名搜尋。"""
    try:
        from tools.common.year_roots import root_file_id

        found = root_file_id("營業額總表", area, year)
        if found:
            return found
    except Exception:
        pass
    if drive is None:
        from services.google_auth import get_drive_service

        drive = get_drive_service()
    for pattern in NAME_PATTERNS:
        name = pattern.format(year=year, area=area).replace("'", "\\'")
        files = drive.files().list(
            q=f"name='{name}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            fields="files(id,name,modifiedTime)", orderBy="modifiedTime desc", pageSize=5,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
        if files:
            return files[0]["id"]
    raise RuntimeError(f"找不到 {year}{area}營業額總表")


def sync_area(area: str, period: str, log: Callable[[str], None] = print, service=None) -> int:
    """回傳寫入的格數。"""
    from tools.common.config_loader import get_sheets_service
    from tools.finance_management.statement_registry import resolve_statement_location

    service = service or get_sheets_service()
    year = int(period[:4])
    source_id = find_revenue_file(area, year)
    source = service.spreadsheets().values().get(
        spreadsheetId=source_id, range=f"'{SOURCE_SHEET.format(year=year)}'!{SOURCE_RANGE}",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    locations = {kind: resolve_statement_location(area, kind, service=service, year=year) for kind in TARGETS}
    headers = {kind: (service.spreadsheets().values().get(spreadsheetId=sid, range=f"'{title}'!1:1").execute()
                      .get("values", [[]]) or [[]])[0] for kind, (sid, title) in locations.items()}
    labels = {kind: service.spreadsheets().values().get(spreadsheetId=sid, range=f"'{title}'!{TARGET_LABEL_RANGE}")
              .execute().get("values", []) for kind, (sid, title) in locations.items()}
    plans, notes = plan_updates(source, labels, headers, period)
    for note in notes:
        log(f"⚠️ {area} {note}")
    written = 0
    for kind, cells in plans.items():
        sid, title = locations[kind]
        writes = [c for c in cells if not c[3]]
        holds = [c for c in cells if c[3]]
        if writes:
            service.spreadsheets().values().batchUpdate(spreadsheetId=sid, body={
                "valueInputOption": "USER_ENTERED",
                "data": [{"range": f"'{title}'!{col}{row}", "values": [[value]]} for row, col, value, _h in writes]}
            ).execute()
            written += len(writes)
            log(f"【{area}】{title} {writes[0][1]} 欄（{period[:4]}.{period[4:]}預估）已貼 {len(writes)} 格")
        if holds:
            mark_holds(service, sid, title, holds, period)
            log(f"【{area}】{title} 第 {'、'.join(str(c[0]) for c in holds)} 列先不更新，已標淺橘底並加註解（營業額總表："
                + "、".join(f"{c[0]}列 {_shown(c[2])}" for c in holds) + "）")
    return written


def _shown(value) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) else str(value)


def _column_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def hold_requests(sheet_id: int, cells: list[tuple], period: str) -> list[dict]:
    """先不更新的格子：淺橘底＋註解（營業額總表的值），不動格子內容。"""
    requests = []
    for row, col, value, _hold in cells:
        col_index = _column_index(col)
        requests.append({"updateCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": row - 1, "endRowIndex": row,
                      "startColumnIndex": col_index, "endColumnIndex": col_index + 1},
            "rows": [{"values": [{"userEnteredFormat": {"backgroundColor": HOLD_FILL},
                                  "note": f"{period[:4]}.{period[4:]} 營業額總表：{_shown(value)}（對應待確認，先不更新）"}]}],
            "fields": "userEnteredFormat.backgroundColor,note"}})
    return requests


def mark_holds(service, spreadsheet_id: str, title: str, cells: list[tuple], period: str) -> None:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    sheet_id = next(s["properties"]["sheetId"] for s in meta.get("sheets", []) if s["properties"]["title"] == title)
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id,
                                       body={"requests": hold_requests(sheet_id, cells, period)}).execute()


def run(period: str, areas: Optional[list[str]] = None, log: Callable[[str], None] = print) -> list[str]:
    """回傳失敗清單（空＝全部成功）。"""
    failures = []
    for area in areas or AREAS:
        try:
            sync_area(area, period, log=log)
        except Exception as exc:
            failures.append(f"{area}：{exc}")
            log(f"❌ {area} 營收總表→財報失敗：{exc}")
    return failures
