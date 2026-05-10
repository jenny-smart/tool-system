from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]


MONTHLY_JOBS = [
    {
        "name": "上半月訂單",
        "script": BASE_DIR / "tools" / "scheduled_monthly" / "half_month_orders.py",
        "args": ["1"],
    },
    {
        "name": "下半月訂單",
        "script": BASE_DIR / "tools" / "scheduled_monthly" / "half_month_orders.py",
        "args": ["2"],
    },
    {
        "name": "已退款",
        "script": BASE_DIR / "tools" / "scheduled_monthly" / "refund_report.py",
        "args": [],
    },
    {
        "name": "預收",
        "script": BASE_DIR / "tools" / "scheduled_monthly" / "prepaid_report.py",
        "args": [],
    },
    {
        "name": "儲值金結算",
        "script": BASE_DIR / "tools" / "scheduled_monthly" / "stored_value_settlement.py",
        "args": [],
    },
    {
        "name": "儲值金預收",
        "script": BASE_DIR / "tools" / "scheduled_monthly" / "stored_value_prepaid.py",
        "args": [],
    },
]


def run_job(job: dict) -> str:
    name = job["name"]
    script = job["script"]
    args = job.get("args", [])

    if not script.exists():
        raise FileNotFoundError(f"找不到檔案：{script}")

    print(f"開始執行：{name}")
    subprocess.run(
        [sys.executable, str(script), *args],
        check=True,
        cwd=BASE_DIR,
    )
    print(f"完成：{name}")

    return f"{name} 執行完成"


def main():
    results = []

    for job in MONTHLY_JOBS:
        results.append(run_job(job))

    return results


if __name__ == "__main__":
    main()
