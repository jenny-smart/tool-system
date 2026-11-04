from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tools.lemon_backend.stored_value_sheet import get_worksheet


from tools.invoice_center.merged import MERGED_INVOICE_DETAILS, split_order_numbers  # noqa: E402


DISPLAY_COLUMNS = ("列號", "B 狀態", "C 細項", "G 訂單編號", "H 客戶", "J 內容", "K 後台備註", "M 收款時間", "N 收款金額")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def get_pending_invoice_candidates(area: str) -> list[dict[str, Any]]:
    """Return rows eligible for invoice creation from the area's 清潔異動 sheet.

    Eligibility: 待收款，或待處理發票＋車馬費發票／合併開立發票，或已收款＋異動發票／合併開立（還沒開發票）；
    G 不可空白且 O 尚無發票。
    """
    ws = get_worksheet(area)
    values = ws.get("B:O")
    rows: list[dict[str, Any]] = []

    for row_no, row in enumerate(values, start=1):
        padded = list(row) + [""] * (14 - len(row))
        status = str(padded[0] or "").strip()
        detail = str(padded[1] or "").strip()
        order_no = str(padded[5] or "").strip()
        invoice_no = str(padded[13] or "").strip()
        is_pending_charge = status == "待收款"
        is_fare_invoice = status == "待處理發票" and detail == "車馬費發票"
        # 合併開立：C 欄寫「合併開立發票」，或 G 欄有兩張以上訂單（例如「異動發票」一次開多張訂單）；改三聯走二聯改三聯
        k_note = str(padded[9] or "")
        is_merged = status == "待處理發票" and (
            detail in MERGED_INVOICE_DETAILS
            or (len(split_order_numbers(order_no)) >= 2 and not re.search(r"(?:改|轉)\s*三聯", f"{detail} {k_note}"))
        )
        # 已收款但還沒開發票的異動發票／合併開立（2026-11 Jenny）
        paid_uninvoiced = status == "已收款" and (
            detail == "異動發票" or detail in MERGED_INVOICE_DETAILS or len(split_order_numbers(order_no)) >= 2)
        if paid_uninvoiced and len(split_order_numbers(order_no)) >= 2:
            is_merged = True
        if not (is_pending_charge or is_fare_invoice or is_merged or paid_uninvoiced) or not order_no or invoice_no:
            continue
        rows.append({
            "選取": False,
            "列號": row_no,
            "B 狀態": status,
            "C 細項": detail,
            "G 訂單編號": order_no,
            "H 客戶": padded[6],
            "J 內容": padded[8],
            "K 後台備註": padded[9],
            "M 收款時間": padded[11],
            "N 收款金額": padded[12],
            "_order_no": order_no,
            "_invoice_flow": "fare" if is_fare_invoice else "merged" if is_merged else "charge",
            "_order_nos": split_order_numbers(order_no),
        })
    return rows


def mark_invoice_completed(area: str, row_no: int, invoice_no: str) -> str:
    """Write invoice number to O and Taiwan completion timestamp to AA."""
    invoice_no = str(invoice_no or "").strip()
    if not invoice_no:
        raise ValueError("發票號碼不可空白")
    completed_at = datetime.now(TAIPEI_TZ).strftime("%Y/%m/%d %H:%M:%S")
    ws = get_worksheet(area)
    ws.batch_update(
        [
            {"range": f"O{row_no}", "values": [[invoice_no]]},
            {"range": f"AA{row_no}", "values": [[completed_at]]},
        ],
        value_input_option="USER_ENTERED",
    )
    return completed_at
