"""【整合執行】固定費用（辦公室租金）：盤點 → 勾選 → 新增到請款記錄（之後由【整合執行】富邦內部請款付款）。

規則（2026-10 與 Jenny 確認）：
  - 台北每月 1 日：當月辦公室租金 77,343、辦公室管理費 9,392（請款記錄／台北）
  - 台中每單月 1 日：當月＋下月館前路租金管理費各 18,000（請款記錄／台中）；雙月不用請款
  - 費用說明跟【財報】台北／台中固定費用請款一樣（例：2026.11-辦公室租金），已新增過的會略過，
    所以之後再跑台北／台中固定費用請款（查信、電費）也不會重複新增租金。
排程：每月 1 日 00:30 雲端自動新增（auto_submit），結果寫進執行 Log。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
AREAS = ["台北", "台中"]
TOOL_NAME = "固定費用整合執行"
REQUEST_SHEET_RANGE = "A:I"


def label(period: str) -> str:
    return f"{period[:4]}.{period[4:6]}"


def next_period(period: str) -> str:
    year, month = int(period[:4]), int(period[4:6])
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}{month:02d}"


def already_submitted(existing: set[str], memo: str) -> bool:
    return any(x == memo or x.startswith(memo + "，") for x in existing)


def planned_items(period: str) -> list[dict[str, Any]]:
    """這個月（period）該有的固定費用；台中雙月回傳一筆不可勾的說明列。"""
    from tools.finance_management.taichung_fixed_expense import MONTHLY_RENT, RENT_ACCOUNT, RENT_PAYEE
    from tools.finance_management.taipei_fixed_expense import OFFICE_RENT_ITEMS

    items = [{"area": "台北", "memo": f"{label(period)}-{name}", "category": category, "amount": amount,
              "payee": payee, "account": ""} for name, category, amount, payee in OFFICE_RENT_ITEMS]
    if int(period[4:6]) % 2 == 1:
        for p in (period, next_period(period)):
            items.append({"area": "台中", "memo": f"{label(p)}-辦公室租金管理費", "category": "辦公室租金",
                          "amount": MONTHLY_RENT, "payee": RENT_PAYEE, "account": RENT_ACCOUNT})
    else:
        items.append({"area": "台中", "memo": f"{label(period)}（雙月）", "category": "", "amount": "",
                      "payee": "", "account": "", "info": "台中單月一次付當月＋下月，雙月不用請款"})
    return items


def mark_status(items: list[dict], existing_by_area: dict[str, set[str]]) -> list[dict]:
    """依請款記錄現有的 E 欄（費用說明）標示：已新增過的不可勾。"""
    result = []
    for item in items:
        item = dict(item)
        if item.get("info"):
            item.update(status=item["info"], selectable=False, default=False)
        elif already_submitted(existing_by_area.get(item["area"], set()), item["memo"]):
            item.update(status="已新增過", selectable=False, default=False)
        else:
            item.update(status="可新增", selectable=True, default=True)
        result.append(item)
    return result


def _existing(service, area: str) -> tuple[str, str, set[str]]:
    from tools.bank_statement.internal_payment_registry import PAYMENT_REQUEST_TYPE, resolve_report_location
    from tools.finance_management.taipei_fixed_expense import _existing_memos

    spreadsheet_id, sheet_title = resolve_report_location(PAYMENT_REQUEST_TYPE, area)
    return spreadsheet_id, sheet_title, _existing_memos(service, spreadsheet_id, sheet_title)


def scan_items(items: list[dict], log: Callable[[str], None] = print, service=None) -> list[dict]:
    """共用：讀各區請款記錄，標示 items 哪些已新增過（會計事務所費用確認也用這個）。"""
    from tools.common.config_loader import get_sheets_service

    service = service or get_sheets_service()
    existing = {}
    for area in sorted({i["area"] for i in items}):
        try:
            existing[area] = _existing(service, area)[2]
        except Exception as exc:
            log(f"❌ 【{area}】請款記錄讀取失敗：{exc}")
            existing[area] = None
    marked = mark_status([i for i in items if existing.get(i["area"]) is not None or i.get("info")], existing)
    failed = [dict(i, status="請款記錄讀取失敗", selectable=False, default=False)
              for i in items if existing.get(i["area"]) is None and not i.get("info")]
    return marked + failed


def scan(period: str, log: Callable[[str], None] = print) -> list[dict]:
    return scan_items(planned_items(period), log=log)


def submit(items: list[dict], tool_name: str = TOOL_NAME, run_type: str = "手動",
           log: Callable[[str], None] = print, now: Optional[datetime] = None) -> dict[str, int]:
    """把勾選的列新增到各區請款記錄（新增前再檢查一次已新增過的就略過）。回傳 {地區: 新增筆數}。"""
    from tools.common.config_loader import get_sheets_service
    from tools.finance_management.execution_log import log_execution

    service = get_sheets_service()
    now_text = (now or datetime.now(TW_TZ)).strftime("%Y/%m/%d %H:%M:%S")
    added: dict[str, int] = {}
    for area in AREAS:
        hit = [i for i in items if i["area"] == area and not i.get("info")]
        if not hit:
            continue
        spreadsheet_id, sheet_title, existing = _existing(service, area)
        rows, skipped = [], []
        for item in hit:
            if already_submitted(existing, item["memo"]):
                skipped.append(item["memo"])
                continue
            rows.append(["待付款", "", now_text, item["category"], item["memo"], int(item["amount"]), "",
                         item["payee"], item.get("account", "")])
        if rows:
            service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'!{REQUEST_SHEET_RANGE}",
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"values": rows},
            ).execute()
        added[area] = len(rows)
        detail = "\n".join([f"{r[4]}：{r[5]}" for r in rows] + [f"{m}：略過（已新增過）" for m in skipped])
        log_execution(tool_name, area, "成功", f"{run_type}｜新增 {len(rows)} 筆\n{detail}")
        log(f"【{area}】請款記錄新增 {len(rows)} 筆" + (f"，略過 {len(skipped)} 筆（已新增過）" if skipped else ""))
    return added


def auto_submit(today: Optional[date] = None, log: Callable[[str], None] = print) -> dict[str, int]:
    """每月 1 日排程：盤點當月，可新增的全部新增。"""
    today = today or datetime.now(TW_TZ).date()
    items = [i for i in scan(today.strftime("%Y%m"), log=log) if i["default"]]
    if not items:
        log("固定費用：本月都已新增過，沒有要新增的列")
        return {}
    return submit(items, run_type="排程", log=log)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="固定費用整合執行：新增當月辦公室租金到請款記錄")
    parser.add_argument("--date", default="", help="YYYY-MM-DD，預設今天（台北時間）")
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.date) if args.date else None
    auto_submit(today, log=lambda text: print(text, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
