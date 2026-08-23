from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tools.memo_system.change_order import get_worksheet


DISPLAY_COLUMNS = ("列號", "B 狀態", "G 訂單編號", "H 客戶", "K 後台備註", "M 收款時間", "N 收款金額")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def get_pending_invoice_candidates(area: str) -> list[dict[str, Any]]:
    """Return rows eligible for invoice creation from the area's 清潔異動 sheet.

    Eligibility: B=待收款, G(order no) is not blank, and O(invoice no) is blank.
    """
    ws = get_worksheet(area)
    values = ws.get("B:O")
    rows: list[dict[str, Any]] = []

    for row_no, row in enumerate(values, start=1):
        padded = list(row) + [""] * (14 - len(row))
        status = str(padded[0] or "").strip()
        order_no = str(padded[5] or "").strip()
        invoice_no = str(padded[13] or "").strip()
        if status != "待收款" or not order_no or invoice_no:
            continue
        rows.append({
            "選取": False,
            "列號": row_no,
            "B 狀態": status,
            "G 訂單編號": order_no,
            "H 客戶": padded[6],
            "K 後台備註": padded[9],
            "M 收款時間": padded[11],
            "N 收款金額": padded[12],
            "_order_no": order_no,
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
