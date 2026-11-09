"""【整合執行】會計事務所費用確認：台北每單月 10 日左右，確認金額 → 寫入請款記錄並直接排富邦轉帳給勤信。

項目（2026-10 與 Jenny 確認，都由台北付給會計事務所）：
  - 每單月：台北前 2 個月營業稅、台中前 2 個月營業稅（金額每次不同，要填）
            當月／下月辦公室租金代扣繳 8,800×2、當月／下月辦公室二代健保 1,857×2
  - 5 月、9 月：營所稅（金額要填）
  - 1 月：半年度記帳費 台北 3,000×6、台中 2,000×6；7 月：台北 3,000×7、台中 3,000×7（偶爾有調整，金額可改）
  - 會計師簽證費 60,000：通常 3 月（3 月預設打勾；其他單月也列出，需要時自己勾），每年一次
  雙月沒有會計事務所費用。已新增過的（同費用說明）不會重複新增。
排程：每單月 10 日 10:00 雲端在 Jenny 行事曆建提醒，列出本期要確認的項目（remind）。

更新紀錄：
  2026-11-09 Jenny：按下去先寫請款記錄（待付款），再直接排【整合執行】富邦內部請款同一套轉帳：
                    - 請款記錄／台北：全部項目，收款對象＝勤信、帳號 013-0000240035015567；
                      本機 Agent 從台北富邦合併成一筆轉給勤信，轉完這幾列自動回填已付款＋付款日期
                    - 請款記錄／台中：台中的費用（台中營業稅、台中記帳費）另記一列待付款，收款對象＝台北
                      （富邦常用轉入帳號「台北」，尾數 3320）；這筆不自動轉，到【整合執行】富邦內部請款付
                    已新增過的（同費用說明）不會重複新增、也不會重複轉。
  2026-11-09 Jenny：台北／台中記帳費每個單月都列出（比照會計師簽證費）：1、7 月預設打勾；
                    其他月份預設不勾，事務所調整收費月份時再勾、改金額。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from tools.finance_management.fixed_expense_batch import label, next_period

TW_TZ = ZoneInfo("Asia/Taipei")
AREA = "台北"
TOOL_NAME = "會計事務所費用確認"
# 2026-11-09 Jenny：會計事務所＝勤信，富邦自行輸入帳號
PAYEE = "勤信"
PAYEE_BANK_CODE = "013"  # 國泰世華
PAYEE_ACCOUNT = "0000240035015567"
PAYEE_ACCOUNT_TEXT = f"{PAYEE_BANK_CODE}-{PAYEE_ACCOUNT}"  # 請款記錄 I 欄格式「銀行代碼-帳號」
# 台中的費用另外記在請款記錄／台中，台中付給台北（富邦常用轉入帳號「台北」，尾數 3320）
TAICHUNG = "台中"
TAICHUNG_PAYEE = "台北"
REMIND_CALENDAR_ID = "jenny@hers.com.tw"

RENT_WITHHOLDING = 8800  # 辦公室租金代扣繳，每月
RENT_NHI = 1857  # 辦公室租金二代健保，每月
AUDIT_FEE = 60000  # 會計師簽證費，通常 3 月
# 半年度記帳費：月份 → {地區: (每月, 月數)}
BOOKKEEPING = {1: {"台北": (3000, 6), "台中": (2000, 6)}, 7: {"台北": (3000, 7), "台中": (3000, 7)}}
INCOME_TAX_MONTHS = {5: "結算申報", 9: "暫繳"}


def prev_period(period: str, months: int) -> str:
    index = int(period[:4]) * 12 + int(period[4:6]) - 1 - months
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def bookkeeping_for(month: int) -> dict[str, tuple[int, int]]:
    """本月的記帳費參考金額：1、7 月照表；其他月份用最近一次（2～6 月看 1 月、8～12 月看 7 月）。"""
    return BOOKKEEPING.get(month) or BOOKKEEPING[1 if month < 7 else 7]


def _item(memo: str, category: str, amount, note: str = "", default: bool = True) -> dict[str, Any]:
    return {"area": AREA, "memo": memo, "category": category, "amount": amount, "payee": PAYEE,
            "account": PAYEE_ACCOUNT_TEXT, "note": note, "default_hint": default}


def is_taichung(item: dict) -> bool:
    """台中的費用（台中營業稅、台中半年度記帳費）：另外記到請款記錄／台中，台中付給台北。"""
    return f"-{TAICHUNG}" in str(item.get("memo", ""))


def with_taichung(selected: list[dict]) -> list[dict]:
    """勾選的項目（請款記錄／台北，付勤信）＋台中的費用另一列（請款記錄／台中，付台北）。"""
    return list(selected) + [dict(i, area=TAICHUNG, payee=TAICHUNG_PAYEE, account="")
                             for i in selected if is_taichung(i)]


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
    for area, (fee, months) in bookkeeping_for(month).items():
        if month in BOOKKEEPING:
            items.append(_item(f"{label(period)}-{area}半年度記帳費", "記帳費", fee * months,
                               f"{fee:,}×{months}（有調整請改金額）"))
        else:
            items.append(_item(f"{label(period)}-{area}半年度記帳費", "記帳費", fee * months,
                               f"通常 1、7 月；事務所調整月份時再勾，金額可改（參考 {fee:,}×{months}）",
                               default=False))
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
    """請款記錄／台北已有（同費用說明）的不能再勾。"""
    from tools.finance_management.fixed_expense_batch import scan_items

    return apply_defaults(scan_items(planned_items(period), log=log))


def build_reminder_text(period: str, items: list[dict]) -> tuple[str, str]:
    todo = [i for i in items if i.get("selectable") and i.get("default_hint", True)]
    lines = [f"請到 Tools App【整合執行】會計事務所費用確認，按「盤點」確認金額後直接排富邦轉帳。", ""]
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

    # 開始時間留 30 分鐘前導，Google 的提醒推播才排得進去（20、10 分鐘前各跳一次）
    event = calendar_notify.create_notify_event(summary=title, description=body, calendar_id=REMIND_CALENDAR_ID,
                                                start=datetime.now(TW_TZ) + timedelta(minutes=30),
                                                attendee="", reminder_minutes=[20, 10],
                                                replace_prefix=title.split("：")[0])
    log(f"已在 {(event or {}).get('_calendar_id', REMIND_CALENDAR_ID)} 行事曆建提醒：{title}")
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
