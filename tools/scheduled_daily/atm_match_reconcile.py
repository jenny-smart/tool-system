"""ATM 對帳 ② 配對銀行明細＋③ 更新系統對帳（雲端排程，每日 08:10）。

台北／台中各自：
  ② auto_match_bank_rows：用 M 欄末碼（後 5 碼）或 K 欄姓名比對 F 欄備註＋金額，配對後
     G 欄補公式、I／N／O 補下拉選單，N/O 空白時 2000 → 清潔／新訓費、其他 → 清潔／服務費用。
  ③ process_atm_rows：只處理 T 欄「已配對」（含多訂單加總）且 J 欄有訂單編號、M 欄不是取消訂單的列，
     按已付款＋開立發票＋發確認信。需確認／疑似拆單／非訂單收入一律留給人工。
     B 欄入帳日在 2026/11/06 之前的不處理；訂單本來就已付款的不再發確認信。
行事曆（jenny@hers.com.tw）：
  當日完成對帳的訂單記一則「【ATM對帳】今日完成」（只留紀錄，不跳提醒）；
  已付款但未開立發票的另記「【ATM對帳】未開立發票」（30 分鐘後、提前 20／10 分鐘跳提醒）。
時段由主控表「排程設定」的 atm_match_reconcile 決定（Cloud Schedule Router 派送）。

tool-system 是 public repo：逐筆訊息（客戶、訂單）不印進 Actions 紀錄，只印各區筆數；
明細寫進私人的執行 Log 試算表。
"""
from __future__ import annotations

import contextlib
import io
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOOL_NAME = "ATM 配對銀行明細＋更新系統對帳"
TZ = ZoneInfo("Asia/Taipei")
REMIND_CALENDAR_ID = "jenny@hers.com.tw"
DONE_PREFIX = "【ATM對帳】今日完成"
NO_INVOICE_PREFIX = "【ATM對帳】未開立發票"


def collect(result: dict, key: str) -> list[dict]:
    items = []
    for item in (result or {}).values():
        items.extend(((item or {}).get("reconcile") or {}).get(key) or [])
    return items


def _line(d: dict) -> str:
    text = f"{d.get('region', '')} 第{d.get('row')}列 {d.get('order_no', '')} {d.get('name', '')} ${d.get('amount', '')}"
    if d.get("invoice_no"):
        text += f"　發票 {d['invoice_no']}"
    if d.get("already_paid"):
        text += "　（原已付款，未發確認信）"
    elif d.get("mail_sent"):
        text += "　已發確認信"
    if d.get("reason"):
        text += f"　{d['reason']}"
    return text


def build_calendar_events(result: dict, now: datetime) -> list[dict]:
    done = collect(result, "completed")
    no_invoice = collect(result, "no_invoice")
    events = []
    if done:
        events.append(dict(
            summary=f"{DONE_PREFIX} {len(done)} 筆（{now:%m/%d}）",
            description="\n".join(_line(d) for d in done),
            start=now, reminder_minutes=[], replace_prefix=DONE_PREFIX,
        ))
    if no_invoice:
        events.append(dict(
            summary=f"{NO_INVOICE_PREFIX} {len(no_invoice)} 筆，請到後台開立",
            description="\n".join(_line(d) for d in no_invoice),
            start=now + timedelta(minutes=30), reminder_minutes=[20, 10], replace_prefix=NO_INVOICE_PREFIX,
        ))
    return events


def write_calendar(result: dict, now: datetime | None = None) -> list[str]:
    """寫行事曆；失敗只回報，不影響對帳結果。回傳要印出的訊息（不含個資）。"""
    now = now or datetime.now(TZ)
    messages = []
    events = build_calendar_events(result, now)
    if not events:
        return messages
    from tools.orders_system import calendar_notify

    for kwargs in events:
        title = kwargs["replace_prefix"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                calendar_notify.create_notify_event(calendar_id=REMIND_CALENDAR_ID, attendee="", **kwargs)
            messages.append(f"已記入行事曆：{title}")
        except Exception as exc:
            messages.append(f"⚠️ 記入行事曆失敗（{title}）：{exc}")
    return messages


def summary_lines(result: dict) -> list[str]:
    lines = []
    for region, item in (result or {}).items():
        item = item or {}
        m = item.get("match") or {}
        r = item.get("reconcile") or {}
        line = (
            f"{region}：② 配對成功 {m.get('success', 0)}、需確認 {m.get('confirm_required', 0)}、"
            f"未配對 {m.get('unmatched', 0)}、多筆候選 {m.get('ambiguous', 0)}；"
            f"③ 更新系統 {r.get('success', 0)}／{len(item.get('reconcile_rows') or [])} 列、"
            f"失敗 {r.get('failed', 0)}、略過 {r.get('skipped', 0)}"
        )
        if item.get("error"):
            line += "（發生錯誤）"
        lines.append(line)
    return lines


def _log(status: str, message: str) -> None:
    try:
        from tools.finance_management.execution_log import log_execution

        log_execution(TOOL_NAME, "台北／台中", status, message)
    except Exception:  # 記錄失敗不影響對帳
        pass


def detail_for_log(detail_text: str, limit: int = 80) -> str:
    keep = ("✅", "⚠️", "❌", "⏭", "=====")
    picked = [line.strip() for line in str(detail_text or "").splitlines() if any(k in line for k in keep)]
    if len(picked) > limit:
        picked = picked[:limit] + [f"…另有 {len(picked) - limit} 行"]
    return "\n".join(picked)


def run() -> int:
    from tools.memo_system import atm, memo

    # 一律正式站（比照 atm_unpaid）：Actions 沒有 Streamlit secrets 的 ENV，不指定會連到測試後台。
    memo.set_env("prod")
    detail = io.StringIO()
    error = None
    result = {}
    try:
        with contextlib.redirect_stdout(detail):
            result = atm.run_scheduled_match_and_reconcile()
    except Exception as exc:
        error = exc
        result = getattr(exc, "results", {}) or {}
    lines = summary_lines(result)
    lines += write_calendar(result)
    for line in lines:
        print(f"{'✅' if not error else '•'} {line}")
    extra = detail_for_log(detail.getvalue())
    if error:
        print(f"❌ {TOOL_NAME}失敗：{error}")
        _log("失敗", str(error) + (f"\n{extra}" if extra else ""))
        return 1
    failed = any(((item or {}).get("reconcile") or {}).get("failed") for item in result.values()) \
        or bool(collect(result, "no_invoice"))
    _log("有失敗" if failed else "完成", "；".join(lines) + (f"\n{extra}" if extra else ""))
    return 0


if __name__ == "__main__":
    sys.exit(run())
