from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TZ = timezone(timedelta(hours=8))
DAILY_ROOT_FOLDER_ID = "11O6eKHQ1e3Irqps7Kq1XJOSVVge3pCAc"
TARGET_FOLDERS = ["排班統計表", "專員班表", "專員個資", "訂單資料"]


def now_tp() -> datetime:
    return datetime.now(TZ)


def is_month_end(dt: datetime) -> bool:
    return (dt + timedelta(days=1)).month != dt.month


def oauth_value(name: str) -> str:
    # GitHub Actions cleanup 會把 OAuth secrets 複製到 CLEANUP_*，
    # 並暫時移除 GOOGLE_OAUTH_*，避免 sitecustomize 把 Log 的 SA 一起改成 OAuth。
    return (
        os.getenv(f"CLEANUP_{name}", "").strip()
        or os.getenv(name, "").strip()
    )


def get_drive_service():
    client_id = oauth_value("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = oauth_value("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = oauth_value("GOOGLE_OAUTH_REFRESH_TOKEN")

    missing = [
        name
        for name, value in [
            ("GOOGLE_OAUTH_CLIENT_ID", client_id),
            ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
            ("GOOGLE_OAUTH_REFRESH_TOKEN", refresh_token),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError("缺少 Jenny OAuth：" + ", ".join(missing))

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def q_escape(value: str) -> str:
    return value.replace("'", "\\'")


def find_child_folder(drive, parent_id: str, name: str) -> str:
    result = drive.files().list(
        q=(
            f"'{q_escape(parent_id)}' in parents and "
            "mimeType='application/vnd.google-apps.folder' and "
            f"name='{q_escape(name)}' and trashed=false"
        ),
        fields="files(id,name)",
        pageSize=10,
    ).execute()
    files = result.get("files", [])
    if not files:
        raise RuntimeError(f"@日排程 找不到子資料夾：{name}")
    return files[0]["id"]


def list_children(drive, folder_id: str) -> list[dict]:
    output: list[dict] = []
    token = None
    while True:
        result = drive.files().list(
            q=f"'{q_escape(folder_id)}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType)",
            pageSize=1000,
            pageToken=token,
        ).execute()
        output.extend(result.get("files", []))
        token = result.get("nextPageToken")
        if not token:
            return output


def trash_all_children(drive, folder_id: str, folder_name: str) -> int:
    files = list_children(drive, folder_id)
    for file_info in files:
        drive.files().update(
            fileId=file_info["id"],
            body={"trashed": True},
        ).execute()
        print(f"🗑️ {folder_name}：{file_info['name']}", flush=True)
    return len(files)


def write_log(status: str, message: str, started_at: datetime, finished_at: datetime | None = None) -> None:
    try:
        from tools.common.log_to_sheet import write_job_log

        write_job_log(
            system_name="日排程系統",
            job_name="月底清理",
            status=status,
            started_at=started_at,
            finished_at=finished_at or "",
            message=message,
            area="全區",
            period=started_at.strftime("%Y%m"),
            date=started_at.strftime("%Y%m%d"),
            target=DAILY_ROOT_FOLDER_ID,
            source_file="",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            traceback_text=message if status == "failed" else "",
        )
    except Exception as exc:
        print(f"⚠️ 月底清理 Log 寫入失敗：{exc}", flush=True)


def main(force: bool = False) -> dict:
    started_at = now_tp()

    if not force and not is_month_end(started_at):
        message = f"{started_at:%Y-%m-%d} 不是月底，略過清理"
        print(message, flush=True)
        return {"skipped": True, "message": message}

    write_log("running", "開始清理 @日排程 四個來源資料夾", started_at)

    try:
        drive = get_drive_service()
        counts: dict[str, int] = {}

        for folder_name in TARGET_FOLDERS:
            folder_id = find_child_folder(drive, DAILY_ROOT_FOLDER_ID, folder_name)
            counts[folder_name] = trash_all_children(drive, folder_id, folder_name)

        total = sum(counts.values())
        message = "；".join(f"{name} {count} 個" for name, count in counts.items()) + f"；共 {total} 個"
        finished_at = now_tp()
        write_log("success", message, started_at, finished_at)
        print("✅ 月底清理完成：" + message, flush=True)
        return {"skipped": False, "counts": counts, "total": total}

    except Exception as exc:
        finished_at = now_tp()
        message = str(exc)
        write_log("failed", message, started_at, finished_at)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="忽略月底判斷（僅供人工測試）")
    args = parser.parse_args()
    result = main(force=args.force)
    print(json.dumps(result, ensure_ascii=False), flush=True)
