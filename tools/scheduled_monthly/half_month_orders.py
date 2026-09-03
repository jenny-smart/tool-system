from __future__ import annotations

"""上下半月訂單下載與上傳。

新竹、高雄的原訂單需用地址關鍵字搜尋，因此另外依付款日期抓取已付款儲值金，
保留「原訂單」「儲值金」兩份來源檔，再產出合併後的「訂單」檔。
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import calendar
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from tools.common.config_loader import load_monthly_config

try:
    from tools.common.log_to_sheet import log_to_sheet
except Exception as e:
    print(f"[debug] import error: {e}", flush=True)
    try:
        from ..common.log_to_sheet import log_to_sheet
    except Exception as e2:
        print(f"[debug] relative import error: {e2}", flush=True)
        log_to_sheet = None

LOGIN_URL = "https://backend.lemonclean.com.tw/login"
EXPORT_URL = "https://backend.lemonclean.com.tw/purchase/export_order"
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"}
TZ = timezone(timedelta(hours=8))
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
KAOHSIUNG_MERGE_REGIONS = ["高雄", "台南"]
STORED_VALUE_MERGE_CITIES = {"新竹", "高雄"}
AREA_FOLDER_NAMES = {
    "台北": "01.台北專員", "台中": "02.台中專員", "桃園": "03.桃園專員",
    "新竹": "04.新竹專員", "高雄": "05.高雄專員",
}
AREA_ALIASES = {v: k for k, v in AREA_FOLDER_NAMES.items()}


@dataclass
class RunArgs:
    half: str | None
    period: str | None
    start: str | None
    end: str | None
    area: str
    folder_id: str
    snapshot_dir: str
    skip_snapshot: bool


def log(message: str) -> None:
    print(message, flush=True)


def tw_now() -> datetime:
    return datetime.now(TZ)


def write_monthly_log(*, function_name: str, area: str, period: str, date_text: str,
                      target: str = "", source_file: str = "", status: str,
                      message: str, traceback_text: str = "") -> None:
    if log_to_sheet is None:
        return
    try:
        log_to_sheet(
            system="月排程系統", function=function_name,
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            area=area, period=period, date=date_text, target=target,
            source_file=source_file, status=status, message=message,
            traceback_text=traceback_text,
        )
        log("✅ 已寫入月排程 Log")
    except Exception as exc:
        log(f"⚠️ 寫入月排程 Log 失敗：{exc}")


def normalize_area(area: str | None) -> str:
    value = str(area or "all").strip()
    if value in {"", "全區", "全部", "ALL", "All", "all"}:
        return "all"
    return AREA_ALIASES.get(value, value)


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
        "台北": {"email": secret_value(["accounts", "taipei", "email"], env_value("TAIPEI_EMAIL")), "password": secret_value(["accounts", "taipei", "password"], env_value("TAIPEI_PASSWORD"))},
        "台中": {"email": secret_value(["accounts", "taichung", "email"], env_value("TAICHUNG_EMAIL")), "password": secret_value(["accounts", "taichung", "password"], env_value("TAICHUNG_PASSWORD"))},
        "桃園": {"email": secret_value(["accounts", "taoyuan", "email"], env_value("TAOYUAN_EMAIL")), "password": secret_value(["accounts", "taoyuan", "password"], env_value("TAOYUAN_PASSWORD"))},
        "新竹": {"email": secret_value(["accounts", "hsinchu", "email"], env_value("HSINCHU_EMAIL")), "password": secret_value(["accounts", "hsinchu", "password"], env_value("HSINCHU_PASSWORD"))},
        "高雄": {"email": secret_value(["accounts", "kaohsiung", "email"], env_value("KAOHSIUNG_EMAIL", env_value("HSINCHU_EMAIL"))), "password": secret_value(["accounts", "kaohsiung", "password"], env_value("KAOHSIUNG_PASSWORD", env_value("HSINCHU_PASSWORD")))},
    }


def parse_args() -> RunArgs:
    parser = argparse.ArgumentParser(description="上下半月訂單下載與上傳")
    parser.add_argument("legacy_half", nargs="?", choices=["1", "2"])
    parser.add_argument("--half", choices=["1", "2"], default=None)
    parser.add_argument("--period", default="")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--area", default=os.getenv("TARGET_AREA", "all"))
    parser.add_argument("--folder-id", default="")
    parser.add_argument("--snapshot-dir", default="snapshots/monthly_orders")
    parser.add_argument("--skip-snapshot", action="store_true")
    args = parser.parse_args()
    cfg = load_monthly_config()
    return RunArgs(
        half=args.half or args.legacy_half,
        period=args.period.strip() or None,
        start=args.start.strip() or None,
        end=args.end.strip() or None,
        area=normalize_area(args.area),
        folder_id=args.folder_id.strip() or cfg["root_folder_id"],
        snapshot_dir=args.snapshot_dir.strip() or "snapshots/monthly_orders",
        skip_snapshot=bool(args.skip_snapshot),
    )


def get_service_account_info() -> dict[str, Any]:
    for env_name in ("GOOGLE_SERVICE_ACCOUNT", "GOOGLE_SERVICE_ACCOUNT_JSON"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            return json.loads(raw)
    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
    except Exception as exc:
        raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定") from exc


def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(get_service_account_info(), scopes=GDRIVE_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def login(session: requests.Session, email: str, password: str) -> None:
    if not email or not password:
        raise RuntimeError("帳號或密碼未設定")
    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()
    token_input = BeautifulSoup(res.text, "html.parser").find("input", {"name": "_token"})
    if token_input is None:
        raise RuntimeError("登入頁面找不到 _token")
    res = session.post(LOGIN_URL, data={"_token": token_input.get("value"), "email": email, "password": password}, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()
    if "login" in res.url.lower():
        raise RuntimeError(f"{email} 登入失敗")
    log(f"✅ 登入成功：{email}")


def period_to_dates(period: str) -> tuple[str, str, str]:
    if "-" not in period:
        raise RuntimeError("期別格式錯誤，應為 202605-1 或 202605-2")
    yyyymm, half = period.split("-", 1)
    if len(yyyymm) != 6 or not yyyymm.isdigit() or half not in {"1", "2"}:
        raise RuntimeError("期別格式錯誤，應為 202605-1 或 202605-2")
    year, month = int(yyyymm[:4]), int(yyyymm[4:6])
    if half == "1":
        return f"{year}-{month:02d}-01", f"{year}-{month:02d}-15", period
    return f"{year}-{month:02d}-16", f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}", period


def resolve_dates(args: RunArgs) -> tuple[str, str, str]:
    if args.start and args.end:
        tag = args.period or f"{datetime.strptime(args.start, '%Y-%m-%d').strftime('%Y%m%d')}-{datetime.strptime(args.end, '%Y-%m-%d').strftime('%Y%m%d')}"
        return args.start, args.end, tag
    if args.period:
        return period_to_dates(args.period)
    now = tw_now()
    return period_to_dates(f"{now.year}{now.month:02d}-{args.half or ('1' if now.day <= 15 else '2')}")


def build_export_url(start: str, end: str, keyword: str = "", *, stored_value: bool = False) -> str:
    params = {
        "keyword": keyword, "name": "", "phone": "", "orderNo": "",
        "date_s": "", "date_e": "",
        "clean_date_s": "" if stored_value else start,
        "clean_date_e": "" if stored_value else end,
        "paid_at_s": start if stored_value else "",
        "paid_at_e": end if stored_value else "",
        "refundDateS": "", "refundDateE": "",
        "buy": "5" if stored_value else "",
        "area_id": "", "isCharge": "", "isRefund": "", "payway": "",
        "purchase_status": "1", "progress_status": "", "invoiceStatus": "",
        "otherFee": "", "orderBy": "", "p_board": "on",
    }
    return requests.Request("GET", EXPORT_URL, params=params).prepare().url


def assert_excel_content(content: bytes, content_type: str) -> None:
    if content[:2] == b"PK" or content[:4] == b"\xd0\xcf\x11\xe0":
        return
    lower_type = (content_type or "").lower()
    if any(x in lower_type for x in ("excel", "spreadsheet", "octet-stream")):
        return
    preview = content[:200].decode("utf-8", errors="ignore").replace("\n", " ")
    raise RuntimeError(f"不是 Excel，Content-Type={content_type}，內容預覽={preview}")


def download_export(session: requests.Session, start: str, end: str, keyword: str = "", *, stored_value: bool = False) -> bytes:
    res = session.get(build_export_url(start, end, keyword, stored_value=stored_value), headers=HEADERS, allow_redirects=True)
    res.raise_for_status()
    assert_excel_content(res.content, res.headers.get("Content-Type", ""))
    return res.content


def read_excel(content: bytes) -> pd.DataFrame:
    if content[:2] == b"PK":
        return pd.read_excel(BytesIO(content), engine="openpyxl")
    if content[:4] == b"\xd0\xcf\x11\xe0":
        try:
            return pd.read_excel(BytesIO(content), engine="xlrd")
        except Exception:
            return pd.read_excel(BytesIO(content), engine="calamine")
    return pd.read_excel(BytesIO(content), engine="openpyxl")


def write_excel_content(content: bytes, path: str) -> pd.DataFrame:
    df = read_excel(content)
    df.to_excel(path, index=False)
    return df


def q_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_child_folders(service, parent_id: str, folder_name: str) -> list[dict[str, Any]]:
    q = f"name='{q_escape(folder_name)}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    return service.files().list(q=q, fields="files(id,name,createdTime)", supportsAllDrives=True, includeItemsFromAllDrives=True, orderBy="createdTime").execute().get("files", [])


def delete_drive_file(service, file_id: str, name: str = "") -> bool:
    try:
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        log(f"🗑️ 已刪除舊項目：{name or file_id}")
        return True
    except HttpError as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            log(f"⚠️ 舊項目不存在，略過：{name or file_id}")
            return False
        raise


def get_or_create_single_child_folder(service, parent_id: str, folder_name: str) -> str:
    folders = list_child_folders(service, parent_id, folder_name)
    if folders:
        keep = folders[0]
        for duplicate in folders[1:]:
            delete_drive_file(service, duplicate["id"], duplicate.get("name", folder_name))
        log(f"📁 使用既有資料夾：{folder_name} / {keep['id']}")
        return keep["id"]
    created = service.files().create(body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}, fields="id,name", supportsAllDrives=True).execute()
    log(f"📁 已建立資料夾：{folder_name} / {created['id']}")
    return created["id"]


def resolve_area_folder(service, root_folder_id: str, city: str) -> str:
    folder_name = AREA_FOLDER_NAMES.get(city)
    if not folder_name:
        raise RuntimeError(f"找不到地區資料夾名稱設定：{city}")
    folder_id = get_or_create_single_child_folder(service, root_folder_id, folder_name)
    log(f"📁 區域資料夾：{city} / {folder_name} / {folder_id}")
    return folder_id


def list_files_in_folder(service, parent_folder_id: str, filename: str) -> list[dict[str, Any]]:
    q = f"name='{q_escape(filename)}' and '{parent_folder_id}' in parents and trashed=false"
    return service.files().list(q=q, fields="files(id,name,webViewLink,mimeType,createdTime)", supportsAllDrives=True, includeItemsFromAllDrives=True, orderBy="createdTime", pageSize=100).execute().get("files", [])


def upload_to_gdrive(service, local_path: str, parent_folder_id: str) -> str:
    filename = os.path.basename(local_path)
    for existing in list_files_in_folder(service, parent_folder_id, filename):
        delete_drive_file(service, existing["id"], existing.get("name", filename))
    created = service.files().create(
        body={"name": filename, "parents": [parent_folder_id]},
        media_body=MediaFileUpload(local_path, resumable=True),
        fields="id,name,webViewLink", supportsAllDrives=True,
    ).execute()
    log(f"☁️ 已上傳新檔：{created['name']} → folder_id={parent_folder_id} {created.get('webViewLink', '')}".strip())
    return created["id"]


def export_original(session: requests.Session, city: str, start: str, end: str) -> pd.DataFrame:
    if city == "高雄":
        frames: list[pd.DataFrame] = []
        for region in KAOHSIUNG_MERGE_REGIONS:
            try:
                df = read_excel(download_export(session, start, end, region))
                if not df.empty:
                    frames.append(df)
                    log(f"✅ {region} 原訂單抓到 {len(df)} 筆")
            except Exception as exc:
                log(f"⚠️ {region} 原訂單略過：{exc}")
        return pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame()
    keyword = "新竹" if city == "新竹" else ""
    return read_excel(download_export(session, start, end, keyword))


def save_snapshot(local_path: str, snapshot_root: str, tag: str, meta: dict[str, Any]) -> None:
    snapshot_dir = Path(snapshot_root) / tag
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    target = snapshot_dir / Path(local_path).name
    shutil.copy2(local_path, target)
    target.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"🧾 已更新 GitHub snapshot：{target}")


def persist_file(service, path: str, folder_id: str, args: RunArgs, tag: str, city: str, start: str, end: str, kind: str) -> None:
    upload_to_gdrive(service, path, folder_id)
    if not args.skip_snapshot:
        save_snapshot(path, args.snapshot_dir, tag, {
            "city": city, "tag": tag, "start": start, "end": end, "kind": kind,
            "root_folder_id": args.folder_id, "tag_folder_id": folder_id,
            "generated_at": tw_now().strftime("%Y-%m-%d %H:%M:%S"),
        })


def process_city(city: str, args: RunArgs, accounts: dict[str, dict[str, str]], service, start: str, end: str, tag: str) -> None:
    session = requests.Session()
    acc = accounts[city]
    log(f"\n=== 處理 {city} ===")
    login(session, acc["email"], acc["password"])
    area_folder_id = resolve_area_folder(service, args.folder_id, city)
    tag_folder_id = get_or_create_single_child_folder(service, area_folder_id, tag)
    log(f"📁 期別資料夾：{tag} / {tag_folder_id}")

    status, message, source_files = "失敗", "", []
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_df = export_original(session, city, start, end)

            if city in STORED_VALUE_MERGE_CITIES:
                original_path = os.path.join(temp_dir, f"{tag}原訂單-{city}.xlsx")
                original_df.to_excel(original_path, index=False)
                persist_file(service, original_path, tag_folder_id, args, tag, city, start, end, "原訂單")
                source_files.append(os.path.basename(original_path))

                stored_df = read_excel(download_export(session, start, end, stored_value=True))
                stored_path = os.path.join(temp_dir, f"{tag}儲值金-{city}.xlsx")
                stored_df.to_excel(stored_path, index=False)
                persist_file(service, stored_path, tag_folder_id, args, tag, city, start, end, "儲值金")
                source_files.append(os.path.basename(stored_path))
                log(f"✅ {city} 儲值金抓到 {len(stored_df)} 筆（付款日期 {start} ~ {end} / 儲值金 / 已付款）")

                frames = [df for df in (original_df, stored_df) if not df.empty]
                merged_df = pd.concat(frames, ignore_index=True).drop_duplicates() if frames else pd.DataFrame(columns=original_df.columns)
                final_path = os.path.join(temp_dir, f"{tag}訂單-{city}.xlsx")
                merged_df.to_excel(final_path, index=False)
                log(f"✅ {city} 合併完成：原訂單 {len(original_df)} + 儲值金 {len(stored_df)} → 合併 {len(merged_df)} 筆")
            else:
                if original_df.empty:
                    status, message = "成功", "本期無資料，略過上傳"
                    return
                final_path = os.path.join(temp_dir, f"{tag}訂單-{city}.xlsx")
                original_df.to_excel(final_path, index=False)

            persist_file(service, final_path, tag_folder_id, args, tag, city, start, end, "訂單")
            source_files.append(os.path.basename(final_path))
            status = "成功"
            message = "已上傳：" + "、".join(source_files)
    except Exception as exc:
        message = str(exc)
        raise
    finally:
        write_monthly_log(
            function_name="上下半月訂單", area=city, period=tag,
            date_text=f"{start} ~ {end}", target=f"folder_id={tag_folder_id}",
            source_file="、".join(source_files), status=status, message=message,
        )


def resolve_cities(args: RunArgs, accounts: dict[str, dict[str, str]]) -> list[str]:
    if args.area == "all":
        return [city for city in ["台北", "台中", "桃園", "新竹", "高雄"] if city in accounts]
    if args.area not in accounts:
        raise RuntimeError(f"找不到地區帳號設定：{args.area}")
    return [args.area]


def main() -> None:
    args = parse_args()
    start, end, tag = resolve_dates(args)
    log(f"📌 期別：{tag}")
    log(f"📌 日期：{start} ~ {end}")
    log(f"📌 執行區域：{args.area}")
    log(f"📌 月排程總根目錄：{args.folder_id}")
    accounts = load_accounts()
    service = get_drive_service()
    failed: list[tuple[str, str]] = []
    succeeded: list[str] = []
    for city in resolve_cities(args, accounts):
        try:
            process_city(city, args, accounts, service, start, end, tag)
            succeeded.append(city)
        except Exception as exc:
            log(f"❌ {city} 失敗：{exc}")
            failed.append((city, str(exc)))
            if args.area != "all":
                raise
    log(f"\n✅ 成功地區：{', '.join(succeeded) if succeeded else '無'}")
    if failed:
        for city, message in failed:
            log(f"- {city}: {message}")
        raise RuntimeError(f"上下半月訂單有失敗地區：{failed}")
    log("🎉 half_month_orders.py 全部完成")


if __name__ == "__main__":
    main()
