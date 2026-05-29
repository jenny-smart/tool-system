from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from tools.common.log_to_sheet import write_job_log

TZ = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parents[2]

JOBS = {
    "schedule_stats": {
        "label": "外場排班統計表",
        "module": "tools.field_management.schedule_stats",
    },
    "staff_schedule": {
        "label": "外場專員班表",
        "module": "tools.field_management.staff_schedule",
    },
    "orders": {
        "label": "外場訂單",
        "module": "tools.field_management.orders",
    },
    "staff_profile": {
        "label": "外場專員個資",
        "module": "tools.field_management.staff_profile",
    },
}


def now_tw() -> datetime:
    return datetime.now(TZ)


def load_log_spreadsheet_id(system_name: str) -> str:
    """
    從 config/systems.yaml 讀取外場排程系統的 log_spreadsheet_id，
    並設進環境變數供 log_to_sheet.get_log_spreadsheet_id() 使用。
    """
    try:
        config_path = BASE_DIR / "config" / "systems.yaml"
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

        for sys_cfg in data.get("systems", []):
            if sys_cfg.get("name") == system_name:
                log_id = str(sys_cfg.get("log_spreadsheet_id", "")).strip()

                if log_id:
                    os.environ["TOOLS_APP_LOG_SPREADSHEET_ID"] = log_id
                    print(f"📋 log_spreadsheet_id 已從設定檔載入", flush=True)
                    return log_id
                else:
                    print(f"⚠️ systems.yaml 中 {system_name} 尚未設定 log_spreadsheet_id", flush=True)
                    return ""

        print(f"⚠️ systems.yaml 找不到系統：{system_name}", flush=True)
        return ""

    except Exception as e:
        print(f"⚠️ 讀取 log_spreadsheet_id 失敗：{e}", flush=True)
        return ""


def punch(label, status, started_at, finished_at=None, area="", date_key="", system_name="", message="", traceback_text=""):
    try:
        write_job_log(
            system_name="外場排程系統",
            job_name=label,
            status=status,
            started_at=started_at,
            finished_at=finished_at or "",
            message=message,
            area=area or "全區",
            period="",
            date=date_key,
            target=system_name,
            source_file="",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            traceback_text=traceback_text,
        )
        print(f"📝 外場排程打卡：{label} / {status}", flush=True)
    except Exception as exc:
        print(f"⚠️ 外場排程打卡失敗：{exc}", flush=True)


def run_job(job_name: str, date_key: str, area: str | None, system_name: str) -> dict:
    if job_name not in JOBS:
        raise RuntimeError(f"未知外場 job：{job_name}")

    job = JOBS[job_name]
    label = job["label"]

    cmd = [
        sys.executable,
        "-u",
        "-m",
        job["module"],
        "--date",
        date_key,
        "--system-name",
        system_name,
        "--run-type",                                        # ★ 透傳 run_type
        "排程" if os.getenv("GITHUB_ACTIONS") else "手動",
    ]

    if area:
        cmd.extend(["--area", area])

    started_at = now_tw()
    punch(label, "running", started_at, area=area or "全區", date_key=date_key, system_name=system_name, message="開始執行")

    print("Command:", " ".join(cmd), flush=True)

    completed = subprocess.run(cmd, cwd=BASE_DIR, text=True, capture_output=True)
    finished_at = now_tw()

    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""

    if stdout_text:
        print(stdout_text, flush=True)
    if stderr_text:
        print(stderr_text, flush=True)

    if completed.returncode != 0:
        message = f"{label} 執行失敗，exit={completed.returncode}\nSTDOUT:\n{stdout_text[-3000:]}\nSTDERR:\n{stderr_text[-5000:]}"
        punch(label, "failed", started_at, finished_at, area=area or "全區", date_key=date_key, system_name=system_name, message=message, traceback_text=stderr_text or message)
        raise RuntimeError(message)

    punch(label, "success", started_at, finished_at, area=area or "全區", date_key=date_key, system_name=system_name, message="完成")

    return {"job": job_name, "label": label, "status": "success"}


def today_yyyymmdd() -> str:
    return now_tw().strftime("%Y%m%d")


def main(target: str = "all", date_key: str | None = None, area: str | None = None, system_name: str = "外場日排程系統") -> list[dict]:
    date_key = date_key or today_yyyymmdd()

    # ★ 從 systems.yaml 讀取 log_spreadsheet_id 並設進環境變數
    load_log_spreadsheet_id(system_name)

    targets = list(JOBS.keys()) if target == "all" else [target]

    results = []
    failed = []

    for job_name in targets:
        try:
            results.append(run_job(job_name, date_key, area, system_name))
        except Exception as exc:
            failed.append({"job": job_name, "status": "failed", "message": str(exc)})
            if target != "all":
                raise

    if failed:
        raise RuntimeError(f"外場排程有失敗項目：{failed}")

    print("field_management scheduler 全部完成", flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="all", choices=["all", *JOBS.keys()])
    parser.add_argument("--date", default=today_yyyymmdd())
    parser.add_argument("--area", default="")
    parser.add_argument("--system-name", default="外場日排程系統")
    args = parser.parse_args()

    main(target=args.target, date_key=args.date, area=args.area or None, system_name=args.system_name)
