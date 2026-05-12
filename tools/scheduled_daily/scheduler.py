from __future__ import annotations
import subprocess, sys
from pathlib import Path
from tools.common.log_to_sheet import log_to_sheet

BASE_DIR = Path(__file__).resolve().parents[2]

JOBS = {
    "schedule_report": {
        "label": "排班統計表",
        "script": "tools/scheduled_daily/schedule_report.py",
    },
    "staff_schedule": {
        "label": "專員班表",
        "script": "tools/scheduled_daily/staff_schedule.py",
    },
}

def run_job(job_name):
    job = JOBS[job_name]
    label = job["label"]

    cmd = [
        sys.executable,
        "-u",
        str(BASE_DIR / job["script"]),
    ]

    log_to_sheet(
        system="日排程系統",
        function=label,
        status="執行中",
        message="開始執行",
    )

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=BASE_DIR,
    )

    logs = []

    for line in process.stdout:
        line = line.rstrip()
        logs.append(line)
        print(line, flush=True)

        log_to_sheet(
            system="日排程系統",
            function=label,
            status="執行中",
            message=line[:500],
        )

    process.wait()

    if process.returncode != 0:
        log_to_sheet(
            system="日排程系統",
            function=label,
            status="失敗",
            message="\n".join(logs)[-3000:],
        )
        raise RuntimeError(f"{label} 執行失敗")

    log_to_sheet(
        system="日排程系統",
        function=label,
        status="成功",
        message="執行完成",
    )

def main():
    for job_name in JOBS:
        run_job(job_name)

if __name__ == "__main__":
    main()
