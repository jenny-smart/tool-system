import os
import sys
import calendar
import tempfile
from io import BytesIO
from datetime import datetime, timezone, timedelta

import pandas as pd
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

# 👉 這裡填「上下半月訂單」主資料夾 ID
ROOT_FOLDER_ID = "1VCb_y-zBA7tm9SF1s7GeixZVweWteWIc"


def load_accounts():
    return {
        "台北": {
            "email": st.secrets["accounts"]["taipei"]["email"],
            "password": st.secrets["accounts"]["taipei"]["password"],
            "folder": "01.台北專員",
        },
        "台中": {
            "email": st.secrets["accounts"]["taichung"]["email"],
            "password": st.secrets["accounts"]["taichung"]["password"],
            "folder": "02.台中專員",
        },
        "桃園": {
            "email": st.secrets["accounts"]["taoyuan"]["email"],
            "password": st.secrets["accounts"]["taoyuan"]["password"],
            "folder": "03.桃園專員",
        },
        "新竹": {
            "email": st.secrets["accounts"]["hsinchu"]["email"],
            "password": st.secrets["accounts"]["hsinchu"]["password"],
            "folder": "04.新竹專員",
        },
        "高雄": {
            "email": st.secrets["accounts"]["kaohsiung"]["email"],
            "password": st.secrets["accounts"]["kaohsiung"]["password"],
            "folder": "05.高雄專員",
        },
    }


def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"]),
        scopes=GDRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=creds)


def login(session, email, password):
    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    soup = BeautifulSoup(res.text, "html.parser")
    token = soup.find("input", {"name": "_token"}).get("value")

    payload = {
        "_token": token,
        "email": email,
        "password": password,
    }

    res = session.post(LOGIN_URL, data=payload, headers=HEADERS, allow_redirects=True)
    if "login" in res.url.lower():
        raise RuntimeError(f"{email} 登入失敗")

    print(f"✅ 登入成功：{email}")


def get_half():
    if len(sys.argv) >= 2 and sys.argv[1] in ("1", "2"):
        return sys.argv[1]

    now = datetime.now(TZ)
    return "1" if now.day <= 15 else "2"


def get_dates(half):
    now = datetime.now(TZ)
    year = now.year
    month = now.month
    yyyymm = f"{year}{month:02d}"

    if half == "1":
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-15"
        tag = f"{yyyymm}-1"
    else:
        last_day = calendar.monthrange(year, month)[1]
        start = f"{year}-{month:02d}-16"
        end = f"{year}-{month:02d}-{last_day:02d}"
        tag = f"{yyyymm}-2"

    return start, end, tag


def build_export_url(start, end, keyword=""):
    params = {
        "keyword": keyword,
        "clean_date_s": start,
        "clean_date_e": end,
        "p_board": "on",
        "purchase_status": "1",
    }
    req = requests.Request("GET", EXPORT_URL, params=params).prepare()
    return req.url


def read_excel_from_response(content: bytes) -> pd.DataFrame:
    return pd.read_excel(BytesIO(content), engine="openpyxl")


def download_single_export(session, start, end, keyword):
    export_url = build_export_url(start, end, keyword)
    res = session.get(export_url, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()

    content_type = res.headers.get("Content-Type", "").lower()
    if "excel" not in content_type and "spreadsheet" not in content_type and "octet-stream" not in content_type:
        raise RuntimeError(f"不是 Excel，Content-Type={content_type}")

    return res.content


def find_child_folder(service, parent_id, folder_name):
    q = (
        f"name='{folder_name}' and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"'{parent_id}' in parents and trashed=false"
    )
    res = service.files().list(
        q=q,
        fields="files(id,name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def create_child_folder(service, parent_id, folder_name):
    body = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    res = service.files().create(
        body=body,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()
    return res["id"]


def get_or_create_child_folder(service, parent_id, folder_name):
    folder_id = find_child_folder(service, parent_id, folder_name)
    if folder_id:
        return folder_id
    return create_child_folder(service, parent_id, folder_name)


def upload_to_gdrive(local_path, parent_folder_id):
    service = get_drive_service()
    filename = os.path.basename(local_path)

    media = MediaFileUpload(local_path, resumable=True)
    body = {
        "name": filename,
        "parents": [parent_folder_id],
    }

    created = service.files().create(
        body=body,
        media_body=media,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()

    print(f"☁️ 已上傳：{created['name']}")
    return created["id"]


def export_kaohsiung(session, start, end, temp_dir, tag):
    df_list = []

    for region in ["高雄", "台南"]:
        try:
            print(f"👉 抓 {region}")
            content = download_single_export(session, start, end, region)
            df = read_excel_from_response(content)
            df_list.append(df)

            single_path = os.path.join(temp_dir, f"{tag}訂單-{region}.xlsx")
            df.to_excel(single_path, index=False)
            print(f"✅ 已下載：{single_path}")

        except Exception as e:
            print(f"⚠️ {region} 失敗：{e}")

    if not df_list:
        raise RuntimeError("高雄 / 台南 都沒有資料")

    merged_df = pd.concat(df_list, ignore_index=True).drop_duplicates()

    final_path = os.path.join(temp_dir, f"{tag}訂單-高雄.xlsx")
    merged_df.to_excel(final_path, index=False)

    print(f"✅ 高雄合併完成：{final_path}")
    return final_path


def main():
    half = get_half()
    start, end, tag = get_dates(half)

    print(f"📌 期別：{tag}")
    print(f"📌 日期：{start} ~ {end}")

    accounts = load_accounts()
    service = get_drive_service()

    for city in ["台北", "台中", "桃園", "新竹", "高雄"]:
        acc = accounts[city]
        session = requests.Session()

        try:
            print(f"\n=== 處理 {city} ===")
            login(session, acc["email"], acc["password"])

            # 建立雲端資料夾：各區名稱 / 期別
            city_folder_id = get_or_create_child_folder(service, ROOT_FOLDER_ID, acc["folder"])
            tag_folder_id = get_or_create_child_folder(service, city_folder_id, tag)

            with tempfile.TemporaryDirectory() as temp_dir:
                if city == "高雄":
                    final_path = export_kaohsiung(session, start, end, temp_dir, tag)
                    upload_to_gdrive(final_path, tag_folder_id)
                    continue

                keyword = "新竹" if city == "新竹" else ""
                content = download_single_export(session, start, end, keyword)

                local_path = os.path.join(temp_dir, f"{tag}訂單-{city}.xlsx")
                with open(local_path, "wb") as f:
                    f.write(content)

                print(f"✅ 已下載：{local_path}")
                upload_to_gdrive(local_path, tag_folder_id)

        except Exception as e:
            print(f"❌ {city} 失敗：{e}")
            raise


if __name__ == "__main__":
    main()
