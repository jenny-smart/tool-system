from __future__ import annotations

import atexit
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))


def _oauth_env_ready() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in (
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
        )
    )


# 相容既有仍建立 service_account.Credentials 的程式：
# 只要 OAuth secrets 已注入，就改由 Jenny OAuth credentials 執行 Google API。
if _oauth_env_ready():
    try:
        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials as UserCredentials

        _original_from_service_account_info = service_account.Credentials.from_service_account_info

        @classmethod
        def _from_service_account_info(cls, info, *args, **kwargs):
            scopes = kwargs.get("scopes")
            if scopes is None and args:
                scopes = args[0]

            return UserCredentials(
                token=None,
                refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"].strip(),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"].strip(),
                client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
                scopes=scopes,
            )

        service_account.Credentials.from_service_account_info = _from_service_account_info
    except Exception:
        pass


# 個別執行日排程時，自動補上與 scheduler 相同的執行 Log。
# scheduler 管理的一鍵執行會設 DAILY_SCHEDULER_MANAGED=1，避免重複記錄。
_DAILY_JOB_BY_SCRIPT = {
    "schedule_report.py": "排班統計表",
    "staff_schedule.py": "專員班表",
    "orders_report.py": "當月次月訂單",
    "staff_info.py": "專員個資",
}


def _arg_value(name: str) -> str:
    try:
        idx = sys.argv.index(name)
        if idx + 1 < len(sys.argv):
            return str(sys.argv[idx + 1])
    except Exception:
        pass
    return ""


def _install_standalone_daily_logger() -> None:
    if os.getenv("DAILY_SCHEDULER_MANAGED", "").strip() == "1":
        return

    script_name = Path(sys.argv[0] or "").name
    label = _DAILY_JOB_BY_SCRIPT.get(script_name)
    if not label:
        return

    started_at = datetime.now(TZ)
    folder_id = _arg_value("--folder-id")
    state = {"failed": False, "traceback": "", "message": "完成"}

    try:
        from tools.common.log_to_sheet import write_job_log
    except Exception:
        return

    def write(status: str, *, finished_at=None, message="", traceback_text=""):
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
                date=datetime.now(TZ).strftime("%Y%m%d"),
                target=folder_id,
                source_file=script_name,
                run_type="手動",
                traceback_text=traceback_text,
            )
        except Exception as exc:
            print(f"⚠️ 個別日排程 Log 寫入失敗：{exc}", flush=True)

    write("running", message="開始執行")

    original_excepthook = sys.excepthook

    def hooked_excepthook(exc_type, exc_value, exc_tb):
        state["failed"] = True
        state["traceback"] = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        state["message"] = str(exc_value)
        original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = hooked_excepthook

    def finish_log():
        finished_at = datetime.now(TZ)
        if state["failed"]:
            write(
                "failed",
                finished_at=finished_at,
                message=state["message"] or "執行失敗",
                traceback_text=state["traceback"],
            )
        else:
            write("success", finished_at=finished_at, message="完成")

    atexit.register(finish_log)


try:
    _install_standalone_daily_logger()
except Exception:
    pass
