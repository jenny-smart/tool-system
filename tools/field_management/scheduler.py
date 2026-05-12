from __future__ import annotations
import argparse, os, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tools.common.log_to_sheet import write_job_log
TZ=timezone(timedelta(hours=8)); BASE_DIR=Path(__file__).resolve().parents[2]
JOBS={"schedule_stats":{"label":"外場排班統計表","module":"tools.field_management.schedule_stats"},"staff_schedule":{"label":"外場專員班表","module":"tools.field_management.staff_schedule"},"orders":{"label":"外場訂單","module":"tools.field_management.orders"},"staff_profile":{"label":"外場專員個資","module":"tools.field_management.staff_profile"}}
def now_tw(): return datetime.now(TZ)
def today_yyyymmdd(): return now_tw().strftime("%Y%m%d")
def punch(label,status,started,finished=None,area="",date_key="",system_name="",message="",traceback_text=""):
    try:
        write_job_log(system_name="外場排程系統",job_name=label,status=status,started_at=started,finished_at=finished or "",message=message,area=area or "全區",date=date_key,target=system_name,run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",traceback_text=traceback_text)
        print(f"📝 外場排程打卡：{label} / {status}",flush=True)
    except Exception as exc: print(f"⚠️ 外場排程打卡失敗：{exc}",flush=True)
def run_job(job_name,date_key,area,system_name):
    job=JOBS[job_name]; label=job["label"]; cmd=[sys.executable,"-u","-m",job["module"],"--date",date_key,"--system-name",system_name]
    if area: cmd+=["--area",area]
    started=now_tw(); punch(label,"running",started,area=area or "全區",date_key=date_key,system_name=system_name,message="開始執行")
    completed=subprocess.run(cmd,cwd=BASE_DIR,text=True,capture_output=True); finished=now_tw()
    out=completed.stdout or ""; err=completed.stderr or ""
    if out: print(out,flush=True)
    if err: print(err,flush=True)
    if completed.returncode!=0:
        msg=f"{label} 執行失敗 exit={completed.returncode}\n{out[-3000:]}\n{err[-5000:]}"
        punch(label,"failed",started,finished,area or "全區",date_key,system_name,msg,err or msg); raise RuntimeError(msg)
    punch(label,"success",started,finished,area or "全區",date_key,system_name,"完成")
    return {"job":job_name,"status":"success"}
def main(target="all",date_key=None,area=None,system_name="外場日排程系統"):
    date_key=date_key or today_yyyymmdd(); targets=list(JOBS) if target=="all" else [target]; return [run_job(t,date_key,area,system_name) for t in targets]
if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--target",default="all",choices=["all",*JOBS.keys()]); p.add_argument("--date",default=today_yyyymmdd()); p.add_argument("--area",default=""); p.add_argument("--system-name",default="外場日排程系統"); a=p.parse_args(); main(a.target,a.date,a.area or None,a.system_name)
