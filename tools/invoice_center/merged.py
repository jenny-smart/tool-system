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


def merged_order_numbers(order_text: str, *notes: str) -> list[str]:
    """合併開立的訂單清單：G 欄訂單，再加上 J／K 欄寫「合併開立」時列出的訂單。
    2026-11-05 Jenny：G 欄只填一張、J 欄寫「服務訂單合併開立發票$14000 LC…；LC…」也要合併開立，每張都回寫發票號碼。"""
    result = split_order_numbers(order_text)
    for note in notes:
        text = str(note or "")
        if "合併" not in text:
            continue
        for order_no in (m.group(0).upper() for m in _ORDER_NO_RE.finditer(text)):
            if order_no not in result:
                result.append(order_no)
    return result
