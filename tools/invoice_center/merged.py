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
