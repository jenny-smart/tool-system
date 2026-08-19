"""
內勤PDF產出（跟清潔/承攬費 PDF 是各自獨立的程式，互不相關）

根目錄》YYYY》地區》YYYY地區薪資單總表》薪資單工作表》AA1:AH15
存入 根目錄》YYYY》地區》薪資單》YYYYMM地區薪資單_姓名.pdf

流程：
1. 讀「薪資單」工作表 B3:B 的姓名清單（沿用內勤結算已寫好的名單）
2. 逐一把姓名寫進 AC2（薪資單樣板靠 AC2 的姓名把公式抓成該員工當月資料）
3. 把 AA1:AH15 這個區塊匯出成 PDF（透過 Sheets 的 export 端點，帶著 gridrange 只匯出這個範圍）
4. 上傳到 根目錄》YYYY》地區》薪資單 資料夾，檔名 YYYYMM地區薪資單_姓名.pdf
   （同名檔案先丟垃圾桶再上傳，避免重複疊加）
"""

from __future__ import annotations

import time
from typing import List

import requests

from services.google_drive import DriveService
from services.google_sheets import SheetsService

from . import ROOT_FOLDER_ID, area_summary_sheet_name, year_folder_name, yyyymm_to_year

PAYROLL_WS = "薪資單"
NAME_CELL = "AC2"
EXPORT_RANGE = "AA1:AH15"


def _open_area_summary(drive: DriveService, sheets: SheetsService, year: int, area: str):
    year_folder = drive.find_folder(ROOT_FOLDER_ID, year_folder_name(year))
    if not year_folder:
        raise FileNotFoundError(f"找不到資料夾：{year_folder_name(year)}")

    area_folder = drive.find_folder(year_folder["id"], area)
    if not area_folder:
        raise FileNotFoundError(f"找不到地區資料夾：{area}")

    sheet_name = area_summary_sheet_name(year, area)
    matches = drive.find_google_sheet_by_name(area_folder["id"], sheet_name)
    if not matches:
        raise FileNotFoundError(f"找不到試算表：{sheet_name}")

    return sheets.open_by_id(matches[0]["id"]), area_folder["id"]


def _get_payroll_names(spreadsheet) -> List[str]:
    ws = spreadsheet.worksheet(PAYROLL_WS)
    col_b = ws.col_values(2)  # B欄
    return [v.strip() for v in col_b[2:] if v.strip()]  # 跳過 B1:B2，從 B3 開始


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
    names: List[str] = None,
    sleep_seconds: float = 1.0,
) -> dict:
    """
    對單一地區跑內勤PDF產出。

    names: 若不傳，預設用薪資單分頁 B3:B 目前的名單（也就是內勤結算跑完後的名單）。
    sleep_seconds: 每人寫入 AC2 後，等公式重新計算再匯出，避免抓到舊資料。
    """
    year = yyyymm_to_year(yyyymm)
    spreadsheet, area_folder_id = _open_area_summary(drive, sheets, year, area)
    ws = spreadsheet.worksheet(PAYROLL_WS)

    target_names = names if names is not None else _get_payroll_names(spreadsheet)
    if not target_names:
        return {"area": area, "count": 0, "files": []}

    pdf_folder = drive.get_or_create_folder(area_folder_id, "薪資單")

    uploaded = []
    for name in target_names:
        ws.update_acell(NAME_CELL, name)
        time.sleep(sleep_seconds)  # 讓試算表公式（VLOOKUP/QUERY 等）重新計算完成

        pdf_bytes = _export_range_as_pdf(access_token, spreadsheet.id, ws.id)

        filename = f"{yyyymm}{area}薪資單_{name}.pdf"
        # 同名先丟垃圾桶，避免重複檔案疊加
        for old in drive.find_files_by_name(pdf_folder["id"], filename):
            drive.trash_file(old["id"])

        file_meta = drive.service.files().create(
            body={"name": filename, "parents": [pdf_folder["id"]], "mimeType": "application/pdf"},
            media_body=_pdf_media(pdf_bytes),
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
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
