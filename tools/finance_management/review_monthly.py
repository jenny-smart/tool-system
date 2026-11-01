"""年度review 每月整理（每月 1 日排程；「當月」＝上個月，例如 11/1 做 2026.10）。

1. layout()：整理各區財報／{YYYY}review 分頁的欄位顯示（2026-11 與 Jenny 確認）
   - 先清掉月份欄範圍內所有舊的欄位分組、取消隱藏（舊程式只會疊分組，越疊越亂）
   - 當月之前：只顯示「實際」（預估收起）
   - 當月：只顯示「預估」（實際收起）
   - 當月之後：實際、預估都收起
   - 年度合計「YYYY實際」「YYYY預估」一律顯示
   收起＝連續的欄位分成一組並隱藏（保留展開箭頭）。打開檔案時凍結欄右邊就是各月實際，接著當月預估。
2. 年度review 預估 SUMIF 公式範圍調整到當月（沿用 review_update.apply_review_formula_updates）。
3. sync_cash_gap_block()：把上個月的儲值金彙整、現金缺口試算寫進 review 檔「現金缺口」分頁該月的區塊
   - 區塊：每 8 欄一塊（標題欄＋台北／台中／桃園／新竹／高雄／ALL），1 月從 I 欄開始，每月往右 8 欄
   - 第 2 列日期改成月底
   - 第 3～10 列：儲值金彙整檔「VIP總表」O3:U10 整塊（格式相同）
   - 現金餘額＝富邦餘額＋元大餘額；藍新ATM／信用卡／ATM／家電／水洗服務金額＝預收款金額；
     內勤薪資；專員薪資＝專員承攬費；行銷費用＝廣告＋服務；2%支出；營業稅；相關費用＝藍新發票金額；
     年終費用；春酒費用（都從主控表「現金缺口試算」讀，要先算好同一個期別）
   - 現金總金額、待支出費用加總、實際現金餘額、現金缺口是公式：空白才補，不覆蓋
   - 讀到「錯誤：…」的格子不寫，列在結果裡
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date
from typing import Callable, Optional

REVIEW_SHEETS = ["台北", "台中", "桃園", "新竹", "高雄", "年度review"]
CASH_GAP_REVIEW_SHEET = "現金缺口"
BLOCK_START_COL = 8  # I 欄＝1 月區塊的標題欄（0-based）
BLOCK_WIDTH = 8
AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
VIP_SUMMARY_SHEET = "VIP總表"
VIP_SUMMARY_RANGE = "O3:U10"

_MONTH_RE = re.compile(r"^(\d{2,4})\.(\d{1,2})(實際|預估)$")
_YEAR_RE = re.compile(r"^(\d{4})(實際|預估)$")


# ── 1. 欄位顯示 ─────────────────────────────────────

def classify_header(header_row: list) -> dict[int, tuple]:
    """{欄 index: ("month", 實際/預估, (年, 月)) 或 ("year", 實際/預估, 年)}"""
    result = {}
    for idx, value in enumerate(header_row):
        text = str(value).strip()
        if m := _MONTH_RE.match(text):
            year = int(m.group(1)) if len(m.group(1)) == 4 else 2000 + int(m.group(1))
            result[idx] = ("month", m.group(3), (year, int(m.group(2))))
        elif m := _YEAR_RE.match(text):
            result[idx] = ("year", m.group(2), int(m.group(1)))
    return result


def hidden_columns(columns: dict[int, tuple], target: tuple[int, int]) -> list[int]:
    hidden = []
    for idx, (kind, label, ym) in sorted(columns.items()):
        if kind != "month":
            continue
        if ym < target and label == "預估":
            hidden.append(idx)
        elif ym == target and label == "實際":
            hidden.append(idx)
        elif ym > target:
            hidden.append(idx)
    return hidden


def runs(indexes: list[int]) -> list[tuple[int, int]]:
    """連續欄位合成 [start, end) 區段。"""
    result: list[list[int]] = []
    for idx in sorted(indexes):
        if result and result[-1][1] == idx:
            result[-1][1] = idx + 1
        else:
            result.append([idx, idx + 1])
    return [tuple(r) for r in result]


def layout_requests(sheet_id: int, columns: dict[int, tuple], groups: list[dict],
                    target: tuple[int, int]) -> tuple[list[dict], list[dict], int]:
    """回傳 (清除用 requests, 重新分組用 requests, 收起的欄數)。只動月份欄範圍內的分組。"""
    if not columns:
        return [], [], 0
    span = (min(columns), max(columns) + 1)
    rng = lambda start, end: {"sheetId": sheet_id, "dimension": "COLUMNS",  # noqa: E731
                              "startIndex": start, "endIndex": end}
    mine = [g for g in groups if span[0] <= g["range"]["startIndex"] and g["range"]["endIndex"] <= span[1]]
    clear = [{"deleteDimensionGroup": {"range": rng(g["range"]["startIndex"], g["range"]["endIndex"])}}
             for g in sorted(mine, key=lambda g: -g.get("depth", 1))]
    clear.append({"updateDimensionProperties": {"range": rng(*span), "properties": {"hiddenByUser": False},
                                                "fields": "hiddenByUser"}})
    hide = hidden_columns(columns, target)
    regroup = []
    for start, end in runs(hide):
        regroup.append({"addDimensionGroup": {"range": rng(start, end)}})
        regroup.append({"updateDimensionProperties": {"range": rng(start, end), "properties": {"hiddenByUser": True},
                                                      "fields": "hiddenByUser"}})
    return clear, regroup, len(hide)


def review_service():
    """review 檔用 Jenny 的 OAuth 寫入：服務帳號沒有 2026目標及review 的編輯權限（Mac 上實際 403）。
    沒有 OAuth 設定時才退回服務帳號。"""
    try:
        from services.google_auth import get_sheets_service as oauth_sheets

        return oauth_sheets()
    except Exception:
        from tools.common.config_loader import get_sheets_service

        return get_sheets_service()


def layout(area: str, period: str, log: Callable[[str], None] = print) -> int:
    from tools.finance_management.review_update import _resolve_location

    spreadsheet_id, title = _resolve_location(area)
    service = review_service()
    header = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{title}'!1:1",
                                                 valueRenderOption="FORMATTED_VALUE").execute().get("values", [[]])
    columns = classify_header(header[0] if header else [])
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                      fields="sheets(properties(sheetId,title),columnGroups)").execute()
    sheet = next(s for s in meta.get("sheets", []) if s["properties"]["title"] == title)
    clear, regroup, hidden = layout_requests(sheet["properties"]["sheetId"], columns, sheet.get("columnGroups", []),
                                             (int(period[:4]), int(period[4:])))
    if clear:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": clear}).execute()
    if regroup:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": regroup}).execute()
    log(f"【{title}】欄位整理完成：收起 {hidden} 欄（當月 {period[:4]}.{period[4:]} 看預估）")
    return hidden


# ── 3. 現金缺口區塊 ──────────────────────────────────

def column_letter(index: int) -> str:
    """0-based index → 欄位字母。"""
    letters, index = "", index + 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def block_start(month: int) -> int:
    return BLOCK_START_COL + BLOCK_WIDTH * (month - 1)


# 現金缺口區塊列號 → (標題, 現金缺口試算的列標題（多個＝加總）)
CASH_ROWS = {
    14: ("現金餘額", ("富邦餘額", "元大餘額")),
    15: ("藍新ATM服務金額", ("藍新ATM服務金額",)),
    16: ("信用卡服務金額", ("信用卡服務金額",)),
    17: ("ATM服務金額", ("ATM服務金額",)),
    18: ("家電服務金額", ("家電服務金額",)),
    19: ("水洗服務金額", ("水洗服務金額",)),
    21: ("內勤薪資", ("內勤薪資",)),
    22: ("專員薪資", ("專員承攬費",)),
    23: ("行銷費用", ("行銷費用(廣告)", "行銷費用(服務)")),
    24: ("2%支出", ("2%",)),
    25: ("營業稅", ("營業稅",)),
    26: ("相關費用", ("藍新發票金額",)),
    27: ("年終費用", ("年終費用",)),
    28: ("春酒費用", ("春酒費用",)),
}
# 公式列：空白才補（{c}＝該欄字母）
FORMULA_ROWS = {
    20: "={c}14-SUM({c}15:{c}19)",
    29: "=SUM({c}21:{c}28)",
    32: "={c}20-{c}29+{c}30+{c}31",
    33: "={c}32-{c}10",
}


def _number(value) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None  # 「錯誤：…」之類


def cash_gap_values(values: list[list]) -> dict[str, dict[str, Optional[float]]]:
    """現金缺口試算（A 標題、B～F 台北～高雄）→ {標題: {地區: 數字或 None（錯誤）}}"""
    header = [str(v).strip() for v in (values[0] if values else [])]
    cols = {area: header.index(area) for area in AREAS if area in header}
    table = {}
    for row in values[2:]:
        label = str(row[0] if row else "").strip()
        if label:
            table[label] = {area: _number(row[i] if i < len(row) else "") for area, i in cols.items()}
    return table


def build_block_updates(title: str, month_end: date, vip_block: list[list], cash: dict, existing: list[list],
                        period_label: str) -> tuple[list[dict], list[str]]:
    """純邏輯：回傳 (values batchUpdate 的 data, 略過的格子說明)。existing＝區塊目前的公式（從第 1 列起）。"""
    start = block_start(month_end.month)
    label_col = column_letter(start)
    area_cols = {area: column_letter(start + 1 + i) for i, area in enumerate(AREAS)}
    all_col = column_letter(start + 6)
    data, skipped = [], []
    date_text = f"{month_end.year}/{month_end.month}/{month_end.day}"
    data.append({"range": f"'{title}'!{label_col}2:{all_col}2", "values": [[date_text] * 7]})
    if vip_block:
        data.append({"range": f"'{title}'!{label_col}3:{all_col}10", "values": vip_block})
    else:
        skipped.append("儲值金彙整（找不到 VIP總表）")
    for row, (label, sources) in CASH_ROWS.items():
        values = [label]
        for area in AREAS:
            parts = [cash.get(src, {}).get(area) for src in sources]
            if any(p is None for p in parts):
                skipped.append(f"{area} {label}")
                values.append(None)
            else:
                values.append(round(sum(parts)))
        for i, value in enumerate(values):
            col = label_col if i == 0 else area_cols[AREAS[i - 1]]
            if value is not None:
                data.append({"range": f"'{title}'!{col}{row}", "values": [[value]]})
    data.append({"range": f"'{title}'!{label_col}33", "values": [[f"{period_label}-現金缺口"]]})
    for row, formula in FORMULA_ROWS.items():
        current = existing[row - 1] if row - 1 < len(existing) else []
        for i, area in enumerate(AREAS):
            cell = current[1 + i] if 1 + i < len(current) else ""
            if str(cell).strip() == "":
                data.append({"range": f"'{title}'!{area_cols[area]}{row}",
                             "values": [[formula.format(c=area_cols[area])]]})
    return data, skipped


def _find_vip_summary(period: str) -> Optional[str]:
    from services.google_auth import get_drive_service

    name = f"{period}VIP儲值金彙整"
    files = get_drive_service().files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
        fields="files(id,name,modifiedTime)", orderBy="modifiedTime desc", pageSize=5,
        supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    return files[0]["id"] if files else None


def sync_cash_gap_block(period: str, log: Callable[[str], None] = print) -> list[str]:
    """寫入 review 檔「現金缺口」分頁 period 那個月的區塊；回傳略過的格子。"""
    from services.google_auth import get_sheets_service as oauth_sheets
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
    from tools.finance_management.cash_gap import CASH_GAP_SHEET_NAME
    from tools.finance_management.statement_registry import review_spreadsheet_id

    year, month = int(period[:4]), int(period[4:])
    month_end = date(year, month, monthrange(year, month)[1])
    service = get_sheets_service()

    cash_raw = service.spreadsheets().values().get(
        spreadsheetId=get_master_spreadsheet_id(), range=f"'{CASH_GAP_SHEET_NAME}'!A1:G30",
        valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING").execute().get("values", [])
    cash_date = str(cash_raw[1][1]).strip() if len(cash_raw) > 1 and len(cash_raw[1]) > 1 else ""
    if cash_date.replace("-", "/") != month_end.strftime("%Y/%m/%d"):
        raise RuntimeError(f"現金缺口試算目前是 {cash_date or '空白'}，不是 {month_end:%Y/%m/%d}；請先算 {period} 的現金缺口")
    cash = cash_gap_values(cash_raw)

    vip_block: list[list] = []
    vip_id = _find_vip_summary(period)
    if vip_id:
        vip_block = oauth_sheets().spreadsheets().values().get(
            spreadsheetId=vip_id, range=f"'{VIP_SUMMARY_SHEET}'!{VIP_SUMMARY_RANGE}",
            valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    else:
        log(f"⚠️ 找不到 {period}VIP儲值金彙整，儲值金那幾列先不寫")

    review_id = review_spreadsheet_id()
    start = block_start(month)
    first, last = column_letter(start), column_letter(start + 6)
    service = review_service()
    existing = service.spreadsheets().values().get(
        spreadsheetId=review_id, range=f"'{CASH_GAP_REVIEW_SHEET}'!{first}1:{last}34",
        valueRenderOption="FORMULA").execute().get("values", [])
    header = existing[0] if existing else []
    if [str(v).strip() for v in header[1:6]] != AREAS:
        raise RuntimeError(f"「{CASH_GAP_REVIEW_SHEET}」{first}～{last} 第 1 列不是 台北～高雄，區塊位置不對，先不寫入")
    data, skipped = build_block_updates(CASH_GAP_REVIEW_SHEET, month_end, vip_block, cash, existing,
                                        f"{year}/{month:02d}")
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=review_id, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
    log(f"【現金缺口】{year}/{month:02d} 區塊（{first}～{last}）已更新" +
        (f"；略過：{'、'.join(skipped)}" if skipped else ""))
    return skipped


# ── 全部 ─────────────────────────────────────────────

def run_all(period: str, log: Callable[[str], None] = print, include_cash_gap: bool = True) -> list[str]:
    """回傳失敗清單（空＝全部成功）。"""
    from tools.finance_management import review_update
    from tools.finance_management.review_update import apply_review_formula_updates

    review_update.get_sheets_service = review_service  # 公式調整也用 OAuth 寫 review 檔
    failures = []
    year_month = f"{period[:4]}.{period[4:]}"
    for area in REVIEW_SHEETS:
        try:
            layout(area, period, log=log)
        except Exception as exc:
            failures.append(f"{area} 欄位整理：{exc}")
            log(f"❌ {area} 欄位整理失敗：{exc}")
        try:
            changed = apply_review_formula_updates(area, year_month)["changed"]
            log(f"【{area}】預估公式調整 {changed} 格")
        except Exception as exc:
            failures.append(f"{area} 公式調整：{exc}")
            log(f"❌ {area} 預估公式調整失敗：{exc}")
    if include_cash_gap:
        try:
            sync_cash_gap_block(period, log=log)
        except Exception as exc:
            failures.append(f"現金缺口區塊：{exc}")
            log(f"❌ 現金缺口區塊寫入失敗：{exc}")
    return failures
