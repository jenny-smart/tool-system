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

# 👉 請填「已退款」主資料夾 ID
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


def get_target_month():
    if len(sys.argv) >= 2:
        ym = sys.argv[1]
        year = int(ym[:4])
        month = int(ym[4:6])
    else:
        now = datetime.now(TZ)
        year = now.year
        month = now.month

    return year, month


def get_dates_and_tag():
    year, month = get_target_month()

    yyyymm = f"{year}{month:02d}"
    folder_tag = f"{yyyymm}-2"

    last_day = calendar.monthrange(year, month)[1]
    refund_start = f"{year}-{month:02d}-01"
    refund_end = f"{year}-{month:02d}-{last_day:02d}"

    return folder_tag, refund_start, refund_end


def build_export_url(mode, start, end, keyword=""):
    params = {
        "keyword": keyword,
        "refundDateS": start,
        "refundDateE": end,
        "p_board": "on",
    }

    if mode == "charge":
        params["isCharge"] = "99"
        label = "全部加收"
    else:
        params["isRefund"] = "99"
        label = "全部退款"

    req = requests.Request("GET", EXPORT_URL, params=params).prepare()
    return req.url, label


def is_html(content):
    return "<html" in content[:300].decode("utf-8", errors="ignore").lower()


def download(session, url):
    res = session.get(url, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()

    content = res.content
    if is_html(content):
        raise RuntimeError("回傳 HTML（登入失效或無資料）")

    return content


def save_file(content, path):
    with open(path, "wb") as f:
        f.write(content)


def read_excel_bytes(content):
    return pd.read_excel(BytesIO(content), engine="openpyxl")


def add_type_column(df, mode_label):
    df = df.copy()
    df["類型"] = mode_label
    return df


def export_one(session, temp_dir, folder_tag, city, keyword, mode, start, end):
    url, label = build_export_url(mode, start, end, keyword)
    content = download(session, url)

    name = keyword if keyword else city
    file_name = f"{folder_tag}已退款{label}-{name}.xlsx"
    full_path = os.path.join(temp_dir, file_name)

    try:
        df = read_excel_bytes(content)
        mode_label = "加收" if mode == "charge" else "退款"
        df = add_type_column(df, mode_label)
        df.to_excel(full_path, index=False)
        print(f"✅（可解析）{full_path}")
        return df, full_path

    except Exception:
        save_file(content, full_path)
        print(f"⚠️（原始下載）{full_path}")
        return None, full_path


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


def export_city(session, city, temp_dir, folder_tag, start, end):
    keyword = "新竹" if city == "新竹" else ""
    dfs = []
    files_to_upload = []

    for mode in ["charge", "refund"]:
        df, path = export_one(session, temp_dir, folder_tag, city, keyword, mode, start, end)
        files_to_upload.append(path)
        if df is not None:
            dfs.append(df)

    merged_path = None
    if dfs:
        merged = pd.concat(dfs, ignore_index=True).drop_duplicates()
        merged_path = os.path.join(temp_dir, f"{folder_tag}已退款-{city}.xlsx")
        merged.to_excel(merged_path, index=False)
        print(f"✅ 合併 {merged_path}")
        files_to_upload.append(merged_path)
    else:
        print(f"⚠️ {city} 無法合併（全部為原始檔）")

    return files_to_upload


def export_kaohsiung(session, temp_dir, folder_tag, start, end):
    dfs = []
    files_to_upload = []

    for mode in ["charge", "refund"]:
        for region in ["高雄", "台南"]:
            df, path = export_one(session, temp_dir, folder_tag, "高雄", region, mode, start, end)
            files_to_upload.append(path)
            if df is not None:
                dfs.append(df)

    if dfs:
        merged = pd.concat(dfs, ignore_index=True).drop_duplicates()
        merged_path = os.path.join(temp_dir, f"{folder_tag}已退款-高雄.xlsx")
        merged.to_excel(merged_path, index=False)
        print(f"✅ 高雄合併 {merged_path}")
        files_to_upload.append(merged_path)
    else:
        print("⚠️ 高雄無法合併（全部為原始檔）")

    return files_to_upload


def main():
    folder_tag, start, end = get_dates_and_tag()

    print(f"📅 查詢期間：{start} ~ {end}")
    print(f"📁 期別：{folder_tag}")

    accounts = load_accounts()
    service = get_drive_service()

    for city in ["台北", "台中", "桃園", "新竹", "高雄"]:
        session = requests.Session()
        acc = accounts[city]

        print(f"\n=== {city} ===")

        try:
            login(session, acc["email"], acc["password"])

            city_folder_id = get_or_create_child_folder(service, ROOT_FOLDER_ID, acc["folder"])
            tag_folder_id = get_or_create_child_folder(service, city_folder_id, folder_tag)

            with tempfile.TemporaryDirectory() as temp_dir:
                if city == "高雄":
                    files = export_kaohsiung(session, temp_dir, folder_tag, start, end)
                else:
                    files = export_city(session, city, temp_dir, folder_tag, start, end)

                for path in files:
                    if os.path.exists(path):
                        upload_to_gdrive(path, tag_folder_id)

        except Exception as e:
            print(f"❌ {city} 失敗：{e}")
            raise


if __name__ == "__main__":
    main()
