"""各系統統一執行 Log：寫到主控表「{系統}執行Log-{年度}」（每個系統每年一張、欄位相同）。

tool-system、orders-system、order-system、salary-system 各一張，例如「tool-system執行Log-2026」。
「系統」欄填系統內的子系統（日排程系統、財務管理、本機Agent…），「功能」欄填功能名稱。
新年度第一次寫入時自動建立工作表。原本各自的 Log 工作表照舊保留，這裡是另外同步一份。
寫入失敗只印警告，不影響原本流程。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
MASTER_SPREADSHEET_ID = "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
REPO = "tool-system"
HEADERS = ["執行時間", "系統", "功能", "執行方式", "區域", "期別/日期", "目標位置", "來源檔案", "結果", "訊息", "執行者"]
MESSAGE_LIMIT = 2000

_ensured: set[tuple[str, str]] = set()


def sheet_title(repo: str = REPO, year: int | None = None) -> str:
    return f"{repo}執行Log-{year or datetime.now(TW_TZ).year}"


def normalize_result(status: Any) -> str:
    text = str(status or "").strip()
    lower = text.lower()
    if lower in {"success", "ok", "0", "completed", "done", "成功", "完成", "✅ 成功"}:
        return "成功"
    if lower in {"fail", "failed", "error", "失敗", "錯誤"}:
        return "失敗"
    if lower in {"running", "start", "started", "執行中", "開始"}:
        return "執行中"
    if lower == "cancelled":
        return "已取消"
    return text


def _master_id() -> str:
    return os.getenv("MASTER_SPREADSHEET_ID", "").strip() or MASTER_SPREADSHEET_ID


def _ensure_sheet(service, spreadsheet_id: str, title: str) -> None:
    if (spreadsheet_id, title) in _ensured:
        return
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    titles = {sheet["properties"]["title"] for sheet in meta.get("sheets", [])}
    if title not in titles:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{"addSheet": {
            "properties": {"title": title, "gridProperties": {"rowCount": 1000, "columnCount": len(HEADERS),
                                                               "frozenRowCount": 1}}}}]}).execute()
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{title}'!A1", valueInputOption="RAW",
            body={"values": [HEADERS]}).execute()
    _ensured.add((spreadsheet_id, title))


def build_row(*, system: str = "", function: str = "", run_type: str = "", area: str = "", period: str = "",
              target: str = "", source_file: str = "", status: Any = "", message: str = "",
              operator: str = "") -> list[str]:
    return [
        datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        str(system or ""), str(function or ""), str(run_type or ""), str(area or ""), str(period or ""),
        str(target or ""), str(source_file or ""), normalize_result(status),
        str(message or "")[:MESSAGE_LIMIT], str(operator or ""),
    ]


def write(*, repo: str = REPO, service=None, spreadsheet_id: str = "", **fields: Any) -> None:
    """寫一筆統一 Log；欄位見 build_row。絕不拋出例外。"""
    try:
        if service is None:
            from tools.common.config_loader import get_sheets_service

            service = get_sheets_service()
        spreadsheet_id = spreadsheet_id or _master_id()
        title = sheet_title(repo)
        _ensure_sheet(service, spreadsheet_id, title)
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=f"'{title}'!A:K", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": [build_row(**fields)]}).execute()
    except Exception as exc:  # Log 寫不進去不能影響主流程
        print(f"[unified_log] 寫入統一執行 Log 失敗（不影響主流程）：{exc}", flush=True)


# ── 網頁執行紀錄：按下按鈕寫「執行」，之後第一個成功／錯誤訊息寫「成功」／「失敗」 ──
MAX_ERRORS_PER_CLICK = 3


def _ctx(st) -> dict[str, Any]:
    """目前功能存在各自的 session（多人同時使用不互相混到）。"""
    state = st.session_state
    if "_unified_log_ctx" not in state:
        state["_unified_log_ctx"] = {}
    return state["_unified_log_ctx"]


def set_context(st, **fields: Any) -> None:
    """更新目前功能的欄位（system／function／area／period／operator）。"""
    try:
        _ctx(st).update({k: v for k, v in fields.items() if v is not None})
    except Exception:
        pass


def report(st, level: str, message: Any) -> None:
    """按下按鈕後的結果：success → 成功（每次按鈕只記一筆），error → 失敗（最多 3 筆）。"""
    try:
        ctx = _ctx(st)
        if not ctx.get("clicked"):
            return
        if level == "success" and not ctx.get("succeeded"):
            ctx["succeeded"] = True
            status = "成功"
        elif level == "error" and ctx.get("errors", 0) < MAX_ERRORS_PER_CLICK:
            ctx["errors"] = ctx.get("errors", 0) + 1
            status = "失敗"
        else:
            return
        write(repo=ctx.get("repo", REPO), system=ctx.get("system", ""),
              function=f"{ctx.get('function', '')}／{ctx['clicked']}", run_type="手動", area=ctx.get("area", ""), period=ctx.get("period", ""), status=status,
              message=message, operator=ctx.get("operator", ""))
    except Exception:
        pass


def install_streamlit_hooks(st, *, repo: str = REPO, **fields: Any) -> None:
    """每次頁面重跑呼叫一次：記下目前功能，並包裝 st.button／st.success／st.error（只包一次）。"""
    try:
        ctx = _ctx(st)
        ctx.update(repo=repo, clicked="", succeeded=False, errors=0)
        ctx.update({k: v for k, v in fields.items() if v is not None})
    except Exception:
        return
    if getattr(st.button, "_unified_log", False):
        return
    original_button, original_success, original_error = st.button, st.success, st.error

    def button(label, *args, **kwargs):
        clicked = original_button(label, *args, **kwargs)
        if clicked:
            try:
                ctx = _ctx(st)
                ctx.update(clicked=str(label).strip(), succeeded=False, errors=0)
                write(repo=ctx.get("repo", REPO), system=ctx.get("system", ""),
                      function=f"{ctx.get('function', '')}／{ctx['clicked']}", run_type="手動",
                      area=ctx.get("area", ""), period=ctx.get("period", ""), status="執行",
                      operator=ctx.get("operator", ""))
            except Exception:
                pass
        return clicked

    def success(body, *args, **kwargs):
        report(st, "success", body)
        return original_success(body, *args, **kwargs)

    def error(body, *args, **kwargs):
        report(st, "error", body)
        return original_error(body, *args, **kwargs)

    button._unified_log = True
    st.button, st.success, st.error = button, success, error
