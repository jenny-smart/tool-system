"""
內勤PDF產出（跟清潔/承攬費 PDF 是各自獨立的程式，互不相關）

根目錄》YYYY》地區》YYYY地區薪資單總表》薪資單工作表》AA1:AH15
存入 根目錄》YYYY》地區》薪資單》YYYYMM地區薪資單_姓名.pdf

流程：
1. 讀「薪資單」工作表 B3:B 的姓名清單，只挑 D 欄是 TRUE 的人（沿用內勤結算寫好的名單，
   但尊重使用者手動取消勾選的情況，不是整批 B3:B 都出）
2. 逐一把姓名寫進 AC2（薪資單樣板靠 AC2 的姓名把公式抓成該員工當月資料）
3. 把 AA1:AH15 這個區塊匯出成 PDF（透過 Sheets 的 export 端點，帶著 gridrange 只匯出這個範圍）
4. 上傳到 根目錄》YYYY》地區》薪資單 資料夾，檔名 YYYYMM地區薪資單_姓名.pdf，
   連結寫回「薪資單」C 欄對應列：
   - C 欄該列原本是空的 → 上傳新檔（同名先丟垃圾桶再上傳，避免重複疊加），把新連結寫回 C
   - C 欄該列已經有連結 → 沿用連結對應的原檔案，直接覆蓋內容（不新建檔案），
     這樣重新產出後連結網址不會變，之前分享出去的連結還是打得開
"""

from __future__ import annotations

import re
import time
from typing import List, Optional

import requests

from services.google_drive import DriveService
from services.google_sheets import SheetsService, rowcol_to_a1

from . import yyyymm_to_year
from ._shared import PAYROLL_WS, open_area_folder, open_area_summary, open_year_folder

NAME_CELL = "AC2"
EXPORT_RANGE = "AA1:AH15"

_DRIVE_FILE_ID_PATTERNS = [
    re.compile(r"/d/([a-zA-Z0-9_-]{10,})"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]{10,})"),
]


def _open_area_summary(drive: DriveService, sheets: SheetsService, year: int, area: str):
    """
    回傳 (薪資單總表 Spreadsheet, 該地區自己的資料夾 id)。
    薪資單總表本身收在「台北」資料夾底下（見 _shared.py），但 PDF 輸出用的
    「薪資單」子資料夾仍然放在各地區自己的資料夾裡，兩者要分開取。
    """
    spreadsheet = open_area_summary(drive, sheets, year, area)
    year_folder = open_year_folder(drive, year)
    area_folder = open_area_folder(drive, year_folder["id"], area)
    return spreadsheet, area_folder["id"]


def _get_payroll_rows(spreadsheet) -> List[dict]:
    """
    讀「薪資單」B3:B（姓名）/ C3:C（既有連結）/ D3:D（是否產出），
    只回傳 D 欄為 TRUE 的列，並帶著實際列號方便之後把連結寫回 C 欄。
    """
    ws = spreadsheet.worksheet(PAYROLL_WS)
    col_b = ws.col_values(2)[2:]  # B3 起
    col_c = ws.col_values(3)[2:]  # C3 起
    col_d = ws.col_values(4)[2:]  # D3 起

    rows: List[dict] = []
    for i, raw_name in enumerate(col_b):
        name = raw_name.strip()
        if not name:
            continue
        flag = col_d[i].strip().upper() if i < len(col_d) else ""
        if flag != "TRUE":
            continue
        rows.append(
            {
                "row": i + 3,
                "name": name,
                "link": col_c[i].strip() if i < len(col_c) else "",
            }
        )
    return rows


def _extract_drive_file_id(link: str) -> Optional[str]:
    if not link:
        return None
    for pattern in _DRIVE_FILE_ID_PATTERNS:
        match = pattern.search(link)
        if match:
            return match.group(1)
    return None


def _export_range_as_pdf(access_token: str, spreadsheet_id: str, sheet_gid: int) -> bytes:
    """
    用 Google Sheets 的 export 端點，只匯出 AA1:AH15 這個 gridrange 成 PDF。
    range 用 A1 記號，Sheets export 支援 &range=AA1:AH15 搭配 &gid=<分頁id>。
    """
    url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"
        f"?format=pdf"
        f"&gid={sheet_gid}"
        f"&range={EXPORT_RANGE}"
        f"&portrait=true"
        f"&fitw=true"
        f"&gridlines=false"
        f"&printtitle=false"
        f"&sheetnames=false"
        f"&pagenum=UNDEFINED"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=60)
    resp.raise_for_status()
    return resp.content


def run_pdf_export(
    drive: DriveService,
    sheets: SheetsService,
    access_token: str,
    area: str,
    yyyymm: str,
    rows: List[dict] = None,
    sleep_seconds: float = 1.0,
) -> dict:
    """
    對單一地區跑內勤PDF產出。

    rows: 若不傳，預設用 `_get_payroll_rows()` 讀「薪資單」目前 D 欄為 TRUE 的名單。
    sleep_seconds: 每人寫入 AC2 後，等公式重新計算再匯出，避免抓到舊資料。
    """
    year = yyyymm_to_year(yyyymm)
    spreadsheet, area_folder_id = _open_area_summary(drive, sheets, year, area)
    ws = spreadsheet.worksheet(PAYROLL_WS)

    target_rows = rows if rows is not None else _get_payroll_rows(spreadsheet)
    if not target_rows:
        return {"area": area, "count": 0, "files": []}

    pdf_folder = drive.get_or_create_folder(area_folder_id, "薪資單")

    uploaded = []
    for row in target_rows:
        name = row["name"]
        ws.update_acell(NAME_CELL, name)
        time.sleep(sleep_seconds)  # 讓試算表公式（VLOOKUP/QUERY 等）重新計算完成

        pdf_bytes = _export_range_as_pdf(access_token, spreadsheet.id, ws.id)
        filename = f"{yyyymm}{area}薪資單_{name}.pdf"

        existing_file_id = _extract_drive_file_id(row.get("link", ""))
        if existing_file_id:
            # C 欄已有連結：沿用原檔案直接覆蓋內容，連結網址維持不變
            file_meta = (
                drive.service.files()
                .update(
                    fileId=existing_file_id,
                    body={"name": filename},
                    media_body=_pdf_media(pdf_bytes),
                    fields="id,name,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        else:
            # 第一次產出：同名先丟垃圾桶再上傳，避免重複檔案疊加，再把新連結寫回 C 欄
            for old in drive.find_files_by_name(pdf_folder["id"], filename):
                drive.trash_file(old["id"])

            file_meta = (
                drive.service.files()
                .create(
                    body={"name": filename, "parents": [pdf_folder["id"]], "mimeType": "application/pdf"},
                    media_body=_pdf_media(pdf_bytes),
                    fields="id,name,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
            ws.update_acell(rowcol_to_a1(row["row"], 3), file_meta.get("webViewLink", ""))

        uploaded.append(file_meta)

    return {"area": area, "count": len(uploaded), "files": uploaded}


def _pdf_media(pdf_bytes: bytes):
    from googleapiclient.http import MediaInMemoryUpload

    return MediaInMemoryUpload(pdf_bytes, mimetype="application/pdf", resumable=False)


def run_pdf_export_all(
    drive: DriveService,
    sheets: SheetsService,
    access_token: str,
    areas: List[str],
    yyyymm: str,
) -> List[dict]:
    results = []
    for area in areas:
        results.append(run_pdf_export(drive, sheets, access_token, area, yyyymm))
    return results
