"""
內勤元大帳戶

根目錄》YYYY薪資》台北》YYYY元大帳戶-地區
（元大帳戶試算表的檔名帶地區後綴，2026-08-20 確認；跟「YYYY地區薪資單總表」一樣，
實際上是收在「台北」資料夾底下，不是各自的地區資料夾——如果實際上元大帳戶還是放在
各地區自己的資料夾，只要改 `_open_yuanta_sheet` 用回 `open_area_folder(area)` 即可）

元大工作表實際欄位（2026-08-20 用實際截圖確認過表頭，row1 是「所有欄位請勿自行
新增或刪除」的提示列，row2 是表頭，資料從 row3 開始）：
A＝轉帳日期(yyyymmdd)、B＝受款人身分證字號、C＝受款人帳號、D＝金額、E＝員工姓名

流程（E/B/C/D 四欄全部查地區自己的「YYYY地區薪資單總表」，不是固定查台北）：
1. 元大工作表 E3:E：該地區自己「YYYY地區薪資單總表》薪資單」B3:B（姓名）
2. 元大工作表 B3:B（受款人身分證字號）：E欄(姓名) 對到該地區「員工個資」B欄時，
   對應員工個資 C欄
3. 元大工作表 C3:C（受款人帳號）：E欄(姓名) 對到該地區「員工個資」B欄時，
   對應員工個資 I欄
4. 元大工作表 D3:D（金額）：E欄(姓名) 對到該地區「YYYY薪資總表」
   （獨立的工作表，不是「薪資單」）D欄，且該表 A欄＝YYYY.MM 時，對應同一列的 AH欄
   （2026-08-20 用截圖確認分頁叫「YYYY薪資總表」；B/C/D 一開始誤植為固定查台北，
   後來確認應該跟 E 一樣查地區自己的）
5. 元大工作表 A3:A（轉帳日期）：YYYYMM 下個月的 5 日，整批填同一個日期，格式
   yyyymmdd（對應表頭要求）；只避開六日，往前挪到最近的工作日（目前沒有國定假日
   資料，只能避開週末，不是真正的「例假日」，遇到國定假日連假還是要人工核對）
6. 元大工作表 K2：YYYY.MM；寫完後檢查 F3:F，若有任何非 0（或非數字）的值，
   回傳結果會帶上 warning，請人工確認
7. 另存 YYYYMM元大帳戶-地區.xlsx，存回跟元大帳戶試算表同一個 Drive 資料夾
"""

from __future__ import annotations

import datetime
import time
from typing import Dict, List, Optional, Tuple

from services.google_drive import DriveService
from services.google_sheets import SheetsService

from . import yuanta_sheet_name, yyyymm_to_dotted, yyyymm_to_year
from ._shared import (
    EMPLOYEE_WS,
    PAYROLL_WS,
    SUMMARY_HOME_AREA,
    open_area_folder,
    open_area_summary,
    open_year_folder,
)

# 欄位索引（0-based，對應 get_all_values() 讀回來的整份資料）
COL_A = 0
COL_B = 1
COL_C = 2
COL_D = 3
COL_I = 8
COL_AH = 33  # A=1,...,Z=26,AA=27,...,AH=34 → 0-based 33


def _open_yuanta_sheet(drive: DriveService, sheets: SheetsService, year: int, area: str):
    """回傳 (元大帳戶 Spreadsheet, 所在資料夾 id)。"""
    year_folder = open_year_folder(drive, year)
    home_folder = open_area_folder(drive, year_folder["id"], SUMMARY_HOME_AREA)

    sheet_name = yuanta_sheet_name(year, area)
    matches = drive.find_google_sheet_by_name(home_folder["id"], sheet_name)
    if not matches:
        raise FileNotFoundError(f"找不到試算表：{sheet_name}（資料夾：{SUMMARY_HOME_AREA}）")

    return sheets.open_by_id(matches[0]["id"]), home_folder["id"]


def _get_yuanta_ws(spreadsheet):
    return spreadsheet.sheet1


def _cell(row: List[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _employee_lookup(employee_values: List[List[str]], name: str) -> Tuple[str, str]:
    """
    員工個資 B欄=name 的那一列，回傳 (C欄值, I欄值)。
    對應元大工作表實際欄位：C欄＝受款人身分證字號、I欄＝受款人帳號
    （2026-08-20 用實際元大工作表截圖確認過表頭）。
    """
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


def _next_month_payday(yyyymm: str) -> str:
    """YYYYMM 下個月的 5 日；遇六日往前挪到最近的工作日（不含國定假日）。
    回傳 yyyymmdd 格式的字串，對應元大工作表 A 欄「轉帳日期(yyyymmdd)」的表頭要求。"""
    year = int(yyyymm[:4])
    month = int(yyyymm[4:6]) + 1
    if month == 13:
        month = 1
        year += 1

    day = datetime.date(year, month, 5)
    while day.weekday() >= 5:  # 5=六, 6=日
        day -= datetime.timedelta(days=1)
    return day.strftime("%Y%m%d")


def _has_nonzero_f_column(yuanta_ws) -> bool:
    """F3:F 若有任何非 0（或無法判讀成數字）的值，代表需要人工確認。"""
    for raw in yuanta_ws.col_values(6)[2:]:
        value = raw.strip()
        if not value:
            continue
        try:
            if float(value.replace(",", "")) != 0:
                return True
        except ValueError:
            return True
    return False


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
    """執行單一地區的內勤元大帳戶，回傳 {"area", "count", "xlsx", "warning"}"""
    year = yyyymm_to_year(yyyymm)
    dotted = yyyymm_to_dotted(yyyymm)

    # E/B/C/D 欄全部查地區自己的總表（不是固定查台北）
    own_summary = open_area_summary(drive, sheets, year, area)
    names = [v.strip() for v in own_summary.worksheet(PAYROLL_WS).col_values(2)[2:] if v.strip()]
    employee_values = own_summary.worksheet(EMPLOYEE_WS).get_all_values()
    payroll_values = own_summary.worksheet(f"{year}薪資總表").get_all_values()

    yuanta_spreadsheet, home_folder_id = _open_yuanta_sheet(drive, sheets, year, area)
    yuanta_ws = _get_yuanta_ws(yuanta_spreadsheet)

    # 清空 A3:E（整欄清到最後一列）再重寫，避免舊資料疊加
    sheets.clear_from_row(yuanta_ws, start_row=3, start_col=1, end_col=5)

    if names:
        payday = _next_month_payday(yyyymm)
        a_values, b_values, c_values, d_values, e_values = [], [], [], [], []
        for name in names:
            national_id, bank_account = _employee_lookup(employee_values, name)
            amount = _payroll_amount_lookup(payroll_values, name, dotted)
            a_values.append([payday])
            b_values.append([national_id])
            c_values.append([bank_account])
            d_values.append([amount])
            e_values.append([name])

        sheets.write_values(yuanta_ws, start_row=3, start_col=1, values=a_values)
        sheets.write_values(yuanta_ws, start_row=3, start_col=2, values=b_values)
        sheets.write_values(yuanta_ws, start_row=3, start_col=3, values=c_values)
        sheets.write_values(yuanta_ws, start_row=3, start_col=4, values=d_values)
        sheets.write_values(yuanta_ws, start_row=3, start_col=5, values=e_values)

    yuanta_ws.update_acell("K2", dotted)
    time.sleep(1.5)  # 讓 F 欄（若是公式）有時間重新計算完成

    warning = "F欄有非0值，請人工確認" if _has_nonzero_f_column(yuanta_ws) else None

    filename = f"{yyyymm}元大帳戶-{area}.xlsx"
    xlsx_file = _export_and_save_xlsx(drive, yuanta_spreadsheet.id, home_folder_id, filename)

    return {"area": area, "count": len(names), "xlsx": xlsx_file, "warning": warning}


def run_yuanta_account_all(drive: DriveService, sheets: SheetsService, areas: List[str], yyyymm: str) -> List[dict]:
    """依序對多個地區跑內勤元大帳戶，回傳每個地區的結果"""
    results = []
    for area in areas:
        results.append(run_yuanta_account(drive, sheets, area, yyyymm))
    return results
