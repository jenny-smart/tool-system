from __future__ import annotations

"""
檔案：toolapp.py
版本：0816_v3
更新日期：2026-08-16
"""
import html
import base64
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

from tools.common.config_loader import get_sheets_service as _get_sheets_service_raw
from tools.local_agent_queue import create_task as create_local_agent_task
from tools.local_agent_queue import list_tasks as list_local_agent_tasks
from tools.local_agent_queue import read_task_log as read_local_agent_task_log

from services.google_api_retry import install_gspread_retry

# gspread（儲值金功能用）遇到 429 Quota exceeded 時自動重試＋退避，
# 避免像 convert_files/move_files/apply_formulas 這種一次跑很多 API
# 呼叫的流程，被 Sheets API 的「每分鐘讀取次數」限制擋下來直接失敗。
install_gspread_retry()

try:
    from tools.local_agent_queue import list_agent_status as list_local_agent_status
except ImportError:
    def list_local_agent_status(*, max_age_seconds=30, **_kwargs):
        return []

def get_sheets_service():  # noqa: F811
    """每次建立 Sheets 連線，避免雲端長連線失效造成 Broken pipe。"""
    return _get_sheets_service_raw()

from utils.auth import authenticate
from utils.permissions import (
    can_access_page,
    can_access_system,
    get_allowed_log_jobs,
)


TW_TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parent

st.set_page_config(
    page_title="Tools App",
    page_icon="🧰",
    layout="centered",
)


# ═══════════════════════════════════════════════════════════
# 設定檔讀寫
# ═══════════════════════════════════════════════════════════
# 舊版 systems.yaml 只保留為讀取 fallback。
# 主要設定來源改為 Google Sheets 主控表。
CONFIG_PATH = Path("config/systems.yaml")
MASTER_CONFIG_SPREADSHEET_ID = "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"

SYSTEM_SETTING_SHEET = "系統設定"
MONTHLY_AREA_SHEET = "月排程地區設定"

SYSTEM_SETTING_HEADERS = [
    "系統名稱",
    "type",
    "主控表ID",
    "共用雲端資料夾ID / 根目錄ID",
    "月排程根目錄 ID",
    "啟用",
    "設定JSON",
]

MONTHLY_AREA_HEADERS = [
    "系統名稱",
    "地區",
    "地區根目錄ID",
    "啟用",
]


DEFAULT_CONFIG = {
    "systems": [
        {
            "name": "儲值金管理",
            "type": "vip",
            "master_spreadsheet_id": "",
            "folder_id": "",
            "enabled": True,
        },
        {
            "name": "日排程系統",
            "type": "daily_scheduler",
            "master_spreadsheet_id": "",
            "folder_id": "",
            "enabled": True,
        },
        {
            "name": "月排程系統",
            "type": "monthly_scheduler",
            "master_spreadsheet_id": "",
            "folder_id": "",
            "enabled": True,
            "areas": {
                "台北": {"folder_id": "", "enabled": True},
                "台中": {"folder_id": "", "enabled": True},
                "桃園": {"folder_id": "", "enabled": True},
                "新竹": {"folder_id": "", "enabled": True},
                "高雄": {"folder_id": "", "enabled": True},
            },
        },
        {
            "name": "外場排程系統",
            "type": "field_daily_schedule",
            "enabled": True,
            "folder_ids": {
                "schedule_stats": "",
                "order": {"台北": "", "台中": ""},
                "staff_profile": {"台北": "", "台中": ""},
                "staff_schedule": {"台北": "", "台中": ""},
            },
            "spreadsheet_ids": {
                "roster": {"台北": "", "台中": ""},
                "salary": {"台北": "", "台中": ""},
                "office": {"台北": "", "台中": ""},
            },
        },
        # ── ★ 新增：客服排程系統 ─────────────────────────────
        {
            "name": "客服排程系統",
            "type": "service_schedule",
            "enabled": True,
            "target_file_id": "",   # 由 Secret LEMON_TARGET_FILE_ID 注入
            "folder_ids": {
                "schedule_stats": "1V0IjoJqHlnkGb3Oq70Cil63pQ9j8r2Xv",
            },
        },
        # ─────────────────────────────────────────────────────
        {
            "name": "財務管理",
            "type": "finance_management",
            "enabled": True,
        },
        {
            "name": "訂單系統",
            "type": "orders_memo_system",
            "enabled": True,
        },
    ]
}


def get_secret_text(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return default


def _build_subprocess_env() -> dict:
    """
    建立子進程用的環境變數字典。
    Streamlit Cloud 的 secrets 只能透過 st.secrets 讀取，
    不會自動出現在 os.environ，需要手動注入給 subprocess。
    子進程透過 STREAMLIT_SECRETS_TOML 環境變數自行解析所需 key。
    """
    env = os.environ.copy()

    # 注入 PYTHONPATH
    base_str = str(BASE_DIR)
    existing_pp = env.get("PYTHONPATH", "")
    if base_str not in existing_pp:
        env["PYTHONPATH"] = base_str + (":" + existing_pp if existing_pp else "")

    # 把 st.secrets 的所有 key 嘗試注入（字串值直接注入，dict 轉 JSON）
    # 同時確保 STREAMLIT_SECRETS_TOML 原始內容也被帶入子進程
    try:
        import toml as _toml
        # 把整個 st.secrets 序列化成 TOML 字串帶給子進程
        secrets_dict = {k: dict(v) if hasattr(v, "to_dict") else v for k, v in st.secrets.items()}
        env["STREAMLIT_SECRETS_TOML"] = _toml.dumps(secrets_dict)
    except Exception:
        pass

    # 直接注入每個 string secret 到 env（雙重保險）
    try:
        for key in st.secrets:
            try:
                val = st.secrets[key]
                if isinstance(val, str):
                    env[key] = val
                elif isinstance(val, dict):
                    env[key] = json.dumps(val, ensure_ascii=False)
            except Exception:
                pass
    except Exception:
        pass

    return env


def get_master_config_spreadsheet_id() -> str:
    spreadsheet_id = (
        os.getenv("TOOLS_APP_LOG_SPREADSHEET_ID", "").strip()
        or os.getenv("MASTER_LOG_SPREADSHEET_ID", "").strip()
        or os.getenv("LOG_SPREADSHEET_ID", "").strip()
        or get_secret_text("TOOLS_APP_LOG_SPREADSHEET_ID")
        or get_secret_text("MASTER_LOG_SPREADSHEET_ID")
        or get_secret_text("LOG_SPREADSHEET_ID")
        or MASTER_CONFIG_SPREADSHEET_ID
    ).strip()

    if not spreadsheet_id:
        raise RuntimeError("找不到主控試算表 ID")

    # 讓 subprocess 執行 half_month_orders.py 時也能讀到同一份主控表。
    os.environ["TOOLS_APP_LOG_SPREADSHEET_ID"] = spreadsheet_id
    return spreadsheet_id


def _quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def _get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int | None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()

    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props.get("sheetId"))

    return None


def _ensure_sheet(service, spreadsheet_id: str, sheet_name: str, headers: list[str]) -> None:
    sheet_id = _get_sheet_id(service, spreadsheet_id, sheet_name)

    if sheet_id is None:
        res = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": sheet_name,
                                "gridProperties": {"frozenRowCount": 1},
                            }
                        }
                    }
                ]
            },
        ).execute()
        sheet_id = int(res["replies"][0]["addSheet"]["properties"]["sheetId"])

    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{_quote_sheet_name(sheet_name)}!A1:Z1",
    ).execute().get("values", [])

    if not current or current[0][: len(headers)] != headers:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{_quote_sheet_name(sheet_name)}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {"red": 0.86, "green": 0.90, "blue": 0.98},
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                },
            ]
        },
    ).execute()


def _read_sheet_records(sheet_name: str) -> list[dict[str, str]]:
    spreadsheet_id = get_master_config_spreadsheet_id()
    service = get_sheets_service()
    _ensure_sheet(
        service,
        spreadsheet_id,
        sheet_name,
        SYSTEM_SETTING_HEADERS if sheet_name == SYSTEM_SETTING_SHEET else MONTHLY_AREA_HEADERS,
    )

    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{_quote_sheet_name(sheet_name)}!A:Z",
    ).execute().get("values", [])

    if not rows:
        return []

    headers = [str(h).strip() for h in rows[0]]
    records: list[dict[str, str]] = []

    for row in rows[1:]:
        record = {}
        for i, header in enumerate(headers):
            record[header] = str(row[i]).strip() if i < len(row) else ""
        records.append(record)

    return records


def _overwrite_sheet(sheet_name: str, headers: list[str], rows: list[list[object]]) -> None:
    spreadsheet_id = get_master_config_spreadsheet_id()
    service = get_sheets_service()
    _ensure_sheet(service, spreadsheet_id, sheet_name, headers)

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{_quote_sheet_name(sheet_name)}!A:Z",
        body={},
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{_quote_sheet_name(sheet_name)}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [headers] + rows},
    ).execute()


def _bool_to_sheet(value) -> str:
    return "TRUE" if bool(value) else "FALSE"


def _sheet_to_bool(value, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in ["true", "1", "yes", "y", "啟用", "✅", "✅ 啟用"]


def _system_to_row(sys_cfg: dict) -> list[object]:
    system_type = sys_cfg.get("type", "")
    folder_id = sys_cfg.get("folder_id", "")

    return [
        sys_cfg.get("name", ""),
        system_type,
        sys_cfg.get("master_spreadsheet_id", ""),
        "" if system_type == "monthly_scheduler" else folder_id,
        folder_id if system_type == "monthly_scheduler" else "",
        _bool_to_sheet(sys_cfg.get("enabled", True)),
        json.dumps(sys_cfg, ensure_ascii=False, sort_keys=False),
    ]


def _config_to_master_sheets(cfg: dict) -> None:
    systems = cfg.get("systems", [])

    system_rows = [_system_to_row(sys_cfg) for sys_cfg in systems]
    monthly_rows: list[list[object]] = []

    for sys_cfg in systems:
        if sys_cfg.get("type") != "monthly_scheduler":
            continue

        system_name = sys_cfg.get("name", "月排程系統")
        for area_name, area_info in normalize_monthly_areas(sys_cfg).items():
            monthly_rows.append(
                [
                    system_name,
                    area_name,
                    area_info.get("folder_id", ""),
                    _bool_to_sheet(area_info.get("enabled", True)),
                ]
            )

    _overwrite_sheet(SYSTEM_SETTING_SHEET, SYSTEM_SETTING_HEADERS, system_rows)
    _overwrite_sheet(MONTHLY_AREA_SHEET, MONTHLY_AREA_HEADERS, monthly_rows)


@st.cache_data(ttl=300, show_spinner=False)
def _load_config_from_master_sheets() -> dict:
    system_records = _read_sheet_records(SYSTEM_SETTING_SHEET)

    systems: list[dict] = []

    for record in system_records:
        name = record.get("系統名稱", "").strip()
        if not name:
            continue

        raw_json = record.get("設定JSON", "").strip()
        sys_cfg: dict = {}

        if raw_json:
            try:
                sys_cfg = json.loads(raw_json)
            except Exception:
                sys_cfg = {}

        sys_cfg["name"] = name
        sys_cfg["type"] = record.get("type", sys_cfg.get("type", "")).strip()
        sys_cfg["master_spreadsheet_id"] = record.get(
            "主控表ID",
            sys_cfg.get("master_spreadsheet_id", ""),
        ).strip()

        folder_id = (
            record.get("月排程根目錄 ID", "").strip()
            or record.get("共用雲端資料夾ID / 根目錄ID", "").strip()
            or sys_cfg.get("folder_id", "")
        )

        sys_cfg["folder_id"] = folder_id
        sys_cfg["enabled"] = _sheet_to_bool(
            record.get("啟用", ""),
            bool(sys_cfg.get("enabled", True)),
        )

        systems.append(sys_cfg)

    monthly_records = _read_sheet_records(MONTHLY_AREA_SHEET)
    monthly_by_system: dict[str, dict] = {}

    for record in monthly_records:
        system_name = record.get("系統名稱", "").strip() or "月排程系統"
        area_name = record.get("地區", "").strip()

        if not area_name:
            continue

        monthly_by_system.setdefault(system_name, {})[area_name] = {
            "folder_id": record.get("地區根目錄ID", "").strip(),
            "enabled": _sheet_to_bool(record.get("啟用", ""), True),
        }

    for sys_cfg in systems:
        if sys_cfg.get("type") == "monthly_scheduler":
            areas = monthly_by_system.get(sys_cfg.get("name", ""), {})
            if areas:
                sys_cfg["areas"] = areas

    return {"systems": systems}


def save_config(cfg: dict) -> None:
    """儲存設定到主控試算表，不再寫入 GitHub systems.yaml。"""
    _config_to_master_sheets(cfg)
    # 清除 config cache，確保下次 load_config 讀到最新設定
    _load_config_from_master_sheets.clear()
    st.cache_data.clear()


def ensure_config_file() -> None:
    """舊名稱保留相容；現在會確保主控表分頁存在。"""
    spreadsheet_id = get_master_config_spreadsheet_id()
    service = get_sheets_service()
    _ensure_sheet(service, spreadsheet_id, SYSTEM_SETTING_SHEET, SYSTEM_SETTING_HEADERS)
    _ensure_sheet(service, spreadsheet_id, MONTHLY_AREA_SHEET, MONTHLY_AREA_HEADERS)


def merge_default_systems(data: dict) -> dict:
    """
    保留現有設定，只補缺少的預設系統。
    """
    if "systems" not in data or not isinstance(data["systems"], list):
        data["systems"] = []

    existing_names = {
        str(system.get("name", "")).strip()
        for system in data.get("systems", [])
        if system.get("name")
    }

    for default_system in DEFAULT_CONFIG["systems"]:
        if default_system["name"] not in existing_names:
            data["systems"].append(default_system)

    return data


def _load_config_from_yaml_fallback() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return merge_default_systems(data)
    return merge_default_systems(DEFAULT_CONFIG.copy())


def load_config() -> dict:
    """
    優先讀主控試算表。
    若主控表尚無資料，會用 systems.yaml 或 DEFAULT_CONFIG 初始化主控表。
    新增系統請直接在主控試算表「系統設定」工作表手動新增一列。
    """
    try:
        data = _load_config_from_master_sheets()

        if data.get("systems"):
            return merge_default_systems(data)

        data = _load_config_from_yaml_fallback()
        save_config(data)
        st.info("已初始化主控表設定")
        return data

    except Exception as exc:
        st.warning(f"主控表設定讀取失敗，暫時使用本機 fallback：{exc}")
        return _load_config_from_yaml_fallback()


def merge_legacy_secrets(cfg: dict) -> dict:
    legacy_master = get_secret_text("MASTER_SPREADSHEET_ID")
    legacy_folder = get_secret_text("ROOT_FOLDER_ID")

    if not legacy_master and not legacy_folder:
        return cfg

    for sys_cfg in cfg.get("systems", []):
        if sys_cfg.get("name") == "儲值金管理":
            if legacy_master and not sys_cfg.get("master_spreadsheet_id"):
                sys_cfg["master_spreadsheet_id"] = legacy_master
            if legacy_folder and not sys_cfg.get("folder_id"):
                sys_cfg["folder_id"] = legacy_folder
            break

    return cfg


def get_enabled_systems(cfg: dict) -> list[dict]:
    systems = cfg.get("systems", [])
    enabled = [s for s in systems if s.get("enabled", True)]
    return enabled or systems


def get_system_by_name(cfg: dict, name: str) -> dict:
    for s in cfg.get("systems", []):
        if s.get("name") == name:
            return s
    return {}


# ═══════════════════════════════════════════════════════════
# UI 樣式
# ═══════════════════════════════════════════════════════════
st.markdown(
    """
<style>
  .stApp { background: #f4f8fc; }
  #MainMenu, footer, header { visibility: hidden; }

  .app-title {
    font-size: 1.45rem;
    font-weight: 800;
    color: #0a4b6e;
    letter-spacing: 1px;
    text-align: center;
    margin-bottom: 16px;
  }

  .card {
    background: white;
    border-radius: 20px;
    padding: 16px 20px;
    margin-bottom: 14px;
    box-shadow: 0 4px 12px rgba(0,32,48,0.06);
    border: 1px solid #e2edf2;
  }

  .card-title {
    font-size: 0.96rem;
    font-weight: 800;
    color: #164a5e;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1.5px solid #e7f0f5;
  }

  .field-label {
    color: #2a5770;
    font-weight: 700;
    font-size: 0.76rem;
    margin-bottom: 4px;
  }

  .stButton > button {
    background: #1f6c9e !important;
    color: white !important;
    border: none !important;
    border-radius: 40px !important;
    font-weight: 700 !important;
    font-size: 0.9rem !important;
  }

  .stButton > button:hover {
    background: #135b84 !important;
  }

  .log-box {
    background: #0c2835;
    color: #d7ecf5;
    border-radius: 20px;
    padding: 14px 16px;
    margin-bottom: 14px;
    font-family: 'Courier New', monospace;
    border: 1px solid #254f60;
  }

  .log-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
    color: #b0d1dd;
    font-size: 0.8rem;
    padding-bottom: 8px;
    border-bottom: 1px solid #2c5a6a;
  }

  .log-scroll {
    max-height: 300px;
    overflow-y: auto;
  }

  .log-entry {
    padding: 4px 0;
    border-bottom: 1px solid #1c4452;
    font-size: 0.75rem;
    color: #cde3ec;
    line-height: 1.4;
    white-space: pre-wrap;
  }

  .log-entry.success { color: #6ee7b7; }
  .log-entry.error   { color: #fca5a5; }
  .log-entry.warning { color: #fcd34d; }

  .setting-note {
    font-size: 0.75rem;
    color: #497084;
    line-height: 1.5;
    margin-top: 6px;
    margin-bottom: 10px;
  }

  .system-card {
    background: #f8fcff;
    border-radius: 16px;
    padding: 12px 14px;
    margin-bottom: 10px;
    border: 1px solid #d9eaf2;
  }

  .badge-ok {
    background: #2a8c5a;
    color: white;
    padding: 2px 8px;
    border-radius: 20px;
    font-size: 0.65rem;
  }

  .badge-err {
    background: #dc2626;
    color: white;
    padding: 2px 8px;
    border-radius: 20px;
    font-size: 0.65rem;
  }

  .detail-row {
    font-size: 0.75rem;
    color: #3e6c87;
    margin: 3px 0;
  }

  .report-table-wrap {
    overflow-x:auto;
    border:1px solid #e2e8f0;
    border-radius:16px;
    background:white;
  }

  table.report-table {
    width:100%;
    border-collapse:separate;
    border-spacing:0;
    font-size:0.95rem;
  }

  .report-table th {
    background:#f8fafc;
    color:#64748b;
    font-weight:800;
    text-align:center;
    padding:13px 14px;
    border-bottom:1px solid #e2e8f0;
    border-right:1px solid #e2e8f0;
    white-space:nowrap;
  }

  .report-table td {
    padding:12px 14px;
    border-bottom:1px solid #edf2f7;
    border-right:1px solid #edf2f7;
    color:#1e293b;
    background:white;
    white-space:nowrap;
  }

  .report-table td.num {
    text-align:right !important;
    font-variant-numeric:tabular-nums;
  }

  .report-table td.text { text-align:left; }

  .report-table tr.total-row td {
    background:#f8fafc;
    font-weight:900;
  }

  .report-table tr:hover td { background:#f1f5f9; }
</style>
""",
    unsafe_allow_html=True,
)


# ═══════════════════════════════════════════════════════════
# Session State
# ═══════════════════════════════════════════════════════════
if "logs" not in st.session_state:
    st.session_state.logs = ["[--:--:--] 系統已就緒，請選擇作業..."]

if "selected_system_name" not in st.session_state:
    st.session_state.selected_system_name = "財務管理"

if "adding_system" not in st.session_state:
    st.session_state.adding_system = False

if "editing_system" not in st.session_state:
    st.session_state.editing_system = None

if "view" not in st.session_state:
    st.session_state.view = "main"

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "username" not in st.session_state:
    st.session_state.username = ""

if "role" not in st.session_state:
    st.session_state.role = ""



def render_login_page() -> None:
    st.markdown(
        """
        <div style="background:white;border:1px solid #e2edf2;border-radius:24px;padding:28px;max-width:460px;margin:70px auto 0 auto;box-shadow:0 10px 30px rgba(0,32,48,0.08);">
          <div style="font-size:1.8rem;font-weight:900;color:#0a4b6e;text-align:center;margin-bottom:8px;">🔐 Tools App 登入</div>
          <div style="color:#7894a5;text-align:center;font-size:0.9rem;margin-bottom:22px;">請使用系統帳號登入後操作</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        username = st.text_input("帳號")
        password = st.text_input("密碼", type="password")
        submitted = st.form_submit_button("登入", use_container_width=True)

    if submitted:
        user = authenticate(username, password)

        if user:
            st.session_state.logged_in = True
            st.session_state.username = user["username"]
            st.session_state.role = user["role"]
            st.rerun()

        else:
            st.error("帳號或密碼錯誤")


def require_login() -> None:
    if not st.session_state.get("logged_in"):
        render_login_page()
        st.stop()


def logout_button() -> None:
    # 保留函式名稱，避免舊流程呼叫錯誤；實際顯示改到主畫面上方。
    return


def render_user_bar() -> None:
    username = st.session_state.get("username", "-")
    role = st.session_state.get("role", "-")

    st.markdown(
        f"""
        <div style="
          background:rgba(255,255,255,0.78);
          border:1px solid #dbeaf2;
          border-radius:999px;
          padding:8px 14px;
          margin:0 auto 14px auto;
          max-width:760px;
          display:flex;
          align-items:center;
          justify-content:space-between;
          gap:10px;
          box-shadow:0 4px 16px rgba(15,23,42,0.04);
          font-size:0.82rem;
          color:#4b6b7d;
        ">
          <div>👤 {html.escape(str(username))}</div>
          <div>角色：<strong>{html.escape(str(role))}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _, mid, _ = st.columns([3, 1, 3])
    with mid:
        if st.button("登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.role = ""
            st.session_state.view = "main"
            st.rerun()


def parse_date_range(start_date, end_date) -> list[str]:
    from datetime import timedelta

    if not start_date or not end_date:
        return []

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    days = []
    current = start_date

    while current <= end_date:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)

    return days


def normalize_monthly_areas(system: dict) -> dict:
    """月排程地區設定標準化。"""
    raw_areas = system.get("areas", {})
    raw_folders = system.get("folders", {}) or {}
    output = {}

    if isinstance(raw_areas, dict):
        for area_name, info in raw_areas.items():
            area_name = str(area_name)

            if isinstance(info, dict):
                folder_id = str(
                    info.get("folder_id")
                    or info.get("root_folder_id")
                    or info.get("folder_name")
                    or raw_folders.get(area_name, "")
                    or ""
                )
                output[area_name] = {
                    "folder_id": folder_id,
                    "folder_name": str(info.get("folder_name", "")),
                    "enabled": bool(info.get("enabled", True)),
                }
            else:
                output[area_name] = {
                    "folder_id": str(raw_folders.get(area_name, "")),
                    "folder_name": "",
                    "enabled": bool(info),
                }

    elif isinstance(raw_areas, list):
        for area_name in raw_areas:
            area_name = str(area_name)
            output[area_name] = {
                "folder_id": str(raw_folders.get(area_name, "")),
                "folder_name": "",
                "enabled": True,
            }

    return output


def monthly_areas_to_df(system: dict) -> pd.DataFrame:
    areas = normalize_monthly_areas(system)

    rows = []

    for area_name, info in areas.items():
        rows.append(
            {
                "地區": area_name,
                "地區根目錄ID": info.get("folder_id", "") or info.get("folder_name", ""),
                "啟用": bool(info.get("enabled", True)),
            }
        )

    return pd.DataFrame(rows, columns=["地區", "地區根目錄ID", "啟用"])


def default_monthly_areas_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"地區": "台北", "地區根目錄ID": "", "啟用": True},
            {"地區": "台中", "地區根目錄ID": "", "啟用": True},
            {"地區": "桃園", "地區根目錄ID": "", "啟用": True},
            {"地區": "新竹", "地區根目錄ID": "", "啟用": True},
            {"地區": "高雄", "地區根目錄ID": "", "啟用": True},
        ],
        columns=["地區", "地區根目錄ID", "啟用"],
    )


def monthly_areas_df_to_config(df) -> dict:
    areas = {}

    if df is None:
        return areas

    for _, row in pd.DataFrame(df).iterrows():
        area_name = str(row.get("地區", "") or "").strip()

        if not area_name or area_name.lower() == "nan":
            continue

        folder_id = str(row.get("地區根目錄ID", "") or "").strip()

        if folder_id.lower() == "nan":
            folder_id = ""

        enabled_raw = row.get("啟用", True)

        if isinstance(enabled_raw, str):
            enabled = enabled_raw.strip().lower() not in ["0", "false", "停用", "disabled", "no"]
        else:
            enabled = bool(enabled_raw)

        areas[area_name] = {
            "folder_id": folder_id,
            "enabled": enabled,
        }

    return areas


def available_areas_for_system(system: dict) -> list[str]:
    system_type = system.get("type", "")

    if system_type == "monthly_scheduler":
        return ["台北", "台中", "桃園", "新竹", "高雄"]

    if system_type == "field_daily_schedule":
        folder_ids = system.get("folder_ids", {}) or {}
        for key in ["order", "staff_profile", "staff_schedule"]:
            value = folder_ids.get(key)
            if isinstance(value, dict) and value:
                return list(value.keys())
        spreadsheet_ids = system.get("spreadsheet_ids", {}) or {}
        for value in spreadsheet_ids.values():
            if isinstance(value, dict) and value:
                return list(value.keys())
        return ["台北", "台中"]

    # ── ★ 新增：客服排程系統固定全區 ──────────────────────
    if system_type == "service_schedule":
        return ["全區"]
    # ────────────────────────────────────────────────────────

    if system_type == "finance_management":
        return ["全區", "台北", "台中", "桃園", "新竹", "高雄"]

    # 舊日排程目前腳本本身會跑全部區域，先提供全區。
    return ["全區"]


def set_view(view_name: str) -> None:
    st.session_state.view = view_name
    try:
        if view_name == "main":
            st.query_params.clear()
        else:
            st.query_params["view"] = view_name
    except Exception:
        pass


def sync_view_from_query_params() -> None:
    try:
        view = st.query_params.get("view")
    except Exception:
        view = None

    if view in ["report", "log"]:
        st.session_state.view = view


# ═══════════════════════════════════════════════════════════
# 工具函式
# ═══════════════════════════════════════════════════════════
def tw_now_text(fmt: str = "%H:%M:%S") -> str:
    return datetime.now(TW_TZ).strftime(fmt)


def add_log(message: str, level: str = "info") -> None:
    now = tw_now_text("%H:%M:%S")
    icons = {
        "info": "🔵",
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
    }
    icon = icons.get(level, "🔵")
    st.session_state.logs.append(f"[{now}] {icon} {message}")

    if len(st.session_state.logs) > 200:
        st.session_state.logs = st.session_state.logs[-200:]


def render_log() -> None:
    log_html = (
        '<div class="log-box">'
        '<div class="log-header">'
        '<span>📋 執行日誌</span>'
        '<span style="background:#1e4757;padding:3px 10px;border-radius:20px;font-size:0.75rem;">台北時區 / 即時更新</span>'
        '</div><div class="log-scroll">'
    )

    for entry in reversed(st.session_state.logs):
        css = "log-entry"

        if "✅" in entry:
            css += " success"
        elif "❌" in entry:
            css += " error"
        elif "⚠️" in entry:
            css += " warning"

        log_html += f'<div class="{css}">{html.escape(entry)}</div>'

    log_html += "</div></div>"
    st.markdown(log_html, unsafe_allow_html=True)


def mask_id(value: str) -> str:
    if not value:
        return "❌ 未設定"
    value = str(value)
    if len(value) <= 14:
        return value
    return f"{value[:8]}...{value[-6:]}"


def mask_nested_ids(value):
    if isinstance(value, dict):
        return {k: mask_nested_ids(v) for k, v in value.items()}
    return mask_id(str(value or ""))


def get_system_type_label(system_type: str) -> str:
    mapping = {
        "vip": "儲值金管理",
        "daily_scheduler": "日排程系統",
        "monthly_scheduler": "月排程系統",
        "field_daily_schedule": "外場排程系統",
        "service_schedule": "客服排程系統",   # ★ 新增
        "gmail_401": "Gmail 401歸檔",
        "invoice_center": "發票中心",
        "finance_management": "財務管理",
        "orders_memo_system": "訂單系統",
    }
    return mapping.get(system_type, system_type or "未設定")


def build_vip_workflow(master_id: str, folder_id: str, system_name: str):
    from services.google_auth import get_gspread_client, get_drive_service
    from services.google_drive import DriveService
    from services.google_sheets import SheetsService
    from services.vip_workflow import VipStoredValueWorkflow

    sheets = SheetsService(get_gspread_client())
    drive = DriveService(get_drive_service())

    return VipStoredValueWorkflow(
        drive,
        sheets,
        master_id,
        folder_id,
        system_name,
    )


def run_script(script_path: str, args: list[str] | None = None) -> str:
    args = args or []

    if script_path.startswith("module:"):
        module_name = script_path.replace("module:", "", 1)
        cmd = [sys.executable, "-m", module_name, *args]
        display_name = module_name
    else:
        script = BASE_DIR / script_path

        if not script.exists():
            raise RuntimeError(f"找不到執行檔：{script_path}")

        cmd = [sys.executable, str(script), *args]
        display_name = script_path

    # Streamlit Secrets 不會自動傳給子程序，必須明確注入環境變數。
    env = _build_subprocess_env()

    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        cwd=BASE_DIR,
        env=env,
    )

    if completed.stdout:
        for line in completed.stdout.splitlines()[-120:]:
            add_log(line, "info")

    if completed.stderr:
        for line in completed.stderr.splitlines()[-120:]:
            add_log(line, "error")

    if completed.returncode != 0:
        raise RuntimeError(
            f"執行失敗：{display_name}\n"
            f"exit={completed.returncode}\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return f"{display_name} 執行完成"

def render_html_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("尚無資料")
        return

    show = df.copy()
    numeric_cols = []

    for col in show.columns:
        col_text = str(col)
        if "佔比" in col_text:
            show[col] = (
                pd.to_numeric(show[col], errors="coerce")
                .fillna(0)
                .map(lambda x: f"{x:.2%}")
            )
            numeric_cols.append(col)
        elif any(x in col_text for x in ["業績", "加總", "家電", "儲值金", "金額", "總額"]):
            show[col] = (
                pd.to_numeric(show[col], errors="coerce")
                .fillna(0)
                .map(lambda x: f"{int(x):,}")
            )
            numeric_cols.append(col)

    out = ['<div class="report-table-wrap"><table class="report-table"><thead><tr>']

    for col in show.columns:
        out.append(f"<th>{html.escape(str(col))}</th>")

    out.append("</tr></thead><tbody>")

    for _, row in show.iterrows():
        is_total = str(row.get("城市", "")) == "加總"
        cls = ' class="total-row"' if is_total else ""
        out.append(f"<tr{cls}>")

        for col in show.columns:
            cell_cls = "num" if col in numeric_cols else "text"
            out.append(f'<td class="{cell_cls}">{html.escape(str(row[col]))}</td>')

        out.append("</tr>")

    out.append("</tbody></table></div>")
    st.markdown("".join(out), unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# 業績報表
# ═══════════════════════════════════════════════════════════
def render_report() -> None:
    latest_dir = Path("dashboard_data/latest")
    df4_path = latest_dir / "df4.csv"
    daily_path = latest_dir / "daily_df.csv"
    next_path = latest_dir / "next_month_daily_df.csv"
    month_end_path = latest_dir / "month_end_summary.csv"
    meta_path = latest_dir / "meta.json"
    html_path = latest_dir / "email_preview.html"

    def load_csv(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, encoding="utf-8-sig")

    def money(value) -> str:
        try:
            return f"{int(float(str(value).replace(',', '').replace('%', ''))):,}"
        except Exception:
            return "0"

    def get_total(df: pd.DataFrame, col: str) -> str:
        if df.empty or col not in df.columns:
            return "0"
        total_row = df[df["城市"].astype(str) == "加總"] if "城市" in df.columns else pd.DataFrame()
        if not total_row.empty:
            return money(total_row.iloc[0][col])
        return money(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    st.markdown(
        """
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;">
          <div>
            <div style="font-size:2.2rem;font-weight:900;color:#0f172a;letter-spacing:1px;">
              📊 業績報表
            </div>
            <div style="font-size:0.85rem;font-weight:800;color:#94a3b8;letter-spacing:5px;margin-top:4px;">
              LATEST DATA · CURRENT / NEXT MONTH / SNAPSHOT
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    top_cols = st.columns([1, 1, 1, 1])
    with top_cols[0]:
        if st.button("← 返回主控台", use_container_width=True):
            set_view("main")
            st.rerun()

    with top_cols[1]:
        if st.button("🔄 更新業績報表", use_container_width=True):
            try:
                add_log("開始更新業績報表", "info")
                run_script("tools/scheduled_daily/performance_report.py", ["dashboard", "true"])
                add_log("業績報表更新完成", "success")
                st.rerun()
            except Exception as e:
                add_log(f"業績報表更新失敗：{e}", "error")
                st.error(f"業績報表更新失敗：{e}")

    with top_cols[2]:
        if st.button("📂 重新讀取資料", use_container_width=True):
            st.rerun()

    with top_cols[3]:
        st.link_button("🔗 開啟獨立連結", "?view=report", use_container_width=True)

    if not df4_path.exists():
        st.info("尚未產生業績報表資料，請先在主控台執行「日排程系統 → 業績報表」。")
        return

    df4 = load_csv(df4_path)
    daily_df = load_csv(daily_path)
    next_df = load_csv(next_path)
    month_end_df = load_csv(month_end_path)

    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            st.info(f"📅 最新更新時間：{meta.get('updated_at', '-')}")
            if meta.get("error"):
                st.warning(meta.get("error"))
        except Exception:
            pass

    if df4.empty:
        st.warning("業績報表資料為空，請重新執行「日排程系統 → 業績報表」。")
        return

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("本月加總", get_total(df4, "本月加總"))
    with k2:
        st.metric("次月加總", get_total(df4, "次月加總"))
    with k3:
        st.metric("本月家電加總", get_total(df4, "本月家電加總"))
    with k4:
        st.metric("儲值金", get_total(df4, "儲值金"))

    st.markdown('<div class="card"><div class="card-title">📍 各區月度摘要</div>', unsafe_allow_html=True)
    render_html_table(df4)
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("🔎 Debug：查看 df2 / raw_df", expanded=False):
        debug_dir = Path("dashboard_data/latest")
        df2_path = debug_dir / "df2.csv"
        raw_path = debug_dir / "raw_df.csv"
        selected_city = "全部"

        if df2_path.exists():
            df2 = pd.read_csv(df2_path, encoding="utf-8-sig")
            city_options = ["全部"] + sorted(df2["城市"].dropna().astype(str).unique().tolist())
            selected_city = st.selectbox("城市", city_options, key="sales_debug_city")
            df2_view = df2 if selected_city == "全部" else df2[df2["城市"].astype(str) == selected_city]
            st.subheader("df2：分類後彙總")
            st.dataframe(df2_view, use_container_width=True, height=520)
        else:
            st.warning("找不到 df2.csv")

        if raw_path.exists():
            raw_df = pd.read_csv(raw_path, encoding="utf-8-sig")
            raw_view = raw_df
            if df2_path.exists() and selected_city != "全部":
                raw_view = raw_df[raw_df["城市"].astype(str) == selected_city]
            st.subheader("raw_df：原始抓取資料")
            st.dataframe(
                raw_view,
                use_container_width=True,
                height=520,
            )
        else:
            st.warning("找不到 raw_df.csv")
    

    st.markdown('<div class="card"><div class="card-title">📈 月度追蹤</div>', unsafe_allow_html=True)
    tabs = st.tabs(["當月每日業績", "次月每日業績", "月底快照"])

    with tabs[0]:
        render_html_table(daily_df)

    if raw_path.exists():
        raw_df = pd.read_csv(raw_path, encoding="utf-8-sig")
        st.subheader("raw_df：台北原始資料")
        st.dataframe(
            raw_df[raw_df["城市"].astype(str) == "台北"],
            use_container_width=True,
        )
    else:
        st.warning("找不到 raw_df.csv，請先更新業績報表")

    with tabs[1]:
        render_html_table(next_df)

    with tabs[2]:
        render_html_table(month_end_df)

    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("📧 信件預覽", expanded=False):
        if html_path.exists():
            st.components.v1.html(html_path.read_text(encoding="utf-8"), height=600, scrolling=True)
        else:
            st.info("尚無 Email HTML")


# ═══════════════════════════════════════════════════════════
# 排程 Log 頁
# ═══════════════════════════════════════════════════════════
def render_log_page() -> None:
    JOB_LABELS = {
        # 日排程：撈資料
        "schedule_report": "排班統計表",
        "staff_schedule": "專員班表",
        "orders_report": "當月次月訂單",
        "staff_info": "專員個資",

        # 業績報表
        "performance_report": "業績報表",

        # 外場排程：彙整資料
        "field_schedule_stats": "外場排班統計表",
        "field_staff_schedule": "外場專員班表",
        "field_orders": "外場訂單",
        "field_staff_profile": "外場專員個資",

        # ★ 客服排程
        "service_schedule_stats": "客服排班統計表",
        "service_daily_report":   "客服每日回報",
        "service_revenue":        "客服前日營業額",

        # 通知
        "send_daily_result": "通知信",
    }

    def today_key() -> str:
        return datetime.now(TW_TZ).strftime("%Y%m%d")

    def read_text_safe(path: Path, max_bytes: int = 2 * 1024 * 1024) -> str:
        if not path.exists():
            return ""
        # 超過 2MB 只讀最後 2MB，避免大型 log 吃光記憶體
        size = path.stat().st_size
        if size > max_bytes:
            with path.open("rb") as f:
                f.seek(-max_bytes, 2)
                return f.read().decode("utf-8", errors="replace")
        return path.read_text(encoding="utf-8", errors="replace")

    def status_for(exit_code: str) -> tuple[str, str, str]:
        if exit_code == "0":
            return "成功", "✅", "#16a34a"
        if exit_code == "missing":
            return "尚無紀錄", "⚪", "#64748b"
        return "失敗", "❌", "#dc2626"

    def important_lines(content: str, limit: int = 8) -> list[str]:
        if not content:
            return ["沒有 log 內容"]

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return ["沒有 log 內容"]

        keywords = [
            "Traceback", "RuntimeError", "ModuleNotFoundError",
            "Error", "ERROR", "Exception", "SMTPAuthenticationError",
            "HttpError", "failed", "Failed", "失敗", "錯誤", "找不到", "登入失敗",
        ]

        matched = [line for line in lines if any(k in line for k in keywords)]

        if matched:
            return matched[-limit:]

        return lines[-min(limit, len(lines)):]

    def summary_line(content: str, exit_code: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return "沒有摘要"

        if exit_code == "0":
            success_keywords = ["全部完成", "執行完成", "已上傳", "完成"]
            for keyword in success_keywords:
                for line in reversed(lines):
                    if keyword in line:
                        return line[-130:]

        return important_lines(content, limit=1)[-1][-130:]

    st.markdown(
        """
        <style>
          .log-title { font-size:2.2rem; font-weight:900; color:#0f172a; letter-spacing:1px; margin-bottom:4px; }
          .log-subtitle { font-size:0.82rem; font-weight:800; color:#94a3b8; letter-spacing:4px; margin-top:6px; margin-bottom:22px; }
          .log-top-card { background:linear-gradient(135deg,#ffffff,#f8fafc); border:1px solid #e2e8f0; border-radius:24px; padding:26px; margin:20px 0 26px 0; box-shadow:0 12px 30px rgba(15,23,42,0.07); }
          .log-overall { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; flex-wrap:wrap; }
          .log-overall-title { font-size:1.55rem; font-weight:900; color:#0f172a; }
          .log-overall-note { color:#64748b; margin-top:8px; font-size:0.98rem; }
          .overall-status { font-size:1.55rem; font-weight:900; }
          .result-strip { display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; }
          .result-pill { background:#f1f5f9; border:1px solid #e2e8f0; border-radius:999px; padding:9px 14px; color:#334155; font-weight:900; font-size:0.93rem; }
          .error-box { background:#fff1f2; border:1px solid #fecdd3; color:#991b1b; border-radius:14px; padding:12px 14px; margin-top:12px; font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; font-size:0.84rem; line-height:1.55; white-space:pre-wrap; }
        </style>
        <div class="log-title">📋 今日排程狀態</div>
        <div class="log-subtitle">DAILY SCHEDULE STATUS · ACTION LOGS</div>
        """,
        unsafe_allow_html=True,
    )

    top_cols = st.columns([1, 1, 2])
    with top_cols[0]:
        if st.button("← 返回主控台", use_container_width=True):
            set_view("main")
            st.rerun()
    with top_cols[1]:
        if st.button("🔄 重新讀取", use_container_width=True):
            st.rerun()

    log_root = Path("logs")

    if not log_root.exists():
        st.info("目前沒有 logs/ 資料夾。請確認 GitHub Actions 已將 logs/ commit 回 repo。")
        return

    day_dirs = sorted([p for p in log_root.iterdir() if p.is_dir()], reverse=True)

    if not day_dirs:
        st.info("目前 logs/ 裡沒有日期資料夾。")
        return

    today = today_key()
    day_options = [p.name for p in day_dirs]
    default_index = day_options.index(today) if today in day_options else 0

    selected_day = st.selectbox("選擇日期", day_options, index=default_index)

    selected_dir = log_root / selected_day
    log_files = sorted(selected_dir.glob("*.log"))

    if not log_files:
        st.info(f"{selected_day} 沒有 log 檔。")
        return

    rows = []

    for log_file in log_files:
        job_name = log_file.stem
        exit_file = log_file.with_suffix(".exit")
        exit_code = read_text_safe(exit_file).strip() if exit_file.exists() else "missing"
        status_text, icon, color = status_for(exit_code)
        content = read_text_safe(log_file)

        rows.append(
            {
                "job_name": job_name,
                "label": JOB_LABELS.get(job_name, job_name),
                "content": content,
                "exit_code": exit_code,
                "status_text": status_text,
                "icon": icon,
                "color": color,
                "summary": summary_line(content, exit_code),
                "important": important_lines(content),
            }
        )

    success_count = sum(1 for r in rows if r["exit_code"] == "0")
    failed_rows = [r for r in rows if r["exit_code"] not in ["0", "missing"]]
    failed_count = len(failed_rows)
    missing_count = sum(1 for r in rows if r["exit_code"] == "missing")

    if failed_count:
        overall_text, overall_icon, overall_color = "有失敗項目", "❌", "#dc2626"
    elif success_count:
        overall_text, overall_icon, overall_color = "全部成功", "✅", "#16a34a"
    else:
        overall_text, overall_icon, overall_color = "尚無成功紀錄", "⚪", "#64748b"

    failed_summary_html = ""
    if failed_rows:
        items = []
        for r in failed_rows:
            msg = "\n".join(r["important"][:5])
            items.append(f"<div style='margin-top:10px;'><b>{html.escape(r['label'])}</b><br>{html.escape(msg)}</div>")
        failed_summary_html = "<div class='error-box'><b>失敗項目與錯誤內容：</b>" + "".join(items) + "</div>"

    st.markdown(
        f"""
        <div class="log-top-card">
          <div class="log-overall">
            <div>
              <div class="log-overall-title">今日排程</div>
              <div class="log-overall-note">日期：{html.escape(selected_day)}。以執行結果為主，失敗內容會優先顯示。</div>
            </div>
            <div class="overall-status" style="color:{overall_color};">{overall_icon} {overall_text}</div>
          </div>
          <div class="result-strip">
            <div class="result-pill">✅ 成功：{success_count}</div>
            <div class="result-pill">❌ 失敗：{failed_count}</div>
            <div class="result-pill">⚪ 尚無紀錄：{missing_count}</div>
            <div class="result-pill">📄 Log 檔：{len(log_files)}</div>
          </div>
          {failed_summary_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("🔎 查看詳細 log", expanded=False):
        tabs = st.tabs([f"{row['icon']} {row['label']}" for row in rows])

        for tab, row in zip(tabs, rows):
            with tab:
                json_file = Path("logs") / selected_day / f"{row['job_name']}.json"
                meta = {}
                if json_file.exists():
                    try:
                        meta = json.loads(json_file.read_text(encoding="utf-8"))
                    except Exception:
                        meta = {}

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.caption("開始時間")
                    st.write(meta.get("started_at", "-"))
                with c2:
                    st.caption("完成時間")
                    st.write(meta.get("finished_at", "-"))
                with c3:
                    st.caption("耗時")
                    st.write(f"{meta.get('duration_seconds', '-')} 秒")

                st.divider()

                if row["exit_code"] == "0":
                    st.success(f"{row['label']}：成功")
                elif row["exit_code"] == "missing":
                    st.warning(f"{row['label']}：尚無 exit code")
                else:
                    st.error(f"{row['label']}：失敗 / exit code {row['exit_code']}")
                    with st.expander("查看錯誤詳細 log", expanded=False):
                        st.code(row["content"] or "空 log", language="text")


# ═══════════════════════════════════════════════════════════
# 系統 / 功能設定
# ═══════════════════════════════════════════════════════════
def start_local_agent_service(*, month="", start_date=None, end_date=None, area="全區"):
    script = BASE_DIR / "scripts" / "local_agent_service.sh"
    if sys.platform != "darwin" or not script.exists():
        raise RuntimeError("Mac 目前離線；請先在 Mac 安裝 launchd 服務")
    result = subprocess.run(
        [str(script), "install"],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Agent 啟動失敗").strip())
    return "本機 Agent 已同步最新版並啟動"


def queue_cetustek_login(*, month="", start_date=None, end_date=None, area="全區"):
    task = create_local_agent_task(
        "cetustek.login",
        {"area": area, "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_cetustek_download(*, month="", start_date=None, end_date=None, area="全區"):
    start_text = start_date.isoformat() if start_date else ""
    end_text = end_date.isoformat() if end_date else ""
    task = create_local_agent_task(
        "cetustek.download",
        {
            "month": str(month or "").strip(),
            "start_date": start_text,
            "end_date": end_text,
            "area": area,
            "format": "csv",
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def run_cetustek_paper_update(*, month="", start_date=None, end_date=None, area="全區"):
    month_text = str(month or "").strip()
    if not month_text:
        raise ValueError("紙本發票更新請輸入月份 YYYYMM")
    from tools.invoice_center.invoice_archive import run_invoice_update

    return run_invoice_update("paper", month_text, area)


def run_cetustek_prize_update(*, month="", start_date=None, end_date=None, area="全區"):
    month_text = str(month or "").strip()
    if not month_text:
        raise ValueError("中獎發票更新請輸入 8 碼期別，例如 20260506")
    from tools.invoice_center.invoice_archive import run_invoice_update

    return run_invoice_update("prize", month_text, area)


def queue_cetustek_allowance(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    if not selected_rows:
        raise ValueError("請先勾選要開立折讓單的資料")
    task = create_local_agent_task("cetustek.allowance", {"area": area, "selected_rows": selected_rows, "cdp_url": "http://127.0.0.1:9222"}, created_by=st.session_state.get("username", "Tool System"))
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_newebpay_login(*, month="", start_date=None, end_date=None, area="全區"):
    task = create_local_agent_task(
        "newebpay.login",
        {"area": area, "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_newebpay_download(*, month="", start_date=None, end_date=None, area="全區"):
    if month and not start_date and not end_date:
        import calendar

        month_text = str(month).strip().replace("-", "").replace("/", "")
        if len(month_text) != 6 or not month_text.isdigit():
            raise ValueError("月份格式請使用 YYYYMM")
        year, month_number = int(month_text[:4]), int(month_text[4:])
        last_day = calendar.monthrange(year, month_number)[1]
        start_date = datetime(year, month_number, 1).date()
        end_date = datetime(year, month_number, last_day).date()
    task = create_local_agent_task(
        "newebpay.download",
        {
            "start_date": start_date.isoformat() if start_date else "",
            "end_date": end_date.isoformat() if end_date else "",
            "area": area,
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_newebpay_invoice_amounts(*, month="", start_date=None, end_date=None, area="全區"):
    if month:
        months = str(month).strip()
    elif start_date and end_date:
        start_month = start_date.strftime("%Y%m")
        end_month = end_date.strftime("%Y%m")
        months = start_month if start_month == end_month else f"{start_month}-{end_month}"
    else:
        raise ValueError("請選擇日期區間")
    task = create_local_agent_task(
        "newebpay.invoice_amounts",
        {"months": months, "area": area, "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_newebpay_refund_pending(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    if not selected_rows:
        raise ValueError("請先勾選要退款的資料")
    task = create_local_agent_task(
        "newebpay.refund_pending",
        {"area": area, "selected_rows": selected_rows, "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_lemon_stored_value_adjustment(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    if not selected_rows:
        raise ValueError("請先勾選要異動儲值金的資料")
    task = create_local_agent_task(
        "lemon.stored_value_adjustment",
        {"area": area, "selected_rows": selected_rows, "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_fubon_login(*, month="", start_date=None, end_date=None, area="全區"):
    mobile = st.session_state.get("fubon_verification_mode") == "手機輸入驗證碼"
    task = create_local_agent_task(
        "fubon.captcha" if mobile else "fubon.login",
        {"area": area, "resume": "login", "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_fubon_download(*, month="", start_date=None, end_date=None, area="全區"):
    mobile = st.session_state.get("fubon_verification_mode") == "手機輸入驗證碼"
    task = create_local_agent_task(
        "fubon.captcha" if mobile else "fubon.download",
        {
            "start_date": start_date.isoformat() if start_date else "",
            "end_date": end_date.isoformat() if end_date else "",
            "area": area,
            "resume": "download",
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_fubon_atm_refund(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    if not selected_rows:
        raise ValueError("請先勾選要處理的 ATM 退款資料")
    task = create_local_agent_task(
        "fubon.atm_refund",
        {
            "area": area,
            "selected_rows": selected_rows,
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_fubon_payment_request(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    if not selected_rows:
        raise ValueError("請先勾選要處理的請款記錄資料")
    task = create_local_agent_task(
        "fubon.payment_request",
        {
            "area": area,
            "selected_rows": selected_rows,
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_fubon_deposit_refund(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    if not selected_rows:
        raise ValueError("請先勾選要處理的工具包押金退款資料")
    task = create_local_agent_task(
        "fubon.deposit_refund",
        {
            "area": area,
            "selected_rows": selected_rows,
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_fubon_supply_purchase(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    if not month:
        raise ValueError("請先輸入採購月份")
    if not selected_rows:
        raise ValueError("請先勾選要處理的清潔用品採購資料")
    task = create_local_agent_task(
        "fubon.supply_purchase",
        {
            "area": area,
            "month": month,
            "selected_rows": selected_rows,
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


@st.fragment(run_every="2s")
def render_fubon_mobile_verification():
    try:
        captcha_tasks = list_local_agent_tasks(limit=40)
        verified_tokens = set()
        for item in captcha_tasks:
            if item.get("action") == "fubon.verify":
                try:
                    verified_tokens.add(json.loads(item.get("params_json") or "{}").get("session_token", ""))
                except json.JSONDecodeError:
                    pass
        captcha_payload = None
        for item in captcha_tasks:
            if item.get("action") != "fubon.captcha" or item.get("status") != "completed":
                continue
            try:
                payload = json.loads(item.get("result_json") or "{}")
            except json.JSONDecodeError:
                continue
            if payload.get("image_base64") and payload.get("session_token") not in verified_tokens:
                captcha_payload = payload
                break
        if not captcha_payload:
            st.caption("驗證碼擷取中…")
            return
        st.markdown("#### 富邦手機驗證")
        st.image(base64.b64decode(captcha_payload["image_base64"]))
        captcha_code = st.text_input("輸入圖片驗證碼", max_chars=12, key="fubon_mobile_captcha")
        if st.button("送出驗證碼並繼續", type="primary", use_container_width=True):
            if not captcha_code.strip():
                st.error("請輸入驗證碼")
                return
            task = create_local_agent_task(
                "fubon.verify",
                {
                    "area": captcha_payload.get("area", ""),
                    "resume": captcha_payload.get("resume", "login"),
                    "start_date": captcha_payload.get("start_date", ""),
                    "end_date": captcha_payload.get("end_date", ""),
                    "session_token": captcha_payload.get("session_token", ""),
                    "captcha": captcha_code.strip(),
                    "cdp_url": "http://127.0.0.1:9222",
                },
                created_by=st.session_state.get("username", "Tool System"),
            )
            st.success(f"驗證任務已建立：{task['task_id']}")
            st.rerun()
    except Exception as exc:
        st.warning(f"富邦手機驗證資料讀取失敗：{exc}")


def queue_yuanta_login(*, month="", start_date=None, end_date=None, area="全區"):
    task = create_local_agent_task(
        "yuanta.login",
        {"area": area, "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_yuanta_download(*, month="", start_date=None, end_date=None, area="全區"):
    task = create_local_agent_task(
        "yuanta.download",
        {
            "start_date": start_date.isoformat() if start_date else "",
            "end_date": end_date.isoformat() if end_date else "",
            "area": area,
            "cdp_url": "http://127.0.0.1:9222",
        },
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def queue_yuanta_salary_status(*, month="", start_date=None, end_date=None, area="全區"):
    task = create_local_agent_task(
        "yuanta.salary_status",
        {"area": area, "cdp_url": "http://127.0.0.1:9222"},
        created_by=st.session_state.get("username", "Tool System"),
    )
    return f"任務已建立：{task['task_id']}（等待本機 Agent）"


def run_deposit_report_aggregate(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    from tools.finance_management.deposit_report import aggregate_month_to_uy

    if area not in ("台北", "台中"):
        raise ValueError("請選擇台北或台中")
    year_month = (month or "").strip()
    if not re.fullmatch(r"\d{6}", year_month):
        raise ValueError("請輸入 6 位數月份（YYYYMM），例如 202608")
    written = aggregate_month_to_uy(area, year_month)
    return (
        f"完成：{area} {year_month} 已新增 {written} 列到 U–X"
        if written
        else f"完成：{area} {year_month} 無資料可輸出"
    )


def run_deposit_report_mark_from_notes(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    from tools.finance_management.deposit_report import mark_from_notes

    if area not in ("台北", "台中"):
        raise ValueError("請選擇台北或台中")
    range_input = st.session_state.get("finance_deposit_note_range", "").strip()
    if not range_input:
        raise ValueError("請輸入備註範圍，例如：U22:U25 或 22-25")
    result = mark_from_notes(area, range_input)
    message = (
        f"完成：處理 {result['processed_rows']} 行，"
        f"有效備註 {result['valid_notes']} 筆，打勾 {result['marked']} 個"
    )
    missing = result.get("missing_names") or []
    if missing:
        preview = "、".join(missing[:5])
        more = f"…等共 {len(missing)} 個" if len(missing) > 5 else ""
        message += f"；找不到姓名：{preview}{more}"
    return message


def run_deposit_report_flag_discrepancies(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    from tools.finance_management.deposit_report import flag_discrepancies

    if area not in ("台北", "台中"):
        raise ValueError("請選擇台北或台中")
    result = flag_discrepancies(area)
    return f"完成：檢查 {result['checked']} 人（今年已上課），標記異常 {result['flagged']} 筆"


def _get_vip_workflow():
    from config.vip_config import MASTER_SPREADSHEET_ID, ROOT_FOLDER_ID
    from services.google_auth import get_drive_service, get_gspread_client
    from services.google_drive import DriveService
    from services.google_sheets import SheetsService
    from services.vip_workflow import VipStoredValueWorkflow

    sheets = SheetsService(get_gspread_client())
    drive = DriveService(get_drive_service())
    return VipStoredValueWorkflow(drive, sheets, MASTER_SPREADSHEET_ID, ROOT_FOLDER_ID)


def _format_step_results(results) -> str:
    parts = []
    for res in results if isinstance(results, list) else [results]:
        text = "、".join(res.messages) if res.messages else ""
        if res.errors:
            text = (text + "；" if text else "") + "❌ " + "；".join(res.errors)
        parts.append(f"{res.step}：{text or '無訊息'}")
    return " ｜ ".join(parts)


def run_vip_copy_period_file(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    period = (month or "").strip()
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202607")
    workflow = _get_vip_workflow()
    result = workflow.create_monthly_summary(period)
    return _format_step_results(result)


def run_vip_convert_files(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    period = (month or "").strip()
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202607")
    workflow = _get_vip_workflow()
    result = workflow.convert_files(period)
    return _format_step_results(result)


def run_vip_move_files(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    period = (month or "").strip()
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202607")
    workflow = _get_vip_workflow()
    result = workflow.move_files(period)
    return _format_step_results(result)


def run_vip_apply_formulas(*, month="", start_date=None, end_date=None, area="全區", selected_rows=None):
    period = (month or "").strip()
    if not re.fullmatch(r"\d{6}", period):
        raise ValueError("請輸入 6 位數期別（YYYYMM），例如 202607")
    workflow = _get_vip_workflow()
    result = workflow.apply_formulas(period)
    return _format_step_results(result)


FINANCE_TASKS = [
    {"name": "【本機 Agent】同步／啟動", "handler": start_local_agent_service, "enabled": True},
    {"name": "【檸檬後台】異動儲值金", "handler": queue_lemon_stored_value_adjustment, "enabled": True},
    {"name": "【富邦銀行】富邦登入", "handler": queue_fubon_login, "enabled": True},
    {"name": "【富邦銀行】富邦明細下載", "handler": queue_fubon_download, "enabled": True},
    {"name": "【富邦銀行】異動 ATM 退款", "handler": queue_fubon_atm_refund, "enabled": True},
    {"name": "【富邦銀行】請款記錄", "handler": queue_fubon_payment_request, "enabled": True},
    {"name": "【富邦銀行】工具包押金退款", "handler": queue_fubon_deposit_refund, "enabled": True},
    {"name": "【富邦銀行】清潔用品採購", "handler": queue_fubon_supply_purchase, "enabled": True},
    {"name": "【元大銀行】元大登入", "handler": queue_yuanta_login, "enabled": True},
    {"name": "【元大銀行】元大明細下載", "handler": queue_yuanta_download, "enabled": True},
    {"name": "【元大銀行】檢查薪資付款狀態", "handler": queue_yuanta_salary_status, "enabled": True},
    {"name": "【鯨躍發票】開立發票", "handler": None, "enabled": True},
    {"name": "【鯨躍發票】鯨躍登入", "handler": queue_cetustek_login, "enabled": True},
    {"name": "【鯨躍發票】鯨躍發票下載", "handler": queue_cetustek_download, "enabled": True},
    {"name": "【鯨躍發票】紙本發票更新", "handler": run_cetustek_paper_update, "enabled": True},
    {"name": "【鯨躍發票】中獎發票更新", "handler": run_cetustek_prize_update, "enabled": True},
    {"name": "【鯨躍發票】開立折讓單", "handler": queue_cetustek_allowance, "enabled": True},
    {"name": "【藍新金流】藍新登入", "handler": queue_newebpay_login, "enabled": True},
    {"name": "【藍新金流】藍新收退款下載", "handler": queue_newebpay_download, "enabled": True},
    {"name": "【藍新金流】藍新手續費發票金額", "handler": queue_newebpay_invoice_amounts, "enabled": True},
    {"name": "【藍新金流】藍新信用卡待退款", "handler": queue_newebpay_refund_pending, "enabled": True},
    {"name": "【財報】工具包押金｜統計月份彙整（U–X）", "handler": run_deposit_report_aggregate, "enabled": True},
    {"name": "【財報】工具包押金｜批次依備註打勾（J–M）", "handler": run_deposit_report_mark_from_notes, "enabled": True},
    {"name": "【財報】工具包押金｜比對異常標記（N欄）", "handler": run_deposit_report_flag_discrepancies, "enabled": True},
    {"name": "【儲值金】複製期別檔案", "handler": run_vip_copy_period_file, "enabled": True},
    {"name": "【儲值金】轉檔", "handler": run_vip_convert_files, "enabled": True},
    {"name": "【儲值金】搬運", "handler": run_vip_move_files, "enabled": True},
    {"name": "【儲值金】套用公式", "handler": run_vip_apply_formulas, "enabled": True},
]

SYSTEM_FUNCTIONS_BY_TYPE = {
    "orders_memo_system": [
        "批次建單", "舊客建單", "新客建單", "儲值金建單", "訂單轉換",
        "儲值金補價差", "排班管理", "LINE通知訊息", "訂單備註", "對帳管理",
        "異動管理", "評估工具", "雙向訂單檢查", "查詢無LINE連結訂單", "儲值獎金備註",
    ],
    "vip": [
        "建立當月彙整檔",
        "轉檔＋高雄/新竹彙整",
        "搬運",
        "計算",
        "彙整金額",
        "一鍵執行：建立＋轉檔＋搬運＋計算＋彙整金額",
    ],
    "daily_scheduler": [
        "排班統計表",
        "專員班表",
        "專員個資",
        "當月次月訂單",
        "業績報表",
        "一鍵執行日排程",
    ],
    "monthly_scheduler": [
        "上半月訂單",
        "下半月訂單",
        "已退款",
        "預收",
        "儲值金結算",
        "儲值金預收",
        "一鍵執行月排程",
    ],
    "field_daily_schedule": [
        "外場排班統計表",
        "外場專員班表",
        "外場當月次月訂單",
        "外場專員個資",
        "一鍵執行外場日排程",
    ],
    # ── ★ 新增：客服排程系統功能清單 ────────────────────────
    "service_schedule": [
        # 每日排班管理
        "【排班】更新排班統計表",
        "【排班】更新每日回報",
        "【排班】更新前一天營業分數及營業額",
        "【排班】三步驟全部執行",
        # CRM 排程管理
        "【CRM】更新排程決策報表",
        "【CRM】更新三個月未排名單",
        "【CRM】重新排序Raw",
        # 儲值金服務排程管理
        "【儲值】抓儲值金",
        "【儲值】匯出VIP日曆對帳",
        "【儲值】全跑（抓儲值金＋匯出VIP）",
    ],
    # ────────────────────────────────────────────────────────
    # ── ★ 新增：gmail401 ────────────────────────
    "gmail_401": [
    "掃描並歸檔401附件",
    ],
    # ────────────────────────────────────────────────────────
    "finance_management": [task["name"] for task in FINANCE_TASKS],
}

DAILY_SCRIPT_MAP = {
    "排班統計表": "tools/scheduled_daily/schedule_report.py",
    "專員班表": "tools/scheduled_daily/staff_schedule.py",
    "專員個資": "tools/scheduled_daily/staff_info.py",
    "當月次月訂單": "tools/scheduled_daily/orders_report.py",
    "業績報表": "tools/scheduled_daily/performance_report.py",
}

MONTHLY_SCRIPT_MAP = {
    "上半月訂單": ["tools/scheduled_monthly/half_month_orders.py", "1"],
    "下半月訂單": ["tools/scheduled_monthly/half_month_orders.py", "2"],
    "已退款": ["tools/scheduled_monthly/refund_report.py"],
    "預收": ["tools/scheduled_monthly/prepaid_report.py"],
    "儲值金結算": ["tools/scheduled_monthly/stored_value_settlement.py"],
    "儲值金預收": ["tools/scheduled_monthly/stored_value_prepaid.py"],
}

DAILY_TARGET_MAP = {
    "一鍵執行日排程": "all",
    "排班統計表": "schedule_report",
    "專員班表": "staff_schedule",
    "當月次月訂單": "orders_report",
    "專員個資": "staff_info",
}

FIELD_SCRIPT_MAP = {
    "外場排班統計表": [
        "module:tools.field_management.scheduler",
        "--target",
        "schedule_stats",
    ],

    "外場專員班表": [
        "module:tools.field_management.scheduler",
        "--target",
        "staff_schedule",
    ],

    "外場當月次月訂單": [
        "module:tools.field_management.scheduler",
        "--target",
        "orders",
    ],

    "外場專員個資": [
        "module:tools.field_management.scheduler",
        "--target",
        "staff_profile",
    ],

    "一鍵執行外場日排程": [
        "module:tools.field_management.scheduler",
        "--target",
        "all",
    ],
}

# ── ★ 客服排程：功能 → --step 對應表 ───────────────────────
SERVICE_STEP_MAP = {
    "【排班】更新排班統計表":           "1",
    "【排班】更新每日回報":             "2",
    "【排班】更新前一天營業分數及營業額": "3",
    "【排班】三步驟全部執行":           "0",
}

# ── ★ CRM：功能 → crm_export.py --step 對應表 ───────────────
SERVICE_CRM_MAP = {
    # 儲值金服務排程管理
    "【儲值】抓儲值金":                  "1",
    "【儲值】匯出VIP日曆對帳":           "2",
    "【儲值】全跑（抓儲值金＋匯出VIP）":  "0",
    # CRM 排程管理
    "【CRM】更新排程決策報表":           "4",
    "【CRM】更新三個月未排名單":         "5",
    "【CRM】重新排序Raw":               "6",
}
# ────────────────────────────────────────────────────────────


def functions_for_system(sys_cfg: dict) -> list[str]:
    system_type = sys_cfg.get("type", "vip")
    return SYSTEM_FUNCTIONS_BY_TYPE.get(system_type, ["尚未設定功能"])


# ═══════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════
require_login()

sync_view_from_query_params()

config = merge_legacy_secrets(load_config())
_log_id = config.get("log_spreadsheet_id", "").strip()
if _log_id and not os.getenv("TOOLS_APP_LOG_SPREADSHEET_ID"):
    os.environ["TOOLS_APP_LOG_SPREADSHEET_ID"] = _log_id

systems = [
    s for s in get_enabled_systems(config)
    if can_access_system(s.get("type", ""))
    and s.get("type", "") != "vip"  # 儲值金管理已收進財務管理，不再獨立顯示
]
system_names = [s.get("name", "") for s in systems if s.get("name")]

if not system_names:
    st.error("目前角色沒有可執行的系統權限，請聯絡管理員。")
    st.stop()

if st.session_state.view == "report":
    if not can_access_page("report"):
        st.error("你沒有權限查看業績報表")
        st.stop()
    render_report()
    st.stop()

if st.session_state.view == "log":
    if not can_access_page("log"):
        st.error("你沒有權限查看排程 Log")
        st.stop()
    render_log_page()
    st.stop()

# ── ★ Gmail 401歸檔 ──────────────────────────────────────
selected_system_cfg = get_system_by_name(config, st.session_state.get("selected_system_name", ""))
if selected_system_cfg.get("type") == "gmail_401":
    from tools.gmail_401.gmail_401 import run_gmail_401
    run_gmail_401()
    st.stop()
# ────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════
# UI — 主標題
# ═══════════════════════════════════════════════════════════
st.markdown(
    '<div class="app-title">🧰 Tools App 作業系統</div>',
    unsafe_allow_html=True,
)
render_user_bar()

try:
    _agent_rows = list_local_agent_status(max_age_seconds=30)
    _online_agents = [row for row in _agent_rows if row.get("online")]
    if _online_agents:
        _agent = _online_agents[0]
        st.success(
            f"Local Agent Online｜{_agent.get('agent_id', '')}｜最後心跳 {_agent.get('last_seen', '')}",
            icon="🟢",
        )
    else:
        _last_seen = _agent_rows[0].get("last_seen", "尚無心跳") if _agent_rows else "尚無心跳"
        st.error(f"Local Agent Offline｜最後心跳 {_last_seen}", icon="🔴")
except Exception:
    st.error("Local Agent Offline｜無法讀取心跳", icon="🔴")


# ═══════════════════════════════════════════════════════════
# UI — 執行設定
# ═══════════════════════════════════════════════════════════
st.markdown('<div class="card">', unsafe_allow_html=True)

head_left, head_orders, head_report, head_log = st.columns([1.7, 1, 1, 1])

with head_left:
    st.markdown('<div class="card-title">⚙️ 執行設定</div>', unsafe_allow_html=True)

with head_orders:
    if can_access_system("orders_memo_system"):
        st.page_link("pages/訂單系統.py", label="🧹 訂單系統", use_container_width=True)

with head_report:
    if can_access_page("report"):
        if st.button("📊 查看業績報表", use_container_width=True):
            set_view("report")
            st.rerun()
    else:
        st.caption("無業績報表權限")

with head_log:
    if can_access_page("log"):
        if st.button("📋 查看排程 Log", use_container_width=True):
            set_view("log")
            st.rerun()
    else:
        st.caption("無 Log 權限")

sys_col, func_col = st.columns([1, 1])

with sys_col:
    st.markdown('<div class="field-label">🗂️ 執行系統</div>', unsafe_allow_html=True)

    default_index = 0
    if st.session_state.selected_system_name in system_names:
        default_index = system_names.index(st.session_state.selected_system_name)

    system_name = st.selectbox(
        "執行系統",
        system_names,
        index=default_index,
        label_visibility="collapsed",
        key="system_name",
    )

    st.session_state.selected_system_name = system_name

selected_system = get_system_by_name(config, system_name)
system_type = selected_system.get("type", "vip")

with func_col:
    st.markdown('<div class="field-label">🎯 執行功能</div>', unsafe_allow_html=True)

    selected_function = st.selectbox(
        "執行功能",
        functions_for_system(selected_system),
        label_visibility="collapsed",
        key="selected_function",
    )

if system_type == "finance_management" and selected_function == "【鯨躍發票】開立發票":
    st.markdown("</div>", unsafe_allow_html=True)
    from tools.invoice_center.ui import render_invoice_create

    render_invoice_create()
    st.stop()

if system_type == "orders_memo_system":
    from tools.memo_system.ui import render_memo_system
    from tools.orders_system.ui import render_orders_system

    _orders_map = {
        "批次建單": ("orders", "批次建單（Google Sheet）"), "舊客建單": ("orders", "舊客快速建單"),
        "新客建單": ("orders", "新客資料拆解"), "儲值金建單": ("orders", "儲值金購買"),
        "訂單轉換": ("orders", "訂單轉換"), "儲值金補價差": ("orders", "儲值金補價差"),
        "排班管理": ("memo", "📅 排班管理"), "LINE通知訊息": ("orders", "LINE 通知產生器"),
        "訂單備註": ("memo", "📋 客服作業"), "對帳管理": ("memo", "💰 財務對帳"),
        "異動管理": ("memo", "🔄 服務異動"), "評估工具": ("memo", "📐 評估文字工具"),
        "雙向訂單檢查": ("orders", "雙向訂單檢查"),
        "查詢無LINE連結訂單": ("orders", "查詢無LINE連結訂單"),
        "儲值獎金備註": ("orders", "儲值獎金備註"),
    }
    _login_a, _login_b, _login_c = st.columns([3, 3, 1])
    with _login_a:
        _backend_email = st.text_input("後台帳號", key="orders_backend_email")
    with _login_b:
        _backend_password = st.text_input("後台密碼", type="password", key="orders_backend_password")
    with _login_c:
        _backend_env = st.selectbox("環境", ["prod", "dev"], key="orders_backend_env")
    _kind, _mode = _orders_map[selected_function]
    if _kind == "orders":
        render_orders_system(forced_mode=_mode, shared_backend_email=_backend_email,
                             shared_backend_password=_backend_password, shared_env=_backend_env)
    else:
        render_memo_system(forced_main_section=_mode, shared_backend_email=_backend_email,
                           shared_backend_password=_backend_password, shared_env=_backend_env)
    st.stop()

monthly_order_functions = ["上半月訂單", "下半月訂單"]

period = ""
start_date_value = None
end_date_value = None
monthly_date_mode = "期別"

date_col, area_col = st.columns([2, 1])

with date_col:
    if system_type == "monthly_scheduler" and selected_function in monthly_order_functions:
        st.markdown('<div class="field-label">📆 執行方式</div>', unsafe_allow_html=True)

        monthly_date_mode = st.radio(
            "執行方式",
            ["期別", "日期區間"],
            horizontal=True,
            label_visibility="collapsed",
            key="monthly_date_mode",
        )

        if monthly_date_mode == "期別":
            period_default = tw_now_text("%Y%m") + ("-1" if selected_function == "上半月訂單" else "-2")
            period = st.text_input(
                "執行期別",
                value=period_default,
                placeholder="例如：202605-1 或 202605-2",
                label_visibility="collapsed",
                key=f"period_monthly_order_{selected_function}",
            )
        else:
            d1, d2 = st.columns(2)
            today_date = datetime.now(TW_TZ).date()
            with d1:
                start_date_value = st.date_input("開始日期", value=today_date, key="monthly_start_date")
            with d2:
                end_date_value = st.date_input("結束日期", value=today_date, key="monthly_end_date")
            period = start_date_value.strftime("%Y%m%d")

    elif system_type == "finance_management":
        today_date = datetime.now(TW_TZ).date()
        if selected_function == "【本機 Agent】啟動":
            st.markdown('<div class="field-label">📆 執行期間</div>', unsafe_allow_html=True)
            st.info("啟動 launchd 常駐服務，不需選擇日期", icon="🟢")
        elif selected_function in (
            "【鯨躍發票】鯨躍登入",
            "【藍新金流】藍新登入",
            "【富邦銀行】富邦登入",
            "【元大銀行】元大登入",
        ):
            st.markdown('<div class="field-label">📆 執行期間</div>', unsafe_allow_html=True)
            st.info("登入不需選擇月份或日期", icon="🔐")
            if selected_function == "【富邦銀行】富邦登入":
                st.radio(
                    "驗證碼輸入方式",
                    ["Mac 人工輸入驗證碼", "手機輸入驗證碼"],
                    horizontal=True,
                    key="fubon_verification_mode",
                )
        elif selected_function == "【元大銀行】檢查薪資付款狀態":
            st.markdown('<div class="field-label">📆 付款日期</div>', unsafe_allow_html=True)
            st.info("固定查詢最近一週", icon="📅")
        elif selected_function == "【藍新金流】藍新信用卡待退款":
            st.markdown('<div class="field-label">📆 查詢期間</div>', unsafe_allow_html=True)
            st.info("固定查詢今天起往前 85 天", icon="📅")
        elif selected_function == "【檸檬後台】異動儲值金":
            st.markdown('<div class="field-label">📆 異動日期</div>', unsafe_allow_html=True)
            st.info("備註日期使用今天（台北時間）", icon="📅")
        elif selected_function == "【富邦銀行】富邦明細下載":
            st.markdown('<div class="field-label">📆 日期區間</div>', unsafe_allow_html=True)
            d1, d2 = st.columns(2)
            with d1:
                start_date_value = st.date_input(
                    "開始日期",
                    value=today_date - timedelta(days=1),
                    key="finance_fubon_start_date",
                )
            with d2:
                end_date_value = st.date_input(
                    "結束日期",
                    value=today_date,
                    key="finance_fubon_end_date",
                )
            st.radio(
                "驗證碼輸入方式",
                ["Mac 人工輸入驗證碼", "手機輸入驗證碼"],
                horizontal=True,
                key="fubon_verification_mode",
            )
        elif selected_function == "【鯨躍發票】鯨躍發票下載":
            st.markdown('<div class="field-label">📆 下載期間</div>', unsafe_allow_html=True)
            finance_invoice_date_mode = st.radio(
                "期間選擇",
                ["月份", "日期區間"],
                horizontal=True,
                label_visibility="collapsed",
                key="finance_invoice_date_mode",
            )
            if finance_invoice_date_mode == "月份":
                period = st.text_input(
                    "月份",
                    value=today_date.strftime("%Y%m"),
                    placeholder="例如：202608",
                    label_visibility="collapsed",
                    key="finance_invoice_month",
                )
                st.caption("格式：YYYYMM；下載該月份完整發票資料")
            else:
                period = ""
                d1, d2 = st.columns(2)
                with d1:
                    start_date_value = st.date_input(
                        "開始日期",
                        value=today_date.replace(day=1),
                        key="finance_invoice_start_date",
                    )
                with d2:
                    end_date_value = st.date_input(
                        "結束日期",
                        value=today_date,
                        key="finance_invoice_end_date",
                    )
        elif selected_function in (
            "【鯨躍發票】紙本發票更新",
            "【鯨躍發票】中獎發票更新",
        ):
            st.markdown('<div class="field-label">📆 更新月份</div>', unsafe_allow_html=True)
            _is_prize_update = selected_function == "【鯨躍發票】中獎發票更新"
            period = st.text_input(
                "期別" if _is_prize_update else "月份",
                value="" if _is_prize_update else today_date.strftime("%Y%m"),
                placeholder="例如：20260506" if _is_prize_update else "例如：202607",
                label_visibility="collapsed",
                key=f"finance_invoice_update_month_{selected_function}",
            )
            if _is_prize_update:
                st.caption("格式：YYYYMMNN；例如 20260506")
            else:
                st.caption("格式：YYYYMM；更新該月份紙本發票")
        elif selected_function == "【藍新金流】藍新收退款下載":
            st.markdown('<div class="field-label">📆 下載期間</div>', unsafe_allow_html=True)
            newebpay_date_mode = st.radio(
                "期間選擇",
                ["月份", "日期區間"],
                horizontal=True,
                label_visibility="collapsed",
                key="finance_newebpay_date_mode",
            )
            if newebpay_date_mode == "月份":
                period = st.text_input(
                    "月份",
                    value=today_date.strftime("%Y%m"),
                    placeholder="例如：202608",
                    label_visibility="collapsed",
                    key="finance_newebpay_month",
                )
                st.caption("格式：YYYYMM；下載該月份完整收款與退款資料")
            else:
                period = ""
                d1, d2 = st.columns(2)
                with d1:
                    start_date_value = st.date_input(
                        "開始日期",
                        value=today_date.replace(day=1),
                        key="finance_newebpay_start_date",
                    )
                with d2:
                    end_date_value = st.date_input(
                        "結束日期",
                        value=today_date,
                        key="finance_newebpay_end_date",
                    )
        elif selected_function == "【富邦銀行】清潔用品採購":
            st.markdown('<div class="field-label">📆 採購月份</div>', unsafe_allow_html=True)
            period = st.text_input(
                "採購月份",
                value=today_date.strftime("%Y%m"),
                placeholder="例如：202608",
                label_visibility="collapsed",
                key="finance_supply_purchase_month",
            )
            st.caption("格式：YYYYMM；比對報表 B 欄的進貨日期月份")
        elif selected_function == "【財報】工具包押金｜統計月份彙整（U–X）":
            st.markdown('<div class="field-label">📆 統計月份</div>', unsafe_allow_html=True)
            period = st.text_input(
                "統計月份",
                value=today_date.strftime("%Y%m"),
                placeholder="例如：202608",
                label_visibility="collapsed",
                key="finance_deposit_aggregate_month",
            )
            st.caption("格式：YYYYMM；統計該月上課/退還/已退/不退，追加寫入 U–X（接在既有資料之後，不覆蓋）")
        elif selected_function == "【財報】工具包押金｜批次依備註打勾（J–M）":
            st.markdown('<div class="field-label">📝 備註範圍</div>', unsafe_allow_html=True)
            st.text_input(
                "備註範圍",
                value="",
                placeholder="例如：U22:U25，或只給列號 22-25（預設抓 U 欄）",
                label_visibility="collapsed",
                key="finance_deposit_note_range",
            )
            st.caption("同一欄位的儲存格範圍；只寫列號（例如 22-25）預設抓 U 欄。會先清空整張表的 J–M 欄，再依範圍內的備註文字重新打勾")
        elif selected_function == "【財報】工具包押金｜比對異常標記（N欄）":
            st.markdown('<div class="field-label">📆 執行期間</div>', unsafe_allow_html=True)
            st.info("只檢查今年、已上課（J已勾）的列，異常會標記在 N 欄", icon="🔍")
        elif selected_function == "【儲值金】複製期別檔案":
            st.markdown('<div class="field-label">📆 期別</div>', unsafe_allow_html=True)
            period = st.text_input(
                "期別",
                value=today_date.strftime("%Y%m"),
                placeholder="例如：202607",
                label_visibility="collapsed",
                key="finance_vip_copy_period",
            )
            st.caption("格式：YYYYMM；從上一期複製出當期的「{期別}儲值金彙整」")
        elif selected_function == "【儲值金】轉檔":
            st.markdown('<div class="field-label">📆 期別</div>', unsafe_allow_html=True)
            period = st.text_input(
                "期別",
                value=today_date.strftime("%Y%m"),
                placeholder="例如：202607",
                label_visibility="collapsed",
                key="finance_vip_convert_period",
            )
            st.caption("執行前請先把該期資料夾內的來源檔（.xlsx）都放好；程式會自動轉成 Google Sheet（含高雄/新竹彙整、台南合併進高雄儲值金預收），不含搬運、不含套公式")
        elif selected_function == "【儲值金】搬運":
            st.markdown('<div class="field-label">📆 期別</div>', unsafe_allow_html=True)
            period = st.text_input(
                "期別",
                value=today_date.strftime("%Y%m"),
                placeholder="例如：202607",
                label_visibility="collapsed",
                key="finance_vip_move_period",
            )
            st.caption("請先完成「轉檔」再執行搬運；會把已轉檔的 Google Sheet 內容搬到當期彙整檔對應頁籤（不含套公式）")
        elif selected_function == "【儲值金】套用公式":
            st.markdown('<div class="field-label">📆 期別</div>', unsafe_allow_html=True)
            period = st.text_input(
                "期別",
                value=today_date.strftime("%Y%m"),
                placeholder="例如：202607",
                label_visibility="collapsed",
                key="finance_vip_formulas_period",
            )
            st.caption("只套用「儲值金公式設定」裡啟用的公式，不會重跑轉檔／搬運；可以重複執行來除錯")
        else:
            st.markdown('<div class="field-label">📆 日期區間</div>', unsafe_allow_html=True)
            d1, d2 = st.columns(2)
            with d1:
                start_date_value = st.date_input(
                    "開始日期", value=today_date.replace(day=1), key="finance_start_date"
                )
            with d2:
                end_date_value = st.date_input(
                    "結束日期", value=today_date, key="finance_end_date"
                )

    elif system_type in ["vip", "monthly_scheduler"]:
        st.markdown('<div class="field-label">📆 執行期別</div>', unsafe_allow_html=True)
        period = st.text_input(
            "執行期別",
            value=tw_now_text("%Y%m"),
            placeholder="例如：202605",
            label_visibility="collapsed",
            key="period",
        )

    # ── ★ 客服排程系統 ──────────────────────────────────────
    elif system_type == "service_schedule":
        _today_date = datetime.now(TW_TZ).date()

        if selected_function in ("【儲值】匯出VIP日曆對帳", "【儲值】全跑（抓儲值金＋匯出VIP）"):
            st.markdown('<div class="field-label">📆 匯出設定</div>', unsafe_allow_html=True)
            _crm_mode = st.radio(
                "匯出方式", ["期別（月份）", "日期區間"],
                horizontal=True, label_visibility="collapsed", key="crm_date_mode",
            )
            import calendar as _calendar
            if _crm_mode == "期別（月份）":
                _default_month = datetime.now(TW_TZ).strftime("%Y%m")
                _month_input = st.text_input(
                    "月份期別（多個用逗號分隔）", value=_default_month,
                    placeholder="例如：202606 或 202606,202607",
                    label_visibility="collapsed", key="crm_month_period",
                )
                _periods = [p.strip() for p in _month_input.split(",") if p.strip()]
                _starts, _ends, _parse_ok = [], [], True
                for _p in _periods:
                    try:
                        _py, _pm = int(_p[:4]), int(_p[4:6])
                        _last = _calendar.monthrange(_py, _pm)[1]
                        _starts.append(datetime(_py, _pm, 1, tzinfo=TW_TZ).date())
                        _ends.append(datetime(_py, _pm, _last, tzinfo=TW_TZ).date())
                    except Exception:
                        _parse_ok = False; break
                if _parse_ok and _starts:
                    start_date_value = min(_starts)
                    end_date_value   = max(_ends)
                    _label = f"匯出範圍：{start_date_value} ～ {end_date_value}"
                    if len(_periods) > 1:
                        _label += f"（共 {len(_periods)} 個期別）"
                    st.caption(_label)
                else:
                    st.error("期別格式錯誤，請輸入 yyyyMM，多個用逗號分隔")
                    start_date_value = end_date_value = None
            else:
                _d1, _d2 = st.columns(2)
                with _d1:
                    start_date_value = st.date_input("起始日期", value=_today_date.replace(day=1), key="crm_start_date")
                with _d2:
                    end_date_value = st.date_input("結束日期", value=_today_date, key="crm_end_date")
                st.caption(f"匯出範圍：{start_date_value} ～ {end_date_value}")

        elif selected_function in ("【儲值】抓儲值金", "【CRM】更新排程決策報表",
                                   "【CRM】更新三個月未排名單", "【CRM】重新排序Raw"):
            st.markdown('<div class="field-label">📆 執行日期</div>', unsafe_allow_html=True)
            st.info("自動使用今日（台北時間）", icon="📅")
            start_date_value = end_date_value = None

        else:
            # 排班管理三步驟：選執行日期
            st.markdown('<div class="field-label">📆 執行日期</div>', unsafe_allow_html=True)
            start_date_value = st.date_input(
                "執行日期", value=_today_date, key="service_run_date",
                label_visibility="collapsed"
            )
            st.caption(f"系統將處理 {start_date_value} 的排班及前一天的營業額")
            end_date_value = None

    else:
        st.markdown('<div class="field-label">📆 執行日期區間</div>', unsafe_allow_html=True)
        d1, d2 = st.columns(2)
        today_date = datetime.now(TW_TZ).date()
        with d1:
            start_date_value = st.date_input("開始日期", value=today_date, key="start_date")
        with d2:
            end_date_value = st.date_input("結束日期", value=today_date, key="end_date")
        period = start_date_value.strftime("%Y%m%d")

with area_col:
    st.markdown('<div class="field-label">📍 執行區域</div>', unsafe_allow_html=True)

    area_options = available_areas_for_system(selected_system)

    if system_type == "monthly_scheduler" and (not area_options or area_options == ["全區"]):
        area_options = ["台北", "新北", "台中", "桃園", "新竹", "高雄"]

    if not area_options:
        area_options = ["全區"]

    area_select_options = ["全區"] + [area for area in area_options if area != "全區"]
    if selected_function in (
        "【檸檬後台】異動儲值金",
        "【藍新金流】藍新登入",
        "【藍新金流】藍新信用卡待退款",
        "【鯨躍發票】開立折讓單",
        "【富邦銀行】富邦登入",
        "【富邦銀行】富邦明細下載",
        "【富邦銀行】異動 ATM 退款",
        "【富邦銀行】請款記錄",
        "【富邦銀行】工具包押金退款",
        "【富邦銀行】清潔用品採購",
        "【元大銀行】元大登入",
        "【元大銀行】元大明細下載",
        "【元大銀行】檢查薪資付款狀態",
        "【財報】工具包押金｜統計月份彙整（U–X）",
        "【財報】工具包押金｜批次依備註打勾（J–M）",
        "【財報】工具包押金｜比對異常標記（N欄）",
    ):
        area_select_options = [area for area in area_options if area != "全區"]
    if selected_function == "【檸檬後台】異動儲值金":
        area_select_options = [area for area in ("台北", "台中", "桃園", "新竹", "高雄") if area in area_options]
    if selected_function in (
        "【富邦銀行】異動 ATM 退款",
        "【富邦銀行】請款記錄",
        "【富邦銀行】工具包押金退款",
        "【富邦銀行】清潔用品採購",
        "【財報】工具包押金｜統計月份彙整（U–X）",
        "【財報】工具包押金｜批次依備註打勾（J–M）",
        "【財報】工具包押金｜比對異常標記（N欄）",
    ):
        area_select_options = [area for area in ("台北", "台中") if area in area_options]

    selected_area_value = st.selectbox(
        "執行區域",
        area_select_options,
        index=0,
        label_visibility="collapsed",
        key=f"area_select_{system_type}_{system_name}_{selected_function}",
    )

    selected_areas = [selected_area_value]

    if selected_area_value == "全區":
        st.caption("將執行全部已啟用地區")
    else:
        st.caption(f"只執行：{selected_area_value}")

refund_selected_rows = []
if system_type == "finance_management" and selected_function == "【藍新金流】藍新信用卡待退款":
    try:
        from tools.memo_system.change_order import get_worksheet
        from tools.newebpay.refund_filter import pending_credit_card_refunds

        _refund_values = get_worksheet(selected_area_value).get_all_values()
        _refund_candidates = pending_credit_card_refunds(_refund_values)
        _refund_editor = st.data_editor(
            pd.DataFrame([
                {
                    "執行": False,
                    "列數": item["sheet_row"],
                    "訂單編號": item["order_no"],
                    "退款金額": item["amount"],
                }
                for item in _refund_candidates
            ]),
            hide_index=True,
            use_container_width=True,
            disabled=["列數", "訂單編號", "退款金額"],
            column_config={"執行": st.column_config.CheckboxColumn("執行")},
            key=f"newebpay_refund_editor_{selected_area_value}",
        )
        refund_selected_rows = [
            int(row["列數"])
            for _, row in _refund_editor.iterrows()
            if bool(row["執行"])
        ]
        st.caption(f"待退款＋信用卡：{len(_refund_candidates)} 筆；已勾選：{len(refund_selected_rows)} 筆")
    except Exception as exc:
        st.error(f"讀取清潔異動表失敗：{exc}")

stored_value_selected_rows = []
if system_type == "finance_management" and selected_function == "【檸檬後台】異動儲值金":
    try:
        from tools.lemon_backend.stored_value_sheet import get_worksheet
        from tools.lemon_backend.stored_value_filter import pending_stored_value_adjustments

        _stored_value_candidates = pending_stored_value_adjustments(get_worksheet(selected_area_value).get_all_values())
        _stored_value_editor = st.data_editor(
            pd.DataFrame([
                {
                    "執行": False,
                    "列數": item["sheet_row"],
                    "狀態": item["status"],
                    "訂單編號": item["order_no"],
                    "異動類型": item["action"],
                    "異動金額": item["amount"],
                    "備註": item["note"],
                }
                for item in _stored_value_candidates
            ]),
            hide_index=True,
            use_container_width=True,
            disabled=["列數", "狀態", "訂單編號", "異動類型", "異動金額", "備註"],
            column_config={"執行": st.column_config.CheckboxColumn("執行")},
            key=f"lemon_stored_value_editor_{selected_area_value}",
        )
        stored_value_selected_rows = [
            int(row["列數"])
            for _, row in _stored_value_editor.iterrows()
            if bool(row["執行"])
        ]
        st.caption(f"待扣／待返儲值金：{len(_stored_value_candidates)} 筆；已勾選：{len(stored_value_selected_rows)} 筆")
    except Exception as exc:
        st.error(f"讀取儲值金異動資料失敗：{exc}")

allowance_selected_rows = []
if system_type == "finance_management" and selected_function == "【鯨躍發票】開立折讓單":
    try:
        from tools.memo_system.change_order import get_worksheet
        from tools.invoice_center.allowance_filter import pending_allowances
        _allowance_candidates = pending_allowances(get_worksheet(selected_area_value).get_all_values())
        _allowance_editor = st.data_editor(pd.DataFrame([{"執行": False, "列數": item["sheet_row"], "訂單編號": item["order_no"], "發票號碼": item["invoice_no"], "折讓未稅價": item["untaxed_amount"]} for item in _allowance_candidates]), hide_index=True, use_container_width=True, disabled=["列數", "訂單編號", "發票號碼", "折讓未稅價"], column_config={"執行": st.column_config.CheckboxColumn("執行")}, key=f"cetustek_allowance_editor_{selected_area_value}")
        allowance_selected_rows = [int(row["列數"]) for _, row in _allowance_editor.iterrows() if bool(row["執行"])]
        st.caption(f"待退款折讓：{len(_allowance_candidates)} 筆；已勾選：{len(allowance_selected_rows)} 筆")
    except Exception as exc:
        st.error(f"讀取折讓資料失敗：{exc}")

fubon_refund_selected_rows = []
if system_type == "finance_management" and selected_function == "【富邦銀行】異動 ATM 退款":
    try:
        from tools.bank_statement.fubon_refund_filter import pending_atm_refunds
        from tools.memo_system.change_order import get_worksheet

        _fubon_refund_candidates = pending_atm_refunds(
            get_worksheet(selected_area_value).get_all_values()
        )
        _fubon_refund_editor = st.data_editor(
            pd.DataFrame([
                {
                    "執行": False,
                    "列數": item["sheet_row"],
                    "訂單編號": item["order_no"],
                    "客人姓名": item["customer"],
                    "轉入銀行": item["bank_code"],
                    "轉入帳號": item["account_number"],
                    "退款金額": item["amount"],
                }
                for item in _fubon_refund_candidates
            ]),
            hide_index=True,
            use_container_width=True,
            disabled=["列數", "訂單編號", "客人姓名", "轉入銀行", "轉入帳號", "退款金額"],
            column_config={"執行": st.column_config.CheckboxColumn("執行")},
            key=f"fubon_atm_refund_editor_{selected_area_value}",
        )
        fubon_refund_selected_rows = [
            int(row["列數"])
            for _, row in _fubon_refund_editor.iterrows()
            if bool(row["執行"])
        ]
        st.caption(
            f"待退款＋ATM：{len(_fubon_refund_candidates)} 筆；"
            f"已勾選：{len(fubon_refund_selected_rows)} 筆"
        )
    except Exception as exc:
        st.error(f"讀取富邦 ATM 退款資料失敗：{exc}")

fubon_payment_request_selected_rows = []
if system_type == "finance_management" and selected_function == "【富邦銀行】請款記錄":
    try:
        from tools.bank_statement.fubon_payment_request_filter import pending_payment_requests
        from tools.bank_statement.internal_payment_registry import (
            PAYMENT_REQUEST_TYPE,
            read_report_values,
        )

        _payment_request_candidates = pending_payment_requests(
            read_report_values(PAYMENT_REQUEST_TYPE, selected_area_value)
        )
        _payment_request_editor = st.data_editor(
            pd.DataFrame([
                {
                    "執行": False,
                    "列數": item["sheet_row"],
                    "請款人": item["requester"],
                    "收款對象（轉入帳號）": item["name"],
                    "請款金額": item["amount"],
                }
                for item in _payment_request_candidates
            ]),
            hide_index=True,
            use_container_width=True,
            disabled=["列數", "請款人", "收款對象（轉入帳號）", "請款金額"],
            column_config={"執行": st.column_config.CheckboxColumn("執行")},
            key=f"fubon_payment_request_editor_{selected_area_value}",
        )
        fubon_payment_request_selected_rows = [
            int(row["列數"])
            for _, row in _payment_request_editor.iterrows()
            if bool(row["執行"])
        ]
        st.caption(
            f"待付款請款記錄：{len(_payment_request_candidates)} 筆；"
            f"已勾選：{len(fubon_payment_request_selected_rows)} 筆"
        )
    except Exception as exc:
        st.error(f"讀取請款記錄資料失敗：{exc}")

fubon_deposit_refund_selected_rows = []
if system_type == "finance_management" and selected_function == "【富邦銀行】工具包押金退款":
    try:
        from tools.bank_statement.fubon_deposit_refund_filter import pending_deposit_refunds
        from tools.bank_statement.internal_payment_registry import (
            DEPOSIT_REFUND_TYPE,
            read_report_values,
        )

        _deposit_refund_candidates = pending_deposit_refunds(
            read_report_values(DEPOSIT_REFUND_TYPE, selected_area_value)
        )
        _deposit_refund_editor = st.data_editor(
            pd.DataFrame([
                {
                    "執行": False,
                    "列數": item["sheet_row"],
                    "備註": item["memo"],
                    "退款金額": item["amount"],
                }
                for item in _deposit_refund_candidates
            ]),
            hide_index=True,
            use_container_width=True,
            disabled=["列數", "備註", "退款金額"],
            column_config={"執行": st.column_config.CheckboxColumn("執行")},
            key=f"fubon_deposit_refund_editor_{selected_area_value}",
        )
        fubon_deposit_refund_selected_rows = [
            int(row["列數"])
            for _, row in _deposit_refund_editor.iterrows()
            if bool(row["執行"])
        ]
        st.caption(
            f"待退款工具包押金：{len(_deposit_refund_candidates)} 筆；"
            f"已勾選：{len(fubon_deposit_refund_selected_rows)} 筆"
        )
    except Exception as exc:
        st.error(f"讀取工具包押金退款資料失敗：{exc}")

fubon_supply_purchase_selected_rows = []
if system_type == "finance_management" and selected_function == "【富邦銀行】清潔用品採購":
    try:
        from tools.bank_statement.fubon_supply_purchase_filter import pending_supply_purchases
        from tools.bank_statement.internal_payment_registry import (
            SUPPLY_PURCHASE_TYPE,
            read_report_values,
        )

        _supply_purchase_candidates = pending_supply_purchases(
            read_report_values(SUPPLY_PURCHASE_TYPE, selected_area_value), period
        )
        _supply_purchase_editor = st.data_editor(
            pd.DataFrame([
                {
                    "執行": False,
                    "列數": item["sheet_row"],
                    "涵蓋列": "、".join(str(r) for r in item["rows"]),
                    "廠商": item["supplier"],
                    "銀行代碼": item["bank_code"],
                    "帳號": item["account_number"],
                    "合計金額": item["amount"],
                }
                for item in _supply_purchase_candidates
            ]),
            hide_index=True,
            use_container_width=True,
            disabled=["列數", "涵蓋列", "廠商", "銀行代碼", "帳號", "合計金額"],
            column_config={"執行": st.column_config.CheckboxColumn("執行")},
            key=f"fubon_supply_purchase_editor_{selected_area_value}_{period}",
        )
        fubon_supply_purchase_selected_rows = [
            int(row["列數"])
            for _, row in _supply_purchase_editor.iterrows()
            if bool(row["執行"])
        ]
        st.caption(
            f"待匯款採購（依銀行帳號合併）：{len(_supply_purchase_candidates)} 筆；"
            f"已勾選：{len(fubon_supply_purchase_selected_rows)} 筆"
        )
    except Exception as exc:
        st.error(f"讀取清潔用品採購資料失敗：{exc}")

run_clicked = st.button("▶ 執行", use_container_width=True)

st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 日誌
# ═══════════════════════════════════════════════════════════
render_log()

if system_type == "finance_management" and selected_function.startswith(("【檸檬後台】", "【鯨躍發票】", "【藍新金流】", "【富邦銀行】", "【元大銀行】")):
    try:
        agent_tasks = [
            task
            for task in list_local_agent_tasks(limit=10)
            if task.get("action", "").startswith(("lemon.", "cetustek.", "newebpay.", "fubon.", "yuanta."))
        ]
        if agent_tasks:
            st.markdown("#### 本機 Agent 任務 Log")
            st.dataframe(
                [
                    {
                        "建立時間": task.get("created_at", ""),
                        "任務": task.get("action", ""),
                        "區域/參數": task.get("params_json", ""),
                        "狀態": task.get("status", ""),
                        "Agent": task.get("agent_id", ""),
                        "訊息": task.get("message", ""),
                    }
                    for task in agent_tasks
                ],
                use_container_width=True,
                hide_index=True,
            )
            with st.expander("最新完整 Log", expanded=agent_tasks[0].get("status") == "failed"):
                full_log = read_local_agent_task_log(agent_tasks[0].get("task_id", ""))
                st.code(full_log or agent_tasks[0].get("log") or "等待本機 Agent", language="text")
    except Exception as exc:
        st.warning(f"本機 Agent 任務 Log 讀取失敗：{exc}")

    if selected_function in ("【富邦銀行】富邦登入", "【富邦銀行】富邦明細下載"):
        render_fubon_mobile_verification()

clear_col, _ = st.columns([1, 3])

with clear_col:
    if st.button("🗑️ 清除日誌"):
        st.session_state.logs = ["[--:--:--] 日誌已清除"]
        st.rerun()


# ═══════════════════════════════════════════════════════════
# UI — 設定檔管理
# ═══════════════════════════════════════════════════════════
if can_access_page("settings"):
    with st.expander("🗃️ 設定檔管理（新增 / 編輯 / 刪除）", expanded=False):
        st.markdown(
            """
    <div class="setting-note">
    可在這裡新增 / 編輯不同系統設定。<br>
    舊系統使用主控表 ID / 共用雲端資料夾 ID。<br>
    外場排程系統的 folder_ids / spreadsheet_ids 建議直接維護在 GitHub repo 的 <code>config/systems.yaml</code>。Streamlit Cloud 畫面上修改只會存在目前執行環境，重新部署或重啟後可能消失。
    </div>
    """,
            unsafe_allow_html=True,
        )

        add_col, _ = st.columns([1, 3])
        with add_col:
            if st.button("➕ 新增系統", use_container_width=True):
                st.session_state.adding_system = True
                st.session_state.editing_system = None
                st.rerun()

        system_type_options = [
            "vip", "daily_scheduler", "monthly_scheduler",
            "field_daily_schedule", "service_schedule", "gmail_401",
            "finance_management",
        ]

        if st.session_state.adding_system:
            with st.form("add_system_form"):
                st.markdown("**新增系統設定**")

                new_name = st.text_input("設定系統名稱", placeholder="例如：客服排程系統")
                new_type = st.selectbox(
                    "設定系統類型",
                    system_type_options,
                    format_func=get_system_type_label,
                )
                new_master_id = st.text_input("設定主控表 ID")
                if new_type == "monthly_scheduler":
                    new_folder_id = st.text_input("月排程總根目錄 ID")
                    st.caption("月排程地區設定：可直接新增列、編輯地區、填寫地區根目錄 ID、勾選啟用；刪除地區＝刪除該列。")
                    new_monthly_areas_df = st.data_editor(
                        default_monthly_areas_df(),
                        num_rows="dynamic",
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "地區": st.column_config.TextColumn("地區", required=True),
                            "地區根目錄ID": st.column_config.TextColumn("地區根目錄 ID"),
                            "啟用": st.column_config.CheckboxColumn("啟用"),
                        },
                        key="new_monthly_areas_editor",
                    )
                else:
                    new_folder_id = st.text_input("設定共用雲端資料夾 ID / 根目錄 ID")
                    new_monthly_areas_df = None
                new_enabled = st.checkbox("啟用", value=True)

                f1, f2 = st.columns(2)
                with f1:
                    submit_add = st.form_submit_button("💾 儲存", use_container_width=True)
                with f2:
                    cancel_add = st.form_submit_button("✕ 取消", use_container_width=True)

                if submit_add:
                    systems_all = config.get("systems", [])

                    if not new_name:
                        st.error("請輸入系統名稱")
                    elif any(s.get("name") == new_name for s in systems_all):
                        st.error(f"系統「{new_name}」已存在")
                    else:
                        new_system = {
                            "name": new_name,
                            "type": new_type,
                            "master_spreadsheet_id": new_master_id,
                            "folder_id": new_folder_id,
                            "enabled": new_enabled,
                        }

                        if new_type == "monthly_scheduler":
                            new_system["areas"] = monthly_areas_df_to_config(new_monthly_areas_df)

                        if new_type == "field_daily_schedule":
                            new_system.update(
                                {
                                    "folder_ids": {
                                        "schedule_stats": "",
                                        "order": {"台北": "", "台中": ""},
                                        "staff_profile": {"台北": "", "台中": ""},
                                        "staff_schedule": {"台北": "", "台中": ""},
                                    },
                                    "spreadsheet_ids": {
                                        "roster": {"台北": "", "台中": ""},
                                        "salary": {"台北": "", "台中": ""},
                                        "office": {"台北": "", "台中": ""},
                                    },
                                }
                            )

                        # ★ 客服排程系統預設結構
                        if new_type == "service_schedule":
                            new_system["target_file_id"] = ""
                            new_system["folder_ids"] = {
                                "schedule_stats": "1V0IjoJqHlnkGb3Oq70Cil63pQ9j8r2Xv",
                            }

                        systems_all.append(new_system)
                        config["systems"] = systems_all
                        save_config(config)
                        add_log(f"新增系統設定：{new_name}", "success")
                        st.session_state.adding_system = False
                        st.rerun()

                if cancel_add:
                    st.session_state.adding_system = False
                    st.rerun()

        for i, sys_cfg in enumerate(config.get("systems", [])):
            name = sys_cfg.get("name", f"系統{i + 1}")
            current_type = sys_cfg.get("type", "vip")
            master_id = sys_cfg.get("master_spreadsheet_id", "")
            folder_id = sys_cfg.get("folder_id", "")
            enabled = sys_cfg.get("enabled", True)

            needs_ids = current_type == "vip"
            complete = bool(name and (not needs_ids or (master_id and folder_id)))
            badge = (
                '<span class="badge-ok">已設定</span>'
                if complete
                else '<span class="badge-err">未完整</span>'
            )

            if st.session_state.editing_system == name:
                with st.form(f"edit_system_{i}"):
                    st.markdown(f"**編輯系統：{name}**")

                    e_name = st.text_input("設定系統名稱", value=name)
                    e_type = st.selectbox(
                        "設定系統類型",
                        system_type_options,
                        index=system_type_options.index(current_type) if current_type in system_type_options else 0,
                        format_func=get_system_type_label,
                    )
                    e_master_id = st.text_input("設定主控表 ID", value=master_id)
                    if e_type == "monthly_scheduler":
                        e_folder_id = st.text_input("月排程總根目錄 ID", value=folder_id)
                        st.caption("月排程地區設定：可直接新增列、編輯地區、填寫地區根目錄 ID、勾選啟用；刪除地區＝刪除該列。")
                        e_monthly_areas_df = st.data_editor(
                            monthly_areas_to_df(sys_cfg),
                            num_rows="dynamic",
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "地區": st.column_config.TextColumn("地區", required=True),
                                "地區根目錄ID": st.column_config.TextColumn("地區根目錄 ID"),
                                "啟用": st.column_config.CheckboxColumn("啟用"),
                            },
                            key=f"edit_monthly_areas_editor_{i}",
                        )
                    else:
                        e_folder_id = st.text_input("設定共用雲端資料夾 ID / 根目錄 ID", value=folder_id)
                        e_monthly_areas_df = None
                    e_enabled = st.checkbox("啟用", value=enabled)

                    e1, e2 = st.columns(2)
                    with e1:
                        save_edit = st.form_submit_button("💾 儲存", use_container_width=True)
                    with e2:
                        cancel_edit = st.form_submit_button("✕ 取消", use_container_width=True)

                    if save_edit:
                        if not e_name:
                            st.error("請輸入系統名稱")
                        else:
                            updated = dict(config["systems"][i])
                            updated.update(
                                {
                                    "name": e_name,
                                    "type": e_type,
                                    "master_spreadsheet_id": e_master_id,
                                    "folder_id": e_folder_id,
                                    "enabled": e_enabled,
                                }
                            )

                            if e_type == "monthly_scheduler":
                                updated["areas"] = monthly_areas_df_to_config(e_monthly_areas_df)

                            if e_type == "field_daily_schedule":
                                updated.setdefault("folder_ids", {})
                                updated.setdefault("spreadsheet_ids", {})

                            config["systems"][i] = updated
                            save_config(config)
                            add_log(f"更新系統設定：{e_name}", "success")
                            st.session_state.editing_system = None
                            st.rerun()

                    if cancel_edit:
                        st.session_state.editing_system = None
                        st.rerun()

            else:
                enabled_text = "✅ 啟用" if enabled else "⚠️ 停用"
                extra_detail = ""

                if current_type == "field_daily_schedule":
                    folder_ids = mask_nested_ids(sys_cfg.get("folder_ids", {}))
                    extra_detail = f"""
      <div class="detail-row"><strong>排班統計資料夾</strong>：{html.escape(str(folder_ids.get("schedule_stats", "")))}</div>
      <div class="detail-row"><strong>訂單/個資/班表資料夾</strong>：台北 / 台中 已設定</div>
    """
                elif current_type == "monthly_scheduler":
                    monthly_areas = normalize_monthly_areas(sys_cfg)
                    enabled_area_names = [k for k, v in monthly_areas.items() if v.get("enabled", True)]
                    disabled_area_names = [k for k, v in monthly_areas.items() if not v.get("enabled", True)]
                    area_root_lines = [
                        f"{k}：{mask_id(v.get('folder_id', ''))}（{'啟用' if v.get('enabled', True) else '停用'}）"
                        for k, v in monthly_areas.items()
                    ]
                    extra_detail = f"""
      <div class="detail-row"><strong>月排程總根目錄 ID</strong>：{mask_id(folder_id)}</div>
      <div class="detail-row"><strong>啟用地區</strong>：{html.escape("、".join(enabled_area_names) or "未設定")}</div>
      <div class="detail-row"><strong>地區根目錄</strong>：<br>{"<br>".join(html.escape(l) for l in area_root_lines) or "未設定"}</div>
    """
                # ★ 客服排程系統顯示資訊
                elif current_type == "service_schedule":
                    target_id = sys_cfg.get("target_file_id", "")
                    sched_folder = (sys_cfg.get("folder_ids") or {}).get("schedule_stats", "")
                    extra_detail = f"""
      <div class="detail-row"><strong>目標試算表 ID</strong>：{mask_id(target_id) if target_id else "由 Secret LEMON_TARGET_FILE_ID 注入"}</div>
      <div class="detail-row"><strong>排班統計來源資料夾</strong>：{mask_id(sched_folder)}</div>
      <div class="detail-row"><strong>打卡目標</strong>：主控表（執行記錄）+ 執行檔（_py_execution_log）</div>
    """
                else:
                    extra_detail = f"""
      <div class="detail-row"><strong>主控表 ID</strong>：{mask_id(master_id)}</div>
      <div class="detail-row"><strong>共用雲端資料夾 ID</strong>：{mask_id(folder_id)}</div>
    """

                st.markdown(
                    f"""
    <div class="system-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <strong style="color:#0a4b6e;">🗂️ {html.escape(name)}</strong>{badge}
      </div>
      <div class="detail-row"><strong>系統類型</strong>：{get_system_type_label(current_type)}</div>
      <div class="detail-row"><strong>狀態</strong>：{enabled_text}</div>
      {extra_detail}
    </div>
    """,
                    unsafe_allow_html=True,
                )

                _, b2, b3 = st.columns([2, 1, 1])
                with b2:
                    if st.button("📝 編輯", key=f"edit_system_{i}", use_container_width=True):
                        st.session_state.editing_system = name
                        st.session_state.adding_system = False
                        st.rerun()

                with b3:
                    if st.button("🗑️ 刪除", key=f"delete_system_{i}", use_container_width=True):
                        config["systems"].pop(i)
                        save_config(config)
                        add_log(f"刪除系統設定：{name}", "warning")
                        st.rerun()

# ═══════════════════════════════════════════════════════════
# 執行邏輯
# ═══════════════════════════════════════════════════════════
if run_clicked:
    if not selected_system:
        add_log("請先新增或啟用系統設定", "error")
        st.rerun()

    system_type = selected_system.get("type", "vip")

    if system_type == "finance_management":
        add_log(f"開始執行：{system_name} / {selected_function}")

        finance_task = next(
            (task for task in FINANCE_TASKS if task["name"] == selected_function),
            None,
        )
        finance_handler = finance_task.get("handler") if finance_task else None

        if not finance_task or not finance_task.get("enabled") or not callable(finance_handler):
            add_log("此功能尚未接入本機 Agent", "warning")
        else:
            finance_kwargs = {
                "month": period,
                "start_date": start_date_value,
                "end_date": end_date_value,
                "area": selected_area_value,
            }
            if selected_function == "【藍新金流】藍新信用卡待退款":
                finance_kwargs["selected_rows"] = refund_selected_rows
            if selected_function == "【檸檬後台】異動儲值金":
                finance_kwargs["selected_rows"] = stored_value_selected_rows
            if selected_function == "【鯨躍發票】開立折讓單":
                finance_kwargs["selected_rows"] = allowance_selected_rows
            if selected_function == "【富邦銀行】異動 ATM 退款":
                finance_kwargs["selected_rows"] = fubon_refund_selected_rows
            if selected_function == "【富邦銀行】請款記錄":
                finance_kwargs["selected_rows"] = fubon_payment_request_selected_rows
            if selected_function == "【富邦銀行】工具包押金退款":
                finance_kwargs["selected_rows"] = fubon_deposit_refund_selected_rows
            if selected_function == "【富邦銀行】清潔用品採購":
                finance_kwargs["selected_rows"] = fubon_supply_purchase_selected_rows
            with st.spinner(f"⏳ 執行中：{selected_function}，請稍候..."):
                try:
                    result = finance_handler(**finance_kwargs)
                    if result is not None:
                        add_log(str(result), "success")
                except Exception as e:
                    add_log(f"執行失敗：{e}", "error")
                    add_log(traceback.format_exc(), "error")
        st.rerun()

    master_id = selected_system.get("master_spreadsheet_id", "")
    folder_id = selected_system.get("folder_id", "")

    if system_type in ["vip", "monthly_scheduler"] and not period:
        if not (system_type == "monthly_scheduler" and selected_function in ["上半月訂單", "下半月訂單"] and monthly_date_mode == "日期區間"):
            add_log("請先輸入執行期別", "error")
            st.rerun()

    if system_type in ["daily_scheduler", "field_daily_schedule"]:
        date_keys = parse_date_range(start_date_value, end_date_value)
        if not date_keys:
            add_log("請選擇執行日期區間", "error")
            st.rerun()
    elif system_type == "monthly_scheduler" and selected_function in ["上半月訂單", "下半月訂單"] and monthly_date_mode == "日期區間":
        date_keys = [
            start_date_value.strftime("%Y%m%d"),
            end_date_value.strftime("%Y%m%d"),
        ]
    else:
        date_keys = [period or tw_now_text("%Y%m%d")]

    if system_type == "field_daily_schedule" and not selected_areas:
        add_log("請至少選擇一個執行區域", "error")
        st.rerun()

    add_log(f"開始執行：{system_name} / {selected_function}")

    with st.spinner(f"⏳ 執行中：{selected_function}，請稍候..."):
        try:
            result = None

            if system_type == "vip":
                if not master_id:
                    add_log("尚未設定主控表 ID", "error")
                    st.rerun()

                if not folder_id:
                    add_log("尚未設定共用雲端資料夾 ID", "error")
                    st.rerun()

                workflow = build_vip_workflow(
                    master_id=master_id,
                    folder_id=folder_id,
                    system_name=system_name,
                )

                if selected_function == "建立當月彙整檔":
                    result = workflow.create_monthly_summary(period)
                elif selected_function == "轉檔＋高雄/新竹彙整":
                    result = workflow.convert_files(period)
                elif selected_function == "搬運":
                    result = workflow.move_files(period)
                elif selected_function == "計算":
                    result = workflow.apply_formulas(period)
                elif selected_function == "彙整金額":
                    result = workflow.summarize_amounts(period)
                elif selected_function == "一鍵執行：建立＋轉檔＋搬運＋計算＋彙整金額":
                    result = [
                        workflow.create_monthly_summary(period),
                        workflow.convert_files(period),
                        workflow.move_files(period),
                        workflow.apply_formulas(period),
                        workflow.summarize_amounts(period),
                    ]
                else:
                    add_log(f"未知功能：{selected_function}", "warning")

            elif system_type == "daily_scheduler":
                if not folder_id:
                    add_log("尚未設定共用雲端資料夾 ID", "error")
                    st.rerun()

                if selected_function == "一鍵執行日排程":
                    from tools.scheduled_daily.scheduler import main as run_daily_scheduler

                    daily_target = DAILY_TARGET_MAP.get(selected_function, "all")
                    result = run_daily_scheduler(target=daily_target, folder_id=folder_id)

                else:
                    script = DAILY_SCRIPT_MAP.get(selected_function)

                    if not script:
                        raise RuntimeError(f"找不到日排程功能：{selected_function}")

                    if selected_function == "業績報表":
                        result = run_script(script)
                        add_log("業績報表已更新，可點「📊 查看業績報表」開啟。", "success")
                    else:
                        result = run_script(script, ["--folder-id", folder_id])

            elif system_type == "monthly_scheduler":
                if not folder_id:
                    add_log("尚未設定月排程根目錄 ID", "error")
                    st.rerun()

                if not selected_areas:
                    add_log("請至少選擇一個執行區域", "error")
                    st.rerun()

                if selected_function == "一鍵執行月排程":
                    from tools.scheduled_monthly.scheduler import main as run_monthly_scheduler

                    try:
                        result = run_monthly_scheduler(folder_id=folder_id)
                    except TypeError:
                        result = run_monthly_scheduler()

                else:
                    cmd = MONTHLY_SCRIPT_MAP.get(selected_function)

                    if not cmd:
                        raise RuntimeError(f"找不到月排程功能：{selected_function}")

                    script = cmd[0]
                    base_args = list(cmd[1:])
                    results = []

                    if selected_function in ["上半月訂單", "下半月訂單"]:
                        for area_name in selected_areas:
                            args = []

                            if monthly_date_mode == "日期區間":
                                if not start_date_value or not end_date_value:
                                    raise RuntimeError("請選擇開始日期與結束日期")

                                args.extend([
                                    "--start", start_date_value.strftime("%Y-%m-%d"),
                                    "--end", end_date_value.strftime("%Y-%m-%d"),
                                ])
                            else:
                                args.extend(base_args)
                                if period:
                                    args.extend(["--period", period])

                            args.extend(["--folder-id", folder_id])

                            if area_name == "全區":
                                args.extend(["--area", "all"])
                            else:
                                args.extend(["--area", area_name])

                            add_log(f"月排程執行：{selected_function} / {period or '日期區間'} / {area_name}")
                            results.append(run_script(script, args))

                        result = results

                    else:
                        args = [*base_args, "--folder-id", folder_id]

                        # 0704_v3：
                        # 這裡不能再使用預設 all。
                        # 必須以畫面目前選到的 selected_area_value 為準。
                        selected_area_text = str(selected_area_value or "").strip()

                        if selected_area_text in ["", "全區", "全部", "ALL", "All", "all"]:
                            area_arg = "all"
                        else:
                            area_arg = selected_area_text

                        args.extend(["--area", area_arg])

                        if period:
                            args.extend(["--period", period])

                        add_log(f"月排程執行：{selected_function} / {period or '預設期別'} / 畫面選擇區域={selected_area_text}")
                        add_log(f"實際傳入區域 --area：{area_arg}")
                        add_log(f"實際執行參數：{' '.join(args)}")

                        result = run_script(script, args)

            elif system_type == "field_daily_schedule":
                cmd = FIELD_SCRIPT_MAP.get(selected_function)

                if not cmd:
                    raise RuntimeError(f"找不到外場日排程功能：{selected_function}")

                script = cmd[0]
                base_args = list(cmd[1:])
                results = []

                for date_key in date_keys:
                    for area_name in selected_areas:
                        args = list(base_args)
                        args.extend(["--date", date_key])
                        args.extend(["--system-name", system_name])

                        if area_name != "全區":
                            args.extend(["--area", area_name])

                        add_log(f"外場排程執行：{selected_function} / {date_key} / {area_name}")
                        results.append(run_script(script, args))

                result = results

            # ── ★ 客服排程系統執行邏輯 ────────────────────────────
            elif system_type == "service_schedule":

                # 把 st.secrets 裡的所有字串值注入到子進程環境
                # （Streamlit Cloud secrets 不在 os.environ，需手動注入）
                _svc_env = os.environ.copy()
                _base_str = str(BASE_DIR)
                _existing_pp = _svc_env.get("PYTHONPATH", "")
                if _base_str not in _existing_pp:
                    _svc_env["PYTHONPATH"] = _base_str + (":" + _existing_pp if _existing_pp else "")
                try:
                    for _k in st.secrets:
                        try:
                            _v = st.secrets[_k]
                            if isinstance(_v, str):
                                _svc_env[_k] = _v
                            elif isinstance(_v, dict):
                                # section → 整體 JSON 注入
                                _svc_env[_k] = json.dumps(dict(_v), ensure_ascii=False)
                                # section 內每個 key 也展開注入（如 GMAIL_USER 在 section 裡）
                                for _sk, _sv in _v.items():
                                    if isinstance(_sv, str):
                                        _svc_env[_sk] = _sv
                        except Exception:
                            pass
                except Exception:
                    pass

                # 確保目標試算表 ID 有注入（從設定管理讀取）
                if not _svc_env.get("SERVICE_TARGET_SPREADSHEET_ID"):
                    _target_id = get_system_by_name(config, system_name).get("folder_id", "").strip()
                    if _target_id:
                        _svc_env["SERVICE_TARGET_SPREADSHEET_ID"] = _target_id

                # ── CRM 功能 ──────────────────────────────────
                if selected_function in SERVICE_CRM_MAP:
                    step = SERVICE_CRM_MAP[selected_function]
                    cmd = [
                        sys.executable, "-u", "-m",
                        "tools.service_management.crm_export",
                        "--step", step,
                    ]
                    if step != "1" and start_date_value and end_date_value:
                        cmd += ["--start", start_date_value.strftime("%Y-%m-%d"),
                                "--end",   end_date_value.strftime("%Y-%m-%d")]
                    if selected_area_value and selected_area_value != "全區":
                        cmd += ["--area", selected_area_value]
                    add_log(f"CRM 執行：{selected_function}（step={step}）", "info")

                # ── 排班同步功能 ──────────────────────────────
                else:
                    step = SERVICE_STEP_MAP.get(selected_function, "0")
                    cmd = [
                        sys.executable, "-u", "-m",
                        "tools.service_management.service_schedule",
                        "--step", step,
                    ]
                    # 帶入使用者選擇的執行日期
                    if start_date_value:
                        cmd += ["--date", start_date_value.strftime("%Y-%m-%d")]
                    add_log(f"客服排程執行：{selected_function}（--step {step}）", "info")

                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=BASE_DIR,
                    env=_svc_env,
                )

                if completed.stdout:
                    for line in completed.stdout.strip().splitlines()[-120:]:
                        add_log(line, "info")

                if completed.stderr:
                    for line in completed.stderr.strip().splitlines()[-60:]:
                        line = line.strip()
                        if not line:
                            continue
                        if "[ERROR]" in line or "[CRITICAL]" in line:
                            add_log(line, "error")
                        elif "[WARNING]" in line:
                            add_log(line, "warning")
                        else:
                            add_log(line, "info")

                # 解析各 Step 結果，顯示部分成功
                stdout_text = completed.stdout or ""
                success_steps = [l for l in stdout_text.splitlines() if "完成" in l and "ERROR" not in l and "失敗" not in l]
                failed_steps  = [l for l in stdout_text.splitlines() if "失敗" in l and "[ERROR]" in l]

                if completed.returncode == 0:
                    result = f"✅ {selected_function} 全部成功"
                elif success_steps and failed_steps:
                    # 部分成功：不拋例外，顯示 warning
                    result = f"⚠️ 部分完成：{len(success_steps)} 個步驟成功，{len(failed_steps)} 個步驟失敗"
                    add_log(result, "warning")
                    for fl in failed_steps[-5:]:
                        add_log(fl.strip(), "error")
                else:
                    raise RuntimeError(
                        f"執行失敗（exit {completed.returncode}）：{selected_function}"
                    )
            # ────────────────────────────────────────────────────────

            else:
                add_log(f"{system_type} 尚未實作", "warning")

            if isinstance(result, list):
                for item in result:
                    add_log(str(item), "success")
            elif result is not None:
                add_log(str(result), "success")

            add_log("執行完成", "success")

        except Exception as e:
            add_log(f"執行失敗：{e}", "error")
            add_log(traceback.format_exc(), "error")

    st.rerun()
