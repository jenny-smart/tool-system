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

# 預收主資料夾 ID
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


def get_date_ranges():
    now = datetime.now(TZ)
    y, m = now.year, now.month

    # 上月
    if m == 1:
        prev_year = y - 1
        prev_month = 12
    else:
        prev_year = y
        prev_month = m - 1

    prev_ym = f"{prev_year}{prev_month:02d}"
    folder_tag = f"{prev_ym}-2"

    paid_at_s = f"{prev_year}-{prev_month:02d}-01"
    paid_at_e = f"{prev_year}-{prev_month:02d}-{calendar.monthrange(prev_year, prev_month)[1]:02d}"

    # 本月服務起
    clean_date_s = f"{y}-{m:02d}-01"

    # +4 個月月底
    end_year = y
    end_month = m + 4
    while end_month > 12:
        end_month -= 12
        end_year += 1

    clean_date_e = f"{end_year}-{end_month:02d}-{calendar.monthrange(end_year, end_month)[1]:02d}"

    return {
        "folder_tag": folder_tag,
        "paid_at_s": paid_at_s,
        "paid_at_e": paid_at_e,
        "clean_date_s": clean_date_s,
        "clean_date_e": clean_date_e,
    }


def build_export_url(keyword, rng):
    params = {
        "keyword": keyword,
        "paid_at_s": rng["paid_at_s"],
        "paid_at_e": rng["paid_at_e"],
        "clean_date_s": rng["clean_date_s"],
        "clean_date_e": rng["clean_date_e"],
        "purchase_status": "1",
        "p_board": "on",
    }

    req = requests.Request("GET", EXPORT_URL, params=params).prepare()
    return req.url


def is_html_response(content: bytes):
    text = content[:300].decode("utf-8", errors="ignore").lower()
    return "<html" in text


def download_single_export(session, keyword, rng):
    url = build_export_url(keyword, rng)
    print(f"🔄 下載 {keyword or '全部'}")

    res = session.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
    res.raise_for_status()

    content = res.content
    if is_html_response(content):
        raise RuntimeError("回傳 HTML（登入過期或查無資料）")

    return content


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


def export_normal_city(session, city, folder_tag, rng, tag_folder_id):
    keyword = "新竹" if city == "新竹" else ""

    filename = f"{folder_tag}預收-{city}.xlsx"

    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = os.path.join(temp_dir, filename)

        content = download_single_export(session, keyword, rng)

        with open(local_path, "wb") as f:
            f.write(content)

        print(f"✅ 已下載：{local_path}")
        upload_to_gdrive(local_path, tag_folder_id)


def export_kaohsiung(session, folder_tag, rng, tag_folder_id):
    service = get_drive_service()

    for region in ["高雄", "台南"]:
        try:
            filename = f"{folder_tag}預收-{region}.xlsx"

            with tempfile.TemporaryDirectory() as temp_dir:
                local_path = os.path.join(temp_dir, filename)

                print(f"👉 抓 {region}")
                content = download_single_export(session, region, rng)

                with open(local_path, "wb") as f:
                    f.write(content)

                print(f"✅ 已下載：{local_path}")
                upload_to_gdrive(local_path, tag_folder_id)

        except Exception as e:
            print(f"⚠️ {region} 失敗：{e}")


def main():
    rng = get_date_ranges()

    print(f"📅 付款日期：{rng['paid_at_s']} ~ {rng['paid_at_e']}")
    print(f"📅 服務日期：{rng['clean_date_s']} ~ {rng['clean_date_e']}")
    print(f"📁 存檔期別：{rng['folder_tag']}")

    accounts = load_accounts()
    service = get_drive_service()

    for city in ["台北", "台中", "桃園", "新竹", "高雄"]:
        session = requests.Session()
        acc = accounts[city]

        print(f"\n=== {city} ===")

        try:
            login(session, acc["email"], acc["password"])

            city_folder_id = get_or_create_child_folder(service, ROOT_FOLDER_ID, acc["folder"])
            tag_folder_id = get_or_create_child_folder(service, city_folder_id, rng["folder_tag"])

            if city == "高雄":
                export_kaohsiung(session, rng["folder_tag"], rng, tag_folder_id)
            else:
                export_normal_city(session, city, rng["folder_tag"], rng, tag_folder_id)

        except Exception as e:
            print(f"❌ {city} 失敗：{e}")
            raise


if __name__ == "__main__":
    main()
