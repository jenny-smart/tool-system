from __future__ import annotations

import argparse
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

try:
    from tools.common.log_to_sheet import log_to_sheet
except Exception:
    log_to_sheet = None


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


def format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def write_daily_log(
    *,
    label: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    message: str = "",
    traceback_text: str = "",
) -> None:
    if log_to_sheet is None:
        print("⚠️ 找不到 tools.common.log_to_sheet，略過寫入打卡表", flush=True)
        return

    try:
        log_to_sheet(
            system="daily",
            function=label,
            run_type="手動",
            area="全區",
            period="",
            date=datetime.now().strftime("%Y%m%d"),
            target="",
            source_file="",
            status="成功" if status == "success" else "失敗",
            message=(
                f"開始時間：{format_dt(started_at)}｜"
                f"完成時間：{format_dt(finished_at)}｜"
                f"{message}"
            ),
            traceback_text=traceback_text,
        )
    except Exception as exc:
        print(f"⚠️ 寫入日排程打卡表失敗：{exc}", flush=True)


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

    started_at = datetime.now()

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

        write_daily_log(
            label=label,
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            message=error_message,
            traceback_text=stderr_text or error_message,
        )

        raise RuntimeError(error_message)

    write_daily_log(
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
        "started_at": format_dt(started_at),
        "finished_at": format_dt(finished_at),
    }


def main(
    target: str = "all",
    folder_id: str = "",
) -> list[dict]:
    targets = list(JOBS.keys()) if target == "all" else [target]

    results = []
    failed = []

    for job_name in targets:
        try:
            results.append(
                run_job(
                    job_name,
                    folder_id=folder_id,
                )
            )
        except Exception as exc:
            failed.append(
                {
                    "job": job_name,
                    "status": "failed",
                    "message": str(exc),
                }
            )

            if target != "all":
                raise

    if failed:
        raise RuntimeError(f"日排程有失敗項目：{failed}")

    print("scheduled_daily scheduler 全部完成", flush=True)

    return results


if __name__ == "__main__":
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
