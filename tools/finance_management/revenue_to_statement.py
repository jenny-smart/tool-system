"""【財報】營收總表→財報：各地區「{YYYY}{地區}營業額總表」的「{YYYY}財報總表」數字，
貼進該區財報「股東損益表_財務／股東損益表_現金」當月「YYYY.MM預估」欄（2026-11 Jenny）。

財務（服務基礎，標題列第 1 列）→ 股東損益表_財務：
  2 當月服務收現→4、3 當月服務上月預收→5、6 儲值金→8、
  8 家電清潔→9、9 收納服務→10、10 水洗服務→11、11 消毒服務→12
現金（現金基礎，標題列第 14 列）→ 股東損益表_現金：
  15 當月服務收現→4、16 次月服務預收→5、18 儲值金預收→8、
  20 家電清潔→9、21 收納服務→10、22 水洗服務→11、23 消毒服務→12
來源格子空白的不貼（保留財報原值）；「-」＝0。

對應還沒確認的列（財務 4、5：財報 1～4 月跟 5 月起放法相反；現金 8：用 18 還是 19 列），
先不更新數字，只把財報那格標淺橘底，並加註解寫營業額總表的值，讓 Jenny 對照（2026-11）。
"""
from __future__ import annotations

from typing import Callable, Optional

AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
SOURCE_SHEET = "{year}財報總表"
SOURCE_RANGE = "A1:P30"
TARGETS = {
    "財務": {"sheet": "股東損益表_財務", "header_row": 1,
             "rows": {2: 4, 3: 5, 6: 8, 8: 9, 9: 10, 10: 11, 11: 12}, "hold": {4, 5}},
    "現金": {"sheet": "股東損益表_現金", "header_row": 14,
             "rows": {15: 4, 16: 5, 18: 8, 20: 9, 21: 10, 22: 11, 23: 12}, "hold": {8}},
}
HOLD_FILL = {"red": 0.988, "green": 0.898, "blue": 0.804}  # 淺橘：先不更新、待確認
NAME_PATTERNS = ("{year}{area}營業額總表", "{year}營業額總表-{area}")


def column_letter(index: int) -> str:
    """0-based → A1 欄位字母"""
    letters, index = "", index + 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _cell(rows: list[list], row: int, col: int):
    values = rows[row - 1] if 0 < row <= len(rows) else []
    return values[col] if 0 <= col < len(values) else ""


def _number(value):
    """營業額總表的格子：數字照用；「-」＝0；空白回傳 None（不貼）。"""
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    if text.strip("- ") == "":
        return 0
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def plan_updates(source: list[list], target_headers: dict[str, list], period: str) -> tuple[dict[str, list], list[str]]:
    """純邏輯：source＝營業額總表「財報總表」A1:P30（未格式化值），target_headers＝{財務／現金: 財報第 1 列}。
    回傳 ({財務／現金: [(列, 欄字母, 值, 先不更新只標底色)]}, 說明)。"""
    label = f"{period[:4]}.{period[4:]}"
    plans: dict[str, list] = {}
    notes: list[str] = []
    for kind, spec in TARGETS.items():
        source_header = [str(v).strip() for v in (source[spec["header_row"] - 1] if len(source) >= spec["header_row"] else [])]
        if label not in source_header:
            notes.append(f"{kind}：營業額總表第 {spec['header_row']} 列找不到 {label}")
            continue
        src_col = source_header.index(label)
        header = [str(v).strip() for v in target_headers.get(kind, [])]
        if f"{label}預估" not in header:
            notes.append(f"{kind}：「{spec['sheet']}」第 1 列找不到 {label}預估")
            continue
        dst_col = column_letter(header.index(f"{label}預估"))
        plans[kind] = []
        for src_row, dst_row in spec["rows"].items():
            value = _number(_cell(source, src_row, src_col))
            if value is not None:
                plans[kind].append((dst_row, dst_col, value, dst_row in spec.get("hold", ())))
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
    locations = {kind: resolve_statement_location(area, kind, service=service) for kind in TARGETS}
    headers = {kind: service.spreadsheets().values().get(spreadsheetId=sid, range=f"'{title}'!1:1").execute()
               .get("values", [[]])[0] for kind, (sid, title) in locations.items()}
    plans, notes = plan_updates(source, headers, period)
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
