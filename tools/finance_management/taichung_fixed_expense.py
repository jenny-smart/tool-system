"""台中固定費用請款：單月一次付當月＋隔月的辦公室租金及電費，寫入請款記錄／台中。"""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from typing import Any

from tools.bank_statement.internal_payment_registry import PAYMENT_REQUEST_TYPE, resolve_report_location
from tools.common.config_loader import get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.taipei_fixed_expense import (
    REQUEST_SHEET_RANGE,
    TW_TZ,
    _already_submitted,
    _existing_memos,
    _next_period,
)

AREA = "台中"
TOOL_NAME = "台中固定費用請款"
MONTHLY_RENT = 18000  # 館前路租金＋管理費，每月
RENT_PAYEE = "館前路租金管理費"
RENT_ACCOUNT = "013-0000234035010972"  # 國泰世華


def _label(period: str) -> str:
    return f"{period[:4]}.{period[4:6]}"


def build_taichung_rows(period: str, electricity: int | None, existing: set[str], now: str):
    """回傳 (要新增的列, 每項結果)；租金、電費都是當月＋隔月各一筆。"""
    next_period = _next_period(period)
    current, following = _label(period), _label(next_period)
    planned = [
        (f"{current}-辦公室租金管理費", "辦公室租金", MONTHLY_RENT),
        (f"{following}-辦公室租金管理費", "辦公室租金", MONTHLY_RENT),
    ]
    if electricity:
        # 電費每次給一筆金額，除以 2 拆成當月／隔月；除不盡時多的 1 元算隔月（例：87 → 43＋44）
        half = electricity // 2
        planned.append((f"{current}-電費", "電費", half))
        planned.append((f"{following}-電費", "電費", electricity - half))
    rows: list[list[Any]] = []
    items: list[dict[str, Any]] = []
    for memo, category, amount in planned:
        if _already_submitted(existing, memo):
            items.append({"label": memo, "amount": None, "status": "略過（本期已新增過）"})
            continue
        rows.append(["待付款", "", now, category, memo, amount, "", RENT_PAYEE, RENT_ACCOUNT])
        items.append({"label": memo, "amount": amount, "status": "成功"})
    if not electricity:
        items.append({"label": f"{current}-電費", "amount": None, "status": "略過（未輸入電費金額）"})
    return rows, items


def submit_taichung_fixed_expenses(period: str, electricity: int | None = None, run_type: str = "手動") -> dict[str, Any]:
    period = (period or "").strip()
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202611")
    if int(period[4:6]) % 2 == 0:
        raise ValueError(f"台中固定費用只在單月請款（一次付當月＋隔月），{period} 是雙月")
    if electricity is not None and electricity < 0:
        raise ValueError("電費金額不能是負數")
    now = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
    spreadsheet_id, sheet_title = resolve_report_location(PAYMENT_REQUEST_TYPE, AREA)
    service = get_sheets_service()
    rows, items = build_taichung_rows(period, electricity, _existing_memos(service, spreadsheet_id, sheet_title), now)
    if rows:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_title}'!{REQUEST_SHEET_RANGE}",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
    item_lines = "\n".join(f"{i['label']}：{i['status']}" + (f"（{i['amount']}）" if i["amount"] is not None else "") for i in items)
    log_execution(TOOL_NAME, AREA, "成功", f"{run_type}｜執行期別={_label(period)}｜新增 {len(rows)} 筆\n{item_lines}")
    return {"period": period, "period_label": _label(period), "sheet_title": sheet_title, "rows_added": len(rows), "items": items}


def main() -> None:
    parser = argparse.ArgumentParser(description="新增『台中固定費用請款』")
    parser.add_argument("--period", required=True)
    parser.add_argument("--electricity", type=int, default=None)
    parser.add_argument("--run-type", default="手動")
    args = parser.parse_args()
    print(submit_taichung_fixed_expenses(args.period, args.electricity, args.run_type))


if __name__ == "__main__":
    main()
