"""
檸檬家事客服排程－儲值功能
tools/service_management/stored_value.py

最後更新：2026-10-20

Change Log：
- 2026-10-21：排程預約條件改由執行畫面勾選（--booking-gate），期間跟著匯出起迄日。
- 2026-10-20：建立VIP排程工作表時套用主控試算表「排程預約條件」（例如年節大掃除 V 欄 TRUE 才預約、A 欄含改／加先不約）。
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

import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

TZ_TAIPEI = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BACKEND_BASE = "https://backend.lemonclean.com.tw"
LOGIN_URL = f"{BACKEND_BASE}/login"
STORED_VALUE_EXPORT_URL = f"{BACKEND_BASE}/member/export_stored_value"

STORED_VALUE_SPREADSHEET_IDS: dict[str, str] = {
    "台北": "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE",
    "台中": "1de41gNvBZCGdfy0qNouRNEaQD7R019VAvz2cfq88ZrE",
}

AREA_SHEET_NAME    = "客服地區設定"
AREA_SHEET_HEADERS = ["地區名稱", "Calendar ID", "目標試算表ID", "啟用"]

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

SERVICE_AREA_GROUPS: dict[str, tuple[str, ...]] = {
    "全區": ("台北", "台中"),
    "台北": ("台北",),
    "台中": ("台中",),
}

# Google Calendar 事件標籤顏色（colorId）→ 排程狀態。
# 只接受明確指定的五種標籤；其他顏色或未標籤一律留白，不得推定為「已安排」。
COLOR_PENDING_CONFIRM = "11"  # 紅：待確認
COLOR_SCHEDULED       = "5"   # 黃：已安排
COLOR_PAUSE           = "10"  # 綠：暫停
COLOR_RESERVED        = "7"   # 藍：保留單
COLOR_UNARRANGED      = "3"   # 紫：未安排
STATUS_BY_COLOR = {
    COLOR_PENDING_CONFIRM: "待確認",
    COLOR_SCHEDULED: "已安排",
    COLOR_PAUSE: "暫停",
    COLOR_RESERVED: "保留單",
    COLOR_UNARRANGED: "未安排",
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _get_credentials() -> Credentials:
    try:
        from tools.common.config_loader import get_service_account_info
        info = get_service_account_info()
    except Exception as e:
        raise EnvironmentError(f"無法取得 GOOGLE_SERVICE_ACCOUNT：{e}")
    return Credentials.from_service_account_info(info, scopes=SCOPES)

def _gc() -> gspread.Client:
    # This module runs in a separate Python subprocess.  The retry patch that
    # toolapp installs in the Streamlit process is not inherited by this one.
    from services.google_api_retry import install_googleapiclient_retry, install_gspread_retry
    install_gspread_retry()
    install_googleapiclient_retry()
    return gspread.authorize(_get_credentials())

def _calendar_service():
    return build("calendar", "v3", credentials=_get_credentials())


def now_tp() -> datetime:
    return datetime.now(TZ_TAIPEI)

def fmt(dt: datetime, f: str) -> str:
    return dt.strftime(f)

def today_tp() -> date:
    return now_tp().date()


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


def _ensure_area_sheet(gc: gspread.Client) -> gspread.Worksheet:
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
    return AREA_TO_SECRET_PREFIX.get(area_name, area_name.upper().replace(" ", "_"))

def get_area_credentials(area_name: str) -> tuple[str, str]:
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


class _CsrfTokenParser(HTMLParser):
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
    """登入檸檬後台（共用 tools/common/lemon_backend_login；程式結束時自動登出）。"""
    from tools.common.lemon_backend_login import login as backend_login

    session = requests.Session()
    try:
        backend_login(session, email, password, login_url=LOGIN_URL,
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    except RuntimeError as exc:
        raise RuntimeError(f"[{area_name}] 後台登入失敗：{exc}") from exc
    return session


def _download_stored_value_export(session: requests.Session, area_name: str) -> bytes:
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


def _convert_stored_value_export(
    gc: gspread.Client,
    content: bytes,
    area_name: str,
) -> list[list[Any]]:
    drive = build("drive", "v3", credentials=_get_credentials(), cache_discovery=False)
    token = uuid.uuid4().hex[:8]
    source_id = ""
    converted_id = ""
    try:
        source = drive.files().create(
            body={"name": f"_tmp_{area_name}儲值金結算_{token}.xlsx"},
            media_body=MediaIoBaseUpload(
                BytesIO(content),
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                resumable=False,
            ),
            fields="id",
        ).execute()
        source_id = source["id"]
        converted = drive.files().copy(
            fileId=source_id,
            body={
                "name": f"_tmp_{area_name}儲值金結算_{token}",
                "mimeType": "application/vnd.google-apps.spreadsheet",
            },
            fields="id",
        ).execute()
        converted_id = converted["id"]

        worksheets = gc.open_by_key(converted_id).worksheets()
        if not worksheets:
            raise RuntimeError("轉檔後沒有工作表")
        values = worksheets[0].get_all_values()
        if not values:
            raise RuntimeError("轉檔後沒有資料")
        return values
    except Exception as exc:
        raise RuntimeError(f"[{area_name}] 儲值金結算檔轉檔失敗：{exc}") from exc
    finally:
        for file_id in (converted_id, source_id):
            if not file_id:
                continue
            try:
                drive.files().update(
                    fileId=file_id,
                    body={"trashed": True},
                    fields="id",
                ).execute()
            except Exception as cleanup_exc:
                log.warning("[%s] 暫存轉檔清理失敗：%s", area_name, cleanup_exc)


def _write_stored_value_sheet(
    gc: gspread.Client,
    area_name: str,
    values: list[list[Any]],
) -> gspread.Worksheet:
    target_id = STORED_VALUE_SPREADSHEET_IDS.get(area_name, "")
    if not target_id:
        raise EnvironmentError(f"[{area_name}] 尚未設定儲值金結算目的試算表")

    ss = gc.open_by_key(target_id)
    sheet_name = f"{area_name}儲值金結算_{fmt(now_tp(), '%Y%m%d')}"
    pattern = re.compile(rf"^{re.escape(area_name)}儲值金結算_\d{{8}}$")
    candidates = [worksheet for worksheet in ss.worksheets() if pattern.fullmatch(worksheet.title)]

    if candidates:
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
            log.info("[%s] 登入後台中…", area_name)
            session = _login_backend_session(email, password, area_name)
            log.info("[%s] 下載儲值金報表中…", area_name)
            content = _download_stored_value_export(session, area_name)
            log.info("[%s] 報表轉檔中…", area_name)
            values = _convert_stored_value_export(gc, content, area_name)
            log.info("[%s] 寫入儲值金工作表中…", area_name)
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

def get_price_by_date(dt: datetime, deep_clean_setting: Any = None) -> float:
    """Return the per-person-hour price, including VIP deep-clean surcharge."""
    regular_price = 700 if dt.weekday() >= 5 else 600
    if deep_clean_setting is None:
        return regular_price

    if deep_clean_setting.phase1_start <= dt <= deep_clean_setting.phase1_end:
        surcharge = (
            deep_clean_setting.phase1_weekend_rate
            if dt.weekday() >= 5 else deep_clean_setting.phase1_weekday_rate
        )
    elif deep_clean_setting.phase2_start <= dt <= deep_clean_setting.phase2_end:
        surcharge = (
            deep_clean_setting.phase2_weekend_rate
            if dt.weekday() >= 5 else deep_clean_setting.phase2_weekday_rate
        )
    else:
        return regular_price

    # 大掃除期間平日、週末的基準皆為每 2 人 1 小時 NT$1,200；
    # 設定中的 VIP 加價也是每 2 人 1 小時，因此換算為每人時後相加。
    return 600 + (float(surcharge) / 2)


def _deep_clean_candidate_years(start_dt: datetime, end_dt: datetime) -> list[int]:
    # A season named 2026 covers the end of 2026 and early 2027.  Do not scan
    # unrelated old/future Drive folders for an October-only export.
    candidate_years = set(range(start_dt.year, end_dt.year + 1))
    if start_dt.month <= 3:
        candidate_years.add(start_dt.year - 1)
    if end_dt.month < 10:
        candidate_years.discard(end_dt.year)
    return sorted(candidate_years)


def _load_deep_clean_pricing_settings(start_dt: datetime, end_dt: datetime) -> list[Any]:
    """Load every annual deep-clean setting that could overlap an export range."""
    from tools.service_management.deep_clean_notice import (
        DEFAULT_MASTER_SPREADSHEET_ID,
        load_deep_clean_settings,
        resolve_deep_clean_sheet_ids,
    )

    settings = []
    for season_year in _deep_clean_candidate_years(start_dt, end_dt):
        log.info("讀取 %d 年度大掃除計價設定中…", season_year)
        try:
            settings_sheet_id, _ = resolve_deep_clean_sheet_ids(
                season_year, DEFAULT_MASTER_SPREADSHEET_ID,
            )
        except ValueError:
            continue
        try:
            setting = load_deep_clean_settings(season_year, settings_sheet_id)
        except Exception as exc:
            raise RuntimeError(
                f"讀取 {season_year} 年度大掃除設定失敗；為避免儲值金扣抵錯誤，已停止匯出：{exc}"
            ) from exc
        settings.append(setting)
        log.info("%d 年度大掃除計價設定讀取完成", season_year)
    return settings

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

def normalize_phone(phone: Any) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 9 and digits.startswith("9"):
        digits = "0" + digits
    return digits if len(digits) == 10 else str(phone or "").strip()

def get_status(color: str) -> str:
    return STATUS_BY_COLOR.get(str(color or "").strip(), "")


def _calendar_label_statuses(calendar: dict) -> dict[str, str]:
    names = {
        "待確認": "待確認", "已安排": "已安排", "已預約": "已安排",
        "暫停": "暫停", "保留單": "保留單", "未安排": "未安排",
    }
    return {
        label["id"]: names.get(str(label.get("name", "")).strip(), "")
        for label in calendar.get("labelProperties", {}).get("eventLabels", [])
        if label.get("id")
    }


def _event_schedule_status(event: dict, label_statuses: dict[str, str]) -> str:
    label_id = event.get("eventLabelId")
    if label_id:
        # 自訂標籤優先；未知標籤不得再以相近的舊色碼推定狀態。
        return label_statuses.get(label_id, "")
    return get_status(event.get("colorId", ""))

def to_number_safe(value: Any) -> float:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _load_stored_value_info(gc: gspread.Client, area_name: str, area_target_id: str = "") -> dict[str, dict]:
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
        log.info("[%s] 讀取儲值金餘額中：%s", area_name, sh.title)
        records = sh.get_all_records()
    except Exception as e:
        log.warning("[%s] 讀取儲值金資料失敗（非致命）：%s", area_name, e)
        return {}

    name_map: dict[str, dict] = {}
    for r in records:
        raw_name = r.get("客戶姓名") or r.get("name") or r.get("customer_name") or ""
        line_val = r.get("LINE@") or r.get("line_url") or r.get("line_id") or ""
        email_val = r.get("Email") or r.get("email") or r.get("電子信箱") or r.get("信箱") or ""
        stored   = to_number_safe(r.get("剩餘儲值金") or r.get("stored_value") or r.get("stored_balance") or 0)
        shopping = to_number_safe(r.get("剩餘購物金") or r.get("shopping_value") or r.get("shopping_balance") or 0)
        name_key = normalize_name_for_compare(raw_name)
        if not name_key:
            continue
        if name_key not in name_map:
            name_map[name_key] = {"totalBalance": 0.0, "lineValue": "", "email": ""}
        name_map[name_key]["totalBalance"] += stored + shopping
        if not name_map[name_key]["lineValue"] and line_val:
            name_map[name_key]["lineValue"] = str(line_val)
        if not name_map[name_key]["email"] and email_val:
            name_map[name_key]["email"] = str(email_val).strip()

    log.info("[%s] 儲值金餘額讀取完成：%d 位", area_name, len(name_map))
    return name_map


def _contiguous_row_ranges(row_numbers: list[int], start_col: str = "A", end_col: str = "S") -> list[str]:
    """Compress row numbers so one formatting request covers each consecutive run."""
    numbers = sorted(set(row_numbers))
    if not numbers:
        return []
    ranges = []
    start = previous = numbers[0]
    for row_number in numbers[1:]:
        if row_number == previous + 1:
            previous = row_number
            continue
        ranges.append(f"{start_col}{start}:{end_col}{previous}")
        start = previous = row_number
    ranges.append(f"{start_col}{start}:{end_col}{previous}")
    return ranges


def _fetch_calendar_events(
    cal_service,
    calendar_id: str,
    start_dt: datetime,
    end_dt: datetime,
    area_name: str,
) -> list:
    if not calendar_id:
        raise ValueError(f"[{area_name}] Calendar ID 未設定，請在主控試算表「客服地區設定」填入")

    calendar = cal_service.calendars().get(calendarId=calendar_id).execute()
    label_statuses = _calendar_label_statuses(calendar)

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
        for event in items:
            event["_schedule_status"] = _event_schedule_status(event, label_statuses)
        events.extend(items)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    log.info("[%s] 讀取到 %d 筆行事曆行程", area_name, len(events))
    return events


def _process_events(
    events: list,
    area_name: str,
    calendar_id: str = "",
    deep_clean_settings: Any = (),
) -> list[dict]:
    rows = []
    seen_keys: set[str] = set()

    for e in events:
        color = e.get("colorId", "")
        title = e.get("summary", "")

        if not re.search(r"09\d{8}", title):
            continue

        parsed   = parse_title(title)
        status   = e.get("_schedule_status", get_status(color))
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

        unique_key = (e.get("_calendar_event_key") or e.get("id")
                      or "|||".join((normalize_name(parsed["name"]), normalize_address(location), date_str, start_str, end_str)))
        if unique_key in seen_keys:
            continue
        seen_keys.add(unique_key)

        weekday = get_weekday_text(start_dt)
        pricing_setting = next(
            (
                setting for setting in deep_clean_settings
                if setting.phase1_start <= start_dt <= setting.phase2_end
            ),
            None,
        )
        lunar_pause = next(
            (
                setting for setting in deep_clean_settings
                if setting.lunar_new_year_start
                and setting.lunar_new_year_end
                and setting.lunar_new_year_start <= start_dt <= setting.lunar_new_year_end
            ),
            None,
        )
        if lunar_pause:
            status = "暫停"
        price      = get_price_by_date(start_dt, pricing_setting)
        people     = parse_service_people(parsed["service"])
        hours      = calc_hours(start_dt, end_dt)
        person_hrs = people * hours
        # 暫停不收費，金額空白；未安排、待確認等其他狀態照常計入。
        amount     = "" if status == "暫停" else price * person_hrs

        rows.append({
            "event_id":   f"{calendar_id or area_name}:{e['id']}" if e.get("id") else "",
            "service":    parsed["service"],
            "note":       parsed["note"],
            "name":       normalize_name(parsed["name"]),
            "phone":      normalize_phone(parsed["phone"]),
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
            "subtotal":   0.0,
            "balance":    0.0,
            "diff":       0.0,
            "line":       "",
        })

    return rows


def _enrich_with_stored_value(rows: list[dict], stored_info: dict[str, dict]) -> list[dict]:
    subtotal_map: dict[tuple[str, str], float] = {}
    for r in rows:
        key = normalize_name_for_compare(r["name"])
        period = r["start_dt"].strftime("%Y%m")
        subtotal_map[(key, period)] = subtotal_map.get((key, period), 0.0) + float(r["amount"] or 0)

    cumulative_map: dict[tuple[str, str], float] = {}
    running_by_name: dict[str, float] = {}
    for key, period in sorted(subtotal_map, key=lambda item: (item[0], item[1])):
        running_by_name[key] = running_by_name.get(key, 0.0) + subtotal_map[(key, period)]
        cumulative_map[(key, period)] = running_by_name[key]

    for r in rows:
        key  = normalize_name_for_compare(r["name"])
        period = r["start_dt"].strftime("%Y%m")
        info = stored_info.get(key, {})
        r["subtotal"] = subtotal_map.get((key, period), 0.0)
        r["balance"]  = info.get("totalBalance", 0.0)
        r["diff"]     = r["balance"] - cumulative_map.get((key, period), 0.0)
        r["line"]     = info.get("lineValue", "")

    return _mark_topup_rows(rows)


TOPUP_FLAG = "需儲值"
# 已安排（已成單，金額已從餘額扣除）與暫停不計入需儲值累計。
TOPUP_SKIP_STATUSES = ("已安排", "暫停")


def _mark_topup_rows(rows: list[dict]) -> list[dict]:
    """同一客人依服務日期累計金額，超過儲值金餘額的那幾次標「需儲值」。"""
    by_customer: dict[str, list[dict]] = {}
    for r in rows:
        r["topup"] = ""
        if r.get("status") in TOPUP_SKIP_STATUSES or not r.get("amount"):
            continue
        by_customer.setdefault(normalize_name_for_compare(r["name"]), []).append(r)
    for customer_rows in by_customer.values():
        running = 0.0
        for r in sorted(customer_rows, key=lambda row: (row["start_dt"], row.get("start_str", ""))):
            running += float(r["amount"] or 0)
            if running > float(r.get("balance") or 0) + 1e-9:
                r["topup"] = TOPUP_FLAG
    return rows


TOPUP_LIST_HEADERS = [
    "姓名", "電話", "地址", "備註", "需儲值次數", "需儲值日期", "期間預約總金額", "儲值金餘額", "差額", "LINE@",
    "通知狀態", "通知備註",
]
TOPUP_MANUAL_HEADERS = ("通知狀態", "通知備註")


def build_topup_list(rows: list[dict]) -> list[list[Any]]:
    """依客人整理需儲值名單（只列有「需儲值」的客人），依第一次約不到的日期排序。"""
    customers: dict[str, dict] = {}
    for r in sorted(rows, key=lambda row: (row["start_dt"], row.get("start_str", ""))):
        key = normalize_name_for_compare(r["name"])
        counted = r.get("status") not in TOPUP_SKIP_STATUSES and r.get("amount")
        item = customers.setdefault(key, {"row": r, "notes": [], "dates": [], "total": 0.0, "first": None})
        if r.get("note") and r["note"] not in item["notes"]:
            item["notes"].append(r["note"])
        if counted:
            item["total"] += float(r["amount"] or 0)
        if r.get("topup"):
            item["dates"].append(r["start_dt"].strftime("%m/%d"))
            item["first"] = item["first"] or r["start_dt"]
    result = []
    for item in sorted((i for i in customers.values() if i["dates"]), key=lambda i: i["first"]):
        r, balance = item["row"], float(item["row"].get("balance") or 0)
        result.append([
            r["name"], r["phone"], r["address"], "、".join(item["notes"]), len(item["dates"]),
            "、".join(item["dates"]), item["total"], balance, balance - item["total"], r.get("line", ""), "", "",
        ])
    return result


def _write_topup_list(ss: Any, sheet_name: str, rows: list[dict]) -> str:
    """每次匯出重新產生需儲值名單；手動填的通知狀態／通知備註依姓名＋電話保留。"""
    list_rows = build_topup_list(rows)
    try:
        sh = ss.worksheet(sheet_name)
        old = sh.get_all_values()
    except gspread.WorksheetNotFound:
        sh, old = None, []
    if old:
        header = old[0]
        index = {name: header.index(name) for name in ("姓名", "電話", *TOPUP_MANUAL_HEADERS) if name in header}
        kept = {}
        for row in old[1:]:
            cells = row + [""] * len(header)
            if "姓名" in index and "電話" in index:
                kept[(normalize_name_for_compare(cells[index["姓名"]]), normalize_phone(cells[index["電話"]]))] = [
                    cells[index[name]] if name in index else "" for name in TOPUP_MANUAL_HEADERS]
        for row in list_rows:
            manual = kept.get((normalize_name_for_compare(row[0]), normalize_phone(row[1])))
            if manual:
                row[-len(TOPUP_MANUAL_HEADERS):] = manual
    if sh is None:
        if not list_rows:
            return ""
        sh = ss.add_worksheet(title=sheet_name, rows=max(len(list_rows) + 20, 100), cols=len(TOPUP_LIST_HEADERS))
    sh.clear()
    sh.update(values=[TOPUP_LIST_HEADERS] + list_rows, range_name="A1", value_input_option="RAW")
    sh.freeze(rows=1)
    log.info("需儲值名單更新完成：%s（%d 位）", sheet_name, len(list_rows))
    return sheet_name


def _write_vip_sheet(
    gc: gspread.Client,
    area_name: str,
    rows: list[dict],
    start_dt: datetime,
    area_target_id: str = "",
    end_dt=None,
    sheet_name: str = "",
) -> str:
    target_id = area_target_id or _load_target_file_id()
    if not target_id:
        raise EnvironmentError(f"[{area_name}] 請在客服地區設定填入「目標試算表ID」")

    ss         = gc.open_by_key(target_id)
    sheet_name = sheet_name or f"定期VIP_{area_name}_{start_dt.strftime('%Y%m')}"

    try:
        sh = ss.worksheet(sheet_name)
        sh.clear()
    except gspread.WorksheetNotFound:
        sh = ss.add_worksheet(title=sheet_name, rows=max(len(rows) + 10, 200), cols=22)

    if getattr(sh, "col_count", 22) < 22:
        sh.resize(cols=22)

    headers = [
        "服務人時", "備註", "姓名", "電話", "地址",
        "日期", "開始時間", "結束時間", "狀態", "",
        "星期", "單價", "人時", "金額",
        "當月預約總金額", "儲值金餘額", "累計差額（截至當月）", "LINE@", "日曆事件ID",
    ]

    # 姓氏總筆劃 → 姓名（去掉「-4樓」等後綴）→ 地址 → 日期 → 開始時間；排程工作表排序用同一規則。
    from tools.common.stroke_order import name_sort_key
    rows = sorted(
        rows,
        key=lambda r: (
            name_sort_key(normalize_name_for_compare(r["name"])),
            normalize_address(r["address"]),
            r["start_dt"],
            r["start_str"],
        ),
    )

    out = [headers]
    shown_customer_months: set[tuple[str, str]] = set()

    for r in rows:
        compare_name = normalize_name_for_compare(r["name"])
        customer_month = (compare_name, r["start_dt"].strftime("%Y%m"))
        is_first     = customer_month not in shown_customer_months
        if is_first:
            shown_customer_months.add(customer_month)

        out.append([
            r["service"], r["note"], r["name"], normalize_phone(r["phone"]), r["address"],
            r["date_str"], r["start_str"], r["end_str"], r["status"], "",
            r["weekday"], r["price"], r["person_hrs"], r["amount"],
            r["subtotal"] if is_first else "",
            r["balance"]  if is_first else "",
            r["diff"]     if is_first else "",
            r["line"]     if is_first else "",
            r.get("event_id", ""),
        ])

    log.info("[%s] 寫入定期VIP工作表中：%d 筆 → %s", area_name, len(rows), sheet_name)
    # These are snapshot values, not formulas. RAW avoids locale/formula parsing,
    # preserves leading-zero phone numbers, and removes a second write request.
    sh.update(values=out, range_name="A1", value_input_option="RAW")
    log.info("[%s] 定期VIP主資料寫入完成，套用格式中…", area_name)
    if rows:
        # Rows already sorted above; keep their positions for duplicate highlighting.
        from tools.service_management.vip_schedule_diff import _same_day_rows
        formats = [{"range": f"A2:S{len(rows) + 1}",
                    "format": {"backgroundColor": {"red": 1, "green": 1, "blue": 1}}}]
        for row_range in _contiguous_row_ranges(_same_day_rows(out[1:], [])):
            formats.append({"range": row_range,
                            "format": {"backgroundColor": {"red": .90, "green": .85, "blue": 1}}})
        sh.batch_format(formats)
        log.info("[%s] 定期VIP格式套用完成", area_name)
    sh.update(
        values=[["匯出開始日期", "匯出結束日期"],
                [start_dt.strftime("%Y-%m-%d"), (end_dt or start_dt).strftime("%Y-%m-%d")]],
        range_name="T1:U2", value_input_option="RAW",
    )
    # V 欄：同一客人依日期累計超過儲值金餘額的那幾次標「需儲值」。
    sh.update(values=[[TOPUP_FLAG]] + [[r.get("topup", "")] for r in rows],
              range_name=f"V1:V{len(rows) + 1}", value_input_option="RAW")
    sh.freeze(rows=1)
    _write_topup_list(ss, sheet_name.replace("定期VIP_", "需儲值名單_", 1), rows)

    log.info("[%s] 定期VIP工作表寫入完成：%d 筆，工作表：%s", area_name, len(rows), sheet_name)
    return sheet_name


def step2_export_vip_calendar(
    gc: gspread.Client,
    areas: list[dict],
    run_id: str,
    start_dt: datetime,
    end_dt: datetime,
) -> dict:
    task = "Step2_匯出定期VIP日曆"
    t0   = now_tp()
    checkin_both(gc, run_id, task, "START", "RUNNING")

    cal_service = _calendar_service()
    deep_clean_settings = _load_deep_clean_pricing_settings(start_dt, end_dt)
    results     = {}
    errors      = []

    for area in areas:
        area_name  = area["name"]
        calendar_id = area["calendar_id"]
        log.info("=== [%s] 匯出定期VIP日曆 ===", area_name)

        try:
            events = _fetch_calendar_events(cal_service, calendar_id, start_dt, end_dt, area_name)
            rows   = _process_events(
                events, area_name, calendar_id, deep_clean_settings,
            )
            log.info("[%s] 行事曆整理完成：%d 筆符合VIP格式", area_name, len(rows))

            if not rows:
                log.warning("[%s] 指定日期範圍內無符合行程，仍更新空白日曆快照", area_name)
            _area_target = area.get("target_spreadsheet_id", "")
            stored_info = _load_stored_value_info(gc, area_name, area_target_id=_area_target) if rows else {}
            periods = _month_keys(start_dt, end_dt)
            sheet_names = []
            if len(periods) == 1:
                period = periods[0]
                month_start = start_dt.replace(year=int(period[:4]), month=int(period[4:]), day=1,
                                               hour=0, minute=0, second=0, microsecond=0)
                next_month = (month_start.replace(year=month_start.year + 1, month=1)
                              if month_start.month == 12 else month_start.replace(month=month_start.month + 1))
                month_end = next_month - timedelta(seconds=1)
                month_rows = [row for row in rows if row["start_dt"].strftime("%Y%m") == period]
                month_rows = _enrich_with_stored_value(month_rows, stored_info)
                sheet_names.append(_write_vip_sheet(
                    gc, area_name, month_rows, max(start_dt, month_start), area_target_id=_area_target,
                    end_dt=min(end_dt, month_end),
                ))
            else:
                combined_rows = _enrich_with_stored_value(rows, stored_info)
                combined_name = (
                    f"定期VIP_{area_name}_{start_dt.strftime('%Y%m')}-{end_dt.strftime('%Y%m')}"
                )
                sheet_names.insert(0, _write_vip_sheet(
                    gc, area_name, combined_rows, start_dt, area_target_id=_area_target,
                    end_dt=end_dt, sheet_name=combined_name,
                ))
            results[area_name] = {"count": len(rows), "sheet": "、".join(sheet_names), "ok": True}

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


VIP_SCHEDULE_FALLBACK_HEADERS = [
    "服務人時", "備註", "姓名", "電話", "地址", "日期", "開始時間", "結束時間", "狀態",
    "購買項目", "簡訊實際服務時間", "客人備註", "訂單編號", "結果", "原因", "沒班表日",
    "餘額不足", "確認信", "日曆改色結果", "日曆改色內容", "日曆原色", "日曆新色",
    "每月確認", "服務人員", "服務狀態", "車馬費", "客服備註", "服務日期", "服務時區", "服務時數",
]


def _schedule_customer_key(row: list[Any]) -> tuple[str, str, str]:
    padded = list(row) + [""] * max(0, 5 - len(row))
    return (
        normalize_name_for_compare(padded[2]),
        normalize_phone(padded[3]),
        normalize_address(padded[4]),
    )


def _schedule_row_key(row: list[Any]) -> tuple[str, ...]:
    padded = list(row) + [""] * max(0, 8 - len(row))
    return tuple(str(padded[i]).strip() for i in (2, 3, 4, 5, 6, 7))


def _month_keys(start_dt: datetime, end_dt: datetime) -> list[str]:
    current = start_dt.replace(day=1)
    end_month = end_dt.replace(day=1)
    result = []
    while current <= end_month:
        result.append(current.strftime("%Y%m"))
        current = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )
    return result


def _purchase_item_lookup(area_name: str):
    """上月排程查不到購買項目時：檸檬後台依電話查訂單，取同地址最近一次服務的購買項目。"""
    cache: dict[str, list] = {}
    state: dict[str, Any] = {"client": None, "failed": False}

    def lookup(phone: Any, address: Any) -> str:
        from tools.lemon_backend.memo import parse_date, same_address
        key = normalize_phone(phone)
        if not key or state["failed"]:
            return ""
        if key not in cache:
            try:
                if state["client"] is None:
                    from tools.lemon_backend.client import BackendClient
                    state["client"] = BackendClient(area_name)
                cache[key] = state["client"].search_orders_by_phone(key)
            except Exception as exc:
                # 帳密或連線有問題時不再逐列重試，購買項目留空待人工補。
                log.warning("[%s] 檸檬後台查購買項目失敗：%s", area_name, exc)
                state["failed"] = True
                return ""
        served = [order for order in cache[key]
                  if order.service_date and order.items and "儲值" not in order.items[0]
                  and same_address(order.address, str(address or ""))]
        latest = max(served, key=lambda order: parse_date(order.service_date) or datetime.min, default=None)
        return latest.items[0] if latest else ""

    return lookup


def _build_vip_schedule_sheet(
    gc: gspread.Client,
    area: dict,
    start_period: str,
    end_period: str = "",
    compare_only: bool = False,
    booking_gate: tuple[str, str] | None = None,
    sort_rows: bool = False,
) -> dict:
    """安全比對本月日曆，已成單異動保留原列供人工核對。

    booking_gate＝（起日, 迄日）時套用排程預約條件，期間跟著本次匯出起迄日；None＝不套用。
    """
    from tools.service_management.vip_schedule_diff import sync_schedule
    area_name = area["name"]
    target_id = area.get("target_spreadsheet_id", "") or _load_target_file_id()
    if not target_id:
        raise EnvironmentError(f"[{area_name}] 請在客服地區設定填入「目標試算表ID」")
    ss = gc.open_by_key(target_id)
    end_period = end_period or start_period
    is_cross_month = start_period != end_period
    suffix = f"{start_period}-{end_period}" if is_cross_month else start_period
    source = ss.worksheet(f"定期VIP_{area_name}_{suffix}")
    target_name = f"{area_name}{suffix}"
    from tools.service_management.booking_gate import RULE_SHEET_NAME, load_area_gate
    gate, gate_rules = None, []
    if booking_gate:
        gate_start, gate_end = (datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=TZ_TAIPEI) for d in booking_gate)
        # 人時不足只檢查年節大掃除期間；匯出期間其他日期只讓客人看到完整服務日期。
        short_periods = [
            (s.phase1_start.strftime("%Y-%m-%d"), s.phase2_end.strftime("%Y-%m-%d"))
            for s in _load_deep_clean_pricing_settings(gate_start, gate_end.replace(hour=23, minute=59, second=59))
        ]
        gate, gate_rules = load_area_gate(gc, area_name, *booking_gate, worksheet_not_found=gspread.WorksheetNotFound,
                                          short_periods=short_periods)
        log.info("[%s] 套用「%s」：%s", area_name, RULE_SHEET_NAME, "、".join(gate_rules))
    else:
        log.info("[%s] 未套用排程預約條件", area_name)
    result = sync_schedule(
        ss, target_name, source.get("A2:V" if getattr(source, "col_count", 22) >= 22
                                    else "A2:S" if getattr(source, "col_count", 19) >= 19 else "A2:R"),
        VIP_SCHEDULE_FALLBACK_HEADERS, gspread.WorksheetNotFound, compare_only=compare_only,
        coverage=source.get("T2:U2") if getattr(source, "col_count", 21) >= 21 else [],
        period_filter="" if is_cross_month else start_period,
        gate=gate,
        sort_rows=sort_rows,
        purchase_item_lookup=None if compare_only else _purchase_item_lookup(area_name),
    )
    if gate_rules:
        result.setdefault("booking_gate", {})["rules"] = gate_rules
        stages = result["booking_gate"].get("stages") or {}
        log.info("[%s] 預約條件：%s（狀態不變；批次成單依勾選的本次預約對象成單）", area_name,
                 "、".join(f"{name} {count} 列" for name, count in stages.items()) or "無")
    mode = "只比對" if compare_only else "安全更新"
    log.info("[%s] VIP排程%s完成：%s（%d 筆）；差異：%s；差異工作表：%s",
             area_name, mode, result["sheet"], result["count"], result["differences"], result["report"])
    print("VIP_SCHEDULE_DIFF=" + json.dumps(result, ensure_ascii=False), flush=True)
    return result


def step3_build_vip_schedule_sheets(
    gc: gspread.Client,
    areas: list[dict],
    start_dt: datetime,
    end_dt: datetime,
    compare_only: bool = False,
    booking_gate: bool = False,
    sort_rows: bool = False,
) -> dict:
    results = {}
    gate_period = (start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")) if booking_gate else None
    errors = []
    for area in areas:
        periods = _month_keys(start_dt, end_dt)
        suffix = f"{periods[0]}-{periods[-1]}" if len(periods) > 1 else periods[0]
        key = f"{area['name']}{suffix}"
        try:
            results[key] = _build_vip_schedule_sheet(
                gc, area, periods[0], periods[-1], compare_only=compare_only, booking_gate=gate_period,
                sort_rows=sort_rows,
            )
        except Exception as exc:
            log.error("[%s] VIP排程工作表建立失敗：%s", key, exc)
            errors.append(f"{key}: {exc}")
            results[key] = {"ok": False, "error": str(exc)}
    if errors:
        raise RuntimeError("部分VIP排程工作表建立失敗：\n" + "\n".join(errors))
    return results


def _load_target_file_id() -> str:
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
    parser.add_argument("--step",  type=int, choices=[0, 1, 2, 3, 4], default=0,
                        help="0=抓儲值金＋匯出VIP, 1=只抓儲值金, 2=只匯出VIP日曆, 3=建立VIP排程工作表, 4=抓儲值金＋匯出VIP＋建立排程工作表（預設：0）")
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
    parser.add_argument("--booking-gate", action="store_true",
                        help="Step 3／4 套用排程預約條件（期間＝本次 --start～--end）")
    parser.add_argument("--sort-schedule", action="store_true",
                        help="Step 3／4 更新後依 姓名→地址→日期→開始時間 排序（與定期VIP匯出相同）")
    parser.add_argument("--compare-only", action="store_true",
                        help="Step 3 只產生差異工作表，不更新排程內容或底色")
    args = parser.parse_args()

    if args.step in (0, 2, 3, 4) and not (_load_target_file_id()):
        sys.exit("❌ 請在主控試算表「系統設定」填入客服排程系統的共用雲端資料夾ID，或設定 Secret SERVICE_TARGET_SPREADSHEET_ID")

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
        3: "【儲值】建立VIP排程工作表",
        4: "【儲值】全跑（抓儲值金＋匯出VIP＋建立排程工作表）",
    }[args.step]

    if not areas:
        sys.exit(f"❌ 沒有啟用的地區設定（filter={args.area}），請檢查主控試算表「客服地區設定」")

    checkin_both(gc, run_id, "RUN", "START", "RUNNING",
                 f"area={args.area} step={args.step or 'ALL'}", 0)

    t_total = now_tp()
    errors  = []

    try:
        if args.step in (0, 1, 4):
            log.info("--- Step 1：抓取儲值金 ---")
            try:
                step1_fetch_stored_value(gc, areas, run_id)
            except Exception as e:
                errors.append(str(e))
                log.error("Step 1 失敗：%s", e)

        if args.step in (0, 2, 4):
            log.info("--- Step 2：匯出定期VIP日曆 ---")
            areas_with_cal = [a for a in areas if a["calendar_id"]]
            if args.step == 4 and areas_with_cal and len(areas_with_cal) != len(areas):
                errors.append("部分啟用地區未設定 Calendar ID，跳過建立排程以避免使用舊資料")
            if not areas_with_cal:
                if args.step == 4:
                    errors.append("所有地區均未設定 Calendar ID，無法匯出 VIP 日曆")
                log.warning("所有地區均未設定 Calendar ID，跳過 Step 2")
                log.warning("請在主控試算表「客服地區設定」填入各地區的 Calendar ID")
            else:
                try:
                    step2_export_vip_calendar(gc, areas_with_cal, run_id, start_dt, end_dt)
                except Exception as e:
                    errors.append(str(e))
                    log.error("Step 2 失敗：%s", e)

        if args.step == 4 and errors:
            log.error("前置步驟失敗，跳過建立排程工作表，避免使用舊日曆資料")
        if args.step == 3 or (args.step == 4 and not errors):
            log.info("--- Step 3：建立VIP排程工作表 ---")
            try:
                step3_build_vip_schedule_sheets(gc, areas, start_dt, end_dt, compare_only=args.compare_only,
                                                booking_gate=args.booking_gate, sort_rows=args.sort_schedule)
            except Exception as e:
                errors.append(str(e))
                log.error("Step 3 失敗：%s", e)

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
