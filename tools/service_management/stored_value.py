"""
檸檬家事客服排程－儲值功能
tools/service_management/stored_value.py

最後更新：2026-08-12

Change Log：
- 2026-08-12：確認全跑參數允許 --step 0，並依序執行 Step 1 儲值結算與 Step 2 VIP 對帳。
- 2026-08-12：修正部署版錯誤引用 crm_export；本檔維持完整獨立實作，不再依賴舊相容入口。
- 2026-08-12：將完整儲值實作由 crm_export.py 正式移至 stored_value.py，名稱與功能一致。
- 2026-08-12：修正明確傳入 --step 0 時被 argparse 拒絕，允許 0 執行儲值全跑。
- 2026-08-12：儲值金流程改為後台下載結算 Excel，覆寫各區固定試算表分頁並更新日期名稱；支援全區／台北／台中。

功能（依序執行）：
  Step 1. 各地區登入 backend.lemonclean.com.tw → 抓取儲值金會員資料
          → 寫入目標試算表「儲值金表_{地區}」工作表
  Step 2. 各地區讀取 Google Calendar → 整理定期VIP行事曆
          → 寫入目標試算表「定期VIP_{地區}_{yyyyMMdd}」工作表

地區設定來源（主控試算表「客服地區設定」工作表）：
  欄位：地區名稱 | Calendar ID | 啟用
  新增地區只需在試算表新增一列即可，無需改程式碼

憑證來源（GitHub Secrets）：
  {地區}_EMAIL    e.g. TAIPEI_EMAIL
  {地區}_PASSWORD e.g. TAIPEI_PASSWORD
  地區名稱轉 Secret key 規則：台北→TAIPEI, 台中→TAICHUNG, 新竹→HSINCHU, 高雄→KAOHSIUNG
  其他地區可用地區名稱拼音大寫

執行方式：
  python -u -m tools.service_management.stored_value            # 全區（台北＋台中）全跑
  python -u -m tools.service_management.stored_value --step 1   # 只抓儲值金
  python -u -m tools.service_management.stored_value --step 2   # 只匯出VIP日曆
  python -u -m tools.service_management.stored_value --area 全區 # 台北＋台中
  python -u -m tools.service_management.stored_value --area 台北 # 只跑台北
  python -u -m tools.service_management.stored_value --area 台中 # 只跑台中
  python -u -m tools.service_management.stored_value --start 2026-06-01 --end 2026-06-30

必要 GitHub Secrets：
  GOOGLE_SERVICE_ACCOUNT        Service Account JSON
  LEMON_TARGET_FILE_ID          目標試算表 ID（儲值金表、VIP 匯出寫在這裡）
  TOOLS_APP_LOG_SPREADSHEET_ID  主控試算表 ID（地區設定讀這裡）
  {地區}_EMAIL / {地區}_PASSWORD  各地區 backend 登入憑證
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone, date
from html.parser import HTMLParser
from io import BytesIO
from typing import Any

import pandas as pd
import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ──────────────────────────────────────────────────────────
# 基本設定
# ──────────────────────────────────────────────────────────

TZ_TAIPEI = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# backend API
BACKEND_BASE = "https://backend.lemonclean.com.tw"
LOGIN_URL = f"{BACKEND_BASE}/login"
STORED_VALUE_EXPORT_URL = f"{BACKEND_BASE}/member/export_stored_value"

# 客服儲值功能沿用月排程系統的儲值金結算試算表；台北、台中分別
# 尋找並沿用「{地區}儲值金結算_YYYYMMDD」分頁。
STORED_VALUE_SPREADSHEET_IDS: dict[str, str] = {
    "台北": "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE",
    "台中": "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE",
}

# 主控試算表地區設定工作表
AREA_SHEET_NAME    = "客服地區設定"
AREA_SHEET_HEADERS = ["地區名稱", "Calendar ID", "目標試算表ID", "啟用"]

# 地區名稱 → Secrets key 前綴對應
AREA_TO_SECRET_PREFIX: dict[str, str] = {
    "台北": "TAIPEI",
    "台中": "TAICHUNG",
    "新竹": "HSINCHU",
    "高雄": "KAOHSIUNG",
    "桃園": "TAOYUAN",
    "新北": "XINBEI",
    "台南": "TAINAN",
    "嘉義": "CHIAYI",
    "宜蘭": "YILAN",
}

# 客服排程介面允許執行的區域。
# 「全區」固定代表台北＋台中，避免設定表新增其他地區後被意外納入。
SERVICE_AREA_GROUPS: dict[str, tuple[str, ...]] = {
    "全區": ("台北", "台中"),
    "台北": ("台北",),
    "台中": ("台中",),
}

# Calendar 行事曆顏色代碼
COLOR_INTERNAL   = "7"   # 內部行程（排除）
COLOR_PAUSE      = "10"  # 暫停
COLOR_UNARRANGED = "3"   # 未安排

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


# ──────────────────────────────────────────────────────────
# Google 認證
# ──────────────────────────────────────────────────────────

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

def _calendar_service():
    return build("calendar", "v3", credentials=_get_credentials())


# ──────────────────────────────────────────────────────────
# 時間工具
# ──────────────────────────────────────────────────────────

def now_tp() -> datetime:
    return datetime.now(TZ_TAIPEI)

def fmt(dt: datetime, f: str) -> str:
    return dt.strftime(f)

def today_tp() -> date:
    return now_tp().date()


# ──────────────────────────────────────────────────────────
# 打卡工具（同 lemon_schedule_sync 模式）
# ──────────────────────────────────────────────────────────
# process-level cache：避免重複讀取工作表造成 429
_LOG_SHEET_CACHE: dict[str, gspread.Worksheet] = {}
_SERVICE_LOG_CONTEXT = {
    "area": "全區",
    "period": "",
    "run_feature": "CRM／儲值全部執行",
}

def _ensure_log_sheet(ss: gspread.Spreadsheet, sheet_name: str) -> gspread.Worksheet:
    cache_key = ss.id + ":" + sheet_name
    if cache_key in _LOG_SHEET_CACHE:
        return _LOG_SHEET_CACHE[cache_key]
    try:
        sh = ss.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=sheet_name, rows=2000, cols=10)
        if sheet_name == "客服排程執行Log":
            sh.append_row([
                "執行時間", "系統", "功能", "執行類型", "區域",
                "期別/日期", "目標", "來源", "結果", "訊息",
            ])
        else:
            sh.append_row(["run_id", "時間", "系統名稱", "任務", "步驟", "狀態", "說明", "耗時(秒)"])
    _LOG_SHEET_CACHE[cache_key] = sh
    return sh

def _checkin(gc: gspread.Client, spreadsheet_id: str, sheet_name: str,
             run_id: str, task: str, step: str, status: str,
             note: str = "", elapsed: float = 0.0) -> None:
    if not spreadsheet_id:
        return
    try:
        ss = gc.open_by_key(spreadsheet_id)
        sh = _ensure_log_sheet(ss, sheet_name)
        sh.append_row([
            run_id, fmt(now_tp(), "%Y/%m/%d %H:%M:%S"),
            "客服CRM系統", task, step, status, note[:300], round(elapsed, 1)
        ])
    except Exception as e:
        log.warning("[打卡失敗（非致命）%s] %s", sheet_name, e)

def checkin_both(gc: gspread.Client, run_id: str, task: str, step: str,
                 status: str, note: str = "", elapsed: float = 0.0) -> None:
    master_id = os.environ.get("TOOLS_APP_LOG_SPREADSHEET_ID", "")
    target_id = _load_target_file_id()
    _checkin_service_schedule_log(
        gc, master_id, run_id, task, step, status, note, elapsed
    )
    _checkin(gc, target_id, "_py_execution_log", run_id, task, step, status, note, elapsed)


def _checkin_service_schedule_log(
    gc: gspread.Client,
    spreadsheet_id: str,
    run_id: str,
    task: str,
    step: str,
    status: str,
    note: str = "",
    elapsed: float = 0.0,
) -> None:
    """將 CRM／儲值執行紀錄併入「客服排程執行Log」。"""
    if not spreadsheet_id:
        return

    feature_names = {
        "Step1_抓取儲值金": "【儲值】抓儲值金",
        "Step2_匯出定期VIP日曆": "【CRM】產生 CRM",
        "RUN": _SERVICE_LOG_CONTEXT["run_feature"],
    }
    result_names = {
        "RUNNING": "執行中",
        "SUCCESS": "成功",
        "ERROR": "失敗",
    }

    try:
        ss = gc.open_by_key(spreadsheet_id)
        sh = _ensure_log_sheet(ss, "客服排程執行Log")
        message_parts = [f"run_id={run_id}", f"step={step}"]
        if note:
            message_parts.append(note[:240])
        if elapsed:
            message_parts.append(f"耗時={elapsed:.1f}秒")
        sh.append_row([
            fmt(now_tp(), "%Y-%m-%d %H:%M:%S"),
            "客服排程",
            feature_names.get(task, task),
            "排程",
            _SERVICE_LOG_CONTEXT["area"],
            _SERVICE_LOG_CONTEXT["period"],
            "",
            "CRM客服系統",
            result_names.get(status, status),
            " | ".join(message_parts)[:500],
        ])
    except Exception as e:
        log.warning("[客服排程 Log 寫入失敗（非致命）] %s", e)


# ──────────────────────────────────────────────────────────
# 地區設定讀取（主控試算表）
# ──────────────────────────────────────────────────────────

def _ensure_area_sheet(gc: gspread.Client) -> gspread.Worksheet:
    """確保主控試算表有「客服地區設定」工作表。"""
    from tools.common.config_loader import get_master_spreadsheet_id
    master_id = (
        os.environ.get("TOOLS_APP_LOG_SPREADSHEET_ID", "").strip()
        or get_master_spreadsheet_id()
    )
    if not master_id:
        raise EnvironmentError("TOOLS_APP_LOG_SPREADSHEET_ID 未設定")
    ss = gc.open_by_key(master_id)
    try:
        sh = ss.worksheet(AREA_SHEET_NAME)
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=AREA_SHEET_NAME, rows=50, cols=5)
        sh.append_row(AREA_SHEET_HEADERS)
        defaults = [
            ["台北", "", "", "TRUE"],
            ["台中", "", "", "TRUE"],
            ["新竹", "", "", "TRUE"],
            ["高雄", "", "", "TRUE"],
        ]
        for row in defaults:
            sh.append_row(row)
        log.info("已建立「%s」工作表，請填入各地區 Calendar ID", AREA_SHEET_NAME)
    return sh

def load_area_config(gc: gspread.Client, filter_area: str | None = "全區") -> list[dict]:
    """
    從主控試算表讀取地區設定。
    回傳 [{"name": "台北", "calendar_id": "...", "target_spreadsheet_id": "...", "enabled": True}, ...]
    """
    selected_area = (filter_area or "全區").strip()
    if selected_area not in SERVICE_AREA_GROUPS:
        allowed = "、".join(SERVICE_AREA_GROUPS)
        raise ValueError(f"不支援的區域：{selected_area}；可選：{allowed}")
    allowed_areas = set(SERVICE_AREA_GROUPS[selected_area])

    sh = _ensure_area_sheet(gc)
    records = sh.get_all_records()
    areas = []
    for r in records:
        name       = str(r.get("地區名稱", "")).strip()
        cal_id     = str(r.get("Calendar ID", "")).strip()
        target_id  = str(r.get("目標試算表ID", "")).strip()
        enabled_v  = str(r.get("啟用", "TRUE")).strip().upper()
        enabled    = enabled_v not in ("FALSE", "0", "否", "停用")
        if not name or not enabled:
            continue
        if name not in allowed_areas:
            continue
        areas.append({
            "name":                name,
            "calendar_id":         cal_id,
            "target_spreadsheet_id": target_id,
        })
    return areas

def get_secret_prefix(area_name: str) -> str:
    """台北 → TAIPEI 等，找不到就用拼音大寫（使用者自訂）。"""
    return AREA_TO_SECRET_PREFIX.get(area_name, area_name.upper().replace(" ", "_"))

def get_area_credentials(area_name: str) -> tuple[str, str]:
    """沿用月排程儲值金結算的帳密來源，找不到就拋出例外。"""
    from tools.scheduled_monthly.stored_value_settlement import load_accounts

    account = load_accounts().get(area_name, {})
    email = str(account.get("email", "")).strip()
    password = str(account.get("password", "")).strip()
    if not email or not password:
        prefix = get_secret_prefix(area_name)
        raise EnvironmentError(
            f"找不到月排程帳密 accounts.{prefix.lower()}，"
            f"也沒有 {prefix}_EMAIL / {prefix}_PASSWORD"
        )
    return email, password


# ──────────────────────────────────────────────────────────
# Step 1：下載並更新儲值金結算表
# ──────────────────────────────────────────────────────────

class _CsrfTokenParser(HTMLParser):
    """Extract the Laravel login form's CSRF token without extra dependencies."""

    def __init__(self) -> None:
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        values = dict(attrs)
        if values.get("name") == "_token" and values.get("value"):
            self.token = values["value"] or ""


def _login_backend_session(email: str, password: str, area_name: str) -> requests.Session:
    """以後台網頁表單登入，回傳保留登入 cookie 的 Session。"""
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = session.get(LOGIN_URL, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    parser = _CsrfTokenParser()
    parser.feed(resp.text)
    if not parser.token:
        raise RuntimeError(f"[{area_name}] 登入頁面找不到 _token")

    resp = session.post(
        LOGIN_URL,
        data={"_token": parser.token, "email": email, "password": password},
        headers=headers,
        timeout=30,
        allow_redirects=True,
    )
    resp.raise_for_status()
    if "login" in resp.url.lower():
        raise RuntimeError(f"[{area_name}] 後台登入失敗")
    return session


def _download_stored_value_export(session: requests.Session, area_name: str) -> bytes:
    """下載執行當下最新的儲值金結算 Excel。"""
    resp = session.get(
        STORED_VALUE_EXPORT_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=120,
        allow_redirects=True,
    )
    resp.raise_for_status()
    preview = resp.content[:300].decode("utf-8", errors="ignore").lower()
    if "<html" in preview or "login" in resp.url.lower():
        raise RuntimeError(f"[{area_name}] 儲值金結算下載失敗：登入已失效或後台回傳 HTML")
    if not resp.content:
        raise RuntimeError(f"[{area_name}] 儲值金結算下載結果為空")
    return resp.content


def _excel_to_rows(content: bytes, area_name: str) -> list[list[Any]]:
    """將下載的第一張 Excel 工作表轉成可貼入 Google Sheets 的二維資料。"""
    try:
        frame = pd.read_excel(BytesIO(content), engine="openpyxl", dtype=object)
    except Exception as exc:
        raise RuntimeError(f"[{area_name}] 儲值金結算檔轉檔失敗：{exc}") from exc

    frame = frame.fillna("")
    values: list[list[Any]] = [list(map(str, frame.columns.tolist()))]
    for row in frame.itertuples(index=False, name=None):
        converted = []
        for value in row:
            if isinstance(value, (datetime, date)):
                converted.append(value.strftime("%Y-%m-%d %H:%M:%S"))
            elif hasattr(value, "item"):
                converted.append(value.item())
            else:
                converted.append(value)
        values.append(converted)
    return values


def _write_stored_value_sheet(
    gc: gspread.Client,
    area_name: str,
    values: list[list[Any]],
) -> gspread.Worksheet:
    """覆寫各區固定試算表中的同一張儲值金結算分頁，並更新日期名稱。"""
    target_id = STORED_VALUE_SPREADSHEET_IDS.get(area_name, "")
    if not target_id:
        raise EnvironmentError(f"[{area_name}] 尚未設定儲值金結算目的試算表")

    ss = gc.open_by_key(target_id)
    sheet_name = f"{area_name}儲值金結算_{fmt(now_tp(), '%Y%m%d')}"
    pattern = re.compile(rf"^{re.escape(area_name)}儲值金結算_\d{{8}}$")
    candidates = [worksheet for worksheet in ss.worksheets() if pattern.fullmatch(worksheet.title)]

    if candidates:
        # 同一區原則上只會有一張；若歷史上有多張，沿用最末日期的一張。
        sh = max(candidates, key=lambda worksheet: worksheet.title)
        if sh.title != sheet_name:
            sh.update_title(sheet_name)
        sh.clear()
    else:
        required_rows = max(len(values) + 10, 500)
        required_cols = max(max((len(row) for row in values), default=0) + 5, 10)
        sh = ss.add_worksheet(
            title=sheet_name,
            rows=required_rows,
            cols=required_cols,
        )

    if values:
        required_rows = max(len(values) + 10, 500)
        required_cols = max(max(len(row) for row in values) + 5, 10)
        if sh.row_count < required_rows or sh.col_count < required_cols:
            sh.resize(rows=max(sh.row_count, required_rows), cols=max(sh.col_count, required_cols))
        sh.update(values=values, range_name="A1", value_input_option="USER_ENTERED")
        sh.freeze(rows=1)

    log.info("[%s] 儲值金結算寫入完成：%d 筆 → 工作表「%s」", area_name, max(len(values) - 1, 0), sheet_name)
    return sh


def step1_fetch_stored_value(
    gc: gspread.Client,
    areas: list[dict],
    run_id: str,
) -> dict:
    """Step 1：各地區登入抓儲值金，寫入試算表。"""
    task = "Step1_抓取儲值金"
    t0 = now_tp()
    checkin_both(gc, run_id, task, "START", "RUNNING")

    results = {}
    errors  = []

    for area in areas:
        area_name = area["name"]
        log.info("=== [%s] 抓取儲值金 ===", area_name)
        try:
            email, password = get_area_credentials(area_name)
            session = _login_backend_session(email, password, area_name)
            content = _download_stored_value_export(session, area_name)
            values = _excel_to_rows(content, area_name)
            worksheet = _write_stored_value_sheet(gc, area_name, values)

            results[area_name] = {
                "count": max(len(values) - 1, 0),
                "sheet": worksheet.title,
                "ok": True,
            }
        except Exception as e:
            log.error("[%s] 儲值金抓取失敗：%s", area_name, e)
            errors.append(f"{area_name}: {e}")
            results[area_name] = {"ok": False, "error": str(e)}

    elapsed = (now_tp() - t0).total_seconds()
    status  = "ERROR" if errors else "SUCCESS"
    note    = json.dumps(results, ensure_ascii=False)[:300]
    checkin_both(gc, run_id, task, "DONE", status, note, elapsed)

    if errors:
        raise RuntimeError("部分地區儲值金抓取失敗：\n" + "\n".join(errors))

    return results


# ──────────────────────────────────────────────────────────
# 工具函式（對應 GAS 邏輯）
# ──────────────────────────────────────────────────────────

def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\u3000", " ").replace("\r\n", " ").replace("\n", " ")).strip()

def normalize_name(name: Any) -> str:
    return normalize_text(name)

def normalize_address(addr: Any) -> str:
    s = normalize_text(addr)
    for old, new in [("－", "-"), ("–", "-"), ("—", "-"), ("，", ","), ("：", ":"), ("；", ";"), ("（", "("), ("）", ")")]:
        s = s.replace(old, new)
    s = re.sub(r"\s*-\s*", "-", s)
    return s

def build_customer_key(name: str, addr: str) -> str:
    return normalize_name(name) + "|||" + normalize_address(addr)

def normalize_name_for_compare(name: str) -> str:
    s = normalize_name(name)
    s = re.sub(r"[－–—─]", "-", s)
    s = re.sub(r"\s*-.*$", "", s)
    return s.strip()

def get_weekday_text(dt: datetime) -> str:
    return ["一", "二", "三", "四", "五", "六", "日"][dt.weekday()]

def get_price_by_date(dt: datetime) -> int:
    return 700 if dt.weekday() >= 5 else 600  # 六日 700，平日 600

def parse_service_people(service_text: str) -> int:
    m = re.search(r"(\d+)\s*人", str(service_text or ""))
    return int(m.group(1)) if m else 2

def calc_hours(start: datetime, end: datetime) -> float:
    diff = (end - start).total_seconds() / 3600
    return max(diff, 0.0)

def parse_title(title: str) -> dict:
    service_m = re.search(r"《(.*?)》", title)
    note_m    = re.search(r"<(.*?)>", title)
    service   = service_m.group(1) if service_m else ""
    note      = note_m.group(1) if note_m else ""
    cleaned   = re.sub(r"《.*?》|<.*?>", "", title).strip()
    parts     = [p.strip() for p in cleaned.split(",")]
    return {
        "service": service,
        "note":    note,
        "name":    parts[0] if parts else "",
        "phone":   parts[1] if len(parts) > 1 else "",
    }

def get_status(color: str) -> str:
    if color == COLOR_PAUSE:      return "暫停"
    if color == COLOR_UNARRANGED: return "未安排"
    return "已安排"

def to_number_safe(value: Any) -> float:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


# ──────────────────────────────────────────────────────────
# Step 2：匯出定期VIP日曆
# ──────────────────────────────────────────────────────────

def _load_stored_value_info(gc: gspread.Client, area_name: str, area_target_id: str = "") -> dict[str, dict]:
    """
    從各區固定試算表內最新的「{地區}儲值金結算_YYYYMMDD」讀取
    姓名 → {totalBalance, lineValue}。
    """
    target_id = STORED_VALUE_SPREADSHEET_IDS.get(area_name, "")
    if not target_id:
        return {}
    try:
        ss = gc.open_by_key(target_id)
        pattern = re.compile(rf"^{re.escape(area_name)}儲值金結算_\d{{8}}$")
        candidates = [worksheet for worksheet in ss.worksheets() if pattern.fullmatch(worksheet.title)]
        if not candidates:
            raise gspread.WorksheetNotFound(
                f"找不到 {area_name}儲值金結算_YYYYMMDD 工作表"
            )
        sh = max(candidates, key=lambda worksheet: worksheet.title)
        records = sh.get_all_records()
    except Exception as e:
        log.warning("[%s] 讀取儲值金資料失敗（非致命）：%s", area_name, e)
        return {}

    name_map: dict[str, dict] = {}
    for r in records:
        # 嘗試各種欄位名稱
        raw_name = r.get("客戶姓名") or r.get("name") or r.get("customer_name") or ""
        line_val = r.get("LINE@") or r.get("line_url") or r.get("line_id") or ""
        stored   = to_number_safe(r.get("剩餘儲值金") or r.get("stored_value") or r.get("stored_balance") or 0)
        shopping = to_number_safe(r.get("剩餘購物金") or r.get("shopping_value") or r.get("shopping_balance") or 0)
        name_key = normalize_name_for_compare(raw_name)
        if not name_key:
            continue
        if name_key not in name_map:
            name_map[name_key] = {"totalBalance": 0.0, "lineValue": ""}
        name_map[name_key]["totalBalance"] += stored + shopping
        if not name_map[name_key]["lineValue"] and line_val:
            name_map[name_key]["lineValue"] = str(line_val)

    return name_map


def _fetch_calendar_events(
    cal_service,
    calendar_id: str,
    start_dt: datetime,
    end_dt: datetime,
    area_name: str,
) -> list:
    """從 Google Calendar API 抓行程。"""
    if not calendar_id:
        raise ValueError(f"[{area_name}] Calendar ID 未設定，請在主控試算表「客服地區設定」填入")

    time_min = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    events = []
    page_token = None
    while True:
        kwargs: dict = {
            "calendarId":  calendar_id,
            "timeMin":     time_min,
            "timeMax":     time_max,
            "singleEvents": True,
            "orderBy":     "startTime",
            "maxResults":  2500,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        resp  = cal_service.events().list(**kwargs).execute()
        items = resp.get("items", [])
        events.extend(items)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    log.info("[%s] 讀取到 %d 筆行事曆行程", area_name, len(events))
    return events


def _process_events(events: list, area_name: str) -> list[dict]:
    """
    篩選、解析行程，對應 GAS exportCalendarWithRange 核心邏輯：
    - 排除 COLOR_INTERNAL 色碼的內部行程
    - 只保留含手機號碼的行程
    - 同客戶+地址+日期去重
    """
    rows = []
    seen_keys: set[str] = set()

    for e in events:
        color = e.get("colorId", "")
        title = e.get("summary", "")

        # 排除內部行程
        if color == COLOR_INTERNAL:
            continue

        # 只保留有手機號碼的
        if not re.search(r"09\d{8}", title):
            continue

        parsed   = parse_title(title)
        status   = get_status(color)
        location = e.get("location", "")

        start_raw = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")
        end_raw   = e.get("end",   {}).get("dateTime") or e.get("end",   {}).get("date", "")

        try:
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(TZ_TAIPEI)
            end_dt   = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(TZ_TAIPEI)
        except Exception:
            log.debug("[%s] 無法解析行程時間：%s / %s", area_name, start_raw, end_raw)
            continue

        date_str  = start_dt.strftime("%Y/%m/%d")
        start_str = start_dt.strftime("%H:%M")
        end_str   = end_dt.strftime("%H:%M")

        # 去重 key
        unique_key = normalize_name(parsed["name"]) + "|||" + normalize_address(location) + "|||" + date_str
        if unique_key in seen_keys:
            continue
        seen_keys.add(unique_key)

        weekday    = get_weekday_text(start_dt)
        price      = get_price_by_date(start_dt)
        people     = parse_service_people(parsed["service"])
        hours      = calc_hours(start_dt, end_dt)
        person_hrs = people * hours
        amount     = price * person_hrs

        rows.append({
            "service":    parsed["service"],
            "note":       parsed["note"],
            "name":       normalize_name(parsed["name"]),
            "phone":      parsed["phone"],
            "address":    location,
            "start_dt":   start_dt,
            "date_str":   date_str,
            "start_str":  start_str,
            "end_str":    end_str,
            "status":     status,
            "weekday":    weekday,
            "price":      price,
            "person_hrs": person_hrs,
            "amount":     amount,
            # 以下由儲值金表回填
            "subtotal":   0.0,
            "balance":    0.0,
            "diff":       0.0,
            "line":       "",
        })

    return rows


def _enrich_with_stored_value(rows: list[dict], stored_info: dict[str, dict]) -> list[dict]:
    """計算各客戶當月小計，並從儲值金表帶入餘額。"""
    # 先算各人當月小計
    subtotal_map: dict[str, float] = {}
    for r in rows:
        key = normalize_name_for_compare(r["name"])
        subtotal_map[key] = subtotal_map.get(key, 0.0) + r["amount"]

    for r in rows:
        key  = normalize_name_for_compare(r["name"])
        info = stored_info.get(key, {})
        r["subtotal"] = subtotal_map.get(key, 0.0)
        r["balance"]  = info.get("totalBalance", 0.0)
        r["diff"]     = r["balance"] - r["subtotal"]
        r["line"]     = info.get("lineValue", "")

    return rows


def _write_vip_sheet(
    gc: gspread.Client,
    area_name: str,
    rows: list[dict],
    start_dt: datetime,
    area_target_id: str = "",
) -> str:
    """寫入地區目標試算表「定期VIP_{地區}_{yyyyMM}」工作表，回傳工作表名稱。"""
    target_id = area_target_id or _load_target_file_id()
    if not target_id:
        raise EnvironmentError(f"[{area_name}] 請在客服地區設定填入「目標試算表ID」")

    ss         = gc.open_by_key(target_id)
    sheet_name = f"定期VIP_{area_name}_{start_dt.strftime('%Y%m')}"

    try:
        sh = ss.worksheet(sheet_name)
        sh.clear()
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=sheet_name, rows=max(len(rows) + 10, 200), cols=20)

    headers = [
        "服務人時", "備註", "姓名", "電話", "地址",
        "日期", "開始時間", "結束時間", "狀態", "",
        "星期", "單價", "人時", "金額",
        "當月預約總金額", "儲值金餘額", "差額", "LINE@",
    ]

    out = [headers]
    shown_names: set[str] = set()

    for r in rows:
        compare_name = normalize_name_for_compare(r["name"])
        is_first     = compare_name not in shown_names
        if is_first:
            shown_names.add(compare_name)

        out.append([
            r["service"], r["note"], r["name"], r["phone"], r["address"],
            r["date_str"], r["start_str"], r["end_str"], r["status"], "",
            r["weekday"], r["price"], r["person_hrs"], r["amount"],
            r["subtotal"] if is_first else "",
            r["balance"]  if is_first else "",
            r["diff"]     if is_first else "",
            r["line"]     if is_first else "",
        ])

    sh.update(values=out, range_name="A1", value_input_option="USER_ENTERED")
    sh.freeze(rows=1)

    log.info("[%s] 定期VIP工作表寫入完成：%d 筆，工作表：%s", area_name, len(rows), sheet_name)
    return sheet_name


def step2_export_vip_calendar(
    gc: gspread.Client,
    areas: list[dict],
    run_id: str,
    start_dt: datetime,
    end_dt: datetime,
) -> dict:
    """Step 2：各地區讀取 Google Calendar → 整理後寫入試算表。"""
    task = "Step2_匯出定期VIP日曆"
    t0   = now_tp()
    checkin_both(gc, run_id, task, "START", "RUNNING")

    cal_service = _calendar_service()
    results     = {}
    errors      = []

    for area in areas:
        area_name  = area["name"]
        calendar_id = area["calendar_id"]
        log.info("=== [%s] 匯出定期VIP日曆 ===", area_name)

        try:
            # 抓行程
            events = _fetch_calendar_events(cal_service, calendar_id, start_dt, end_dt, area_name)
            rows   = _process_events(events, area_name)

            if not rows:
                log.warning("[%s] 指定日期範圍內無符合行程", area_name)
                results[area_name] = {"count": 0, "sheet": "", "ok": True}
                continue

            # 排序：姓名 → 日期 → 開始時間
            rows.sort(key=lambda r: (r["name"], r["start_dt"], r["start_str"]))

            # 帶入儲值金資料
            _area_target = area.get("target_spreadsheet_id", "")
            stored_info = _load_stored_value_info(gc, area_name, area_target_id=_area_target)
            rows        = _enrich_with_stored_value(rows, stored_info)

            # 寫入工作表
            sheet_name = _write_vip_sheet(gc, area_name, rows, start_dt, area_target_id=_area_target)
            results[area_name] = {"count": len(rows), "sheet": sheet_name, "ok": True}

        except Exception as e:
            log.error("[%s] VIP日曆匯出失敗：%s", area_name, e)
            errors.append(f"{area_name}: {e}")
            results[area_name] = {"ok": False, "error": str(e)}

    elapsed = (now_tp() - t0).total_seconds()
    status  = "ERROR" if errors else "SUCCESS"
    note    = json.dumps(results, ensure_ascii=False)[:300]
    checkin_both(gc, run_id, task, "DONE", status, note, elapsed)

    if errors:
        raise RuntimeError("部分地區VIP日曆匯出失敗：\n" + "\n".join(errors))

    return results


# ──────────────────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────────────────

def _load_target_file_id() -> str:
    """依序從 Secret 或主控試算表系統設定取得目標試算表 ID。"""
    val = os.environ.get("SERVICE_TARGET_SPREADSHEET_ID", "").strip()
    if val:
        log.info("目標試算表 ID 來源：環境變數 SERVICE_TARGET_SPREADSHEET_ID")
        return val
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
        log.warning("從主控試算表讀取目標 ID 失敗：%s", e)
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="檸檬家事 CRM 客服系統")
    parser.add_argument("--step",  type=int, choices=[0, 1, 2], default=0,
                        help="0=全跑, 1=只抓儲值金, 2=只匯出VIP日曆（預設：0）")
    parser.add_argument(
        "--area",
        type=str,
        choices=list(SERVICE_AREA_GROUPS),
        default="全區",
        help="執行區域：全區（台北＋台中）、台北或台中；預設為全區",
    )
    parser.add_argument("--start", type=str, default="",
                        help="匯出起始日期 YYYY-MM-DD（預設：本月1日）")
    parser.add_argument("--end",   type=str, default="",
                        help="匯出結束日期 YYYY-MM-DD（預設：本月最後一天）")
    args = parser.parse_args()

    if args.step in (0, 2) and not (_load_target_file_id()):
        sys.exit("❌ 請在主控試算表「系統設定」填入客服排程系統的共用雲端資料夾ID，或設定 Secret SERVICE_TARGET_SPREADSHEET_ID")

    # 日期範圍
    today = now_tp()
    if args.start:
        start_dt = datetime.fromisoformat(args.start).replace(
            hour=0, minute=0, second=0, tzinfo=TZ_TAIPEI
        )
    else:
        start_dt = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if args.end:
        end_dt = datetime.fromisoformat(args.end).replace(
            hour=23, minute=59, second=59, tzinfo=TZ_TAIPEI
        )
    else:
        # 本月最後一天
        if today.month == 12:
            last_day = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_day = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        end_dt = last_day.replace(hour=23, minute=59, second=59)

    run_id = str(uuid.uuid4())[:8]
    log.info("=== CRM 客服系統 run_id=%s area=%s step=%s %s ~ %s ===",
             run_id, args.area, args.step or "ALL",
             fmt(start_dt, "%Y-%m-%d"), fmt(end_dt, "%Y-%m-%d"))

    gc    = _gc()
    areas = load_area_config(gc, filter_area=args.area)

    _SERVICE_LOG_CONTEXT["area"] = args.area
    _SERVICE_LOG_CONTEXT["period"] = (
        f"{fmt(start_dt, '%Y-%m-%d')}~{fmt(end_dt, '%Y-%m-%d')}"
    )
    _SERVICE_LOG_CONTEXT["run_feature"] = {
        0: "CRM／儲值全部執行",
        1: "【儲值】抓儲值金",
        2: "【CRM】產生 CRM",
    }[args.step]

    if not areas:
        sys.exit(f"❌ 沒有啟用的地區設定（filter={args.area}），請檢查主控試算表「客服地區設定」")

    checkin_both(gc, run_id, "RUN", "START", "RUNNING",
                 f"area={args.area} step={args.step or 'ALL'}", 0)

    t_total = now_tp()
    errors  = []

    try:
        if args.step in (0, 1):
            log.info("--- Step 1：抓取儲值金 ---")
            try:
                step1_fetch_stored_value(gc, areas, run_id)
            except Exception as e:
                errors.append(str(e))
                log.error("Step 1 失敗：%s", e)

        if args.step in (0, 2):
            log.info("--- Step 2：匯出定期VIP日曆 ---")
            # Step 2 需要有 Calendar ID 的地區
            areas_with_cal = [a for a in areas if a["calendar_id"]]
            if not areas_with_cal:
                log.warning("所有地區均未設定 Calendar ID，跳過 Step 2")
                log.warning("請在主控試算表「客服地區設定」填入各地區的 Calendar ID")
            else:
                try:
                    step2_export_vip_calendar(gc, areas_with_cal, run_id, start_dt, end_dt)
                except Exception as e:
                    errors.append(str(e))
                    log.error("Step 2 失敗：%s", e)

    finally:
        elapsed_total = (now_tp() - t_total).total_seconds()
        final_status  = "ERROR" if errors else "SUCCESS"
        final_note    = "; ".join(errors) if errors else "全部完成"
        checkin_both(gc, run_id, "RUN", "DONE", final_status, final_note[:300], elapsed_total)

    if errors:
        log.error("執行結束（有失敗）")
        sys.exit(1)

    log.info("=== 全部完成 run_id=%s（%.1fs）===", run_id, (now_tp() - t_total).total_seconds())


if __name__ == "__main__":
    main()
