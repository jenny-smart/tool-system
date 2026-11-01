"""上下半月訂單下載後檢查：已付款或已加收，但還沒開發票的訂單（2026-11 Jenny）。

- 已付款：付款狀態＝已付款、服務金額 > 0、發票號碼空白
  （儲值金扣款／折扣後 0 元的訂單不用開；發票號碼寫「不開發票」「不開立發票」是刻意不開，不算）
- 加收：加收狀態＝已加收、加收金額 > 0、加收發票號碼空白
"""
from __future__ import annotations

from typing import Any

PAID = "已付款"
CHARGED = "已加收"
REMINDER_PREFIX = "上下半月訂單未開發票"


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _amount(value: Any) -> float:
    text = _text(value).replace(",", "").replace("$", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _column(columns: list[str], *names: str) -> str:
    cleaned = {str(c).strip(): c for c in columns}
    for name in names:
        if name in cleaned:
            return cleaned[name]
    for name in names:  # 「付款方式(含vip時數)」之類的長標題
        for key, original in cleaned.items():
            if key.startswith(name):
                return original
    return ""


def find_uninvoiced(df) -> list[dict[str, str]]:
    """回傳 [{訂單編號, 類型, 金額, 付款方式, 服務日期, 客戶姓名}]。df＝後台匯出的訂單（pandas DataFrame）。"""
    columns = list(df.columns)
    col = {key: _column(columns, *names) for key, names in {
        "order": ("訂單編號",), "paid": ("付款狀態",), "invoice": ("發票號碼",), "amount": ("服務金額",),
        "payway": ("付款方式",), "date": ("服務日期",), "name": ("客戶姓名",),
        "charge": ("加收狀態",), "charge_amount": ("加收金額",), "charge_invoice": ("加收發票號碼",),
    }.items()}
    if not col["order"] or not col["paid"] or not col["invoice"]:
        raise RuntimeError("訂單檔找不到「訂單編號／付款狀態／發票號碼」欄，無法檢查未開發票")
    found = []
    for _i, row in df.iterrows():
        get = lambda key: _text(row[col[key]]) if col[key] else ""  # noqa: E731
        base = {"訂單編號": get("order"), "付款方式": get("payway"), "服務日期": get("date"), "客戶姓名": get("name")}
        if not base["訂單編號"]:
            continue
        if get("paid") == PAID and _amount(get("amount")) > 0 and not get("invoice"):
            found.append({**base, "類型": "已付款未開發票", "金額": f"{_amount(get('amount')):,.0f}"})
        if col["charge"] and get("charge") == CHARGED and _amount(get("charge_amount")) > 0 \
                and not get("charge_invoice"):
            found.append({**base, "類型": "已加收未開發票", "金額": f"{_amount(get('charge_amount')):,.0f}"})
    return found


def summary_lines(city: str, items: list[dict[str, str]]) -> list[str]:
    return [f"[{city}] {i['類型']} {i['訂單編號']}（{i['客戶姓名']}，服務日 {i['服務日期']}，"
            f"{i['付款方式']} {i['金額']}）" for i in items]
