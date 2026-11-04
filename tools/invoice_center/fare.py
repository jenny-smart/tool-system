"""車馬費金額解析（清潔異動 J 欄服務註記）。清潔異動整合執行與【鯨躍發票】開立發票共用。"""
from __future__ import annotations

import re

# 「車馬費」後面隔幾個字再接金額都算：車馬費 $300、車馬費發票$200、車馬費：300、車馬費 NT$1,200
_FARE_RE = re.compile(r"車馬費[^\d\n，,；;。]{0,8}?(?:NT)?[$＄]?\s*([\d][\d,]*)")
_ONLY_NUMBER_RE = re.compile(r"\s*(?:NT)?[$＄]?\s*([\d][\d,]*)\s*(?:元)?\s*")


def fare_amount(text: object) -> int:
    """J 欄文字 → 車馬費金額；找不到回傳 0。"""
    value = str(text or "")
    match = _FARE_RE.search(value) or _ONLY_NUMBER_RE.fullmatch(value)
    if not match:
        return 0
    return int(match.group(1).replace(",", ""))
