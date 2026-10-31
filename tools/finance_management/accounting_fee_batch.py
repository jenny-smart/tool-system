"""【整合執行】會計事務所費用確認：台北每單月 10 日左右，確認金額 → 新增到請款記錄／台北。

項目（2026-10 與 Jenny 確認，都記在請款記錄／台北、收款對象＝會計事務所）：
  - 每單月：台北前 2 個月營業稅、台中前 2 個月營業稅（金額每次不同，要填）
            當月／下月辦公室租金代扣繳 8,800×2、當月／下月辦公室二代健保 1,857×2
  - 5 月、9 月：營所稅（金額要填）
  - 1 月：半年度記帳費 台北 3,000×6、台中 2,000×6；7 月：台北 3,000×7、台中 3,000×7（偶爾有調整，金額可改）
  - 會計師簽證費 60,000：通常 3 月（3 月預設打勾；其他單月也列出，需要時自己勾），每年一次
  雙月沒有會計事務所費用。已新增過的（同費用說明）不會重複新增。
排程：每單月 10 日 10:00 雲端在 Jenny 行事曆建提醒，列出本期要確認的項目（remind）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from tools.finance_management.fixed_expense_batch import label, next_period

TW_TZ = ZoneInfo("Asia/Taipei")
AREA = "台北"
TOOL_NAME = "會計事務所費用確認"
PAYEE = "會計事務所"
REMIND_CALENDAR_ID = "jenny@lemonclean.com.tw"

RENT_WITHHOLDING = 8800  # 辦公室租金代扣繳，每月
RENT_NHI = 1857  # 辦公室租金二代健保，每月
AUDIT_FEE = 60000  # 會計師簽證費，通常 3 月
# 半年度記帳費：月份 → {地區: (每月, 月數)}
BOOKKEEPING = {1: {"台北": (3000, 6), "台中": (2000, 6)}, 7: {"台北": (3000, 7), "台中": (3000, 7)}}
INCOME_TAX_MONTHS = {5: "結算申報", 9: "暫繳"}


def prev_period(period: str, months: int) -> str:
    index = int(period[:4]) * 12 + int(period[4:6]) - 1 - months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def _item(memo: str, category: str, amount, note: str = "", default: bool = True) -> dict[str, Any]:
    return {"area": AREA, "memo": memo, "category": category, "amount": amount, "payee": PAYEE,
            "account": "", "note": note, "default_hint": default}


def planned_items(period: str) -> list[dict[str, Any]]:
    month = int(period[4:6])
    if month % 2 == 0:
        return [dict(_item(f"{label(period)}（雙月）", "", ""), info="雙月沒有會計事務所費用（每單月 10 日左右確認）")]
    first, second = prev_period(period, 2), prev_period(period, 1)
    tax_span = f"{label(first)}～{second[4:6]}"
    following = next_period(period)
    items = [
        _item(f"{tax_span}-台北營業稅", "營業稅", "", "前 2 個月營業稅，請填金額"),
        _item(f"{tax_span}-台中營業稅", "營業稅", "", "前 2 個月營業稅，請填金額"),
        _item(f"{label(period)}-辦公室租金代扣繳", "辦公室租金", RENT_WITHHOLDING),
        _item(f"{label(following)}-辦公室租金代扣繳", "辦公室租金", RENT_WITHHOLDING),
        _item(f"{label(period)}-辦公室二代健保", "辦公室租金", RENT_NHI),
        _item(f"{label(following)}-辦公室二代健保", "辦公室租金", RENT_NHI),
    ]
    if month in INCOME_TAX_MONTHS:
        items.append(_item(f"{label(period)}-營所稅", "營所稅", "", f"{INCOME_TAX_MONTHS[month]}，請填金額"))
    for area, (fee, months) in BOOKKEEPING.get(month, {}).items():
        items.append(_item(f"{label(period)}-{area}半年度記帳費", "記帳費", fee * months,
                           f"{fee:,}×{months}（有調整請改金額）"))
    items.append(_item(f"{period[:4]}-會計師簽證費", "記帳費", AUDIT_FEE,
                       "通常 3 月" if month == 3 else "通常 3 月，今年還沒新增；需要時再勾", default=month == 3))
    return items


def apply_defaults(items: list[dict]) -> list[dict]:
    """可新增但不是本月一定要做的（簽證費非 3 月）不預設打勾；沒金額的也先不勾，填了金額再勾。"""
    result = []
    for item in items:
        item = dict(item)
        if item.get("selectable"):
            item["default"] = bool(item.get("default_hint", True)) and item["amount"] not in ("", None)
            if item["amount"] in ("", None):
                item["status"] = "可新增（請填金額）"
        result.append(item)
    return result


def scan(period: str, log: Callable[[str], None] = print) -> list[dict]:
    from tools.finance_management.fixed_expense_batch import scan_items

    return apply_defaults(scan_items(planned_items(period), log=log))


def build_reminder_text(period: str, items: list[dict]) -> tuple[str, str]:
    todo = [i for i in items if i.get("selectable") and i.get("default_hint", True)]
    lines = [f"請到 Tools App【整合執行】會計事務所費用確認，按「盤點」確認金額後新增到請款記錄／台北。", ""]
    for item in todo:
        amount = f"{int(item['amount']):,}" if item["amount"] not in ("", None) else "（請填金額）"
        lines.append(f"・{item['memo']}：{amount}")
    done = [i for i in items if i.get("status") == "已新增過"]
    if done:
        lines += ["", "已新增過：" + "、".join(i["memo"] for i in done)]
    return f"會計事務所費用確認 {label(period)}：{len(todo)} 項待確認", "\n".join(lines)


def remind(today: Optional[date] = None, log: Callable[[str], None] = print) -> Optional[dict]:
    """每單月 10 日：在 Jenny 行事曆建提醒（雙月不建）。"""
    today = today or datetime.now(TW_TZ).date()
    period = today.strftime("%Y%m")
    if today.month % 2 == 0:
        log(f"{period} 是雙月，沒有會計事務所費用，不建提醒")
        return None
    items = scan(period, log=log)
    title, body = build_reminder_text(period, items)
    from tools.orders_system import calendar_notify

    event = calendar_notify.create_notify_event(summary=title, description=body, calendar_id=REMIND_CALENDAR_ID,
                                                attendee="")
    log(f"已在 {REMIND_CALENDAR_ID} 行事曆建提醒：{title}")
    return event


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="會計事務所費用確認：建立行事曆提醒")
    parser.add_argument("command", choices=["remind"])
    parser.add_argument("--date", default="", help="YYYY-MM-DD，預設今天（台北時間）")
    args = parser.parse_args(argv)
    remind(date.fromisoformat(args.date) if args.date else None, log=lambda text: print(text, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
