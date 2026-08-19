"""
tools/staff_payroll

內勤薪資管理
------------
根目錄（Drive Folder ID）：1LYmhOIcgjmO5-74zh1D1pKfVizIF5Wls
結構：根目錄 》 YYYY薪資 》 地區 》 ...

目前支援地區：台北、桃園（新竹之後補齊時直接加進 AREAS 即可）

子功能：
- settlement.py      內勤結算：把該月要發薪的員工姓名寫回「YYYY地區薪資單總表」的薪資單分頁
- pdf_export.py       內勤PDF產出：依薪資單分頁 AA1:AH15 的樣板，逐人產出 PDF 存回 Drive
- mail_notify.py       內勤薪資單通知信：組出「YYYY內勤薪資名冊」試算表裡的 mail 分頁收件清單
- yuanta_account.py 內勤元大帳戶：組出轉帳用的元大帳戶明細分頁，並另存 xlsx

各功能都是獨立函式，由 UI（Streamlit）依「地區 / 年月 / 功能」組合呼叫。
"""

from __future__ import annotations

ROOT_FOLDER_ID = "1LYmhOIcgjmO5-74zh1D1pKfVizIF5Wls"

# 目前只有台北、桃園有完整資料；新竹/台中之後補上資料夾與工作表命名規則後
# 直接把地區名稱加進這個 list 即可，其餘程式碼不需要改動。
AREAS = ["台北", "桃園"]

# 薪資單分頁 PDF 匯出範圍（單一員工薪資單樣板）
PAYROLL_TEMPLATE_RANGE = "AA1:AH15"


def year_folder_name(year: int) -> str:
    """YYYY薪資 資料夾名稱"""
    return f"{year}薪資"


def area_summary_sheet_name(year: int, area: str) -> str:
    """YYYY地區薪資單總表 試算表名稱"""
    return f"{year}{area}薪資單總表"


def roster_sheet_name(year: int) -> str:
    """YYYY內勤薪資名冊 試算表名稱"""
    return f"{year}內勤薪資名冊"


def yuanta_sheet_name(year: int, area: str) -> str:
    """YYYY元大帳戶-地區 試算表名稱"""
    return f"{year}元大帳戶-{area}"


def yyyymm_to_dotted(yyyymm: str) -> str:
    """20260807 這種 6 碼 YYYYMM -> 2026.08"""
    yyyymm = str(yyyymm).strip()
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        raise ValueError(f"yyyymm 格式錯誤，應為 6 碼 YYYYMM，收到：{yyyymm}")
    return f"{yyyymm[:4]}.{yyyymm[4:]}"


def yyyymm_to_year(yyyymm: str) -> int:
    return int(str(yyyymm)[:4])
