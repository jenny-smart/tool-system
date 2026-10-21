# -*- coding: utf-8 -*-
"""三種批次成單共用的建單條件（批次建單／批次建單優化／雲端批次成單）。

可建單＝訂單編號空白＋狀態「未安排」＋「預約條件」欄不是「先不約」開頭。
「預約條件」由 tool-system 更新排程時依主控試算表「排程預約條件」寫入
（例如年節大掃除：確認表 V 欄非 TRUE、或 A 欄標記改／加），狀態本身不改。
"""
from __future__ import annotations

import re

HOLD_COLUMN = "預約條件"
HOLD_PREFIX = "先不約"


def _scalar(value):
    if hasattr(value, "iloc"):
        return value.iloc[0] if len(value) else ""
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return value


def _text(value) -> str:
    text = str(_scalar(value) if value is not None else "").strip()
    return "" if text in ("nan", "None") else text


def normalize_status(value) -> str:
    text = _text(value).replace(" ", " ").replace("﻿", "").replace("​", "")
    return re.sub(r"\s+", "", text)


def hold_reason(row) -> str:
    text = _text(row.get(HOLD_COLUMN, ""))
    return text if text.startswith(HOLD_PREFIX) else ""


def create_block_reason(row) -> str:
    """回傳不能建單的原因；空字串代表可建單。"""
    if _text(row.get("訂單編號", "")):
        return "已有訂單編號"
    status = normalize_status(row.get("狀態", ""))
    if status != "未安排":
        return f"狀態為「{status or '空白'}」，非未安排"
    return hold_reason(row)


def can_create_order(row) -> bool:
    return not create_block_reason(row)


def can_process_row(row) -> bool:
    """已有單號（寄信／改日曆）或可建單的列才進批次。"""
    return bool(_text(row.get("訂單編號", ""))) or can_create_order(row)
