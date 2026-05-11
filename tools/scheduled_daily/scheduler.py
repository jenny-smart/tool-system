from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from datetime import datetime

from tools.shared.log_to_sheet import write_job_log

BASE_DIR = Path(__file__).resolve().parents[2]

JOBS = {
    "schedule_report": {
        "label": "排班統計表",
        "script": "tools/scheduled_daily/schedule_report.py",
        "needs_folder_id": True,
    },
    "staff_schedule": {
        "label": "專員班表",
        "script": "tools/scheduled_daily/staff_schedule.py",
        "needs_folder_id": True,
    },
    "orders_report": {
        "label": "當月次月訂單",
        "script": "tools/scheduled_daily/orders_report.py",
        "needs_folder_id": True,
    },
    "staff_info": {
        "label": "專員個資",
        "script": "tools/scheduled_daily/staff_info.py",
        "needs_folder_id": True,
    },
}


def run_job(job_name: str, folder_id: str = "") -> dict:

    if job_name not in JOBS:
        raise RuntimeError(f"未知 job：{job_name}")

    job = JOBS[job_name]

    label = job["label"]

    script = BASE_DIR / job["script"]

    if not script.exists():
        raise RuntimeError(f"{label} 找不到執行檔：{script}")

    cmd = [sys.executable, "-u", str(script)]

    if job.get("needs_folder_id"):

        if not folder_id:
            raise RuntimeError(f"{label} 缺少 folder_id")

        cmd.extend(["--folder-id", folder_id])

    print(f"開始執行：{label}", flush=True)

    print("Command:", " ".join(cmd), flush=True)

    start_time = datetime.now()

    completed = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
    )

    finish_time = datetime.now()

    stdout_text = completed.stdout or ""

    stderr_text = completed.stderr or ""

    if stdout_text:
        print(stdout_text, flush=True)

    if stderr_text:
        print(stderr_text, flush=True)

    if completed.returncode != 0:

        error_message = (
            f"{label} 執行失敗，exit={completed.returncode}\n\n"
            f"STDERR:\n{stderr_text[:5000]}"
        )

        # ========= 打卡失敗 =========
        try:
            write_job_log(
                system_name="日排程系統",
                job_name=label,
                status="failed",
                started_at=start_time,
                finished_at=finish_time,
                message=error_message,
            )
        except Exception as log_err:
            print(f"寫入打卡表失敗：{log_err}", flush=True)

        raise RuntimeError(error_message)

    # ========= 打卡成功 =========
    try:
        write_job_log(
            system_name="日排程系統",
            job_name=label,
            status="success",
            started_at=start_time,
            finished_at=finish_time,
            message="完成",
        )
    except Exception as log_err:
        print(f"寫入打卡表失敗：{log_err}", flush=True)

    print(f"完成執行：{label}", flush=True)

    return {
        "job": job_name,
        "label": label,
        "status": "success",
    }


def main(
    target: str = "all",
    folder_id: str = "",
) -> list[dict]:

    targets = list(JOBS.keys()) if target == "all" else [target]

    results = []

    for job_name in targets:

        results.append(
            run_job(
                job_name,
                folder_id=folder_id,
            )
        )

    print("scheduled_daily scheduler 全部完成", flush=True)

    return results


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--target",
        default="all",
        choices=["all", *JOBS.keys()],
    )

    parser.add_argument(
        "--folder-id",
        default="",
    )

    args = parser.parse_args()

    main(
        target=args.target,
        folder_id=args.folder_id,
    )
