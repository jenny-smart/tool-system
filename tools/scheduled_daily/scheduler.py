from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

TZ = timezone(timedelta(hours=8))

try:
    from tools.common.log_to_sheet import write_job_log as _write_job_log
except Exception:
    _write_job_log = None

try:
    from tools.common.log_to_sheet import log_to_sheet as _log_to_sheet
except Exception:
    _log_to_sheet = None

JOBS = {
    "schedule_report": {"label": "排班統計表", "script": "tools/scheduled_daily/schedule_report.py", "needs_folder_id": True},
    "staff_schedule": {"label": "專員班表", "script": "tools/scheduled_daily/staff_schedule.py", "needs_folder_id": True},
    "orders_report": {"label": "當月次月訂單", "script": "tools/scheduled_daily/orders_report.py", "needs_folder_id": True},
    "staff_info": {"label": "專員個資", "script": "tools/scheduled_daily/staff_info.py", "needs_folder_id": True},
}


def now_tw() -> datetime:
    return datetime.now(TZ)


def format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")


def status_to_sheet_text(status: str) -> str:
    if status in ["running", "執行中"]:
        return "執行中"
    if status in ["success", "成功"]:
        return "成功"
    return "失敗"


def current_run_type() -> str:
    return "排程" if os.getenv("GITHUB_EVENT_NAME", "").strip() == "schedule" else "手動"


def _plain_secret(value):
    try:
        if hasattr(value, "to_dict"):
            return value.to_dict()
    except Exception:
        pass
    try:
        if hasattr(value, "items") and not isinstance(value, str):
            return {str(k): _plain_secret(v) for k, v in value.items()}
    except Exception:
        pass
    return value


def build_child_env() -> dict[str, str]:
    env = os.environ.copy()

    base_str = str(BASE_DIR)
    existing_pp = env.get("PYTHONPATH", "")
    pp_parts = [p for p in existing_pp.split(os.pathsep) if p]
    if base_str not in pp_parts:
        env["PYTHONPATH"] = base_str + (os.pathsep + existing_pp if existing_pp else "")

    try:
        import streamlit as st

        try:
            import toml
            secrets_dict = {str(k): _plain_secret(v) for k, v in st.secrets.items()}
            env["STREAMLIT_SECRETS_TOML"] = toml.dumps(secrets_dict)
        except Exception as exc:
            print(f"⚠️ 一鍵日排程 secrets TOML 注入失敗：{exc}", flush=True)

        try:
            for key in st.secrets:
                try:
                    value = st.secrets[key]
                    plain = _plain_secret(value)
                    if isinstance(plain, str):
                        env[str(key)] = plain
                    elif isinstance(plain, dict):
                        env[str(key)] = json.dumps(plain, ensure_ascii=False)
                except Exception:
                    pass
        except Exception as exc:
            print(f"⚠️ 一鍵日排程 secrets env 注入失敗：{exc}", flush=True)

    except Exception as exc:
        print(f"⚠️ 一鍵日排程無法讀取 Streamlit Secrets：{exc}", flush=True)

    env["DAILY_SCHEDULER_MANAGED"] = "1"

    oauth_keys = (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    )
    missing = [key for key in oauth_keys if not env.get(key, "").strip()]
    if missing:
        raise RuntimeError("一鍵日排程子程序缺少 OAuth Secret：" + ", ".join(missing))

    return env


def punch(*, label: str, job_name: str, status: str, started_at: datetime,
          finished_at: datetime | None = None, folder_id: str = "", message: str = "",
          traceback_text: str = "") -> None:
    status_text = status_to_sheet_text(status)
    finished_text = format_dt(finished_at) if finished_at else ""
    msg = (f"開始時間：{format_dt(started_at)}｜完成時間：{finished_text}｜"
           f"folder_id：{folder_id}｜{message}")
    try:
        if _write_job_log is not None:
            _write_job_log(
                system_name="日排程系統", job_name=label, status=status,
                started_at=started_at, finished_at=finished_at or "", message=message,
                area="全區", period="", date=now_tw().strftime("%Y%m%d"),
                target=folder_id, source_file="", run_type=current_run_type(),
                traceback_text=traceback_text,
            )
            return
        if _log_to_sheet is not None:
            _log_to_sheet(
                system="daily", function=label, run_type=current_run_type(), area="全區", period="",
                date=now_tw().strftime("%Y%m%d"), target=folder_id, source_file="",
                status=status_text, message=msg, traceback_text=traceback_text,
            )
            return
        print("⚠️ 找不到 tools.common.log_to_sheet，略過打卡", flush=True)
    except Exception as exc:
        print(f"⚠️ 寫入日排程打卡失敗：{exc}", flush=True)


def build_child_command(script: Path, folder_id: str) -> list[str]:
    """強制在子程序中重新載入 sitecustomize，再執行真正的日排程腳本。

    這可確保 OAuth secrets 已進入 child env 後，service_account.Credentials
    一定會被 Jenny OAuth credentials 接管，不再依賴 Python 是否自動載入
    sitecustomize.py。
    """
    bootstrap = (
        "import importlib, runpy, sys; "
        "import sitecustomize; "
        "importlib.reload(sitecustomize); "
        "script = sys.argv[1]; "
        "sys.argv = sys.argv[1:]; "
        "runpy.run_path(script, run_name='__main__')"
    )
    return [
        sys.executable,
        "-u",
        "-c",
        bootstrap,
        str(script),
        "--folder-id",
        folder_id,
    ]


def run_job(job_name: str, folder_id: str = "") -> dict:
    if job_name not in JOBS:
        raise RuntimeError(f"未知 job：{job_name}")

    job = JOBS[job_name]
    label = job["label"]
    script = BASE_DIR / job["script"]
    if not script.exists():
        raise RuntimeError(f"{label} 找不到執行檔：{script}")
    if job.get("needs_folder_id") and not folder_id:
        raise RuntimeError(f"{label} 缺少 folder_id")

    started_at = now_tw()
    punch(
        label=label,
        job_name=job_name,
        status="running",
        started_at=started_at,
        folder_id=folder_id,
        message="開始執行",
    )

    print(f"開始執行：{label}", flush=True)

    try:
        child_env = build_child_env()
    except Exception as exc:
        finished_at = now_tw()
        error_message = f"{label} 啟動前失敗：{exc}"
        punch(
            label=label,
            job_name=job_name,
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            folder_id=folder_id,
            message=error_message,
            traceback_text=error_message,
        )
        raise RuntimeError(error_message) from exc

    cmd = build_child_command(script, folder_id)
    print("OAuth env ready = True", flush=True)
    print("OAuth bootstrap forced = True", flush=True)
    print("Command: Python bootstrap ->", str(script), flush=True)

    completed = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        env=child_env,
    )

    finished_at = now_tw()
    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""

    if stdout_text:
        print(stdout_text, flush=True)
    if stderr_text:
        print(stderr_text, flush=True)

    if completed.returncode != 0:
        error_message = (
            f"{label} 執行失敗，exit={completed.returncode}\n\n"
            f"STDOUT:\n{stdout_text[:3000]}\n\n"
            f"STDERR:\n{stderr_text[:5000]}"
        )
        punch(
            label=label,
            job_name=job_name,
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            folder_id=folder_id,
            message=error_message,
            traceback_text=stderr_text or error_message,
        )
        raise RuntimeError(error_message)

    punch(
        label=label,
        job_name=job_name,
        status="success",
        started_at=started_at,
        finished_at=finished_at,
        folder_id=folder_id,
        message="完成",
    )

    print(f"完成執行：{label}", flush=True)
    return {
        "job": job_name,
        "label": label,
        "status": "success",
        "started_at": format_dt(started_at),
        "finished_at": format_dt(finished_at),
    }


def main(target: str = "all", folder_id: str = "") -> list[dict]:
    targets = list(JOBS.keys()) if target == "all" else [target]
    results = []
    failed = []

    for job_name in targets:
        try:
            results.append(run_job(job_name, folder_id=folder_id))
        except Exception as exc:
            failed.append({"job": job_name, "status": "failed", "message": str(exc)})
            if target != "all":
                raise

    if failed:
        raise RuntimeError(f"日排程有失敗項目：{failed}")

    print("scheduled_daily scheduler 全部完成", flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="all", choices=["all", *JOBS.keys()])
    parser.add_argument("--folder-id", default=os.getenv("DAILY_ROOT_FOLDER_ID", ""))
    args = parser.parse_args()
    main(target=args.target, folder_id=args.folder_id)
