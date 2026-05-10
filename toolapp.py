"""
Tool App 主控入口
多系統工具平台：
- 儲值金管理
- 日排程系統
- 月排程系統
"""

from __future__ import annotations

import subprocess
import sys
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


def render_report() -> None:
    import json
    import pandas as pd
    from pathlib import Path

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
            return f"{int(float(str(value).replace(',', ''))):,}"
        except Exception:
            return "0"

    def ratio(value) -> str:
        try:
            if isinstance(value, str) and value.endswith("%"):
                return value
            return f"{float(value):.2%}"
        except Exception:
            return "-"

    def get_total(df: pd.DataFrame, col: str) -> str:
        if df.empty or col not in df.columns:
            return "0"
        total_row = df[df["城市"].astype(str) == "加總"] if "城市" in df.columns else pd.DataFrame()
        if not total_row.empty:
            return money(total_row.iloc[0][col])
        return money(df[col].sum())

    def format_money_ratio_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
        show = df.copy()
        numeric_cols = []

        for col in show.columns:
            if "佔比" in col:
                show[col] = (
                    pd.to_numeric(show[col], errors="coerce")
                    .fillna(0)
                    .map(lambda x: f"{x:.2%}")
                )
                numeric_cols.append(col)
            elif any(x in col for x in ["業績", "加總", "家電", "儲值金"]):
                show[col] = (
                    pd.to_numeric(show[col], errors="coerce")
                    .fillna(0)
                    .map(lambda x: f"{int(x):,}")
                )
                numeric_cols.append(col)

        return show.style.set_properties(
            subset=numeric_cols,
            **{"text-align": "right"},
        )

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
                run_script("tools/scheduled_daily/performance_report.py")
                add_log("業績報表更新完成", "success")
                st.rerun()
            except Exception as e:
                add_log(f"業績報表更新失敗：{e}", "error")
                st.error(f"業績報表更新失敗：{e}")

    with top_cols[2]:
        if st.button("📂 重新讀取資料", use_container_width=True):
            st.rerun()

    with top_cols[3]:
        try:
            st.link_button("🔗 開啟獨立連結", "?view=report", use_container_width=True)
        except Exception:
            st.caption("網址加上 ?view=report 可直接開啟此頁")

    if not df4_path.exists():
        st.info("尚未產生業績報表資料，請先在主控台執行「日排程系統 → 業績報表」。")
        return

    df4 = load_csv(df4_path)
    daily_df = load_csv(daily_path)
    next_df = load_csv(next_path)
    month_end_df = load_csv(month_end_path)

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    if meta:
        st.info(f"📅 最新更新時間：{meta.get('updated_at', '-')}")
        if meta.get("error"):
            st.warning(meta.get("error"))

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

    st.divider()

    st.markdown("### 📍 各區月度摘要")
    st.dataframe(
        format_money_ratio_table(df4),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    st.markdown("## 月度追蹤")
    tabs = st.tabs(["當月每日業績", "次月每日業績", "月底快照"])

    with tabs[0]:
        st.caption("資料來源：上方各區月度摘要 df4.csv 的本月加總；每次更新資料會新增一筆。")
        if daily_df.empty:
            st.info("尚無當月每日業績資料")
        else:
            st.dataframe(
                format_money_ratio_table(daily_df),
                use_container_width=True,
                hide_index=True,
            )

    with tabs[1]:
        st.caption("資料來源：上方各區月度摘要 df4.csv 的次月加總；每次更新資料會新增一筆。")
        if next_df.empty:
            st.info("尚無次月每日業績資料")
        else:
            st.dataframe(
                format_money_ratio_table(next_df),
                use_container_width=True,
                hide_index=True,
            )

    with tabs[2]:
        st.caption("月底快照只在月底執行時產生。")
        if month_end_df.empty:
            st.info("尚無月底快照")
        else:
            st.dataframe(
                format_money_ratio_table(month_end_df),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()

    with st.expander("📧 信件預覽", expanded=False):
        if html_path.exists():
            html = html_path.read_text(encoding="utf-8")
            st.components.v1.html(html, height=600, scrolling=True)
        else:
            st.info("尚無 Email HTML")


def render_log_page() -> None:
    from pathlib import Path
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import html

    JOB_LABELS = {
        "schedule_report": "排班統計表",
        "staff_schedule": "專員班表",
        "orders_report": "當月次月訂單",
        "staff_info": "專員個資",
        "send_daily_result": "通知信",
        "performance_report": "業績報表",
    }

    JOB_ORDER = [
        "schedule_report",
        "staff_schedule",
        "orders_report",
        "staff_info",
        "send_daily_result",
        "performance_report",
    ]

    def today_key() -> str:
        return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")

    def read_text_safe(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def exit_code_for(log_dir: Path, job_name: str) -> str:
        return read_text_safe(log_dir / f"{job_name}.exit").strip() or "missing"

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

        matched = []
        for line in lines:
            if any(k in line for k in keywords):
                matched.append(line)

        if matched:
            return matched[-limit:]

        return lines[-min(limit, len(lines)):]

    def summary_line(content: str, exit_code: str) -> str:
        lines = important_lines(content, limit=1)
        if exit_code == "0":
            success_keywords = ["全部完成", "執行完成", "已上傳", "完成"]
            all_lines = [line.strip() for line in content.splitlines() if line.strip()]
            for keyword in success_keywords:
                for line in reversed(all_lines):
                    if keyword in line:
                        return line[-130:]
        return lines[-1][-130:] if lines else "沒有摘要"

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
    selected_day = today_key()
    selected_dir = log_root / selected_day

    if not log_root.exists():
        st.info("目前沒有 logs/ 資料夾。請確認 GitHub Actions 已將 logs/ commit 回 repo。")
        return

    if not selected_dir.exists():
        st.warning(f"今日尚無排程紀錄：{selected_day}")
        return

    log_files = sorted(selected_dir.glob("*.log"))

    job_names = []
    for name in JOB_ORDER:
        if (selected_dir / f"{name}.log").exists() or (selected_dir / f"{name}.exit").exists():
            job_names.append(name)

    for log_file in log_files:
        if log_file.stem not in job_names:
            job_names.append(log_file.stem)

    if not job_names:
        st.info("今日尚無 log 檔。")
        return

    rows = []
    for job_name in job_names:
        label = JOB_LABELS.get(job_name, job_name)
        log_path = selected_dir / f"{job_name}.log"
        content = read_text_safe(log_path)
        exit_code = exit_code_for(selected_dir, job_name)
        status_text, icon, color = status_for(exit_code)
        rows.append(
            {
                "job_name": job_name,
                "label": label,
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

    cards = ['<div class="status-grid">']
    for row in rows:
        error_html = ""
        if row["exit_code"] not in ["0", "missing"]:
            error_html = "<div class='error-box'>" + html.escape("\n".join(row["important"][:6])) + "</div>"

        cards.append(
            f"""
            <div class="status-card" style="--status-color:{row['color']};">
              <div class="status-head">
                <div class="status-name">{html.escape(row['label'])}</div>
                <div class="status-badge">{row['icon']} {html.escape(row['status_text'])}</div>
              </div>
              <div class="status-summary">{html.escape(row['summary'])}</div>
              <div class="status-meta">exit code: {html.escape(row['exit_code'])}</div>
              {error_html}
            </div>
            """
        )
    cards.append("</div>")
    st.markdown("".join(cards), unsafe_allow_html=True)

    with st.expander("🔎 查看詳細 log", expanded=failed_count > 0):
        label_by_job = {row["job_name"]: f"{row['icon']} {row['label']}" for row in rows}
        tabs = st.tabs([label_by_job[row["job_name"]] for row in rows])

        for tab, row in zip(tabs, rows):
            with tab:
                if row["exit_code"] == "0":
                    st.success(f"{row['label']}：成功")
                elif row["exit_code"] == "missing":
                    st.warning(f"{row['label']}：尚無 exit code")
                else:
                    st.error(f"{row['label']}：失敗 / exit code {row['exit_code']}")

                st.code(row["content"] or "空 log", language="text")

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

    cmd = [sys.executable, script_path, *args]

    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parent,
    )

    if completed.stdout:
        add_log(completed.stdout, "info")

    if completed.stderr:
        add_log(completed.stderr, "error")

    if completed.returncode != 0:
        raise RuntimeError(
            f"執行失敗：{script_path}\n"
            f"exit={completed.returncode}\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return f"{script_path} 執行完成"


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


def functions_for_system(sys_cfg: dict) -> list[str]:
    system_type = sys_cfg.get("type", "vip")
    return SYSTEM_FUNCTIONS_BY_TYPE.get(system_type, ["尚未設定功能"])

sync_view_from_query_params()

config = merge_legacy_secrets(load_config())
systems = get_enabled_systems(config)
system_names = [s.get("name", "") for s in systems if s.get("name")]

if not system_names:
    system_names = ["儲值金管理"]


if st.session_state.view == "report":
    render_report()
    st.stop()


if st.session_state.view == "report":
    render_report()
    st.stop()

if st.session_state.view == "log":
    render_log_page()
    st.stop()


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
st.markdown('<div class="card">', unsafe_allow_html=True)

head_left, head_report, head_log = st.columns([2.2, 1, 1])

with head_left:
    st.markdown('<div class="card-title">⚙️ 執行設定</div>', unsafe_allow_html=True)

with head_report:
    if st.button("📊 查看業績報表", use_container_width=True):
        set_view("report")
        st.rerun()

with head_log:
    if st.button("📋 查看排程 Log", use_container_width=True):
        set_view("log")
        st.rerun()

c1, c2 = st.columns(2)

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
system_type = selected_system.get("type", "vip")

with c1:
    label = "📆 執行期別" if system_type in ["vip", "monthly_scheduler"] else "📆 執行日期"
    st.markdown(
        f'<div class="field-label">{label}</div>',
        unsafe_allow_html=True,
    )

    if system_type in ["vip", "monthly_scheduler"]:
        period = st.text_input(
            "執行期別",
            value=tw_now_text("%Y%m"),
            placeholder="例如：202605",
            label_visibility="collapsed",
            key="period",
        )
    else:
        period = tw_now_text("%Y%m%d")
        st.text_input(
            "執行日期",
            value=period,
            disabled=True,
            label_visibility="collapsed",
            key="daily_date",
        )

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
# UI — 日誌
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
with st.expander("🗃️ 設定檔管理（新增 / 編輯 / 刪除）", expanded=False):
    st.markdown(
        """
<div class="setting-note">
可在這裡新增 / 編輯不同系統對應的主控表 ID 與共用雲端資料夾 ID。
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
            new_folder_id = st.text_input("設定共用雲端資料夾 ID")
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
                            "folder_id": new_folder_id,
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
                    ["vip", "daily_scheduler", "monthly_scheduler"],
                    index=(
                        ["vip", "daily_scheduler", "monthly_scheduler"].index(current_type)
                        if current_type in ["vip", "daily_scheduler", "monthly_scheduler"]
                        else 0
                    ),
                    format_func=get_system_type_label,
                )
                e_master_id = st.text_input("設定主控表 ID", value=master_id)
                e_folder_id = st.text_input("設定共用雲端資料夾 ID", value=folder_id)
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
                            "folder_id": e_folder_id,
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
  <div class="detail-row"><strong>系統類型</strong>：{get_system_type_label(current_type)}</div>
  <div class="detail-row"><strong>狀態</strong>：{enabled_text}</div>
  <div class="detail-row"><strong>主控表 ID</strong>：{mask_id(master_id)}</div>
  <div class="detail-row"><strong>共用雲端資料夾 ID</strong>：{mask_id(folder_id)}</div>
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

    if system_type in ["vip", "monthly_scheduler"]:
        if not period:
            add_log("請先輸入執行期別", "error")
            st.rerun()

    add_log(f"開始執行：{system_name} / {selected_function} / 期別 {period}")

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

                result = run_daily_scheduler(folder_id=folder_id)

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
                add_log("尚未設定共用雲端資料夾 ID", "error")
                st.rerun()

            if selected_function == "一鍵執行月排程":
                from tools.scheduled_monthly.scheduler import main as run_monthly_scheduler

                result = run_monthly_scheduler(folder_id=folder_id)

            else:
                cmd = MONTHLY_SCRIPT_MAP.get(selected_function)

                if not cmd:
                    raise RuntimeError(f"找不到月排程功能：{selected_function}")

                script = cmd[0]
                args = cmd[1:]

                result = run_script(script, [*args, "--folder-id", folder_id])

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
