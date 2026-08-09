import os
import random
import time

from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SOURCE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def execute_with_retry(request, max_retries: int = 6):
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)

            if status in [429, 500, 503]:
                wait = min(60, (2 ** attempt) * 5) + random.uniform(0, 1.5)
                print(
                    f"⚠️ Google API 暫時限流/忙碌，等待 {wait:.1f} 秒後重試 "
                    f"({attempt + 1}/{max_retries})",
                    flush=True,
                )
                time.sleep(wait)
                continue

            raise

    return request.execute()


def get_source_oauth_credentials() -> UserCredentials:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()

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
        raise RuntimeError(
            "外場來源讀取缺少 Jenny OAuth：" + ", ".join(missing)
        )

    return UserCredentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SOURCE_SCOPES,
    )


def get_source_sheets_service():
    """來源資料一律用 Jenny OAuth 讀取，不使用目標表的 Service Account。"""
    return build(
        "sheets",
        "v4",
        credentials=get_source_oauth_credentials(),
        cache_discovery=False,
    )


def convert_excel_to_google_sheet(drive, file_id, file_name):
    copied = execute_with_retry(
        drive.files().copy(
            fileId=file_id,
            body={
                "name": f"{file_name}_temp_{int(time.time())}",
                "mimeType": GOOGLE_SHEET_MIME,
            },
            fields="id,name,mimeType",
            supportsAllDrives=True,
        )
    )

    print(
        f"🔄 Jenny OAuth 已將來源 Excel 轉成暫存 Google Sheet：{copied['id']}",
        flush=True,
    )
    return copied["id"]


def get_first_sheet_name(sheets, spreadsheet_id):
    meta = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties.title",
        )
    )

    sheet_list = meta.get("sheets", [])
    if not sheet_list:
        raise RuntimeError("Google Sheet 沒有任何工作表")

    return sheet_list[0]["properties"]["title"]


def read_google_sheet_values(sheets, spreadsheet_id):
    sheet_name = get_first_sheet_name(sheets, spreadsheet_id)

    result = execute_with_retry(
        sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
        )
    )

    return result.get("values", [])


def cleanup_temp_file(drive, file_id):
    if not file_id:
        return

    try:
        execute_with_retry(
            drive.files().delete(
                fileId=file_id,
                supportsAllDrives=True,
            )
        )
        print(f"🗑️ 已刪除來源暫存 Google Sheet：{file_id}", flush=True)
    except Exception as exc:
        print(f"⚠️ 暫存 Google Sheet 刪除失敗：{exc}", flush=True)


def read_drive_spreadsheet_values(drive, sheets, file):
    """
    統一讀取 Jenny My Drive 上的來源試算表。

    認證分工：
    1. 來源 Drive：Jenny OAuth
    2. 來源 Google Sheets / Excel 轉檔後的暫存 Sheet：Jenny OAuth
    3. 呼叫端傳入的 sheets（Service Account）保留給目標表寫入，不用來讀來源

    支援：
    1. Google Sheets：Jenny OAuth 直接讀取
    2. Excel xls/xlsx：Jenny OAuth 先轉成暫存 Google Sheet，再以 Jenny OAuth 讀取
    """
    file_id = file["id"]
    file_name = file.get("name", "")
    mime_type = file.get("mimeType", "")

    # 不使用呼叫端的 Service Account sheets 讀來源。
    source_sheets = get_source_sheets_service()
    temp_file_id = None

    try:
        if mime_type == GOOGLE_SHEET_MIME:
            print(
                f"📖 使用 Jenny OAuth 讀取來源 Google Sheet：{file_name} ({file_id})",
                flush=True,
            )
            return read_google_sheet_values(source_sheets, file_id)

        temp_file_id = convert_excel_to_google_sheet(
            drive,
            file_id,
            file_name,
        )

        return read_google_sheet_values(
            source_sheets,
            temp_file_id,
        )

    finally:
        if temp_file_id:
            cleanup_temp_file(drive, temp_file_id)
