from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

CONFIG_SHEET = "鯨躍發票根目錄設定"
CONFIG_HEADERS = ["地區", "財務根目錄ID", "承攬費總根目錄ID", "啟用", "備註"]
UPDATE_LOG_SHEET = "鯨躍發票更新Log"
UPDATE_LOG_HEADERS = ["執行時間", "功能", "地區", "期別", "狀態", "筆數", "訊息"]
MONTHLY_ROOT_FOLDER_ID = "1t0B8BdUKBvaS6TM40-hpodUnxeZr3eWe"
DEFAULT_CONFIG = [
    ["台北", "17EQNG8VM9N4YgxXGb23CNZxYT3HsDkQ2", MONTHLY_ROOT_FOLDER_ID, "TRUE", "紙本／中獎存財務路徑"],
    ["台中", "12XktB_jDnd3ufkIZX2hweHdV3nv14tAH", MONTHLY_ROOT_FOLDER_ID, "TRUE", "紙本／中獎存財務路徑"],
    ["桃園", "", MONTHLY_ROOT_FOLDER_ID, "TRUE", "未設財務根目錄時存承攬費路徑"],
    ["新竹", "", MONTHLY_ROOT_FOLDER_ID, "TRUE", "未設財務根目錄時存承攬費路徑"],
    ["高雄", "", MONTHLY_ROOT_FOLDER_ID, "TRUE", "未設財務根目錄時存承攬費路徑"],
]
AREA_FOLDER_NAMES = {
    "台北": "01.台北專員",
    "台中": "02.台中專員",
    "桃園": "03.桃園專員",
    "新竹": "04.新竹專員",
    "高雄": "05.高雄專員",
}
NEW_PERIOD_HEADER = [
    "mail", "invoice number", "發票日期", "order number", "訂單日期",
    "company name", "company number", "買方電話", "買方Email", "買方地址",
    "課稅別", "銷售合計", "營業稅", "總計", "檢查碼", "付款方式", "備註",
    "狀態", "隨機碼", "", "link",
]
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class AreaRootConfig:
    area: str
    finance_root_id: str
    contractor_root_id: str


@dataclass(frozen=True)
class ArchiveFolders:
    full_invoice: str
    paper_invoice: str
    prize_invoice: str
    annual_parent: str
    prize_annual_parent: str


def normalize_folder_id(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"/folders/([\w-]+)", text)
    return match.group(1) if match else text


def bi_month_period(yyyymm: str) -> str:
    year, month = int(yyyymm[:4]), int(yyyymm[4:])
    start = month if month % 2 else month - 1
    return f"{year:04d}{start:02d}{start + 1:02d}"


def previous_prize_period(yyyymm: str) -> str | None:
    year, month = int(yyyymm[:4]), int(yyyymm[4:])
    if month % 2 == 0:
        return None
    if month == 1:
        return f"{year - 1:04d}1112"
    return f"{year:04d}{month - 2:02d}{month - 1:02d}"


def _secret_values() -> dict[str, Any]:
    values: dict[str, Any] = dict(os.environ)
    required = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")

    def find_nested(mapping: Any, target: str) -> str:
        if not hasattr(mapping, "items"):
            return ""
        for key, value in mapping.items():
            if str(key) == target and str(value or "").strip():
                return str(value).strip()
            found = find_nested(value, target)
            if found:
                return found
        return ""

    try:
        import streamlit as st

        for key in required:
            if not str(values.get(key) or "").strip():
                found = find_nested(st.secrets, key)
                if found:
                    values[key] = found
    except Exception:
        pass
    path_text = os.getenv("TOOLS_APP_SECRETS_FILE", "").strip()
    candidates = [Path(path_text).expanduser()] if path_text else []
    candidates.extend([Path.home() / "lemon" / ".streamlit" / "secrets.toml", Path.home() / ".streamlit" / "secrets.toml"])
    for path in candidates:
        if not path.exists():
            continue
        try:
            import toml

            data = toml.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in required:
            if not str(values.get(key) or "").strip():
                found = find_nested(data, key)
                if found:
                    values[key] = found
        break
    return values


def get_google_services() -> tuple[Any, Any]:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    values = _secret_values()
    required = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN")
    missing = [key for key in required if not str(values.get(key) or "").strip()]
    if missing:
        raise RuntimeError("鯨躍發票歸檔缺少 Jenny OAuth：" + ", ".join(missing))
    credentials = Credentials(
        token=None,
        refresh_token=str(values["GOOGLE_OAUTH_REFRESH_TOKEN"]).strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=str(values["GOOGLE_OAUTH_CLIENT_ID"]).strip(),
        client_secret=str(values["GOOGLE_OAUTH_CLIENT_SECRET"]).strip(),
        scopes=["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"],
    )
    return (
        build("drive", "v3", credentials=credentials, cache_discovery=False),
        build("sheets", "v4", credentials=credentials, cache_discovery=False),
    )


def _sheet_id(sheets: Any, spreadsheet_id: str, title: str) -> int | None:
    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))").execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == title:
            return int(props["sheetId"])
    return None


def _ensure_sheet(sheets: Any, spreadsheet_id: str, title: str, *, hidden: bool = False) -> int:
    found = _sheet_id(sheets, spreadsheet_id, title)
    if found is not None:
        return found
    response = sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title, "hidden": hidden, "gridProperties": {"frozenRowCount": 1}}}}]},
    ).execute()
    return int(response["replies"][0]["addSheet"]["properties"]["sheetId"])


def ensure_config_sheet(sheets: Any, spreadsheet_id: str = "") -> dict[str, AreaRootConfig]:
    if not spreadsheet_id:
        from tools.common.config_loader import get_master_spreadsheet_id

        spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(sheets, spreadsheet_id, CONFIG_SHEET)
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{CONFIG_SHEET}'!A:E"
    ).execute().get("values", [])
    if not values:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{CONFIG_SHEET}'!A1",
            valueInputOption="RAW",
            body={"values": [CONFIG_HEADERS, *DEFAULT_CONFIG]},
        ).execute()
        values = [CONFIG_HEADERS, *DEFAULT_CONFIG]
    else:
        existing = {str(row[0]).strip() for row in values[1:] if row}
        missing = [row for row in DEFAULT_CONFIG if row[0] not in existing]
        if missing:
            sheets.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=f"'{CONFIG_SHEET}'!A:E",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": missing},
            ).execute()
            values.extend(missing)
    result: dict[str, AreaRootConfig] = {}
    for row in values[1:]:
        padded = list(row) + [""] * (5 - len(row))
        enabled = str(padded[3]).strip().lower() not in {"false", "0", "停用", "no"}
        area = str(padded[0]).strip()
        if area and enabled:
            result[area] = AreaRootConfig(area, normalize_folder_id(padded[1]), normalize_folder_id(padded[2]))
    return result


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def get_or_create_folder(drive: Any, parent_id: str, name: str) -> str:
    query = f"'{_escape(parent_id)}' in parents and name='{_escape(name)}' and mimeType='{FOLDER_MIME}' and trashed=false"
    files = drive.files().list(q=query, fields="files(id,name)", pageSize=10, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    if files:
        return files[0]["id"]
    created = drive.files().create(
        body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        fields="id,name", supportsAllDrives=True,
    ).execute()
    return created["id"]


def _child_folders_starting_with(drive: Any, parent_id: str, prefix: str) -> list[dict[str, Any]]:
    query = (
        f"'{_escape(parent_id)}' in parents and name contains '{_escape(prefix)}' "
        f"and mimeType='{FOLDER_MIME}' and trashed=false"
    )
    files = drive.files().list(
        q=query, fields="files(id,name)", pageSize=100,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    return [item for item in files if str(item.get("name") or "").startswith(prefix)]


def get_or_create_finance_year_folder(drive: Any, parent_id: str, year: str, area: str) -> str:
    candidates = _child_folders_starting_with(drive, parent_id, year)
    if candidates:
        candidates.sort(
            key=lambda item: (
                area in str(item.get("name") or ""),
                "發票" in str(item.get("name") or ""),
                str(item.get("name") or "") != year,
            ),
            reverse=True,
        )
        return candidates[0]["id"]
    return get_or_create_folder(drive, parent_id, year)


def get_or_create_period_folder(drive: Any, parent_id: str, period4: str) -> str:
    aliases = {period4, f"{period4[:2]}-{period4[2:]}"}
    query = f"'{_escape(parent_id)}' in parents and mimeType='{FOLDER_MIME}' and trashed=false"
    files = drive.files().list(
        q=query, fields="files(id,name)", pageSize=100,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    candidates = [item for item in files if str(item.get("name") or "") in aliases]
    for item in candidates:
        children = drive.files().list(
            q=f"'{_escape(item['id'])}' in parents and trashed=false",
            fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute().get("files", [])
        if children:
            return item["id"]
    if candidates:
        return candidates[0]["id"]
    # 兩種寫法都找不到既有資料夾時，新建的名稱要跟既有慣例（01-02／03-04／
    # 05-06／07-08…）一致，用有連字號的版本，不要用沒有連字號的 period4。
    return get_or_create_folder(drive, parent_id, f"{period4[:2]}-{period4[2:]}")


def resolve_archive_folders(drive: Any, config: AreaRootConfig, yyyymm: str) -> ArchiveFolders:
    year = yyyymm[:4]
    contractor_year = get_or_create_folder(drive, config.contractor_root_id, f"{year}專員承攬服務費")
    area_parent = get_or_create_folder(drive, contractor_year, AREA_FOLDER_NAMES[config.area])
    contractor_period = get_or_create_folder(drive, area_parent, f"{yyyymm}-2")
    if config.finance_root_id:
        finance_year = get_or_create_finance_year_folder(drive, config.finance_root_id, year, config.area)
        finance_pair = get_or_create_period_folder(drive, finance_year, bi_month_period(yyyymm)[4:])
        paper = finance_pair
        annual_parent = finance_year
        prize_period = previous_prize_period(yyyymm)
        if prize_period:
            prize_year = get_or_create_finance_year_folder(
                drive, config.finance_root_id, prize_period[:4], config.area
            )
            prize_folder = get_or_create_period_folder(drive, prize_year, prize_period[4:])
            prize_annual_parent = prize_year
        else:
            prize_folder = finance_pair
            prize_annual_parent = finance_year
    else:
        paper = contractor_period
        annual_parent = area_parent
        prize_folder = contractor_period
        prize_annual_parent = area_parent
    return ArchiveFolders(contractor_period, paper, prize_folder, annual_parent, prize_annual_parent)


def upload_replacing(drive: Any, local_path: Path, folder_id: str) -> str:
    from googleapiclient.http import MediaFileUpload

    name = local_path.name
    query = f"'{_escape(folder_id)}' in parents and name='{_escape(name)}' and trashed=false"
    files = drive.files().list(q=query, fields="files(id,name)", pageSize=100, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    media = MediaFileUpload(str(local_path), resumable=True)
    if files:
        first = files[0]
        drive.files().update(fileId=first["id"], media_body=media, supportsAllDrives=True).execute()
        for duplicate in files[1:]:
            drive.files().delete(fileId=duplicate["id"], supportsAllDrives=True).execute()
        return first["id"]
    return drive.files().create(
        body={"name": name, "parents": [folder_id]}, media_body=media,
        fields="id", supportsAllDrives=True,
    ).execute()["id"]


def _find_or_create_annual_spreadsheet(drive: Any, parent_id: str, year: str, area: str) -> str:
    query = f"'{_escape(parent_id)}' in parents and mimeType='{SHEET_MIME}' and trashed=false"
    files = drive.files().list(q=query, fields="files(id,name)", pageSize=100, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    for item in files:
        name = str(item.get("name") or "")
        if year in name and area in name and "紙本" in name and "中獎" in name:
            return item["id"]
    created = drive.files().create(
        body={"name": f"{year}紙本／中獎發票-{area}", "mimeType": SHEET_MIME, "parents": [parent_id]},
        fields="id,name", supportsAllDrives=True,
    ).execute()
    return created["id"]


def _clean_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y/%m/%d")
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _read_csv(data: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=encoding, dtype=object)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("ZIP 內 CSV 無法判斷編碼")


def _archive_members(path: Path) -> Iterable[tuple[str, bytes]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.endswith("/"):
                    yield name, archive.read(name)
    else:
        yield path.name, path.read_bytes()


def _read_excel(data: bytes, name: str, sheet_name: str | int = 0) -> pd.DataFrame:
    suffix = Path(name).suffix.lower()
    engine = "xlrd" if suffix == ".xls" else "openpyxl"
    return pd.read_excel(io.BytesIO(data), sheet_name=sheet_name, dtype=object, engine=engine)


def paper_rows(path: Path) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for name, data in _archive_members(path):
        suffix = Path(name).suffix.lower()
        if suffix == ".csv":
            frame = _read_csv(data)
        elif suffix in {".xls", ".xlsx"}:
            frame = _read_excel(data, name)
        else:
            continue
        rows.extend([[_clean_value(value) for value in row] for row in frame.values.tolist()])
    return rows


def prize_rows(path: Path) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for name, data in _archive_members(path):
        suffix = Path(name).suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            continue
        try:
            frame = _read_excel(data, name, "發票中獎資料")
        except ValueError:
            continue
        rows.extend([[_clean_value(value) for value in row[:10]] for row in frame.values.tolist()])
    return rows


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _already_imported(sheets: Any, spreadsheet_id: str, kind: str, period: str, checksum: str) -> bool:
    _ensure_sheet(sheets, spreadsheet_id, "_鯨躍匯入Log", hidden=True)
    values = sheets.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range="'_鯨躍匯入Log'!A:E").execute().get("values", [])
    return any(len(row) >= 4 and row[0] == kind and row[1] == period and row[3] == checksum for row in values[1:])


def _write_import_log(sheets: Any, spreadsheet_id: str, kind: str, period: str, path: Path, checksum: str, count: int) -> None:
    values = sheets.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range="'_鯨躍匯入Log'!A1:E1").execute().get("values", [])
    if not values:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range="'_鯨躍匯入Log'!A1", valueInputOption="RAW",
            body={"values": [["類型", "期別", "檔名", "SHA256", "匯入時間／筆數"]]},
        ).execute()
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range="'_鯨躍匯入Log'!A:E", valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [[kind, period, path.name, checksum, f"{datetime.now(TAIPEI_TZ):%Y-%m-%d %H:%M:%S} / {count}"]]},
    ).execute()


def import_paper(sheets: Any, spreadsheet_id: str, period: str, path: Path) -> int:
    checksum = _checksum(path)
    if _already_imported(sheets, spreadsheet_id, "紙本", period, checksum):
        return 0
    rows = paper_rows(path)
    if not rows:
        raise RuntimeError(f"{path.name} 找不到可匯入的紙本發票資料")
    year_title = period[:4]
    year_sheet_id = _ensure_sheet(sheets, spreadsheet_id, year_title)
    current = sheets.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{year_title}'!A:A").execute().get("values", [])
    start_row = len(current) + 1
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    filled = [[bool(row[5]) if len(row) > 5 else False, not bool(row[5]) if len(row) > 5 else True, *row] for row in padded]
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{year_title}'!A{start_row}", valueInputOption="USER_ENTERED", body={"values": filled}
    ).execute()
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"setDataValidation": {"range": {"sheetId": year_sheet_id, "startRowIndex": start_row - 1, "endRowIndex": start_row - 1 + len(filled), "startColumnIndex": 0, "endColumnIndex": 2}, "rule": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True}}}]},
    ).execute()
    selected = [row[:18] + [""] * max(0, 18 - len(row)) for row in padded if len(row) > 5 and bool(row[5])]
    period_sheet_id = _ensure_sheet(sheets, spreadsheet_id, period)
    sheets.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"'{period}'!A:U", body={}).execute()
    output = [NEW_PERIOD_HEADER]
    for index, row in enumerate(selected, start=2):
        output.append([f"=I{index}", *row[:18], "", ""])
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{period}'!A1", valueInputOption="USER_ENTERED", body={"values": output}
    ).execute()
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"updateSheetProperties": {"properties": {"sheetId": period_sheet_id, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}}]},
    ).execute()
    _write_import_log(sheets, spreadsheet_id, "紙本", period, path, checksum, len(rows))
    return len(rows)


def import_prize(sheets: Any, spreadsheet_id: str, period: str, path: Path) -> int:
    checksum = _checksum(path)
    if _already_imported(sheets, spreadsheet_id, "中獎", period, checksum):
        return 0
    rows = prize_rows(path)
    if not rows:
        raise RuntimeError(f"{path.name} 找不到「發票中獎資料」A:J")
    _ensure_sheet(sheets, spreadsheet_id, "中獎發票")
    current = sheets.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range="'中獎發票'!C:C").execute().get("values", [])
    start_row = max(len(current) + 1, 2)
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'中獎發票'!C{start_row}", valueInputOption="USER_ENTERED", body={"values": rows}
    ).execute()
    _write_import_log(sheets, spreadsheet_id, "中獎", period, path, checksum, len(rows))
    return len(rows)


class InvoiceArchiveProcessor:
    def __init__(self) -> None:
        self.drive, self.sheets = get_google_services()
        self.master_spreadsheet_id = _master_spreadsheet_id()
        self.configs = ensure_config_sheet(self.sheets, self.master_spreadsheet_id)

    def _config_and_folders(self, area: str, yyyymm: str) -> tuple[AreaRootConfig, ArchiveFolders]:
        config = self.configs.get(area)
        if not config:
            raise RuntimeError(f"{CONFIG_SHEET} 找不到已啟用的 {area} 設定")
        if not config.contractor_root_id:
            raise RuntimeError(f"{area} 尚未設定承攬費總根目錄ID")
        return config, resolve_archive_folders(self.drive, config, yyyymm)

    def archive_month(self, *, area: str, yyyymm: str, full_path: Path, paper_path: Path, prize_path: Path | None = None) -> None:
        _config, folders = self._config_and_folders(area, yyyymm)
        upload_replacing(self.drive, full_path, folders.full_invoice)
        upload_replacing(self.drive, paper_path, folders.paper_invoice)
        if prize_path is not None:
            upload_replacing(self.drive, prize_path, folders.prize_invoice)

    def _download_named(self, folder_id: str, name: str, destination: Path) -> Path:
        query = f"'{_escape(folder_id)}' in parents and name='{_escape(name)}' and trashed=false"
        files = self.drive.files().list(q=query, fields="files(id,name)", pageSize=10, supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
        if not files:
            raise RuntimeError(f"Google Drive 找不到檔案：{name}")
        request = self.drive.files().get_media(fileId=files[0]["id"], supportsAllDrives=True)
        destination.write_bytes(request.execute())
        return destination

    def update_paper(self, *, area: str, yyyymm: str) -> int:
        _config, folders = self._config_and_folders(area, yyyymm)
        name = f"{yyyymm}紙本發票-{area}.zip"
        with tempfile.TemporaryDirectory(prefix="ei_paper_update_") as temp_dir:
            path = self._download_named(folders.paper_invoice, name, Path(temp_dir) / name)
            spreadsheet_id = _find_or_create_annual_spreadsheet(self.drive, folders.annual_parent, yyyymm[:4], area)
            return import_paper(self.sheets, spreadsheet_id, yyyymm, path)

    def update_prize(self, *, area: str, yyyymm: str) -> int:
        prize_period = previous_prize_period(yyyymm)
        if not prize_period:
            raise RuntimeError("中獎發票只在單月更新；請輸入 01、03、05、07、09 或 11 月")
        _config, folders = self._config_and_folders(area, yyyymm)
        name = f"{prize_period}中獎發票-{area}.zip"
        with tempfile.TemporaryDirectory(prefix="ei_prize_update_") as temp_dir:
            path = self._download_named(folders.prize_invoice, name, Path(temp_dir) / name)
            spreadsheet_id = _find_or_create_annual_spreadsheet(self.drive, folders.prize_annual_parent, prize_period[:4], area)
            return import_prize(self.sheets, spreadsheet_id, prize_period, path)

    def update_prize_period(self, *, area: str, period8: str) -> int:
        if not re.fullmatch(r"\d{8}", period8):
            raise ValueError("中獎期別請輸入 8 碼，例如 20260506")
        start_month, end_month = int(period8[4:6]), int(period8[6:8])
        if start_month not in {1, 3, 5, 7, 9, 11} or end_month != start_month + 1:
            raise ValueError("中獎期別需為 0102、0304、0506、0708、0910 或 1112")
        config = self.configs.get(area)
        if not config:
            raise RuntimeError(f"{CONFIG_SHEET} 找不到已啟用的 {area} 設定")
        name = f"{period8}中獎發票-{area}.zip"
        if config.finance_root_id:
            annual_parent = get_or_create_finance_year_folder(
                self.drive, config.finance_root_id, period8[:4], area
            )
            folder_id = get_or_create_period_folder(self.drive, annual_parent, period8[4:])
            file_id = self._find_named(folder_id, name)
        else:
            if not config.contractor_root_id:
                raise RuntimeError(f"{area} 尚未設定承攬費總根目錄ID")
            contractor_year = get_or_create_folder(
                self.drive, config.contractor_root_id, f"{period8[:4]}專員承攬服務費"
            )
            annual_parent = get_or_create_folder(self.drive, contractor_year, AREA_FOLDER_NAMES[area])
            file_id = self._find_named_anywhere(name)
        with tempfile.TemporaryDirectory(prefix="ei_prize_update_") as temp_dir:
            path = self._download_file(file_id, Path(temp_dir) / name)
            spreadsheet_id = _find_or_create_annual_spreadsheet(
                self.drive, annual_parent, period8[:4], area
            )
            return import_prize(self.sheets, spreadsheet_id, period8, path)

    def _find_named(self, folder_id: str, name: str) -> str:
        query = f"'{_escape(folder_id)}' in parents and name='{_escape(name)}' and trashed=false"
        files = self.drive.files().list(
            q=query, fields="files(id,name)", pageSize=10,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute().get("files", [])
        if not files:
            raise RuntimeError(f"Google Drive 找不到檔案：{name}")
        return files[0]["id"]

    def _find_named_anywhere(self, name: str) -> str:
        query = f"name='{_escape(name)}' and trashed=false"
        files = self.drive.files().list(
            q=query, fields="files(id,name,parents)", pageSize=20,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute().get("files", [])
        if len(files) != 1:
            raise RuntimeError(f"Google Drive 找到 {len(files)} 個同名檔案：{name}，請確認歸檔位置")
        return files[0]["id"]

    def _download_file(self, file_id: str, destination: Path) -> Path:
        request = self.drive.files().get_media(fileId=file_id, supportsAllDrives=True)
        destination.write_bytes(request.execute())
        return destination

    def write_update_log(
        self, *, function_name: str, area: str, period: str, status: str,
        count: int = 0, message: str = "",
    ) -> None:
        _ensure_sheet(self.sheets, self.master_spreadsheet_id, UPDATE_LOG_SHEET)
        current = self.sheets.spreadsheets().values().get(
            spreadsheetId=self.master_spreadsheet_id,
            range=f"'{UPDATE_LOG_SHEET}'!A1:G1",
        ).execute().get("values", [])
        if not current:
            self.sheets.spreadsheets().values().update(
                spreadsheetId=self.master_spreadsheet_id,
                range=f"'{UPDATE_LOG_SHEET}'!A1",
                valueInputOption="RAW",
                body={"values": [UPDATE_LOG_HEADERS]},
            ).execute()
        appended = self.sheets.spreadsheets().values().append(
            spreadsheetId=self.master_spreadsheet_id,
            range=f"'{UPDATE_LOG_SHEET}'!A:G",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[
                datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S"), function_name,
                area, period, status, count, message,
            ]]},
        ).execute()
        updated_range = str(appended.get("updates", {}).get("updatedRange") or "")
        match = re.search(r"!A(\d+):G\d+$", updated_range)
        if match:
            row_index = int(match.group(1)) - 1
            sheet_id = _sheet_id(self.sheets, self.master_spreadsheet_id, UPDATE_LOG_SHEET)
            if sheet_id is not None:
                self.sheets.spreadsheets().batchUpdate(
                    spreadsheetId=self.master_spreadsheet_id,
                    body={"requests": [{"repeatCell": {
                        "range": {
                            "sheetId": sheet_id, "startRowIndex": row_index,
                            "endRowIndex": row_index + 1, "startColumnIndex": 0,
                            "endColumnIndex": len(UPDATE_LOG_HEADERS),
                        },
                        "cell": {"userEnteredFormat": {
                            "backgroundColorStyle": {"rgbColor": {"red": 1, "green": 1, "blue": 1}},
                            "textFormat": {
                                "bold": False,
                                "foregroundColorStyle": {"rgbColor": {"red": 0, "green": 0, "blue": 0}},
                            },
                        }},
                        "fields": "userEnteredFormat(backgroundColorStyle,textFormat)",
                    }}]},
                ).execute()


def _master_spreadsheet_id() -> str:
    from tools.common.config_loader import get_master_spreadsheet_id

    return get_master_spreadsheet_id()


def run_invoice_update(mode: str, period: str, area: str = "全區") -> str:
    period = str(period or "").strip()
    if mode == "paper" and (
        not re.fullmatch(r"\d{6}", period) or not 1 <= int(period[4:]) <= 12
    ):
        raise ValueError("紙本發票月份請輸入 6 碼，例如 202607")
    if mode == "prize" and not re.fullmatch(r"\d{8}", period):
        raise ValueError("中獎發票期別請輸入 8 碼，例如 20260506")
    processor = InvoiceArchiveProcessor()
    areas = list(processor.configs) if area in {"", "全區", "all"} else [area]
    label = "紙本發票更新" if mode == "paper" else "中獎發票更新"
    failures: list[str] = []
    total = 0
    for area_name in areas:
        try:
            count = (
                processor.update_paper(area=area_name, yyyymm=period)
                if mode == "paper"
                else processor.update_prize_period(area=area_name, period8=period)
            )
            total += count
            processor.write_update_log(
                function_name=label, area=area_name, period=period,
                status="成功", count=count, message="更新完成",
            )
        except Exception as exc:
            failures.append(f"{area_name}: {exc}")
            processor.write_update_log(
                function_name=label, area=area_name, period=period,
                status="失敗", message=str(exc),
            )
    if failures:
        raise RuntimeError("；".join(failures))
    return f"{label}完成：{len(areas)} 區，共 {total} 筆"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新年度紙本／中獎發票 Google Sheet")
    parser.add_argument("mode", choices=("paper", "prize"))
    parser.add_argument("period", help="紙本 YYYYMM；中獎 YYYYMMNN")
    parser.add_argument("--area", default="全區")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(run_invoice_update(args.mode, args.period, args.area), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
