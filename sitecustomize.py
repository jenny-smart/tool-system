from __future__ import annotations

import atexit
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
DAILY_ROOT_FOLDER_ID = "11O6eKHQ1e3Irqps7Kq1XJOSVVge3pCAc"
MONTHLY_ROOT_FOLDER_ID = "1t0B8BdUKBvaS6TM40-hpodUnxeZr3eWe"


# ============================================================
# gspread 429 自動重試
# ============================================================
# install_gspread_retry() 原本只有 toolapp.py 會呼叫（跑在 Streamlit
# Cloud）。本機 Agent 執行的每一支腳本（fubon_agent.py、
# stored_value_adjustment.py…）都是各自獨立的 `python -m` 行程，不會繼承
# toolapp.py 裝好的重試保護，一撞到 Sheets API 配額（429 Quota exceeded）
# 就直接整支崩潰。這裡在直譯器啟動時就裝好，讓所有本機腳本都受惠，不用
# 逐一修改每支腳本。
try:
    from services.google_api_retry import install_gspread_retry

    install_gspread_retry()
except Exception as exc:
    print(f"⚠️ gspread 429 自動重試安裝失敗：{exc}", flush=True)

# 舊共用雲端來源 ID -> Jenny「我的雲端硬碟 / @日排程」子資料夾名稱。
# 外場 / 客服程式可以繼續沿用既有設定值，Drive proxy 會在 API 呼叫前
# 自動改寫成新的 My Drive 子資料夾 ID。
_DAILY_SOURCE_FOLDER_NAMES = {
    "1V0IjoJqHlnkGb3Oq70Cil63pQ9j8r2Xv": "排班統計表",
    "10__ajnbpu2oabAVUG_u3RAHK2a2vcgj2": "專員班表",
    "1QnOJzn-xmZ_oAMoiM6Qnfk3Y2CWuM1c4": "訂單資料",
    "199wJef-ISEP5bsSWaSseCAHynoVRE26e": "專員個資",
}
_DAILY_SOURCE_FOLDER_CACHE: dict[str, str] = {}


def _oauth_env_ready() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in (
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
        )
    )


def _argv_text() -> str:
    values = []
    try:
        values.extend(str(v) for v in getattr(sys, "orig_argv", []) or [])
    except Exception:
        pass
    try:
        values.extend(str(v) for v in sys.argv)
    except Exception:
        pass
    return " ".join(values)


def _is_field_process() -> bool:
    text = _argv_text()
    return (
        os.getenv("FIELD_SOURCE_FROM_DAILY", "").strip() == "1"
        or "field_management" in text
    )


def _is_service_process() -> bool:
    text = _argv_text()
    return (
        os.getenv("SERVICE_SOURCE_FROM_DAILY", "").strip() == "1"
        or "service_management" in text
    )


def _is_field_or_service_process() -> bool:
    return _is_field_process() or _is_service_process()


def _oauth_credentials(scopes=None):
    from google.oauth2.credentials import Credentials as UserCredentials

    return UserCredentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"].strip(),
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
        scopes=scopes or [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ],
    )


# ============================================================
# 日 / 月排程既有程式 OAuth 相容層
# ============================================================
# 外場 / 客服刻意排除：它們需要「來源 Drive = OAuth、目標 Sheets = SA」的
# 混合認證，下面另有專用 bridge。
if _oauth_env_ready() and not _is_field_or_service_process():
    try:
        from google.oauth2 import service_account

        _original_from_service_account_info = service_account.Credentials.from_service_account_info

        @classmethod
        def _from_service_account_info(cls, info, *args, **kwargs):
            scopes = kwargs.get("scopes")
            if scopes is None and args:
                scopes = args[0]
            return _oauth_credentials(scopes=scopes)

        service_account.Credentials.from_service_account_info = _from_service_account_info
    except Exception:
        pass


# ============================================================
# 外場 / 客服：Drive 來源改讀 Jenny My Drive，但 Sheets 仍保留 SA
# ============================================================

def _resolve_daily_source_folder(old_folder_id: str, original_build) -> str:
    if old_folder_id not in _DAILY_SOURCE_FOLDER_NAMES:
        return old_folder_id
    if old_folder_id in _DAILY_SOURCE_FOLDER_CACHE:
        return _DAILY_SOURCE_FOLDER_CACHE[old_folder_id]

    folder_name = _DAILY_SOURCE_FOLDER_NAMES[old_folder_id]
    drive = original_build(
        "drive",
        "v3",
        credentials=_oauth_credentials(["https://www.googleapis.com/auth/drive"]),
        cache_discovery=False,
    )
    escaped_parent = DAILY_ROOT_FOLDER_ID.replace("'", "\\'")
    escaped_name = folder_name.replace("'", "\\'")
    result = drive.files().list(
        q=(
            f"'{escaped_parent}' in parents and "
            "mimeType='application/vnd.google-apps.folder' and "
            f"name='{escaped_name}' and trashed=false"
        ),
        fields="files(id,name)",
        pageSize=10,
    ).execute()
    files = result.get("files", [])
    if not files:
        raise RuntimeError(
            f"@日排程 找不到來源子資料夾：{folder_name} "
            f"(root={DAILY_ROOT_FOLDER_ID})"
        )

    folder_id = files[0]["id"]
    _DAILY_SOURCE_FOLDER_CACHE[old_folder_id] = folder_id
    print(
        f"📂 外場/客服來源已切換：{folder_name} -> {folder_id}",
        flush=True,
    )
    return folder_id


def _rewrite_daily_source_ids(value, original_build):
    if isinstance(value, str):
        output = value
        for old_id in _DAILY_SOURCE_FOLDER_NAMES:
            if old_id in output:
                new_id = _resolve_daily_source_folder(old_id, original_build)
                output = output.replace(old_id, new_id)
        return output
    if isinstance(value, list):
        return [_rewrite_daily_source_ids(v, original_build) for v in value]
    if isinstance(value, tuple):
        return tuple(_rewrite_daily_source_ids(v, original_build) for v in value)
    if isinstance(value, dict):
        return {
            k: _rewrite_daily_source_ids(v, original_build)
            for k, v in value.items()
        }
    return value


class _DailySourceFilesProxy:
    def __init__(self, files_resource, original_build):
        self._files_resource = files_resource
        self._original_build = original_build

    def __getattr__(self, name):
        attr = getattr(self._files_resource, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            new_args = tuple(
                _rewrite_daily_source_ids(v, self._original_build) for v in args
            )
            new_kwargs = {
                k: _rewrite_daily_source_ids(v, self._original_build)
                for k, v in kwargs.items()
            }
            return attr(*new_args, **new_kwargs)

        return wrapped


class _DailySourceDriveProxy:
    def __init__(self, service, original_build):
        self._service = service
        self._original_build = original_build

    def files(self):
        return _DailySourceFilesProxy(
            self._service.files(),
            self._original_build,
        )

    def __getattr__(self, name):
        return getattr(self._service, name)


def _install_field_service_drive_bridge() -> None:
    if not (_oauth_env_ready() and _is_field_or_service_process()):
        return

    import googleapiclient.discovery as discovery

    original_build = discovery.build

    def patched_build(serviceName, version, *args, **kwargs):
        if str(serviceName).lower() == "drive":
            # 來源 Drive 一律 Jenny OAuth；忽略呼叫端傳入的 SA credentials。
            kwargs = dict(kwargs)
            kwargs["credentials"] = _oauth_credentials(
                ["https://www.googleapis.com/auth/drive"]
            )
            kwargs.setdefault("cache_discovery", False)
            service = original_build(serviceName, version, *args, **kwargs)
            return _DailySourceDriveProxy(service, original_build)
        # Sheets / 其他 API 維持原本 credentials（通常為 Service Account）。
        return original_build(serviceName, version, *args, **kwargs)

    discovery.build = patched_build
    print(
        "🔐 外場/客服混合認證：來源 Drive=Jenny OAuth；目標 Sheets=Service Account",
        flush=True,
    )


try:
    _install_field_service_drive_bridge()
except Exception as exc:
    print(f"⚠️ 外場/客服 Drive OAuth bridge 啟用失敗：{exc}", flush=True)


# 客服程式以 Service Account gspread 寫目標表；讀取 Jenny My Drive 中的
# Google Sheet / xlsx 轉檔結果時，若 SA 無權限，才 fallback 到 Jenny OAuth。
def _install_service_gspread_source_fallback() -> None:
    if not (_oauth_env_ready() and _is_service_process()):
        return

    import gspread

    original_open_by_key = gspread.Client.open_by_key
    oauth_client_holder = {"client": None}

    def patched_open_by_key(client, key):
        try:
            return original_open_by_key(client, key)
        except Exception as original_exc:
            if oauth_client_holder["client"] is None:
                oauth_client_holder["client"] = gspread.authorize(
                    _oauth_credentials(
                        [
                            "https://www.googleapis.com/auth/drive",
                            "https://www.googleapis.com/auth/spreadsheets",
                        ]
                    )
                )
            try:
                return original_open_by_key(oauth_client_holder["client"], key)
            except Exception:
                raise original_exc

    gspread.Client.open_by_key = patched_open_by_key


try:
    _install_service_gspread_source_fallback()
except Exception as exc:
    print(f"⚠️ 客服來源 Sheet OAuth fallback 啟用失敗：{exc}", flush=True)


# ============================================================
# 月排程：單項執行先進年度根目錄
# ============================================================

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
    script_path = Path(sys.argv[0] or "")
    if script_path.parent.name != "scheduled_monthly":
        return

    folder_id = _arg_value("--folder-id").strip()
    if folder_id != MONTHLY_ROOT_FOLDER_ID or not _oauth_env_ready():
        return

    from googleapiclient.discovery import build

    year = _monthly_year_from_args()
    folder_name = f"{year}專員承攬服務費"
    service = build(
        "drive",
        "v3",
        credentials=_oauth_credentials(["https://www.googleapis.com/auth/drive"]),
        cache_discovery=False,
    )

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


try:
    _route_standalone_monthly_folder()
except Exception as exc:
    print(f"⚠️ 月排程年度資料夾解析失敗：{exc}", flush=True)


# ============================================================
# 個別日排程 Log
# ============================================================
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
