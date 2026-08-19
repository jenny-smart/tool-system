"""
內勤薪資單通知信

根目錄》YYYY薪資》YYYY內勤薪資名冊

這支程式只整理名冊／收件清單，不寄信；寄信是另一套機制觸發（跟這支程式無關）。

流程：
1. mail 工作表 A2:An：依序把「台北」「桃園」員工個資 J欄=='Y' 的內勤員工姓名接起來
   （直接沿用「YYYY地區薪資單總表》薪資單」B3:B——內勤結算跑完後已經是篩選過的名單），
   B2:Bn 對應寫入同一批人「薪資單」F3:F 的值（沿用規格文字，假設 F 欄是信箱）
2. 複製「YYYY內勤薪資名冊」裡最近一個月（yyyymm 的前一個月）分頁，改名成當月
   YYYYMM，刪除新分頁的 F 欄，並把新分頁的 D1 改成當月 YYYYMM
   （2026-08-19 已用實際案例跟你確認過這個流程：先複製上個月分頁 → 改名 → 刪 F 欄
   → D1 改成新月份，不是「複製固定樣板分頁」）
3. 新分頁 B2 起，依序貼「台北」「桃園」的「薪資單」B3:B（姓名）與 C3:C（連結，
   沿用規格文字，假設 C 欄是連結），桃園緊接在台北最後一列之後，動態接續、不是
   固定位置

備註：
- 名單只看「員工個資」J欄是否為 Y（在職/適用旗標），不是看薪資單 D3:D 當月是否勾選發薪
- 「薪資單」F3:F（信箱）與 C3:C（連結）目前沒有其他程式會寫入，這裡假設是既有、
  維護在該工作表上的既有資料；如果實際欄位不是這樣，之後只要調整
  `_read_payroll_roster` 裡的欄位索引即可
"""

from __future__ import annotations

from typing import Dict, List

import gspread

from services.google_drive import DriveService
from services.google_sheets import SheetsService

from . import (
    AREAS,
    ROOT_FOLDER_ID,
    area_summary_sheet_name,
    roster_sheet_name,
    year_folder_name,
    yyyymm_to_year,
)

PAYROLL_WS = "薪資單"
MAIL_WS = "mail"


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

    return sheets.open_by_id(matches[0]["id"])


def _open_roster(drive: DriveService, sheets: SheetsService, year: int):
    year_folder = drive.find_folder(ROOT_FOLDER_ID, year_folder_name(year))
    if not year_folder:
        raise FileNotFoundError(f"找不到資料夾：{year_folder_name(year)}")

    sheet_name = roster_sheet_name(year)
    matches = drive.find_google_sheet_by_name(year_folder["id"], sheet_name)
    if not matches:
        raise FileNotFoundError(f"找不到試算表：{sheet_name}")

    return sheets.open_by_id(matches[0]["id"])


def _read_payroll_roster(spreadsheet) -> List[Dict[str, str]]:
    """讀「薪資單」B3:B（姓名）、C3:C（連結）、F3:F（信箱），依 B 欄的列數對齊。"""
    ws = spreadsheet.worksheet(PAYROLL_WS)
    col_b = ws.col_values(2)[2:]  # B3 起
    col_c = ws.col_values(3)[2:]  # C3 起
    col_f = ws.col_values(6)[2:]  # F3 起

    rows: List[Dict[str, str]] = []
    for i, raw_name in enumerate(col_b):
        name = raw_name.strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "link": col_c[i].strip() if i < len(col_c) else "",
                "email": col_f[i].strip() if i < len(col_f) else "",
            }
        )
    return rows


def _ordered_areas(areas: List[str]) -> List[str]:
    """固定用台北優先、桃園在後的順序，只保留有被選到的地區。"""
    selected = set(areas)
    return [a for a in AREAS if a in selected]


def update_mail_roster(drive: DriveService, sheets: SheetsService, roster_spreadsheet, areas: List[str], year: int) -> dict:
    """mail 工作表：A 欄姓名、B 欄信箱，依序台北→桃園接起來。"""
    mail_ws = sheets.get_or_create_ws(roster_spreadsheet, MAIL_WS)

    names: List[str] = []
    emails: List[str] = []
    for area in _ordered_areas(areas):
        summary = _open_area_summary(drive, sheets, year, area)
        for row in _read_payroll_roster(summary):
            names.append(row["name"])
            emails.append(row["email"])

    sheets.clear_from_row(mail_ws, start_row=2, start_col=1, end_col=2)
    if names:
        sheets.write_values(mail_ws, start_row=2, start_col=1, values=[[n] for n in names])
        sheets.write_values(mail_ws, start_row=2, start_col=2, values=[[e] for e in emails])

    return {"count": len(names), "names": names}


def _prev_yyyymm(yyyymm: str) -> str:
    year = int(yyyymm[:4])
    month = int(yyyymm[4:6])
    month -= 1
    if month == 0:
        month = 12
        year -= 1
    return f"{year}{month:02d}"


def ensure_month_tab(roster_spreadsheet, yyyymm: str):
    """
    確保「YYYY內勤薪資名冊」裡有當月 YYYYMM 分頁：
    - 已存在就直接沿用（不重複複製）
    - 不存在就複製「前一個月」分頁 → 改名成當月 YYYYMM → 刪除新分頁的 F 欄
      → 把新分頁的 D1 改成當月 YYYYMM
    - 前一個月分頁也不存在（例如第一次建立）就丟出清楚的錯誤，請先手動建立第一份
      月份分頁範本
    """
    try:
        return roster_spreadsheet.worksheet(yyyymm)
    except gspread.WorksheetNotFound:
        pass

    prev_yyyymm = _prev_yyyymm(yyyymm)
    try:
        source_ws = roster_spreadsheet.worksheet(prev_yyyymm)
    except gspread.WorksheetNotFound as exc:
        raise FileNotFoundError(
            f"找不到前一個月分頁「{prev_yyyymm}」，無法複製建立「{yyyymm}」，"
            f"請先手動建立第一份月份分頁範本。"
        ) from exc

    new_ws = roster_spreadsheet.duplicate_sheet(source_ws.id, new_sheet_name=yyyymm)
    new_ws.delete_columns(6)  # F 欄：上個月的寄信狀態不該帶到新月份
    new_ws.update_acell("D1", yyyymm)
    return new_ws


def fill_month_tab_payroll_links(drive: DriveService, sheets: SheetsService, month_ws, areas: List[str], year: int) -> int:
    """新分頁 B2 起依序貼台北→桃園的姓名（B欄）與連結（C欄），動態接續、不是固定位置。"""
    row = 2
    total = 0
    for area in _ordered_areas(areas):
        summary = _open_area_summary(drive, sheets, year, area)
        rows = _read_payroll_roster(summary)
        if not rows:
            continue
        values = [[r["name"], r["link"]] for r in rows]
        sheets.write_values(month_ws, start_row=row, start_col=2, values=values)
        row += len(values)
        total += len(values)
    return total


def run_mail_notify(drive: DriveService, sheets: SheetsService, areas: List[str], yyyymm: str) -> dict:
    """執行內勤薪資單通知信：更新 mail 分頁 + 建立/更新當月分頁。"""
    year = yyyymm_to_year(yyyymm)
    roster = _open_roster(drive, sheets, year)

    mail_result = update_mail_roster(drive, sheets, roster, areas, year)
    month_ws = ensure_month_tab(roster, yyyymm)
    month_count = fill_month_tab_payroll_links(drive, sheets, month_ws, areas, year)

    return {
        "yyyymm": yyyymm,
        "mail_count": mail_result["count"],
        "month_tab": month_ws.title,
        "month_tab_count": month_count,
    }
