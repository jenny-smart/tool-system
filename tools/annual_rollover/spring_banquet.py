"""
tools/annual_rollover/spring_banquet.py

生成新年度「春酒專員名冊」：尾牙春酒專用區 →「{Y}春酒」資料夾 →「{Y}春酒專員名冊」，
每個地區一個分頁，內容為檸檬後台專員個資中狀態「正常」的 姓名／電話／到職日期／狀態（A/B/G/N 欄）。

- 各地區登入帳號：沿用月排程「儲值金結算」的 load_accounts()（Streamlit secrets → 環境變數）
- 匯出方式：沿用日排程「專員個資」的後台匯出網址
- 可重複執行：資料夾／檔案已存在就沿用，分頁內容整個覆蓋成最新名冊
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List

import pandas as pd
import requests

SPRING_ROOT_FOLDER_ID = "1q5IeRNtI4a6tUwWCglmzPHj3Ff7hBCxq"  # 尾牙春酒專用區
YEAR_FOLDER_NAME = "{Y}春酒"
ROSTER_FILE_NAME = "{Y}春酒專員名冊"
ROSTER_AREAS = ["台北", "台中", "桃園", "新竹"]
ROSTER_COLUMNS = ["姓名", "電話", "到職日期", "狀態"]   # 後台匯出的 A/B/G/N 欄
ROSTER_COLUMN_INDEX = [0, 1, 6, 13]                    # 標題對不上時的備援欄位位置
ACTIVE_STATUS = "正常"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def export_staff_frame(area: str, accounts: Dict[str, Dict[str, str]]) -> pd.DataFrame:
    """用該地區帳號登入後台，下載專員個資並讀成 DataFrame（全部欄位為字串）。"""
    from tools.common.lemon_backend_login import login as backend_login
    from tools.scheduled_daily.staff_info import EXPORT_URL, HEADERS, LOGIN_URL

    account = accounts.get(area) or {}
    session = requests.Session()
    backend_login(session, account.get("email", ""), account.get("password", ""),
                  login_url=LOGIN_URL, headers=HEADERS, timeout=60)
    res = session.get(EXPORT_URL, headers=HEADERS, allow_redirects=True, timeout=120)
    res.raise_for_status()
    content_type = res.headers.get("Content-Type", "").lower()
    if "excel" not in content_type and "octet-stream" not in content_type:
        raise RuntimeError(f"{area} 專員個資回傳不是 Excel，Content-Type={content_type}")
    try:
        # 後台實際給的是舊版 xls（副檔名是 xlsx），xlrd 會誤判檔案損毀，需略過檢查
        return pd.read_excel(BytesIO(res.content), dtype=str, engine="xlrd",
                             engine_kwargs={"ignore_workbook_corruption": True}).fillna("")
    except Exception:
        return pd.read_excel(BytesIO(res.content), dtype=str).fillna("")


def active_rows(df: pd.DataFrame) -> List[List[str]]:
    """篩出狀態「正常」的專員，取 姓名／電話／到職日期／狀態；電話加 ' 保留開頭的 0。"""
    if all(col in df.columns for col in ROSTER_COLUMNS):
        picked = df[ROSTER_COLUMNS]
    else:
        picked = df.iloc[:, ROSTER_COLUMN_INDEX]
    rows = []
    for name, phone, onboard, status in picked.astype(str).values.tolist():
        if status.strip() != ACTIVE_STATUS:
            continue
        phone = phone.strip()
        rows.append([name.strip(), f"'{phone}" if phone else "", onboard.strip(), status.strip()])
    return rows


def ensure_roster_file(rollover, folder_id: str, name: str) -> tuple[Dict[str, Any], bool]:
    existing = [] if folder_id.startswith("(dry-run") else [
        f for f in rollover.children(folder_id) if f["mimeType"] == SHEET_MIME and f["name"] == name
    ]
    if existing:
        rollover.log(f"  檔案已存在，沿用：{name}")
        return existing[0], False
    rollover.log(f"  建立檔案：{name}")
    if rollover.dry_run:
        return {"id": f"(dry-run:{name})", "name": name}, True
    created = rollover.drive.files().create(
        body={"name": name, "mimeType": SHEET_MIME, "parents": [folder_id]},
        fields="id,name", supportsAllDrives=True,
    ).execute()
    return created, True


def write_area_sheet(rollover, file_id: str, area: str, rows: List[List[str]]) -> None:
    sheets = rollover.sheets.spreadsheets()
    meta = sheets.get(spreadsheetId=file_id, fields="sheets.properties").execute()
    props = [s["properties"] for s in meta.get("sheets", [])]
    titles = {p["title"] for p in props}
    if area not in titles:
        # 新建檔案的預設「工作表1」直接改名給第一個地區用
        blank = next((p for p in props if p["title"] not in ROSTER_AREAS), None) if len(props) == 1 else None
        if blank:
            req = {"updateSheetProperties": {"properties": {"sheetId": blank["sheetId"], "title": area},
                                             "fields": "title"}}
        else:
            req = {"addSheet": {"properties": {"title": area}}}
        sheets.batchUpdate(spreadsheetId=file_id, body={"requests": [req]}).execute()
    sheets.values().clear(spreadsheetId=file_id, range=f"'{area}'!A:D").execute()
    sheets.values().update(
        spreadsheetId=file_id, range=f"'{area}'!A1", valueInputOption="USER_ENTERED",
        body={"values": [ROSTER_COLUMNS] + rows},
    ).execute()


def run_staff_roster(rollover, area: str = "全區") -> List[Dict[str, str]]:
    """建立／更新「{Y}春酒專員名冊」；area 為「全區」時處理台北、台中、桃園、新竹。"""
    from tools.scheduled_monthly.stored_value_settlement import load_accounts

    areas = ROSTER_AREAS if area in ("", "全區") else [a for a in ROSTER_AREAS if a == area]
    if not areas:
        rollover.log(f"  春酒專員名冊沒有「{area}」地區，略過")
        return []
    y = str(rollover.new_year)
    folder = rollover.ensure_folder(SPRING_ROOT_FOLDER_ID, YEAR_FOLDER_NAME.format(Y=y))
    file, created = ensure_roster_file(rollover, folder["id"], ROSTER_FILE_NAME.format(Y=y))

    accounts = load_accounts()
    failed = []
    for a in areas:
        try:
            rows = active_rows(export_staff_frame(a, accounts))
        except Exception as exc:  # 一區失敗不影響其他地區
            rollover.log(f"  ✗ {a} 專員個資匯出失敗：{exc}")
            failed.append(a)
            continue
        rollover.log(f"  {a}：狀態「{ACTIVE_STATUS}」{len(rows)} 人 → {file['name']}!{a}")
        if not rollover.dry_run:
            write_area_sheet(rollover, file["id"], a, rows)

    records = [
        {"kind": "資料夾", "old_name": "", "old_id": "", "new_name": folder["name"], "new_id": folder["id"]},
        {"kind": "檔案", "old_name": "專員個資（" + "、".join(areas) + "）", "old_id": "",
         "new_name": file["name"], "new_id": file["id"], "created": created},
    ]
    for a in failed:
        records.append({"kind": "檔案", "old_name": f"專員個資-{a}", "old_id": "",
                        "new_name": "找不到來源", "new_id": ""})
    return records
