"""
Tool App 主控入口
多系統工具平台：
- 儲值金管理
- 日排程系統
- 月排程系統

版面：
1. 執行設定
2. 執行日誌
3. 可收合設定檔管理

重點：
- 設定檔可新增 / 編輯 / 刪除
- 設定檔放在執行日誌下方，且可收合
- 使用台北時區 Asia/Taipei
- 儲值金管理直接建立 VipStoredValueWorkflow，不再呼叫舊版 get_workflow()
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
import yaml


TW_TZ = ZoneInfo("Asia/Taipei")


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
            "root_folder_id": "",
            "enabled": True,
        },
        {
            "name": "日排程系統",
            "type": "daily_scheduler",
            "master_spreadsheet_id": "",
            "root_folder_id": "",
            "enabled": True,
        },
        {
            "name": "月排程系統",
            "type": "monthly_scheduler",
            "master_spreadsheet_id": "",
            "root_folder_id": "",
            "enabled": True,
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


def load_config() -> dict:
    ensure_config_file()
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if "systems" not in data or not isinstance(data["systems"], list):
        data = DEFAULT_CONFIG
        save_config(data)

    return data


def get_secret_text(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return default


def merge_legacy_secrets(cfg: dict) -> dict:
    """
    相容舊版 secrets：
    MASTER_SPREADSHEET_ID
    ROOT_FOLDER_ID

    若 config/systems.yaml 內的儲值金管理尚未填 ID，
    會先用 secrets 內的舊設定顯示與執行。
    """
    legacy_master = get_secret_text("MASTER_SPREADSHEET_ID")
    legacy_root = get_secret_text("ROOT_FOLDER_ID")

    if not legacy_master and not legacy_root:
        return cfg

    for sys_cfg in cfg.get("systems", []):
        if sys_cfg.get("name") == "儲值金管理":
            if legacy_master and not sys_cfg.get("master_spreadsheet_id"):
                sys_cfg["master_spreadsheet_id"] = legacy_master
            if legacy_root and not sys_cfg.get("root_folder_id"):
                sys_cfg["root_folder_id"] = legacy_root
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
    html = (
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

        html += f'<div class="{css}">{entry}</div>'

    html += "</div></div>"
    st.markdown(html, unsafe_allow_html=True)


def mask_id(value: str) -> str:
    if not value:
        return "❌ 未設定"
    if len(value) <= 14:
        return value
    return f"{value[:8]}...{value[-6:]}"


def get_system_type_label(system_type: str) -> str:
    mapping = {
        "vip": "儲值金管理",
        "daily_scheduler": "日排程系統",
        "monthly_scheduler": "月排程系統",
    }
    return mapping.get(system_type, system_type or "未設定")


def build_vip_workflow(master_id: str, root_id: str, system_name: str):
    """
    直接用目前 toolapp.py 的設定建立 workflow。
    不再呼叫舊版 get_workflow()，避免 SheetsService 參數不一致。
    """
    from services.google_auth import get_gspread_client, get_drive_service
    from services.google_sheets import SheetsService
    from services.vip_workflow import VipStoredValueWorkflow

    try:
        from services.google_drive import DriveService
    except ModuleNotFoundError:
        from services.drive_service import DriveService

    sheets = SheetsService(get_gspread_client())
    drive = DriveService(get_drive_service())

    try:
        return VipStoredValueWorkflow(
            drive,
            sheets,
            master_id,
            root_id,
            system_name,
        )
    except TypeError:
        return VipStoredValueWorkflow(
            drive,
            sheets,
            master_id,
            root_id,
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
        "執行日排程",
    ],
    "monthly_scheduler": [
        "執行月排程",
    ],
}


def functions_for_system(sys_cfg: dict) -> list[str]:
    system_type = sys_cfg.get("type", "vip")
    return SYSTEM_FUNCTIONS_BY_TYPE.get(system_type, ["尚未設定功能"])


config = merge_legacy_secrets(load_config())
systems = get_enabled_systems(config)
system_names = [s.get("name", "") for s in systems if s.get("name")]

if not system_names:
    system_names = ["儲值金管理"]


# ═══════════════════════════════════════════════════════════
# UI — 主標題
# ═══════════════════════════════════════════════════════════
st.markdown(
    '<div class="app-title">🧰 Tools App 作業系統</div>',
    unsafe_allow_html=True,
)


# ═══════════════════════════════════════════════════════════
# UI — 執行設定
# ═══════════════════════════════════════════════════════════
st.markdown(
    '<div class="card"><div class="card-title">⚙️ 執行設定</div>',
    unsafe_allow_html=True,
)

c1, c2 = st.columns(2)

with c1:
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

with c2:
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

run_clicked = st.button("▶ 執行", use_container_width=True)

st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 日誌：在執行視窗下方
# ═══════════════════════════════════════════════════════════
render_log()

clear_col, _ = st.columns([1, 3])

with clear_col:
    if st.button("🗑️ 清除日誌"):
        st.session_state.logs = ["[--:--:--] 日誌已清除"]
        st.rerun()


# ═══════════════════════════════════════════════════════════
# UI — 設定檔：在執行視窗下方，可收合，可新增/編輯
# ═══════════════════════════════════════════════════════════
with st.expander("🗃️ 設定檔管理（新增 / 編輯 / 刪除）", expanded=False):
    st.markdown(
        """
<div class="setting-note">
可在這裡新增 / 編輯不同系統對應的主控表 ID 與根目錄 ID。
Service Account 憑證仍放在 <code>.streamlit/secrets.toml</code>。
系統設定會存到 <code>config/systems.yaml</code>。
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

    if st.session_state.adding_system:
        with st.form("add_system_form"):
            st.markdown("**新增系統設定**")

            new_name = st.text_input("設定系統名稱", placeholder="例如：儲值金管理")
            new_type = st.selectbox(
                "設定系統類型",
                ["vip", "daily_scheduler", "monthly_scheduler"],
                format_func=get_system_type_label,
            )
            new_master_id = st.text_input("設定主控表 ID")
            new_root_id = st.text_input("設定根目錄 ID")
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
                    systems_all.append(
                        {
                            "name": new_name,
                            "type": new_type,
                            "master_spreadsheet_id": new_master_id,
                            "root_folder_id": new_root_id,
                            "enabled": new_enabled,
                        }
                    )
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
        system_type = sys_cfg.get("type", "vip")
        master_id = sys_cfg.get("master_spreadsheet_id", "")
        root_id = sys_cfg.get("root_folder_id", "")
        enabled = sys_cfg.get("enabled", True)

        needs_ids = system_type == "vip"
        complete = bool(name and (not needs_ids or (master_id and root_id)))
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
                    ["vip", "daily_scheduler", "monthly_scheduler"],
                    index=["vip", "daily_scheduler", "monthly_scheduler"].index(system_type)
                    if system_type in ["vip", "daily_scheduler", "monthly_scheduler"]
                    else 0,
                    format_func=get_system_type_label,
                )
                e_master_id = st.text_input("設定主控表 ID", value=master_id)
                e_root_id = st.text_input("設定根目錄 ID", value=root_id)
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
                        config["systems"][i] = {
                            "name": e_name,
                            "type": e_type,
                            "master_spreadsheet_id": e_master_id,
                            "root_folder_id": e_root_id,
                            "enabled": e_enabled,
                        }
                        save_config(config)
                        add_log(f"更新系統設定：{e_name}", "success")
                        st.session_state.editing_system = None
                        st.rerun()

                if cancel_edit:
                    st.session_state.editing_system = None
                    st.rerun()

        else:
            enabled_text = "✅ 啟用" if enabled else "⚠️ 停用"
            st.markdown(
                f"""
<div class="system-card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
    <strong style="color:#0a4b6e;">🗂️ {name}</strong>{badge}
  </div>
  <div class="detail-row"><strong>系統類型</strong>：{get_system_type_label(system_type)}</div>
  <div class="detail-row"><strong>狀態</strong>：{enabled_text}</div>
  <div class="detail-row"><strong>主控表 ID</strong>：{mask_id(master_id)}</div>
  <div class="detail-row"><strong>根目錄 ID</strong>：{mask_id(root_id)}</div>
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
    if not period:
        add_log("請先輸入執行期別", "error")
        st.rerun()

    if not selected_system:
        add_log("請先新增或啟用系統設定", "error")
        st.rerun()

    system_type = selected_system.get("type", "vip")
    master_id = selected_system.get("master_spreadsheet_id", "")
    root_id = selected_system.get("root_folder_id", "")

    add_log(f"開始執行：{system_name} / {selected_function} / 期別 {period}")

    try:
        result = None

        # ───────────────────────────────────────────────
        # 儲值金管理
        # ───────────────────────────────────────────────
        if system_type == "vip":
            if not master_id:
                add_log("尚未設定主控表 ID", "error")
                st.rerun()

            if not root_id:
                add_log("尚未設定根目錄 ID", "error")
                st.rerun()

            workflow = build_vip_workflow(
                master_id=master_id,
                root_id=root_id,
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

        # ───────────────────────────────────────────────
        # 日排程系統
        # ───────────────────────────────────────────────
        elif system_type == "daily_scheduler":
            from tools.scheduled_daily.scheduler import main as run_daily_scheduler

            result = run_daily_scheduler()

        # ───────────────────────────────────────────────
        # 月排程系統
        # ───────────────────────────────────────────────
        elif system_type == "monthly_scheduler":
            from tools.scheduled_monthly.scheduler import main as run_monthly_scheduler

            result = run_monthly_scheduler()

        else:
            add_log(f"{system_type} 尚未實作", "warning")

        if isinstance(result, list):
            for item in result:
                add_log(str(item), "success")

        elif result is not None:
            add_log(str(result), "success")

        add_log("執行完成", "success")

    except Exception as e:
        import traceback

        add_log(f"執行失敗：{e}", "error")
        add_log(traceback.format_exc(), "error")

    st.rerun()
