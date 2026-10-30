"""【富邦銀行】內部請款整合執行：盤點台北／台中待付款 → 勾選 → 排入本機 Agent → 留紀錄。

三種內部請款（資料位置見主控表「財務帳務設定」）：
  - 請款記錄（財報「請款記錄」A＝待付款）            → 【富邦銀行】請款記錄
  - 工具包押金退款（H 押金＞0、I 有日期、S 空白）        → 【富邦銀行】工具包押金退款
  - 清潔用品採購（進貨明細表，Q 匯款日期與 S 請款日期都空白，依月份） → 【富邦銀行】清潔用品採購
勾選後每個地區排一筆本機 Agent：登入一次、轉帳全部做完才下載明細（財報最後一列日期～今天）（富邦轉帳仍需手機核准）；最後排一筆「留紀錄」，
重新盤點勾選的列，已付款的列寫進 Jenny 行事曆（jenny@lemonclean.com.tw，不跳提醒）。
每週二、五 10:00 的富邦待辦通知也用這裡的 scan() 列出待處理筆數。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
AREAS = ["台北", "台中"]
RECORD_CALENDAR_ID = "jenny@lemonclean.com.tw"

# kind → (顯示名稱, 本機 Agent action)
KINDS = {
    "payment": ("請款記錄", "fubon.payment_request"),
    "deposit": ("工具包押金退款", "fubon.deposit_refund"),
    "supply": ("清潔用品採購", "fubon.supply_purchase"),
}
# 同一地區三種一起做：登入一次、轉帳全部完成後才下載明細
BATCH_ACTION = "fubon.internal_payment"


def _supply_months(values: list[list[str]]) -> list[str]:
    """進貨明細表裡還有未付款（Q、S 都空白）列的採購月份（B 欄，YYYYMM）。"""
    from tools.bank_statement.fubon_supply_purchase_filter import is_paid

    months = []
    for row in values[1:]:
        month = re.sub(r"\D", "", str(row[1] if len(row) > 1 else ""))[:6]
        if len(month) == 6 and not is_paid(row) and month not in months:
            months.append(month)
    return sorted(months)


def _items_for(kind: str, area: str, values: list[list[str]]) -> list[dict]:
    from tools.bank_statement.fubon_deposit_refund_filter import pending_deposit_refunds
    from tools.bank_statement.fubon_payment_request_filter import pending_payment_requests
    from tools.bank_statement.fubon_supply_purchase_filter import pending_supply_purchases

    items = []
    if kind == "payment":
        for c in pending_payment_requests(values):
            items.append({"kind": kind, "area": area, "row": int(c["sheet_row"]), "rows": [int(c["sheet_row"])],
                          "month": "", "payee": c["name"], "amount": str(c["amount"]), "memo": c.get("memo", ""),
                          "account": str(c.get("account_number") or "") or str(c.get("account_text") or "")})
    elif kind == "deposit":
        for c in pending_deposit_refunds(values):
            items.append({"kind": kind, "area": area, "row": int(c["sheet_row"]), "rows": [int(c["sheet_row"])],
                          "month": "", "payee": str(c.get("name", "")), "amount": str(c["amount"]),
                          "memo": str(c.get("memo", ""))})
    else:
        for month in _supply_months(values):
            for c in pending_supply_purchases(values, month):
                items.append({"kind": kind, "area": area, "row": int(c["sheet_row"]),
                              "rows": [int(r) for r in c["rows"]], "month": month,
                              "payee": str(c.get("supplier", "")), "amount": str(c["amount"]),
                              "memo": f"{month} 採購（第{'、'.join(map(str, c['rows']))}列）"})
    return items


def _report_type(kind: str) -> str:
    from tools.bank_statement import internal_payment_registry as reg

    return {"payment": reg.PAYMENT_REQUEST_TYPE, "deposit": reg.DEPOSIT_REFUND_TYPE,
            "supply": reg.SUPPLY_PURCHASE_TYPE}[kind]


def scan(areas=None, kinds=None, log: Callable[[str], None] = print) -> list[dict]:
    from tools.bank_statement.internal_payment_registry import read_report_values

    items = []
    for area in areas or AREAS:
        for kind in kinds or KINDS:
            try:
                items.extend(_items_for(kind, area, read_report_values(_report_type(kind), area)))
            except Exception as exc:
                log(f"❌ 【{area}】{KINDS[kind][0]} 讀取失敗：{exc}")
    return items


def _key(item: dict) -> tuple:
    return item["kind"], item["area"], int(item["row"]), item.get("month", "")


def merge_preview(items: list[dict], merge_rows) -> list[dict]:
    """勾了「合併」的請款記錄裡，同地區、同收款對象＋收款帳號、超過一列的會合併成一筆（跟 merge_by_payee 同規則）。"""
    groups: dict[tuple, list[dict]] = {}
    for i in items:
        if i["kind"] == "payment" and (i["area"], int(i["row"])) in merge_rows:
            groups.setdefault((i["area"], i.get("payee", ""), i.get("account", "")), []).append(i)
    result = []
    for (area, payee, _account), members in groups.items():
        if len(members) < 2:
            continue
        total = sum(Decimal(str(m.get("amount") or 0)) for m in members)
        result.append({"area": area, "payee": payee, "rows": sorted(int(m["row"]) for m in members),
                       "amount": str(int(total)) if total == total.to_integral() else format(total, "f")})
    return result


def queue_actions(items: list[dict], create_task: Callable, created_by: str = "內部請款整合執行",
                  log: Callable[[str], None] = print, merge_rows=(), split_supply=()) -> list[dict]:
    """每區排一筆本機 Agent 任務：登入一次、轉帳全部做完才下載明細；最後排一筆「留紀錄」。

    merge_rows：勾了「合併」的請款記錄 {(地區, 列號)}；其中同一收款對象的列合併成一筆轉帳，沒勾的照單筆轉。
    split_supply：取消「合併」的清潔用品採購 {(地區, 月份, 列號)}；同帳號的各列改成各自轉帳。"""
    tasks = []
    for area in AREAS:
        hit = [i for i in items if i["area"] == area]
        if not hit:
            continue
        rows: dict[str, list[int]] = {"payment": [], "deposit": []}
        supply: dict[str, list[int]] = {}
        for i in hit:
            target = supply.setdefault(i.get("month", ""), []) if i["kind"] == "supply" else rows[i["kind"]]
            target.append(int(i["row"]))
        params = {"area": area, "payment_rows": sorted(rows["payment"]), "deposit_rows": sorted(rows["deposit"]),
                  "supply": {month: sorted(r) for month, r in sorted(supply.items())},
                  "cdp_url": "http://127.0.0.1:9222"}
        merge = sorted(r for r in params["payment_rows"] if (area, r) in merge_rows)
        if merge:
            params["merge_rows"] = merge
        split = {month: sorted(r for r in rows if (area, month, r) in split_supply)
                 for month, rows in params["supply"].items()}
        if any(split.values()):
            params["supply_split"] = {month: rows for month, rows in split.items() if rows}
        task = create_task(BATCH_ACTION, params, created_by=created_by)
        tasks.append(task)
        counts = "、".join(f"{label} {sum(1 for i in hit if i['kind'] == kind)} 筆"
                          for kind, (label, _action) in KINDS.items() if any(i["kind"] == kind for i in hit))
        log(f"已排入：{area} {counts}（轉完才下載明細）→ {task.get('task_id', '')}")
    if items:
        selected = [{"kind": i["kind"], "area": i["area"], "row": int(i["row"]), "month": i.get("month", ""),
                     "payee": i.get("payee", ""), "amount": i.get("amount", "")} for i in items]
        task = create_task("internal_payment.record", {"items": selected}, created_by=created_by)
        tasks.append(task)
        log(f"已排入：執行完留紀錄 → {task.get('task_id', '')}")
    return tasks


def build_record_text(done: list[dict], pending: list[dict], now: datetime) -> tuple[str, str]:
    lines = [f"{now:%Y/%m/%d %H:%M}"]
    for title, rows in (("已付款", done), ("未完成", pending)):
        if not rows:
            continue
        lines.append(f"【{title}】")
        for area in AREAS:
            for kind, (label, _action) in KINDS.items():
                hit = [r for r in rows if r["area"] == area and r["kind"] == kind]
                if hit:
                    detail = "，".join(f"{r['row']}（{r.get('payee', '')} {r.get('amount', '')}）" for r in hit)
                    lines.append(f"{area} {label}：{len(hit)}筆，列數：{detail}")
    return f"本日內部請款已付款：{len(done)} 筆" + (f"（未完成 {len(pending)} 筆）" if pending else ""), "\n".join(lines)


def record(selected: list[dict], log: Callable[[str], None] = print, now: Optional[datetime] = None) -> dict:
    """重新盤點勾選的列：已不在待付款清單＝已付款。結果寫進 Jenny 行事曆（不跳提醒）。"""
    areas = sorted({s["area"] for s in selected}, key=AREAS.index)
    kinds = [k for k in KINDS if any(s["kind"] == k for s in selected)]
    still = {_key(i) for i in scan(areas, kinds, log)}
    done = [s for s in selected if _key(s) not in still]
    pending = [s for s in selected if _key(s) in still]
    now = now or datetime.now(TW_TZ)
    title, body = build_record_text(done, pending, now)
    log(title)
    from tools.orders_system import calendar_notify

    calendar_notify.create_notify_event(summary=title, description=body, start=now,
                                        calendar_id=RECORD_CALENDAR_ID, attendee="", reminder_minutes=[])
    log(f"已在 {RECORD_CALENDAR_ID} 行事曆留紀錄")
    return {"done": done, "pending": pending}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="內部請款整合執行：執行完留紀錄")
    parser.add_argument("command", choices=["record"])
    parser.add_argument("--items", required=True, help="JSON：勾選的列")
    args = parser.parse_args(argv)
    result = record(json.loads(args.items))
    return 1 if result["pending"] else 0


if __name__ == "__main__":
    sys.exit(main())
