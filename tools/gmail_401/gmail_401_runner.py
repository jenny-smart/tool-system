"""
gmail_401_runner.py
供 GitHub Actions 排程呼叫，不依賴 Streamlit。
直接讀環境變數執行 Gmail 401 附件歸檔。
"""

import base64
import email
import imaplib
import json
import os
import re
import sys
from datetime import datetime
from email.header import decode_header
from pathlib import Path

import yaml
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from google.oauth2 import service_account


# ────────────────────────────────────────────────
# 設定載入
# ────────────────────────────────────────────────

def load_config():
    yaml_path = Path(__file__).resolve().parents[2] / "systems.yaml"
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for system in data.get("systems", []):
        if system.get("type") == "gmail_401":
            return system
    raise ValueError("systems.yaml 找不到 type: gmail_401 的設定")


def get_drive_service():
    sa_raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    if not sa_raw:
        raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 環境變數")
    sa_info = json.loads(sa_raw) if isinstance(sa_raw, str) else sa_raw
    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def get_sheets_service():
    sa_raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    sa_info = json.loads(sa_raw) if isinstance(sa_raw, str) else sa_raw
    creds = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


# ────────────────────────────────────────────────
# 工具函式
# ────────────────────────────────────────────────

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def parse_roc_year(filename):
    match = re.search(r'(\d{3})\.\d{2}', filename)
    if match:
        return str(int(match.group(1)) + 1911)
    return None


def match_region(filename, regions):
    for region_name, region_info in regions.items():
        for kw in region_info.get("keywords", []):
            if kw in filename:
                return region_name, region_info.get("folder_id", "")
    return None, None


def decode_filename(part):
    raw = part.get_filename()
    if not raw:
        return None
    decoded_parts = decode_header(raw)
    filename = ""
    for part_bytes, charset in decoded_parts:
        if isinstance(part_bytes, bytes):
            filename += part_bytes.decode(charset or "utf-8", errors="replace")
        else:
            filename += part_bytes
    return filename


def get_or_create_year_folder(drive, parent_id, year_str):
    query = (
        f"'{parent_id}' in parents and "
        f"name = '{year_str}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"trashed = false"
    )
    res = drive.files().list(
        q=query, fields="files(id, name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": year_str,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    folder = drive.files().create(
        body=meta, fields="id", supportsAllDrives=True
    ).execute()
    return folder["id"]


def upload_to_drive(drive, folder_id, filename, data):
    query = (
        f"'{folder_id}' in parents and "
        f"name = '{filename}' and trashed = false"
    )
    res = drive.files().list(
        q=query, fields="files(id)",
        supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    if res.get("files"):
        return "already_exists"
    file_meta = {"name": filename, "parents": [folder_id]}
    media = MediaInMemoryUpload(data, mimetype="application/pdf", resumable=False)
    drive.files().create(
        body=file_meta, media_body=media,
        fields="id", supportsAllDrives=True
    ).execute()
    return "uploaded"


def write_log_to_sheet(success, skipped, error, total):
    try:
        master_id = os.environ.get(
            "TOOLS_APP_LOG_SPREADSHEET_ID",
            "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"
        )
        sheets = get_sheets_service()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [[
            now, "Gmail401歸檔", "掃描歸檔", "排程", "全區",
            now[:10].replace("-", ""), "Google Drive", "",
            "成功" if error == 0 else "部分失敗",
            f"信件數={total}｜成功={success}｜略過={skipped}｜失敗={error}"
        ]]
        sheets.spreadsheets().values().append(
            spreadsheetId=master_id,
            range="日排程執行Log!A:J",
            valueInputOption="RAW",
            body={"values": row}
        ).execute()
        log("已寫入主控表 Log")
    except Exception as e:
        log(f"⚠️ 寫入主控表 Log 失敗（不影響主流程）：{e}")


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────

def main():
    config = load_config()
    regions = config.get("regions", {})
    search_query = config.get("search_query", 'HAS ATTACHMENT SUBJECT "401"')

    gmail_user = os.environ.get("GMAIL_401_USER", "")
    gmail_password = os.environ.get("GMAIL_401_APP_PASSWORD", "")

    if not gmail_user or not gmail_password:
        log("❌ 找不到 GMAIL_401_USER 或 GMAIL_401_APP_PASSWORD 環境變數")
        sys.exit(1)

    log(f"連線 Gmail：{gmail_user}")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(gmail_user, gmail_password)
    mail.select("inbox")

    status, data = mail.uid("search", None, search_query.encode())
    if status != "OK" or not data[0]:
        log("沒有找到待處理的信件")
        mail.logout()
        write_log_to_sheet(0, 0, 0, 0)
        return

    uids = data[0].split()
    log(f"找到 {len(uids)} 封待處理信件")

    drive = get_drive_service()
    success, skipped, error = 0, 0, 0

    for uid in uids:
        status, msg_data = mail.uid("fetch", uid, "(RFC822)")
        if status != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        processed_any = False

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition") is None:
                continue

            filename = decode_filename(part)
            if not filename or "401" not in filename:
                continue
            if not filename.lower().endswith(".pdf"):
                log(f"⚠️ 略過非PDF：{filename}")
                skipped += 1
                continue

            region_name, folder_id = match_region(filename, regions)
            if not region_name:
                log(f"⚠️ 無法判斷地區：{filename}")
                skipped += 1
                continue
            if not folder_id:
                log(f"⚠️ {region_name} folder_id 未設定：{filename}")
                skipped += 1
                continue

            year_str = parse_roc_year(filename)
            if not year_str:
                log(f"⚠️ 無法解析年份：{filename}")
                skipped += 1
                continue

            try:
                year_folder_id = get_or_create_year_folder(drive, folder_id, year_str)
                file_data = part.get_payload(decode=True)
                result = upload_to_drive(drive, year_folder_id, filename, file_data)

                if result == "already_exists":
                    log(f"⏭️ 已存在略過：{region_name}/{year_str}/{filename}")
                    skipped += 1
                else:
                    log(f"✅ {region_name}/{year_str}/{filename}")
                    success += 1
                processed_any = True

            except Exception as e:
                log(f"❌ 失敗：{filename}｜{e}")
                error += 1

        if processed_any:
            try:
                mail.uid("store", uid, "+X-GM-LABELS", "(401已歸檔)")
            except Exception:
                mail.uid("store", uid, "+FLAGS", r"(\Seen)")

    mail.logout()
    log(f"完成｜成功 {success}，略過 {skipped}，失敗 {error}")
    write_log_to_sheet(success, skipped, error, len(uids))

    if error > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
