"""ATM 待付款清單同步（雲端版）。

取代原本 Mac launchd 的 orders-system ATM 本機排程（com.lemonclean.orders-system.atm-unpaid）：
台北／台中各自登入後台，查詢到今天為止的 ATM 待付款訂單，貼進當年度內勤工作表單「ATM」分頁。
查詢、去重、貼上沿用 orders-system 的 memo_system.atm.run_scheduled_unpaid_sync，不另寫一份。

時段由主控表「排程設定」的 atm_unpaid 決定（Cloud Schedule Router 派送），例假日由 Router 略過；
帳密讀 TAIPEI_／TAICHUNG_EMAIL／PASSWORD，Google 讀 GOOGLE_SERVICE_ACCOUNT。

tool-system 是 public repo，Actions 紀錄任何人都看得到：同步過程的逐筆訊息（客戶、訂單）不印出，
只印各區筆數；出錯時才印錯誤訊息。逐筆的查到／略過明細只寫進執行 Log 試算表（私人），方便追查。
"""
from __future__ import annotations

import contextlib
import io
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
TOOL_NAME = "ATM 待付款同步"


def summary_lines(result: dict) -> list[str]:
    lines = []
    for region, item in result.items():
        item = item or {}
        status = item.get("status_check") or {}
        lines.append(
            f"{region}：新增 {item.get('pasted', 0)} 筆、略過重複 {item.get('skipped_duplicates', 0)} 筆、"
            f"已取消標記 {status.get('flagged', 0)} 筆"
        )
    return lines


def _log(status: str, message: str) -> None:
    try:
        from tools.finance_management.execution_log import log_execution

        log_execution(TOOL_NAME, "台北／台中", status, message)
    except Exception:  # 記錄失敗不影響同步
        pass


def detail_for_log(detail_text: str, limit: int = 60) -> str:
    """從逐筆訊息挑出查到的訂單（「  - 」開頭）與略過／警告（⚠️），寫進私人的執行 Log。"""
    picked = [
        line.strip() for line in str(detail_text or "").splitlines()
        if line.startswith("  - ") or "⚠️" in line or "單張複查完成" in line
    ]
    if len(picked) > limit:
        picked = picked[:limit] + [f"…另有 {len(picked) - limit} 行"]
    return "\n".join(picked)


def run(today: str | None = None) -> int:
    from tools.memo_system import atm, memo

    # 一律正式站（比照清潔異動整合）：tools/orders_system/memo_system/env.py 是 ENV="dev"，
    # Actions 又沒有 Streamlit secrets 的 ENV，不指定會連到測試後台（backend-dev），
    # 2026-11-05 因此把正式站已付款的訂單當成待付款貼進 ATM 分頁。
    memo.set_env("prod")
    today = today or datetime.now(TZ).date().isoformat()
    detail = io.StringIO()
    try:
        with contextlib.redirect_stdout(detail):
            result = atm.run_scheduled_unpaid_sync(date_until=today)
    except Exception as exc:
        print(f"❌ {TOOL_NAME}失敗：{exc}")
        extra = detail_for_log(detail.getvalue())
        _log("失敗", str(exc) + (f"\n{extra}" if extra else ""))
        return 1
    lines = summary_lines(result)
    for line in lines:
        print(f"✅ {line}")
    extra = detail_for_log(detail.getvalue())
    _log("完成", "；".join(lines) + (f"\n{extra}" if extra else ""))
    return 0


if __name__ == "__main__":
    sys.exit(run())
