from __future__ import annotations
import argparse, os, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tools.common.log_to_sheet import write_job_log
TZ=timezone(timedelta(hours=8)); BASE_DIR=Path(__file__).resolve().parents[2]
JOBS={"half_month_orders_1":{"label":"上半月訂單","script":"tools/scheduled_monthly/half_month_orders.py","extra":["--half","1"]},"half_month_orders_2":{"label":"下半月訂單","script":"tools/scheduled_monthly/half_month_orders.py","extra":["--half","2"]}}
def now_tw(): return datetime.now(TZ)
def punch(label,status,started_at,finished_at=None,area="",period="",target="",message="",traceback_text=""):
    try:
        write_job_log(system_name="月排程系統",job_name=label,status=status,started_at=started_at,finished_at=finished_at or "",message=message,area=area,period=period,target=target,run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",traceback_text=traceback_text)
        print(f"📝 月排程打卡：{label} / {status}",flush=True)
    except Exception as exc: print(f"⚠️ 月排程打卡失敗：{exc}",flush=True)
def run_job(job_name,folder_id="",area="all",period="",start="",end=""):
    job=JOBS[job_name]; label=job["label"]; script=BASE_DIR/job["script"]
    cmd=[sys.executable,"-u",str(script),*job.get("extra",[]),"--folder-id",folder_id,"--area",area]
    if period: cmd+=["--period",period]
    if start and end: cmd+=["--start",start,"--end",end]
    started=now_tw(); punch(label,"running",started,area=area,period=period or f"{start}~{end}",target=folder_id,message="開始執行")
    completed=subprocess.run(cmd,cwd=BASE_DIR,text=True,capture_output=True); finished=now_tw()
    out=completed.stdout or ""; err=completed.stderr or ""
    if out: print(out,flush=True)
    if err: print(err,flush=True)
    if completed.returncode!=0:
        msg=f"{label} 執行失敗 exit={completed.returncode}\n{out[-3000:]}\n{err[-5000:]}"
        punch(label,"failed",started,finished,area,period or f"{start}~{end}",folder_id,msg,err or msg); raise RuntimeError(msg)
    punch(label,"success",started,finished,area,period or f"{start}~{end}",folder_id,"完成")
    return {"job":job_name,"status":"success"}
def main(target="half_month_orders_1",folder_id="",area="all",period="",start="",end=""):
    targets=list(JOBS) if target=="all" else [target]; return [run_job(t,folder_id,area,period,start,end) for t in targets]
if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--target",default="half_month_orders_1",choices=["all",*JOBS.keys()]); p.add_argument("--folder-id",default=os.getenv("MONTHLY_ROOT_FOLDER_ID","")); p.add_argument("--area",default="all"); p.add_argument("--period",default=""); p.add_argument("--start",default=""); p.add_argument("--end",default=""); a=p.parse_args(); main(a.target,a.folder_id,a.area,a.period,a.start,a.end)
