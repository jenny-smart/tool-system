import json
import os
import calendar
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
EXPORT_URL = "https://backend.lemonclean.com.tw/purchase/export_order"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
}

# Google Drive（訂單資料資料夾）
GDRIVE_FOLDER_ID = "1QnOJzn-xmZ_oAMoiM6Qnfk3Y2CWuM1c4"
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# 台北時區
TZ = timezone(timedelta(hours=8))

# 本機輸出資料夾（只有本機跑時才會用）
LOCAL_OUTPUT_DIR = Path(
    "/Users/jenny/Library/CloudStorage/GoogleDrive-jenny@lemonclean.com.tw/我的雲端硬碟/lemon_Jenny/Jenny@lemon程式/訂單資料"
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


def login(email: str, password: str) -> requests.Session:
    session = requests.Session()

    log(f"開始登入：{email}")

    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True, timeout=60)
    soup = BeautifulSoup(res.text, "html.parser")

    token_input = soup.find("input", {"name": "_token"})
    if not token_input:
        raise RuntimeError("找不到 _token，無法登入")

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


def get_date_range():
    now = datetime.now(TZ)

    start = f"{now.year}-{now.month:02d}-01"

    if now.month == 12:
        ny, nm = now.year + 1, 1
    else:
        ny, nm = now.year, now.month + 1

    last_day = calendar.monthrange(ny, nm)[1]
    end = f"{ny}-{nm:02d}-{last_day:02d}"

    file_date = now.strftime("%Y%m%d")

    return start, end, file_date


def build_export_url(start, end):
    params = {
        "clean_date_s": start,
        "clean_date_e": end,
        "purchase_status": "1",
    }

    return requests.Request("GET", EXPORT_URL, params=params).prepare().url


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


def export_order(session: requests.Session, city: str, export_url: str, file_date: str):
    log(f"開始下載：city={city} / export_url={export_url}")

    res = session.get(export_url, headers=HEADERS, allow_redirects=True, timeout=120)

    if res.status_code != 200:
        raise RuntimeError(f"{city} 匯出失敗，status={res.status_code}")

    content_type = res.headers.get("Content-Type", "")
    if "excel" not in content_type.lower() and "octet-stream" not in content_type.lower():
        raise RuntimeError(f"{city} 不是 Excel，Content-Type={content_type}")

    filename = f"{file_date}訂單-{city}.xls"

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, filename)

        with open(path, "wb") as f:
            f.write(res.content)

        log(f"✅ 已下載到暫存：{path}")
        log(f"📦 檔案大小：{os.path.getsize(path)} bytes")
        log(f"📂 暫存目錄存在：{Path(tmpdir).exists()}")
        log(f"📄 暫存檔案存在：{Path(path).exists()}")

        save_to_local_if_possible(path, filename)
        upload_to_gdrive(path)

        log(f"✅ 完成處理：{filename}")


def main():
    log("====================================================")
    log("orders_report.py 開始執行")
    log(f"GITHUB_ACTIONS = {os.getenv('GITHUB_ACTIONS')}")
    log(f"PWD = {os.getcwd()}")
    log(f"LOCAL_OUTPUT_DIR = {LOCAL_OUTPUT_DIR}")
    log(f"LOCAL_OUTPUT_DIR exists = {LOCAL_OUTPUT_DIR.exists()}")
    log(f"can_use_local_output_dir = {can_use_local_output_dir()}")
    log("====================================================")

    start, end, file_date = get_date_range()
    export_url = build_export_url(start, end)

    log(f"服務日期：{start} ~ {end}")
    log(f"file_date = {file_date}")

    regions = load_accounts()
    log(f"帳號數量 = {len(regions)}")

    for city, email, password in regions:
        log("")
        log(f"=== 開始處理 {city} ===")

        try:
            session = login(email, password)
            export_order(session, city, export_url, file_date)
            log(f"✅ {city} 完成")

        except Exception as e:
            log(f"❌ {city} 失敗：{e}")
            raise

    log("🎉 orders_report.py 全部完成")


if __name__ == "__main__":
    main()
