"""
內勤結算

根目錄》YYYY薪資》地區》YYYY地區薪資單總表》薪資單工作表

步驟：
1. AC1 》 YYYY.MM
2. 清空 C3:D
3. 從「員工個資」工作表篩選 J欄=='Y' 的員工姓名（B欄），
   依序寫入「薪資單」工作表 B3:B，並將對應列的 D3:D 設為 TRUE
"""

from __future__ import annotations

from typing import List

from services.google_drive import DriveService
from services.google_sheets import SheetsService

from . import yyyymm_to_dotted, yyyymm_to_year
from ._shared import PAYROLL_WS, EMPLOYEE_WS, open_area_summary as _open_area_summary


def get_eligible_employees(spreadsheet, sheets: SheetsService) -> List[str]:
    """讀「員工個資」工作表，回傳 J欄=='Y' 的員工姓名（B欄），依原列順序"""
    ws = spreadsheet.worksheet(EMPLOYEE_WS)
    records = ws.get_all_values()  # 含表頭，index 0 = row1

    names: List[str] = []
    for row in records[1:]:  # 跳過表頭
        # B欄 index 1, J欄 index 9
        if len(row) <= 9:
            continue
        name = row[1].strip() if len(row) > 1 else ""
        flag = row[9].strip() if len(row) > 9 else ""
        if name and flag.upper() == "Y":
            names.append(name)
    return names


def run_settlement(drive: DriveService, sheets: SheetsService, area: str, yyyymm: str) -> dict:
    """
    執行單一地區的內勤結算

    回傳 {"area": area, "count": 已寫入人數, "names": [...]}
    """
    year = yyyymm_to_year(yyyymm)
    dotted = yyyymm_to_dotted(yyyymm)

    spreadsheet = _open_area_summary(drive, sheets, year, area)
    ws = spreadsheet.worksheet(PAYROLL_WS)

    # 1. AC1 》 YYYY.MM
    ws.update_acell("AC1", dotted)

    # 2. 清空 C3:D（整欄清到最後一列）
    sheets.clear_from_row(ws, start_row=3, start_col=3, end_col=4)  # C=3, D=4

    # 3. 員工個資 J欄=='Y' 的姓名 -> 薪資單 B3:B，對應列 D3:D = TRUE
    names = get_eligible_employees(spreadsheet, sheets)
    if names:
        b_values = [[n] for n in names]
        d_values = [[True] for _ in names]
        sheets.write_values(ws, start_row=3, start_col=2, values=b_values)  # B=2
        sheets.write_values(ws, start_row=3, start_col=4, values=d_values)  # D=4

    return {"area": area, "count": len(names), "names": names}


def run_settlement_all(drive: DriveService, sheets: SheetsService, areas: List[str], yyyymm: str) -> List[dict]:
    """依序對多個地區跑內勤結算，回傳每個地區的結果"""
    results = []
    for area in areas:
        results.append(run_settlement(drive, sheets, area, yyyymm))
    return results
