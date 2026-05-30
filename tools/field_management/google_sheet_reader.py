import random
import time

from googleapiclient.errors import HttpError


GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"


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
    except Exception:
        pass


def read_drive_spreadsheet_values(drive, sheets, file):
    """
    統一讀取 Drive 上的試算表。

    支援：
    1. Google Sheets：直接讀取
    2. Excel xls/xlsx：先轉成 Google Sheets，再讀取 values
    """
    file_id = file["id"]
    file_name = file.get("name", "")
    mime_type = file.get("mimeType", "")

    temp_file_id = None

    try:
        if mime_type == GOOGLE_SHEET_MIME:
            return read_google_sheet_values(sheets, file_id)

        temp_file_id = convert_excel_to_google_sheet(
            drive,
            file_id,
            file_name,
        )

        return read_google_sheet_values(
            sheets,
            temp_file_id,
        )

    finally:
        if temp_file_id:
            cleanup_temp_file(drive, temp_file_id)
