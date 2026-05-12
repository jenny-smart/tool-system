from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

try:
    import streamlit as st
    HAS_STREAMLIT = True
except Exception:
    HAS_STREAMLIT = False


LOGIN_URL = "https://backend.lemonclean.com.tw/login"
EXPORT_BASE = "https://backend.lemonclean.com.tw/cleaner1/export_all"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
}

API_LIMIT = 10000
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
TZ = timezone(timedelta(hours=8))

LOCAL_OUTPUT_DIR = Path(
    "/Users/jenny/Library/CloudStorage/GoogleDrive-jenny@lemonclean.com.tw/我的雲端硬碟/lemon_Jenny/Jenny@lemon程式/專員班表"
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder-id", required=True, help="日排程系統 Google Drive 根資料夾 ID")
    return parser.parse_args()


def log(msg: str):
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] {msg}")


def get_secret(path_list, env_name=None, required=True, default=None):
    if HAS_STREAMLIT:
        try:
            cur = st.secrets
            for key in path_list:
                cur = cur[key]
            if cur is not None and str(cur) != "":
                return cur
        except Exception:
            pass

    if env_name:
        value = os.getenv(env_name)
        if value not in (None, ""):
            return value

    if required:
        raise RuntimeError(f"讀不到設定值：{'/'.join(path_list)}")

    return default


def load_accounts():
    return [
        (
            "台北",
            get_secret(["accounts", "taipei", "email"], env_name="TAIPEI_EMAIL"),
            get_secret(["accounts", "taipei", "password"], env_name="TAIPEI_PASSWORD"),
        ),
        (
            "台中",
            get_secret(["accounts", "taichung", "email"], env_name="TAICHUNG_EMAIL"),
            get_secret(["accounts", "taichung", "password"], env_name="TAICHUNG_PASSWORD"),
        ),
    ]


def get_service_account_info():
    if HAS_STREAMLIT:
        try:
            creds_dict = dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
            if creds_dict:
                return creds_dict
        except Exception:
            pass

    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        return json.loads(json_str)

    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT")
    if json_str:
        return json.loads(json_str)

    raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定")


def login(email: str, password: str) -> requests.Session:
    session = requests.Session()

    log(f"開始登入：{email}")

    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True, timeout=60)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "html.parser")
    token_input = soup.find("input", {"name": "_token"})

    if not token_input:
        raise RuntimeError("找不到 _token")

    payload = {
        "_token": token_input.get("value"),
        "email": email,
        "password": password,
    }

    login_res = session.post(
        LOGIN_URL,
        data=payload,
        headers=HEADERS,
        allow_redirects=True,
        timeout=60,
    )
    login_res.raise_for_status()

    if "login" in login_res.url.lower():
        raise RuntimeError(f"{email} 登入失敗")

    log(f"登入成功：{email}")
    return session


def get_months():
    now = datetime.now(TZ)

    this_month = now.strftime("%Y-%m")
    this_file_date = now.strftime("%Y%m%d")

    if now.month == 12:
        next_year = now.year + 1
        next_month_num = 1
    else:
        next_year = now.year
        next_month_num = now.month + 1

    next_month = f"{next_year}-{next_month_num:02d}"
    next_file_date = f"{next_year}{next_month_num:02d}{now.day:02d}"

    return this_month, next_month, this_file_date, next_file_date


def get_drive_service():
    try:
        creds = service_account.Credentials.from_service_account_info(
            get_service_account_info(),
            scopes=GDRIVE_SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        log("Google Drive 初始化成功")
        return service

    except Exception as e:
        raise RuntimeError(f"Google Drive 初始化失敗：{e}")


def q_escape(value: str) -> str:
    return value.replace("'", "\\'")


def find_child_folder(service, parent_id: str, folder_name: str):
    q = (
        f"'{q_escape(parent_id)}' in parents and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"name='{q_escape(folder_name)}' and "
        f"trashed=false"
    )

    res = service.files().list(
        q=q,
        fields="files(id,name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()

    files = res.get("files", [])
    return files[0]["id"] if files else None


def create_child_folder(service, parent_id: str, folder_name: str):
    body = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }

    created = service.files().create(
        body=body,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()

    log(f"📁 已建立資料夾：{created['name']}")
    return created["id"]


def get_or_create_child_folder(service, parent_id: str, folder_name: str):
    folder_id = find_child_folder(service, parent_id, folder_name)

    if folder_id:
        log(f"📁 使用既有資料夾：{folder_name}")
        return folder_id

    return create_child_folder(service, parent_id, folder_name)


def find_files_in_folder(service, parent_id: str, filename: str):
    q = (
        f"'{q_escape(parent_id)}' in parents and "
        f"name='{q_escape(filename)}' and "
        f"trashed=false"
    )

    res = service.files().list(
        q=q,
        fields="files(id,name,webViewLink,modifiedTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        pageSize=100,
        orderBy="modifiedTime desc",
    ).execute()

    return res.get("files", [])


def trash_duplicate_files(service, files: list[dict], keep_file_id: str) -> None:
    for file in files:
        file_id = file.get("id")
        if not file_id or file_id == keep_file_id:
            continue

        try:
            service.files().update(
                fileId=file_id,
                body={"trashed": True},
                supportsAllDrives=True,
            ).execute()
            log(f"🗑️ 已移除重複舊檔：{file.get('name')} (file_id={file_id})")
        except Exception as exc:
            log(f"⚠️ 移除重複檔失敗：{file.get('name')} / {exc}")


def upload_to_gdrive(local_path: str, folder_id: str):
    service = get_drive_service()
    filename = os.path.basename(local_path)

    media = MediaFileUpload(
        local_path,
        resumable=True,
    )

    log(f"準備上傳到 Google Drive：{filename}")

    existing_files = find_files_in_folder(service, folder_id, filename)

    if existing_files:
        keep = existing_files[0]

        updated = service.files().update(
            fileId=keep["id"],
            media_body=media,
            fields="id,name,webViewLink,modifiedTime",
            supportsAllDrives=True,
        ).execute()

        trash_duplicate_files(service, existing_files, updated["id"])

        log(
            f"♻️ 已覆蓋同名檔：{updated['name']} "
            f"(file_id={updated['id']}) "
            f"{updated.get('webViewLink', keep.get('webViewLink', ''))}"
        )
        return updated["id"]

    file_metadata = {
        "name": filename,
        "parents": [folder_id],
    }

    created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,webViewLink,modifiedTime",
        supportsAllDrives=True,
    ).execute()

    log(
        f"☁️ 已上傳新檔：{created['name']} "
        f"(file_id={created['id']}) "
        f"{created.get('webViewLink', '')}"
    )
    return created["id"]

def can_use_local_output_dir() -> bool:
    try:
        users_dir = Path("/Users")
        jenny_dir = Path("/Users/jenny")
        return users_dir.exists() and jenny_dir.exists()
    except Exception:
        return False


def save_to_local_if_possible(temp_file_path: str, filename: str):
    if not can_use_local_output_dir():
        log("目前不是 Jenny 本機環境，略過本機存檔")
        return

    try:
        LOCAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        local_file = LOCAL_OUTPUT_DIR / filename
        shutil.copy2(temp_file_path, local_file)
        log(f"💾 已另存本機：{local_file}")
    except Exception as e:
        log(f"⚠️ 本機存檔失敗，已略過：{e}")


def export_cleaner_schedule(
    session: requests.Session,
    month: str,
    city: str,
    filename: str,
    upload_folder_id: str,
):
    url = f"{EXPORT_BASE}?month={month}&limit={API_LIMIT}"
    log(f"開始下載：city={city} / month={month} / filename={filename}")

    res = session.get(url, headers=HEADERS, allow_redirects=True, timeout=120)

    content_type = res.headers.get("Content-Type", "")

    if res.status_code != 200:
        raise RuntimeError(f"{city} 匯出失敗，status={res.status_code}")

    if "excel" not in content_type.lower() and "octet-stream" not in content_type.lower():
        raise RuntimeError(f"{city} 回傳不是 Excel，Content-Type={content_type}")

    with tempfile.TemporaryDirectory() as tmpdir:
        full_path = os.path.join(tmpdir, filename)

        with open(full_path, "wb") as f:
            f.write(res.content)

        log(f"✅ 已下載到暫存：{full_path}")
        log(f"📦 檔案大小：{os.path.getsize(full_path)} bytes")
        log(f"📂 暫存目錄存在：{Path(tmpdir).exists()}")
        log(f"📄 暫存檔案存在：{Path(full_path).exists()}")

        save_to_local_if_possible(full_path, filename)
        upload_to_gdrive(full_path, upload_folder_id)

        log(f"✅ 完成處理：{filename}")


def main():
    args = parse_args()

    log("====================================================")
    log("staff_schedule.py 開始執行")
    log(f"GITHUB_ACTIONS = {os.getenv('GITHUB_ACTIONS')}")
    log(f"PWD = {os.getcwd()}")
    log(f"LOCAL_OUTPUT_DIR = {LOCAL_OUTPUT_DIR}")
    log(f"LOCAL_OUTPUT_DIR exists = {LOCAL_OUTPUT_DIR.exists()}")
    log(f"can_use_local_output_dir = {can_use_local_output_dir()}")
    log(f"root folder_id = {args.folder_id}")
    log("====================================================")

    service = get_drive_service()
    upload_folder_id = get_or_create_child_folder(
        service,
        args.folder_id,
        "專員班表",
    )

    this_month, next_month, this_date, next_date = get_months()

    log(f"this_month = {this_month}")
    log(f"next_month = {next_month}")
    log(f"this_date = {this_date}")
    log(f"next_date = {next_date}")
    log(f"專員班表 folder_id = {upload_folder_id}")

    regions = load_accounts()
    log(f"帳號數量 = {len(regions)}")

    for city, email, password in regions:
        log("")
        log(f"=== 開始處理 {city} ===")

        try:
            session = login(email, password)

            current_filename = f"{this_date}專員班表-{city}.xls"
            next_filename = f"{next_date}專員班表-{city}.xls"

            export_cleaner_schedule(session, this_month, city, current_filename, upload_folder_id)
            export_cleaner_schedule(session, next_month, city, next_filename, upload_folder_id)

            log(f"✅ {city} 全部完成")

        except Exception as e:
            log(f"❌ {city} 失敗：{e}")
            raise

    log("🎉 staff_schedule.py 全部完成")


if __name__ == "__main__":
    main()
