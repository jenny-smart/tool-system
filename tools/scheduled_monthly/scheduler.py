from __future__ import annotations

"""
檔案：tools/scheduled_monthly/scheduler.py
版本：0621_v1
更新日期：2026-06-21
更新內容：
- 月排程一鍵執行維持 6 個 job：上半月訂單、下半月訂單、已退款、預收、儲值金結算、儲值金預收。
- 相容舊地區參數：01.台北專員、02.台中專員、03.桃園專員、04.新竹專員、05.高雄專員。
- 不新增「已退款待加收」。
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.common.log_to_sheet import write_job_log

TZ = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parents[2]

AREA_ALIASES = {
    "01.台北專員": "台北",
    "02.台中專員": "台中",
    "03.桃園專員": "桃園",
    "04.新竹專員": "新竹",
    "05.高雄專員": "高雄",
}

JOBS = {
    "half_month_orders_1": {
        "label": "上半月訂單",
        "script": "tools/scheduled_monthly/half_month_orders.py",
        "extra": ["--half", "1"],
        "needs_folder_id": True,
    },
    "half_month_orders_2": {
        "label": "下半月訂單",
        "script": "tools/scheduled_monthly/half_month_orders.py",
        "extra": ["--half", "2"],
        "needs_folder_id": True,
    },
    "refund_report": {
        "label": "已退款",
        "script": "tools/scheduled_monthly/refund_report.py",
        "extra": [],
        "needs_folder_id": True,
    },
    "prepaid_report": {
        "label": "預收",
        "script": "tools/scheduled_monthly/prepaid_report.py",
        "extra": [],
        "needs_folder_id": True,
    },
    "stored_value_settlement": {
        "label": "儲值金結算",
        "script": "tools/scheduled_monthly/stored_value_settlement.py",
        "extra": [],
        "needs_folder_id": True,
    },
    "stored_value_prepaid": {
        "label": "儲值金預收",
        "script": "tools/scheduled_monthly/stored_value_prepaid.py",
        "extra": [],
        "needs_folder_id": True,
    },
}


def now_tw() -> datetime:
    return datetime.now(TZ)


def normalize_area_arg(area: str = "all") -> str:
    value = str(area or "all").strip()
    if value in ["", "全區", "全部", "ALL", "All", "all"]:
        return "all"
    return AREA_ALIASES.get(value, value)


def punch(
    label,
    status,
    started_at,
    finished_at=None,
    area="",
    period="",
    target="",
    message="",
    traceback_text="",
):
    try:
        write_job_log(
            system_name="月排程系統",
            job_name=label,
            status=status,
            started_at=started_at,
            finished_at=finished_at or "",
            message=message,
            area=area,
            period=period,
            date="",
            target=target,
            source_file="",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            traceback_text=traceback_text,
        )
        print(f"📝 月排程打卡：{label} / {status}", flush=True)
    except Exception as exc:
        print(f"⚠️ 月排程打卡失敗：{exc}", flush=True)


def run_job(
    job_name: str,
    folder_id: str = "",
    area: str = "all",
    period: str = "",
    start: str = "",
    end: str = "",
) -> dict:
    if job_name not in JOBS:
        raise RuntimeError(f"未知月排程 job：{job_name}")

    area = normalize_area_arg(area)
    job = JOBS[job_name]
    label = job["label"]
    script = BASE_DIR / job["script"]

    if not script.exists():
        raise RuntimeError(f"{label} 找不到執行檔：{script}")

    cmd = [sys.executable, "-u", str(script), *job.get("extra", [])]

    if job.get("needs_folder_id"):
        if not folder_id:
            raise RuntimeError(f"{label} 缺少 folder_id")
        cmd.extend(["--folder-id", folder_id])

    if area:
        cmd.extend(["--area", area])

    if period:
        cmd.extend(["--period", period])

    if start and end:
        cmd.extend(["--start", start, "--end", end])

    period_text = period or f"{start}~{end}".strip("~")

    started_at = now_tw()
    punch(
        label,
        "running",
        started_at,
        area=area,
        period=period_text,
        target=folder_id,
        message="開始執行",
    )

    print("Command:", " ".join(cmd), flush=True)

    completed = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
    )
    finished_at = now_tw()

    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""

    if stdout_text:
        print(stdout_text, flush=True)
    if stderr_text:
        print(stderr_text, flush=True)

    if completed.returncode != 0:
        message = (
            f"{label} 執行失敗，exit={completed.returncode}\n"
            f"STDOUT:\n{stdout_text[-3000:]}\n"
            f"STDERR:\n{stderr_text[-5000:]}"
        )
        punch(
            label,
            "failed",
            started_at,
            finished_at,
            area=area,
            period=period_text,
            target=folder_id,
            message=message,
            traceback_text=stderr_text or message,
        )
        raise RuntimeError(message)

    punch(
        label,
        "success",
        started_at,
        finished_at,
        area=area,
        period=period_text,
        target=folder_id,
        message="完成",
    )
    return {"job": job_name, "label": label, "status": "success"}


def main(
    target: str = "half_month_orders_1",
    folder_id: str = "",
    area: str = "all",
    period: str = "",
    start: str = "",
    end: str = "",
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
                    area=area,
                    period=period,
                    start=start,
                    end=end,
                )
            )
        except Exception as exc:
            failed.append({"job": job_name, "status": "failed", "message": str(exc)})
            if target != "all":
                raise

    if failed:
        raise RuntimeError(f"月排程有失敗項目：{failed}")

    print("scheduled_monthly scheduler 全部完成", flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="half_month_orders_1", choices=["all", *JOBS.keys()])
    parser.add_argument("--folder-id", default=os.getenv("MONTHLY_ROOT_FOLDER_ID", ""))
    parser.add_argument("--area", default="all")
    parser.add_argument("--period", default="")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    args = parser.parse_args()

    main(
        target=args.target,
        folder_id=args.folder_id,
        area=args.area,
        period=args.period,
        start=args.start,
        end=args.end,
    )
