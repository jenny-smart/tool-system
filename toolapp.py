"""
Tool App 主控入口
動態系統版：
- 從 config/systems.yaml 讀取系統與功能
- 支援手動執行 script
- 支援業績報表頁：?view=report
- 支援排程 Log 頁：?view=log
"""

from __future__ import annotations

import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml


TW_TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parent

st.set_page_config(
    page_title="Tools App",
    page_icon="🧰",
    layout="centered",
)


# ═══════════════════════════════════════════════════════════
# 基礎工具
# ═══════════════════════════════════════════════════════════

def tw_now() -> datetime:
    return datetime.now(TW_TZ)


def tw_now_text(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return tw_now().strftime(fmt)


def init_state() -> None:
    defaults = {
        "logs": [],
        "view": "main",
        "last_result": None,
        "selected_system_name": "",
        "selected_function_name": "",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def add_log(message: str, level: str = "info") -> None:
    icons = {
        "info": "🔵",
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
    }
    icon = icons.get(level, "🔵")
    st.session_state.logs.append(f"[{tw_now_text('%H:%M:%S')}] {icon} {message}")

    if len(st.session_state.logs) > 500:
        st.session_state.logs = st.session_state.logs[-500:]


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

    if view in ["main", "report", "log"]:
        st.session_state.view = view


def mask_id(value: str) -> str:
    value = str(value or "")
    if len(value) <= 10:
        return value
    return value[:6] + "..." + value[-4:]


def run_script(script: str, args: list[str] | None = None) -> str:
    args = args or []
    script_path = BASE_DIR / script

    if not script_path.exists():
        raise RuntimeError(f"找不到執行檔：{script}")

    cmd = [sys.executable, str(script_path), *args]

    add_log(f"執行指令：{' '.join(cmd)}", "info")

    result = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    if stdout:
        for line in stdout.splitlines()[-80:]:
            add_log(line, "info")

    if stderr:
        for line in stderr.splitlines()[-80:]:
            add_log(line, "error")

    if result.returncode != 0:
        raise RuntimeError(
            f"執行失敗：{script}\n"
            f"exit={result.returncode}\n"
            f"STDOUT:\n{stdout}\n"
            f"STDERR:\n{stderr}"
        )

    return stdout.strip() or "執行完成"


# ═══════════════════════════════════════════════════════════
# 設定讀取
# ═══════════════════════════════════════════════════════════

def load_config() -> dict:
    config_path = BASE_DIR / "config" / "systems.yaml"

    if not config_path.exists():
        return {
            "systems": [
                {
                    "name": "尚未設定系統",
                    "type": "empty",
                    "enabled": True,
                    "functions": [],
                }
            ]
        }

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    if "systems" not in data:
        data["systems"] = []

    return data


def get_enabled_systems(config: dict) -> list[dict]:
    return [
        system
        for system in config.get("systems", [])
        if system.get("enabled", True)
    ]


def get_system_by_name(config: dict, name: str) -> dict | None:
    for system in get_enabled_systems(config):
        if system.get("name") == name:
            return system
    return None


def get_functions(system: dict) -> list[dict]:
    functions = system.get("functions", [])

    # 相容舊版：如果 systems.yaml 沒寫 functions，就依 type 提供預設功能
    if functions:
        return functions

    system_type = system.get("type")

    defaults = {
        "field_daily_schedule": [
            {
                "name": "排班統計表",
                "script": "tools/field_management/schedule_stats.py",
            },
            {
                "name": "專員班表",
                "script": "tools/field_management/staff_schedule.py",
            },
            {
                "name": "當月次月訂單",
                "script": "tools/field_management/orders.py",
            },
            {
                "name": "專員個資",
                "script": "tools/field_management/staff_profile.py",
            },
            {
                "name": "一鍵執行外場日排程",
                "script": "tools/field_management/scheduler.py",
                "args": ["--target", "all"],
            },
        ],
        "scheduled_daily": [
            {
                "name": "排班統計表",
                "script": "tools/scheduled_daily/schedule_report.py",
            },
            {
                "name": "專員班表",
                "script": "tools/scheduled_daily/staff_schedule.py",
            },
            {
                "name": "當月次月訂單",
                "script": "tools/scheduled_daily/orders_report.py",
            },
            {
                "name": "專員個資",
                "script": "tools/scheduled_daily/staff_info.py",
            },
            {
                "name": "業績報表",
                "script": "tools/scheduled_daily/performance_report.py",
            },
        ],
    }

    return defaults.get(system_type, [])


def build_args(system: dict, function: dict, period: str) -> list[str]:
    args: list[str] = []

    function_args = function.get("args", [])
    if function_args:
        args.extend([str(x) for x in function_args])

    # 通用支援：若 script 是 field_management，補 system-name
    script = function.get("script", "")
    if "tools/field_management/" in script:
        args.extend(["--system-name", system.get("name", "外場日排程系統")])

        if period:
            # period 可輸入 20260511 或 202605
            if len(period) == 8 and period.isdigit():
                args.extend(["--date", period])

    # 舊日排程：如果有 root folder id，傳 --folder-id；業績報表不傳
    if system.get("type") == "scheduled_daily" and function.get("name") != "業績報表":
        folder_id = (
            system.get("folder_id")
            or system.get("root_folder_id")
            or system.get("folder_ids", {}).get("root")
        )
        if folder_id:
            args.extend(["--folder-id", str(folder_id)])

    return args


# ═══════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════

def inject_style() -> None:
    st.markdown(
        """
        <style>
          .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
            max-width: 1100px;
          }

          .app-title {
            font-size: 2.35rem;
            font-weight: 900;
            color: #0f172a;
            margin-bottom: 0.35rem;
            letter-spacing: 1px;
          }

          .app-subtitle {
            color: #94a3b8;
            font-size: 0.86rem;
            font-weight: 800;
            letter-spacing: 5px;
            margin-bottom: 1.4rem;
          }

          .card {
            background: linear-gradient(135deg, #ffffff, #f8fafc);
            border: 1px solid #e2e8f0;
            border-radius: 22px;
            padding: 24px;
            margin: 18px 0 24px 0;
            box-shadow: 0 8px 26px rgba(15, 23, 42, 0.06);
          }

          .card-title {
            font-size: 1.35rem;
            font-weight: 900;
            color: #0f172a;
            margin-bottom: 0.75rem;
          }

          .muted {
            color: #64748b;
            font-size: 0.9rem;
          }

          .small-muted {
            color: #94a3b8;
            font-size: 0.82rem;
          }

          .log-box {
            background: #0f172a;
            color: #e2e8f0;
            border-radius: 16px;
            padding: 16px;
            max-height: 430px;
            overflow-y: auto;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.86rem;
            line-height: 1.55;
          }

          .report-table-wrap {
            overflow-x: auto;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            background: white;
          }

          table.report-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.95rem;
          }

          .report-table th {
            background: #f8fafc;
            color: #64748b;
            font-weight: 800;
            text-align: center;
            padding: 13px 14px;
            border-bottom: 1px solid #e2e8f0;
            border-right: 1px solid #e2e8f0;
            white-space: nowrap;
          }

          .report-table td {
            padding: 12px 14px;
            border-bottom: 1px solid #edf2f7;
            border-right: 1px solid #edf2f7;
            color: #1e293b;
            background: white;
            white-space: nowrap;
          }

          .report-table td.num {
            text-align: right !important;
            font-variant-numeric: tabular-nums;
          }

          .report-table td.text {
            text-align: left;
          }

          .report-table tr.total-row td {
            background: #f8fafc;
            font-weight: 900;
          }

          .report-table tr:hover td {
            background: #f1f5f9;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════
# 共用畫面元件
# ═══════════════════════════════════════════════════════════

def render_log_panel() -> None:
    st.markdown('<div class="card"><div class="card-title">📜 執行日誌</div>', unsafe_allow_html=True)

    if not st.session_state.logs:
        st.info("尚無執行紀錄")
    else:
        content = "<br>".join(html.escape(line) for line in reversed(st.session_state.logs[-160:]))
        st.markdown(f'<div class="log-box">{content}</div>', unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


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
        elif any(k in col_text for k in ["業績", "加總", "家電", "儲值金", "金額", "總額"]):
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
# 業績報表頁
# ═══════════════════════════════════════════════════════════

def render_report() -> None:
    latest_dir = BASE_DIR / "dashboard_data" / "latest"

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
        <div class="app-title">📊 業績報表</div>
        <div class="app-subtitle">REPORT DASHBOARD · LATEST DATA</div>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns([1, 1, 1, 1])

    with cols[0]:
        if st.button("← 返回主控台", use_container_width=True):
            set_view("main")
            st.rerun()

    with cols[1]:
        if st.button("🔄 更新業績報表", use_container_width=True):
            try:
                add_log("開始更新業績報表", "info")
                run_script("tools/scheduled_daily/performance_report.py", ["dashboard"])
                add_log("業績報表更新完成", "success")
                st.rerun()
            except Exception as e:
                add_log(f"業績報表更新失敗：{e}", "error")
                st.error(e)

    with cols[2]:
        if st.button("📂 重新讀取", use_container_width=True):
            st.rerun()

    with cols[3]:
        st.link_button("🔗 獨立連結", "?view=report", use_container_width=True)

    if not df4_path.exists():
        st.info("尚未產生業績報表資料。請按「更新業績報表」，或確認 GitHub Actions 已將 dashboard_data/latest commit 回 GitHub。")
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
        st.warning("業績報表資料為空。")
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
# Log 頁
# ═══════════════════════════════════════════════════════════

def render_log_page() -> None:
    job_labels = {
        "schedule_report": "排班統計表",
        "staff_schedule": "專員班表",
        "orders_report": "當月次月訂單",
        "staff_info": "專員個資",
        "send_daily_result": "通知信",
        "performance_report": "業績報表",
        "schedule_stats": "外場排班統計表",
        "orders": "外場訂單",
        "staff_profile": "外場專員個資",
    }

    def read_text(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def status_for(code: str) -> tuple[str, str, str]:
        if code == "0":
            return "成功", "✅", "#16a34a"
        if code == "missing":
            return "尚無紀錄", "⚪", "#64748b"
        return "失敗", "❌", "#dc2626"

    def summary(content: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return "沒有 log 內容"

        keywords = [
            "全部完成",
            "執行完成",
            "完成",
            "失敗",
            "錯誤",
            "RuntimeError",
            "Traceback",
            "ModuleNotFoundError",
        ]

        for keyword in keywords:
            for line in reversed(lines):
                if keyword in line:
                    return line[-140:]

        return lines[-1][-140:]

    st.markdown(
        """
        <div class="app-title">📋 排程執行狀態</div>
        <div class="app-subtitle">SYSTEM STATUS · DAILY JOBS · ACTION LOGS</div>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns([1, 1, 2])

    with cols[0]:
        if st.button("← 返回主控台", use_container_width=True):
            set_view("main")
            st.rerun()

    with cols[1]:
        if st.button("🔄 重新讀取", use_container_width=True):
            st.rerun()

    log_root = BASE_DIR / "logs"

    if not log_root.exists():
        st.info("目前沒有 logs/ 資料夾。請確認 workflow 已將 logs/ commit 回 GitHub。")
        return

    day_dirs = sorted([p for p in log_root.iterdir() if p.is_dir()], reverse=True)

    if not day_dirs:
        st.info("logs/ 裡沒有日期資料夾。")
        return

    selected_day = st.selectbox("選擇日期", [p.name for p in day_dirs])
    selected_dir = log_root / selected_day
    log_files = sorted(selected_dir.glob("*.log"))

    if not log_files:
        st.info(f"{selected_day} 沒有 log 檔。")
        return

    rows = []

    for log_file in log_files:
        job = log_file.stem
        exit_file = log_file.with_suffix(".exit")
        code = read_text(exit_file).strip() if exit_file.exists() else "missing"
        status_text, icon, color = status_for(code)
        content = read_text(log_file)

        rows.append(
            {
                "job": job,
                "label": job_labels.get(job, job),
                "code": code,
                "status": status_text,
                "icon": icon,
                "color": color,
                "content": content,
                "summary": summary(content),
            }
        )

    success_count = sum(1 for r in rows if r["code"] == "0")
    failed_count = sum(1 for r in rows if r["code"] not in ["0", "missing"])
    missing_count = sum(1 for r in rows if r["code"] == "missing")

    st.markdown(
        f"""
        <div class="card">
          <div class="card-title">📅 {html.escape(selected_day)} 執行總覽</div>
          <div class="muted">以執行結果為主，詳細 log 放在下方展開查看。</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;">
            <div style="background:#f1f5f9;border-radius:999px;padding:8px 13px;font-weight:800;">✅ 成功：{success_count}</div>
            <div style="background:#f1f5f9;border-radius:999px;padding:8px 13px;font-weight:800;">❌ 失敗：{failed_count}</div>
            <div style="background:#f1f5f9;border-radius:999px;padding:8px 13px;font-weight:800;">⚪ 尚無紀錄：{missing_count}</div>
            <div style="background:#f1f5f9;border-radius:999px;padding:8px 13px;font-weight:800;">📄 Log 檔：{len(log_files)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cards = [
        """
        <style>
          .status-grid {
            display:grid;
            grid-template-columns:repeat(3,minmax(0,1fr));
            gap:14px;
            margin-top:14px;
          }
          .status-card {
            background:white;
            border:1px solid #e2e8f0;
            border-left:7px solid var(--status-color);
            border-radius:18px;
            padding:18px;
            box-shadow:0 4px 14px rgba(15,23,42,0.04);
          }
          .status-name {
            font-size:1.05rem;
            font-weight:900;
            color:#0f172a;
            margin-bottom:8px;
          }
          .status-badge {
            display:inline-flex;
            align-items:center;
            gap:6px;
            color:var(--status-color);
            background:#f8fafc;
            border:1px solid #e2e8f0;
            border-radius:999px;
            padding:5px 10px;
            font-size:0.86rem;
            font-weight:800;
          }
          .status-summary {
            color:#64748b;
            font-size:0.86rem;
            margin-top:12px;
            line-height:1.45;
            min-height:38px;
          }
          .status-meta {
            color:#94a3b8;
            font-size:0.78rem;
            margin-top:10px;
            font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
          }
          @media (max-width: 900px) {
            .status-grid { grid-template-columns:1fr; }
          }
        </style>
        <div class="status-grid">
        """
    ]

    for row in rows:
        cards.append(
            f"""
            <div class="status-card" style="--status-color:{row['color']};">
              <div class="status-name">{html.escape(row['label'])}</div>
              <div class="status-badge">{row['icon']} {html.escape(row['status'])}</div>
              <div class="status-summary">{html.escape(row['summary'])}</div>
              <div class="status-meta">exit code: {html.escape(row['code'])}</div>
            </div>
            """
        )

    cards.append("</div>")

    st.markdown("".join(cards), unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🔎 詳細 Log")

    tabs = st.tabs([f"{r['icon']} {r['label']}" for r in rows])

    for tab, row in zip(tabs, rows):
        with tab:
            if row["code"] == "0":
                st.success(f"{row['label']}：成功")
            elif row["code"] == "missing":
                st.warning(f"{row['label']}：尚無 exit code")
            else:
                st.error(f"{row['label']}：失敗 / exit code {row['code']}")

            st.code(row["content"] or "空 log", language="text")


# ═══════════════════════════════════════════════════════════
# 主控台
# ═══════════════════════════════════════════════════════════

def render_main(config: dict) -> None:
    st.markdown(
        """
        <div class="app-title">🧰 Tools App 作業系統</div>
        <div class="app-subtitle">AUTOMATION CONTROL CENTER</div>
        """,
        unsafe_allow_html=True,
    )

    systems = get_enabled_systems(config)

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

    if not systems:
        st.warning("目前沒有啟用的系統，請檢查 config/systems.yaml")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    system_names = [s.get("name", "未命名系統") for s in systems]

    default_index = 0
    if st.session_state.selected_system_name in system_names:
        default_index = system_names.index(st.session_state.selected_system_name)

    system_name = st.selectbox(
        "選擇系統",
        system_names,
        index=default_index,
    )

    st.session_state.selected_system_name = system_name

    selected_system = get_system_by_name(config, system_name) or systems[0]
    functions = get_functions(selected_system)

    if not functions:
        st.info("此系統尚未設定 functions。")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    function_names = [fn.get("name", "未命名功能") for fn in functions]

    default_fn_index = 0
    if st.session_state.selected_function_name in function_names:
        default_fn_index = function_names.index(st.session_state.selected_function_name)

    function_name = st.selectbox(
        "選擇功能",
        function_names,
        index=default_fn_index,
    )

    st.session_state.selected_function_name = function_name

    selected_function = next(fn for fn in functions if fn.get("name") == function_name)

    period = st.text_input(
        "執行日期 / 期別",
        value=tw_now().strftime("%Y%m%d"),
        help="日排程建議輸入 YYYYMMDD，例如 20260511；月排程可輸入 YYYYMM。",
    )

    with st.expander("目前系統設定", expanded=False):
        st.json(
            {
                "name": selected_system.get("name"),
                "type": selected_system.get("type"),
                "enabled": selected_system.get("enabled", True),
                "script": selected_function.get("script"),
                "args": build_args(selected_system, selected_function, period),
                "folder_ids": selected_system.get("folder_ids", {}),
                "spreadsheet_ids": {
                    k: {
                        area: mask_id(value)
                        for area, value in v.items()
                    }
                    for k, v in selected_system.get("spreadsheet_ids", {}).items()
                    if isinstance(v, dict)
                },
            }
        )

    c1, c2 = st.columns([1, 1])

    with c1:
        run_clicked = st.button("▶️ 執行", type="primary", use_container_width=True)

    with c2:
        clear_clicked = st.button("🧹 清除畫面日誌", use_container_width=True)

    if clear_clicked:
        st.session_state.logs = []
        st.rerun()

    if run_clicked:
        script = selected_function.get("script")

        if not script:
            st.error("此功能未設定 script")
        else:
            try:
                add_log(f"開始執行：{system_name} / {function_name} / {period}", "info")
                args = build_args(selected_system, selected_function, period)

                result = run_script(script, args)

                st.session_state.last_result = result
                add_log("執行完成", "success")
                st.success("執行完成")

            except Exception as e:
                add_log(f"執行失敗：{e}", "error")
                st.error(e)

    st.markdown("</div>", unsafe_allow_html=True)

    render_log_panel()


# ═══════════════════════════════════════════════════════════
# App Entry
# ═══════════════════════════════════════════════════════════

init_state()
sync_view_from_query_params()
inject_style()

config = load_config()

if st.session_state.view == "report":
    render_report()
    st.stop()

if st.session_state.view == "log":
    render_log_page()
    st.stop()

render_main(config)
