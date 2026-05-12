from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from tools.common.log_to_sheet import write_job_log

BASE_DIR = Path(__file__).resolve().parents[2]
TZ = timezone(timedelta(hours=8))

JOBS = {
    "schedule_report": {"label": "排班統計表", "script": "tools/scheduled_daily/schedule_report.py"},
    "staff_schedule": {"label": "專員班表", "script": "tools/scheduled_daily/staff_schedule.py"},
    "orders_report": {"label": "當月次月訂單", "script": "tools/scheduled_daily/orders_report.py"},
    "staff_info": {"label": "專員個資", "script": "tools/scheduled_daily/staff_info.py"},
}

def now_tw() -> datetime:
    return datetime.now(TZ)

def punch(label: str, status: str, started_at: datetime, finished_at: datetime | None = None, folder_id: str = "", message: str = "", traceback_text: str = "") -> None:
    try:
        write_job_log(
            system_name="日排程系統",
            job_name=label,
            status=status,
            started_at=started_at,
            finished_at=finished_at or "",
            message=message,
            area="全區",
            period="",
            date=now_tw().strftime("%Y%m%d"),
            target=folder_id,
            source_file="",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            traceback_text=traceback_text,
        )
        print(f"📝 日排程打卡：{label} / {status}", flush=True)
    except Exception as exc:
        print(f"⚠️ 日排程打卡失敗：{exc}", flush=True)

def run_job(job_name: str, folder_id: str = "") -> dict:
    if job_name not in JOBS:
        raise RuntimeError(f"未知 job：{job_name}")
    job=JOBS[job_name]
    label=job["label"]
    script=BASE_DIR/job["script"]
    if not script.exists():
        raise RuntimeError(f"{label} 找不到執行檔：{script}")
    if not folder_id:
        raise RuntimeError(f"{label} 缺少 folder_id")
    cmd=[sys.executable,"-u",str(script),"--folder-id",folder_id]
    started_at=now_tw()
    punch(label,"running",started_at,folder_id=folder_id,message="開始執行")
    print("Command:"," ".join(cmd),flush=True)
    completed=subprocess.run(cmd,cwd=BASE_DIR,text=True,capture_output=True)
    finished_at=now_tw()
    stdout_text=completed.stdout or ""
    stderr_text=completed.stderr or ""
    if stdout_text: print(stdout_text,flush=True)
    if stderr_text: print(stderr_text,flush=True)
    if completed.returncode!=0:
        msg=f"{label} 執行失敗，exit={completed.returncode}\nSTDOUT:\n{stdout_text[-3000:]}\nSTDERR:\n{stderr_text[-5000:]}"
        punch(label,"failed",started_at,finished_at,folder_id,msg,stderr_text or msg)
        raise RuntimeError(msg)
    punch(label,"success",started_at,finished_at,folder_id,"完成")
    print(f"完成執行：{label}", flush=True)
    return {"job":job_name,"label":label,"status":"success"}

def main(target: str = "all", folder_id: str = "") -> list[dict]:
    targets=list(JOBS.keys()) if target=="all" else [target]
    results=[]
    failed=[]
    for job_name in targets:
        try:
            results.append(run_job(job_name,folder_id))
        except Exception as exc:
            failed.append({"job":job_name,"status":"failed","message":str(exc)})
            if target!="all":
                raise
    if failed:
        raise RuntimeError(f"日排程有失敗項目：{failed}")
    print("scheduled_daily scheduler 全部完成", flush=True)
    return results

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--target",default="all",choices=["all",*JOBS.keys()])
    parser.add_argument("--folder-id",default=os.getenv("DAILY_ROOT_FOLDER_ID",""))
    args=parser.parse_args()
    main(args.target,args.folder_id)
