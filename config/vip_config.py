from __future__ import annotations

TIMEZONE = "Asia/Taipei"

AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
TYPES = ["儲值金結算", "儲值金預收"]

SUMMARY_SHEET_PREFIX = "VIP預收款彙整"
SUMMARY_FILE_NAME_TEMPLATE = "{period}VIP預收款彙整"
PERIOD_FOLDER_TEMPLATE = "{period}"

MASTER_SYSTEM_SHEET = "系統設定"
MASTER_FORMULA_SHEET = "公式設定"
MASTER_MONTHLY_LOG_SHEET = "月度作業紀錄"
MASTER_AMOUNT_SHEET = "金額統整設定"
MASTER_EXECUTION_LOG_SHEET = "執行紀錄"

# 搬運資料時每種資料來源要讀取的最後欄位
LAST_COL_BY_TYPE = {
    "儲值金結算": "T",
    "儲值金預收": "BJ",
}

# H 欄符合這些內容時：新竹儲值金結算移除，高雄儲值金結算只保留
KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE = "儲值金結算"
KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA = "新竹"
KAOHSIUNG_DERIVED_AREA = "高雄"
KAOHSIUNG_FILTER_COLUMN = "H"
KAOHSIUNG_FILTER_VALUES = {"儲值金18900", "儲值金36000", "儲值金9900"}
KAOHSIUNG_DERIVED_FILE_TEMPLATE = "{period}儲值金結算-高雄"

# 月度作業紀錄會依此順序打卡：轉檔 -> 搬運 -> 計算 -> 彙整金額
STEP_ORDER = ["轉檔", "搬運", "計算", "彙整金額"]

# 先依你指定的項目建立，程式也會自動補缺少的項目。
DEFAULT_MONTHLY_LOG_ITEMS = [
    "彙整檔建立時間",
    "來源彙整檔",
    "當月彙整檔",
    "當月資料夾",
]
for step in STEP_ORDER:
    for area in AREAS:
        for typ in TYPES:
            if step in ["轉檔", "搬運"]:
                DEFAULT_MONTHLY_LOG_ITEMS.append(f"{area}{typ}{step}筆數")
                DEFAULT_MONTHLY_LOG_ITEMS.append(f"{area}{typ}{step}時間")
            elif step == "計算":
                DEFAULT_MONTHLY_LOG_ITEMS.append(f"{area}{typ}計算公式數")
                DEFAULT_MONTHLY_LOG_ITEMS.append(f"{area}{typ}計算時間")
            elif step == "彙整金額":
                DEFAULT_MONTHLY_LOG_ITEMS.append(f"{area}{typ}總金額")
                DEFAULT_MONTHLY_LOG_ITEMS.append(f"{area}{typ}彙整金額時間")
