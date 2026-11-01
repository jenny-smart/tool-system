"""【財報】營收總表→財報：各地區「{YYYY}{地區}營業額總表」的「{YYYY}財報總表」數字，
貼進該區財報「股東損益表_財務／股東損益表_現金」當月「YYYY.MM預估」欄（2026-11 Jenny）。

財務（服務基礎，標題列第 1 列）→ 股東損益表_財務：
  2 當月服務收現→4、3 當月服務上月預收→5、6 儲值金→8、
  8 家電清潔→9、9 收納服務→10、10 水洗服務→11、11 消毒服務→12
現金（現金基礎，標題列第 14 列）→ 股東損益表_現金：
  15 當月服務收現→4、16 次月服務預收→5、18 儲值金預收→8、
  20 家電清潔→9、21 收納服務→10、22 水洗服務→11、23 消毒服務→12
來源格子空白的不貼（保留財報原值）；「-」＝0。
"""
from __future__ import annotations

from typing import Callable, Optional

AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
SOURCE_SHEET = "{year}財報總表"
SOURCE_RANGE = "A1:P30"
TARGETS = {
    "財務": {"sheet": "股東損益表_財務", "header_row": 1,
             "rows": {2: 4, 3: 5, 6: 8, 8: 9, 9: 10, 10: 11, 11: 12}},
    "現金": {"sheet": "股東損益表_現金", "header_row": 14,
             "rows": {15: 4, 16: 5, 18: 8, 20: 9, 21: 10, 22: 11, 23: 12}},
}
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
    回傳 ({財務／現金: [(列, 欄字母, 值)]}, 說明)。"""
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
                plans[kind].append((dst_row, dst_col, value))
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
        if not cells:
            continue
        service.spreadsheets().values().batchUpdate(spreadsheetId=sid, body={
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": f"'{title}'!{col}{row}", "values": [[value]]} for row, col, value in cells]}).execute()
        written += len(cells)
        log(f"【{area}】{title} {cells[0][1]} 欄（{period[:4]}.{period[4:]}預估）已貼 {len(cells)} 格")
    return written


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
