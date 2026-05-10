"""
dashboard_main.py — cloud version with GitHub Actions status.
Called by opapp.py via render_page().
"""

import json
import calendar
import subprocess
import sys
import os
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from paths import (
    PATH_CLEANER_DATA, PATH_CLEANER_SCHEDULE, PATH_HR,
    PATH_ORDER, PATH_REPORT, PATH_SCHEDULE, PATH_VIP,
)
from performance_report import (
    generate_sales_report,
    load_output_file_log,
    LATEST_DIR,
)

TZ_TAIPEI = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "cron.log"
PYTHON_CMD = sys.executable or "python3"

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

CITY_LIST = ["全部", "台北", "台中", "桃園", "新竹", "高雄"]

OUTPUT_DIRS = {
    "排班統計表": Path(PATH_SCHEDULE),
    "專員班表": Path(PATH_CLEANER_SCHEDULE),
    "專員個資": Path(PATH_CLEANER_DATA),
    "訂單資料": Path(PATH_ORDER),
    "業績報表": Path(PATH_REPORT),
    "預收": Path(PATH_VIP),
    "儲值金結算": Path(PATH_VIP),
    "儲值金預收": Path(PATH_VIP),
    "上下半月訂單": Path(PATH_HR),
    "已退款": Path(PATH_HR),
}

MAIN_REPORT_TASKS = [
    {
        "name": "排班統計表",
        "task_key": "schedule_report",
        "script": "schedule_report.py",
        "workflow_file": "nightly-reports.yml",
        "workflow_job_name": "run-nightly",
        "workflow_step_name": "Run schedule report",
        "schedule_text": "GitHub Actions / 每天 01:10（台北）",
        "cmd": f'cd "{BASE_DIR}" && "{PYTHON_CMD}" schedule_report.py',
    },
    {
        "name": "專員班表",
        "task_key": "staff_schedule",
        "script": "staff_schedule.py",
        "workflow_file": "nightly-reports.yml",
        "workflow_job_name": "run-nightly",
        "workflow_step_name": "Run staff schedule",
        "schedule_text": "GitHub Actions / 每天 01:10 後（台北）",
        "cmd": f'cd "{BASE_DIR}" && "{PYTHON_CMD}" staff_schedule.py',
    },
    {
        "name": "專員個資",
        "task_key": "staff_info",
        "script": "staff_info.py",
        "workflow_file": "nightly-reports.yml",
        "workflow_job_name": "run-nightly",
        "workflow_step_name": "Run staff info",
        "schedule_text": "GitHub Actions / 每天 01:10 後（台北）",
        "cmd": f'cd "{BASE_DIR}" && "{PYTHON_CMD}" staff_info.py',
    },
    {
        "name": "訂單資料",
        "task_key": "orders_report",
        "script": "orders_report.py",
        "workflow_file": "nightly-reports.yml",
        "workflow_job_name": "run-nightly",
        "workflow_step_name": "Run orders report",
        "schedule_text": "GitHub Actions / 每天 01:10 後（台北）",
        "cmd": f'cd "{BASE_DIR}" && "{PYTHON_CMD}" orders_report.py',
    },
    {
        "name": "業績報表",
        "task_key": "performance_report",
        "script": "performance_report.py",
        "workflow_file": "performance-report.yml",
        "workflow_job_name": "run-performance-report",
        "workflow_step_name": "Run performance report",
        "schedule_text": "GitHub Actions / 每天 00:00、08:00、18:00（台北）",
        "cmd": f'cd "{BASE_DIR}" && "{PYTHON_CMD}" performance_report.py dashboard false',
    },
]

MANUAL_TASKS = [{"name": t["name"], "cmd": t["cmd"]} for t in MAIN_REPORT_TASKS]


def now_taipei():
    return datetime.now(TZ_TAIPEI)


def run_shell(cmd):
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True, executable="/bin/bash")
    return p.returncode, p.stdout, p.stderr


def read_last_lines(path, n=200):
    if not path.exists():
        return "(尚無 log)"
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-n:])
    except Exception as e:
        return f"(讀取失敗) {e}"


def file_mtime(path):
    if not path or not path.exists():
        return "-"
    return datetime.fromtimestamp(path.stat().st_mtime, tz=TZ_TAIPEI).strftime("%m/%d %H:%M")


def file_size_str(path):
    if not path or not path.exists():
        return "-"
    s = path.stat().st_size
    if s < 1024:
        return f"{s} B"
    if s < 1024 * 1024:
        return f"{s/1024:.1f} KB"
    return f"{s/1024/1024:.1f} MB"


def find_latest_files(base_dir, limit=10):
    if not base_dir.exists():
        return []
    files = [p for p in base_dir.rglob("*") if p.is_file() and not p.name.startswith((".", "~$"))]
    return sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:limit]


def highlight_log(text):
    lines = []
    for line in text.splitlines():
        e = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if any(k in line for k in ["Traceback", "Error", "ERROR", "❌", "PermissionError", "FAILED"]):
            lines.append(f'<span class="log-err">{e}</span>')
        elif any(k in line for k in ["✅", "SUCCESS", "success", "完成", "Done"]):
            lines.append(f'<span class="log-ok">{e}</span>')
        elif any(k in line for k in ["WARNING", "warn", "⚠"]):
            lines.append(f'<span class="log-warn">{e}</span>')
        elif any(k in line for k in ["INFO", "開始", "Start"]):
            lines.append(f'<span class="log-info">{e}</span>')
        else:
            lines.append(f'<span class="log-normal">{e}</span>')
    return "\n".join(lines)


def load_sales_latest_payload():
    ld = Path(LATEST_DIR)
    p = {"df4": pd.DataFrame(), "daily_df": pd.DataFrame(), "meta": {}, "email_html": ""}
    for key, fname in [("df4", "df4.csv"), ("daily_df", "daily_df.csv")]:
        fp = ld / fname
        if fp.exists():
            try:
                p[key] = pd.read_csv(fp, encoding="utf-8-sig")
            except Exception as e:
                p[f"{key}_error"] = str(e)
    mp = ld / "meta.json"
    if mp.exists():
        try:
            p["meta"] = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            pass
    hp = ld / "email_preview.html"
    if hp.exists():
        p["email_html"] = hp.read_text(encoding="utf-8")
    return p


def _fmt_int(x):
    try:
        return f"{int(float(x)):,}"
    except Exception:
        return "—"


def _fmt_pct(x):
    try:
        return f"{float(x):.2%}"
    except Exception:
        return "—"


def _badge(label, cls):
    cls_map = {
        "green": "b-green",
        "yellow": "b-yellow",
        "red": "b-red",
        "gray": "b-gray",
        "blue": "b-blue",
    }
    return f'<span class="badge {cls_map.get(cls, "b-gray")}">{label}</span>'


def render_html_table(df, right_cols, pct_cols, int_cols):
    def _cell(val, col):
        if pd.isna(val) or str(val).strip() in ("", "nan"):
            return "—"
        if col in pct_cols:
            return _fmt_pct(val)
        if col in int_cols:
            return _fmt_int(val)
        return str(val)

    th_style = (
        "padding:10px 14px;font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;"
        "color:#64748b;border-bottom:2px solid #e8ecf0;white-space:nowrap;background:#fafafa;"
    )
    td_base = "padding:9px 14px;font-size:13px;color:#1e293b;border-bottom:1px solid #f1f5f9;white-space:nowrap;"
    td_right = td_base + "text-align:right;font-variant-numeric:tabular-nums;font-family:'DM Mono','Menlo',monospace;"
    td_left = td_base + "text-align:left;"

    ths = "".join(
        f'<th style="{th_style}text-align:{"right" if c in right_cols else "left"}">{c}</th>'
        for c in df.columns
    )
    rows = []
    for _, row in df.iterrows():
        tds = "".join(
            f'<td style="{td_right if c in right_cols else td_left}">{_cell(row[c], c)}</td>'
            for c in df.columns
        )
        rows.append(f"<tr>{tds}</tr>")

    return (
        f'<div style="overflow-x:auto;border:1px solid #e8ecf0;border-radius:10px;">'
        f'<table style="width:100%;border-collapse:collapse;background:#fff;">'
        f'<thead><tr>{ths}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _read_secret_path(path_list, default=None):
    try:
        cur = st.secrets
        for key in path_list:
            cur = cur[key]
        return cur
    except Exception:
        return default


def get_github_config():
    owner = _read_secret_path(["github", "owner"], os.getenv("GITHUB_OWNER", ""))
    repo = _read_secret_path(["github", "repo"], os.getenv("GITHUB_REPO", ""))
    token = _read_secret_path(["github", "token"], os.getenv("GITHUB_TOKEN", ""))
    branch = _read_secret_path(["github", "branch"], os.getenv("GITHUB_BRANCH", "main"))
    return owner, repo, token, branch


def get_github_headers(token: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def to_taipei_time(text: str):
    if not text:
        return ""
    try:
        s = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text


def fetch_latest_workflow_run(owner: str, repo: str, token: str, workflow_file: str, branch: str = "main"):
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs"
    params = {
        "per_page": 5,
        "branch": branch,
        "exclude_pull_requests": "true",
    }
    r = requests.get(url, headers=get_github_headers(token), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    runs = data.get("workflow_runs", [])
    return runs[0] if runs else None


def fetch_jobs_for_run(owner: str, repo: str, token: str, run_id: int):
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
    params = {"per_page": 100}
    r = requests.get(url, headers=get_github_headers(token), params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("jobs", [])


def _map_run_badge(status: str, conclusion: str):
    if status == "completed":
        if conclusion == "success":
            return ("成功", "green")
        if conclusion in ("failure", "timed_out", "cancelled", "action_required", "startup_failure"):
            return ("失敗", "red")
        if conclusion in ("skipped", "neutral"):
            return ("略過", "gray")
        return (conclusion or "完成", "gray")
    if status in ("queued", "requested", "waiting", "pending"):
        return ("排隊中", "yellow")
    if status == "in_progress":
        return ("執行中", "yellow")
    return ("未知", "gray")


def _step_status_from_job(job: dict, step_name: str):
    steps = job.get("steps", []) or []
    for step in steps:
        if step.get("name") == step_name:
            return {
                "status": step.get("status", ""),
                "conclusion": step.get("conclusion", ""),
                "started_at": step.get("started_at", ""),
                "completed_at": step.get("completed_at", ""),
                "number": step.get("number"),
            }
    return None


def fetch_github_task_statuses(tasks):
    owner, repo, token, branch = get_github_config()
    result = {
        "available": False,
        "reason": "",
        "task_status": {},
        "summary": {},
    }

    if not owner or not repo:
        result["reason"] = "未設定 github.owner / github.repo"
        return result
    if not token:
        result["reason"] = "未設定 github.token"
        return result

    result["available"] = True

    workflow_cache = {}
    job_cache = {}

    try:
        workflow_files = sorted(set(t["workflow_file"] for t in tasks))

        for wf in workflow_files:
            run = fetch_latest_workflow_run(owner, repo, token, wf, branch=branch)
            workflow_cache[wf] = run
            if run and run.get("id"):
                job_cache[run["id"]] = fetch_jobs_for_run(owner, repo, token, run["id"])
    except Exception as e:
        result["available"] = False
        result["reason"] = f"GitHub API 讀取失敗：{e}"
        return result

    for task in tasks:
        wf = task["workflow_file"]
        run = workflow_cache.get(wf)

        if not run:
            result["task_status"][task["task_key"]] = {
                "badge_text": "尚未執行",
                "badge_cls": "gray",
                "ran_at": "—",
                "actor": "",
                "run_url": "",
                "workflow_name": wf,
            }
            continue

        run_status = run.get("status", "")
        run_conclusion = run.get("conclusion", "")
        badge_text, badge_cls = _map_run_badge(run_status, run_conclusion)
        run_url = run.get("html_url", "")
        actor = ((run.get("triggering_actor") or {}).get("login")
                 or (run.get("actor") or {}).get("login")
                 or "")
        ran_at = to_taipei_time(run.get("created_at", ""))

        task_state = {
            "badge_text": badge_text,
            "badge_cls": badge_cls,
            "ran_at": ran_at or "—",
            "actor": actor,
            "run_url": run_url,
            "workflow_name": wf,
        }

        jobs = job_cache.get(run.get("id"), [])
        target_job = None
        for job in jobs:
            if job.get("name") == task["workflow_job_name"]:
                target_job = job
                break

        if target_job:
            step_state = _step_status_from_job(target_job, task["workflow_step_name"])
            if step_state:
                s_badge_text, s_badge_cls = _map_run_badge(step_state.get("status", ""), step_state.get("conclusion", ""))
                ran_time = to_taipei_time(step_state.get("started_at") or target_job.get("started_at") or run.get("created_at", ""))
                task_state["badge_text"] = s_badge_text
                task_state["badge_cls"] = s_badge_cls
                task_state["ran_at"] = ran_time or task_state["ran_at"]

        result["task_status"][task["task_key"]] = task_state

    return result


def halfmonth_derive_dates(mode, yyyymm, half, start_date, end_date):
    try:
        if mode == "自訂區間":
            s = start_date.strftime("%Y%m%d")
            e = end_date.strftime("%Y%m%d")
            return f"{s}{e}", f"{s}-{e}", start_date.strftime("%Y/%m/%d"), end_date.strftime("%Y/%m/%d")
        if len(yyyymm) != 6 or not yyyymm.isdigit():
            return "", "格式錯誤", "", ""
        yr, mo = int(yyyymm[:4]), int(yyyymm[4:])
        if half == "1":
            s, e = date(yr, mo, 1), date(yr, mo, 15)
        else:
            last = calendar.monthrange(yr, mo)[1]
            s, e = date(yr, mo, 16), date(yr, mo, last)
        tag = f"{yyyymm}-{half}"
        return tag, tag, s.strftime("%Y/%m/%d"), e.strftime("%Y/%m/%d")
    except Exception:
        return "", "日期錯誤", "", ""


def halfmonth_build_cmd(period_arg, city):
    base = f'cd "{BASE_DIR}" && "{PYTHON_CMD}" "上下半月訂單.py" {period_arg}'
    if city != "全部":
        base += f" {city}"
    return base


def render_main_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">排程主控表</div>'
        '<div class="page-subtitle">GitHub Actions Dashboard</div></div>',
        unsafe_allow_html=True,
    )

    if "task_results" not in st.session_state:
        st.session_state.task_results = {}
    if "latest_run_key" not in st.session_state:
        st.session_state.latest_run_key = None

    github_status = fetch_github_task_statuses(MAIN_REPORT_TASKS)

    total_tasks = len(MAIN_REPORT_TASKS)
    manual_count = sum(1 for v in st.session_state.task_results.values() if v)
    github_success = 0
    github_fail = 0
    if github_status["available"]:
        for item in github_status["task_status"].values():
            if item["badge_cls"] == "green":
                github_success += 1
            elif item["badge_cls"] == "red":
                github_fail += 1

    st.markdown(
        f"""<div class="kpi-row">
          <div class="kpi-card blue"><div class="kpi-label">Total</div><div class="kpi-value">{total_tasks}</div><div class="kpi-sub">可手動執行任務</div></div>
          <div class="kpi-card green"><div class="kpi-label">GitHub Success</div><div class="kpi-value">{github_success}</div><div class="kpi-sub">最近一次成功數</div></div>
          <div class="kpi-card {"red" if github_fail > 0 else "amber"}"><div class="kpi-label">GitHub Fail</div><div class="kpi-value">{github_fail}</div><div class="kpi-sub">最近一次失敗數</div></div>
          <div class="kpi-card blue"><div class="kpi-label">Manual Runs</div><div class="kpi-value">{manual_count}</div><div class="kpi-sub">本次 session 已手動執行</div></div>
        </div>""",
        unsafe_allow_html=True,
    )

    if github_status["available"]:
        st.info("主控表狀態已改為讀取 GitHub Actions。最近結果與最近執行時間會依 workflow 最新 run 自動更新。")
    else:
        st.warning(f"GitHub 狀態未啟用：{github_status['reason']}")

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📋 報表任務</div>', unsafe_allow_html=True)
    st.markdown(
        """<div style="display:grid;grid-template-columns:1.6fr 1.4fr 1fr 1.1fr .7fr .5fr;
          gap:0;padding:0 4px 9px;border-bottom:1px solid #e8ecf0;margin-bottom:2px;">
          <span style="font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#94a3b8;">任務 / 腳本</span>
          <span style="font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#94a3b8;">排程方式</span>
          <span style="font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#94a3b8;">最近結果</span>
          <span style="font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#94a3b8;">最近執行</span>
          <span style="font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#94a3b8;">觸發者</span>
          <span style="font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#94a3b8;text-align:center;">▶</span>
        </div>""",
        unsafe_allow_html=True,
    )

    for task in MAIN_REPORT_TASKS:
        gh_item = github_status["task_status"].get(task["task_key"], {}) if github_status["available"] else {}
        badge_text = gh_item.get("badge_text", "尚未執行")
        badge_cls = gh_item.get("badge_cls", "gray")
        ran_at = gh_item.get("ran_at", "—")
        actor = gh_item.get("actor", "")
        run_url = gh_item.get("run_url", "")

        c1, c2, c3, c4, c5, c6 = st.columns([1.6, 1.4, 1.0, 1.1, .7, .5])

        with c1:
            st.markdown(
                f"<span style='font-weight:700;color:#0f172a;font-size:13px'>{task['name']}</span><br>"
                f"<span style='font-size:10.5px;color:#94a3b8;font-family:monospace'>{task['script']}</span>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<span style='font-size:12px;color:#1e293b;font-weight:600'>{task['schedule_text']}</span>",
                unsafe_allow_html=True,
            )
        with c3:
            if run_url:
                st.markdown(f'<a href="{run_url}" target="_blank">{_badge(badge_text, badge_cls)}</a>', unsafe_allow_html=True)
            else:
                st.markdown(_badge(badge_text, badge_cls), unsafe_allow_html=True)
        with c4:
            st.markdown(
                f"<span style='font-size:12px;color:#64748b'>{ran_at}</span>",
                unsafe_allow_html=True,
            )
        with c5:
            st.markdown(
                f"<span style='font-size:12px;color:#64748b'>{actor or '—'}</span>",
                unsafe_allow_html=True,
            )
        with c6:
            st.markdown('<div class="run-btn">', unsafe_allow_html=True)
            if st.button("▶", key=f'run_{task["task_key"]}', help=f'執行 {task["name"]}'):
                with st.spinner(f"執行中：{task['name']}…"):
                    rc, out, err = run_shell(task["cmd"])
                st.session_state.task_results[task["task_key"]] = {
                    "name": task["name"],
                    "script": task["script"],
                    "code": rc,
                    "stdout": out,
                    "stderr": err,
                    "ran_at": now_taipei().strftime("%Y-%m-%d %H:%M:%S"),
                }
                st.session_state.latest_run_key = task["task_key"]
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:3px'></div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

    latest_run_key = st.session_state.get("latest_run_key")
    latest_result = st.session_state.task_results.get(latest_run_key) if latest_run_key else None
    if latest_result:
        st.markdown('<div class="section-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">🖥 最近一次手動執行結果</div>', unsafe_allow_html=True)

        cls = "ok" if latest_result["code"] == 0 else "fail"
        st.markdown(
            f'<div class="exec-panel {cls}">'
            f'<div class="exec-panel-title">▶ {latest_result["name"]}'
            f'&emsp;<span style="font-size:12px;color:#94a3b8">{latest_result["ran_at"]}</span>'
            f'&emsp;{_badge("✓ 成功","green") if latest_result["code"] == 0 else _badge("✗ 失敗","red")}'
            f'</div>',
            unsafe_allow_html=True,
        )

        if latest_result["stdout"].strip():
            st.markdown('<div class="exec-label">STDOUT</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="log-box">{highlight_log(latest_result["stdout"])}</div>', unsafe_allow_html=True)

        if latest_result["stderr"].strip():
            st.markdown('<div class="exec-label">STDERR</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="log-box">{highlight_log(latest_result["stderr"])}</div>', unsafe_allow_html=True)

        if not latest_result["stdout"].strip() and not latest_result["stderr"].strip():
            st.markdown('<div class="log-box"><span class="log-normal">(無輸出)</span></div>', unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


def render_sales_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">業績報表</div>'
        '<div class="page-subtitle">Latest Data · Send Later</div></div>',
        unsafe_allow_html=True
    )

    result = None
    c1, c2, c3 = st.columns([1, 1, 1.5])
    with c1:
        update_btn = st.button("🔄 更新資料", use_container_width=True)
    with c2:
        send_btn = st.button("📧 寄送目前結果", use_container_width=True)
    with c3:
        if st.button("📂 重新讀取已存資料", use_container_width=True):
            st.rerun()

    if update_btn:
        with st.spinner("更新資料中…"):
            result = generate_sales_report(
                send_email=False,
                persist_dashboard=False,
                trigger="dashboard"
            )

    if result is not None:
        df4 = result.get("df4", pd.DataFrame())
        daily_df = result.get("daily_df", pd.DataFrame())
        email_html = result.get("email_html", "")
        updated_at = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
        error_msg = result.get("error")
    else:
        payload = load_sales_latest_payload()
        df4 = payload.get("df4", pd.DataFrame())
        daily_df = payload.get("daily_df", pd.DataFrame())
        meta = payload.get("meta", {})
        email_html = payload.get("email_html", "")
        raw_ts = meta.get("updated_at", "") if isinstance(meta, dict) else ""
        updated_at = raw_ts if raw_ts else "尚未產生資料"
        error_msg = meta.get("error") if isinstance(meta, dict) else None

        if payload.get("df4_error"):
            st.warning(f"df4.csv 讀取錯誤：{payload['df4_error']}")
        if payload.get("daily_df_error"):
            st.warning(f"daily_df.csv 讀取錯誤：{payload['daily_df_error']}")

    if send_btn:
        if df4.empty:
            st.warning("目前沒有可寄送資料，請先更新資料")
        else:
            try:
                from performance_report import send_region4_email
                send_region4_email(df4)
                st.success("寄信完成")
            except Exception as e:
                st.error(f"寄信失敗：{e}")

    if error_msg:
        st.error(f"上次執行有錯誤：{error_msg}")

    st.info(f"📅 最新更新時間：{updated_at}")

    total = None
    if not df4.empty:
        t = df4[df4["城市"] == "加總"]
        if not t.empty:
            total = t.iloc[0]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("本月加總", _fmt_int(total.get("本月加總", 0)) if total is not None else "—")
    k2.metric("次月加總", _fmt_int(total.get("次月加總", 0)) if total is not None else "—")
    k3.metric("本月家電加總", _fmt_int(total.get("本月家電加總", 0)) if total is not None else "—")
    k4.metric("儲值金", _fmt_int(total.get("儲值金", 0)) if total is not None else "—")

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📊 各區月度摘要</div>', unsafe_allow_html=True)
    if df4.empty:
        st.markdown(
            '<div class="empty-state"><span class="icon">📭</span>目前沒有資料，請先按「更新資料」</div>',
            unsafe_allow_html=True
        )
    else:
        int4 = {"本月加總", "次月加總", "本月家電加總", "次月家電加總", "儲值金"}
        pct4 = {"本月佔比", "次月佔比"}
        st.markdown(
            render_html_table(df4, right_cols=int4 | pct4, pct_cols=pct4, int_cols=int4),
            unsafe_allow_html=True
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📅 當月每日業績總覽</div>', unsafe_allow_html=True)

    df4_csv = Path(LATEST_DIR) / "df4.csv"
    daily_csv = Path(LATEST_DIR) / "daily_df.csv"

    parts = [
        f"daily_df.csv {'存在' if daily_csv.exists() else '⚠️ 不存在'}（{file_size_str(daily_csv)}，{file_mtime(daily_csv)}）",
        f"載入：{len(daily_df)} 行 × {len(daily_df.columns)} 欄"
    ]
    if not daily_df.empty:
        parts.append(
            f"欄位：{', '.join(daily_df.columns[:8].tolist())}{'…' if len(daily_df.columns) > 8 else ''}"
        )

    st.caption("  ·  ".join(parts))

    if daily_df.empty:
        reason = "daily_df.csv 不存在，請先按「更新資料」。" if not daily_csv.exists() else "CSV 存在但無資料列。"
        st.markdown(
            f'<div class="empty-state"><span class="icon">📭</span>{reason}</div>',
            unsafe_allow_html=True
        )
    else:
        if "id" in daily_df.columns:
            del_ids = st.multiselect(
                "勾選要刪除的每日業績總覽紀錄",
                options=daily_df["id"].astype(str).tolist(),
                key="del_daily_df_ids"
            )

            if st.button("🗑 刪除勾選列", key="del_daily_df_btn", use_container_width=True):
                keep_df = daily_df[~daily_df["id"].astype(str).isin([str(x) for x in del_ids])].copy()
                keep_df.to_csv(daily_csv, index=False, encoding="utf-8-sig")
                st.success(f"已刪除 {len(daily_df) - len(keep_df)} 筆")
                st.rerun()

        show_cols = [
            "id", "來源", "日期",
            "台北業績", "台北佔比",
            "台中業績", "台中佔比",
            "桃園業績", "桃園佔比",
            "新竹業績", "新竹佔比",
            "高雄業績", "高雄佔比",
            "全區合計"
        ]
        show_cols = [c for c in show_cols if c in daily_df.columns]

        int_d = {c for c in show_cols if "業績" in c or c == "全區合計"}
        pct_d = {c for c in show_cols if "佔比" in c}

        st.markdown(
            render_html_table(
                daily_df[show_cols].copy(),
                right_cols=int_d | pct_d,
                pct_cols=pct_d,
                int_cols=int_d
            ),
            unsafe_allow_html=True
        )

    st.markdown("</div>", unsafe_allow_html=True)

    if email_html:
        with st.expander("📧 信件預覽"):
            st.components.v1.html(email_html, height=520, scrolling=True)


def render_halfmonth_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">上下半月訂單</div>'
        '<div class="page-subtitle">Half-Month Orders · Google Drive Upload</div></div>',
        unsafe_allow_html=True
    )

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📅 期間設定</div>', unsafe_allow_html=True)

    mode = st.radio(
        "模式",
        ["上半月 (YYYYMM-1)", "下半月 (YYYYMM-2)", "自訂區間 (YYYYMMDD-YYYYMMDD)"],
        horizontal=True,
        label_visibility="collapsed"
    )

    today = now_taipei().date()
    start_date = end_date = None
    yyyymm = ""
    half = ""

    if mode.startswith("上半月") or mode.startswith("下半月"):
        half = "1" if mode.startswith("上") else "2"
        col_a, col_b = st.columns([1, 2])
        with col_a:
            yyyymm = st.text_input("月份 YYYYMM", value=today.strftime("%Y%m"), key="hm_yyyymm", placeholder="例：202604")
        with col_b:
            st.markdown("<br>", unsafe_allow_html=True)
            if len(yyyymm) == 6 and yyyymm.isdigit():
                try:
                    yr, mo = int(yyyymm[:4]), int(yyyymm[4:])
                    if half == "1":
                        s_d, e_d = f"{yr}/{mo:02d}/01", f"{yr}/{mo:02d}/15"
                    else:
                        last = calendar.monthrange(yr, mo)[1]
                        s_d, e_d = f"{yr}/{mo:02d}/16", f"{yr}/{mo:02d}/{last:02d}"
                    st.markdown(f'<div class="date-chip">📆 {s_d} &nbsp;～&nbsp; {e_d}</div>', unsafe_allow_html=True)
                except Exception:
                    st.warning("月份格式錯誤")
            else:
                st.caption("請輸入 6 位數月份，例如 202604")
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            start_date = st.date_input("開始日期", value=today.replace(day=1), key="hm_start")
        with col_b:
            end_date = st.date_input("結束日期", value=today, key="hm_end")

    city = st.selectbox("區域", CITY_LIST, key="hm_city")

    period_arg, label, s_disp, e_disp = halfmonth_derive_dates(
        "自訂區間" if mode.startswith("自訂") else "半月",
        yyyymm, half, start_date, end_date
    )
    cmd = halfmonth_build_cmd(period_arg, city) if period_arg else ""

    if cmd:
        city_txt = city if city != "全部" else "全部城市"
        st.markdown(
            f'<div class="cmd-preview">'
            f'<span class="cmd-hl">python3</span> "上下半月訂單.py" '
            f'<span class="cmd-arg">{period_arg}</span>'
            f'{" <span class=\'cmd-city\'>" + city + "</span>" if city != "全部" else ""}'
            f'<br><span style="color:#64748b;font-size:11px">→ 跑 {city_txt} &nbsp; {s_disp} ～ {e_disp}</span>'
            f'</div>',
            unsafe_allow_html=True
        )
    else:
        st.warning("請確認輸入值再執行")

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">▶ 執行</div>', unsafe_allow_html=True)

    c_run, c_note = st.columns([1, 3])
    with c_run:
        run_clicked = st.button("📥 開始下載上傳", use_container_width=True, disabled=not bool(cmd))
    with c_note:
        st.caption(f"目標：{city}  ·  {label}  ·  執行後會登入後台並上傳至 Google Drive")

    if run_clicked and cmd:
        with st.spinner(f"執行中：{label} {city}…"):
            rc, out, err = run_shell(cmd)
        st.session_state["hm_last_result"] = {
            "code": rc,
            "stdout": out,
            "stderr": err,
            "ran_at": now_taipei().strftime("%Y-%m-%d %H:%M:%S"),
            "label": label,
            "city": city
        }
        st.rerun()

    r = st.session_state.get("hm_last_result")
    if r:
        rc = r["code"]
        cls = "ok" if rc == 0 else "fail"
        st.markdown(
            f'<div class="exec-panel {cls}">'
            f'<div class="exec-panel-title">▶ {r["label"]} {r["city"]}'
            f'&emsp;<span style="font-size:12px;color:#94a3b8">{r["ran_at"]}</span>'
            f'&emsp;{_badge("✓ 成功","green") if rc == 0 else _badge("✗ 失敗","red")}'
            f'</div>',
            unsafe_allow_html=True
        )
        if rc != 0 and r["stderr"].strip():
            st.markdown('<div class="exec-label">STDERR（失敗原因）</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="log-box">{highlight_log(r["stderr"])}</div>', unsafe_allow_html=True)
        if r["stdout"].strip():
            with st.expander("完整 STDOUT"):
                st.markdown(f'<div class="log-box">{highlight_log(r["stdout"])}</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def render_manual_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">手動執行</div>'
        '<div class="page-subtitle">Manual Trigger</div></div>',
        unsafe_allow_html=True
    )
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">▶ 選擇任務執行</div>', unsafe_allow_html=True)
    selected = st.selectbox("選擇任務", MANUAL_TASKS, format_func=lambda x: x["name"])
    if st.button("▶ 執行", use_container_width=True):
        with st.spinner("執行中…"):
            rc, out, err = run_shell(selected["cmd"])
        st.markdown(f"回傳碼：{_badge(f'exit {rc}', 'green' if rc == 0 else 'red')}", unsafe_allow_html=True)
        if out.strip():
            st.markdown('<div class="exec-label">STDOUT</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="log-box">{highlight_log(out)}</div>', unsafe_allow_html=True)
        if err.strip():
            st.markdown('<div class="exec-label">STDERR</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="log-box">{highlight_log(err)}</div>', unsafe_allow_html=True)
        if not out.strip() and not err.strip():
            st.markdown('<div class="log-box"><span class="log-normal">(無輸出)</span></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def render_log_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">Log 監控</div>'
        '<div class="page-subtitle">Cloud Log Monitor</div></div>',
        unsafe_allow_html=True
    )
    st.info("雲端版不使用 launchd stderr log。這裡只保留 app 目錄下可讀取的 log 檔。")

    log_choices = {"主 log（cron.log）": LOG_FILE}
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        sel_log = st.selectbox("選擇 log 檔", list(log_choices.keys()))
    with c2:
        n_lines = st.selectbox("顯示行數", [50, 100, 200, 500], index=1)
    with c3:
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()

    log_path = log_choices[sel_log]
    st.markdown(f'<div class="log-meta">📄 {log_path}&emsp;·&emsp;更新：{file_mtime(log_path)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="log-box">{highlight_log(read_last_lines(log_path, n_lines))}</div>', unsafe_allow_html=True)


def render_output_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">輸出檔案監控</div>'
        '<div class="page-subtitle">Output Files</div></div>',
        unsafe_allow_html=True
    )

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">📦 各分類最新輸出</div>', unsafe_allow_html=True)

    rows = []
    for name, out_dir in OUTPUT_DIRS.items():
        files = find_latest_files(out_dir, limit=1)
        latest = files[0] if files else None
        rows.append({
            "分類": name,
            "最新檔案": latest.name if latest else "(無)",
            "狀態": "今日完成" if latest and file_mtime(latest).startswith(now_taipei().strftime("%m/%d")) else "未更新",
            "大小": file_size_str(latest),
            "完成時間": file_mtime(latest),
            "資料夾": str(out_dir),
            "完整路徑": str(latest) if latest else "-"
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">🧾 輸出檔案路徑紀錄</div>', unsafe_allow_html=True)

    output_log_df = load_output_file_log()

    if output_log_df.empty:
        st.markdown('<div class="empty-state"><span class="icon">📭</span>目前沒有輸出檔案紀錄</div>', unsafe_allow_html=True)
    else:
        keyword = st.text_input("搜尋檔名 / 路徑", value="", key="output_log_keyword", placeholder="例如：台中、daily_df、/Users/jenny/")
        category = st.selectbox("查看哪個資料夾", ["全部"] + list(OUTPUT_DIRS.keys()), key="output_log_category")

        view_df = output_log_df.copy()

        if category != "全部" and "分類" in view_df.columns:
            view_df = view_df[view_df["分類"] == category]

        if keyword.strip():
            kw = keyword.strip()
            mask = (
                view_df["檔名"].astype(str).str.contains(kw, case=False, na=False) |
                view_df["完整路徑"].astype(str).str.contains(kw, case=False, na=False)
            )
            view_df = view_df[mask]

        st.dataframe(view_df, use_container_width=True, hide_index=True)

    st.markdown("</div>", unsafe_allow_html=True)


def render_code_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">程式管理</div>'
        '<div class="page-subtitle">Code Management</div></div>',
        unsafe_allow_html=True
    )
    st.info("雲端版保留此頁。")


def render_schedule_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">排程設定</div>'
        '<div class="page-subtitle">GitHub Actions Schedule</div></div>',
        unsafe_allow_html=True
    )

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">⏰ 排程說明</div>', unsafe_allow_html=True)
    st.info(
        "雲端版不使用 Mac launchd / plist。\n\n"
        "自動排程由 GitHub Actions 執行：\n"
        "- Nightly Reports：每天 01:10（台北）\n"
        "- Performance Report：每天 08:00（台北）\n\n"
        "主控表最近結果與最近執行時間會直接讀 GitHub Actions。"
    )
    st.code(
        "Nightly Reports:\n"
        "  - cron: '10 17 * * *'  # 台北 01:10\n\n"
        "Performance Report:\n"
        "  - cron: '0 0 * * *'    # 台北 08:00",
        language="yaml",
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_page(page):
    dispatch = {
        "主控表": render_main_page,
        "業績報表": render_sales_page,
        "上下半月訂單": render_halfmonth_page,
        "手動執行": render_manual_page,
        "Log 監控": render_log_page,
        "輸出檔案": render_output_page,
        "程式管理": render_code_page,
        "排程設定": render_schedule_page,
    }
    dispatch.get(page, render_main_page)()
    st.markdown(
        f'<div class="footer-cap">Lemon Clean Scheduler Console &nbsp;·&nbsp; '
        f'{now_taipei().strftime("%Y-%m-%d %H:%M:%S")} (台北)</div>',
        unsafe_allow_html=True
    )
