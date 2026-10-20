# ============================================================
# unified_log.py
# 各系統統一執行 Log：寫到主控表（Jenny's Lemonhometools）
# 「orders-system執行Log-{年度}」，與 tool-system、order-system、salary-system
# 欄位相同，每個系統每年一張；新年度自動建表。寫入失敗只印警告，不影響原本流程。
#
# orders-system 原本沒有執行 Log，ordersapp 用 install_streamlit_hooks() 記錄：
#   按下功能按鈕 → 一筆「執行」；之後同一次執行出現錯誤訊息（st.error）→ 一筆「失敗」。
# ============================================================
from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
MASTER_SPREADSHEET_ID = "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
REPO = "orders-system"
HEADERS = ["執行時間", "系統", "功能", "執行方式", "區域", "期別/日期", "目標位置", "來源檔案", "結果", "訊息", "執行者"]

_ensured: set[str] = set()
_context: dict[str, str] = {"function": "", "operator": "", "clicked": ""}


def sheet_title(year: int | None = None) -> str:
    return f"{REPO}執行Log-{year or datetime.now(TW_TZ).year}"


def build_row(*, system: str = "訂單系統", function: str = "", run_type: str = "手動", area: str = "",
              period: str = "", target: str = "", source_file: str = "", status: str = "",
              message: Any = "", operator: str = "") -> list[str]:
    return [datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S"), system, str(function or ""), str(run_type or ""),
            str(area or ""), str(period or ""), str(target or ""), str(source_file or ""), str(status or ""),
            str(message or "")[:2000], str(operator or "")]


def _worksheet(spreadsheet):
    title = sheet_title()
    if title in _ensured:
        return spreadsheet.worksheet(title)
    import gspread

    try:
        ws = spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(HEADERS))
        ws.update(range_name="A1", values=[HEADERS])
        ws.freeze(rows=1)
    _ensured.add(title)
    return ws


def write(client=None, **fields: Any) -> None:
    """寫一筆統一 Log；欄位見 build_row。絕不拋出例外。"""
    try:
        if client is None:
            from orders import build_gsheet_client

            client = build_gsheet_client()
        spreadsheet = client.open_by_key(os.getenv("MASTER_SPREADSHEET_ID", "").strip() or MASTER_SPREADSHEET_ID)
        _worksheet(spreadsheet).append_row(build_row(**fields), value_input_option="RAW",
                                            insert_data_option="INSERT_ROWS")
    except Exception as exc:  # Log 寫不進去不能影響主流程
        print(f"[unified_log] 寫入統一執行 Log 失敗（不影響主流程）: {exc}")


def _ctx(st) -> dict[str, str]:
    """目前功能存在各自的 session（多人同時使用不互相混到）；沒有 session 時用模組變數。"""
    state = getattr(st, "session_state", None)
    if state is None:
        return _context
    if "_unified_log_ctx" not in state:
        state["_unified_log_ctx"] = {"function": "", "operator": "", "clicked": ""}
    return state["_unified_log_ctx"]


def install_streamlit_hooks(st, function_name: str, operator: str = "") -> None:
    """記錄目前功能；st.button 被按下時寫「執行」，之後 st.error 寫「失敗」。每次 rerun 呼叫一次。"""
    _ctx(st).update(function=function_name, operator=operator, clicked="")
    if getattr(st.button, "_unified_log", False):
        return
    original_button, original_error = st.button, st.error

    def button(label, *args, **kwargs):
        clicked = original_button(label, *args, **kwargs)
        if clicked:
            ctx = _ctx(st)
            ctx["clicked"] = str(label).strip()
            write(function=f"{ctx['function']}／{ctx['clicked']}", status="執行", operator=ctx["operator"])
        return clicked

    def error(body, *args, **kwargs):
        ctx = _ctx(st)
        if ctx["clicked"]:
            write(function=f"{ctx['function']}／{ctx['clicked']}", status="失敗", message=body,
                  operator=ctx["operator"])
        return original_error(body, *args, **kwargs)

    button._unified_log = True
    st.button, st.error = button, error
