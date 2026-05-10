"""
Tool App 主控入口
版面參考 Lemon Clean 薪資系統主控，改為多系統工具平台。

目前系統：
- 儲值金管理

需要搭配：
- tools/vip_stored_value.py
- services/google_auth.py
- services/google_sheets.py
- services/vip_workflow.py
"""

from __future__ import annotations

import streamlit as st
from datetime import datetime


st.set_page_config(
    page_title="Tools App",
    page_icon="🧰",
    layout="centered",
)


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


# ═══════════════════════════════════════════════════════════
# 工具函式
# ═══════════════════════════════════════════════════════════
def add_log(message: str, level: str = "info") -> None:
    now = datetime.now().strftime("%H:%M:%S")
    icons = {"info": "🔵", "success": "✅", "error": "❌", "warning": "⚠️"}
    icon = icons.get(level, "🔵")
    st.session_state.logs.append(f"[{now}] {icon} {message}")
    if len(st.session_state.logs) > 500:
        st.session_state.logs = st.session_state.logs[-500:]


def render_log() -> None:
    html = (
        '<div class="log-box">'
        '<div class="log-header">'
        '<span>📋 執行日誌</span>'
        '<span style="background:#1e4757;padding:3px 10px;border-radius:20px;font-size:0.75rem;">即時更新</span>'
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


def get_secret_text(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return default


def get_system_config(system_name: str) -> dict:
    """
    優先讀取多系統設定：
    [systems.儲值金管理]
    master_spreadsheet_id = "..."
    root_folder_id = "..."

    若還沒改成多系統 secrets，則相容舊版：
    MASTER_SPREADSHEET_ID = "..."
    ROOT_FOLDER_ID = "..."
    """
    cfg = {
        "system_name": system_name,
        "master_spreadsheet_id": "",
        "root_folder_id": "",
    }

    try:
        systems = st.secrets.get("systems", {})
        if system_name in systems:
            sys_cfg = systems[system_name]
            cfg["master_spreadsheet_id"] = str(sys_cfg.get("master_spreadsheet_id", ""))
            cfg["root_folder_id"] = str(sys_cfg.get("root_folder_id", ""))
            return cfg
    except Exception:
        pass

    cfg["master_spreadsheet_id"] = get_secret_text("MASTER_SPREADSHEET_ID")
    cfg["root_folder_id"] = get_secret_text("ROOT_FOLDER_ID")
    return cfg


# ═══════════════════════════════════════════════════════════
# 系統 / 功能設定
# ═══════════════════════════════════════════════════════════
SYSTEM_FUNCTIONS = {
    "儲值金管理": [
        "建立當月彙整檔",
        "轉檔＋高雄/新竹彙整",
        "搬運",
        "計算",
        "彙整金額",
        "一鍵執行：建立＋轉檔＋搬運＋計算＋彙整金額",
    ],
}

SYSTEM_LABELS = list(SYSTEM_FUNCTIONS.keys())


# ═══════════════════════════════════════════════════════════
# UI — 主標題
# ═══════════════════════════════════════════════════════════
st.markdown('<div class="app-title">🧰 Tools App 作業系統</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 執行設定
# ═══════════════════════════════════════════════════════════
st.markdown('<div class="card"><div class="card-title">⚙️ 執行設定</div>', unsafe_allow_html=True)

c1, c2 = st.columns(2)
with c1:
    st.markdown('<div class="field-label">📆 執行期別</div>', unsafe_allow_html=True)
    period = st.text_input(
        "執行期別",
        value="202604",
        placeholder="例如：202604",
        label_visibility="collapsed",
        key="period",
    )

with c2:
    st.markdown('<div class="field-label">🗂️ 執行系統</div>', unsafe_allow_html=True)
    system_name = st.selectbox(
        "執行系統",
        SYSTEM_LABELS,
        index=SYSTEM_LABELS.index(st.session_state.selected_system_name)
        if st.session_state.selected_system_name in SYSTEM_LABELS
        else 0,
        label_visibility="collapsed",
        key="system_name",
    )
    st.session_state.selected_system_name = system_name

st.markdown('<div class="field-label">🎯 執行功能</div>', unsafe_allow_html=True)
selected_function = st.selectbox(
    "執行功能",
    SYSTEM_FUNCTIONS[system_name],
    label_visibility="collapsed",
    key="selected_function",
)

run_clicked = st.button("▶ 執行", use_container_width=True)

st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 設定檔
# ═══════════════════════════════════════════════════════════
cfg = get_system_config(system_name)

st.markdown('<div class="card"><div class="card-title">🗃️ 設定檔</div>', unsafe_allow_html=True)

s1, s2 = st.columns(2)
with s1:
    st.markdown('<div class="field-label">設定系統名稱</div>', unsafe_allow_html=True)
    st.text_input(
        "設定系統名稱",
        value=cfg["system_name"],
        disabled=True,
        label_visibility="collapsed",
    )

with s2:
    st.markdown('<div class="field-label">設定主控表 ID</div>', unsafe_allow_html=True)
    st.text_input(
        "設定主控表 ID",
        value=cfg["master_spreadsheet_id"],
        disabled=True,
        label_visibility="collapsed",
    )

st.markdown('<div class="field-label">設定根目錄</div>', unsafe_allow_html=True)
st.text_input(
    "設定根目錄",
    value=cfg["root_folder_id"],
    disabled=True,
    label_visibility="collapsed",
)

st.markdown(
    """
<div class="setting-note">
目前設定從 <code>.streamlit/secrets.toml</code> 讀取。之後若新增其他系統，可在 secrets 裡新增不同系統的主控表 ID 與根目錄 ID。
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 日誌
# ═══════════════════════════════════════════════════════════
render_log()

clear_col, _ = st.columns([1, 3])
with clear_col:
    if st.button("🗑️ 清除日誌"):
        st.session_state.logs = ["[--:--:--] 日誌已清除"]
        st.rerun()


# ═══════════════════════════════════════════════════════════
# 執行邏輯
# ═══════════════════════════════════════════════════════════
if run_clicked:
    if not period:
        add_log("請先輸入執行期別", "error")
        st.rerun()

    if system_name != "儲值金管理":
        add_log(f"{system_name} 尚未實作", "warning")
        st.rerun()

    if not cfg["master_spreadsheet_id"]:
        add_log("尚未設定主控表 ID", "error")
        st.rerun()

    if not cfg["root_folder_id"]:
        add_log("尚未設定根目錄 ID", "error")
        st.rerun()

    add_log(f"開始執行：{system_name} / {selected_function} / 期別 {period}")

    try:
        from tools.vip_stored_value import get_workflow

        workflow = get_workflow(
            master_spreadsheet_id=cfg["master_spreadsheet_id"],
            root_folder_id=cfg["root_folder_id"],
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
            result = []
            result.append(workflow.create_monthly_summary(period))
            result.append(workflow.convert_files(period))
            result.append(workflow.move_files(period))
            result.append(workflow.apply_formulas(period))
            result.append(workflow.summarize_amounts(period))

        else:
            result = None
            add_log(f"未知功能：{selected_function}", "warning")

        if isinstance(result, list):
            for item in result:
                add_log(str(item), "success")
        elif result is not None:
            add_log(str(result), "success")

        add_log("執行完成", "success")

    except TypeError:
        add_log(
            "get_workflow() 目前可能還是舊版，不支援傳入 master_spreadsheet_id/root_folder_id/system_name。"
            "請同步修改 tools/vip_stored_value.py。",
            "error",
        )

    except Exception as e:
        import traceback

        add_log(f"執行失敗：{e}", "error")
        add_log(traceback.format_exc(), "error")

    st.rerun()
"""
Tool App 主控入口
版面參考 Lemon Clean 薪資系統主控，改為多系統工具平台。

目前系統：
- 儲值金管理

需要搭配：
- tools/vip_stored_value.py
- services/google_auth.py
- services/google_sheets.py
- services/vip_workflow.py
"""

from __future__ import annotations

import streamlit as st
from datetime import datetime


st.set_page_config(
    page_title="Tools App",
    page_icon="🧰",
    layout="centered",
)


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


# ═══════════════════════════════════════════════════════════
# 工具函式
# ═══════════════════════════════════════════════════════════
def add_log(message: str, level: str = "info") -> None:
    now = datetime.now().strftime("%H:%M:%S")
    icons = {"info": "🔵", "success": "✅", "error": "❌", "warning": "⚠️"}
    icon = icons.get(level, "🔵")
    st.session_state.logs.append(f"[{now}] {icon} {message}")
    if len(st.session_state.logs) > 500:
        st.session_state.logs = st.session_state.logs[-500:]


def render_log() -> None:
    html = (
        '<div class="log-box">'
        '<div class="log-header">'
        '<span>📋 執行日誌</span>'
        '<span style="background:#1e4757;padding:3px 10px;border-radius:20px;font-size:0.75rem;">即時更新</span>'
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


def get_secret_text(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return default


def get_system_config(system_name: str) -> dict:
    """
    優先讀取多系統設定：
    [systems.儲值金管理]
    master_spreadsheet_id = "..."
    root_folder_id = "..."

    若還沒改成多系統 secrets，則相容舊版：
    MASTER_SPREADSHEET_ID = "..."
    ROOT_FOLDER_ID = "..."
    """
    cfg = {
        "system_name": system_name,
        "master_spreadsheet_id": "",
        "root_folder_id": "",
    }

    try:
        systems = st.secrets.get("systems", {})
        if system_name in systems:
            sys_cfg = systems[system_name]
            cfg["master_spreadsheet_id"] = str(sys_cfg.get("master_spreadsheet_id", ""))
            cfg["root_folder_id"] = str(sys_cfg.get("root_folder_id", ""))
            return cfg
    except Exception:
        pass

    cfg["master_spreadsheet_id"] = get_secret_text("MASTER_SPREADSHEET_ID")
    cfg["root_folder_id"] = get_secret_text("ROOT_FOLDER_ID")
    return cfg


# ═══════════════════════════════════════════════════════════
# 系統 / 功能設定
# ═══════════════════════════════════════════════════════════
SYSTEM_FUNCTIONS = {
    "儲值金管理": [
        "建立當月彙整檔",
        "轉檔＋高雄/新竹彙整",
        "搬運",
        "計算",
        "彙整金額",
        "一鍵執行：建立＋轉檔＋搬運＋計算＋彙整金額",
    ],
}

SYSTEM_LABELS = list(SYSTEM_FUNCTIONS.keys())


# ═══════════════════════════════════════════════════════════
# UI — 主標題
# ═══════════════════════════════════════════════════════════
st.markdown('<div class="app-title">🧰 Tools App 作業系統</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 執行設定
# ═══════════════════════════════════════════════════════════
st.markdown('<div class="card"><div class="card-title">⚙️ 執行設定</div>', unsafe_allow_html=True)

c1, c2 = st.columns(2)
with c1:
    st.markdown('<div class="field-label">📆 執行期別</div>', unsafe_allow_html=True)
    period = st.text_input(
        "執行期別",
        value="202604",
        placeholder="例如：202604",
        label_visibility="collapsed",
        key="period",
    )

with c2:
    st.markdown('<div class="field-label">🗂️ 執行系統</div>', unsafe_allow_html=True)
    system_name = st.selectbox(
        "執行系統",
        SYSTEM_LABELS,
        index=SYSTEM_LABELS.index(st.session_state.selected_system_name)
        if st.session_state.selected_system_name in SYSTEM_LABELS
        else 0,
        label_visibility="collapsed",
        key="system_name",
    )
    st.session_state.selected_system_name = system_name

st.markdown('<div class="field-label">🎯 執行功能</div>', unsafe_allow_html=True)
selected_function = st.selectbox(
    "執行功能",
    SYSTEM_FUNCTIONS[system_name],
    label_visibility="collapsed",
    key="selected_function",
)

run_clicked = st.button("▶ 執行", use_container_width=True)

st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 設定檔
# ═══════════════════════════════════════════════════════════
cfg = get_system_config(system_name)

st.markdown('<div class="card"><div class="card-title">🗃️ 設定檔</div>', unsafe_allow_html=True)

s1, s2 = st.columns(2)
with s1:
    st.markdown('<div class="field-label">設定系統名稱</div>', unsafe_allow_html=True)
    st.text_input(
        "設定系統名稱",
        value=cfg["system_name"],
        disabled=True,
        label_visibility="collapsed",
    )

with s2:
    st.markdown('<div class="field-label">設定主控表 ID</div>', unsafe_allow_html=True)
    st.text_input(
        "設定主控表 ID",
        value=cfg["master_spreadsheet_id"],
        disabled=True,
        label_visibility="collapsed",
    )

st.markdown('<div class="field-label">設定根目錄</div>', unsafe_allow_html=True)
st.text_input(
    "設定根目錄",
    value=cfg["root_folder_id"],
    disabled=True,
    label_visibility="collapsed",
)

st.markdown(
    """
<div class="setting-note">
目前設定從 <code>.streamlit/secrets.toml</code> 讀取。之後若新增其他系統，可在 secrets 裡新增不同系統的主控表 ID 與根目錄 ID。
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("</div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# UI — 日誌
# ═══════════════════════════════════════════════════════════
render_log()

clear_col, _ = st.columns([1, 3])
with clear_col:
    if st.button("🗑️ 清除日誌"):
        st.session_state.logs = ["[--:--:--] 日誌已清除"]
        st.rerun()


# ═══════════════════════════════════════════════════════════
# 執行邏輯
# ═══════════════════════════════════════════════════════════
if run_clicked:
    if not period:
        add_log("請先輸入執行期別", "error")
        st.rerun()

    if system_name != "儲值金管理":
        add_log(f"{system_name} 尚未實作", "warning")
        st.rerun()

    if not cfg["master_spreadsheet_id"]:
        add_log("尚未設定主控表 ID", "error")
        st.rerun()

    if not cfg["root_folder_id"]:
        add_log("尚未設定根目錄 ID", "error")
        st.rerun()

    add_log(f"開始執行：{system_name} / {selected_function} / 期別 {period}")

    try:
        from tools.vip_stored_value import get_workflow

        workflow = get_workflow(
            master_spreadsheet_id=cfg["master_spreadsheet_id"],
            root_folder_id=cfg["root_folder_id"],
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
            result = []
            result.append(workflow.create_monthly_summary(period))
            result.append(workflow.convert_files(period))
            result.append(workflow.move_files(period))
            result.append(workflow.apply_formulas(period))
            result.append(workflow.summarize_amounts(period))

        else:
            result = None
            add_log(f"未知功能：{selected_function}", "warning")

        if isinstance(result, list):
            for item in result:
                add_log(str(item), "success")
        elif result is not None:
            add_log(str(result), "success")

        add_log("執行完成", "success")

    except TypeError:
        add_log(
            "get_workflow() 目前可能還是舊版，不支援傳入 master_spreadsheet_id/root_folder_id/system_name。"
            "請同步修改 tools/vip_stored_value.py。",
            "error",
        )

    except Exception as e:
        import traceback

        add_log(f"執行失敗：{e}", "error")
        add_log(traceback.format_exc(), "error")

    st.rerun()
