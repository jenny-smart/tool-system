"""上下半月訂單下載後檢查（2026-11 Jenny）。

1. 已付款未開發票：付款狀態＝已付款、服務金額 > 0、發票號碼空白
   （儲值金扣款／折扣後 0 元的訂單不用開；發票號碼寫「不開發票」「不開立發票」是刻意不開，不算）
2. 已加收：加收日期、加收金流、加收發票日期、加收發票號碼、加收金額 都要填
3. 已部分退款／已全額退款：退款日期、退款金流、退款金額、折讓單號 都要填（退款金額 0 不用折讓單號）

上傳的訂單檔：有加收／退款狀態的列整列淺黃底，缺的格子紅底。
"""
from __future__ import annotations

from typing import Any

PAID = "已付款"
CHARGED = ("已加收",)
REFUNDED = ("已部分退款", "已部份退款", "已全額退款")
CHARGE_FIELDS = ("加收日期", "加收金流", "加收發票日期", "加收發票號碼", "加收金額")
REFUND_FIELDS = ("退款日期", "退款金流", "退款金額", "折讓單號")
REMINDER_PREFIX = "上下半月訂單資料待補"
STATUS_FILL = "FFF2CC"  # 淺黃：有加收／退款狀態
MISSING_FILL = "F4CCCC"  # 淺紅：該填沒填


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "nat", "none") else text


def _amount(value: Any) -> float:
    text = _text(value).replace(",", "").replace("$", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _column(columns: list, *names: str):
    cleaned = {str(c).strip(): c for c in columns}
    for name in names:
        if name in cleaned:
            return cleaned[name]
    for name in names:  # 「付款方式(含vip時數)」之類的長標題
        for key, original in cleaned.items():
            if key.startswith(name):
                return original
    return None


def row_issues(get) -> tuple[bool, list[tuple[str, list[str], str]]]:
    """get(欄名)→格子文字。回傳 (有加收／退款狀態, [(類型, 缺的欄位, 金額)])。"""
    issues = []
    if get("付款狀態") == PAID and _amount(get("服務金額")) > 0 and not get("發票號碼"):
        issues.append(("已付款未開發票", ["發票號碼"], get("服務金額")))
    charged = get("加收狀態") in CHARGED
    refunded = get("待退狀態") in REFUNDED
    if charged:
        missing = [f for f in CHARGE_FIELDS if not get(f)]
        if missing:
            issues.append((f"{get('加收狀態')}資料不完整", missing, get("加收金額")))
    if refunded:
        fields = REFUND_FIELDS if _amount(get("退款金額")) != 0 or not get("退款金額") else REFUND_FIELDS[:3]
        missing = [f for f in fields if not get(f)]
        if missing:
            issues.append((f"{get('待退狀態')}資料不完整", missing, get("退款金額")))
    return charged or refunded, issues


NAMES = ("訂單編號", "付款狀態", "發票號碼", "服務金額", "付款方式", "服務日期", "客戶姓名", "加收狀態", "待退狀態") \
    + CHARGE_FIELDS + REFUND_FIELDS


def find_uninvoiced(df) -> list[dict[str, str]]:
    """回傳 [{訂單編號, 類型, 缺, 金額, 付款方式, 服務日期, 客戶姓名}]（同一訂單同一類型只列一次）。"""
    columns = list(df.columns)
    col = {name: _column(columns, name) for name in NAMES}
    if col["訂單編號"] is None or col["付款狀態"] is None or col["發票號碼"] is None:
        raise RuntimeError("訂單檔找不到「訂單編號／付款狀態／發票號碼」欄，無法檢查")
    found, seen = [], set()
    for _i, row in df.iterrows():
        def get(name, row=row):
            return _text(row[col[name]]) if col.get(name) is not None else ""
        order = get("訂單編號")
        if not order:
            continue
        for kind, missing, amount in row_issues(get)[1]:
            if (order, kind) in seen:
                continue
            seen.add((order, kind))
            found.append({"訂單編號": order, "類型": kind, "缺": "、".join(missing),
                          "金額": f"{_amount(amount):,.0f}" if _text(amount) else "",
                          "付款方式": get("付款方式"), "服務日期": get("服務日期"), "客戶姓名": get("客戶姓名")})
    return found


def summary_lines(city: str, items: list[dict[str, str]]) -> list[str]:
    lines = []
    for i in items:
        extra = f"，缺 {i['缺']}" if i.get("缺") and i["類型"] != "已付款未開發票" else ""
        lines.append(f"[{city}] {i['類型']} {i['訂單編號']}（{i['客戶姓名']}，服務日 {i['服務日期']}，"
                     f"{i['付款方式']} {i['金額']}{extra}）")
    return lines


def highlight_workbook(path: str) -> int:
    """訂單檔（xlsx）：有加收／退款狀態的列整列淺黃底、缺的格子（含已付款未開發票的發票號碼）淺紅底。回傳上色列數。"""
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill

    workbook = load_workbook(path)
    sheet = workbook.worksheets[0]
    header = [_text(c.value) for c in sheet[1]]
    index = {name: header.index(found) for name in NAMES
             if (found := _column(header, name)) is not None}
    status_fill = PatternFill("solid", start_color=STATUS_FILL, end_color=STATUS_FILL)
    missing_fill = PatternFill("solid", start_color=MISSING_FILL, end_color=MISSING_FILL)
    colored = 0
    for cells in sheet.iter_rows(min_row=2):
        def get(name, cells=cells):
            i = index.get(name)
            return _text(cells[i].value) if i is not None and i < len(cells) else ""
        has_status, issues = row_issues(get)
        if not has_status and not issues:
            continue
        colored += 1
        if has_status:
            for cell in cells:
                cell.fill = status_fill
        for _kind, missing, _amount_value in issues:
            for name in missing:
                i = index.get(name)
                if i is not None and i < len(cells):
                    cells[i].fill = missing_fill
    workbook.save(path)
    return colored
