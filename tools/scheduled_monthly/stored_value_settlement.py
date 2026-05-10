import os
import tempfile
from datetime import datetime, timezone, timedelta

import requests
import streamlit as st
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

LOGIN_URL = "https://backend.lemonclean.com.tw/login"
EXPORT_URL = "https://backend.lemonclean.com.tw/member/export_stored_value"

HEADERS = {"User-Agent": "Mozilla/5.0"}

TZ = timezone(timedelta(hours=8))
ROOT_FOLDER_ID = "填你的儲值金主資料夾ID"


def load_accounts():
    return st.secrets["accounts"]


def get_drive():
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"]),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def login(session, email, password):
    res = session.get(LOGIN_URL)
    soup = BeautifulSoup(res.text, "html.parser")
    token = soup.find("input", {"name": "_token"}).get("value")

    session.post(LOGIN_URL, data={
        "_token": token,
        "email": email,
        "password": password,
    })

    print(f"✅ 登入成功：{email}")


def get_prev_month():
    now = datetime.now(TZ)
    y, m = now.year, now.month

    if m == 1:
        y -= 1
        m = 12
    else:
        m -= 1

    return f"{y}{m:02d}"


def get_or_create_folder(service, parent, name):
    q = f"name='{name}' and '{parent}' in parents and trashed=false"
    res = service.files().list(q=q, fields="files(id,name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    folder = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]}
    ).execute()
    return folder["id"]


def upload(path, folder_id):
    drive = get_drive()
    media = MediaFileUpload(path)
    drive.files().create(
        body={"name": os.path.basename(path), "parents": [folder_id]},
        media_body=media,
    ).execute()


def main():
    prev_ym = get_prev_month()
    accounts = load_accounts()
    drive = get_drive()

    month_folder = get_or_create_folder(drive, ROOT_FOLDER_ID, prev_ym)

    for city, acc in accounts.items():
        session = requests.Session()
        print(f"\n=== {city} ===")

        login(session, acc["email"], acc["password"])

        res = session.get(EXPORT_URL)
        filename = f"{prev_ym}儲值金結算-{city}.xlsx"

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, filename)
            open(path, "wb").write(res.content)

            upload(path, month_folder)
            print(f"☁️ 上傳 {filename}")


if __name__ == "__main__":
    main()
