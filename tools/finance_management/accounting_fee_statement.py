"""會計事務所費用：富邦轉帳完成、下載明細到財報「富邦更新」後，把合併的那一筆拆成逐項列。

更新紀錄：
  2026-11-09 Jenny：新增。拆列規則（以 9 月為例）：
    2026.07～08-台北營業稅 → 2026.07-台北營業稅、2026.08-台北營業稅（金額平分，餘數放後面那列），I＝稅捐
    2026.07～08-台中營業稅 → 2026.08-台中營業稅（記前一月），I＝稅捐
    2026.09-辦公室租金代扣繳 → 2026.09-辦公室代墊，I＝辦公室租金費
    2026.09-辦公室二代健保 → 2026.09-辦公室補充保費，I＝辦公室租金費
    營所稅 → I＝稅捐，L＝費用說明
    記帳費、會計師簽證費 → I 不填（請人工選），L＝費用說明（沒有月份的補付款月份）
    明細金額比項目合計多出來的（跨行手續費）→ 最後一列「付款月份-銀行手續費」，I＝銀行手續費
  台中（台中付給檸檬台北，只拆全部是台中營業稅／台中記帳費的那筆）：
    2026.07～08-台中營業稅 → 2026.07-營業稅-台中、2026.08-營業稅-台中（金額平分），I＝稅捐
    台中記帳費 → I 不填，L＝費用說明
  每列 B～H、K、M～P 照原列（K、M～P 公式改成指向自己那列），只改 E（金額）、I（收支類別）、
  L（分類標籤，寫文字）、Q／R（更新時間／更新內容）。找不到對應的明細列只記 log，不影響轉帳結果。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
TAX = "稅捐"
OFFICE = "辦公室租金費"
BANK_FEE = "銀行手續費"
MAX_FEE = 30  # 明細金額比項目合計多出這個範圍內，視為同一筆的跨行手續費
UPDATE_NOTE = "會計事務所費用拆列"

_TAX_SPAN = re.compile(r"^(\d{4})\.(\d{2})～(\d{2})-(台北|台中)營業稅$")
_MONTHLY = re.compile(r"^(\d{4}\.\d{2})-(.+)$")
_RENAME = {"辦公室租金代扣繳": ("辦公室代墊", OFFICE), "辦公室二代健保": ("辦公室補充保費", OFFICE)}


def _split_amount(total: int, parts: int) -> list[int]:
    base, rest = divmod(total, parts)
    return [base + (1 if i >= parts - rest else 0) for i in range(parts)]


def should_split(area: str, payee: str, members: list[dict]) -> bool:
    """台北：收款對象＝勤信會計事務所；台中：收款對象＝檸檬台北且每列都是台中營業稅／台中記帳費。"""
    from tools.finance_management import accounting_fee_batch as batch

    payee = payee.strip()
    if area == batch.AREA:
        return payee == batch.PAYEE
    if area == batch.TAICHUNG:
        return payee == batch.TAICHUNG_PAYEE and bool(members) and all(
            batch.is_taichung(m) and ("營業稅" in str(m.get("memo")) or "記帳費" in str(m.get("memo")))
            for m in members)
    return False


def statement_lines(items: list[dict], paid_month: str, area: str = "台北") -> list[tuple[str, str, int]]:
    """[(L 分類標籤, I 收支類別, 金額)]；items 每筆要有 memo、amount。paid_month＝"YYYY.MM"。
    area＝寫哪一區的富邦更新（台中付給台北時，台中營業稅拆兩列「YYYY.MM-營業稅-台中」）。"""
    lines: list[tuple[str, str, int]] = []
    for item in items:
        memo, amount = str(item["memo"]).strip(), int(round(float(str(item["amount"]).replace(",", ""))))
        tax = _TAX_SPAN.match(memo)
        if tax:
            year, first, second, tax_area = tax.groups()
            if tax_area == "台北":
                for month, part in zip((first, second), _split_amount(amount, 2)):
                    lines.append((f"{year}.{month}-台北營業稅", TAX, part))
            elif area == "台中":
                for month, part in zip((first, second), _split_amount(amount, 2)):
                    lines.append((f"{year}.{month}-營業稅-台中", TAX, part))
            else:
                lines.append((f"{year}.{second}-台中營業稅", TAX, amount))
            continue
        monthly = _MONTHLY.match(memo)
        if monthly and monthly.group(2) in _RENAME:
            name, category = _RENAME[monthly.group(2)]
            lines.append((f"{monthly.group(1)}-{name}", category, amount))
            continue
        category = TAX if "營所稅" in memo else ""
        plain = re.sub(r"^\d{4}-", "", memo)
        label = memo if monthly else f"{paid_month}-{plain}"
        lines.append((label, category, amount))
    return lines


def _number(value) -> float | None:
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def find_statement_row(values: list[list], total: int, paid_on: str) -> tuple[int, int] | None:
    """在富邦更新（FORMATTED 值，A 起）找付款日、支出＝合計（可多跨行手續費）的那列；回傳 (列號, 明細金額)。
    取最後一筆（剛下載的在最下面）。已經拆過（L 有值）的不算。"""
    found = None
    for index, row in enumerate(values[1:], start=2):
        cell = lambda col: row[col] if col < len(row) else ""
        if not str(cell(1)).strip().startswith(paid_on):
            continue
        amount = _number(cell(4))
        if amount is None or not (total <= amount <= total + MAX_FEE):
            continue
        if str(cell(8)).strip():  # I 欄已有收支類別＝已處理過
            continue
        found = (index, int(round(amount)))
    return found


def _rewrite_formulas(row: list, old: int, new: int) -> list:
    """把公式裡指向原列（例 E120）的參照改成新列。"""
    pattern = re.compile(rf"(?<![A-Z])([A-Z]{{1,2}}){old}(?!\d)")
    return [pattern.sub(lambda m: f"{m.group(1)}{new}", v) if isinstance(v, str) and v.startswith("=") else v
            for v in row]


def build_rows(original: list, row_number: int, lines: list[tuple[str, str, int]], now_text: str) -> list[list]:
    """原列（FORMULA 值，A:R）複製成每項一列：只改 E、I、L、Q、R。"""
    base = list(original) + [""] * max(0, 18 - len(original))
    rows = []
    for offset, (label, category, amount) in enumerate(lines):
        row = _rewrite_formulas(base, row_number, row_number + offset)
        row[4] = amount
        row[8] = category or row[8]
        row[11] = label
        row[16] = now_text
        row[17] = f"{UPDATE_NOTE}：{label} {amount}"
        rows.append(row)
    return rows


def split_after_payment(area: str, items: list[dict], paid_on: str, log: Callable[[str], None] = print) -> int:
    """paid_on＝"YYYY/MM/DD"。回傳拆成幾列；找不到明細列回傳 0。"""
    from tools.common.config_loader import get_sheets_service
    from tools.finance_management.statement_registry import resolve_statement_location

    service = get_sheets_service()
    year = int(paid_on[:4])
    spreadsheet_id, title = resolve_statement_location(area, "富邦更新", service=service, year=year)
    sheet = service.spreadsheets()
    formatted = sheet.values().get(spreadsheetId=spreadsheet_id, range=f"'{title}'!A:L").execute().get("values", [])
    total = sum(int(round(float(str(i["amount"]).replace(",", "")))) for i in items)
    hit = find_statement_row(formatted, total, paid_on)
    if not hit:
        log(f"⚠ 富邦更新找不到 {paid_on} 支出 {total:,} 的明細列，會計事務所費用沒有拆列，請人工處理。")
        return 0
    row_number, statement_amount = hit
    lines = statement_lines(items, paid_on[:7].replace("/", "."), area)
    fee = statement_amount - total
    if fee:
        lines.append((f"{paid_on[:7].replace('/', '.')}-{BANK_FEE}", BANK_FEE, fee))
    original = sheet.values().get(spreadsheetId=spreadsheet_id, range=f"'{title}'!A{row_number}:R{row_number}",
                                  valueRenderOption="FORMULA").execute().get("values", [[]])[0]
    if len(lines) > 1:
        sheet_id = next(s["properties"]["sheetId"] for s in sheet.get(spreadsheetId=spreadsheet_id).execute()["sheets"]
                        if s["properties"]["title"] == title)
        sheet.batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{"insertDimension": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": row_number,
                      "endIndex": row_number + len(lines) - 1},
            "inheritFromBefore": True}}]}).execute()
    now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
    rows = build_rows(original, row_number, lines, now_text)
    sheet.values().update(spreadsheetId=spreadsheet_id,
                          range=f"'{title}'!A{row_number}:R{row_number + len(rows) - 1}",
                          valueInputOption="USER_ENTERED", body={"values": rows}).execute()
    log(f"富邦更新第 {row_number} 列（{statement_amount:,}）已拆成 {len(rows)} 列：" + "、".join(l[0] for l in lines))
    return len(rows)
