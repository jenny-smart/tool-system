from __future__ import annotations

TIMEZONE = "Asia/Taipei"

AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
TYPES = ["儲值金結算", "儲值金預收"]

# 主控表：Jenny's Lemonhometools
MASTER_SPREADSHEET_ID = "1nNAXy6rvBnGR8ACnqKKzKNA4-UwZtZp47i806EPmR_8"

# Drive 根目錄：「02.VIP儲值金」，底下依序是年度資料夾（例如 2026）、
# 再底下才是期別資料夾（例如 202606），共三層。
ROOT_FOLDER_ID = "15GQ7eUqUrxS95JKOaO6W_qV0g6OBXRPv"

SUMMARY_SHEET_PREFIX = "VIP儲值金彙整"
SUMMARY_FILE_NAME_TEMPLATE = "{period}VIP儲值金彙整"
PERIOD_FOLDER_TEMPLATE = "{period}"

MASTER_SYSTEM_SHEET = "系統設定"
MASTER_FORMULA_SHEET = "儲值金公式設定"
MASTER_MONTHLY_LOG_SHEET = "儲值金月度作業紀錄"
MASTER_AMOUNT_SHEET = "金額統整設定"
MASTER_EXECUTION_LOG_SHEET = "儲值金執行記錄"

# 搬運資料時每種資料來源要讀取的最後欄位
LAST_COL_BY_TYPE = {
    "儲值金結算": "T",
    "儲值金預收": "BJ",
}

# H 欄符合這些內容時：新竹儲值金結算移除，高雄儲值金結算只保留
# 注意：儲值金50000 這個方案名稱在新竹/高雄/其他地區共用，光看 H 欄名稱
# 分不出來是不是高雄的，所以不放進這個集合，改用下面的 E 欄地址關鍵字判斷。
KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE = "儲值金結算"
KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA = "新竹"
KAOHSIUNG_DERIVED_AREA = "高雄"
KAOHSIUNG_FILTER_COLUMN = "H"
KAOHSIUNG_FILTER_VALUES = {
    "儲值金18900",
    "儲值金36000",
    "儲值金9900",
    "儲值金17000",
    "儲值金19400",
}
# E 欄（地址）只要包含這些關鍵字，不管 H 欄方案名稱是什麼都算高雄
# （這樣共用方案名稱如儲值金50000 也能靠地址判斷歸到高雄）。
KAOHSIUNG_ADDRESS_COLUMN = "E"
KAOHSIUNG_ADDRESS_KEYWORDS = ["高雄", "台南"]

# 篩選新竹/高雄結算前，先留一份新竹過濾前的完整備份（檔名帶這個字尾），
# 方便事後回頭核對篩選結果對不對。這份備份不能被搬運/套公式當成正常
# 的地區資料處理，所以檔名刻意不會被 parse_area_type 誤判。
HSINCHU_PRE_FILTER_BACKUP_SUFFIX = "-篩選前備份"
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
