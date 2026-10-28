"""工具包押金退款金額（H 欄）＋新訓退訓實境費用。

步驟（同一份試算表，依登記表「工具包押金退款／地區」找到）：
1. 工具包押金退款 H 欄（只處理 I 欄退款日期空白＝尚未匯款、且 H 欄空白的列；
   填入時整列套用上一列格式，J/M/N/O/P/Q/R 欄沿用上一列公式）：
   = 新人實境＋新人實習 中 C 欄＝本列 B 欄姓名、且 A 欄空白的列 J×K 加總
   ＋ 工具包押金 A 欄＝本列 B 欄姓名的 B 欄金額
   （新人實境、新人實習從該人在「基本資料、協議規章」的建檔年度開始找，
   逐年讀「{年度}專員薪資相關-{地區}」加總到今年度；名冊沒有此人時，今年度找不到才找前一年度）
2. 新訓退訓實境費用：只往下補新人，表上已有的列（含手動加的）都保留；
   工具包押金退款 H > 2000、且表上還沒有的人，接在 A3 起最後一筆之後填姓名，B 欄＝H − 2000；
   D 欄若 D3 是公式就把公式往下填滿（公式改成只回傳身分證，不再溢出到 E–F）；
   E–F 取「{今年}專員名冊與時數-{地區}」的「基本資料、協議規章」E 欄生日、G 欄地址，
   找不到才沿用既有資料或工具包押金 M–N；生日是 4 位數西元年（2026 年起建檔都是）
   一律減 1911 轉成民國年（例如 1997/2/4 → 86/02/04），已是民國年的原樣帶入。
3. C 欄（新增的人，以及既有列尚未打勾的人）：雲端資料夾（根目錄或 YYYY新訓退訓 底下）有該人姓名的資料夾、
   且資料夾內有檔案（身分證）即打勾。
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from tools.bank_statement.internal_payment_registry import (
    DEPOSIT_REFUND_TYPE,
    resolve_report_location,
)
from tools.common.config_loader import get_sheets_service

TW_TZ = ZoneInfo("Asia/Taipei")
DEPOSIT_SHEET = "工具包押金"
FIELD_SHEETS = ("新人實境", "新人實習")
FEE_SHEET = "新訓退訓實境費用"
DEPOSIT_BASE = Decimal(2000)
ID_ROOT_FOLDER_ID = os.getenv(
    "TRAINEE_ID_ROOT_FOLDER_ID", "1Keke_afpPNgy6ZZYphHRkLW3N6M1S2Ks"
)
_YEAR_FOLDER_RE = re.compile(r"^\d{4}新訓退訓$")
FOLDER_MIME = "application/vnd.google-apps.folder"
ROSTER_NAME = "{year}專員名冊與時數-{area}"
SALARY_NAME = "{year}專員薪資相關-{area}"
ROSTER_SHEET = "基本資料、協議規章"
_ID_ONLY_RE = re.compile(r"\{\s*2\s*(?:,\s*\d+\s*)+\}")


def _cell(row: list, idx: int) -> str:
    return str(row[idx] if idx < len(row) else "").strip()


def _num(text: str) -> Decimal:
    cleaned = re.sub(r"[^0-9.\-]", "", unicodedata.normalize("NFKC", text))
    try:
        return Decimal(cleaned) if cleaned else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _fmt(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral() else float(value)


def to_roc_date(text: str) -> str:
    """生日統一成民國年 YY/MM/DD（例如 1997/2/4、860204、86.2.4、民國86年2月4日 → 86/02/04）。

    無法辨識的寫法（打錯字等）回傳空白，讓人工檢查補填。
    """
    cleaned = unicodedata.normalize("NFKC", text.strip()).replace("民國", "")
    cleaned = re.sub(r"[年月]", "/", cleaned).replace("日", "").strip(" /")
    parts = [p for p in re.split(r"[/.\-, ]+", cleaned) if p]
    if len(parts) == 1 and parts[0].isdigit():
        digits = parts[0]
        if len(digits) == 8:
            parts = [digits[:4], digits[4:6], digits[6:]]
        elif len(digits) in (6, 7):
            parts = [digits[:-4], digits[-4:-2], digits[-2:]]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return ""
    year, month, day = (int(p) for p in parts)
    if year >= 1912:
        year -= 1911
    if not (1 <= year <= 150 and 1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:02d}/{month:02d}/{day:02d}"


def roster_info(values: list[list]) -> dict[str, tuple[str, str]]:
    """基本資料、協議規章：C 欄姓名 → (E 欄生日, G 欄地址)，同名取最後一筆。

    生日寫法不一（2026 年起建檔是西元年，之前多為民國年但格式不一），
    一律統一成民國年 YY/MM/DD。
    """
    result: dict[str, tuple[str, str]] = {}
    for row in values[1:]:
        name = _cell(row, 2)
        if name:
            result[name] = (to_roc_date(_cell(row, 4)), _cell(row, 6))
    return result


def roster_start_years(values: list[list]) -> dict[str, int]:
    """基本資料、協議規章：C 欄姓名 → A 欄時間戳記的建檔年份（同名取最後一筆）。"""
    result: dict[str, int] = {}
    for row in values[1:]:
        name = _cell(row, 2)
        year = re.match(r"^\s*(\d{4})", _cell(row, 0))
        if name and year:
            result[name] = int(year.group(1))
    return result


def field_fees(sheets: list[list[list]]) -> dict[str, Decimal]:
    """新人實境／新人實習：依 C 欄姓名加總 J×K。

    A 欄空白（未付）或「YYYY/MM/DD已匯款」（隨押金退款一起匯）都算；
    A 欄是期別（例如 202610-1，已隨薪資發放）的不算。
    """
    totals: dict[str, Decimal] = {}
    for values in sheets:
        for row in values[1:]:
            name, paid = _cell(row, 2), _cell(row, 0)
            if not name or (paid and "已匯款" not in paid):
                continue
            totals[name] = totals.get(name, Decimal(0)) + _num(_cell(row, 9)) * _num(_cell(row, 10))
    return totals


def deposit_lookup(values: list[list]) -> dict[str, list]:
    """工具包押金：姓名 → 該列（同名取第一筆）。"""
    result: dict[str, list] = {}
    for row in values[1:]:
        name = _cell(row, 0)
        if name and name not in result:
            result[name] = row
    return result


def compute_refund_updates(
    refund_values: list[list],
    fees: dict[str, Decimal],
    deposits: dict[str, list],
) -> tuple[dict[int, Decimal], list[str]]:
    """回傳 ({列號: 新 H 值}, 警告訊息)。只處理 B 有姓名、S 空白的列。"""
    pending: dict[str, list[int]] = {}
    for sheet_row, row in enumerate(refund_values[1:], start=2):
        name = _cell(row, 1)
        # I 欄退款日期空白＝尚未匯款；G 欄註明「略過」的列（帳號錯誤、重複回報）不處理
        if name and not _cell(row, 8) and "略過" not in _cell(row, 6):
            pending.setdefault(name, []).append(sheet_row)

    updates: dict[int, Decimal] = {}
    warnings: list[str] = []
    for name, rows in pending.items():
        if len(rows) > 1:
            warnings.append(f"{name} 有多列未匯款（第 {'、'.join(map(str, rows))} 列），未更新")
            continue
        if name not in deposits:
            warnings.append(f"{name} 在「{DEPOSIT_SHEET}」找不到，未更新")
            continue
        updates[rows[0]] = fees.get(name, Decimal(0)) + deposit_amount(deposits[name])
    return updates, warnings


def deposit_amount(row: list) -> Decimal:
    """工具包押金 B 欄；會計已先退款（B 歸 0、G 欄「已匯款」）時以押金 2000 計。"""
    amount = _num(_cell(row, 1))
    if amount == 0 and "已匯款" in _cell(row, 6):
        return DEPOSIT_BASE
    return amount


def build_fee_rows(
    refund_values: list[list],
    h_overrides: dict[int, Decimal],
    existing_fee_values: list[list],
    deposits: dict[str, list],
    has_id: dict[str, bool],
    roster: dict[str, tuple[str, str]] | None = None,
) -> list[list]:
    """新訓退訓實境費用 A3 起的 A–F 資料。"""
    existing: dict[str, list] = {}
    for row in existing_fee_values[2:]:
        name = _cell(row, 0)
        if name and name not in existing:
            existing[name] = row

    rows: list[list] = []
    for sheet_row, row in enumerate(refund_values[1:], start=2):
        name = _cell(row, 1)
        h = h_overrides.get(sheet_row, _num(_cell(row, 7)))
        if not name or h <= DEPOSIT_BASE:
            continue
        old = existing.get(name)
        dep = deposits.get(name, [])
        if old and any(_cell(old, i) for i in (3, 4, 5)):
            info = [_cell(old, 3), _cell(old, 4), _cell(old, 5)]
        else:
            info = [_cell(dep, 11), _cell(dep, 12), _cell(dep, 13)]
        birthday, address = (roster or {}).get(name, ("", ""))
        if name in (roster or {}):
            info[1] = birthday  # 名冊生日寫法有誤會是空白，留給人工檢查
        info[1] = to_roc_date(info[1])
        if address:
            info[2] = address
        rows.append([name, _fmt(h - DEPOSIT_BASE), bool(has_id.get(name)), *info])
    return rows


def _list_children(drive, folder_id: str, folders_only: bool) -> list[dict]:
    query = f"'{folder_id}' in parents and trashed = false"
    if folders_only:
        query += f" and mimeType = '{FOLDER_MIME}'"
    items: list[dict] = []
    token = None
    while True:
        res = drive.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        items.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            return items


def find_id_owners(names: list[str]) -> dict[str, bool]:
    """根目錄及 YYYY新訓退訓 底下，資料夾名稱含姓名且內有檔案者為 True。"""
    from tools.common.google_auth import get_drive_service

    drive = get_drive_service()
    top = _list_children(drive, ID_ROOT_FOLDER_ID, folders_only=True)
    person_folders = [f for f in top if not _YEAR_FOLDER_RE.match(f["name"])]
    for year_folder in (f for f in top if _YEAR_FOLDER_RE.match(f["name"])):
        person_folders.extend(_list_children(drive, year_folder["id"], folders_only=True))

    result: dict[str, bool] = {}
    for name in names:
        result[name] = any(
            name in folder["name"]
            and any(c["mimeType"] != FOLDER_MIME for c in _list_children(drive, folder["id"], False))
            for folder in person_folders
        )
    return result


def pick_spreadsheet(prefix: str, files: list[dict]) -> str:
    """檔名以 prefix 開頭的試算表：完全相同優先，其次是其他未標「停用」的（例如「…-台北new」）。"""
    candidates = [f for f in files if f["name"].startswith(prefix) and "停用" not in f["name"]]
    candidates.sort(key=lambda f: f["name"] != prefix)
    return candidates[0]["id"] if candidates else ""


def _find_spreadsheet(prefix: str) -> str:
    """依檔名在雲端找試算表，找不到回傳空字串。"""
    from tools.common.google_auth import get_drive_service

    res = get_drive_service().files().list(
        q=f"name contains '{prefix}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false",
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return pick_spreadsheet(prefix, res.get("files", []))


def read_roster_values(service, area: str) -> list[list]:
    """今年度＋前一年度的「基本資料、協議規章」。

    前一年度的列放前面、今年度放後面，同名以今年度（最後一筆）為準；
    今年度名冊找不到的人就會用到前一年度的資料。
    """
    year = datetime.now(TW_TZ).year
    current_name = ROSTER_NAME.format(year=year, area=area)
    current_id = _find_spreadsheet(current_name)
    if not current_id:
        raise RuntimeError(f"雲端找不到試算表「{current_name}」")
    current = _read(service, current_id, ROSTER_SHEET, "A:G")
    previous: list[list] = []
    previous_id = _find_spreadsheet(ROSTER_NAME.format(year=year - 1, area=area))
    if previous_id:
        try:
            previous = _read(service, previous_id, ROSTER_SHEET, "A:G")[1:]
        except Exception:  # 前一年度沒有該分頁就只用今年度
            previous = []
    return current[:1] + previous + current[1:]


def field_names(sheets: list[list[list]]) -> set[str]:
    """新人實境／新人實習中出現過的姓名（不論 A 欄是否已填）。"""
    return {_cell(row, 2) for values in sheets for row in values[1:] if _cell(row, 2)}


def field_start_years(
    pending_names: set[str],
    start_years: dict[str, int],
    names_this_year: set[str],
    current_year: int,
) -> dict[str, int]:
    """每位待退款人員的新人實境／實習要從哪一年度開始找。

    基本資料、協議規章有建檔年份就從該年度開始；名冊沒有此人時，
    今年度找得到就只看今年，找不到再往前一年度。
    """
    result: dict[str, int] = {}
    for name in pending_names:
        if name in start_years:
            result[name] = min(start_years[name], current_year)
        elif name in names_this_year:
            result[name] = current_year
        else:
            result[name] = current_year - 1
    return result


def sum_fees_by_year(
    start_from: dict[str, int],
    fees_by_year: dict[int, dict[str, Decimal]],
    current_year: int,
) -> dict[str, Decimal]:
    """從起始年度加總到今年度的 J×K（缺檔的年度略過）。"""
    return {
        name: sum(
            (fees_by_year.get(year, {}).get(name, Decimal(0)) for year in range(start, current_year + 1)),
            Decimal(0),
        )
        for name, start in start_from.items()
    }


def d_formula_for_row(d3_formula: str, row: int) -> str:
    """D3 公式改成第 row 列使用，且 VLOOKUP 只回傳身分證（不溢出到 E–F）。"""
    return _ID_ONLY_RE.sub("2", re.sub(r"\bA3\b", f"A{row}", d3_formula))


REFUND_FORMULA_COLS = "JMNOPQR"  # 工具包押金退款填 H 欄時，這些欄沿用上一列公式


def _col_index(letter: str) -> int:
    return ord(letter) - ord("A")


def plan_formula_copies(
    formula_values: list[list],
    rows: list[int],
) -> tuple[list[tuple[int, int, int]], list[str]]:
    """formula_values 為 A 欄起的 FORMULA 內容；回傳 ([(欄索引, 來源列, 目標列)], 提醒)。

    目標儲存格已有公式就不動；來源取目標列往上最近一個有公式的列（通常就是上一列）。
    """
    copies: list[tuple[int, int, int]] = []
    warnings: list[str] = []
    for row in rows:
        for letter in REFUND_FORMULA_COLS:
            col = _col_index(letter)
            if _cell(formula_values[row - 1] if row - 1 < len(formula_values) else [], col).startswith("="):
                continue
            source = next(
                (r for r in range(row - 1, 1, -1)
                 if r - 1 < len(formula_values) and _cell(formula_values[r - 1], col).startswith("=")),
                0,
            )
            if source:
                copies.append((col, source, row))
            else:
                warnings.append(f"第 {row} 列 {letter} 欄往上找不到公式可複製，請人工補")
    return copies, warnings


def copy_prev_row_format_and_formulas(service, spreadsheet_id: str, title: str, rows: list[int]) -> list[str]:
    """新填 H 欄的列：整列套用上一列格式，J/M/N/O/P/Q/R 欄沿用上一列公式。"""
    if not rows:
        return []
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)"
    ).execute()
    sheet_id = next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == title)
    formula_values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!A:R", valueRenderOption="FORMULA",
    ).execute().get("values", [])
    copies, warnings = plan_formula_copies(formula_values, rows)

    def grid(row: int, col: int | None = None) -> dict:
        rng = {"sheetId": sheet_id, "startRowIndex": row - 1, "endRowIndex": row}
        if col is not None:
            rng.update(startColumnIndex=col, endColumnIndex=col + 1)
        return rng

    requests = [
        {"copyPaste": {"source": grid(r - 1), "destination": grid(r), "pasteType": "PASTE_FORMAT"}}
        for r in rows if r > 2
    ]
    requests += [
        {"copyPaste": {"source": grid(src, col), "destination": grid(dst, col), "pasteType": "PASTE_FORMULA"}}
        for col, src, dst in copies
    ]
    if requests:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    return warnings


def plan_fee_append(
    refund_values: list[list],
    h_overrides: dict[int, Decimal],
    fee_values: list[list],
) -> dict[str, object]:
    """新訓退訓實境費用只往下補新人：表上沒有、且 H > 2000 的人接在最後一筆之後。"""
    existing: dict[str, int] = {}
    last = 2
    recheck_rows: dict[str, int] = {}
    for r, row in enumerate(fee_values[2:], start=3):
        name = _cell(row, 0)
        if not name:
            continue
        last = r
        existing.setdefault(name, r)
        if _cell(row, 2).upper() != "TRUE":
            recheck_rows.setdefault(name, r)
    new_names: list[str] = []
    for r, row in enumerate(refund_values[1:], start=2):
        name = _cell(row, 1)
        h = h_overrides.get(r, _num(_cell(row, 7)))
        if name and h > DEPOSIT_BASE and name not in existing and name not in new_names:
            new_names.append(name)
    return {
        "new_names": new_names,
        "next_row": last + 1,
        "recheck_rows": recheck_rows,
        "recheck_names": list(recheck_rows),
    }


def _read(service, spreadsheet_id: str, title: str, cols: str = "A:Z") -> list[list]:
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!{cols}",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return res.get("values", [])


def update_refund_and_fee_sheet(area: str = "台北") -> dict[str, object]:
    """執行並寫一筆到主控表「tool-system執行Log-年度」（成功、失敗都記）。"""
    from tools.common import unified_log

    log_fields = dict(system="外場排程系統", function="新訓退訓實境費用", run_type="手動",
                      area=area, period=datetime.now(TW_TZ).strftime("%Y%m%d"), target=FEE_SHEET)
    try:
        result = _update_refund_and_fee_sheet(area)
    except Exception as exc:
        unified_log.write(status="失敗", message=str(exc), **log_fields)
        raise
    message = (
        f"退款金額 H 欄更新 {result['h_updated']} 列；新增 {result['fee_rows']} 人；"
        f"身分證打勾 {result['id_checked']} 人"
    )
    if result["warnings"]:
        message += "；注意：" + "；".join(result["warnings"])
    unified_log.write(status="成功", message=message, **log_fields)
    return result


def _update_refund_and_fee_sheet(area: str) -> dict[str, object]:
    service = get_sheets_service()
    spreadsheet_id, refund_title = resolve_report_location(DEPOSIT_REFUND_TYPE, area)

    refund_values = _read(service, spreadsheet_id, refund_title)
    deposits = deposit_lookup(_read(service, spreadsheet_id, DEPOSIT_SHEET))
    field_sheets = [_read(service, spreadsheet_id, t, "A:K") for t in FIELD_SHEETS]
    fees = field_fees(field_sheets)
    roster_values = read_roster_values(service, area)

    # 新人實境／實習從該人在基本資料、協議規章的建檔年度開始找，加總到今年度
    current_year = datetime.now(TW_TZ).year
    pending_names = {
        _cell(row, 1) for row in refund_values[1:]
        if _cell(row, 1) and not _cell(row, 8) and "略過" not in _cell(row, 6)
    }
    start_from = field_start_years(
        pending_names, roster_start_years(roster_values), field_names(field_sheets), current_year
    )
    fees_by_year: dict[int, dict[str, Decimal]] = {current_year: fees}
    warnings: list[str] = []
    for year in sorted({y for y in start_from.values() if y < current_year}):
        year_id = _find_spreadsheet(SALARY_NAME.format(year=year, area=area))
        if not year_id:
            warnings.append(f"雲端找不到「{SALARY_NAME.format(year=year, area=area)}」，該年度實境費用未計入")
            continue
        fees_by_year[year] = field_fees([_read(service, year_id, t, "A:K") for t in FIELD_SHEETS])
    fees = {**fees, **sum_fees_by_year(start_from, fees_by_year, current_year)}
    earlier = sorted(f"{n}（{y}起）" for n, y in start_from.items() if y < current_year)

    updates, update_warnings = compute_refund_updates(refund_values, fees, deposits)
    warnings = update_warnings + warnings
    if earlier:
        warnings.append(f"以下人員實境費用含前年度：{'、'.join(earlier)}")
    # 只填 H 欄空白的列；已有金額（可能是人工調整）不覆蓋，不一致時提醒
    changed = {r: v for r, v in updates.items() if not _cell(refund_values[r - 1], 7)}
    for r, v in updates.items():
        old = _cell(refund_values[r - 1], 7)
        if old and _num(old) != v:
            warnings.append(
                f"第 {r} 列 {_cell(refund_values[r - 1], 1)} H 欄已有 {old}，試算為 {_fmt(v)}，未覆蓋請人工確認"
            )
    updates = changed
    if changed:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": f"'{refund_title}'!H{r}", "values": [[_fmt(v)]]}
                    for r, v in changed.items()
                ],
            },
        ).execute()

        warnings += copy_prev_row_format_and_formulas(service, spreadsheet_id, refund_title, sorted(changed))

    fee_values = _read(service, spreadsheet_id, FEE_SHEET, "A:F")
    plan = plan_fee_append(refund_values, updates, fee_values)
    has_id = find_id_owners(plan["new_names"] + plan["recheck_names"]) if (
        plan["new_names"] or plan["recheck_names"]
    ) else {}
    roster = roster_info(roster_values)
    candidate_rows = build_fee_rows(refund_values, updates, fee_values, deposits, has_id, roster)
    seen: set[str] = set()
    new_rows = []
    for row in candidate_rows:
        if row[0] in plan["new_names"] and row[0] not in seen:
            seen.add(row[0])
            new_rows.append(row)

    data = []
    start = plan["next_row"]
    end = start + len(new_rows) - 1
    if new_rows:
        d3 = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{FEE_SHEET}'!D3",
            valueRenderOption="FORMULA",
        ).execute().get("values", [[""]])
        d3_text = str(d3[0][0] if d3 and d3[0] else "")
        data.append({"range": f"'{FEE_SHEET}'!A{start}:C{end}", "values": [r[:3] for r in new_rows]})
        data.append({"range": f"'{FEE_SHEET}'!E{start}:F{end}", "values": [r[4:] for r in new_rows]})
        if d3_text.startswith("="):
            # D 欄沿用 D3 公式（改成只回傳身分證，E–F 由程式寫入）
            data.append({
                "range": f"'{FEE_SHEET}'!D{start}:D{end}",
                "values": [[d_formula_for_row(d3_text, start + i)] for i in range(len(new_rows))],
            })
        else:
            data.append({"range": f"'{FEE_SHEET}'!D{start}:D{end}", "values": [[r[3]] for r in new_rows]})
    # 既有列 C 欄未勾、這次雲端找到身分證的補打勾
    rechecked = [r for name, r in plan["recheck_rows"].items() if has_id.get(name)]
    for r in rechecked:
        data.append({"range": f"'{FEE_SHEET}'!C{r}", "values": [[True]]})
    if data:
        # 公式需 USER_ENTERED；其餘值（生日等）先用 RAW 寫入避免被轉成日期
        formula_data = [d for d in data if str(d["values"][0][0]).startswith("=")]
        value_data = [d for d in data if d not in formula_data]
        for option, chunk in (("RAW", value_data), ("USER_ENTERED", formula_data)):
            if chunk:
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"valueInputOption": option, "data": chunk},
                ).execute()

    return {
        "h_updated": len(changed),
        "fee_rows": len(new_rows),
        "id_checked": sum(1 for r in new_rows if r[2]) + len(rechecked),
        "warnings": warnings,
    }


# ── 【新訓退款】新訓退訓實境費用已退款 ─────────────────────────────

PAID_SUFFIX = "已匯款"  # 與既有資料（例如「2026/10/16已匯款」）及工具包押金財報的判斷一致


def normalize_refund_date(text: str) -> str:
    """I 欄退款日期統一成 YYYY/MM/DD（2026/1/9 → 2026/01/09）；不是日期回傳空字串。"""
    match = re.match(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        return ""
    year, month, day = (int(g) for g in match.groups())
    return f"{year:04d}/{month:02d}/{day:02d}"


def plan_mark_refunded(
    refund_values: list[list],
    deposit_values: list[list],
) -> tuple[list[dict[str, object]], list[str]]:
    """找出 I 欄已填退款日期、但工具包押金 G 欄尚未註記的人。

    回傳 ([{name, date, deposit_row, field_amount}], 提醒)；
    field_amount＝H − 2000（實境／實習費用），大於 0 才需要註記新人實境／實習 A 欄。
    """
    deposit_rows: dict[str, int] = {}
    deposit_marked: dict[str, bool] = {}
    for r, row in enumerate(deposit_values[1:], start=2):
        name = _cell(row, 0)
        if name and name not in deposit_rows:
            deposit_rows[name] = r
            deposit_marked[name] = bool(_cell(row, 6))

    items: list[dict[str, object]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for r, row in enumerate(refund_values[1:], start=2):
        name, date_text = _cell(row, 1), _cell(row, 8)
        if not name or not date_text or "略過" in _cell(row, 6) or name in seen:
            continue
        if name not in deposit_rows:
            continue
        if deposit_marked[name]:
            continue
        date = normalize_refund_date(date_text)
        if not date:
            warnings.append(f"第 {r} 列 {name} 退款日期「{date_text}」無法辨識，未處理")
            continue
        seen.add(name)
        items.append({
            "name": name,
            "date": date,
            "deposit_row": deposit_rows[name],
            "field_amount": _num(_cell(row, 7)) - DEPOSIT_BASE,
        })
    return items, warnings


def plan_field_marks(
    sheets: dict[str, list[list]],
    name: str,
) -> tuple[list[tuple[str, int]], Decimal]:
    """新人實境／實習中該人 A 欄空白的列：回傳 ([(分頁, 列號)], J×K 合計)。"""
    cells: list[tuple[str, int]] = []
    total = Decimal(0)
    for title, values in sheets.items():
        for r, row in enumerate(values[1:], start=2):
            if _cell(row, 2) == name and not _cell(row, 0):
                cells.append((title, r))
                total += _num(_cell(row, 9)) * _num(_cell(row, 10))
    return cells, total


def mark_refunded(area: str = "台北") -> dict[str, object]:
    """執行並寫一筆到主控表「tool-system執行Log-年度」（成功、失敗都記）。"""
    from tools.common import unified_log

    log_fields = dict(system="外場排程系統", function="新訓退訓實境費用已退款", run_type="手動",
                      area=area, period=datetime.now(TW_TZ).strftime("%Y%m%d"), target=DEPOSIT_SHEET)
    try:
        result = _mark_refunded(area)
    except Exception as exc:
        unified_log.write(status="失敗", message=str(exc), **log_fields)
        raise
    message = f"工具包押金 G 欄註記 {result['deposit_marked']} 人；新人實境／實習 A 欄註記 {result['field_marked']} 列"
    if result["names"]:
        message += f"（{'、'.join(result['names'])}）"
    if result["warnings"]:
        message += "；注意：" + "；".join(result["warnings"])
    unified_log.write(status="成功", message=message, **log_fields)
    result["message"] = message
    return result


def _mark_refunded(area: str) -> dict[str, object]:
    service = get_sheets_service()
    spreadsheet_id, refund_title = resolve_report_location(DEPOSIT_REFUND_TYPE, area)
    refund_values = _read(service, spreadsheet_id, refund_title)
    deposit_values = _read(service, spreadsheet_id, DEPOSIT_SHEET, "A:G")
    items, warnings = plan_mark_refunded(refund_values, deposit_values)

    sheets = {t: _read(service, spreadsheet_id, t, "A:K") for t in FIELD_SHEETS} if items else {}
    data = []
    field_marked = 0
    for item in items:
        note = f"{item['date']}{PAID_SUFFIX}"
        data.append({"range": f"'{DEPOSIT_SHEET}'!G{item['deposit_row']}", "values": [[note]]})
        amount = item["field_amount"]
        if amount <= 0:
            continue
        cells, total = plan_field_marks(sheets, str(item["name"]))
        if not cells:
            warnings.append(f"{item['name']} 實境費用 {_fmt(amount)}，但今年度新人實境／實習找不到 A 欄空白的列，請人工註記")
            continue
        if total != amount:
            warnings.append(f"{item['name']} 實境費用 {_fmt(amount)}，A 欄空白列合計 {_fmt(total)}，請確認")
        for title, r in cells:
            data.append({"range": f"'{title}'!A{r}", "values": [[note]]})
        field_marked += len(cells)

    if data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
    return {
        "deposit_marked": len(items),
        "field_marked": field_marked,
        "names": [str(i["name"]) for i in items],
        "warnings": warnings,
    }
