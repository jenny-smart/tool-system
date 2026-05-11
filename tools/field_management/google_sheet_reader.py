import time


GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def convert_excel_to_google_sheet(drive, file_id, file_name):
    copied = drive.files().copy(
        fileId=file_id,
        body={
            "name": f"{file_name}_temp_{int(time.time())}",
            "mimeType": GOOGLE_SHEET_MIME,
        },
        fields="id,name,mimeType",
    ).execute()

    return copied["id"]


def get_first_sheet_name(sheets, spreadsheet_id):
    meta = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.title",
    ).execute()

    sheet_list = meta.get("sheets", [])
    if not sheet_list:
        raise RuntimeError("Google Sheet 沒有任何工作表")

    return sheet_list[0]["properties"]["title"]


def read_google_sheet_values(sheets, spreadsheet_id):
    sheet_name = get_first_sheet_name(sheets, spreadsheet_id)

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=sheet_name,
    ).execute()

    return result.get("values", [])


def cleanup_temp_file(drive, file_id):
    if not file_id:
        return

    try:
        drive.files().delete(fileId=file_id).execute()
    except Exception:
        pass


def read_drive_spreadsheet_values(drive, sheets, file):
    """
    統一讀取 Drive 上的試算表。
    - Google Sheets：直接讀
    - Excel xls/xlsx：先轉成 Google Sheets 再讀
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

        return read_google_sheet_values(sheets, temp_file_id)

    finally:
        if temp_file_id:
            cleanup_temp_file(drive, temp_file_id)
