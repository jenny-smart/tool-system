from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]


DAILY_JOBS = [
    {
        "name": "排班統計表",
        "script": BASE_DIR / "tools" / "scheduled_daily" / "schedule_report.py",
        "args": [],
    },
    {
        "name": "專員班表",
        "script": BASE_DIR / "tools" / "scheduled_daily" / "staff_schedule.py",
        "args": [],
    },
    {
        "name": "專員個資",
        "script": BASE_DIR / "tools" / "scheduled_daily" / "staff_info.py",
        "args": [],
    },
    {
        "name": "當月次月訂單",
        "script": BASE_DIR / "tools" / "scheduled_daily" / "orders_report.py",
        "args": [],
    },
    {
        "name": "業績報表",
        "script": BASE_DIR / "tools" / "scheduled_daily" / "performance_report.py",
        "args": ["schedule", "true"],
    },
]


def run_job(job: dict) -> str:
    name = job["name"]
    script = job["script"]
    args = job.get("args", [])

    if not script.exists():
        raise FileNotFoundError(f"找不到檔案：{script}")

    print(f"開始執行：{name}")

    result = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
    )

    if result.stdout:
        print(result.stdout)

    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"{name} 執行失敗，exit={result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n\n"
            f"STDERR:\n{result.stderr}"
        )

    print(f"完成：{name}")
    return f"{name} 執行完成"


def main():
    results = []

    for job in DAILY_JOBS:
        results.append(run_job(job))

    return results


if __name__ == "__main__":
    main()
