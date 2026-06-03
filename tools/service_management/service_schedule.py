"""
檸檬家事 客服排程系統
tools/service_management/service_schedule.py

三步驟：
  Step 1. 更新排班統計表（Drive 來源資料夾 → 目標試算表）
  Step 2. 更新每日回報（排班統計表 → 每日回報）
  Step 3. 更新前一天營業分數及營業額（Gmail → 每日回報）

打卡：
  - 主控表  (TOOLS_APP_LOG_SPREADSHEET_ID) → 工作表「執行記錄」
  - 執行檔  (LEMON_TARGET_FILE_ID)         → 工作表「_py_execution_log」

執行方式：
  python -u -m tools.service_management.service_schedule --step 0   # 全部
  python -u -m tools.service_management.service_schedule --step 1
  python -u -m tools.service_management.service_schedule --step 2
  python -u -m tools.service_management.service_schedule --step 3

必要 GitHub Secrets：
  GOOGLE_SERVICE_ACCOUNT        Service Account JSON 字串
  LEMON_TARGET_FILE_ID          目標試算表 ID
  TOOLS_APP_LOG_SPREADSHEET_ID  主控表 ID（打卡用）
  GMAIL_USER                    Gmail 帳號（讀信）
  GMAIL_APP_PASSWORD            Gmail App 密碼

選填：
  LOG_SPREADSHEET_ID            執行檔試算表（預設同 LEMON_TARGET_FILE_ID）
  NOTIFY_EMAIL / NOTIFY_PASSWORD / NOTIFY_TO
"""

from __future__ import annotations

import argparse
import email
import email.utils
import imaplib
import io
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ──────────────────────────────────────────────────────────
# 時區 & 基本設定
# ──────────────────────────────────────────────────────────

TZ_TAIPEI = timezone(timedelta(hours=8))

CONFIG: dict[str, Any] = {
    # Drive 來源資料夾（放排班統計表 xlsx / Google Sheets）
    "source_folder_id": "1V0IjoJqHlnkGb3Oq70Cil63pQ9j8r2Xv",

    # 目標試算表（台北台中排班統計表、每日回報所在）
    # 由 main() 動態載入（env SERVICE_TARGET_SPREADSHEET_ID 或主控試算表）
    "target_file_id": "",

    # 工作表名稱
    "target_sheet_name": "台北台中排班統計表",
    "report_sheet_name": "【空班】居家清潔(每日回報)",

    # 打卡 - 主控表（由 main() 動態載入）
    "master_log_spreadsheet_id": "",
    "master_log_sheet_name": "執行記錄",

    # 打卡 - 執行檔（同目標試算表）
    "job_log_sheet_name": "_py_execution_log",

    # 系統名稱（打卡用）
    "system_name": "客服排程系統",

    # 排班匯入區塊設定
    "import_config": {
        "taipei_current":   {"source_skip_rows": 1, "target_range": "G3"},
        "taipei_next":      {"source_skip_rows": 1, "target_range": "V3"},
        "taichung_current": {"source_skip_rows": 1, "target_range": "CD3"},
        "taichung_next":    {"source_skip_rows": 1, "target_range": "CS3"},
    },

    # 每日回報欄位對應
    "report_mappings": [
        {"source": "Q5:S5",   "target_col": 11},   # K:M
        {"source": "AF5:AH5", "target_col": 15},   # O:Q
        {"source": "CN5:CP5", "target_col": 101},  # CW:CY
        {"source": "DC5:DE5", "target_col": 105},  # DA:DC
    ],

    # Gmail
    "mail_subjects": {
        "taipei":   "近三日營業額-台北",
        "taichung": "近三日營業額-台中",
    },
    "mail_target_hour":   17,
    "mail_target_minute": 25,

    # 每日回報：從哪一列開始搜尋日期欄
    "report_date_start_row": 2000,
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Google 認證（使用 tools.common.config_loader，支援 st.secrets）
# ──────────────────────────────────────────────────────────

def _get_credentials() -> Credentials:
    try:
        from tools.common.config_loader import get_service_account_info
        info = get_service_account_info()
    except Exception as e:
        raise EnvironmentError(f"無法取得 GOOGLE_SERVICE_ACCOUNT：{e}")
    return Credentials.from_service_account_info(info, scopes=SCOPES)

def _gc() -> gspread.Client:
    return gspread.authorize(_get_credentials())

def _drive():
    return build("drive", "v3", credentials=_get_credentials())

# ──────────────────────────────────────────────────────────
# 時間工具
# ──────────────────────────────────────────────────────────

def now_tp() -> datetime:
    return datetime.now(TZ_TAIPEI)

def yesterday_tp() -> datetime:
    return now_tp() - timedelta(days=1)

def fmt(dt: datetime, f: str) -> str:
    return dt.strftime(f)


# ──────────────────────────────────────────────────────────
# 打卡工具
# ──────────────────────────────────────────────────────────
# process-level cache：同一次執行不重複呼叫 Sheets API 確認工作表
_LOG_SHEET_CACHE: dict[str, gspread.Worksheet] = {}

def _ensure_log_sheet(ss: gspread.Spreadsheet, sheet_name: str) -> gspread.Worksheet:
    """確保 log 工作表存在，不存在就建立並加標題列。結果 cache 在 process 內。"""
    cache_key = ss.id + ":" + sheet_name
    if cache_key in _LOG_SHEET_CACHE:
        return _LOG_SHEET_CACHE[cache_key]
    try:
        sh = ss.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=sheet_name, rows=2000, cols=12)
        sh.append_row(
            ["run_id", "時間", "系統名稱", "任務", "步驟", "狀態", "說明", "耗時(秒)"],
            value_input_option="USER_ENTERED",
        )
    _LOG_SHEET_CACHE[cache_key] = sh
    return sh


def checkin_master(
    gc: gspread.Client,
    run_id: str,
    task: str,
    step: str,
    status: str,
    note: str = "",
    elapsed: float = 0.0,
) -> None:
    """打卡到主控表 - 用 write_job_log 對齊主控表欄位格式。"""
    try:
        from tools.common.log_to_sheet import write_job_log
        write_job_log(
            system_name=CONFIG["system_name"],
            job_name=task,
            status="成功" if status == "SUCCESS" else ("執行中" if status == "RUNNING" else "失敗"),
            started_at=now_tp() if step == "START" else "",
            finished_at=now_tp() if step == "DONE" else "",
            message=note[:300],
            area="全區",
            period="",
            date=fmt(now_tp(), "%Y%m%d"),
            target="",
            source_file="",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            traceback_text=note[:300] if status == "ERROR" else "",
        )
    except Exception as e:
        log.warning("[主控表打卡失敗（非致命）] %s", e)


def checkin_job(
    gc: gspread.Client,
    run_id: str,
    task: str,
    step: str,
    status: str,
    note: str = "",
    elapsed: float = 0.0,
) -> None:
    """打卡到執行檔（目標試算表）的 _py_execution_log 工作表。"""
    target_id = (
        CONFIG.get("target_file_id", "")
        or os.environ.get("SERVICE_TARGET_SPREADSHEET_ID", "")
    )
    if not target_id:
        return
    try:
        ss = gc.open_by_key(target_id)
        sh = _ensure_log_sheet(ss, CONFIG["job_log_sheet_name"])
        sh.append_row(
            [
                run_id,
                fmt(now_tp(), "%Y/%m/%d %H:%M:%S"),
                CONFIG["system_name"],
                task,
                step,
                status,
                note[:300],
                round(elapsed, 1),
            ],
            value_input_option="USER_ENTERED",
        )
    except Exception as e:
        log.warning("[執行檔打卡失敗（非致命）] %s", e)


def checkin_both(
    gc: gspread.Client,
    run_id: str,
    task: str,
    step: str,
    status: str,
    note: str = "",
    elapsed: float = 0.0,
) -> None:
    """同時打卡到主控表 + 執行檔。"""
    checkin_master(gc, run_id, task, step, status, note, elapsed)
    checkin_job(gc, run_id, task, step, status, note, elapsed)


# ──────────────────────────────────────────────────────────
# 找排班檔
# ──────────────────────────────────────────────────────────

def _build_next_month_day_str(base: datetime) -> str:
    """年月+1，日不變（對應 GAS buildNextMonthSameDayString_）"""
    y, m, d = base.year, base.month, base.strftime("%d")
    nm = m + 1
    ny = y
    if nm > 12:
        nm = 1
        ny += 1
    return f"{ny}{nm:02d}{d}"


def _normalize_name(name: str) -> str:
    name = re.sub(r"\.[^.]+$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+|_|-", "", name)
    name = re.sub(r"schedule|lemon", "", name, flags=re.IGNORECASE)
    return name


def _matches(normalized: str, date_str: str, city: str) -> bool:
    return normalized == f"排班統計表{date_str}{city}"


def _pick_best(files: list[dict]) -> dict | None:
    if not files:
        return None
    sheets = [f for f in files if f["mimeType"] == "application/vnd.google-apps.spreadsheet"]
    pool = sheets if sheets else files
    pool.sort(key=lambda f: f.get("modifiedTime", ""), reverse=True)
    return pool[0]


def find_schedule_files(base: datetime) -> dict[str, dict]:
    drive = _drive()
    cur = fmt(base, "%Y%m%d")
    nxt = _build_next_month_day_str(base)

    q = (
        f"'{CONFIG['source_folder_id']}' in parents and trashed=false and ("
        "mimeType='application/vnd.google-apps.spreadsheet' or "
        "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')"
    )
    files = drive.files().list(
        q=q,
        fields="files(id,name,mimeType,modifiedTime)",
        pageSize=100,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute().get("files", [])

    log.info("Drive 資料夾找到 %d 個檔案", len(files))
    for f in files:
        log.info("  找到檔案：%s (%s)", f.get("name"), f.get("mimeType"))
    buckets: dict[str, list] = {
        "taipei_current": [], "taipei_next": [],
        "taichung_current": [], "taichung_next": [],
    }
    for f in files:
        name = f.get("name", "").strip()
        if "_temp_" in name:
            continue
        n = _normalize_name(name)
        if   _matches(n, cur, "台北"): buckets["taipei_current"].append(f)
        elif _matches(n, nxt, "台北"): buckets["taipei_next"].append(f)
        elif _matches(n, cur, "台中"): buckets["taichung_current"].append(f)
        elif _matches(n, nxt, "台中"): buckets["taichung_next"].append(f)

    found = {k: _pick_best(v) for k, v in buckets.items()}

    expected = {
        "taipei_current":   f"排班統計表{cur}-台北",
        "taipei_next":      f"排班統計表{nxt}-台北",
        "taichung_current": f"排班統計表{cur}-台中",
        "taichung_next":    f"排班統計表{nxt}-台中",
    }
    missing = [f"{k}：{expected[k]}" for k, v in found.items() if v is None]
    if missing:
        raise FileNotFoundError(
            "找不到以下排班檔：\n  " + "\n  ".join(missing)
        )

    log.info("排班檔確認 OK：%s", {k: v["name"] for k, v in found.items()})
    return found


def _a1_start(a1: str) -> tuple[int, int]:
    """把 'G3' 解成 (row=3, col=7)，忽略結尾的 :XX"""
    cell = a1.split(":")[0]
    m = re.match(r"^([A-Za-z]+)(\d+)$", cell)
    if not m:
        raise ValueError(f"無法解析 A1: {a1}")
    col = sum((ord(c) - 64) * (26 ** i) for i, c in enumerate(reversed(m.group(1).upper())))
    return int(m.group(2)), col


def _import_block(
    file_info: dict,
    target_sheet: gspread.Worksheet,
    cfg: dict,
    drive,
    gc: gspread.Client,
) -> None:
    fid  = file_info["id"]
    mime = file_info["mimeType"]
    name = file_info["name"]
    log.info("  匯入：%s", name)

    if mime == "application/vnd.google-apps.spreadsheet":
        ss = gc.open_by_key(fid)
        all_values = ss.get_worksheet(0).get_all_values()
    else:
        # xlsx 在共用雲端硬碟：轉成 Google Sheets 保留（新蓋舊），再讀取
        gs_name = re.sub(r"\.xlsx$", "", name, flags=re.IGNORECASE)
        log.info("  xlsx → Google Sheets：%s", gs_name)

        # 先查並刪除資料夾內所有同名 Google Sheets（確保不留舊檔）
        existing = drive.files().list(
            q=(
                f"'{CONFIG['source_folder_id']}' in parents"
                f" and name = '{gs_name}'"
                f" and mimeType = 'application/vnd.google-apps.spreadsheet'"
                f" and trashed = false"
            ),
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute().get("files", [])

        for old in existing:
            drive.files().delete(
                fileId=old["id"], supportsAllDrives=True
            ).execute()
            log.info("  已刪除舊版 Google Sheets：%s (%s)", old["name"], old["id"])

        # 用 Drive v2 copy 轉換（v2 支援 mimeType 轉換）
        from googleapiclient.discovery import build as _build
        drive_v2 = _build("drive", "v2", credentials=_get_credentials())
        copied = drive_v2.files().copy(
            fileId=fid,
            body={
                "title": gs_name,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [{"id": CONFIG["source_folder_id"]}],
            },
            supportsAllDrives=True,
        ).execute()

        new_gs_id = copied["id"]
        log.info("  轉換完成，新 Google Sheets ID：%s", new_gs_id)

        gs_ss = gc.open_by_key(new_gs_id)
        all_values = gs_ss.get_worksheet(0).get_all_values()

    skip = cfg["source_skip_rows"]
    values = [(r[:9] + [""] * 9)[:9] for r in all_values[skip:]]
    values = [r for r in values if any(c.strip() for c in r)]

    if not values:
        log.warning("  來源無資料，略過：%s", name)
        return

    start_row, start_col = _a1_start(cfg["target_range"])
    target_sheet.update(
        values=values,
        range_name=f"R{start_row}C{start_col}:R{start_row + len(values) - 1}C{start_col + 8}",
        value_input_option="USER_ENTERED",
    )
    log.info("  → 寫入 %d 列（起始 R%dC%d）", len(values), start_row, start_col)


# ──────────────────────────────────────────────────────────
# Step 1：更新排班統計表
# ──────────────────────────────────────────────────────────

def step1_update_schedule_stats(
    run_dt: datetime, gc: gspread.Client, drive, run_id: str
) -> dict:
    task = "Step1_更新排班統計表"
    t0 = now_tp()
    checkin_both(gc, run_id, task, "START", "RUNNING")

    try:
        found = find_schedule_files(run_dt)
        target_ss = gc.open_by_key(CONFIG["target_file_id"])
        target_sh = target_ss.worksheet(CONFIG["target_sheet_name"])

        cfg = CONFIG["import_config"]
        _import_block(found["taipei_current"],   target_sh, cfg["taipei_current"],   drive, gc)
        _import_block(found["taipei_next"],      target_sh, cfg["taipei_next"],      drive, gc)
        _import_block(found["taichung_current"], target_sh, cfg["taichung_current"], drive, gc)
        _import_block(found["taichung_next"],    target_sh, cfg["taichung_next"],    drive, gc)

        elapsed = (now_tp() - t0).total_seconds()
        note = json.dumps({k: v["name"] for k, v in found.items()}, ensure_ascii=False)
        checkin_both(gc, run_id, task, "DONE", "SUCCESS", note, elapsed)
        log.info("Step 1 完成（%.1fs）", elapsed)
        return {"ok": True, "files": {k: v["name"] for k, v in found.items()}}

    except Exception as e:
        elapsed = (now_tp() - t0).total_seconds()
        checkin_both(gc, run_id, task, "DONE", "ERROR", str(e)[:300], elapsed)
        raise


# ──────────────────────────────────────────────────────────
# Step 2：更新每日回報
# ──────────────────────────────────────────────────────────

def _find_date_row(sheet: gspread.Worksheet, target_dt: datetime) -> int:
    start = CONFIG["report_date_start_row"]
    target_key = fmt(target_dt, "%Y%m%d")
    col_a = sheet.col_values(1)  # 1-indexed list，col_a[0]=row1
    for i in range(start - 1, len(col_a)):
        cell = col_a[i]
        if not cell:
            continue
        m = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", str(cell).strip())
        if m:
            key = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
            if key == target_key:
                return i + 1  # 1-based
    return -1


def step2_write_daily_report(
    run_dt: datetime, gc: gspread.Client, run_id: str
) -> dict:
    task = "Step2_更新每日回報"
    t0 = now_tp()
    checkin_both(gc, run_id, task, "START", "RUNNING")

    try:
        target_ss  = gc.open_by_key(CONFIG["target_file_id"])
        stat_sh    = target_ss.worksheet(CONFIG["target_sheet_name"])
        report_sh  = target_ss.worksheet(CONFIG["report_sheet_name"])

        row = _find_date_row(report_sh, run_dt)
        if row < 1:
            raise ValueError(
                f"找不到今天日期列：{fmt(run_dt, '%Y-%m-%d')}"
            )

        for mp in CONFIG["report_mappings"]:
            vals = stat_sh.get(mp["source"])
            if not vals:
                continue
            tc = mp["target_col"]
            report_sh.update(
                values=vals,
                range_name=gspread.utils.rowcol_to_a1(row, tc),
                value_input_option="USER_ENTERED",
            )

        elapsed = (now_tp() - t0).total_seconds()
        checkin_both(gc, run_id, task, "DONE", "SUCCESS", f"row={row}", elapsed)
        log.info("Step 2 完成，row=%d（%.1fs）", row, elapsed)
        return {"ok": True, "row": row}

    except Exception as e:
        elapsed = (now_tp() - t0).total_seconds()
        checkin_both(gc, run_id, task, "DONE", "ERROR", str(e)[:300], elapsed)
        raise


# ──────────────────────────────────────────────────────────
# Step 3：從 Gmail 抓前一天營業額
# ──────────────────────────────────────────────────────────

def _get_secret(key: str) -> str:
    """
    依序嘗試四個來源取得 secret（對齊 config_loader.get_service_account_info 邏輯）：
    1. os.environ
    2. os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]（只適用 SA，一般 key 不用）
    3. st.secrets[key]
    4. st.secrets["gcp_service_account"][key]（不適用一般 key）
    """
    # 1. 直接從 os.environ 讀
    val = os.environ.get(key, "").strip()
    if val:
        return val

    # 2. 從 st.secrets 讀（子進程裡 Streamlit 仍可能有 secrets）
    try:
        import streamlit as st
        val = str(st.secrets.get(key, "") or "").strip()
        if val:
            return val
    except Exception:
        pass

    return ""


def _imap_connect() -> imaplib.IMAP4_SSL:
    user = _get_secret("GMAIL_USER")
    pwd  = _get_secret("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise EnvironmentError("需要 GMAIL_USER 和 GMAIL_APP_PASSWORD")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, pwd)
    return imap


def _decode_subject(msg) -> str:
    parts = decode_header(msg.get("Subject", ""))
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += str(part)
    return result


def _plain_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return msg.get_payload(decode=True).decode(charset, errors="replace")


def _pick_mail(subject: str, target_date: datetime) -> str:
    """抓 target_date 當天、主旨完全符合的信，取最接近 17:25 的那封。"""
    target_time = target_date.replace(
        hour=CONFIG["mail_target_hour"],
        minute=CONFIG["mail_target_minute"],
        second=0, microsecond=0,
    )
    imap_date = fmt(target_date, "%d-%b-%Y")
    imap = _imap_connect()
    imap.select("INBOX")

    # 中文主旨需用 UTF-8 搜尋（IMAP 預設 ASCII 無法處理中文）
    search_criteria = f'(SUBJECT "{subject}" ON {imap_date})'
    try:
        # 嘗試 UTF-8 搜尋
        _, data = imap.search("UTF-8", search_criteria.encode("utf-8"))
    except Exception:
        # fallback：用 CHARSET 參數
        try:
            _, data = imap.uid("search", "CHARSET", "UTF-8",
                               "SUBJECT", subject.encode("utf-8"),
                               "ON", imap_date)
        except Exception:
            _, data = imap.search(None, f'(ON {imap_date})')
    mail_ids = data[0].split()

    if not mail_ids:
        imap.logout()
        raise RuntimeError(f"找不到主旨「{subject}」在 {fmt(target_date, '%Y-%m-%d')} 的信件")

    candidates = []
    for mid in mail_ids:
        _, msg_data = imap.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        if _decode_subject(msg).strip() != subject:
            continue
        date_tuple = email.utils.parsedate_tz(msg.get("Date", ""))
        if not date_tuple:
            continue
        msg_dt = datetime.fromtimestamp(email.utils.mktime_tz(date_tuple), tz=TZ_TAIPEI)
        if msg_dt.date() != target_date.date():
            continue
        diff = abs((msg_dt - target_time).total_seconds())
        candidates.append({"dt": msg_dt, "diff": diff, "body": _plain_body(msg)})

    imap.logout()
    if not candidates:
        raise RuntimeError(f"找到信件但沒有主旨完全符合「{subject}」且日期符合的信")

    candidates.sort(key=lambda x: x["diff"])
    chosen = candidates[0]
    log.info("  %s → 選中 %s（與17:25差%.0fs）", subject, fmt(chosen["dt"], "%H:%M:%S"), chosen["diff"])
    return chosen["body"]


def _normalize_lines(text: str) -> list[str]:
    for ch in ["\r\n", "\r"]:
        text = text.replace(ch, "\n")
    for ch in ["\u00A0", "\u200B", "\u200C", "\u200D", "\uFEFF"]:
        text = text.replace(ch, "")
    text = text.replace("\u3000", " ").replace("﹕", "：")
    return [l.strip() for l in text.split("\n") if l.strip()]


def _find_daily_value(lines: list[str], date_str: str) -> int:
    header = "【專業清潔】日營業額"
    idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if header in lines[i].replace(" ", ""):
            idx = i
            break
    if idx == -1:
        raise ValueError("找不到【專業清潔】日營業額標題")
    for i in range(idx + 1, len(lines)):
        compact = lines[i].replace(" ", "")
        if compact != header and re.match(r"^【.+】日營業額$", compact):
            break
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[：:](\d+)分?$", compact)
        if m and m.group(1) == date_str:
            return int(m.group(2))
    raise ValueError(f"找到標題但找不到 {date_str} 的數值")


def _find_monthly_total(lines: list[str], month_str: str) -> tuple[int, int]:
    label = f"【{month_str}總營業額】"
    amount, count = None, None
    for i in range(len(lines) - 1, -1, -1):
        compact = lines[i].replace(" ", "")
        if label not in compact:
            continue
        if amount is None:
            m = re.search(r"\$([\d,]+)", compact)
            if m:
                amount = int(m.group(1).replace(",", ""))
        if count is None and "$" not in compact:
            m = re.search(r"[：:](\d+)$", compact)
            if m:
                count = int(m.group(1))
        if amount is not None and count is not None:
            break
    if amount is None:
        raise ValueError(f"找不到【{month_str}總營業額】金額")
    if count is None:
        raise ValueError(f"找不到【{month_str}總營業額】筆數")
    return amount, count


def _parse_mail(body: str, month_str: str, date_str: str) -> dict:
    lines = _normalize_lines(body)
    daily = _find_daily_value(lines, date_str)
    amount, count = _find_monthly_total(lines, month_str)
    return {"daily_value": daily, "total_count": count, "total_amount": amount}


def step3_import_revenue(gc: gspread.Client, run_id: str) -> dict:
    task = "Step3_更新前一天營業分數及營業額"
    t0 = now_tp()
    checkin_both(gc, run_id, task, "START", "RUNNING")

    try:
        yesterday  = yesterday_tp()
        date_str   = fmt(yesterday, "%Y-%m-%d")
        month_str  = fmt(yesterday, "%Y-%m")

        target_ss  = gc.open_by_key(CONFIG["target_file_id"])
        report_sh  = target_ss.worksheet(CONFIG["report_sheet_name"])

        row = _find_date_row(report_sh, yesterday)
        if row < 1:
            raise ValueError(f"找不到前一天日期列：{date_str}")

        taipei   = _parse_mail(_pick_mail(CONFIG["mail_subjects"]["taipei"],   yesterday), month_str, date_str)
        taichung = _parse_mail(_pick_mail(CONFIG["mail_subjects"]["taichung"], yesterday), month_str, date_str)

        log.info("  台北：%s", taipei)
        log.info("  台中：%s", taichung)

        # 台北 → H/I/J（欄 8,9,10）
        report_sh.update(
            values=[[taipei["daily_value"], taipei["total_count"], taipei["total_amount"]]],
            range_name=gspread.utils.rowcol_to_a1(row, 8),
            value_input_option="USER_ENTERED",
        )
        # 台中 → CT/CU/CV（欄 98,99,100）
        report_sh.update(
            values=[[taichung["daily_value"], taichung["total_count"], taichung["total_amount"]]],
            range_name=gspread.utils.rowcol_to_a1(row, 98),
            value_input_option="USER_ENTERED",
        )

        elapsed = (now_tp() - t0).total_seconds()
        note = json.dumps({"row": row, "taipei": taipei, "taichung": taichung}, ensure_ascii=False)
        checkin_both(gc, run_id, task, "DONE", "SUCCESS", note[:300], elapsed)
        log.info("Step 3 完成，row=%d（%.1fs）", row, elapsed)
        return {"ok": True, "row": row, "taipei": taipei, "taichung": taichung}

    except Exception as e:
        elapsed = (now_tp() - t0).total_seconds()
        checkin_both(gc, run_id, task, "DONE", "ERROR", str(e)[:300], elapsed)
        raise


# ──────────────────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────────────────

def _load_target_file_id() -> str:
    """
    依序嘗試取得目標試算表 ID：
    1. 環境變數 SERVICE_TARGET_SPREADSHEET_ID（由 toolapp.py 注入或 GitHub Secret）
    2. 主控試算表「系統設定」→ 客服排程系統 → 共用雲端資料夾ID 欄位
    """
    # 1. 環境變數優先（toolapp.py 執行前已注入）
    val = os.environ.get("SERVICE_TARGET_SPREADSHEET_ID", "").strip()
    if val:
        log.info("目標試算表 ID 來源：環境變數 SERVICE_TARGET_SPREADSHEET_ID")
        return val

    # 2. 從主控試算表讀取（429 時忽略）
    try:
        from tools.common.config_loader import get_system_config
        system_cfg = get_system_config("客服排程系統")
        folder_id = (
            system_cfg.get("共用雲端資料夾ID / 根目錄ID", "").strip()
            or system_cfg.get("folder_id", "").strip()
        )
        if folder_id:
            log.info("目標試算表 ID 來源：主控試算表系統設定")
            return folder_id
    except Exception as e:
        log.warning("從主控試算表讀取目標 ID 失敗（非致命）：%s", e)

    return ""



def main() -> None:
    parser = argparse.ArgumentParser(description="檸檬家事客服排程系統")
    parser.add_argument(
        "--step", type=int, choices=[0, 1, 2, 3], default=0,
        help="0=全部, 1=排班統計表, 2=每日回報, 3=前一天營業額",
    )
    args = parser.parse_args()

    # 動態載入目標試算表 ID（Secret 或主控試算表）
    target_id = _load_target_file_id()
    if not target_id:
        sys.exit("❌ 請在主控試算表「系統設定」填入客服排程系統的共用雲端資料夾ID，或設定 Secret SERVICE_TARGET_SPREADSHEET_ID")
    CONFIG["target_file_id"] = target_id

    # 主控表 ID（打卡用）
    CONFIG["master_log_spreadsheet_id"] = (
        os.environ.get("TOOLS_APP_LOG_SPREADSHEET_ID", "").strip()
        or _get_secret("TOOLS_APP_LOG_SPREADSHEET_ID")
    )

    run_id = str(uuid.uuid4())[:8]
    run_dt = now_tp()
    step   = args.step

    log.info("=== 客服排程系統 run_id=%s step=%s 台北時間=%s ===",
             run_id, step or "ALL", fmt(run_dt, "%Y-%m-%d %H:%M:%S"))

    gc    = _gc()
    drive = _drive()

    # 整體開始打卡
    checkin_both(gc, run_id, "RUN", "START", "RUNNING",
                 f"step={step}", 0)

    t_total = now_tp()
    errors  = []

    def run(step_num: int, fn, *a):
        labels = {
            1: "Step1_更新排班統計表",
            2: "Step2_更新每日回報",
            3: "Step3_更新前一天營業分數及營業額",
        }
        log.info("--- %s ---", labels[step_num])
        try:
            fn(*a)
        except Exception as e:
            errors.append(f"Step {step_num}: {e}")
            log.error("Step %d 失敗：%s", step_num, e)

    if step in (0, 1):
        run(1, step1_update_schedule_stats, run_dt, gc, drive, run_id)
    if step in (0, 2):
        run(2, step2_write_daily_report, run_dt, gc, run_id)
    if step in (0, 3):
        run(3, step3_import_revenue, gc, run_id)

    elapsed_total = (now_tp() - t_total).total_seconds()
    final_status  = "ERROR" if errors else "SUCCESS"
    final_note    = "; ".join(errors) if errors else "全部完成"

    checkin_both(gc, run_id, "RUN", "DONE", final_status, final_note[:300], elapsed_total)

    if errors:
        log.error("執行結束（有失敗）：%s", errors)
        sys.exit(1)

    log.info("=== 全部完成 run_id=%s（%.1fs）===", run_id, elapsed_total)


if __name__ == "__main__":
    main()
