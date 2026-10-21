"""姓名排序：姓氏依總筆劃（台灣標準）由少到多；同姓再依原本的字碼順序（與先前匯出日曆一致）。

筆劃資料見 tools/common/stroke_data.py。個別字與教育部標準不同時，加進 STROKE_OVERRIDES。
"""
from __future__ import annotations

from tools.common.stroke_data import BASIC, BASIC_START, EXT_A, EXT_A_START

# 與教育部標準筆劃不同、需人工指定的字。
STROKE_OVERRIDES = {"嚴": 20, "魏": 18}
NON_CJK_STROKES = 999  # 英文、數字等排在中文姓名之後


def strokes(char: str) -> int:
    if char in STROKE_OVERRIDES:
        return STROKE_OVERRIDES[char]
    code = ord(char)
    for start, data in ((BASIC_START, BASIC), (EXT_A_START, EXT_A)):
        if start <= code < start + len(data):
            count = ord(data[code - start]) - 0x30
            return count or NON_CJK_STROKES
    return NON_CJK_STROKES


def name_sort_key(name: str) -> tuple:
    """（姓氏筆劃, 姓氏, 其餘字）：同筆劃不同姓依字碼分組，同姓維持原本的字碼順序。"""
    text = str(name or "").strip()
    if not text:
        return (NON_CJK_STROKES + 1, "", "")
    return (strokes(text[0]), text[0], text[1:])
