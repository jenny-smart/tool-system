import os
import calendar
import tempfile
from datetime import datetime, timezone, timedelta

import requests
import streamlit as st
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

LOGIN_URL = "https://backend.lemonclean.com.tw/login"
EXPORT_URL = "https://backend.lemonclean.com.tw/purchase/export_order"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
}

TZ = timezone(timedelta(hours=8))
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

ROOT_FOLDER_ID = "填你的儲值金主資料夾ID"


def load_accounts():
    return {
        "台北": st.secrets["accounts"]["taipei"],
        "台中": st.secrets["accounts"]["taichung"],
        "桃園": st.secrets["accounts"]["taoyuan"],
        "新竹": st.secrets["accounts"]["hsinchu"],
        "高雄": st.secrets["accounts"]["kaohsiung"],
    }


def get_drive():
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"]),
        scopes=GDRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=creds)


def login(session, email, password):
    res = session.get(LOGIN_URL, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")
    token = soup.find("input", {"name": "_token"}).get("value")

    session.post(LOGIN_URL, data={
        "_token": token,
        "email": email,
        "password": password,
    }, headers=HEADERS)

    print(f"✅ 登入成功：{email}")


def get_date():
    now = datetime.now(TZ)
    y, m = now.year, now.month

    if m == 1:
        py, pm = y - 1, 12
    else:
        py, pm = y, m - 1

    prev_ym = f"{py}{pm:02d}"

    paid_s = f"{py}-{pm:02d}-01"
    paid_e = f"{py}-{pm:02d}-{calendar.monthrange(py, pm)[1]:02d}"

    clean_s = f"{y}-{m:02d}-01"

    ey, em = y, m + 4
    while em > 12:
        em -= 12
        ey += 1

    clean_e = f"{ey}-{em:02d}-{calendar.monthrange(ey, em)[1]:02d}"

    return prev_ym, paid_s, paid_e, clean_s, clean_e


def build_url(keyword, paid_s, paid_e, clean_s, clean_e):
    params = {
        "keyword": keyword,
        "paid_at_s": paid_s,
        "paid_at_e": paid_e,
        "clean_date_s": clean_s,
        "clean_date_e": clean_e,
        "purchase_status": "1",
        "payway": "4",
        "p_board": "on",
    }
    return requests.Request("GET", EXPORT_URL, params=params).prepare().url


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
    prev_ym, paid_s, paid_e, clean_s, clean_e = get_date()
    accounts = load_accounts()
    drive = get_drive()

    month_folder = get_or_create_folder(drive, ROOT_FOLDER_ID, prev_ym)

    for city, acc in accounts.items():
        session = requests.Session()
        print(f"\n=== {city} ===")

        login(session, acc["email"], acc["password"])

        keyword = "新竹" if city == "新竹" else ""
        url = build_url(keyword, paid_s, paid_e, clean_s, clean_e)

        res = session.get(url, headers=HEADERS)
        filename = f"{prev_ym}儲值金預收-{city}.xlsx"

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, filename)
            open(path, "wb").write(res.content)

            upload(path, month_folder)
            print(f"☁️ 上傳 {filename}")


if __name__ == "__main__":
    main()
