"""
內勤結算

根目錄》YYYY薪資》地區》YYYY地區薪資單總表》薪資單工作表

步驟：
1. AC1 》 'YYYY.MM（前面加 ' 強制存成文字，不然 2026.10 會被當數字顯示成 2026.1）
2. 清空 B3:D（連 B 欄舊名單一起清，避免上個月多出來的人殘留在下面）
3. 從同一份總表的「YYYY薪資總表」工作表篩選 A欄＝YYYY.MM 的列，取 D欄（姓名），
   依原列順序寫入「薪資單」工作表 B3:B，並將對應列的 D3:D 設為 TRUE
   （名單＝該月份實際有薪資資料的人，不是「員工個資」J欄=='Y' 的在職名單）
"""

from __future__ import annotations

from typing import List

from services.google_drive import DriveService
from services.google_sheets import SheetsService

from . import yyyymm_to_dotted, yyyymm_to_year
from ._shared import PAYROLL_WS, open_area_summary as _open_area_summary


def _same_period(value: str, dotted: str) -> bool:
    """A欄月份是否等於 YYYY.MM；A欄若被存成數字（2026.1）也視為 2026.10"""
    value = value.strip()
    if value == dotted:
        return True
    try:
        return float(value) == float(dotted)
    except ValueError:
        return False


def get_month_employees(spreadsheet, year: int, dotted: str) -> List[str]:
    """讀「YYYY薪資總表」工作表，回傳 A欄＝dotted 的員工姓名（D欄），依原列順序、去重"""
    ws = spreadsheet.worksheet(f"{year}薪資總表")
    records = ws.get_all_values()

    names: List[str] = []
    for row in records:
        # A欄 index 0（月份）, D欄 index 3（姓名）
        if len(row) <= 3:
            continue
        name = row[3].strip()
        if name and _same_period(row[0], dotted) and name not in names:
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

    # 先抓名單，找不到該月資料就直接中止，不動到薪資單現有內容
    names = get_month_employees(spreadsheet, year, dotted)
    if not names:
        raise ValueError(f"{area}：「{year}薪資總表」找不到 A欄＝{dotted} 的資料，請先確認該月薪資總表已建立")

    # 1. AC1 》 'YYYY.MM（加 ' 存成文字，10 月才不會顯示成 2026.1）
    ws.update_acell("AC1", f"'{dotted}")

    # 2. 清空 B3:D（整欄清到最後一列）
    sheets.clear_from_row(ws, start_row=3, start_col=2, end_col=4)  # B=2, D=4

    # 3. 該月薪資總表的姓名 -> 薪資單 B3:B，對應列 D3:D = TRUE
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
