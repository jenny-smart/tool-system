from __future__ import annotations

import atexit
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
MONTHLY_ROOT_FOLDER_ID = "1t0B8BdUKBvaS6TM40-hpodUnxeZr3eWe"


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


def _arg_value(name: str) -> str:
    try:
        idx = sys.argv.index(name)
        if idx + 1 < len(sys.argv):
            return str(sys.argv[idx + 1])
    except Exception:
        pass
    return ""


def _replace_arg_value(name: str, value: str) -> None:
    try:
        idx = sys.argv.index(name)
        if idx + 1 < len(sys.argv):
            sys.argv[idx + 1] = value
    except Exception:
        pass


def _monthly_year_from_args() -> int:
    period = _arg_value("--period").strip()
    start = _arg_value("--start").strip()
    if len(period) >= 4 and period[:4].isdigit():
        return int(period[:4])
    if len(start) >= 4 and start[:4].isdigit():
        return int(start[:4])
    return datetime.now(TZ).year


def _route_standalone_monthly_folder() -> None:
    """單項月排程若收到總根目錄，自動改成年度根目錄。

    toolapp.py 的單項月排程目前直接把 MONTHLY_ROOT_FOLDER_ID 傳給各支 script。
    這裡在 script 真正開始前先改成：
    總根目錄 -> YYYY專員承攬服務費 -> 各區 -> 各期別。
    一鍵月排程 scheduler 已經會先解析年度，因此收到年度 folder_id 時不再重複處理。
    """
    script_path = Path(sys.argv[0] or "")
    if script_path.parent.name != "scheduled_monthly":
        return

    folder_id = _arg_value("--folder-id").strip()
    if folder_id != MONTHLY_ROOT_FOLDER_ID:
        return
    if not _oauth_env_ready():
        return

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        year = _monthly_year_from_args()
        folder_name = f"{year}專員承攬服務費"

        creds = Credentials(
            token=None,
            refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"].strip(),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"].strip(),
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        escaped_parent = MONTHLY_ROOT_FOLDER_ID.replace("'", "\\'")
        escaped_name = folder_name.replace("'", "\\'")
        result = service.files().list(
            q=(
                f"'{escaped_parent}' in parents and "
                "mimeType='application/vnd.google-apps.folder' and "
                f"name='{escaped_name}' and trashed=false"
            ),
            fields="files(id,name)",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = result.get("files", [])

        if files:
            year_folder_id = files[0]["id"]
        else:
            created = service.files().create(
                body={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [MONTHLY_ROOT_FOLDER_ID],
                },
                fields="id,name",
                supportsAllDrives=True,
            ).execute()
            year_folder_id = created["id"]

        _replace_arg_value("--folder-id", year_folder_id)
        print(
            f"📂 月排程路徑已修正：{MONTHLY_ROOT_FOLDER_ID} -> {folder_name} ({year_folder_id})",
            flush=True,
        )
    except Exception as exc:
        print(f"⚠️ 月排程年度資料夾解析失敗：{exc}", flush=True)


try:
    _route_standalone_monthly_folder()
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
