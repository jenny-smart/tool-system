from __future__ import annotations

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
from googleapiclient.http import MediaFileUpload

try:
    from tools.common.log_to_sheet import log_to_sheet
except Exception as e:
    print(f"[debug] import error: {e}", flush=True)
    try:
        from ..common.log_to_sheet import log_to_sheet
    except Exception as e2:
        print(f"[debug] relative import error: {e2}", flush=True)
        log_to_sheet = None


def _load_log_spreadsheet_id() -> None:
    if os.getenv("TOOLS_APP_LOG_SPREADSHEET_ID"):
        return

    try:
        import yaml

        candidates = [
            Path(__file__).resolve().parents[2] / "config" / "systems.yaml",
            Path(__file__).resolve().parents[2] / "systems.yaml",
        ]

        for cfg_path in candidates:
            if not cfg_path.exists():
                continue

            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

            log_id = str(cfg.get("log_spreadsheet_id", "")).strip()

            if log_id:
                os.environ["TOOLS_APP_LOG_SPREADSHEET_ID"] = log_id
                print(f"[debug] loaded log_spreadsheet_id from {cfg_path}")
                return

    except Exception as e:
        print(f"[debug] _load_log_spreadsheet_id error: {e}", flush=True)

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
        _load_log_spreadsheet_id()
        run_type = "排程" if os.getenv("GITHUB_ACTIONS") else "手動"
        log_to_sheet(
            system="月排程系統",
            function=function_name,
            run_type=run_type,
            area=area,
            period=period,
            date=date_text,
            target=target,
            source_file=source_file,
            status=status,
            message=message,
            traceback_text=traceback_text,
        )

        print("✅ 已寫入月排程 Log", flush=True)


LOGIN_URL = "https://backend.lemonclean.com.tw/login"
EXPORT_URL = "https://backend.lemonclean.com.tw/purchase/export_order"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
}

TZ = timezone(timedelta(hours=8))
GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

DEFAULT_ROOT_FOLDER_ID = "1VCb_y-zBA7tm9SF1s7GeixZVweWteWIc"

AREA_FOLDER_NAMES = {
    "台北": "01.台北專員",
    "台中": "02.台中專員",
    "桃園": "03.桃園專員",
    "新竹": "04.新竹專員",
    "高雄": "05.高雄專員",
}

KAOHSIUNG_MERGE_REGIONS = ["高雄", "台南"]


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


def normalize_area(area: str | None) -> str:
    value = str(area or "all").strip()
    if value in ["", "全區", "全部", "ALL", "All", "all"]:
        return "all"
    return value


def parse_args() -> RunArgs:
    parser = argparse.ArgumentParser(description="上下半月訂單下載與上傳")

    parser.add_argument(
        "legacy_half",
        nargs="?",
        choices=["1", "2"],
        help="舊版相容參數：1=上半月、2=下半月",
    )

    parser.add_argument("--half", choices=["1", "2"], default=None)
    parser.add_argument("--period", default="", help="例如：202605-1 或 202605-2")
    parser.add_argument("--start", default="", help="日期區間開始，例如：2026-05-01")
    parser.add_argument("--end", default="", help="日期區間結束，例如：2026-05-15")
    parser.add_argument("--area", default=os.getenv("TARGET_AREA", "all"), help="地區，例如：台北 / 台中 / all")
    parser.add_argument("--folder-id", default="", help="月排程總根目錄 ID")
    parser.add_argument("--snapshot-dir", default="snapshots/monthly_orders")
    parser.add_argument("--skip-snapshot", action="store_true")

    args = parser.parse_args()
    half = args.half or args.legacy_half

    root_folder_id = (
        args.folder_id.strip()
        or os.getenv("MONTHLY_ORDERS_ROOT_FOLDER_ID", "").strip()
        or DEFAULT_ROOT_FOLDER_ID
    )

    return RunArgs(
        half=half,
        period=args.period.strip() or None,
        start=args.start.strip() or None,
        end=args.end.strip() or None,
        area=normalize_area(args.area),
        folder_id=root_folder_id,
        snapshot_dir=args.snapshot_dir.strip() or "snapshots/monthly_orders",
        skip_snapshot=bool(args.skip_snapshot),
    )


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
    """讀取各區帳號（新北已移除）"""
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
            "email": secret_value(["accounts", "kaohsiung", "email"], env_value("KAOHSIUNG_EMAIL", env_value("HSINCHU_EMAIL"))),
            "password": secret_value(["accounts", "kaohsiung", "password"], env_value("KAOHSIUNG_PASSWORD", env_value("HSINCHU_PASSWORD"))),
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

    token = token_input.get("value")

    payload = {
        "_token": token,
        "email": email,
        "password": password,
    }

    res = session.post(LOGIN_URL, data=payload, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()

    if "login" in res.url.lower():
        raise RuntimeError(f"{email} 登入失敗")

    log(f"✅ 登入成功：{email}")


def period_to_dates(period: str) -> tuple[str, str, str]:
    if "-" not in period:
        raise RuntimeError("期別格式錯誤，應為 202605-1 或 202605-2")

    yyyymm, half = period.split("-", 1)

    if len(yyyymm) != 6 or half not in ["1", "2"]:
        raise RuntimeError("期別格式錯誤，應為 202605-1 或 202605-2")

    year = int(yyyymm[:4])
    month = int(yyyymm[4:6])

    if half == "1":
        return f"{year}-{month:02d}-01", f"{year}-{month:02d}-15", period

    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-16", f"{year}-{month:02d}-{last_day:02d}", period


def half_to_dates(half: str | None) -> tuple[str, str, str]:
    now = tw_now()
    selected_half = half or ("1" if now.day <= 15 else "2")
    yyyymm = f"{now.year}{now.month:02d}"
    return period_to_dates(f"{yyyymm}-{selected_half}")


def date_range_to_tag(start: str, end: str) -> str:
    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d")
    return f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"


def resolve_dates(args: RunArgs) -> tuple[str, str, str]:
    if args.start and args.end:
        return args.start, args.end, args.period or date_range_to_tag(args.start, args.end)

    if args.period:
        return period_to_dates(args.period)

    return half_to_dates(args.half)


def build_export_url(start: str, end: str, keyword: str = "") -> str:
    params = {
        "keyword": keyword,
        "name": "",
        "phone": "",
        "orderNo": "",
        "date_s": "",
        "date_e": "",
        "clean_date_s": start,
        "clean_date_e": end,
        "paid_at_s": "",
        "paid_at_e": "",
        "refundDateS": "",
        "refundDateE": "",
        "buy": "",
        "area_id": "",
        "isCharge": "",
        "isRefund": "",
        "payway": "",
        "purchase_status": "1",
        "progress_status": "",
        "invoiceStatus": "",
        "otherFee": "",
        "orderBy": "",
        "p_board": "on",
    }
    req = requests.Request("GET", EXPORT_URL, params=params).prepare()
    return req.url


def assert_excel_content(content: bytes, content_type: str) -> None:
    if content[:2] == b"PK":
        return
    if content[:4] == b"\xd0\xcf\x11\xe0":
        return
    lower_type = (content_type or "").lower()
    if "excel" in lower_type or "spreadsheet" in lower_type or "octet-stream" in lower_type:
        return
    preview = content[:200].decode("utf-8", errors="ignore").replace("\n", " ")
    raise RuntimeError(f"不是 Excel，Content-Type={content_type}，內容預覽={preview}")


def download_single_export(session: requests.Session, start: str, end: str, keyword: str) -> bytes:
    export_url = build_export_url(start, end, keyword)
    res = session.get(export_url, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()
    content_type = res.headers.get("Content-Type", "")
    assert_excel_content(res.content, content_type)
    return res.content


def read_excel_from_response(content: bytes) -> pd.DataFrame:
    bio = BytesIO(content)
    if content[:2] == b"PK":
        return pd.read_excel(bio, engine="openpyxl")
    if content[:4] == b"\xd0\xcf\x11\xe0":
        try:
            return pd.read_excel(BytesIO(content), engine="xlrd")
        except Exception:
            return pd.read_excel(BytesIO(content), engine="calamine")
    return pd.read_excel(bio, engine="openpyxl")


def escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_child_folder(service, parent_id: str, folder_name: str) -> str | None:
    escaped_name = escape_drive_query_value(folder_name)
    q = (
        f"name='{escaped_name}' and "
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


def create_child_folder(service, parent_id: str, folder_name: str) -> str:
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


def get_or_create_child_folder(service, parent_id: str, folder_name: str) -> str:
    folder_id = find_child_folder(service, parent_id, folder_name)
    if folder_id:
        return folder_id
    return create_child_folder(service, parent_id, folder_name)


def resolve_area_folder(service, root_folder_id: str, city: str) -> str:
    folder_name = AREA_FOLDER_NAMES.get(city, city)
    folder_id = get_or_create_child_folder(service, root_folder_id, folder_name)
    log(f"📁 區域資料夾：{folder_name} / {folder_id}")
    return folder_id


def find_file_in_folder(service, parent_folder_id: str, filename: str) -> dict[str, Any] | None:
    escaped_name = escape_drive_query_value(filename)
    q = (
        f"name='{escaped_name}' and "
        f"'{parent_folder_id}' in parents and "
        f"trashed=false"
    )
    res = service.files().list(
        q=q,
        fields="files(id,name,webViewLink,mimeType)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        pageSize=10,
    ).execute()
    files = res.get("files", [])
    return files[0] if files else None


def upload_to_gdrive(service, local_path: str, parent_folder_id: str) -> str:
    filename = os.path.basename(local_path)
    media = MediaFileUpload(local_path, resumable=True)
    existing = find_file_in_folder(service, parent_folder_id, filename)

    if existing:
        updated = service.files().update(
            fileId=existing["id"],
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
        link = updated.get("webViewLink", existing.get("webViewLink", ""))
        log(f"♻️ 已覆蓋舊檔：{updated['name']} → folder_id={parent_folder_id} {link}".strip())
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


def export_kaohsiung(
    session: requests.Session,
    start: str,
    end: str,
    temp_dir: str,
    tag: str,
) -> str | None:
    df_list: list[pd.DataFrame] = []

    for region in KAOHSIUNG_MERGE_REGIONS:
        try:
            log(f"👉 抓 {region}")
            content = download_single_export(session, start, end, region)
            df = read_excel_from_response(content)
            if df.empty:
                log(f"ℹ️ {region} 本期無資料，略過")
                continue
            df_list.append(df)
            log(f"✅ {region} 抓到 {len(df)} 筆")
        except Exception as exc:
            log(f"⚠️ {region} 略過：{exc}")

    if not df_list:
        log("ℹ️ 高雄 / 台南 本期均無資料，略過上傳")
        return None

    merged_df = pd.concat(df_list, ignore_index=True).drop_duplicates()
    final_path = os.path.join(temp_dir, f"{tag}訂單-高雄.xlsx")
    merged_df.to_excel(final_path, index=False)
    log(f"✅ 高雄合併完成：{len(merged_df)} 筆 → {final_path}")
    return final_path


def choose_keyword(city: str) -> str:
    if city == "新竹":
        return "新竹"
    return ""


def save_snapshot(
    local_path: str,
    snapshot_root: str,
    tag: str,
    city: str,
    meta: dict[str, Any],
) -> None:
    snapshot_dir = Path(snapshot_root) / tag
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    src = Path(local_path)
    target = snapshot_dir / src.name
    shutil.copy2(src, target)
    meta_path = target.with_suffix(".json")
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"🧾 已更新 GitHub snapshot：{target}")


def resolve_cities(args: RunArgs, accounts: dict[str, dict[str, str]]) -> list[str]:
    if args.area == "all":
        return list(accounts.keys())
    if args.area not in accounts:
        raise RuntimeError(f"找不到地區帳號設定：{args.area}")
    return [args.area]


def process_city(
    city: str,
    args: RunArgs,
    accounts: dict[str, dict[str, str]],
    service,
    start: str,
    end: str,
    tag: str,
) -> None:
    acc = accounts[city]
    session = requests.Session()

    log(f"\n=== 處理 {city} ===")
    login(session, acc["email"], acc["password"])

    area_folder_id = resolve_area_folder(service, args.folder_id, city)
    tag_folder_id = get_or_create_child_folder(service, area_folder_id, tag)
    log(f"📁 期別資料夾：{tag} / {tag_folder_id}")

    status = "失敗"
    message = ""
    final_filename = ""

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            if city == "高雄":
                final_path = export_kaohsiung(session, start, end, temp_dir, tag)
                if final_path is None:
                    status = "成功"
                    message = "本期無資料，略過上傳"
                    return
            else:
                keyword = choose_keyword(city)
                content = download_single_export(session, start, end, keyword)
                final_path = os.path.join(temp_dir, f"{tag}訂單-{city}.xlsx")
                with open(final_path, "wb") as f:
                    f.write(content)
                log(f"✅ 已下載：{final_path}")

            final_filename = os.path.basename(final_path)
            upload_to_gdrive(service, final_path, tag_folder_id)

            if not args.skip_snapshot:
                save_snapshot(
                    final_path,
                    args.snapshot_dir,
                    tag,
                    city,
                    {
                        "city": city,
                        "tag": tag,
                        "start": start,
                        "end": end,
                        "root_folder_id": args.folder_id,
                        "area_folder_id": area_folder_id,
                        "tag_folder_id": tag_folder_id,
                        "generated_at": tw_now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )

            status = "成功"
            message = f"已上傳：{final_filename}"

    except Exception as exc:
        status = "失敗"
        message = str(exc)
        raise

    finally:
        write_monthly_log(
            function_name="上下半月訂單",
            area=city,
            period=tag,
            date_text=f"{start} ~ {end}",
            target=f"folder_id={tag_folder_id}",
            source_file=final_filename,
            status=status,
            message=message,
        )


def main() -> None:
    args = parse_args()
    start, end, tag = resolve_dates(args)

    log(f"📌 期別：{tag}")
    log(f"📌 日期：{start} ~ {end}")
    log(f"📌 執行區域：{args.area}")
    log(f"📌 月排程總根目錄：{args.folder_id}")

    accounts = load_accounts()
    service = get_drive_service()
    cities = resolve_cities(args, accounts)

    failed: list[tuple[str, str]] = []
    succeeded: list[str] = []

    for city in cities:
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
        log("❌ 失敗地區：")
        for city, message in failed:
            log(f"- {city}: {message}")
        raise RuntimeError(f"上下半月訂單有失敗地區：{failed}")

    log("🎉 half_month_orders.py 全部完成")


if __name__ == "__main__":
    main()
