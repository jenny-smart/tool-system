# -*- coding: utf-8 -*-
"""指定列批次建單狀態防呆。

只有「未安排」且訂單編號空白的列可以進入建單。
待確認／已安排／暫停／保留單一律不執行。
同時清除 Google Sheet 可能帶入的不可見空白字元，避免畫面看似「未安排」卻被誤判。
"""
from __future__ import annotations

import re

import orders

_INSTALLED = False


def normalize_status(value) -> str:
    text = str(value or "")
    # Google Sheet / 複製貼上偶爾會帶 NBSP、BOM、zero-width space。
    text = text.replace("\u00a0", " ").replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", "", text)


def _is_unarranged_blank_order(row) -> bool:
    return normalize_status(row.get("狀態", "")) == "未安排" and orders.is_blank(row.get("訂單編號", ""))


def install_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # 共用核心：指定列進入 run_process_web 後仍只允許「未安排」。
    orders.should_process_row = _is_unarranged_blank_order
    orders.should_create_order = _is_unarranged_blank_order

    # 優化版／雲端版候選清單也同步套同一規則，避免待確認等狀態先被列為候選，
    # 到核心才顯示「沒有符合條件的資料可執行」。
    try:
        import batch_booking_optimized as batch_opt

        original_load_candidates = batch_opt._load_candidates

        def _load_candidates_unarranged_only(sheet_name: str):
            work = original_load_candidates(sheet_name)
            if work.empty:
                return work
            return work[work["狀態"].map(normalize_status).eq("未安排")].copy()

        batch_opt._load_candidates = _load_candidates_unarranged_only
    except Exception:
        # 不影響非優化批次入口；核心防呆仍然有效。
        pass

    _INSTALLED = True
