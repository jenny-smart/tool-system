"""工具包押金退款金額（H 欄）＋新訓退訓實境費用。

步驟（同一份試算表，依登記表「工具包押金退款／地區」找到）：
1. 工具包押金退款 H 欄（只處理 I 欄退款日期空白＝尚未匯款的列）：
   = 新人實境＋新人實習 中 C 欄＝本列 B 欄姓名、且 A 欄空白的列 J×K 加總
   ＋ 工具包押金 A 欄＝本列 B 欄姓名的 B 欄金額
2. 新訓退訓實境費用 A3 起：工具包押金退款 H > 2000 的 B 欄姓名，B 欄＝H − 2000；
   D 欄若 D3 是公式就把公式往下填滿（公式改成只回傳身分證，不再溢出到 E–F）；
   E–F 取「{今年}專員名冊與時數-{地區}」的「基本資料、協議規章」E 欄生日、G 欄地址，
   找不到才沿用既有資料或工具包押金 M–N；生日是 4 位數西元年（2026 年起建檔都是）
   一律減 1911 轉成民國年（例如 1997/2/4 → 86/02/04），已是民國年的原樣帶入。
3. C 欄：雲端資料夾（根目錄或 YYYY新訓退訓 底下）有該人姓名的資料夾、
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
ROSTER_SHEET = "基本資料、協議規章"
_ID_ONLY_RE = re.compile(r"\{\s*2\s*(?:,\s*\d+\s*)+\}")
_WESTERN_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")


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
    """西元年（1997/2/4）轉民國年（86/02/04）；已是民國年或其他格式則原樣回傳。"""
    text = text.strip()
    match = _WESTERN_DATE_RE.match(text)
    if not match or int(match.group(1)) < 1912:
        return text
    year, month, day = (int(g) for g in match.groups())
    return f"{year - 1911:02d}/{month:02d}/{day:02d}"


def roster_info(values: list[list]) -> dict[str, tuple[str, str]]:
    """基本資料、協議規章：C 欄姓名 → (E 欄生日, G 欄地址)，同名取最後一筆。

    2026 年起建檔的生日是西元年（2026 年前也偶有填西元年），
    凡是 4 位數西元年一律轉成民國年；已是民國年的原樣帶入。
    """
    result: dict[str, tuple[str, str]] = {}
    for row in values[1:]:
        name = _cell(row, 2)
        if name:
            result[name] = (to_roc_date(_cell(row, 4)), _cell(row, 6))
    return result


def field_fees(sheets: list[list[list]]) -> dict[str, Decimal]:
    """新人實境／新人實習：A 欄空白的列，依 C 欄姓名加總 J×K。"""
    totals: dict[str, Decimal] = {}
    for values in sheets:
        for row in values[1:]:
            name = _cell(row, 2)
            if not name or _cell(row, 0):
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
        updates[rows[0]] = fees.get(name, Decimal(0)) + _num(_cell(deposits[name], 1))
    return updates, warnings


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
        if birthday:
            info[1] = birthday
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


def read_roster(service, area: str) -> dict[str, tuple[str, str]]:
    from tools.common.google_auth import get_drive_service

    name = ROSTER_NAME.format(year=datetime.now(ZoneInfo("Asia/Taipei")).year, area=area)
    res = get_drive_service().files().list(
        q=f"name = '{name}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false",
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    if not files:
        raise RuntimeError(f"雲端找不到試算表「{name}」")
    return roster_info(_read(service, files[0]["id"], ROSTER_SHEET, "A:G"))


def _fill_down_d(service, spreadsheet_id: str, n: int) -> None:
    """把新訓退訓實境費用 D3 的公式往下填到 D{2+n}。"""
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)"
    ).execute()
    sheet_id = next(
        s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == FEE_SHEET
    )
    source = {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 3, "endColumnIndex": 4}
    dest = dict(source, startRowIndex=3, endRowIndex=2 + n)
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"copyPaste": {"source": source, "destination": dest, "pasteType": "PASTE_FORMULA"}}]},
    ).execute()


def _read(service, spreadsheet_id: str, title: str, cols: str = "A:Z") -> list[list]:
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!{cols}",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return res.get("values", [])


def update_refund_and_fee_sheet(area: str = "台北") -> dict[str, object]:
    service = get_sheets_service()
    spreadsheet_id, refund_title = resolve_report_location(DEPOSIT_REFUND_TYPE, area)

    refund_values = _read(service, spreadsheet_id, refund_title)
    deposits = deposit_lookup(_read(service, spreadsheet_id, DEPOSIT_SHEET))
    fees = field_fees([_read(service, spreadsheet_id, t, "A:K") for t in FIELD_SHEETS])

    updates, warnings = compute_refund_updates(refund_values, fees, deposits)
    changed = {
        r: v for r, v in updates.items()
        if _num(_cell(refund_values[r - 1], 7)) != v or not _cell(refund_values[r - 1], 7)
    }
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

    fee_values = _read(service, spreadsheet_id, FEE_SHEET, "A:F")
    names = sorted({
        _cell(row, 1)
        for r, row in enumerate(refund_values[1:], start=2)
        if _cell(row, 1) and updates.get(r, _num(_cell(row, 7))) > DEPOSIT_BASE
    })
    has_id = find_id_owners(names) if names else {}
    roster = read_roster(service, area)
    fee_rows = build_fee_rows(refund_values, updates, fee_values, deposits, has_id, roster)

    d3 = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{FEE_SHEET}'!D3",
        valueRenderOption="FORMULA",
    ).execute().get("values", [[""]])
    d3_text = str(d3[0][0] if d3 and d3[0] else "")
    d_formula = d3_text.startswith("=")
    n = len(fee_rows)

    if d_formula:
        # 原公式 VLOOKUP(...,{2,3,5}) 會溢出到 E–F，改成只回傳身分證，E–F 由程式寫入
        id_only = _ID_ONLY_RE.sub("2", d3_text)
        if id_only != d3_text:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{FEE_SHEET}'!D3",
                valueInputOption="USER_ENTERED",
                body={"values": [[id_only]]},
            ).execute()

    last_row = max(len(fee_values), 2 + n)
    if last_row >= 3:
        clear_ranges = [f"'{FEE_SHEET}'!A3:C{last_row}", f"'{FEE_SHEET}'!E3:F{last_row}"]
        if not d_formula:
            clear_ranges.append(f"'{FEE_SHEET}'!D3:D{last_row}")
        service.spreadsheets().values().batchClear(
            spreadsheetId=spreadsheet_id, body={"ranges": clear_ranges}
        ).execute()
        # 清空後 C 欄核取方塊會變空白，補回 FALSE 維持核取方塊顯示
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{FEE_SHEET}'!C3:C{last_row}",
            valueInputOption="RAW",
            body={"values": [[False] for _ in range(last_row - 2)]},
        ).execute()
    if fee_rows:
        if d_formula:
            data = [
                {"range": f"'{FEE_SHEET}'!A3:C{2 + n}", "values": [r[:3] for r in fee_rows]},
                {"range": f"'{FEE_SHEET}'!E3:F{2 + n}", "values": [r[4:] for r in fee_rows]},
            ]
        else:
            data = [{"range": f"'{FEE_SHEET}'!A3:F{2 + n}", "values": fee_rows}]
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        if d_formula and n > 1:
            _fill_down_d(service, spreadsheet_id, n)

    return {
        "h_updated": len(changed),
        "fee_rows": len(fee_rows),
        "id_checked": sum(1 for r in fee_rows if r[2]),
        "warnings": warnings,
    }
