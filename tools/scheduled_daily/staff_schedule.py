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

# Google Drive
GDRIVE_FOLDER_ID = "10__ajnbpu2oabAVUG_u3RAHK2a2vcgj2"
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# 台北時區
TZ = timezone(timedelta(hours=8))

# 本機輸出資料夾（只有本機跑時才會用）
LOCAL_OUTPUT_DIR = Path(
    "/Users/jenny/Library/CloudStorage/GoogleDrive-jenny@lemonclean.com.tw/我的雲端硬碟/lemon_Jenny/Jenny@lemon程式/專員班表"
)


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
    # 1. Streamlit secrets
    if HAS_STREAMLIT:
        try:
            creds_dict = dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
            if creds_dict:
                return creds_dict
        except Exception:
            pass

    # 2. 舊環境變數名稱
    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        return json.loads(json_str)

    # 3. GitHub Actions 目前使用的名稱
    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT")
    if json_str:
        return json.loads(json_str)

    raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定")


def login(email: str, password: str) -> requests.Session:
    session = requests.Session()

    log(f"開始登入：{email}")

    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True, timeout=60)
    soup = BeautifulSoup(res.text, "html.parser")

    token_input = soup.find("input", {"name": "_token"})
    if not token_input:
        raise RuntimeError("找不到 _token")

    token = token_input.get("value")

    payload = {
        "_token": token,
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
        creds_dict = get_service_account_info()

        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=GDRIVE_SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        log("Google Drive 初始化成功")
        return service

    except Exception as e:
        raise RuntimeError(f"Google Drive 初始化失敗：{e}")


def upload_to_gdrive(local_path: str):
    service = get_drive_service()
    filename = os.path.basename(local_path)

    file_metadata = {
        "name": filename,
        "parents": [GDRIVE_FOLDER_ID],
    }

    media = MediaFileUpload(
        local_path,
        mimetype="application/vnd.ms-excel",
        resumable=True,
    )

    log(f"準備上傳到 Google Drive：{filename}")

    created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()

    log(f"☁️ 已上傳：{created['name']} (file_id={created['id']})")
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


def export_cleaner_schedule(session: requests.Session, month: str, city: str, filename: str):
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
        upload_to_gdrive(full_path)

        log(f"✅ 完成處理：{filename}")


def main():
    log("====================================================")
    log("staff_schedule.py 開始執行")
    log(f"GITHUB_ACTIONS = {os.getenv('GITHUB_ACTIONS')}")
    log(f"PWD = {os.getcwd()}")
    log(f"LOCAL_OUTPUT_DIR = {LOCAL_OUTPUT_DIR}")
    log(f"LOCAL_OUTPUT_DIR exists = {LOCAL_OUTPUT_DIR.exists()}")
    log(f"can_use_local_output_dir = {can_use_local_output_dir()}")
    log("====================================================")

    this_month, next_month, this_date, next_date = get_months()
    log(f"this_month = {this_month}")
    log(f"next_month = {next_month}")
    log(f"this_date = {this_date}")
    log(f"next_date = {next_date}")

    regions = load_accounts()
    log(f"帳號數量 = {len(regions)}")

    for city, email, password in regions:
        log("")
        log(f"=== 開始處理 {city} ===")

        try:
            session = login(email, password)

            current_filename = f"{this_date}專員班表-{city}.xls"
            next_filename = f"{next_date}專員班表-{city}.xls"

            export_cleaner_schedule(session, this_month, city, current_filename)
            export_cleaner_schedule(session, next_month, city, next_filename)

            log(f"✅ {city} 全部完成")

        except Exception as e:
            log(f"❌ {city} 失敗：{e}")
            raise

    log("🎉 staff_schedule.py 全部完成")


if __name__ == "__main__":
    main()
