"""
staff_payroll 四支功能檔案共用的 Drive／Sheets 定位邏輯，統一放這裡，
之後 Drive 實際結構再有異動只需要改一個地方。

根目錄結構重點（2026-08-20 用實際 Drive 結構確認過）：
- 「YYYY地區薪資單總表」不管是哪個地區，實際上都收在同一個「台北」資料夾底下，
  不是各自放在自己的地區資料夾（例如 2026桃園薪資單總表 也放在台北資料夾裡）。
- 其他各地區自己的東西（PDF 輸出用的「薪資單」資料夾、「YYYY元大帳戶」試算表）
  目前仍假設放在各自的地區資料夾底下，沒有被要求跟著搬到台北資料夾——如果實際上
  這些也統一收在台北資料夾，`open_area_folder` 這裡要跟著改。
"""

from __future__ import annotations

from services.google_drive import DriveService
from services.google_sheets import SheetsService

from . import ROOT_FOLDER_ID, area_summary_sheet_name, year_folder_name

PAYROLL_WS = "薪資單"
EMPLOYEE_WS = "員工個資"

# YYYY地區薪資單總表實際收納的資料夾（不管是哪個地區的總表，都在這個資料夾底下）
SUMMARY_HOME_AREA = "台北"


def open_year_folder(drive: DriveService, year: int) -> dict:
    year_folder = drive.find_folder(ROOT_FOLDER_ID, year_folder_name(year))
    if not year_folder:
        raise FileNotFoundError(f"找不到資料夾：{year_folder_name(year)}")
    return year_folder


def open_area_folder(drive: DriveService, year_folder_id: str, area: str) -> dict:
    area_folder = drive.find_folder(year_folder_id, area)
    if not area_folder:
        raise FileNotFoundError(f"找不到地區資料夾：{area}")
    return area_folder


def open_area_summary(drive: DriveService, sheets: SheetsService, year: int, area: str):
    """開啟 YYYY地區薪資單總表（實際收在「台北」資料夾底下，不是各自的地區資料夾）。"""
    year_folder = open_year_folder(drive, year)
    summary_folder = open_area_folder(drive, year_folder["id"], SUMMARY_HOME_AREA)

    sheet_name = area_summary_sheet_name(year, area)
    matches = drive.find_google_sheet_by_name(summary_folder["id"], sheet_name)
    if not matches:
        raise FileNotFoundError(f"找不到試算表：{sheet_name}（資料夾：{SUMMARY_HOME_AREA}）")

    return sheets.open_by_id(matches[0]["id"])
