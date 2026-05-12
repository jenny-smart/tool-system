from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.common.log_to_sheet import write_job_log

TZ = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parents[2]

def now_tw() -> datetime:
    return datetime.now(TZ)

def apply_email_fallback_env() -> None:
    if not os.getenv("REPORT_EMAIL_SENDER") and os.getenv("NOTIFY_EMAIL"):
        os.environ["REPORT_EMAIL_SENDER"] = os.getenv("NOTIFY_EMAIL", "")
    if not os.getenv("REPORT_EMAIL_APP_PASSWORD") and os.getenv("NOTIFY_PASSWORD"):
        os.environ["REPORT_EMAIL_APP_PASSWORD"] = os.getenv("NOTIFY_PASSWORD", "")
    if not os.getenv("REPORT_EMAIL_RECIPIENT") and os.getenv("NOTIFY_TO"):
        os.environ["REPORT_EMAIL_RECIPIENT"] = os.getenv("NOTIFY_TO", "")

def write_log(status: str, started_at: datetime, finished_at=None, message="", traceback_text=""):
    try:
        write_job_log(
            system_name="日排程系統",
            job_name="業績報表",
            status=status,
            started_at=started_at,
            finished_at=finished_at or "",
            message=message,
            area="全區",
            period="",
            date=now_tw().strftime("%Y%m%d"),
            target="dashboard_data/latest",
            source_file="performance_report.py",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            traceback_text=traceback_text,
        )
        print(f"📝 業績報表打卡：{status}", flush=True)
    except Exception as exc:
        print(f"⚠️ 業績報表打卡失敗：{exc}", flush=True)

def main(mode: str="schedule", send_email: str="true") -> None:
    started_at=now_tw()
    apply_email_fallback_env()
    write_log("running",started_at,message="開始執行業績報表")
    cmd=[sys.executable,"-u",str(BASE_DIR/"tools/scheduled_daily/performance_report.py"),mode,send_email]
    print("Command:"," ".join(cmd),flush=True)
    completed=subprocess.run(cmd,cwd=BASE_DIR,text=True,capture_output=True,env=os.environ.copy())
    finished_at=now_tw()
    stdout_text=completed.stdout or ""
    stderr_text=completed.stderr or ""
    if stdout_text: print(stdout_text,flush=True)
    if stderr_text: print(stderr_text,flush=True)
    log_dir=BASE_DIR/"logs"/now_tw().strftime("%Y%m%d")
    log_dir.mkdir(parents=True,exist_ok=True)
    (log_dir/"performance_report.log").write_text(stdout_text+"\n"+stderr_text,encoding="utf-8")
    (log_dir/"performance_report.exit").write_text(str(completed.returncode),encoding="utf-8")
    if completed.returncode!=0:
        msg=f"業績報表執行失敗，exit={completed.returncode}\nSTDOUT:\n{stdout_text[-3000:]}\nSTDERR:\n{stderr_text[-5000:]}"
        write_log("failed",started_at,finished_at,msg,stderr_text or msg)
        raise RuntimeError(msg)
    email_ready=bool(os.getenv("REPORT_EMAIL_SENDER") and os.getenv("REPORT_EMAIL_APP_PASSWORD") and os.getenv("REPORT_EMAIL_RECIPIENT"))
    write_log("success",started_at,finished_at,f"完成；email設定={'已設定' if email_ready else '未設定'}")
    print("🎉 performance_report_runner.py 全部完成",flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",nargs="?",default="schedule")
    parser.add_argument("send_email",nargs="?",default="true")
    args=parser.parse_args()
    main(args.mode,args.send_email)
