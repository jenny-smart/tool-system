"""
內勤元大帳戶

根目錄》YYYY薪資》地區》YYYY元大帳戶

流程（照規格逐欄實作，「元大工作表」規格沒說明確切分頁名稱，這裡假設是該試算表
的預設／第一個工作表——如果之後改成固定分頁名稱，改 `_get_yuanta_ws` 即可）：
1. 元大工作表 E3:E：YYYY地區薪資單總表》薪資單 B3:B（姓名）
2. 元大工作表 B3:B：E欄(姓名)＝員工個資 B欄 時，對應員工個資 C欄
3. 元大工作表 C3:C：E欄(姓名)＝員工個資 B欄 時，對應員工個資 I欄
4. 元大工作表 D3:D：薪資單 D欄＝E欄(姓名) 且 薪資單 A欄＝YYYY.MM 時，對應薪資單 AH欄
   （規格文字沒有指明這是薪資單哪個資料表的欄位，這裡照字面直接對「薪資單」工作表
   整份資料的 A/D/AH 欄做比對；如果實際上薪資單另外有一份紀錄用的資料表跟 AA1:AH15
   樣板不同，這段之後要對照真實表格再調整欄位索引）
5. 另存 YYYYMM元大帳戶-地區.xlsx（存回同一個 Drive 資料夾）
"""

from __future__ import annotations

from typing import List, Tuple

from services.google_drive import DriveService
from services.google_sheets import SheetsService

from . import (
    ROOT_FOLDER_ID,
    area_summary_sheet_name,
    year_folder_name,
    yuanta_sheet_name,
    yyyymm_to_dotted,
    yyyymm_to_year,
)

PAYROLL_WS = "薪資單"
EMPLOYEE_WS = "員工個資"

# 欄位索引（0-based，對應 get_all_values() 讀回來的整份資料）
COL_A = 0
COL_B = 1
COL_C = 2
COL_D = 3
COL_I = 8
COL_AH = 33  # A=1,...,Z=26,AA=27,...,AH=34 → 0-based 33


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


def _open_yuanta_sheet(drive: DriveService, sheets: SheetsService, area_folder_id: str, year: int):
    sheet_name = yuanta_sheet_name(year)
    matches = drive.find_google_sheet_by_name(area_folder_id, sheet_name)
    if not matches:
        raise FileNotFoundError(f"找不到試算表：{sheet_name}")
    return sheets.open_by_id(matches[0]["id"])


def _get_yuanta_ws(spreadsheet):
    return spreadsheet.sheet1


def _cell(row: List[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _employee_lookup(employee_values: List[List[str]], name: str) -> Tuple[str, str]:
    """員工個資 B欄=name 的那一列，回傳 (C欄值, I欄值)"""
    for row in employee_values[1:]:  # 跳過表頭
        if _cell(row, COL_B) == name:
            return _cell(row, COL_C), _cell(row, COL_I)
    return "", ""


def _payroll_amount_lookup(payroll_values: List[List[str]], name: str, dotted_period: str) -> str:
    """薪資單整份資料裡 D欄=name 且 A欄=dotted_period 的那一列，回傳 AH欄值"""
    for row in payroll_values:
        if _cell(row, COL_D) == name and _cell(row, COL_A) == dotted_period:
            return _cell(row, COL_AH)
    return ""


def _export_and_save_xlsx(drive: DriveService, spreadsheet_id: str, folder_id: str, filename: str) -> dict:
    from googleapiclient.http import MediaInMemoryUpload

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    content = drive.service.files().export(fileId=spreadsheet_id, mimeType=xlsx_mime).execute()

    # 同名先丟垃圾桶，避免重複檔案疊加
    for old in drive.find_files_by_name(folder_id, filename):
        drive.trash_file(old["id"])

    media = MediaInMemoryUpload(content, mimetype=xlsx_mime, resumable=False)
    return (
        drive.service.files()
        .create(
            body={"name": filename, "parents": [folder_id], "mimeType": xlsx_mime},
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )


def run_yuanta_account(drive: DriveService, sheets: SheetsService, area: str, yyyymm: str) -> dict:
    """執行單一地區的內勤元大帳戶，回傳 {"area", "count", "xlsx"}"""
    year = yyyymm_to_year(yyyymm)
    dotted = yyyymm_to_dotted(yyyymm)

    summary, area_folder_id = _open_area_summary(drive, sheets, year, area)
    payroll_ws = summary.worksheet(PAYROLL_WS)
    employee_ws = summary.worksheet(EMPLOYEE_WS)

    names = [v.strip() for v in payroll_ws.col_values(2)[2:] if v.strip()]  # B3:B
    payroll_values = payroll_ws.get_all_values()
    employee_values = employee_ws.get_all_values()

    yuanta_spreadsheet = _open_yuanta_sheet(drive, sheets, area_folder_id, year)
    yuanta_ws = _get_yuanta_ws(yuanta_spreadsheet)

    # 清空 B3:E（整欄清到最後一列）再重寫，避免舊資料疊加
    sheets.clear_from_row(yuanta_ws, start_row=3, start_col=2, end_col=5)

    if names:
        b_values, c_values, d_values, e_values = [], [], [], []
        for name in names:
            bank_code, bank_account = _employee_lookup(employee_values, name)
            amount = _payroll_amount_lookup(payroll_values, name, dotted)
            b_values.append([bank_code])
            c_values.append([bank_account])
            d_values.append([amount])
            e_values.append([name])

        sheets.write_values(yuanta_ws, start_row=3, start_col=2, values=b_values)
        sheets.write_values(yuanta_ws, start_row=3, start_col=3, values=c_values)
        sheets.write_values(yuanta_ws, start_row=3, start_col=4, values=d_values)
        sheets.write_values(yuanta_ws, start_row=3, start_col=5, values=e_values)

    filename = f"{yyyymm}元大帳戶-{area}.xlsx"
    xlsx_file = _export_and_save_xlsx(drive, yuanta_spreadsheet.id, area_folder_id, filename)

    return {"area": area, "count": len(names), "xlsx": xlsx_file}


def run_yuanta_account_all(drive: DriveService, sheets: SheetsService, areas: List[str], yyyymm: str) -> List[dict]:
    """依序對多個地區跑內勤元大帳戶，回傳每個地區的結果"""
    results = []
    for area in areas:
        results.append(run_yuanta_account(drive, sheets, area, yyyymm))
    return results
