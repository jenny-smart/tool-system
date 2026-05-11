"""
Tool App 主控入口
多系統工具平台：
- 儲值金管理
- 日排程系統
- 月排程系統
- 外場日排程系統
"""

from __future__ import annotations

import html
import json
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

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
CONFIG_PATH = Path("config/systems.yaml")

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
    ]
}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    st.cache_data.clear()


def ensure_config_file() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)


def merge_default_systems(data: dict) -> dict:
    """
    保留 GitHub / 現有 systems.yaml 內容，只補缺少的預設系統。
    這樣重新部署時不會把日排程、月排程或外場排程弄不見。
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


def load_config() -> dict:
    ensure_config_file()
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    data = merge_default_systems(data)
    return data


def get_secret_text(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return default


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
    st.session_state.selected_system_name = "儲值金管理"

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
    """月排程地區設定標準化。

    建議結構：
    areas:
      台北:
        folder_id: "地區根目錄ID"
        enabled: true

    也兼容舊結構：
    areas:
      台北:
        folder_name: "01.台北專員"
        enabled: true
    """
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
    """把月排程地區表格轉回 systems.yaml 結構。

    刪除地區：在表格刪除該列即可。
    停用地區：取消「啟用」勾選即可。
    """
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
        areas = normalize_monthly_areas(system)
        enabled_areas = [
            area_name
            for area_name, info in areas.items()
            if info.get("enabled", True)
        ]
        return enabled_areas or ["全區"]

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

    if len(st.session_state.logs) > 500:
        st.session_state.logs = st.session_state.logs[-500:]


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
    }
    return mapping.get(system_type, system_type or "未設定")


def build_vip_workflow(master_id: str, folder_id: str, system_name: str):
    from services.auth import get_gspread_client, get_drive_service
    from services.google_drive import DriveService
    from services.google_sheets import SheetsService
    from services.vip_workflow import VipStoredValueWorkflow

    sheets = SheetsService(get_gspread_client())
    drive = DriveService(get_drive_service())

    try:
        return VipStoredValueWorkflow(
            drive,
            sheets,
            master_id,
            folder_id,
            system_name,
        )
    except TypeError:
        return VipStoredValueWorkflow(
            drive,
            sheets,
            master_id,
            folder_id,
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

    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        cwd=BASE_DIR,
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

    st.markdown('<div class="card"><div class="card-title">📈 月度追蹤</div>', unsafe_allow_html=True)
    tabs = st.tabs(["當月每日業績", "次月每日業績", "月底快照"])

    with tabs[0]:
        render_html_table(daily_df)

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

        # 通知
        "send_daily_result": "通知信",
    }

    def today_key() -> str:
        return datetime.now(TW_TZ).strftime("%Y%m%d")

    def read_text_safe(path: Path) -> str:
        if not path.exists():
            return ""
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
            "Traceback",
            "RuntimeError",
            "ModuleNotFoundError",
            "Error",
            "ERROR",
            "Exception",
            "SMTPAuthenticationError",
            "HttpError",
            "failed",
            "Failed",
            "失敗",
            "錯誤",
            "找不到",
            "登入失敗",
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
          .log-title {
            font-size:2.2rem;
            font-weight:900;
            color:#0f172a;
            letter-spacing:1px;
            margin-bottom:4px;
          }
          .log-subtitle {
            font-size:0.82rem;
            font-weight:800;
            color:#94a3b8;
            letter-spacing:4px;
            margin-top:6px;
            margin-bottom:22px;
          }
          .log-top-card {
            background:linear-gradient(135deg,#ffffff,#f8fafc);
            border:1px solid #e2e8f0;
            border-radius:24px;
            padding:26px;
            margin:20px 0 26px 0;
            box-shadow:0 12px 30px rgba(15,23,42,0.07);
          }
          .log-overall {
            display:flex;
            align-items:flex-start;
            justify-content:space-between;
            gap:18px;
            flex-wrap:wrap;
          }
          .log-overall-title {
            font-size:1.55rem;
            font-weight:900;
            color:#0f172a;
          }
          .log-overall-note {
            color:#64748b;
            margin-top:8px;
            font-size:0.98rem;
          }
          .overall-status {
            font-size:1.55rem;
            font-weight:900;
          }
          .result-strip {
            display:flex;
            gap:12px;
            flex-wrap:wrap;
            margin-top:18px;
          }
          .result-pill {
            background:#f1f5f9;
            border:1px solid #e2e8f0;
            border-radius:999px;
            padding:9px 14px;
            color:#334155;
            font-weight:900;
            font-size:0.93rem;
          }
          .status-grid {
            display:grid;
            grid-template-columns:repeat(2,minmax(0,1fr));
            gap:16px;
            margin-top:16px;
          }
          .status-card {
            background:white;
            border:1px solid #e2e8f0;
            border-left:8px solid var(--status-color);
            border-radius:20px;
            padding:20px;
            box-shadow:0 5px 18px rgba(15,23,42,0.05);
          }
          .status-head {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:12px;
            margin-bottom:10px;
          }
          .status-name {
            font-size:1.18rem;
            font-weight:900;
            color:#0f172a;
          }
          .status-badge {
            display:inline-flex;
            align-items:center;
            gap:6px;
            color:var(--status-color);
            background:#f8fafc;
            border:1px solid #e2e8f0;
            border-radius:999px;
            padding:6px 12px;
            font-size:0.92rem;
            font-weight:900;
            white-space:nowrap;
          }
          .status-summary {
            color:#475569;
            font-size:0.93rem;
            line-height:1.55;
            margin:10px 0 12px 0;
          }
          .status-meta {
            color:#94a3b8;
            font-size:0.8rem;
            font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
          }
          .error-box {
            background:#fff1f2;
            border:1px solid #fecdd3;
            color:#991b1b;
            border-radius:14px;
            padding:12px 14px;
            margin-top:12px;
            font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
            font-size:0.84rem;
            line-height:1.55;
            white-space:pre-wrap;
          }
          @media (max-width: 900px) {
            .status-grid { grid-template-columns:1fr; }
          }
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

    selected_day = st.selectbox(
        "選擇日期",
        day_options,
        index=default_index,
    )

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
        overall_text = "有失敗項目"
        overall_icon = "❌"
        overall_color = "#dc2626"
    elif success_count:
        overall_text = "全部成功"
        overall_icon = "✅"
        overall_color = "#16a34a"
    else:
        overall_text = "尚無成功紀錄"
        overall_icon = "⚪"
        overall_color = "#64748b"

    failed_summary_html = ""
    if failed_rows:
        items = []
        for r in failed_rows:
            msg = "\n".join(r["important"][:5])
            items.append(
                f"<div style='margin-top:10px;'><b>{html.escape(r['label'])}</b><br>{html.escape(msg)}</div>"
            )
        failed_summary_html = (
            "<div class='error-box'>"
            + "<b>失敗項目與錯誤內容：</b>"
            + "".join(items)
            + "</div>"
        )

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

                json_file = (
                    Path("logs")
                    / selected_day
                    / f"{row['job_name']}.json"
                )

                meta = {}

                if json_file.exists():
                    try:
                        meta = json.loads(
                            json_file.read_text(
                                encoding="utf-8"
                            )
                        )
                    except Exception:
                        meta = {}

                started_at = meta.get("started_at", "-")
                finished_at = meta.get("finished_at", "-")
                duration = meta.get("duration_seconds", "-")
                status = meta.get("status", "-")

                c1, c2, c3 = st.columns(3)

                with c1:
                    st.caption("開始時間")
                    st.write(started_at)

                with c2:
                    st.caption("完成時間")
                    st.write(finished_at)

                with c3:
                    st.caption("耗時")
                    st.write(f"{duration} 秒")

                st.divider()

                if row["exit_code"] == "0":

                    st.success(f"{row['label']}：成功")

                elif row["exit_code"] == "missing":

                    st.warning(f"{row['label']}：尚無 exit code")

                else:

                    st.error(
                        f"{row['label']}：失敗 / exit code {row['exit_code']}"
                    )

                    with st.expander(
                        "查看錯誤詳細 log",
                        expanded=False
                    ):

                        st.code(
                            row["content"] or "空 log",
                            language="text"
                        )


# ═══════════════════════════════════════════════════════════
# 系統 / 功能設定
# ═══════════════════════════════════════════════════════════
SYSTEM_FUNCTIONS_BY_TYPE = {
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
}

DAILY_SCRIPT_MAP = {
    "排班統計表": "tools/scheduled_daily/schedule_report.py",
    "專員班表": "tools/scheduled_daily/staff_schedule.py",
    "專員個資": "tools/scheduled_daily/staff_info.py",
    "當月次月訂單": "tools/scheduled_daily/orders_report.py",
    "業績報表": "tools/scheduled_daily/performance_report.py",
}

DAILY_TARGET_MAP = {
    "一鍵執行日排程": "all",
    "排班統計表": "schedule_report",
    "專員班表": "staff_schedule",
    "當月次月訂單": "orders_report",
    "專員個資": "staff_info",
}


MONTHLY_SCRIPT_MAP = {
    "上半月訂單": ["tools/scheduled_monthly/half_month_orders.py", "1"],
    "下半月訂單": ["tools/scheduled_monthly/half_month_orders.py", "2"],
    "已退款": ["tools/scheduled_monthly/refund_report.py"],
    "預收": ["tools/scheduled_monthly/prepaid_report.py"],
    "儲值金結算": ["tools/scheduled_monthly/stored_value_settlement.py"],
    "儲值金預收": ["tools/scheduled_monthly/stored_value_prepaid.py"],
}

FIELD_SCRIPT_MAP = {
    "外場排班統計表": ["tools/field_management/schedule_stats.py"],
    "外場專員班表": ["tools/field_management/staff_schedule.py"],
    "外場當月次月訂單": ["tools/field_management/orders.py"],
    "外場專員個資": ["tools/field_management/staff_profile.py"],
    "一鍵執行外場日排程": [
        "module:tools.field_management.scheduler",
        "--target",
        "all",
    ],
}


def functions_for_system(sys_cfg: dict) -> list[str]:
    system_type = sys_cfg.get("type", "vip")
    return SYSTEM_FUNCTIONS_BY_TYPE.get(system_type, ["尚未設定功能"])


# ═══════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════
require_login()

sync_view_from_query_params()

config = merge_legacy_secrets(load_config())
systems = [
    s for s in get_enabled_systems(config)
    if can_access_system(s.get("type", ""))
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


# ═══════════════════════════════════════════════════════════
# UI — 主標題
# ═══════════════════════════════════════════════════════════
st.markdown(
    '<div class="app-title">🧰 Tools App 作業系統</div>',
    unsafe_allow_html=True,
)
render_user_bar()


# ═══════════════════════════════════════════════════════════
# UI — 執行設定
# ═══════════════════════════════════════════════════════════
st.markdown('<div class="card">', unsafe_allow_html=True)

head_left, head_report, head_log = st.columns([2.2, 1, 1])

with head_left:
    st.markdown('<div class="card-title">⚙️ 執行設定</div>', unsafe_allow_html=True)

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
    st.markdown(
        '<div class="field-label">🗂️ 執行系統</div>',
        unsafe_allow_html=True,
    )

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
    st.markdown(
        '<div class="field-label">🎯 執行功能</div>',
        unsafe_allow_html=True,
    )

    selected_function = st.selectbox(
        "執行功能",
        functions_for_system(selected_system),
        label_visibility="collapsed",
        key="selected_function",
    )

monthly_order_functions = ["上半月訂單", "下半月訂單"]

period = ""
start_date_value = None
end_date_value = None
monthly_date_mode = "期別"

date_col, area_col = st.columns([2, 1])

with date_col:
    if system_type == "monthly_scheduler" and selected_function in monthly_order_functions:
        st.markdown(
            '<div class="field-label">📆 執行方式</div>',
            unsafe_allow_html=True,
        )

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
                key="period_monthly_order",
            )
        else:
            d1, d2 = st.columns(2)
            today_date = datetime.now(TW_TZ).date()
            with d1:
                start_date_value = st.date_input(
                    "開始日期",
                    value=today_date,
                    key="monthly_start_date",
                )
            with d2:
                end_date_value = st.date_input(
                    "結束日期",
                    value=today_date,
                    key="monthly_end_date",
                )
            period = start_date_value.strftime("%Y%m%d")

    elif system_type in ["vip", "monthly_scheduler"]:
        st.markdown(
            '<div class="field-label">📆 執行期別</div>',
            unsafe_allow_html=True,
        )
        period = st.text_input(
            "執行期別",
            value=tw_now_text("%Y%m"),
            placeholder="例如：202605",
            label_visibility="collapsed",
            key="period",
        )

    else:
        st.markdown(
            '<div class="field-label">📆 執行日期區間</div>',
            unsafe_allow_html=True,
        )
        d1, d2 = st.columns(2)
        today_date = datetime.now(TW_TZ).date()
        with d1:
            start_date_value = st.date_input(
                "開始日期",
                value=today_date,
                key="start_date",
            )
        with d2:
            end_date_value = st.date_input(
                "結束日期",
                value=today_date,
                key="end_date",
            )
        period = start_date_value.strftime("%Y%m%d")

with area_col:
    st.markdown(
        '<div class="field-label">📍 執行區域</div>',
        unsafe_allow_html=True,
    )

    area_options = available_areas_for_system(selected_system)

    # 月排程如果設定檔還沒有 areas，就用預設地區。
    # 這樣上下半月訂單一定可以選單一地區，不會被鎖住。
    if system_type == "monthly_scheduler" and (not area_options or area_options == ["全區"]):
        area_options = ["台北", "新北", "台中", "桃園", "新竹", "高雄"]

    if not area_options:
        area_options = ["全區"]

    area_select_options = ["全區"] + [area for area in area_options if area != "全區"]

    selected_area_value = st.selectbox(
        "執行區域",
        area_select_options,
        index=0,
        label_visibility="collapsed",
        key=f"area_select_{system_name}_{selected_function}",
    )

    # selected_areas 一律只放使用者目前選到的值。
    # 全區 -> 後面轉成 --area all
    # 台北 -> 後面轉成 --area 台北
    selected_areas = [selected_area_value]

    if selected_area_value == "全區":
        st.caption("將執行全部已啟用地區")
    else:
        st.caption(f"只執行：{selected_area_value}")

run_clicked = st.button("▶ 執行", use_container_width=True)

st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 日誌# UI — 日誌
# ═══════════════════════════════════════════════════════════
render_log()

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

        system_type_options = ["vip", "daily_scheduler", "monthly_scheduler", "field_daily_schedule"]

        if st.session_state.adding_system:
            with st.form("add_system_form"):
                st.markdown("**新增系統設定**")

                new_name = st.text_input("設定系統名稱", placeholder="例如：外場日排程系統")
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
                    spreadsheet_ids = mask_nested_ids(sys_cfg.get("spreadsheet_ids", {}))

                    extra_detail = f"""
      <div class="detail-row"><strong>排班統計資料夾</strong>：{html.escape(str(folder_ids.get("schedule_stats", "")))}</div>
      <div class="detail-row"><strong>訂單資料夾</strong>：台北 / 台中 已設定</div>
      <div class="detail-row"><strong>專員個資資料夾</strong>：台北 / 台中 已設定</div>
      <div class="detail-row"><strong>專員班表資料夾</strong>：台北 / 台中 已設定</div>
      <div class="detail-row"><strong>目標表單</strong>：名冊 / 薪資 / 辦公室 已設定</div>
    """
                elif current_type == "monthly_scheduler":
                    monthly_areas = normalize_monthly_areas(sys_cfg)
                    enabled_area_names = [
                        area_name
                        for area_name, area_info in monthly_areas.items()
                        if area_info.get("enabled", True)
                    ]
                    disabled_area_names = [
                        area_name
                        for area_name, area_info in monthly_areas.items()
                        if not area_info.get("enabled", True)
                    ]
                    area_summary = "、".join(enabled_area_names) if enabled_area_names else "未設定"
                    disabled_summary = "、".join(disabled_area_names) if disabled_area_names else "無"
                    area_root_lines = []
                    for area_name, area_info in monthly_areas.items():
                        status_text = "啟用" if area_info.get("enabled", True) else "停用"
                        area_root_lines.append(
                            f"{area_name}：{mask_id(area_info.get('folder_id', ''))}（{status_text}）"
                        )
                    area_root_summary = "<br>".join(html.escape(line) for line in area_root_lines) or "未設定"

                    extra_detail = f"""
      <div class="detail-row"><strong>月排程總根目錄 ID</strong>：{mask_id(folder_id)}</div>
      <div class="detail-row"><strong>啟用地區</strong>：{html.escape(area_summary)}</div>
      <div class="detail-row"><strong>停用地區</strong>：{html.escape(disabled_summary)}</div>
      <div class="detail-row"><strong>地區根目錄</strong>：<br>{area_root_summary}</div>
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
        date_keys = [period]

    if system_type == "field_daily_schedule" and not selected_areas:
        add_log("請至少選擇一個執行區域", "error")
        st.rerun()

    add_log(f"開始執行：{system_name} / {selected_function} / {date_keys[0]}~{date_keys[-1]} / 區域 {', '.join(selected_areas)}")

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

            if selected_function == "業績報表":
                script = DAILY_SCRIPT_MAP.get(selected_function)

                if not script:
                    raise RuntimeError(f"找不到日排程功能：{selected_function}")

                result = run_script(script)
                add_log("業績報表已更新，可點「📊 查看業績報表」開啟。", "success")

            else:
                from tools.scheduled_daily.scheduler import main as run_daily_scheduler

                daily_target = DAILY_TARGET_MAP.get(selected_function)

                if not daily_target:
                    raise RuntimeError(f"找不到日排程 target：{selected_function}")

                result = run_daily_scheduler(
                    target=daily_target,
                    folder_id=folder_id,
                )

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
                                "--start",
                                start_date_value.strftime("%Y-%m-%d"),
                                "--end",
                                end_date_value.strftime("%Y-%m-%d"),
                            ])
                        else:
                            # 舊版 half_month_orders.py 仍支援 positional half。
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
                    result = run_script(script, [*base_args, "--folder-id", folder_id])

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

                    # scheduler.py target all 若指定區域也支援 --area
                    if area_name != "全區":
                        args.extend(["--area", area_name])

                    add_log(f"外場排程執行：{selected_function} / {date_key} / {area_name}")
                    results.append(run_script(script, args))

            result = results

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
