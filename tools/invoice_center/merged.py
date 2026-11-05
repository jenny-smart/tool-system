"""合併開立發票共用：G 欄多張訂單編號解析（不載入試算表或瀏覽器模組）。"""
from __future__ import annotations

import re

# 2026-11 Jenny：待處理發票「合併開立發票」——G 欄放多張訂單，以第一張訂單的發票資料開一張發票（金額＝各訂單合計），
# 回寫後台時每張訂單的發票號碼及財務備註都要寫
MERGED_INVOICE_DETAILS = ("合併開立發票", "合併發票")
_ORDER_NO_RE = re.compile(r"LC\d{6,}", re.I)


def split_order_numbers(text: str) -> list[str]:
    """G 欄訂單編號（可多張，空白／逗號／換行分隔）→ ["LC…", …]，保留順序、去重。"""
    found = [m.group(0).upper() for m in _ORDER_NO_RE.finditer(str(text or ""))]
    if not found and str(text or "").strip():
        found = [part for part in re.split(r"[\s,，、/／]+", str(text).strip()) if part]
    result = []
    for order_no in found:
        if order_no not in result:
            result.append(order_no)
    return result



def merged_order_numbers(order_text: str, *notes: str, merged: bool = False) -> list[str]:
    """合併開立要回寫發票號碼的訂單：G 欄原訂單＋J／K 欄列出的所有訂單（2026-11-05 Jenny）。
    合併開立＝G 欄有兩張以上、C 欄是合併開立發票（merged=True），或 J／K 寫了「合併」；不是合併開立就只回傳 G 欄。"""
    result = split_order_numbers(order_text)
    if not (merged or len(result) >= 2 or any("合併" in str(note or "") for note in notes)):
        return result
    for note in notes:
        for order_no in (m.group(0).upper() for m in _ORDER_NO_RE.finditer(str(note or ""))):
            if order_no not in result:
                result.append(order_no)
    return result
