"""【整合執行】月初（1日所有排程）：依「功能」盤點各地區的執行狀態，盤點完勾選沒完成的直接執行。

每一列是一個功能、每一欄是一個地區（台北／台中／桃園／新竹／高雄），格子：
  ✅ 完成　❌ 沒完成／失敗　⏳ 已排入本機 Agent、執行中　— 這區不用做
全部地區都完成，「完成」欄打 ✅ 完成。

功能與狀態來源（期別＝上個月，例如 11/1 做 202610）：
  已退款／預收／儲值金結算／儲值金預收 → 主控表「月排程執行Log」各地區最新結果（排程或手動都算）
  儲值金彙整（完整流程）             → 「儲值金月度作業紀錄」期別欄各地區的結算／預收搬運時間
  藍新收／退款                       → Drive 有沒有 {期別}-2藍新收款／退款-{地區}.csv
  藍新發票                           → 「藍新發票總表」期別各地區的發票金額（查無發票先算 ❌，確認後才 ✅）
  發票紙本／發票中獎（單月期別才有）／發票PDF（台中） → 「鯨躍發票更新Log」
  現金缺口                           → 「現金缺口試算」日期是期別月底、各地區沒有「錯誤」
  固定費用（台北每月、台中單月）      → 請款記錄
  富邦明細／元大明細                  → 各區財報最後一筆帳務日；台北／台中可排入 Mac 補下載，其他地區只顯示
執行（依序）：月排程（沒完成的地區逐區跑）→ 儲值金彙整 → 固定費用 → 紙本／中獎更新（讀 Drive，不用登入）
  → 排入本機 Agent：富邦／元大、台中紙本PDF、藍新（收退款＋發票，每區登入一次）、鯨躍、現金缺口
  （有排藍新時現金缺口跟在藍新後面算；沒排藍新就直接在這裡算）。
排程：每月 1 日 02:00～23:00 每小時用同一份盤點檢查，沒完成就在 Jenny 行事曆建提醒（只留最新一則，
  全部完成就刪掉）；固定費用、儲值金彙整沒做會自動補做（儲值金最多自動試 2 次）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
REMIND_CALENDAR_ID = "jenny@hers.com.tw"
LOG_ROOT = Path(__file__).resolve().parents[2] / "logs"
AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
PAGE = "Tools App【整合執行】月初（1日所有排程）→ 盤點 → 勾選 → 執行"

OK, FAIL, WAIT, NONE, NA = "✅", "❌", "⏳", "⚪", "—"
DONE_ICONS = {OK, NONE, NA}

MONTHLY_LOG_SHEET = "月排程執行Log"
VIP_LOG_SHEET = "儲值金月度作業紀錄"
NEWEBPAY_INVOICE_SHEET = "藍新發票總表"
EI_LOG_SHEET = "鯨躍發票更新Log"
CASH_GAP_SHEET = "現金缺口試算"
# 月排程：(列 key, 顯示名稱＝月排程 Log 的「功能」, scheduler target)；依這個順序執行
MONTHLY_JOBS = [("refund_report", "已退款", "refund_report"), ("prepaid_report", "預收", "prepaid_report"),
                ("stored_value_settlement", "儲值金結算", "stored_value_settlement"),
                ("stored_value_prepaid", "儲值金預收", "stored_value_prepaid")]
VIP_KEY, VIP_LABEL = "vip_full_pipeline", "儲值金彙整（完整流程）"
VIP_NO_SOURCE = 2  # runner.run_vip_full_pipeline：期別資料夾沒有結算／預收來源檔
# 月排程總根目錄（03 服務分潤表），跟 scheduled_monthly.yml 的 MONTHLY_ROOT_FOLDER_ID 相同
MONTHLY_ROOT_FOLDER_DEFAULT = "1t0B8BdUKBvaS6TM40-hpodUnxeZr3eWe"
PDF_AREAS = ("台中",)
STATEMENT_REPORTS = [("fubon", "富邦明細", "富邦更新", "fubon.download"),
                     ("yuanta", "元大明細", "元大更新", "yuanta.download")]
AGENT_STATEMENT_AREAS = ("台北", "台中")
# 要人工驗證碼、05:00 才排入的列：05:00 前排程不提醒
AGENT_ROW_KEYS = {"newebpay_files", "newebpay_invoice", "paper", "prize", "pdf"}
MONTH_START_QUEUE_HOUR = 5


# ── 讀資料 ────────────────────────────────────────────

def _read_master(sheet: str, rng: str) -> list[list]:
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

    return get_sheets_service().spreadsheets().values().get(
        spreadsheetId=get_master_spreadsheet_id(), range=f"'{sheet}'!{rng}").execute().get("values", [])


def _drive_names(name_contains: str) -> list[str]:
    from services.google_auth import get_drive_service

    files = get_drive_service().files().list(
        q=f"name contains '{name_contains}' and trashed=false", fields="files(name)", pageSize=200,
        supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    return [f["name"] for f in files]


def previous_period(day: date) -> str:
    return (day.replace(day=1) - timedelta(days=1)).strftime("%Y%m")


def period_end(period: str) -> date:
    from calendar import monthrange

    year, month = int(period[:4]), int(period[4:])
    return date(year, month, monthrange(year, month)[1])


# ── 各功能逐區狀態（純邏輯，方便測試） ─────────────────────

def _cell(row: list, index: int) -> str:
    return str(row[index]).strip() if row and index < len(row) else ""


def monthly_cells(values: list[list], label: str, period2: str) -> dict[str, tuple[str, str]]:
    """月排程執行Log（A 時間、C 功能、E 區域、F 期別、I 結果）：各地區最新一筆；只有 all 成功那筆也算。"""
    latest: dict[str, list] = {}
    for row in values[1:]:
        if _cell(row, 2) == label and _cell(row, 5) == period2:
            latest[_cell(row, 4)] = row
    cells = {}
    for area in AREAS:
        row = latest.get(area)
        if row is None and _cell(latest.get("all", []), 8) == "成功":
            row = latest["all"]
        if row is None:
            cells[area] = (FAIL, "沒有執行紀錄")
        elif _cell(row, 8) == "成功":
            cells[area] = (OK, _cell(row, 0))
        elif _cell(row, 8) == "執行中":
            cells[area] = (WAIT, "執行中")
        else:
            cells[area] = (FAIL, f"{_cell(row, 8)}：{_cell(row, 9)[:60]}")
    return cells


def vip_cells(values: list[list], period: str) -> dict[str, tuple[str, str]]:
    """儲值金月度作業紀錄：期別欄「{地區}儲值金結算搬運時間」「{地區}儲值金預收搬運時間」都有值＝完成。"""
    header = [str(v).strip() for v in (values[0] if values else [])]
    if period not in header:
        return {area: (FAIL, "還沒有這個期別的紀錄") for area in AREAS}
    col = header.index(period)
    rows = {_cell(r, 0): _cell(r, col) for r in values[1:] if r}
    cells = {}
    for area in AREAS:
        moved = [rows.get(f"{area}儲值金{kind}搬運時間", "") for kind in ("結算", "預收")]
        cells[area] = (OK, max(moved)) if all(moved) else (FAIL, f"搬運 {sum(1 for m in moved if m)}／2")
    return cells


def newebpay_file_cells(names: list[str], period: str) -> dict[str, tuple[str, str]]:
    cells = {}
    for area in AREAS:
        has = [f"{period}-2藍新{kind}-{area}.csv" in names for kind in ("收款", "退款")]
        cells[area] = (OK, "收款＋退款") if all(has) else (FAIL, "缺" + "、".join(
            k for k, h in zip(("收款", "退款"), has) if not h))
    return cells


def newebpay_invoice_cells(values: list[list], period: str) -> dict[str, tuple[str, str]]:
    """藍新發票總表（A 地區、B 月份、D 金額、F 狀態）：有金額 ✅；只有「查無發票」先算 ❌，
    要 Jenny 在盤點頁按「確認沒有發票」（寫成金額 0、狀態「確認無發票」）才算 ✅（2026-11 Jenny）。"""
    rows: dict[str, list] = {}
    for row in values[1:]:
        if _cell(row, 1) == period:
            area = _cell(row, 0)
            if _cell(row, 3) or area not in rows:
                rows[area] = row
    from tools.newebpay.month_close import NO_INVOICE_AREAS

    cells = {}
    for area in AREAS:
        row = rows.get(area)
        if area in NO_INVOICE_AREAS:
            cells[area] = (NA, "不用算藍新發票金額")
        elif row is None:
            cells[area] = (FAIL, "還沒有資料")
        elif _cell(row, 5) == CONFIRMED_NO_INVOICE:
            cells[area] = (OK, CONFIRMED_NO_INVOICE)
        elif _cell(row, 3):
            cells[area] = (OK, f"NT$ {_cell(row, 3)}")
        else:
            cells[area] = (FAIL, f"{NO_INVOICE_PREFIX}（{_cell(row, 5) or '查無發票'}），請確認")
    return cells


NO_INVOICE_PREFIX = "查無發票"
CONFIRMED_NO_INVOICE = "確認無發票"


def unconfirmed_no_invoice_areas(row: dict) -> list[str]:
    """藍新發票那一列，「查無發票、還沒確認」的地區（盤點頁要顯示確認按鈕）。"""
    return [a for a in row["todo"] if row["cells"][a][1].startswith(NO_INVOICE_PREFIX)]


def confirm_no_invoice(area: str, period: str, values: Optional[list[list]] = None, write=None) -> int:
    """Jenny 確認某區某期別沒有藍新手續費發票：「藍新發票總表」那一列寫金額 0、狀態「確認無發票」。
    現金缺口的藍新發票金額本來就算 0，不受影響。回傳更新的列號。"""
    if values is None:
        values = _read_master(NEWEBPAY_INVOICE_SHEET, "A:F")
    target = None
    for index, row in enumerate(values[1:], start=2):
        if _cell(row, 0) == area and _cell(row, 1) == period and not _cell(row, 3):
            target = index
    if target is None:
        raise RuntimeError(f"藍新發票總表找不到 {area} {period} 的查無發票列")
    if write is None:
        from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

        def write(rng, vals):
            get_sheets_service().spreadsheets().values().update(
                spreadsheetId=get_master_spreadsheet_id(), range=f"'{NEWEBPAY_INVOICE_SHEET}'!{rng}",
                valueInputOption="USER_ENTERED", body={"values": vals}).execute()
    write(f"D{target}", [[0]])
    write(f"F{target}", [[CONFIRMED_NO_INVOICE]])
    return target


def ei_log_cells(values: list[list], function: str, period: str, areas=tuple(AREAS)) -> dict[str, tuple[str, str]]:
    """鯨躍發票更新Log（A 時間、B 功能、C 地區、D 期別、E 狀態、F 筆數、G 訊息）：各地區最新一筆。"""
    latest: dict[str, list] = {}
    for row in values[1:]:
        if _cell(row, 1) == function and _cell(row, 3) == period:
            latest[_cell(row, 2)] = row
    cells = {}
    for area in AREAS:
        if area not in areas:
            cells[area] = (NA, "")
            continue
        row = latest.get(area)
        if row is None:
            cells[area] = (FAIL, "沒有執行紀錄")
        elif _cell(row, 4) == "成功":
            cells[area] = (OK, f"{_cell(row, 5) or 0} 筆")
        else:
            cells[area] = (FAIL, f"{_cell(row, 4)}：{_cell(row, 6)[:60]}")
    return cells


def cash_gap_cells(values: list[list], period: str) -> dict[str, tuple[str, str]]:
    """現金缺口試算（第 1 列地區、第 2 列日期）：日期是期別月底、該區沒有「錯誤」才算完成。"""
    header = [str(v).strip() for v in (values[0] if values else [])]
    dates = values[1] if len(values) > 1 else []
    want = period_end(period).strftime("%Y/%m/%d")
    cells = {}
    for area in AREAS:
        if area not in header:
            cells[area] = (FAIL, "還沒有資料")
            continue
        col = header.index(area)
        if _cell(dates, col).replace("-", "/") != want:
            cells[area] = (FAIL, f"還沒算到 {want}")
            continue
        errors = [_cell(r, 0) for r in values[2:] if _cell(r, col).startswith("錯誤")]
        cells[area] = (FAIL, "出錯：" + "、".join(errors)) if errors else (OK, want)
    return cells


def _agent_wait(cells: dict, waiting: bool) -> dict:
    """已排入本機 Agent、還在跑的：沒完成的格子改成 ⏳。"""
    if not waiting:
        return cells
    return {a: ((WAIT, "已排入本機 Agent") if icon == FAIL else (icon, text)) for a, (icon, text) in cells.items()}


def make_row(key: str, label: str, cells: dict, action: str, required=tuple(AREAS), note: str = "",
             remind: bool = True, extra: Optional[dict] = None) -> dict:
    todo = [a for a in AREAS if a in required and cells.get(a, (NA, ""))[0] not in DONE_ICONS]
    waiting = any(cells.get(a, (NA, ""))[0] == WAIT for a in todo)
    row = {"key": key, "label": label, "cells": cells, "action": action, "todo": todo,
           "done": not todo, "runnable": bool(todo) and not waiting and bool(action), "remind": remind,
           "note": note}
    row.update(extra or {})
    return row


def summary_text(row: dict) -> str:
    return "完成" if row["done"] else "未完成：" + "、".join(f"{a}{row['cells'][a][0]}" for a in row["todo"])


# ── 盤點 ──────────────────────────────────────────────

def _error_cells(text: str) -> dict:
    return {area: (FAIL, text) for area in AREAS}


def collect_rows(day: date, log: Callable[[str], None] = print, readers: Optional[dict] = None) -> list[dict]:
    """day＝那個月的 1 日（期別＝上個月）。readers 可替換讀資料的函式（測試用）。"""
    from tools.finance_management import month_start_batch as msb
    from tools.invoice_center.invoice_archive import previous_prize_period

    readers = readers or {}
    period = previous_period(day)

    def read(name: str, default):
        try:
            return readers.get(name, default)(), ""
        except Exception as exc:
            return None, f"讀取{name}失敗：{str(exc)[:120]}"

    rows: list[dict] = []
    monthly, err = read("月排程執行Log", lambda: _read_master(MONTHLY_LOG_SHEET, "A:J"))
    for key, label, _target in MONTHLY_JOBS:
        cells = monthly_cells(monthly, label, f"{period}-2") if monthly is not None else _error_cells(err)
        rows.append(make_row(key, label, cells, "monthly"))
    vip, err = read("儲值金月度作業紀錄", lambda: _read_master(VIP_LOG_SHEET, "A1:ZZ120"))
    rows.append(make_row(VIP_KEY, VIP_LABEL, vip_cells(vip, period) if vip is not None else _error_cells(err), "vip"))

    def list_agent_tasks():
        from tools.local_agent_queue import list_tasks

        return list_tasks(limit=500)

    tasks, _err = read("本機Agent任務", list_agent_tasks)

    def waiting(step: str) -> bool:
        task = msb.latest_task(tasks or [], step, period)
        return bool(task and task.get("status") in msb.ACTIVE)

    names, err = read("藍新收退款檔", lambda: _drive_names(f"{period}-2藍新"))
    cells = newebpay_file_cells(names, period) if names is not None else _error_cells(err)
    rows.append(make_row("newebpay_files", "藍新收／退款", _agent_wait(cells, waiting("newebpay")), "agent"))
    invoice, err = read("藍新發票總表", lambda: _read_master(NEWEBPAY_INVOICE_SHEET, "A:F"))
    cells = newebpay_invoice_cells(invoice, period) if invoice is not None else _error_cells(err)
    rows.append(make_row("newebpay_invoice", "藍新發票", _agent_wait(cells, waiting("newebpay")), "agent"))

    ei_log, err = read("鯨躍發票更新Log", lambda: _read_master(EI_LOG_SHEET, "A:G"))
    cetustek_wait = waiting("cetustek_download")
    cells = ei_log_cells(ei_log, "紙本發票更新", period) if ei_log is not None else _error_cells(err)
    rows.append(make_row("paper", "發票紙本", _agent_wait(cells, cetustek_wait), "invoice_update"))
    prize = previous_prize_period(period)
    if prize:
        cells = ei_log_cells(ei_log, "中獎發票更新", prize) if ei_log is not None else _error_cells(err)
        rows.append(make_row("prize", f"發票中獎（{prize[4:6]}-{prize[6:]}）", _agent_wait(cells, cetustek_wait),
                             "invoice_update", extra={"prize_period": prize}))
    else:
        rows.append(make_row("prize", "發票中獎", {a: (NA, "雙月沒有") for a in AREAS}, "",
                             note=f"{period} 是雙月，沒有單月中獎發票"))
    cells = ei_log_cells(ei_log, "紙本發票PDF建檔", period, PDF_AREAS) if ei_log is not None else _error_cells(err)
    rows.append(make_row("pdf", "發票PDF（台中有統編）", _agent_wait(cells, cetustek_wait), "pdf", required=PDF_AREAS))

    cash, err = read("現金缺口試算", lambda: _read_master(CASH_GAP_SHEET, "A1:G16"))
    cells = cash_gap_cells(cash, period) if cash is not None else _error_cells(err)
    rows.append(make_row("cash_gap", "現金缺口", _agent_wait(cells, waiting("cash_gap")), "cash_gap"))

    rows.append(fixed_expense_row(day, log, readers))
    rows += statement_rows(day, readers, tasks=tasks or [])
    ignore_values, err = read("月初檢查不提醒", lambda: _read_master(IGNORE_SHEET, "A:D"))
    if err and "Unable to parse range" not in err:
        log(f"⚠️ {err}")
    return apply_ignored(rows, read_ignored(ignore_values or []))


# ── 不提醒：有些功能 Jenny 自己沒辦法完成（例如其他地區的富邦／元大明細），盤點照樣顯示 ❌，
#    但行事曆、1 日每小時檢查不再提醒（2026-11 Jenny）。設定存在主控表「月初檢查不提醒」，每個月都適用。
IGNORE_SHEET = "月初檢查不提醒"
IGNORE_HEADER = ["功能代碼", "功能", "地區", "設定時間"]


def read_ignored(values: list[list]) -> set[tuple[str, str]]:
    return {(_cell(r, 0), _cell(r, 2)) for r in values[1:] if _cell(r, 0) and _cell(r, 2)}


def apply_ignored(rows: list[dict], ignored: set[tuple[str, str]]) -> list[dict]:
    """每列加上 ignored（設定不提醒、還沒完成的地區）與 remind_todo（還要提醒的地區）。"""
    for row in rows:
        row["ignored"] = [a for a in row["todo"] if (row["key"], a) in ignored]
        row["remind_todo"] = [a for a in row["todo"] if a not in row["ignored"]]
    return rows


def ignore_options(rows: list[dict]) -> list[tuple[str, str, str]]:
    """可以設定不提醒的 (功能代碼, 功能, 地區)：這個功能要做的地區（— 的不列）。"""
    return [(r["key"], r["label"], a) for r in rows for a in AREAS if r["cells"].get(a, (NA, ""))[0] != NA]


def save_ignored(items: list[tuple[str, str, str]], write=None) -> int:
    """整份覆寫「月初檢查不提醒」：items＝[(功能代碼, 功能, 地區)]。分頁不存在會自動建。"""
    now = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M")
    values = [IGNORE_HEADER] + [[k, label, area, now] for k, label, area in items]
    if write is None:
        from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

        service, sid = get_sheets_service(), get_master_spreadsheet_id()
        titles = [s["properties"]["title"] for s in service.spreadsheets().get(
            spreadsheetId=sid, fields="sheets.properties.title").execute().get("sheets", [])]
        if IGNORE_SHEET not in titles:
            service.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
                {"addSheet": {"properties": {"title": IGNORE_SHEET}}}]}).execute()
        service.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{IGNORE_SHEET}'!A:D").execute()

        def write(vals):
            service.spreadsheets().values().update(
                spreadsheetId=sid, range=f"'{IGNORE_SHEET}'!A1", valueInputOption="RAW",
                body={"values": vals}).execute()
    write(values)
    return len(items)


def fixed_expense_row(day: date, log, readers: dict) -> dict:
    from tools.finance_management import fixed_expense_batch

    try:
        items = readers.get("固定費用", lambda: fixed_expense_batch.scan(day.strftime("%Y%m"), log=log))()
    except Exception as exc:
        cells = {a: (FAIL, f"讀取請款記錄失敗：{str(exc)[:80]}") if a in ("台北", "台中") else (NA, "") for a in AREAS}
        return make_row("fixed", "固定費用（辦公室租金）", cells, "", required=("台北", "台中"))
    cells = {}
    for area in AREAS:
        mine = [i for i in items if i["area"] == area and not i.get("info")]
        if not mine:
            info = [i for i in items if i["area"] == area and i.get("info")]
            cells[area] = (NA, info[0]["info"] if info else "")
            continue
        todo = [i for i in mine if i.get("default")]
        cells[area] = (FAIL, "未新增：" + "、".join(i["memo"] for i in todo)) if todo else (OK, f"{len(mine)} 筆已新增")
    return make_row("fixed", "固定費用（辦公室租金）", cells, "fixed", required=("台北", "台中"),
                    extra={"items": [i for i in items if i.get("default") and not i.get("info")]})


def last_workday_before(day: date) -> date:
    """day（某月 1 日）的前一個工作天＝上個月最後一個工作天。"""
    from tools.common.tw_holidays import is_workday

    current = day - timedelta(days=1)
    while not is_workday(current):
        current -= timedelta(days=1)
    return current


def last_statement_date(values: list[list]) -> Optional[date]:
    from tools.finance_management.cash_gap import COL_B, _cell as cg_cell, _parse_date

    dates = [d for row in values[1:] if (d := _parse_date(cg_cell(row, COL_B)))]
    return max(dates) if dates else None


def downloaded_through(tasks: list[dict], action: str, area: str) -> Optional[date]:
    """本機 Agent 已完成的明細下載任務（富邦／元大），這區下載到哪一天（params end_date 最大值）。"""
    best = None
    for task in tasks:
        if task.get("action") != action or task.get("status") != "completed":
            continue
        try:
            params = json.loads(task.get("params_json") or "{}")
            end = date.fromisoformat(str(params.get("end_date") or ""))
        except (ValueError, TypeError):
            continue
        if params.get("area") == area and (best is None or end > best):
            best = end
    return best


def statement_rows(day: date, readers: Optional[dict] = None, target: Optional[date] = None,
                   tasks: Optional[list[dict]] = None) -> list[dict]:
    """富邦／元大明細：已更新到月底才算完成。兩種情況都算：
      ① 財報最後一筆帳務日已到上個月最後一個工作天；
      ② 本機 Agent 已完成一次下載、而且下載到上個月月底以後（月底那幾天沒交易的帳戶，最後一筆本來就比較早，
         例如台中元大最後一筆 10/21，2026-11 Jenny 回報更新完仍是 ❌）。
    台北／台中可補做、會提醒；其他地區由各區處理，只看 ①。"""
    from tools.finance_management.cash_gap import _report_values

    read_values = (readers or {}).get("財報", _report_values)
    target = target or last_workday_before(day)
    month_end = day - timedelta(days=1)
    rows = []
    for key, label, report, action in STATEMENT_REPORTS:
        cells = {}
        for area in AREAS:
            try:
                last = last_statement_date(read_values(area, report))
                text = f"最後一筆 {last:%m/%d}" if last else "沒有資料"
                through = downloaded_through(tasks or [], action, area)
                if last and last >= target:
                    cells[area] = (OK, text)
                elif through and through >= month_end:
                    cells[area] = (OK, f"{text}（已下載到 {through:%m/%d}，之後沒有交易）")
                else:
                    cells[area] = (FAIL, f"{text}（要到 {target:%m/%d}，或下載到 {month_end:%m/%d} 以後）")
            except Exception as exc:
                from tools.finance_management.cash_gap import is_missing_account

                cells[area] = (NA, "沒有這個帳戶") if is_missing_account(exc, report) else \
                    (FAIL, f"讀取失敗：{str(exc)[:60]}")
        rows.append(make_row(key, label, cells, "statement", required=AGENT_STATEMENT_AREAS,
                             note="桃園／新竹／高雄由各區財務更新，只顯示、不提醒",
                             extra={"agent_action": action}))
    return rows


# ── 執行 ──────────────────────────────────────────────

def _run_monthly_here(target: str, period2: str, area: str, log: Callable[[str], None]) -> None:
    """在 Tools App 直接跑月排程（同月排程系統，失敗會丟例外）。"""
    import os

    from tools.scheduled_monthly.scheduler import main as run_monthly

    folder_id = os.getenv("MONTHLY_ROOT_FOLDER_ID", "").strip() or MONTHLY_ROOT_FOLDER_DEFAULT
    log(f"===== 月排程：{target}／{area}（{period2}） =====")
    run_monthly(target=target, folder_id=folder_id, area=area, period=period2)


NEWEBPAY_PART_LABELS = {"all": "藍新收退款＋發票", "download": "藍新收退款", "invoice": "藍新發票"}


def newebpay_plan(by_key: dict) -> list[tuple[str, list[str]]]:
    """勾了「藍新收／退款」「藍新發票」哪幾列 → [(part, 地區)]。同一區兩邊都要做就一起做（只登入一次）。
    高雄跟新竹同一個帳號：高雄收退款改做新竹（會複製一份）。"""
    from tools.newebpay.month_close import COPY_AREAS

    alias = {copy: source for source, copies in COPY_AREAS.items() for copy in copies}
    need: dict[str, set] = {}
    for key, part in (("newebpay_files", "download"), ("newebpay_invoice", "invoice")):
        for area in by_key.get(key, {}).get("todo", []):
            need.setdefault(alias.get(area, area), set()).add(part)
    groups: dict[str, list[str]] = {}
    for area in sorted(need, key=AREAS.index):
        part = "all" if len(need[area]) == 2 else next(iter(need[area]))
        groups.setdefault(part, []).append(area)
    return [(part, groups[part]) for part in ("all", "download", "invoice") if part in groups]


def _run_vip_here(day: date, log: Callable[[str], None]) -> int:
    from tools.scheduled_finance import runner

    runner.log = log  # 進度直接顯示在 Tools App 執行日誌
    return runner.run_vip_full_pipeline(day)


def _update_invoice_here(kind: str, area: str, period: str) -> int:
    from tools.invoice_center.invoice_archive import InvoiceArchiveProcessor

    processor = InvoiceArchiveProcessor()
    name = "紙本發票更新" if kind == "paper" else "中獎發票更新"
    try:
        count = processor.update_paper(area=area, yyyymm=period) if kind == "paper" else \
            processor.update_prize_period(area=area, period8=period)
    except Exception as exc:
        processor.write_update_log(function_name=name, area=area, period=period, status="失敗", message=str(exc))
        raise
    processor.write_update_log(function_name=name, area=area, period=period, status="成功", count=count)
    return count


def run_rows(rows: list[dict], day: date, create_task, log: Callable[[str], None] = print,
             created_by: str = "月初整合執行", runners: Optional[dict] = None) -> list[str]:
    """執行勾選的列（只做沒完成的地區）。runners 可替換執行函式（測試用）。回傳結果訊息。"""
    from tools.finance_management import month_start_batch as msb

    runners = runners or {}
    run_monthly = runners.get("monthly", _run_monthly_here)
    run_vip = runners.get("vip", _run_vip_here)
    update_invoice = runners.get("invoice_update", _update_invoice_here)
    period = previous_period(day)
    by_key = {r["key"]: r for r in rows if r.get("runnable")}
    messages: list[str] = []
    steps: list[str] = []

    for key, label, target in MONTHLY_JOBS:  # 已退款 → 預收 → 儲值金結算 → 儲值金預收
        for area in by_key.get(key, {}).get("todo", []):
            try:
                run_monthly(target, f"{period}-2", area, log)
                messages.append(f"✅ {label}／{area}：完成")
            except Exception as exc:
                messages.append(f"❌ {label}／{area}：{str(exc)[:200]}")
    if VIP_KEY in by_key:
        try:
            code = run_vip(day, log)
            if code == VIP_NO_SOURCE:
                # 期別資料夾沒有結算／預收檔（例如舊程式存到別的資料夾）：重跑兩個月排程再彙整一次
                messages.append(f"ℹ️ {VIP_LABEL}：找不到儲值金結算／預收檔，先重跑儲值金結算、儲值金預收")
                for target, label in (("stored_value_settlement", "儲值金結算"), ("stored_value_prepaid", "儲值金預收")):
                    try:
                        run_monthly(target, f"{period}-2", "all", log)
                        messages.append(f"✅ {label}（重跑）：完成")
                    except Exception as exc:
                        messages.append(f"❌ {label}（重跑）：{str(exc)[:200]}")
                code = run_vip(day, log)
            messages.append(f"✅ {VIP_LABEL}：完成" if code == 0 else f"❌ {VIP_LABEL}：失敗，請看執行日誌")
        except Exception as exc:
            messages.append(f"❌ {VIP_LABEL}：{exc}")
    if "fixed" in by_key:
        from tools.finance_management.fixed_expense_batch import submit

        try:
            added = runners.get("fixed", submit)(by_key["fixed"]["items"], run_type="月初整合執行", log=log)
            messages.append("✅ 固定費用：" + "、".join(f"{a} 新增 {n} 筆" for a, n in added.items()))
        except Exception as exc:
            messages.append(f"❌ 固定費用：{exc}")
    for key in ("paper", "prize"):
        row = by_key.get(key)
        for area in (row or {}).get("todo", []):
            try:
                count = update_invoice(key, area, row.get("prize_period", period))
                messages.append(f"✅ {row['label']}／{area}：{count} 筆")
            except Exception as exc:
                if "找不到檔案" in str(exc):  # Drive 還沒有鯨躍檔：排入鯨躍下載（下載完會自動更新）
                    if "cetustek_download" not in steps:
                        steps.append("cetustek_download")
                    messages.append(f"ℹ️ {row['label']}／{area}：Drive 還沒有鯨躍檔，改排入鯨躍下載")
                else:
                    messages.append(f"❌ {row['label']}／{area}：{str(exc)[:200]}")
    for key in ("fubon", "yuanta"):
        row = by_key.get(key)
        for area in (row or {}).get("todo", []):
            params = {"start_from": "sheet", "end_date": datetime.now(TW_TZ).strftime("%Y-%m-%d"), "area": area,
                      "cdp_url": "http://127.0.0.1:9222"}
            if key == "fubon":
                params["resume"] = "download"
            task = create_task(row["agent_action"], params, created_by=created_by)
            messages.append(f"✅ {row['label']}／{area}：已排入本機 Agent（{task.get('task_id', '')}）")
    for area in by_key.get("pdf", {}).get("todo", []):
        task = create_task("cetustek.paper_invoice_pdf", {"area": area, "month": period, "upload": True,
                                                          "cdp_url": "http://127.0.0.1:9222"}, created_by=created_by)
        messages.append(f"✅ 發票PDF／{area}：已排入本機 Agent（{task.get('task_id', '')}），要輸入鯨躍驗證碼")
    newebpay_queued = False
    for part, areas in newebpay_plan(by_key):
        # 勾哪一邊就只跑哪一邊（兩個都勾＝同一次登入一起做）；只跑沒完成的地區，每區登入一次
        task = create_task("newebpay.month_close", {"period": period, "areas": areas, "part": part,
                                                    "cdp_url": "http://127.0.0.1:9222"}, created_by=created_by)
        messages.append(f"✅ {NEWEBPAY_PART_LABELS[part]}／{'、'.join(areas)}：已排入本機 Agent（{task.get('task_id', '')}）")
        newebpay_queued = True
    if "cash_gap" in by_key:
        if newebpay_queued and "cash_gap" not in steps:
            steps.append("cash_gap")  # 本機 Agent 依序執行：藍新做完才算現金缺口
        else:
            code = runners.get("cash_gap", lambda p: msb._run_step("cash_gap", p, ""))(period)
            messages.append("✅ 現金缺口：已重新試算" if code == 0 else "❌ 現金缺口：試算失敗")
    if steps:
        task = msb.queue_steps(period, steps, create_task, created_by=created_by, log=log)
        labels = "→".join(msb.STEPS[s][0] for s in msb.STEP_ORDER if s in steps)
        messages.append(f"✅ {labels}：已排入本機 Agent（{(task or {}).get('task_id', '')}），要到 Mac 輸入驗證碼")
    for message in messages:
        log(message)
    return messages


# ── 排程：提醒＋自動補做 ───────────────────────────────

REMINDER_PREFIX = "月初工作未完成"
AUTO_MAX_ATTEMPTS = 2  # 儲值金彙整自動補做最多試 2 次，避免每小時一直失敗重跑


def problems(rows: list[dict], hour: int = 23) -> list[dict]:
    """排程要提醒的列：沒完成、會提醒；05:00 前不看藍新／鯨躍（還沒排入）。"""
    return [r for r in rows if not r["done"] and r["remind"] and r.get("remind_todo", r["todo"])
            and (hour >= MONTH_START_QUEUE_HOUR or r["key"] not in AGENT_ROW_KEYS)]


def build_reminder(day: date, now: datetime, todo: list[dict]) -> tuple[str, str]:
    lines = [f"{now:%Y/%m/%d %H:%M} 檢查，{len(todo)} 個功能還沒完成：", ""]
    lines += [f"・{r['label']}：未完成：" + "、".join(f"{a}{r['cells'][a][0]}" for a in r.get("remind_todo", r["todo"]))
              for r in todo]
    lines += ["", f"處理方式：{PAGE}", "",
              "1 日每小時會再檢查一次（到 23:00），行事曆只留最新一則；全部完成後提醒會自動刪除。"]
    return f"{REMINDER_PREFIX} {len(todo)} 項（{day:%m/%d}）", "\n".join(lines)


def clear_reminders(day: date, log: Callable[[str], None] = print) -> int:
    """刪掉 day 當天之前建的「月初工作未完成」提醒（只留最新一則／全部完成就全刪）。"""
    from tools.orders_system import calendar_notify

    service, calendar_id = calendar_notify.calendar_for(REMIND_CALENDAR_ID)
    start = datetime.combine(day, datetime.min.time(), tzinfo=TW_TZ)
    events = service.events().list(calendarId=calendar_id, timeMin=start.isoformat(),
                                   timeMax=(start + timedelta(days=2)).isoformat(), q=REMINDER_PREFIX,
                                   singleEvents=True, maxResults=100).execute().get("items", [])
    removed = 0
    for event in events:
        if str(event.get("summary", "")).startswith(REMINDER_PREFIX):
            service.events().delete(calendarId=calendar_id, eventId=event["id"]).execute()
            removed += 1
    if removed:
        log(f"已刪除 {removed} 則之前的月初提醒")
    return removed


def _attempts_path(day: date) -> Path:
    return LOG_ROOT / day.strftime("%Y%m%d") / "finance_vip_full_pipeline.json"


def read_attempts(day: date) -> int:
    try:
        return int(json.loads(_attempts_path(day).read_text(encoding="utf-8")).get("auto_attempts", 0) or 0)
    except (OSError, ValueError, AttributeError):
        return 0


def write_attempts(day: date, status: str, attempts: int) -> None:
    """寫進 logs/YYYYMMDD/（workflow 會 commit），下一次檢查才知道已經自動試過幾次。"""
    path = _attempts_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    now_text = datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps({"job_name": "finance_vip_full_pipeline", "status": status,
                                "exit_code": 0 if status == "success" else 1, "finished_at": now_text,
                                "auto_attempts": attempts}, ensure_ascii=False), encoding="utf-8")


def auto_fix(day: date, rows: list[dict], log: Callable[[str], None] = print, run_vip=None, submit_fixed=None) -> list[str]:
    """不用人工的直接補做：固定費用直接新增；儲值金結算／預收都完成但儲值金彙整沒完成 → 直接跑（最多 2 次）。"""
    done = []
    by_key = {r["key"]: r for r in rows}
    fixed = by_key.get("fixed")
    if fixed and fixed.get("runnable") and fixed.get("items"):
        if submit_fixed is None:
            from tools.finance_management.fixed_expense_batch import submit as submit_fixed
        try:
            added = submit_fixed(fixed["items"], run_type="月初檢查自動補做", log=log)
            done.append("固定費用：" + "、".join(f"{a} 新增 {n} 筆" for a, n in added.items()))
        except Exception as exc:
            log(f"❌ 固定費用自動補做失敗：{exc}")
    vip = by_key.get(VIP_KEY)
    sources_ok = all(by_key.get(k, {}).get("done") for k in ("stored_value_settlement", "stored_value_prepaid"))
    if vip and not vip["done"] and sources_ok:
        attempts = read_attempts(day)
        if attempts >= AUTO_MAX_ATTEMPTS:
            log(f"儲值金彙整已自動補做 {attempts} 次都失敗，改提醒人工處理")
        else:
            if run_vip is None:
                from tools.scheduled_finance.runner import run_vip_full_pipeline as run_vip
            log("===== 自動補做：儲值金彙整（完整流程） =====")
            try:
                code = run_vip(day)
            except Exception as exc:
                log(f"❌ 儲值金彙整失敗：{exc}")
                code = 1
            write_attempts(day, "success" if code == 0 else "failed", attempts + 1)
            done.append("儲值金彙整：" + ("完成" if code == 0 else "失敗"))
    return done


def check(now: Optional[datetime] = None, log: Callable[[str], None] = print) -> list[dict]:
    now = now or datetime.now(TW_TZ)
    fixed_up = auto_fix(now.date(), collect_rows(now.date(), log=log), log=log)
    if fixed_up:
        log("自動補做：" + "；".join(fixed_up))
    todo = problems(collect_rows(now.date(), log=log), now.hour)
    try:
        clear_reminders(now.date(), log=log)
    except Exception as exc:
        log(f"⚠️ 刪除之前的月初提醒失敗：{exc}")
    if not todo:
        log("月初工作全部完成，不建提醒 🎉")
        return []
    title, body = build_reminder(now.date(), now, todo)
    log(title + "\n" + body)
    from tools.orders_system import calendar_notify

    # 開始時間留 30 分鐘前導，Google 的提醒推播才排得進去（20、10 分鐘前各跳一次）
    calendar_notify.create_notify_event(summary=title, description=body, start=now + timedelta(minutes=30),
                                        calendar_id=REMIND_CALENDAR_ID, attendee="", reminder_minutes=[20, 10])
    log(f"已在 {REMIND_CALENDAR_ID} 行事曆建提醒")
    return todo
