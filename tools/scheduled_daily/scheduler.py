from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools.common.log_to_sheet import write_job_log

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


def punch(
    *,
    label: str,
    status: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    message: str = "",
    traceback_text: str = "",
):
    write_job_log(
        system_name="日排程系統",
        job_name=label,
        status=status,
        started_at=started_at,
        finished_at=finished_at or "",
        message=message,
        area="全區",
        period="",
        date=datetime.now().strftime("%Y%m%d"),
        target="",
        source_file="",
        run_type="手動",
        traceback_text=traceback_text,
    )


def run_job(job_name: str, folder_id: str = "") -> dict:
    job = JOBS[job_name]
    label = job["label"]
    script = BASE_DIR / job["script"]

    cmd = [sys.executable, "-u", str(script)]

    if job.get("needs_folder_id"):
        if not folder_id:
            raise RuntimeError(f"{label} 缺少 folder_id")
        cmd.extend(["--folder-id", folder_id])

    started_at = datetime.now()

    punch(
        label=label,
        status="running",
        started_at=started_at,
        message="開始執行",
    )

    print(f"開始執行：{label}", flush=True)
    print("Command:", " ".join(cmd), flush=True)

    completed = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
    )

    finished_at = datetime.now()

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
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            message=error_message,
            traceback_text=stderr_text or error_message,
        )

        raise RuntimeError(error_message)

    punch(
        label=label,
        status="success",
        started_at=started_at,
        finished_at=finished_at,
        message="完成",
    )

    print(f"完成執行：{label}", flush=True)

    return {
        "job": job_name,
        "label": label,
        "status": "success",
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
    parser.add_argument("--folder-id", default="")
    args = parser.parse_args()

    main(target=args.target, folder_id=args.folder_id)
