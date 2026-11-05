"""月度盤點（2026-11 Jenny）：每個月有哪些該做的事、做完了沒，盤點完可以直接勾選執行。

- 「月份」＝做事的月份（例如 11 月）。月初那批（期別＝上個月）沿用【整合執行】月初的盤點（month_start_check）。
- 其他每月工作各自檢查：工具包押金、年度review、台北查信、各區固定費用匯款、台中固定費用請款（單月）、現金缺口各項數字、
  會計事務所費用（單月）、鯨躍字軌（雙月）。
- 結果寫進主控表「月度盤點」分頁：每月每項一列、保留歷史；第一次變成完成時記「完成時間」，可回頭查拖了多久。
  自己加的列（項目代號空白）程式不會動，可當手動項目，在「狀態」欄自己填「✅ 完成」。
- 排程（排程設定 monthly_checklist）每天更新分頁；超過應完成日還沒做完的項目合併成一則行事曆提醒。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
SHEET_NAME = "月度盤點"
HEADER = ["月份", "項目代號", "項目", "應完成日", "狀態", "檢查結果", "最後檢查", "完成時間", "備註"]
REMIND_PREFIX = "【月度盤點】"

DONE, TODO, NOT_DUE, RUNNING, SKIP, MANUAL = "✅ 完成", "❌ 未完成", "⏳ 未到期", "🕒 執行中", "— 本月不用", "✋ 到功能頁處理"
DEPOSIT_AREAS = ("台北", "台中")


def previous_month(month: str) -> str:
    first = date(int(month[:4]), int(month[4:]), 1)
    return (first - timedelta(days=1)).strftime("%Y%m")


def month_day(month: str, day: int) -> date:
    return date(int(month[:4]), int(month[4:]), day)


def _earlier_workday(day: date) -> date:
    try:
        from tools.common.tw_holidays import is_workday
    except Exception:  # 讀不到政府辦公日曆：只略過週末
        def is_workday(d):
            return d.weekday() < 5
    while not is_workday(day):
        day -= timedelta(days=1)
    return day


def item(key: str, label: str, due: date, done: bool, detail: str, runnable: bool = True,
         running: bool = False, applicable: bool = True, **extra) -> dict:
    today = datetime.now(TW_TZ).date()
    if not applicable:
        status = SKIP
    elif done:
        status = DONE
    elif running:
        status = RUNNING
    elif not runnable:
        status = MANUAL if today >= due else NOT_DUE
    else:
        status = TODO if today >= due else NOT_DUE
    return {"key": key, "label": label, "due": due, "status": status, "detail": detail,
            "runnable": runnable and applicable and not done and not running, **extra}


# ── 各項檢查 ──────────────────────────────────────────

def month_start_items(month: str, log: Callable[[str], None]) -> list[dict]:
    """月初那批（期別＝上個月），沿用月初盤點；應完成日＝1 日。"""
    from tools.finance_management import month_start_check as chk

    rows = chk.collect_rows(month_day(month, 1), log=log)
    result = []
    for row in rows:
        waiting = any(row["cells"].get(a, ("", ""))[0] == chk.WAIT for a in row["todo"])
        detail = chk.summary_text(row) if row["todo"] else (row.get("note") or "完成")
        if row["todo"] and not row.get("remind_todo", row["todo"]):
            detail += "（已設定不提醒）"
        result.append(item(f"month_start:{row['key']}", f"月初｜{row['label']}", month_day(month, 1),
                           row["done"], detail, runnable=row["runnable"], running=waiting,
                           group="month_start", row=row))
    return result


def deposit_item(month: str) -> dict:
    from tools.finance_management.deposit_report import check_balance

    problems = []
    for area in DEPOSIT_AREAS:
        try:
            b = check_balance(area, month)
            if not b["equal"]:
                problems.append(f"{area} 第{b['row']}列 Y {b['y']} ≠ B1 {b['b1']}")
        except Exception as exc:
            problems.append(f"{area} 讀取失敗：{str(exc)[:60]}")
    return item("deposit_report", "【財報】工具包押金（彙整＋打勾＋比對＋Y／B1）", month_day(month, 1),
                not problems, "；".join(problems) or "台北、台中 Y＝B1")


def review_item(month: str) -> dict:
    from tools.finance_management.review_monthly import REVIEW_SHEETS
    from tools.finance_management.review_update import preview_review_formula_updates

    period = previous_month(month)
    pending = []
    for area in REVIEW_SHEETS:
        try:
            count = len(preview_review_formula_updates(area, f"{period[:4]}.{period[4:]}"))
            if count:
                pending.append(f"{area} {count} 格")
        except Exception as exc:
            pending.append(f"{area} 讀取失敗：{str(exc)[:60]}")
    return item("review_monthly", f"【財報】年度review（公式到 {period[:4]}.{period[4:]}預估＋隱藏欄位）",
                month_day(month, 1), not pending,
                ("預估公式還沒調整：" + "、".join(pending)) if pending else "預估公式已調整到期別")


def fixed_expense_mail_item(month: str) -> dict:
    from tools.common.config_loader import get_sheets_service
    from tools.finance_management import taipei_fixed_expense as tfe

    period = previous_month(month)
    plabel, mlabel = f"{period[:4]}.{period[4:]}", f"{month[:4]}.{month[4:]}"
    wanted = [f"{plabel}-Amazon Web Services", f"{plabel}-震旦行", f"{plabel}-眾點", f"{mlabel}-台灣連線"]
    try:
        spreadsheet_id, title = tfe.resolve_report_location(tfe.PAYMENT_REQUEST_TYPE, tfe.AREA)
        existing = tfe._existing_memos(get_sheets_service(), spreadsheet_id, title)
        missing = [m for m in wanted if not tfe._already_submitted(existing, m)]
    except Exception as exc:
        missing = [f"讀取請款記錄失敗：{str(exc)[:60]}"]
    return item("fixed_expense_mail", "台北固定費用查信（AWS／震旦／眾點／台灣連線）", month_day(month, 10),
                not missing, ("還沒新增：" + "、".join(missing)) if missing else "都已新增到請款記錄")


def fixed_expense_remit_item(month: str) -> dict:
    from tools.finance_management.fixed_expense_transfer import remit_status

    label = "各區固定費用匯款（台中／桃園／新竹／高雄／學院）"
    try:
        status = remit_status(month)
    except Exception as exc:
        return item("fixed_expense_remit", label, month_day(month, 10), False,
                    f"讀取失敗：{str(exc)[:80]}", runnable=False)
    todo = [x for x in status if not x["done"]]
    detail = ("待匯款：" + "、".join(f"{x['area']} {x['total']:,}" for x in todo)) if todo else \
        "、".join(f"{x['area']} {x['detail']}" for x in status)
    return item("fixed_expense_remit", label, month_day(month, 10), not todo, detail,
                plans=[{"area": x["area"]} for x in todo])


def taichung_billing_item(month: str) -> dict:
    from tools.finance_management.taichung_fixed_expense import taichung_status

    label = "台中固定費用請款（租金當月＋隔月、電費）"
    if int(month[4:]) % 2 == 0:
        return item("taichung_billing", label, month_day(month, 10), True, "雙月沒有", applicable=False)
    try:
        st = taichung_status(month)
        missing = st["missing"]
    except Exception as exc:
        missing = [f"讀取失敗：{str(exc)[:60]}"]
    return item("taichung_billing", label, month_day(month, 10), not missing,
                ("還沒新增：" + "、".join(missing)) if missing else "都已新增到請款記錄",
                runnable=False, page="【整合執行】各區固定費用匯款（每月10日）")


def cash_gap_item(month: str) -> dict:
    from tools.scheduled_finance.runner import cash_gap_missing

    try:
        missing = cash_gap_missing()
    except Exception as exc:
        missing = [f"讀取現金缺口試算失敗：{str(exc)[:60]}"]
    return item("cash_gap_numbers", "現金缺口各項數字（行銷／營業稅／藍新／銀行餘額）＋review 區塊",
                month_day(month, 10), not missing, "；".join(missing) or "各項都有數字")


def accounting_item(month: str) -> dict:
    if int(month[4:]) % 2 == 0:
        return item("accounting_fee", "會計事務所費用確認", month_day(month, 10), True, "雙月沒有", applicable=False)
    from tools.finance_management import accounting_fee_batch

    try:
        items = accounting_fee_batch.scan(month, log=lambda _t: None)
        pending = [i["memo"] for i in items if i.get("selectable") and i.get("default_hint", True)]
    except Exception as exc:
        pending = [f"讀取失敗：{str(exc)[:60]}"]
    return item("accounting_fee", "會計事務所費用確認（要填金額）", month_day(month, 10), not pending,
                ("還沒新增：" + "、".join(pending)) if pending else "都已新增", runnable=False,
                page="【整合執行】會計事務所費用確認")


def mof_item(month: str) -> dict:
    due = _earlier_workday(month_day(month, 20))
    if int(month[4:]) % 2 == 1:
        return item("mof_serial", "鯨躍字軌下載＋匯入＋配號（下一期）", due, True, "單月不用", applicable=False)
    from tools.scheduled_finance.runner import mof_status

    try:
        done, todo, period_text = mof_status(month_day(month, 20))
        detail = (f"{period_text} 未完成：" + "、".join(todo)) if todo else f"{period_text} 各區已配號"
    except Exception as exc:
        todo, detail = ["?"], f"讀取配號 Log 失敗：{str(exc)[:60]}"
    return item("mof_serial", "鯨躍字軌下載＋匯入＋配號（下一期）", due, not todo, detail, areas=todo)


CHECKS: list[tuple[str, Callable[[str], dict]]] = [
    ("deposit_report", deposit_item),
    ("review_monthly", review_item),
    ("fixed_expense_mail", fixed_expense_mail_item),
    ("fixed_expense_remit", fixed_expense_remit_item),
    ("taichung_billing", taichung_billing_item),
    ("cash_gap_numbers", cash_gap_item),
    ("accounting_fee", accounting_item),
    ("mof_serial", mof_item),
]


def collect(month: str, log: Callable[[str], None] = print) -> list[dict]:
    """月份 YYYYMM 的所有盤點項目（依應完成日排序）。"""
    items: list[dict] = []
    try:
        items += month_start_items(month, log)
    except Exception as exc:
        items.append(item("month_start", "月初工作（期別＝上個月）", month_day(month, 1), False,
                          f"月初盤點失敗：{str(exc)[:80]}", runnable=False))
    for key, check in CHECKS:
        try:
            items.append(check(month))
        except Exception as exc:
            items.append(item(key, key, month_day(month, 1), False, f"檢查失敗：{str(exc)[:80]}", runnable=False))
    return sorted(items, key=lambda i: (i["due"], i["key"].startswith("month_start") is False))


# ── 執行 ──────────────────────────────────────────────

def run_items(month: str, selected: list[dict], create_task, log: Callable[[str], None] = print,
              created_by: str = "月度盤點") -> list[str]:
    """執行勾選的項目：雲端能做的直接做，要 Mac 的排入本機 Agent。回傳結果訊息。"""
    messages: list[str] = []
    start_rows = [i["row"] for i in selected if i.get("group") == "month_start"]
    if start_rows:
        from tools.finance_management import month_start_check as chk

        messages += chk.run_rows(start_rows, month_day(month, 1), create_task, log=log, created_by=created_by)
    for entry in (i for i in selected if i.get("group") != "month_start"):
        try:
            messages.append(_run_one(month, entry, create_task, log, created_by))
        except Exception as exc:
            messages.append(f"❌ {entry['label']}：{exc}")
    return messages


def _run_one(month: str, entry: dict, create_task, log, created_by: str) -> str:
    key, label = entry["key"], entry["label"]
    if key == "deposit_report":
        from tools.finance_management.deposit_report import run_monthly

        problems = []
        for area in DEPOSIT_AREAS:
            problems += run_monthly(area, month_day(month, 1), log=log)
        return f"✅ {label}" + (f"（要確認：{'；'.join(problems)}）" if problems else "")
    if key == "review_monthly":
        from tools.finance_management.review_monthly import run_all

        failures = run_all(previous_month(month), log=log)
        return f"❌ {label}：{'；'.join(failures)}" if failures else f"✅ {label}"
    if key == "fixed_expense_mail":
        from tools.finance_management.taipei_fixed_expense import MAIL_ITEMS, submit_taipei_fixed_expenses

        result = submit_taipei_fixed_expenses(previous_month(month), run_type="月度盤點", only=set(MAIL_ITEMS))
        return (f"⚠️ {label}：{'；'.join(result['errors'])}" if result["errors"] else f"✅ {label}")
    if key == "fixed_expense_remit":
        plans = entry.get("plans") or []
        notes = []
        for plan in plans:
            if plan.get("self_remit"):
                from tools.finance_management.fixed_expense_transfer import transfer_fixed_expenses

                res = transfer_fixed_expenses(month, plan["area"])
                notes.append(f"{plan['area']} 台北分錄新增 {res['statement_rows']} 列")
                continue
            create_task("fubon.fixed_expense_transfer",
                        {"area": plan["area"], "month": month, "cdp_url": "http://127.0.0.1:9222"},
                        created_by=created_by)
        queued = sum(1 for p in plans if not p.get("self_remit"))
        return f"⏳ {label}：已排入本機 Agent {queued} 區，請用手機核准" + (f"；{'；'.join(notes)}" if notes else "")
    if key == "cash_gap_numbers":
        from tools.finance_management.review_monthly import paste_to_review
        from tools.scheduled_finance.runner import run_cash_gap, run_prepaid_amount

        first = month_day(month, 1)
        run_cash_gap(first)
        run_prepaid_amount(first)
        paste_to_review(previous_month(month), "all", log=log)
        return f"✅ {label}：已重算並更新 review（還缺的數字要等上游功能做完）"
    if key == "mof_serial":
        from tools.scheduled_finance.runner import mof_target

        period, qyear, qmonth = mof_target(month_day(month, 20))
        for area in entry.get("areas") or []:
            params = {"area": area, "cdp_url": "http://127.0.0.1:9222"}
            create_task("mofei.login", params, created_by=created_by)
            create_task("cetustek.full_pipeline", dict(params, month=period, qyear=qyear, qmonth=qmonth),
                        created_by=created_by)
        return f"⏳ {label}：已排入本機 Agent（{'、'.join(entry.get('areas') or [])}），請登入財政部"
    return f"— {label}：請到功能頁處理"


# ── 主控表「月度盤點」分頁 ─────────────────────────────

def _service_and_id():
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

    return get_sheets_service(), get_master_spreadsheet_id()


def _ensure_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    if SHEET_NAME in {s["properties"]["title"] for s in meta.get("sheets", [])}:
        return
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{"addSheet": {
        "properties": {"title": SHEET_NAME, "gridProperties": {"frozenRowCount": 1}}}}]}).execute()
    service.spreadsheets().values().update(spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A1",
                                           valueInputOption="RAW", body={"values": [HEADER]}).execute()


def merge_rows(existing: list[list], month: str, items: list[dict], now_text: str) -> tuple[list[list], list[list]]:
    """回傳 (要更新的 [列號, 列內容] 清單, 要新增的列)。只動同月份、程式產生的項目；手動列與備註不動。"""
    index = {(str(r[0]).strip(), str(r[1]).strip()): n for n, r in enumerate(existing[1:], start=2)
             if len(r) > 1 and str(r[1]).strip()}
    updates, appends = [], []
    for it in items:
        key = (month, it["key"])
        values = [month, it["key"], it["label"], it["due"].strftime("%Y-%m-%d"), it["status"], it["detail"], now_text]
        if key in index:
            old = existing[index[key] - 1] + [""] * len(HEADER)
            done_at = old[7] or (now_text if it["status"] == DONE else "")
            if it["status"] != DONE and old[4] != DONE:
                done_at = ""
            updates.append([index[key], values + [done_at, old[8]]])
        else:
            appends.append(values + [now_text if it["status"] == DONE else "", ""])
    return updates, appends


def save(month: str, items: list[dict], log: Callable[[str], None] = print) -> None:
    service, spreadsheet_id = _service_and_id()
    _ensure_sheet(service, spreadsheet_id)
    existing = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A:I"
                                                   ).execute().get("values", [])
    now_text = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M")
    updates, appends = merge_rows(existing or [HEADER], month, items, now_text)
    data = [{"range": f"'{SHEET_NAME}'!A{n}:I{n}", "values": [row]} for n, row in updates]
    if data:
        service.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id,
                                                    body={"valueInputOption": "RAW", "data": data}).execute()
    if appends:
        service.spreadsheets().values().append(spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A:I",
                                               valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                                               body={"values": appends}).execute()
    log(f"「{SHEET_NAME}」{month}：更新 {len(updates)} 列、新增 {len(appends)} 列")


def manual_rows(month: str) -> list[dict]:
    """自己加在分頁上的手動項目（項目代號空白）。"""
    service, spreadsheet_id = _service_and_id()
    values = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A:I"
                                                 ).execute().get("values", [])
    result = []
    for row in values[1:]:
        row = list(row) + [""] * len(HEADER)
        if str(row[0]).strip() == month and not str(row[1]).strip() and str(row[2]).strip():
            result.append({"label": row[2], "due": row[3], "status": row[4] or TODO, "detail": row[8]})
    return result


def overdue(items: list[dict], today: Optional[date] = None) -> list[dict]:
    today = today or datetime.now(TW_TZ).date()
    return [i for i in items if i["status"] in (TODO, MANUAL) and i["due"] <= today]


def scheduled_run(today: Optional[date] = None, log: Callable[[str], None] = print) -> int:
    """排程：盤點本月 → 寫分頁 → 過期沒做完的合併成一則提醒。"""
    today = today or datetime.now(TW_TZ).date()
    month = today.strftime("%Y%m")
    items = collect(month, log=log)
    for it in items:
        log(f"{it['status']}｜{it['label']}｜{it['due']:%m/%d}｜{it['detail']}")
    save(month, items, log=log)
    late = overdue(items, today)
    try:
        late_manual = [m for m in manual_rows(month) if m["status"] != DONE]
    except Exception:
        late_manual = []
    if not late and not late_manual:
        log("沒有過期未完成的項目")
        return 0
    from tools.orders_system import calendar_notify

    lines = [f"・{i['label']}（應完成 {i['due']:%m/%d}）：{i['detail']}" for i in late]
    lines += [f"・{m['label']}（手動項目，應完成 {m['due']}）" for m in late_manual]
    calendar_notify.create_notify_event(
        summary=f"{REMIND_PREFIX}{month[:4]}/{month[4:]} 還有 {len(lines)} 項沒做完",
        description="\n".join(lines) + "\n\n到 Tools App【整合執行】月度盤點 → 盤點 → 勾選 → 執行。",
        start=datetime.now(TW_TZ) + timedelta(minutes=30), calendar_id="jenny@hers.com.tw", attendee="",
        reminder_minutes=[20, 10], replace_prefix=REMIND_PREFIX)
    log(f"已建立提醒：{len(lines)} 項")
    return 0
