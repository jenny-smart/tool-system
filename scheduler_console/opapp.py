import os
import json
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timedelta, timezone

# 重要：一定要在 import dashboard_main 之前 patch performance_report。
# 目的只剩一個：確保 dashboard_main 按「更新資料」時一定會落檔。
# 不可以在這裡再呼叫 persist_dashboard_payload()，否則同一次更新會寫入兩次。
import performance_report as _performance_report

_ORIGINAL_GENERATE_SALES_REPORT = _performance_report.generate_sales_report

def _generate_sales_report_force_persist(*args, **kwargs):
    kwargs["persist_dashboard"] = True
    return _ORIGINAL_GENERATE_SALES_REPORT(*args, **kwargs)

_performance_report.generate_sales_report = _generate_sales_report_force_persist

from dashboard_main import render_page

st.set_page_config(
    page_title="Jenny 排程控制台",
    page_icon="🍋",
    layout="wide",
)

OPAPP_VERSION = "2026-05-04-force-persist-v1"

TZ_TAIPEI = timezone(timedelta(hours=8))

TOP_PAGES = [
    ("主控表",       "📋"),
    ("業績報表",     "💹"),
    ("上下半月訂單", "🧾"),
    ("手動執行",     "▶️"),
    ("Log 監控",    "📄"),
    ("輸出檔案",     "📂"),
    ("程式管理",     "⚙️"),
    ("排程設定",     "⏰"),
]

if "page" not in st.session_state:
    st.session_state.page = "主控表"

_VALID_PAGES = [label for label, _icon in TOP_PAGES]
if st.session_state.page not in _VALID_PAGES:
    st.session_state.page = "業績報表"

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

/* ─── Base ─── */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stApp"] {
    background: #f0f2f6 !important;
    font-family: 'DM Sans','PingFang TC','Noto Sans TC',sans-serif !important;
    color: #1e293b !important;
}
[data-testid="stHeader"],
[data-testid="stSidebar"] { display: none !important; }
.block-container { padding: 0 2.4rem 4rem !important; max-width: 1480px !important; }

/* ─── Topbar ─── */
.topbar {
    background: #fff;
    margin: 0 -2.4rem;
    padding: 0 28px;
    border-bottom: 1px solid #e8ecf0;
    position: sticky; top: 0; z-index: 999;
    box-shadow: 0 1px 8px rgba(15,23,42,.06);
}
.topbar-inner { display: flex; align-items: center; height: 64px; }
.topbar-brand { display: flex; align-items: center; gap: 9px; flex-shrink: 0; }
.topbar-logo  { font-size: 26px; line-height: 1; }
.topbar-name  { font-size: 20px; font-weight: 700; color: #0f172a; letter-spacing: -.01em; white-space: nowrap; }
.topbar-sep   { width: 1px; height: 20px; background: #dde2e8; margin: 0 18px; flex-shrink: 0; }
.topbar-clock { font-size: 12px; color: #64748b; font-weight: 500; margin-left: auto; font-variant-numeric: tabular-nums; }

/* ─── Nav strip ─── */
.nav-strip {
    background: #fff;
    margin: 0 -2.4rem;
    padding: 0 12px;
    border-bottom: 1px solid #e8ecf0;
}

/* ─── Nav buttons — ALWAYS override global button style ─── */
html body .nav-wrap div[data-testid="stButton"] > button,
html body .nav-wrap div[data-testid="stButton"] > button:focus,
html body .nav-wrap div[data-testid="stButton"] > button:active {
    height: 46px !important;
    padding: 0 16px !important;
    border-radius: 0 !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    background: transparent !important;
    color: #64748b !important;
    font-weight: 600 !important;
    font-size: 14px !important;
    box-shadow: none !important;
    white-space: nowrap !important;
    letter-spacing: 0 !important;
}
html body .nav-wrap div[data-testid="stButton"] > button:hover {
    color: #1e293b !important;
    background: #f4f6f9 !important;
    border-bottom: 2px solid transparent !important;
}
html body .nav-wrap.active div[data-testid="stButton"] > button {
    color: #2563eb !important;
    background: #eff6ff !important;
    border-bottom: 2px solid #2563eb !important;
}

/* ─── Page header ─── */
.page-header {
    padding: 22px 0 16px;
    border-bottom: 1px solid #e8ecf0;
    margin-bottom: 22px;
    display: flex; align-items: flex-end; gap: 12px;
}
.page-title    { font-size: 34px; font-weight: 700; color: #0f172a; line-height: 1; letter-spacing: -.02em; }
.page-subtitle { font-size: 14px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: #94a3b8; padding-bottom: 1px; }

/* ─── KPI cards ─── */
.kpi-row { display: flex; gap: 12px; margin-bottom: 22px; }
.kpi-card {
    flex: 1; background: #fff; border: 1px solid #e8ecf0; border-radius: 12px;
    padding: 16px 20px 14px; position: relative; overflow: hidden;
    box-shadow: 0 1px 3px rgba(15,23,42,.04), 0 3px 10px rgba(15,23,42,.04);
}
.kpi-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; border-radius: 12px 12px 0 0;
}
.kpi-card.blue::before  { background: linear-gradient(90deg,#2563eb,#60a5fa); }
.kpi-card.green::before { background: linear-gradient(90deg,#059669,#34d399); }
.kpi-card.amber::before { background: linear-gradient(90deg,#b45309,#fbbf24); }
.kpi-card.red::before   { background: linear-gradient(90deg,#dc2626,#f87171); }
.kpi-label { font-size: 9.5px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: #64748b; margin-bottom: 7px; }
.kpi-value { font-size: 34px; font-weight: 700; color: #0f172a; line-height: 1; letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.kpi-sub   { font-size: 11.5px; color: #64748b; font-weight: 500; margin-top: 5px; }

/* ─── Section card ─── */
.section-card {
    background: #fff; border: 1px solid #e8ecf0; border-radius: 12px;
    padding: 20px 22px 18px; margin-bottom: 16px;
    box-shadow: 0 1px 3px rgba(15,23,42,.04), 0 3px 10px rgba(15,23,42,.04);
}
.section-title {
    font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
    color: #2563eb; margin-bottom: 16px; padding-bottom: 12px;
    border-bottom: 1px solid #f1f5f9;
    display: flex; align-items: center; gap: 7px;
}

/* ─── Status badges ─── */
.badge {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 20px; white-space: nowrap;
}
.badge::before { content:''; width:5px; height:5px; border-radius:50%; flex-shrink:0; }
.b-green  { color:#065f46; background:#d1fae5; } .b-green::before  { background:#059669; }
.b-yellow { color:#78350f; background:#fef3c7; } .b-yellow::before { background:#d97706; }
.b-red    { color:#991b1b; background:#fee2e2; } .b-red::before    { background:#dc2626; }
.b-gray   { color:#475569; background:#f1f5f9; } .b-gray::before   { background:#94a3b8; }
.b-blue   { color:#1d4ed8; background:#dbeafe; } .b-blue::before   { background:#2563eb; }

/* ─── Run button ─── */
html body .run-btn div[data-testid="stButton"] > button {
    background: #1e293b !important; color: #f1f5f9 !important;
    border: none !important; border-radius: 6px !important;
    font-weight: 600 !important; font-size: 12px !important;
    padding: 3px 11px !important; height: 28px !important; min-height: 28px !important;
    box-shadow: none !important;
}
html body .run-btn div[data-testid="stButton"] > button:hover { background: #0f172a !important; }

/* ─── Save button ─── */
html body .save-btn div[data-testid="stButton"] > button {
    background: #f0f9ff !important; color: #0369a1 !important;
    border: 1px solid #bae6fd !important; border-radius: 6px !important;
    font-weight: 700 !important; font-size: 12px !important;
    padding: 2px 9px !important; height: 28px !important; min-height: 28px !important;
    box-shadow: none !important;
}
html body .save-btn div[data-testid="stButton"] > button:hover { background: #e0f2fe !important; }

/* ─── Inline task result (success/fail tag under each row) ─── */
.task-result-row {
    padding: 6px 4px 10px;
    border-bottom: 1px solid #f1f5f9;
    margin-bottom: 4px;
}
.task-result-ok   { background: #f0fdf4; border-radius: 8px; padding: 8px 14px; font-size: 12.5px; color: #166534; font-weight: 500; }
.task-result-fail { background: #fef2f2; border-radius: 8px; padding: 8px 14px; font-size: 12.5px; color: #991b1b; font-weight: 500; }

/* ─── Exec log panel ─── */
.exec-panel {
    background: #fff; border: 1px solid #e8ecf0; border-left: 3px solid #2563eb;
    border-radius: 10px; padding: 14px 18px; margin-top: 10px;
    box-shadow: 0 1px 3px rgba(15,23,42,.04);
}
.exec-panel.ok   { border-left-color: #059669; }
.exec-panel.fail { border-left-color: #dc2626; }
.exec-panel-title { font-size: 13px; font-weight: 700; color: #0f172a; margin-bottom: 8px; display:flex; align-items:center; gap:8px; }
.exec-label { font-size: 9.5px; font-weight: 700; letter-spacing:.1em; text-transform:uppercase; color:#94a3b8; margin: 10px 0 5px; }

/* ─── Log box ─── */
.log-box {
    background: #0d1117; border: 1px solid #1e2d3d; border-radius: 9px;
    padding: 12px 16px;
    font-family: 'DM Mono','Menlo',monospace; font-size: 12px;
    line-height: 1.75; white-space: pre-wrap; word-break: break-all;
    max-height: 380px; overflow: auto;
}
.log-err    { color: #f87171; display: block; }
.log-ok     { color: #4ade80; display: block; }
.log-warn   { color: #fbbf24; display: block; }
.log-info   { color: #60a5fa; display: block; }
.log-normal { color: #94a3b8; display: block; }
.log-meta   { font-size: 11px; color: #64748b; font-weight: 500; margin-bottom: 7px; }

/* ─── Next-run chip ─── */
.next-run {
    display: inline-flex; align-items: center; gap: 5px;
    background: #f8fafc; border: 1px solid #e8ecf0; border-radius: 7px;
    padding: 3px 9px; font-size: 11px; font-weight: 600; color: #475569;
}

/* ─── Command preview ─── */
.cmd-preview {
    background: #1e293b; color: #94a3b8; border-radius: 9px;
    padding: 10px 16px; font-family: 'DM Mono','Menlo',monospace; font-size: 12px;
    margin: 10px 0 14px; word-break: break-all;
}
.cmd-preview .cmd-hl { color: #60a5fa; }
.cmd-preview .cmd-arg { color: #a3e635; }
.cmd-preview .cmd-city { color: #fb923c; }

/* ─── Empty state ─── */
.empty-state {
    text-align: center; padding: 28px 20px; color: #94a3b8; font-size: 12.5px; font-weight: 500;
    background: #f8fafc; border-radius: 9px; border: 1px dashed #dde2e8;
}
.empty-state .icon { font-size: 26px; display: block; margin-bottom: 7px; }

/* ─── Date range chip ─── */
.date-chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px;
    padding: 5px 12px; font-size: 12.5px; font-weight: 600; color: #0369a1; margin: 8px 0 12px;
}

/* ─── Streamlit overrides ─── */
div[data-testid="stButton"] > button {
    background: #1e293b !important; color: #f8fafc !important; border: none !important;
    border-radius: 8px !important; font-weight: 600 !important; font-size: 13px !important;
    padding: 8px 18px !important; box-shadow: 0 1px 3px rgba(15,23,42,.12) !important;
}
div[data-testid="stButton"] > button:hover { background: #0f172a !important; }

div[data-testid="stSelectbox"] > div > div,
div[data-testid="stTextInput"] > div > div > input {
    background: #fff !important; border: 1px solid #d1d9e0 !important;
    border-radius: 8px !important; color: #1e293b !important; font-size: 13.5px !important;
}
div[data-testid="stSelectbox"] label,
div[data-testid="stTextInput"] label,
div[data-testid="stTextArea"] label,
div[data-testid="stRadio"] label {
    color: #374151 !important; font-size: 13px !important; font-weight: 600 !important;
}
div[data-testid="stTextArea"] textarea {
    border: 1px solid #d1d9e0 !important; border-radius: 8px !important;
    font-family: 'DM Mono',monospace !important; font-size: 12.5px !important;
    color: #1e293b !important; background: #fafafa !important;
}
div[data-testid="stMetric"] {
    background: #fff; border-radius: 11px; padding: 14px 16px;
    border: 1px solid #e8ecf0; box-shadow: 0 1px 3px rgba(15,23,42,.04);
}
div[data-testid="stMetric"] label { color: #475569 !important; font-size: 12px !important; font-weight: 600 !important; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #0f172a !important; font-size: 26px !important; font-weight: 700 !important; }
div[data-testid="stDataFrame"] { border-radius: 9px !important; overflow: hidden !important; border: 1px solid #e8ecf0 !important; }
div[data-testid="stAlert"] { border-radius: 9px !important; font-size: 13px !important; font-weight: 500 !important; }
.stCaption, div[data-testid="stCaption"] { color: #64748b !important; font-size: 11.5px !important; font-weight: 500 !important; }
div[data-testid="stCheckbox"] label { color: #374151 !important; font-size: 13px !important; font-weight: 500 !important; }
h3 { color: #0f172a !important; font-size: 22px !important; font-weight: 700 !important; }

/* ─── Friendly dropdown navigation ─── */
.mobile-nav-card {
    background: #ffffff;
    border: 1px solid #e8ecf0;
    border-radius: 14px;
    padding: 14px 16px;
    margin: 16px 0 20px;
    box-shadow: 0 1px 8px rgba(15,23,42,.06);
}
.mobile-nav-card label {
    font-size: 13px !important;
    font-weight: 700 !important;
    color: #334155 !important;
}
.mobile-nav-card div[data-testid="stSelectbox"] > div > div {
    min-height: 44px !important;
    font-size: 15px !important;
    border-radius: 12px !important;
    border: 1.5px solid #dbe3ea !important;
    background: #f8fafc !important;
}
.mobile-nav-hint {
    margin-top: 6px;
    font-size: 12px;
    color: #64748b;
    font-weight: 500;
}
@media (max-width: 768px) {
    .block-container { padding: 0 1rem 3rem !important; }
    .topbar { margin: 0 -1rem; padding: 0 16px; }
    .topbar-inner { height: 58px; }
    .topbar-name { font-size: 17px; }
    .topbar-logo { font-size: 23px; }
    .topbar-sep { display: none; }
    .topbar-clock { font-size: 11px; }
    .page-header { display: block; padding: 18px 0 14px; margin-bottom: 16px; }
    .page-title { font-size: 26px; line-height: 1.15; }
    .page-subtitle { font-size: 11px; margin-top: 6px; }
    .kpi-row { flex-direction: column; gap: 10px; }
    .section-card { padding: 16px 14px; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { font-size: 22px !important; }
}

</style>
""", unsafe_allow_html=True)

# ── Topbar ────────────────────────────────────────────────────────────────────
now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d  %H:%M")
st.markdown(
    f"""<div class="topbar">
      <div class="topbar-inner">
        <div class="topbar-brand">
          <span class="topbar-logo">🍋</span>
          <span class="topbar-name">Jenny 排程控制台</span>
        </div>
        <div class="topbar-sep"></div>
        <div class="topbar-clock">🕐 {now_str}</div>
      </div>
    </div>""",
    unsafe_allow_html=True,
)

# ── Navigation dropdown ───────────────────────────────────────────────────────
page_options = [f"{icon} {label}" for label, icon in TOP_PAGES]
page_label_map = {f"{icon} {label}": label for label, icon in TOP_PAGES}

current_option = next(
    f"{icon} {label}"
    for label, icon in TOP_PAGES
    if label == st.session_state.page
)

st.markdown('<div class="mobile-nav-card">', unsafe_allow_html=True)
selected_page_option = st.selectbox(
    "選擇功能頁面",
    options=page_options,
    index=page_options.index(current_option),
    key="page_selectbox",
)
st.markdown(
    '<div class="mobile-nav-hint">手機版使用下拉選單切換頁面，避免功能列擠成一排。</div>',
    unsafe_allow_html=True,
)
st.markdown("</div>", unsafe_allow_html=True)

selected_page = page_label_map[selected_page_option]
if selected_page != st.session_state.page:
    st.session_state.page = selected_page
    st.rerun()

# ── Render page ───────────────────────────────────────────────────────────────
DASHBOARD_DIR = os.path.join(".", "dashboard_data")
LATEST_DIR = os.path.join(DASHBOARD_DIR, "latest")
DAILY_HISTORY_DIR = os.path.join(DASHBOARD_DIR, "daily_overview_history")


def _read_csv_safe(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()




def _sort_report_rows_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Sort report rows strictly by the 日期 column, newest first.

    This fixes old rows whose id format differs from newer rows. The display
    order must never depend on id, because old ids such as 20260503010745 and
    new ids such as 20260506152607_本月 do not sort reliably together.
    """
    if df is None or df.empty:
        return df

    out = df.copy()

    if "日期" in out.columns:
        sort_dt = pd.to_datetime(out["日期"], errors="coerce")

        # If some very old rows have an unparseable 日期, recover datetime from id.
        if "id" in out.columns:
            id_text = out["id"].astype(str).str.extract(r"^(\d{14})", expand=False)
            id_dt = pd.to_datetime(id_text, format="%Y%m%d%H%M%S", errors="coerce")
            sort_dt = sort_dt.fillna(id_dt)

        out["_sort_dt"] = sort_dt

        # Repair old rows where 統計月份 was blank/None, using 日期 when possible.
        if "統計月份" in out.columns:
            month_text = out["_sort_dt"].dt.strftime("%Y/%m")
            old_month = out["統計月份"].astype(str).str.strip()
            missing_month = out["統計月份"].isna() | old_month.isin(["", "None", "nan", "NaT"])
            out.loc[missing_month, "統計月份"] = month_text[missing_month]

        # Strictly sort by actual datetime only. Do not use id as secondary sort.
        out = out.sort_values("_sort_dt", ascending=False, na_position="last")
        out = out.drop(columns=["_sort_dt"])

    return out.reset_index(drop=True)


def _format_report_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        col_text = str(col)
        if any(k in col_text for k in ["業績", "合計", "總業績", "加總"]):
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int).map(lambda x: f"{x:,}")
        if "佔比" in col_text:
            nums = pd.to_numeric(out[col], errors="coerce")
            out[col] = nums.map(lambda x: "" if pd.isna(x) else f"{x:.2%}")
    return out


def _delete_rows_from_csv(path: str, selected_ids) -> bool:
    if not selected_ids or not os.path.exists(path):
        return False

    df = _read_csv_safe(path)
    if df.empty or "id" not in df.columns:
        return False

    before = len(df)
    df = df[~df["id"].astype(str).isin([str(x) for x in selected_ids])].copy()
    if len(df) == before:
        return False

    df.to_csv(path, index=False, encoding="utf-8-sig")
    return True


def _show_deletable_csv_section(
    title: str,
    path: str,
    empty_msg: str,
    key_prefix: str,
    fallback_df: pd.DataFrame | None = None,
    source_note: str | None = None,
    display_df: pd.DataFrame | None = None,
):
    # 預設讀 CSV；當月/次月追蹤會傳入 display_df，確保畫面直接使用
    # latest/df4.csv 補出的最新列，不會因 CSV 寫入或快取問題顯示舊資料。
    df = display_df.copy() if display_df is not None else _read_csv_safe(path)
    df = _sort_report_rows_for_display(df)

    if df.empty:
        st.info(empty_msg)
        return

    if "id" in df.columns and os.path.exists(path):
        options = df["id"].astype(str).tolist()
        selected = st.multiselect(
            "勾選要刪除的紀錄",
            options=options,
            key=f"{key_prefix}_delete_ids",
        )
        if st.button("🗑️ 刪除勾選列", key=f"{key_prefix}_delete_btn", use_container_width=True):
            if _delete_rows_from_csv(path, selected):
                st.success(f"已刪除 {len(selected)} 筆紀錄")
                st.rerun()
            else:
                st.warning("沒有刪除任何資料，請先勾選紀錄。")

    st.dataframe(_format_report_df(df), use_container_width=True, hide_index=True)

def _show_csv_section(title: str, path: str, empty_msg: str):
    _show_deletable_csv_section(title, path, empty_msg, key_prefix=title.replace(" ", "_"))


def _get_latest_payload_time() -> datetime:
    """Return the update time shown by the dashboard.

    Prefer latest/meta.json updated_at so the monthly-tracking row uses the
    same timestamp as the blue "最新更新時間" banner. Fall back to df4.csv
    mtime when meta.json is unavailable.
    """
    meta_path = os.path.join(LATEST_DIR, "meta.json")
    try:
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            updated_at = str(meta.get("updated_at") or "").strip()
            if updated_at:
                return datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_TAIPEI)
    except Exception:
        pass

    df4_path = os.path.join(LATEST_DIR, "df4.csv")
    try:
        if os.path.exists(df4_path):
            return datetime.fromtimestamp(os.path.getmtime(df4_path), TZ_TAIPEI)
    except Exception:
        pass

    return datetime.now(TZ_TAIPEI)


def _get_latest_df4_mtime_ns() -> int:
    """Return latest df4.csv mtime in nanoseconds.

    This is the update event key. Every time the user presses 更新資料 and
    df4.csv is overwritten, this value changes, so the monthly tracking tabs
    can append one current-month row and one next-month row for that exact run.
    """
    df4_path = os.path.join(LATEST_DIR, "df4.csv")
    try:
        if os.path.exists(df4_path):
            return int(os.stat(df4_path).st_mtime_ns)
    except Exception:
        pass
    return 0


def _dt_from_ns(ns: int) -> datetime:
    if ns:
        return datetime.fromtimestamp(ns / 1_000_000_000, TZ_TAIPEI)
    return datetime.now(TZ_TAIPEI)

def _period_config(period_label: str, dt: datetime):
    if period_label == "次月":
        amount_col = "次月加總"
        ratio_col = "次月佔比"
        y, m = dt.year, dt.month
        stat_month = f"{y + 1}/01" if m == 12 else f"{y}/{m + 1:02d}"
    else:
        amount_col = "本月加總"
        ratio_col = "本月佔比"
        stat_month = dt.strftime("%Y/%m")
    return amount_col, ratio_col, stat_month


def _build_overview_from_df4(period_label: str, row_dt: datetime | None = None, run_key: str | None = None) -> pd.DataFrame:
    """Build one overview row from latest df4.csv using current/next logic."""
    df4 = _read_csv_safe(os.path.join(LATEST_DIR, "df4.csv"))
    cols = [
        "id", "來源", "統計月份", "日期",
        "台北業績", "台北佔比", "台中業績", "台中佔比",
        "桃園業績", "桃園佔比", "新竹業績", "新竹佔比",
        "高雄業績", "高雄佔比", "全區合計",
    ]
    if df4.empty:
        return pd.DataFrame(columns=cols)

    row_dt = row_dt or _get_latest_payload_time()
    amount_col, ratio_col, stat_month = _period_config(period_label, row_dt)

    def get_val(city: str, col: str):
        row = df4[df4["城市"].astype(str) == city]
        if row.empty or col not in df4.columns:
            return 0
        return row.iloc[0][col]

    source = "dashboard"
    try:
        meta_path = os.path.join(LATEST_DIR, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            source = str(meta.get("trigger") or source)
    except Exception:
        pass

    row = {
        "id": f"{run_key or row_dt.strftime('%Y%m%d%H%M%S%f')}_{period_label}",
        "來源": source,
        "統計月份": stat_month,
        "日期": row_dt.strftime("%Y/%m/%d %H:%M"),
        "台北業績": get_val("台北", amount_col),
        "台北佔比": get_val("台北", ratio_col),
        "台中業績": get_val("台中", amount_col),
        "台中佔比": get_val("台中", ratio_col),
        "桃園業績": get_val("桃園", amount_col),
        "桃園佔比": get_val("桃園", ratio_col),
        "新竹業績": get_val("新竹", amount_col),
        "新竹佔比": get_val("新竹", ratio_col),
        "高雄業績": get_val("高雄", amount_col),
        "高雄佔比": get_val("高雄", ratio_col),
        "全區合計": get_val("加總", amount_col),
    }
    return pd.DataFrame([row], columns=cols)


def _sync_period_csv_from_df4(path: str, period_label: str) -> pd.DataFrame:
    """Return monthly tracking with the latest df4 row guaranteed at top.

    The top table latest/df4.csv is the source of truth. This function first
    builds the exact row that should match the blue latest timestamp, then
    merges it with the existing historical CSV. The returned dataframe is used
    directly for display, so even if an old CSV was not updated by the report
    writer, the lower table still immediately matches the top summary.
    """
    row_dt = _get_latest_payload_time()
    run_key = row_dt.strftime("%Y%m%d%H%M%S")
    new_df = _build_overview_from_df4(period_label, row_dt=row_dt, run_key=run_key)

    if new_df.empty:
        return _read_csv_safe(path)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    old_df = _read_csv_safe(path)

    cols = list(new_df.columns)
    if old_df.empty:
        out = new_df.copy()
    else:
        for c in cols:
            if c not in old_df.columns:
                old_df[c] = ""
        old_df = old_df[cols].copy()

        new_id = str(new_df.iloc[0]["id"])
        # Remove only the same event id, then put the latest df4-derived row
        # at the top. This also repairs bad old rows with the same id.
        if "id" in old_df.columns:
            old_df = old_df[old_df["id"].astype(str) != new_id].copy()
        out = pd.concat([new_df, old_df], ignore_index=True)

    if "日期" in out.columns:
        out["_sort_dt"] = pd.to_datetime(out["日期"], errors="coerce")
        out = out.sort_values("_sort_dt", ascending=False, na_position="last").drop(columns=["_sort_dt"])
    out = out.reset_index(drop=True)

    # 只回傳給畫面使用，不寫回 CSV。
    # CSV 只能由 performance_report.generate_sales_report() 在更新資料/排程時寫入。
    return out

def _show_period_section(title: str, filename: str, period_label: str):
    # 只讀取 performance_report.py 已經寫好的 CSV。
    # 注意：畫面 render 不可以 append / 排序後寫回 CSV，否則 Streamlit rerun 會造成資料重複。
    path = os.path.join(LATEST_DIR, filename)
    _show_deletable_csv_section(
        title=title,
        path=path,
        empty_msg="目前沒有資料。請先按『更新資料』。",
        key_prefix=f"period_{period_label}",
        fallback_df=None,
        source_note=None,
        display_df=None,
    )

def _show_month_end_snapshot_tab():
    latest_path = os.path.join(LATEST_DIR, "month_end_summary.csv")
    history_path = os.path.join(DAILY_HISTORY_DIR, "month_end_summary.csv")

    latest_df = _read_csv_safe(latest_path)
    history_df = _read_csv_safe(history_path)

    if latest_df.empty and history_df.empty:
        st.info("目前還沒有月底快照。系統會在每月最後一天更新資料時，記錄當月與次月各區業績及總業績。")
        st.caption(f"目前找不到檔案：{latest_path} 或 {history_path}")
        return

    if not latest_df.empty:
        _show_deletable_csv_section(
            "最近一次月底快照",
            latest_path,
            "目前 latest 裡還沒有月底快照檔。",
            key_prefix="month_end_latest",
        )

    if not history_df.empty:
        _show_deletable_csv_section(
            "月底快照歷史",
            history_path,
            "目前還沒有月底快照歷史。",
            key_prefix="month_end_history",
        )



def _render_page_without_builtin_daily_overview():
    """Render the original performance page, but suppress blocks now owned here.

    This app renders one combined monthly-tracking area and places the email
    preview below it. Therefore the old standalone daily overview and the old
    email preview from dashboard_main are hidden during the original render.
    """
    original = {
        "markdown": st.markdown,
        "caption": st.caption,
        "multiselect": st.multiselect,
        "button": st.button,
        "dataframe": st.dataframe,
        "info": st.info,
        "warning": st.warning,
        "success": st.success,
        "write": st.write,
        "expander": st.expander,
        "components_html": components.html,
    }
    skipping = {"on": False}

    class _SkipContext:
        def __enter__(self):
            skipping["on"] = True
            return self
        def __exit__(self, exc_type, exc, tb):
            skipping["on"] = False
            return False

    def should_start_skip(args, kwargs):
        text = ""
        if args:
            text = str(args[0])
        elif "body" in kwargs:
            text = str(kwargs.get("body"))
        # dashboard_main's old standalone block. Once this starts, everything
        # after it in that render is skipped; this file renders the replacement.
        return "當月每日業績總覽" in text

    def patch_markdown(*args, **kwargs):
        if should_start_skip(args, kwargs):
            skipping["on"] = True
            return None
        if skipping["on"]:
            return None
        return original["markdown"](*args, **kwargs)

    def patch_expander(label, *args, **kwargs):
        if "信件預覽" in str(label):
            return _SkipContext()
        if skipping["on"]:
            return _SkipContext()
        return original["expander"](label, *args, **kwargs)

    def patch_components_html(*args, **kwargs):
        if skipping["on"]:
            return None
        return original["components_html"](*args, **kwargs)

    def patch_noop(name):
        def inner(*args, **kwargs):
            if skipping["on"]:
                if name == "multiselect":
                    return []
                if name == "button":
                    return False
                return None
            return original[name](*args, **kwargs)
        return inner

    try:
        st.markdown = patch_markdown
        st.caption = patch_noop("caption")
        st.multiselect = patch_noop("multiselect")
        st.button = patch_noop("button")
        st.dataframe = patch_noop("dataframe")
        st.info = patch_noop("info")
        st.warning = patch_noop("warning")
        st.success = patch_noop("success")
        st.write = patch_noop("write")
        st.expander = patch_expander
        components.html = patch_components_html
        render_page("業績報表")
    finally:
        st.markdown = original["markdown"]
        st.caption = original["caption"]
        st.multiselect = original["multiselect"]
        st.button = original["button"]
        st.dataframe = original["dataframe"]
        st.info = original["info"]
        st.warning = original["warning"]
        st.success = original["success"]
        st.write = original["write"]
        st.expander = original["expander"]
        components.html = original["components_html"]


def render_email_preview_section():
    html_path = os.path.join(LATEST_DIR, "email_preview.html")
    with st.expander("📧 信件預覽", expanded=False):
        if not os.path.exists(html_path):
            st.info("目前沒有信件預覽。請先按『更新資料』產生 email_preview.html。")
            return
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
            if not html.strip():
                st.info("信件預覽檔案是空的。請重新更新資料。")
                return
            components.html(html, height=520, scrolling=True)
        except Exception as e:
            st.warning(f"無法讀取信件預覽：{e}")

def render_monthly_tracking_tabs():
    # 取代 dashboard_main 原本單獨的「當月每日業績總覽」。
    # 三個區塊整併在同一個頁籤區，且各自保留刪除功能。
    st.markdown(
        '<div class="page-header"><div class="page-title">月度追蹤</div>'
        '<div class="page-subtitle">CURRENT / NEXT MONTH / SNAPSHOT</div></div>',
        unsafe_allow_html=True,
    )

    tab_current, tab_next, tab_snapshot = st.tabs(["當月每日業績", "次月每日業績", "月底快照"])

    with tab_current:
        st.caption("資料來源：上方各區月度摘要（df4.csv）的本月加總；每次『更新資料』會新增一筆，舊紀錄會保留，除非手動勾選刪除。")
        _show_period_section("當月每日業績總覽", "daily_df.csv", "本月")

    with tab_next:
        st.caption("資料來源：上方各區月度摘要（df4.csv）的次月加總；每次『更新資料』會新增一筆，舊紀錄會保留，除非手動勾選刪除。")
        _show_period_section("次月每日業績總覽", "next_month_daily_df.csv", "次月")

    with tab_snapshot:
        _show_month_end_snapshot_tab()


def render_performance_report_page():
    _render_page_without_builtin_daily_overview()
    render_monthly_tracking_tabs()
    render_email_preview_section()


if st.session_state.page == "業績報表":
    render_performance_report_page()
else:
    render_page(st.session_state.page)
