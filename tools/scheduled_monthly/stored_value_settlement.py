from __future__ import annotations

"""
檔案：tools/scheduled_monthly/stored_value_prepaid.py
版本：0704_v3
更新日期：2026-07-04

功能：
- 月排程：儲值金預收
- 修正：上傳時不再「每次新增一個同名檔案」。
- 上傳規則改為：先尋找同名檔案，能更新就更新既有檔案；若有多個可處理同名檔，保留一個並刪除其他重複檔。
- 若舊檔 ID 已失效或無權限，會略過該舊項目，不讓刪除失敗中斷；但不再因此一直新增重複檔。
- 搭配 toolapp_0704_v3.py，可確認畫面選台北時實際傳入 --area 台北。
- 若期別資料夾已存在，直接使用既有資料夾。
- 同一地區、同一期別、同一檔名原則上只保留一個可處理檔案。

存取期間說明：
- 預設不帶 --period / --start / --end：以今天所在月份的「上個月」為作業月份。
  例如今天是 2026-07-04，預設期別為 202606，預收/服務日期為 2026-07-01 ~ 2026-10-30。
- 帶 --period 202606：預收/服務日期為 2026-07-01 ~ 2026-10-30，存入 202606。
- 帶 --start / --end：預收/服務日期使用指定區間，期別以 --period 為主。
"""

import argparse
import calendar
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

try:
    from tools.common.config_loader import load_monthly_config
except Exception:
    load_monthly_config = None

try:
    from tools.common.log_to_sheet import log_to_sheet
except Exception:
    log_to_sheet = None


LOGIN_URL = "https://backend.lemonclean.com.tw/login"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
}
TZ = timezone(timedelta(hours=8))
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

AREA_FOLDER_NAMES = {
    "台北": "01.台北專員",
    "台中": "02.台中專員",
    "桃園": "03.桃園專員",
    "新竹": "04.新竹專員",
    "高雄": "05.高雄專員",
}

AREA_ALIASES = {
    "01.台北專員": "台北",
    "02.台中專員": "台中",
    "03.桃園專員": "桃園",
    "04.新竹專員": "新竹",
    "05.高雄專員": "高雄",
}


@dataclass
class RunArgs:
    period: str | None
    start: str | None
    end: str | None
    area: str
    folder_id: str


def log(message: str) -> None:
    print(message, flush=True)


def tw_now() -> datetime:
    return datetime.now(TZ)


def normalize_area(area: str | None) -> str:
    value = str(area or "all").strip()
    if value in ["", "全區", "全部", "ALL", "All", "all"]:
        return "all"
    return AREA_ALIASES.get(value, value)


def normalize_period(period: str | None) -> str | None:
    value = str(period or "").strip()
    if not value:
        return None
    if "-" in value:
        return value
    if len(value) == 6 and value.isdigit():
        return f"{value}-2"
    return value


def secret_value(path: list[str], default: str = "") -> str:
    try:
        value: Any = st.secrets
        for key in path:
            value = value[key]
        return str(value)
    except Exception:
        return default


def env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_accounts() -> dict[str, dict[str, str]]:
    return {
        "台北": {
            "email": secret_value(["accounts", "taipei", "email"], env_value("TAIPEI_EMAIL")),
            "password": secret_value(["accounts", "taipei", "password"], env_value("TAIPEI_PASSWORD")),
        },
        "台中": {
            "email": secret_value(["accounts", "taichung", "email"], env_value("TAICHUNG_EMAIL")),
            "password": secret_value(["accounts", "taichung", "password"], env_value("TAICHUNG_PASSWORD")),
        },
        "桃園": {
            "email": secret_value(["accounts", "taoyuan", "email"], env_value("TAOYUAN_EMAIL")),
            "password": secret_value(["accounts", "taoyuan", "password"], env_value("TAOYUAN_PASSWORD")),
        },
        "新竹": {
            "email": secret_value(["accounts", "hsinchu", "email"], env_value("HSINCHU_EMAIL")),
            "password": secret_value(["accounts", "hsinchu", "password"], env_value("HSINCHU_PASSWORD")),
        },
        "高雄": {
            "email": secret_value(["accounts", "kaohsiung", "email"], env_value("KAOHSIUNG_EMAIL")),
            "password": secret_value(["accounts", "kaohsiung", "password"], env_value("KAOHSIUNG_PASSWORD")),
        },
    }


def get_service_account_info() -> dict[str, Any]:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT", "").strip()
    if raw:
        return json.loads(raw)

    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        return json.loads(raw_json)

    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))

    try:
        return dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
    except Exception as exc:
        raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定") from exc


def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        get_service_account_info(),
        scopes=GDRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def login(session: requests.Session, email: str, password: str) -> None:
    if not email or not password:
        raise RuntimeError("帳號或密碼未設定")

    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "html.parser")
    token_input = soup.find("input", {"name": "_token"})
    if token_input is None:
        raise RuntimeError("登入頁面找不到 _token")

    payload = {
        "_token": token_input.get("value"),
        "email": email,
        "password": password,
    }

    res = session.post(LOGIN_URL, data=payload, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()

    if "login" in res.url.lower():
        raise RuntimeError(f"{email} 登入失敗")

    log(f"✅ 登入成功：{email}")


def escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_child_folders(service, parent_id: str, folder_name: str) -> list[dict[str, Any]]:
    escaped_name = escape_drive_query_value(folder_name)
    q = (
        f"name='{escaped_name}' and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"'{parent_id}' in parents and trashed=false"
    )
    res = service.files().list(
        q=q,
        fields="files(id,name,createdTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        orderBy="createdTime",
    ).execute()
    return res.get("files", [])


def delete_drive_file(service, file_id: str, name: str = "") -> bool:
    try:
        service.files().delete(
            fileId=file_id,
            supportsAllDrives=True,
        ).execute()
        log(f"🗑️ 已刪除舊項目：{name or file_id}")
        return True
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 404:
            log(f"⚠️ 舊項目不存在，略過刪除：{name or file_id}")
            return False
        raise


def get_or_create_single_child_folder(service, parent_id: str, folder_name: str) -> str:
    folders = list_child_folders(service, parent_id, folder_name)

    if folders:
        keep = folders[0]
        for duplicate in folders[1:]:
            delete_drive_file(
                service,
                duplicate["id"],
                f"{duplicate.get('name', folder_name)} / duplicate folder",
            )
        log(f"📁 使用既有資料夾：{folder_name} / {keep['id']}")
        return keep["id"]

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
    log(f"📁 已建立資料夾：{folder_name} / {res['id']}")
    return res["id"]


def resolve_area_folder(service, root_folder_id: str, city: str) -> str:
    folder_name = AREA_FOLDER_NAMES.get(city)
    if not folder_name:
        raise RuntimeError(f"找不到地區資料夾名稱設定：{city}")

    folder_id = get_or_create_single_child_folder(service, root_folder_id, folder_name)
    log(f"📁 區域資料夾：{city} / {folder_name} / {folder_id}")
    return folder_id


def list_files_in_folder(service, parent_folder_id: str, filename: str) -> list[dict[str, Any]]:
    escaped_name = escape_drive_query_value(filename)
    q = (
        f"name='{escaped_name}' and "
        f"'{parent_folder_id}' in parents and "
        f"trashed=false"
    )
    res = service.files().list(
        q=q,
        fields="files(id,name,webViewLink,mimeType,createdTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        orderBy="createdTime",
        pageSize=100,
    ).execute()
    return res.get("files", [])


def file_is_accessible(service, file_id: str) -> bool:
    try:
        service.files().get(
            fileId=file_id,
            fields="id,name",
            supportsAllDrives=True,
        ).execute()
        return True
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 404:
            return False
        raise


def safe_delete_drive_file(service, file_id: str, name: str = "") -> bool:
    try:
        service.files().delete(
            fileId=file_id,
            supportsAllDrives=True,
        ).execute()
        log(f"🗑️ 已刪除重複舊檔：{name or file_id}")
        return True
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 404:
            log(f"⚠️ 舊項目不存在或無法存取，略過：{name or file_id}")
            return False
        raise


def upload_to_gdrive(service, local_path: str, parent_folder_id: str) -> str:
    filename = os.path.basename(local_path)
    existing_files = list_files_in_folder(service, parent_folder_id, filename)

    accessible_files = []
    inaccessible_files = []

    for file_info in existing_files:
        if file_is_accessible(service, file_info["id"]):
            accessible_files.append(file_info)
        else:
            inaccessible_files.append(file_info)

    for file_info in inaccessible_files:
        log(f"⚠️ 找到同名舊項目但無法存取，略過：{file_info.get('name', filename)} / {file_info['id']}")

    media = MediaFileUpload(local_path, resumable=True)

    if accessible_files:
        keep = accessible_files[0]

        updated = service.files().update(
            fileId=keep["id"],
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()

        for duplicate in accessible_files[1:]:
            safe_delete_drive_file(
                service,
                duplicate["id"],
                duplicate.get("name", filename),
            )

        link = updated.get("webViewLink", keep.get("webViewLink", ""))
        log(f"♻️ 已更新既有檔案：{updated['name']} → folder_id={parent_folder_id} {link}".strip())

        remaining = list_files_in_folder(service, parent_folder_id, filename)
        log(f"📌 同名檔檢查：{filename} / Drive 查詢筆數={len(remaining)}")
        return updated["id"]

    body = {
        "name": filename,
        "parents": [parent_folder_id],
    }

    created = service.files().create(
        body=body,
        media_body=media,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()

    link = created.get("webViewLink", "")
    log(f"☁️ 已上傳新檔：{created['name']} → folder_id={parent_folder_id} {link}".strip())
    return created["id"]


def is_html_response(content: bytes) -> bool:
    return "<html" in content[:300].decode("utf-8", errors="ignore").lower()


def assert_download_file(content: bytes, content_type: str = "") -> None:
    if is_html_response(content):
        preview = content[:200].decode("utf-8", errors="ignore").replace("\n", " ")
        raise RuntimeError(f"回傳 HTML（登入過期或查無資料），內容預覽={preview}")


def read_excel_from_bytes(content: bytes) -> pd.DataFrame:
    bio = BytesIO(content)
    return pd.read_excel(bio, engine="openpyxl")


def resolve_cities(args: RunArgs, accounts: dict[str, dict[str, str]]) -> list[str]:
    if args.area == "all":
        preferred_order = ["台北", "台中", "桃園", "新竹", "高雄"]
        return [city for city in preferred_order if city in accounts]

    if args.area not in accounts:
        raise RuntimeError(f"找不到地區帳號設定：{args.area}")

    return [args.area]


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    while month > 12:
        month -= 12
        year += 1
    while month <= 0:
        month += 12
        year -= 1
    return year, month


def parse_yyyymm(value: str) -> tuple[int, int]:
    if len(value) != 6 or not value.isdigit():
        raise RuntimeError(f"月份格式錯誤，應為 YYYYMM：{value}")
    return int(value[:4]), int(value[4:6])


def period_month(period: str | None) -> tuple[int, int] | None:
    if not period:
        return None
    yyyymm = str(period).split("-", 1)[0]
    return parse_yyyymm(yyyymm)


def default_prev_month_period() -> tuple[str, int, int]:
    now = tw_now()
    py, pm = previous_month(now.year, now.month)
    return f"{py}{pm:02d}-2", py, pm


def root_folder_from_args_or_config(folder_id: str) -> str:
    if folder_id:
        return folder_id

    if load_monthly_config is not None:
        try:
            cfg = load_monthly_config()
            return str(cfg.get("root_folder_id") or cfg.get("folder_id") or "").strip()
        except Exception:
            pass

    return os.getenv("MONTHLY_ROOT_FOLDER_ID", "").strip()


def write_monthly_log(
    *,
    function_name: str,
    area: str,
    period: str,
    date_text: str,
    target: str = "",
    source_file: str = "",
    status: str,
    message: str,
    traceback_text: str = "",
) -> None:
    if log_to_sheet is None:
        return

    try:
        log_to_sheet(
            system="月排程系統",
            function=function_name,
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            area=area,
            period=period,
            date=date_text,
            target=target,
            source_file=source_file,
            status=status,
            message=message,
            traceback_text=traceback_text,
        )
        log("✅ 已寫入月排程 Log")
    except Exception as exc:
        log(f"⚠️ 寫入月排程 Log 失敗：{exc}")


def parse_common_args(description: str) -> RunArgs:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--period", default="", help="例如：202606-2")
    parser.add_argument("--start", default="", help="查詢開始日，例如：2026-06-01")
    parser.add_argument("--end", default="", help="查詢結束日，例如：2026-06-30")
    parser.add_argument("--area", default=os.getenv("TARGET_AREA", "all"), help="台北 / 台中 / 桃園 / 新竹 / 高雄 / all")
    parser.add_argument("--folder-id", default=os.getenv("MONTHLY_ROOT_FOLDER_ID", ""), help="月排程總根目錄 ID")

    # 舊版相容：python xxx.py 202606
    parser.add_argument("legacy_month", nargs="?", default="")

    args = parser.parse_args()

    period = normalize_period(args.period.strip())
    if not period and args.legacy_month.strip():
        legacy = args.legacy_month.strip()
        period = normalize_period(legacy)

    root_folder_id = root_folder_from_args_or_config(args.folder_id.strip())
    if not root_folder_id:
        raise RuntimeError("缺少月排程總根目錄 ID，請提供 --folder-id 或 MONTHLY_ROOT_FOLDER_ID")

    return RunArgs(
        period=period or None,
        start=args.start.strip() or None,
        end=args.end.strip() or None,
        area=normalize_area(args.area),
        folder_id=root_folder_id,
    )


EXPORT_URL = "https://backend.lemonclean.com.tw/purchase/export_order"
FUNCTION_NAME = "儲值金預收"


def resolve_ranges(args: RunArgs) -> dict[str, str]:
    if args.period:
        year, month = period_month(args.period)
        folder_tag = args.period
    else:
        now = tw_now()
        year, month = previous_month(now.year, now.month)
        folder_tag = f"{year}{month:02d}"

    service_year, service_month = add_months(year, month, 1)
    default_start = f"{service_year}-{service_month:02d}-01"

    end_year, end_month = add_months(year, month, 4)
    default_end = f"{end_year}-{end_month:02d}-30"

    clean_start = args.start or default_start
    clean_end = args.end or default_end

    return {
        "folder_tag": folder_tag,
        "paid_at_s": "",
        "paid_at_e": "",
        "clean_date_s": clean_start,
        "clean_date_e": clean_end,
        "date_text": f"預收/服務：{clean_start} ~ {clean_end}",
    }


def build_url(keyword: str, rng: dict[str, str]) -> str:
    params = {
        "keyword": keyword,
        "paid_at_s": rng["paid_at_s"],
        "paid_at_e": rng["paid_at_e"],
        "clean_date_s": rng["clean_date_s"],
        "clean_date_e": rng["clean_date_e"],
        "purchase_status": "1",
        "payway": "4",
        "p_board": "on",
    }
    return requests.Request("GET", EXPORT_URL, params=params).prepare().url


def download_export(session: requests.Session, keyword: str, rng: dict[str, str]) -> bytes:
    url = build_url(keyword, rng)
    log(f"🔄 下載 {keyword or '全部'}")
    res = session.get(url, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()
    assert_download_file(res.content, res.headers.get("Content-Type", ""))
    return res.content


def choose_keyword(city: str) -> str:
    return "新竹" if city == "新竹" else ""


def process_city(city: str, args: RunArgs, accounts: dict[str, dict[str, str]], service, rng: dict[str, str]) -> None:
    acc = accounts[city]
    session = requests.Session()
    tag = rng["folder_tag"]

    status = "失敗"
    message = ""
    filename = ""
    tag_folder_id = ""

    try:
        log(f"\n=== 處理 {city} ===")
        login(session, acc["email"], acc["password"])

        area_folder_id = resolve_area_folder(service, args.folder_id, city)
        tag_folder_id = get_or_create_single_child_folder(service, area_folder_id, tag)

        with tempfile.TemporaryDirectory() as temp_dir:
            keyword = choose_keyword(city)
            filename = f"{tag}儲值金預收-{city}.xlsx"
            path = os.path.join(temp_dir, filename)
            content = download_export(session, keyword, rng)
            with open(path, "wb") as f:
                f.write(content)

            upload_to_gdrive(service, path, tag_folder_id)

        status = "成功"
        message = f"已上傳：{filename}"

    except Exception as exc:
        message = str(exc)
        raise

    finally:
        write_monthly_log(
            function_name=FUNCTION_NAME,
            area=city,
            period=tag,
            date_text=rng["date_text"],
            target=f"folder_id={tag_folder_id}",
            source_file=filename,
            status=status,
            message=message,
        )


def main() -> None:
    args = parse_common_args("月排程：儲值金預收")
    rng = resolve_ranges(args)

    log(f"📌 功能：{FUNCTION_NAME}")
    log("📌 版本：0704_v3")
    log(f"📌 期別：{rng['folder_tag']}")
    log(f"📌 存取期間：{rng['date_text']}")
    log(f"📌 執行區域：{args.area}")
    log(f"📌 月排程總根目錄：{args.folder_id}")

    accounts = load_accounts()
    service = get_drive_service()
    cities = resolve_cities(args, accounts)

    failed = []
    succeeded = []

    for city in cities:
        try:
            process_city(city, args, accounts, service, rng)
            succeeded.append(city)
        except Exception as exc:
            log(f"❌ {city} 失敗：{exc}")
            failed.append((city, str(exc)))
            if args.area != "all":
                raise

    log(f"\n✅ 成功地區：{', '.join(succeeded) if succeeded else '無'}")
    if failed:
        raise RuntimeError(f"{FUNCTION_NAME} 有失敗地區：{failed}")

    log(f"🎉 {FUNCTION_NAME} 全部完成")


if __name__ == "__main__":
    main()
