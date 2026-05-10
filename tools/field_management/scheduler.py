from __future__ import annotations

import argparse
import traceback

from tools.field_management.schedule_stats import main as run_schedule_stats, today_yyyymmdd, log
from tools.field_management.staff_schedule import main as run_staff_schedule
from tools.field_management.orders import main as run_orders
from tools.field_management.staff_profile import main as run_staff_profile

JOBS = {
    "schedule_stats": run_schedule_stats,
    "staff_schedule": run_staff_schedule,
    "orders": run_orders,
    "staff_profile": run_staff_profile,
}


def run_job(job_name: str, date_key: str, area: str | None, system_name: str) -> dict:
    if job_name not in JOBS:
        raise RuntimeError(f"未知 job：{job_name}")

    log(f"開始執行：{job_name}")

    try:
        JOBS[job_name](date_key=date_key, area=area, system_name=system_name)
        log(f"完成執行：{job_name}")
        return {"job": job_name, "status": "success", "message": "完成"}
    except Exception as e:
        log(f"執行失敗：{job_name} / {e}")
        traceback.print_exc()
        return {"job": job_name, "status": "failed", "message": str(e)}


def main(target: str = "all", date_key: str | None = None, area: str | None = None, system_name: str = "外場日排程系統") -> list[dict]:
    date_key = date_key or today_yyyymmdd()
    targets = ["schedule_stats", "staff_schedule", "orders", "staff_profile"] if target == "all" else [target]
    results = [run_job(job, date_key, area, system_name) for job in targets]
    failed = [r for r in results if r["status"] != "success"]
    if failed:
        raise RuntimeError(f"外場日排程有失敗項目：{failed}")
    log("scheduler.py 全部完成")
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="all", choices=["all", "schedule_stats", "staff_schedule", "orders", "staff_profile"])
    p.add_argument("--date", default=today_yyyymmdd())
    p.add_argument("--area", default="")
    p.add_argument("--system-name", default="外場日排程系統")
    args = p.parse_args()
    main(args.target, args.date, args.area or None, args.system_name)
