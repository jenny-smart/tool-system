"""
tools/annual_rollover/new_year_generate.py

換年度：依主控表「生成新年度」工作表，一列一列複製前一年度的資料夾與檔案，
產生新年度版本；每個新檔記到「生成新年度Log」，「年度工作檔ID_GID總表」M～P 欄由 Log 自動帶入新年度 ID。

「生成新年度」欄位（第 1 列為標題）：
  A 名稱
  B 資料夾（上層資料夾網址）
  C 說明
  D 前一年度資料夾範例：
      - 含年份的名稱（例：2026、2026專員回報表單）→ 指定要複製的年度資料夾
      - 不含年份的名稱（例：01.台北專員/02.台中專員、台北/桃園）→ 年度資料夾底下
        要一併建立的子資料夾
      - 空白 → 自動找名稱含前一年度的資料夾（例：2026年）
  E 檔案：要複製的前一年度檔名，以「，」或「,」分隔；檔名中的年份會換成新年度
  F 台北/台中、G 非台北/台中：說明用，程式不讀
  H 工作表整理：例「移除富邦更新/元大更新的A2:」→ 新檔中這些工作表清掉 A2 以下資料
  I 生成結果：程式回寫（時間、新資料夾與檔案 ID）

新年度 ID 不另外回寫工作表：「年度工作檔ID_GID總表」以舊 ID（或舊檔名）比對 Log 取最新一筆。

用法：
  python -m tools.annual_rollover.new_year_generate --year 2027 --rows 2 --dry-run
  python -m tools.annual_rollover.new_year_generate --year 2027 --rows 2-8
  python -m tools.annual_rollover.new_year_generate --year 2027 --rows all --fresh

預設可重複執行：新年度資料夾／檔案已存在就沿用並記錄 ID，不會重複建立。
加 --fresh 會把已存在的同名新年度資料夾／檔案改名為「<原名>_舊版<時間>」後重新生成
（只改名，不刪除）。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.vip_config import MASTER_SPREADSHEET_ID

GENERATE_SHEET = "生成新年度"
RUN_LOG_SHEET = "生成新年度Log"
# 各系統自己的「年度工作檔ID_GID總表」從本檔的「生成新年度Log」帶入新年度 ID，
# Log 同步追加到這些試算表（固定檔，不隨年度）
SYSTEM_LOG_SPREADSHEETS = {
    "salary-system（LemonSalarySystem）": "1GdW3FSZ0s3TGeYiNx3JtYvED_RRfJjiFYwLFeYHZ1hA",
    "orders-system": "1nnbpR1s-VYeClpmIbYjKfi3IiDWtBUBXuXUjQhhgYT0",
}
# 各系統「年度工作檔ID_GID總表」：N 欄起依年度排列（每年 4 欄），K1 為參照年度（使用者手動切換）
ID_TABLE_SHEET = "年度工作檔ID_GID總表"
ID_TABLE_HEADER_ROW = 3
YEAR_BLOCK_FIELDS = ("檔名（自動）", "ID（自動）", "GID（自動）", "生成時間（自動）")
RUN_LOG_HEADERS = ["執行時間", "新年度", "功能", "區域", "模式", "重新生成", "類型",
                   "前一年度名稱", "前一年度ID", "新年度名稱", "新年度ID", "狀態", "訊息"]
RESULT_COL = "I"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
TZ = ZoneInfo("Asia/Taipei")

_SPLIT_LIST = re.compile(r"[，,、]")
_SPLIT_SLASH = re.compile(r"[/／]")
_FOLDER_ID = re.compile(r"/folders/([A-Za-z0-9_-]+)")
# 「生成新年度」A 欄為這些名稱時，套用財務報表專用整理（取代 H 欄的通用清除）
FINANCE_ROW_NAMES = {"財務報表"}
# 「內勤表單」：新建立的內勤工作表單整理（取代 H 欄的通用清除）
OFFICE_ROW_NAMES = {"內勤表單"}
OFFICE_CLEAR_RANGES = ["'信用卡'!A2:J", "'專員收現'!A2:P", "'專員回報'!A2:P",
                       "'專員請款'!A2:AS", "'清潔客訴'!A3:AN",
                       "'清潔異動'!A2:J", "'清潔異動'!L2:AE"]   # 清潔異動 K 欄公式保留
OFFICE_CLEAR_WITH_FILL = [("清潔異動", "A2:AE")]  # 底色清除
# 新內勤工作表單只保留這些分頁（加上「{前一年度}目標」改名後的「{新年度}目標」），其餘刪除；
# 原檔沒有的分頁略過
OFFICE_KEEP_SHEETS = [
    "總覽", "專員班表查詢本月_1009", "專員班表查詢次月_1009", "刷卡連結", "評價預約",
    "信用卡", "ATM", "清潔異動", "專員收現", "專員回報", "專員請款", "清潔客訴",
    "客訴統計表", "專員個人資料", "偏遠區域個案服務規範與流程", "報價單", "工具組內容",
    "專員跨區表", "裝細評估", "搬家打包評估", "系數參數", "外場排程系統執行Log", "週末提醒",
]
OFFICE_KEEP_CONTAINS = ["儲值金不足"]  # 分頁名稱含這些字也保留（例：202611儲值金不足）
OFFICE_ATM_CELL = "'ATM'!A2"

# 「服務分潤表」年度資料夾（例：2026專員承攬服務費）下各區資料夾的檔案。
# func：哪個功能執行時生成（服務分潤表／專員表單／內勤表單／財務報表），新檔一律放在
#       服務分潤表新年度的地區資料夾（不存在就先建立）。
# sources 依序比對前一年度檔名（{y}＝前一年度、{a}＝地區，正規表示式；比對前檔名的
#       「_地區」會先統一成「-地區」），target 是新年度檔名（{Y}＝新年度）。
# 來源是捷徑時用捷徑指向的原始檔複製。先找本區資料夾，找不到再找其他地區資料夾。
# ops：新建立的檔案要做的分頁整理，見 Rollover.apply_ops。
SERVICE_ROW_NAMES = {"服務分潤表"}
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_ALL = ["台北", "台中", "桃園", "新竹", "高雄"]
_STAFF_MONTHLY_OPS = {
    "keep_latest": [r"^\d{6}調薪資料$", r"^\d{6}專員名冊$"],
    "rename_year": [r"^{y}薪資$", r"^{y}排班統計表$"],   # 調薪資料／專員名冊保留原月份名稱
}
SERVICE_FILE_RULES = [
    {"func": "服務分潤表", "areas": _ALL, "target": "{Y}承攬服務費mail-{a}",
     "sources": [r"{y}承攬服務費mail-{a}"],
     "ops": {"keep_only": ["mail"], "keep_latest": [r"^\d{6}-\d$"]}},
    {"func": "服務分潤表", "areas": _ALL, "target": "{Y}營業額總表-{a}",
     "sources": [r"{y}營業額總表-{a}", r"{y}{a}營業額總表"],
     # 台北／台中在財務的營業額總表資料夾另建捷徑「{Y}{a}營業額總表」指向新檔
     "shortcut": {"folders": {"台北": "1Pjldya3PQu1Fhi_oHMfCWI5kYTCVIXXU",
                              "台中": "1ou9JRoLYdmDZWmFM1kOPpAzpOSA9rBOw"},
                  "name": "{Y}{a}營業額總表"},
     "ops": {"year_columns": "{y}財報總表",
             "rename_year": [r"^{y}財報總表$", r"^{y}各服務收入金流總表$"],
             "replace_year_in": ["{Y}各服務收入金流總表"],
             "keep_only": ["{y}財報總表", "{y}各服務收入金流總表"], "keep_latest": [r"^\d{6}$"]}},
    {"func": "服務分潤表", "areas": ["台北", "台中"], "target": "{Y}專員薪資申報-{a}",
     "sources": [r"{y}專員薪資申報-{a}"],
     "ops": {"rename_year": [r"^{y}公司補充保費$"], "values": {"'{Y}公司補充保費'!A2": "{Y}"}}},
    {"func": "服務分潤表", "areas": ["桃園", "新竹"], "target": "{Y}支出明細-{a}",
     "sources": [r"{y}支出明細-{a}", r"{a}{y}支出明細", r"桃園{y}支出明細"],
     "ops": {"keep_only": ["富邦ATM"], "keep_contains": ["零用金"],
             "replace_finance_id_in": ["富邦ATM"]}},  # 公式中前一年度該區財報 ID → 新年度
    {"func": "專員表單", "areas": ["桃園", "新竹"], "target": "{Y}專員名冊薪資-{a}",
     "sources": [r"{y}專員名冊薪資-{a}", r"{y}{a}專員薪資\(外場\)", r"{y}桃園專員薪資\(外場\)"],
     "ops": {**_STAFF_MONTHLY_OPS,
             "replace_year_in": ["{Y}薪資"],
             "clear": ["'專員請款'!A3:J", "'新人實境'!A2:K"],
             "clear_months": {r"^\d{6}調薪資料$": "B3:M", r"^\d{6}專員名冊$": "B2:I"},
             "clear_constants": ["'{Y}排班統計表'!Y6:ET"],
             # 富邦ATM 引用新年度同區支出明細（需先跑「服務分潤表」）
             "lookup": {"exp": "{Y}支出明細-{a}"},
             "values": {"'場次和時數'!A2": "{Y}",
                        "'富邦ATM'!A1": '=importrange("{exp}","富邦ATM!A1:P")'}}},
    {"func": "專員表單", "areas": ["高雄"], "target": "{Y}專員名冊與時數-高雄",
     "sources": [r"{y}專員名冊與時數-高雄"], "post": "staff"},
    {"func": "專員表單", "areas": ["高雄"], "target": "{Y}專員薪資相關-高雄",
     "sources": [r"{y}專員薪資相關-高雄"], "post": "staff"},
    {"func": "內勤表單", "areas": ["桃園", "新竹"], "target": "{Y}服務異動-{a}",
     "sources": [r"{y}服務異動-{a}", r"{y}{a}退款及儲值金異動", r"{y}桃園退款及儲值金異動"],
     "ops": {"clear": ["'{y}訂單金流*'!A2:J", "'{y}訂單金流*'!M2:AE"]}},
    {"func": "內勤表單", "areas": ["高雄"], "target": "{Y}內勤工作表單-高雄",
     "sources": [r"{y}內勤工作表單-高雄", r"{y}高雄內勤工作表單"],
     "ops": {"clear": ["'專員收現'!A2:N", "'專員請款'!A2:AI", "'清潔異動'!A2:AE", "'ATM'!A2:G"],
             "values": {"'ATM'!A2": (
                 '=filter({filter(importrange("{fin}","元大更新!$A3:$i"),'
                 'importrange("{fin}","元大更新!$A3:A"))},{0,1,1,0,1,1,1,1,1})')}}},
    {"func": "財務報表", "areas": ["桃園", "新竹"], "target": "{Y}紙本／中獎發票-{a}",
     "sources": [r"{y}紙本\s*[／/]\s*中獎發票-{a}"], "ops": "invoice"},
]
# 紙本／中獎發票年度檔
INVOICE_OPS = {"rename_year": [r"^{y}$"], "clear": ["'{Y}'!A2:Y", "'中獎發票'!A2:L"],
               "delete_regex": [r"^\d{6}$"]}
# 「財務報表」功能另外處理的年度資料夾：在資料夾（或其下名稱含前一年度的子資料夾）旁
# 建立新年度資料夾，子資料夾結構照建但不複製子資料夾內的檔案；最上層名稱含前一年度的
# 試算表複製成新年度檔，依 files 套用整理。
FINANCE_YEAR_FOLDERS = [
    {"label": "台北發票", "area": "台北", "folder_id": "17EQNG8VM9N4YgxXGb23CNZxYT3HsDkQ2",
     "files": {r"紙本\s*[／/]\s*中獎發票": INVOICE_OPS}},
    {"label": "台中發票", "area": "台中", "folder_id": "12XktB_jDnd3ufkIZX2hweHdV3nv14tAH",
     "files": {r"紙本\s*[／/]\s*中獎發票": INVOICE_OPS}},
    {"label": "台北進項發票", "area": "台北", "folder_id": "1XBWWOoDjZ74HsnAGsGUr7jlf34AHJajG", "files": {}},
    {"label": "儲值金", "area": "", "folder_id": "15GQ7eUqUrxS95JKOaO6W_qV0g6OBXRPv", "files": {}},
]
# 「行銷費用總管理」：同一個檔，每年複製「{y}廣告」「{y}總表」成新年度工作表後清空實際數字
MARKETING_FILE_ID = "18KlxjxtbyjKGrmczSH3Eg3J8rem1JskhMjDE4M560m4"
MARKETING_AD_SHEET = "{y}廣告"
MARKETING_TOTAL_SHEET = "{y}總表"
# 「{Y}廣告」A～E 欄（項目名稱在 E 欄）是這些項目的列，清空各月實際欄位
MARKETING_AD_ITEM_COLS = "A:E"
MARKETING_AD_ITEMS = [
    "台北居家(SEM)", "台北品牌(SEM)", "台北大掃除(SEM)", "台北外廣(DGC)", "台北居家(YT)", "台北居家(RDA)",
    "台北裝潢(SEM)", "台北裝潢(RDA)", "台北裝潢(YT)", "台北電器(SEM)", "台北收納(SEM)",
    "桃園居家(SEM)", "桃園品牌(SEM)", "桃園大掃除(SEM)", "桃園外廣(DGC)", "桃園居家(YT)", "桃園居家(RDA)",
    "桃園裝潢(SEM)", "桃園裝潢(RDA)", "桃園裝潢(YT)", "桃園電器(SEM)", "桃園收納(SEM)",
    "新竹居家(SEM)", "新竹品牌(SEM)", "新竹大掃除(SEM)", "新竹居家(YT)", "新竹外廣(DGC)", "新竹居家(RDA)",
    "新竹裝潢(SEM)", "新竹裝潢(RDA)", "新竹裝潢(YT)", "新竹收納(SEM)",
    "台中居家(SEM)", "台中品牌(SEM)", "台中大掃除(SEM)", "台中居家(YT)", "台中外廣(DGC)", "台中居家(RDA)",
    "台中裝潢(SEM)", "台中裝潢(RDA)", "台中裝潢(YT)", "台中電器(SEM)", "台中收納(SEM)",
    "家電電器-台北(SEM)", "家電電器-台北(RDA)", "家電電器-台北(YT)", "家電外廣(DGC)",
    "家電電器-桃園(SEM)", "家電電器-桃園(RDA)", "家電電器-桃園(YT)",
    "家電電器-新竹(SEM)", "家電電器-新竹(RDA)", "家電電器-新竹(YT)",
    "家電電器-台中(SEM)", "家電電器-台中(RDA)", "家電電器-台中(YT)",
]
MARKETING_AD_COLUMNS = ["F", "I", "L", "O", "R", "U", "X", "AA", "AD", "AG", "AJ", "AM"]  # 各月實際
MARKETING_TOTAL_CLEAR = ["C24:CH29", "C47:CH64", "C74:CH85"]

# 「清潔用品」：根目錄下「{y}清潔用品訂購記錄-四店共用new」複製成新年度後清空
SUPPLY_FOLDER_ID = "1GGD6fjEgvepKM3QWMe4A5MYBmSNXB39v"
SUPPLY_FILE_NAME = "{y}清潔用品訂購記錄-四店共用new"
SUPPLY_PURCHASE_PREFIX = "進貨明細表-"
SUPPLY_PURCHASE_CLEAR = ["A3:A", "C3:C", "E3:E", "F3:F", "G3:K", "M3:M", "O3:T"]
SUPPLY_PURCHASE_FALSE_COL = "U"          # U3:U 設為 FALSE
SUPPLY_STOCK_PREFIX = "庫存統計表-"         # 各地區庫存統計表
SUPPLY_STOCK_COLS = ("C", "AY")
SUPPLY_STOCK_OPENING = "期初庫存"          # B 欄為此值的列與第 2 列清空 C:AY；A 欄年月標籤改為新年度
# 不含儲存格參照的公式（例如 =87369、=1200+300）視同手打數字，清空時一併清除
_LITERAL_FORMULA = re.compile(r"=[\d\s.+\-*/(),%]*")

# 「春節禮品名單」分兩階段：
#   第一階段（各地區資料統計）：根目錄「春節禮品」下建立新年度資料夾 {Y}，登入各區後台下載「儲值金結算」
#     （同月排程儲值金結算的會員匯出）存成「{Y}儲值金結算-{a}」，轉檔後貼入「{Y}名單統整」的「{a}儲值金」
#     工作表（高雄沒有自己的結算，跟儲值金結算轉檔一樣從新竹拆出來），每張建樞紐（B 客戶姓名、
#     G 購買日期在前一年度、加總 I 購買原價），並依金額區間統計人數
#   第二階段（設定金額產生送禮名單）：畫面輸入的門檻金額寫入「設定」B1，「{Y}送禮名單」列出加總 ≥ 門檻
#     的客戶，再貼進新年度「{Y}春節送禮名單」並在根目錄總表加分頁
GIFT_ROOT_FOLDER_ID = "1Vg6hg762A5fXkVVMVA_2sfYldTMoATz5"
GIFT_ROOT_FILE = "春節送禮名單"               # 根目錄總表，每年一個分頁 IMPORTRANGE 當年度檔
GIFT_ROOT_TAB = "{y}春節送禮名單"
GIFT_SUMMARY_FILE = "{Y}名單統整"
GIFT_SEND_FILE = "{y}春節送禮名單"            # 年度資料夾內的送禮名單檔
GIFT_AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
GIFT_SOURCE_KEYWORD = "儲值金結算"
GIFT_AREA_SHEET = "{a}儲值金"
GIFT_SETTING_SHEET = "設定"
GIFT_THRESHOLD_CELL = "B1"
GIFT_ZIP_SHEET = "郵遞區號"
GIFT_LIST_SHEET = "{Y}送禮名單"
GIFT_LIST_HEADERS = ["地區", "姓名", "郵遞區號", "行政區", "地址", "電話", "LINE連結"]
# 來源（儲值金結算會員匯出）欄位：B 客戶姓名、D 電話、E 地址、F LINE@、G 購買日期、H 購買項目、I 購買原價
GIFT_NAME_COL, GIFT_DATE_COL, GIFT_AMOUNT_COL = "B", "G", "I"
GIFT_ADDRESS_COL, GIFT_PHONE_COL, GIFT_LINE_COL = "E", "D", "F"
GIFT_PLAN_COL = "H"
# 只加總 H 購買項目是「儲值金…」的列：結算裡另有「轉換」（舊系統轉入的餘額）與「購物金」（贈送），不算消費
GIFT_PLAN_PREFIX = "儲值金"
GIFT_EXCLUDE_NAME_KEYWORD = "保留"             # 檸檬保留、檸檬保留單、代客保留等內部帳號不列入
GIFT_SPLIT_FROM = {"高雄": "新竹"}                # 高雄從新竹的結算拆出來（新竹後台含高雄）
GIFT_DISTRICT_CHARS = 6                       # 行政區＝地址前 6 個字
# 送禮名單檔：清空 J2:L 與第 18 列以下，第 21 列起貼上；送禮名單 A～G → 這些欄（F 欄是勾選框）
GIFT_SEND_CLEAR = "J2:L"
GIFT_SEND_CLEAR_ROW = 18
GIFT_SEND_PASTE_ROW = 21
GIFT_SEND_COLUMNS = ["A", "B", "C", "D", "E", "G", "H"]
GIFT_SEND_CHECKBOX_COL = "F"
GIFT_STAT_FUNCTION = "春節禮品名單統計"         # 第一階段
GIFT_LIST_FUNCTION = "春節送禮名單產生"         # 第二階段
# 第一階段依加總金額分區間統計人數：<5萬、5～8萬、8～10萬、10～12萬、≥12萬（下限含、上限不含）
GIFT_STAT_BANDS = [50000, 80000, 100000, 120000]
GIFT_STAT_SHEET = "區間統計"
_IMPORTRANGE = re.compile(r'(?i)importrange\(\s*"([^"]+)"\s*,\s*"([^"!]+)!([^"]+)"\s*\)')

EXTRA_FUNCTIONS = {"行銷費用總管理", "清潔用品", GIFT_STAT_FUNCTION, GIFT_LIST_FUNCTION}   # 不依「生成新年度」列的功能

# 「生成新年度」A 欄名稱 → 功能名稱
ROW_FUNCTIONS = {"財務報表": "財務報表", "內勤表單": "內勤表單", "專員名冊/薪資檔": "專員表單",
                 "服務分潤表": "服務分潤表", "內勤薪資": "內勤薪資"}

# 「專員名冊/薪資檔」：新建立的專員名冊與時數、專員薪資相關整理（取代 H 欄的通用清除）
STAFF_ROW_NAMES = {"專員名冊/薪資檔"}
ROSTER_KEYWORD = "專員名冊與時數"
SALARY_KEYWORD = "專員薪資相關"
ROSTER_SCHEDULE_SHEET = "{year}排班統計表"   # 改名為新年度；每月「地區」～「備註」清除第 5 列以下
ROSTER_SCHEDULE_LAST_COL = "GB"
ROSTER_CLEAR_RANGES = ["'應徵履歷統計'!A2:K", "'104應徵履歷'!A2:G", "'面試紀錄'!A2:K"]
ROSTER_MONTHLY_SHEET = re.compile(r"^(\d{6})專員名冊$")      # 只留最近一個月
SALARY_CLEAR_RANGES = ["'場次和時數'!E2:AN", "'教育訓練簽到名單'!A2:AE", "'工具包押金退款'!A2:I",
                       "'新人實境'!A2:K", "'新人實習'!A2:K", "'外場現金出入記錄'!A2:J"]
SALARY_YEAR_CELLS = ["'場次和時數'!A2", "'外場現金出入記錄'!A1"]
SALARY_DEPOSIT_SHEET = "工具包押金"   # G 欄非空白（已全數提領）的列刪除
SALARY_DEPOSIT_COL = "G"
SALARY_MONTHLY_SHEET = re.compile(r"^(\d{6})調薪資料$")      # 只留最近一個月
SALARY_MONTHLY_EXTRA = "調薪"   # 名稱含這個字的其他分頁（-old、副本、藏函數用…）一併刪除
OFFICE_ATM_FORMULA = (
    '=filter({{filter(importrange("{fid}","富邦更新!$A2:$H"),'
    'importrange("{fid}","富邦更新!$A2:A"))}},{{0,1,1,1,1,1,0,1}})'
)
FINANCE_AREAS = ["台北", "台中", "桃園", "新竹", "高雄", "電器"]
FINANCE_REVIEW_KEYWORD = "目標及review"
FINANCE_PL_SHEET = "股東損益表_財務"
FINANCE_PL_CELL = "A241"
FINANCE_YEAR_CELLS = ["'科目對照表'!E1"]  # 新年度檔案中改成新年度年份
FINANCE_CLEAR_RANGES = {
    "台北/台中": ["'請款記錄'!A2:J", "'富邦更新'!A2:H", "'元大更新'!A2:I", "'零用金'!A3:H"],
    "其他": ["'富邦更新'!A2:H", "'元大更新'!A2:I"],
}


def finance_area(name: str) -> str:
    return next((area for area in FINANCE_AREAS if str(name).startswith(area)), "")


def gift_cell(value: Any) -> Any:
    """來源儲存格以 USER_ENTERED 寫入（日期、金額才會是日期與數字）；= 或 + 開頭的文字加 ' 保持原樣。"""
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+") else value


def gift_headers(header: List[str]) -> List[str]:
    """空白（含全形空白）標題改成「欄X」，樞紐篩選才不會因重複的空白標題對不到欄位。"""
    return [h if str(h).strip(" 　") else f"欄{col_letters(i)}" for i, h in enumerate(header)]


def gift_pivot(sheet_id: int, rows: int, cols: int, prev_year: int, date_header: str) -> Dict[str, Any]:
    """樞紐：列＝客戶姓名（依加總由大到小、不顯示總計），值＝加總購買原價；篩選購買日期在前一年度、
    購買項目是「儲值金…」（不含轉換、購物金）、姓名不含「保留」（內部帳號）。"""
    return {
        "source": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": rows,
                   "startColumnIndex": 0, "endColumnIndex": cols},
        "rows": [{"sourceColumnOffset": col_index(GIFT_NAME_COL), "showTotals": False,
                  "sortOrder": "DESCENDING", "valueBucket": {"valuesIndex": 0}}],
        "values": [{"sourceColumnOffset": col_index(GIFT_AMOUNT_COL), "summarizeFunction": "SUM",
                    "name": f"{prev_year}加總"}],
        # visibleByDefault 要設 true，否則條件會跟空的 visibleValues 取交集而什麼都不顯示
        "filterSpecs": [
            {"columnOffsetIndex": col_index(GIFT_DATE_COL), "filterCriteria": {
                "visibleByDefault": True,
                "condition": {"type": "CUSTOM_FORMULA", "values": [
                    {"userEnteredValue": f"=YEAR('{date_header}')={prev_year}"}]}}},
            {"columnOffsetIndex": col_index(GIFT_PLAN_COL), "filterCriteria": {
                "visibleByDefault": True,
                "condition": {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": GIFT_PLAN_PREFIX}]}}},
            {"columnOffsetIndex": col_index(GIFT_NAME_COL), "filterCriteria": {
                "visibleByDefault": True,
                "condition": {"type": "TEXT_NOT_CONTAINS", "values": [
                    {"userEnteredValue": GIFT_EXCLUDE_NAME_KEYWORD}]}}},
        ],
    }


def _wan(amount: int) -> str:
    return f"{amount / 10000:g}萬"


def gift_band_labels(bands: List[int] = GIFT_STAT_BANDS) -> List[str]:
    """[5萬, 8萬, 10萬, 12萬] → ["5萬以下", "5萬～8萬", "8萬～10萬", "10萬～12萬", "12萬以上"]。"""
    return ([f"{_wan(bands[0])}以下"] + [f"{_wan(a)}～{_wan(b)}" for a, b in zip(bands, bands[1:])]
            + [f"{_wan(bands[-1])}以上"])


def gift_band_counts(amounts: List[float], bands: List[int] = GIFT_STAT_BANDS) -> List[int]:
    """各區間人數（下限含、上限不含），順序同 gift_band_labels。"""
    edges = [float("-inf")] + list(bands) + [float("inf")]
    return [sum(lo <= a < hi for a in amounts) for lo, hi in zip(edges, edges[1:])]


def gift_stats_table(blocks: List[Tuple[str, str, str, str]], bands: List[int] = GIFT_STAT_BANDS
                     ) -> List[List[str]]:
    """「區間統計」工作表內容：各地區一列，人數／加總／各區間人數都用公式（樞紐更新就跟著變）。"""
    edges = [None] + list(bands) + [None]
    rows = [["地區", "人數", "加總"] + gift_band_labels(bands)]
    for i, (area, sheet, name_col, sum_col) in enumerate(blocks, start=2):
        names, sums = f"'{sheet}'!{name_col}2:{name_col}", f"'{sheet}'!{sum_col}2:{sum_col}"
        row = [area, f'=COUNTIFS({names},"<>",{sums},">0")', f"=SUM({sums})"]
        for lo, hi in zip(edges, edges[1:]):
            cond = [f'{sums},">0"'] if lo is None else [f'{sums},">={lo}"']
            if hi is not None:
                cond.append(f'{sums},"<{hi}"')
            row.append(f"=COUNTIFS({names},\"<>\",{','.join(cond)})")
        rows.append(row)
    if len(rows) > 1:
        last = len(rows)
        rows.append(["全區"] + [f"=SUM({col_letters(c)}2:{col_letters(c)}{last})"
                                for c in range(1, len(rows[0]))])
    return rows


def gift_list_formula(blocks: List[Tuple[str, str, str, str]], zip_rows: int) -> str:
    """「{Y}送禮名單」A2 公式。blocks＝[(地區, 地區工作表, 樞紐姓名欄, 樞紐加總欄)]；
    「設定」B1 門檻空白時顯示提示，其餘列出各地區加總 ≥ 門檻的客戶與地址／電話／LINE。"""
    t = f"'{GIFT_SETTING_SHEET}'!${GIFT_THRESHOLD_CELL[0]}${GIFT_THRESHOLD_CELL[1:]}"
    zb = f"'{GIFT_ZIP_SHEET}'!$B$2:$B${zip_rows + 1}"
    za = f"'{GIFT_ZIP_SHEET}'!$A$2:$A${zip_rows + 1}"
    empty = "{" + ",".join(['""'] * len(GIFT_LIST_HEADERS)) + "}"
    parts = []
    for area, sheet, name_col, sum_col in blocks:
        s = f"'{sheet}'!"

        def look(col: str) -> str:  # 同名有多列時取第一個非空白值
            return (f"IFERROR(INDEX(FILTER({s}${col}:${col},{s}${GIFT_NAME_COL}:${GIFT_NAME_COL}=x,"
                    f"{s}${col}:${col}<>\"\"),1),\"\")")

        parts.append(
            f"IFERROR(LET(n,FILTER({s}{name_col}2:{name_col},{s}{sum_col}2:{sum_col}>=t,{s}{name_col}2:{name_col}<>\"\"),"
            f"a,MAP(n,LAMBDA(x,{look(GIFT_ADDRESS_COL)}&\"\")),"
            f"HSTACK(MAP(n,LAMBDA(x,\"{area}\")),n,"
            f"MAP(a,LAMBDA(v,LET(w,SUBSTITUTE(REGEXREPLACE(v,\"^[0-9\\s]+\",\"\"),\"臺\",\"台\"),"
            f"IF(w=\"\",\"\",XLOOKUP(TRUE,ARRAYFORMULA(LEFT(w,LEN({zb}))={zb}),{za},\"\"))))),"
            f"MAP(a,LAMBDA(v,LEFT(v,{GIFT_DISTRICT_CHARS}))),a,"
            f"MAP(n,LAMBDA(x,LET(p,{look(GIFT_PHONE_COL)},IF(ISNUMBER(p),TEXT(p,\"0000000000\"),p)))),"
            f"MAP(n,LAMBDA(x,{look(GIFT_LINE_COL)})))),{empty})")
    body = f"LET(all,VSTACK({','.join(parts)}),FILTER(all,INDEX(all,,2)<>\"\"))" if parts else '""'
    return (f"=LET(t,{t},IF(t=\"\",\"請先在「{GIFT_SETTING_SHEET}」{GIFT_THRESHOLD_CELL} 填寫送禮門檻金額\","
            f"IFERROR({body},\"沒有符合門檻的客戶\")))")


_CLEANUP = re.compile(r"移除(.+?)的([A-Z]+\d+):?([A-Z]*\d*)")


# ============================================================
# 解析
# ============================================================
@dataclass
class RowSpec:
    row_number: int
    name: str
    folder_id: str
    year_folder_names: List[str] = field(default_factory=list)
    sub_folder_names: List[str] = field(default_factory=list)
    file_names: List[str] = field(default_factory=list)
    cleanup_sheets: List[str] = field(default_factory=list)
    cleanup_start: str = "A2"
    area: str = ""  # F 欄（台北/台中…），單一地區時用來找該區財報


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_letters(index: int) -> str:
    """0-based 欄號 → 欄名（0→A、17→R）。"""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def month_block_starts(header: List[str]) -> List[int]:
    """排班統計表第 4 列中每個月「地區」標題的欄號（0-based）。"""
    return [i for i, v in enumerate(header) if str(v).strip() == "地區"]


def a1_to_grid(sheet_id: int, a1: str) -> Dict[str, int]:
    """「A2:AE」→ GridRange（0-based、結束不含；沒寫結束列＝到底）。"""
    start, end = a1.split(":")
    s_col, s_row = re.match(r"([A-Z]+)(\d+)", start).groups()
    e_col, e_row = re.match(r"([A-Z]+)(\d*)", end).groups()
    grid = {"sheetId": sheet_id, "startRowIndex": int(s_row) - 1,
            "startColumnIndex": col_index(s_col), "endColumnIndex": col_index(e_col) + 1}
    if e_row:
        grid["endRowIndex"] = int(e_row)
    return grid


ALL_AREA = "全區"


def mentions_area(text: str, area: str) -> bool:
    names = [area] + [alias for alias, canon in NAME_ALIASES.items() if canon == area]
    return any(n in text for n in names)


def filter_spec_by_area(spec: "RowSpec", area: str) -> Optional["RowSpec"]:
    """依執行區域篩選：列有 F 欄單一地區時整列比對；否則只留檔名／子資料夾含該地區的項目
    （例：選「台北」只複製「台北2026財報」，選「目標及review」只複製 review）。"""
    if not area or area == ALL_AREA:
        return spec
    if spec.area:
        return spec if spec.area == area else None
    files = [f for f in spec.file_names if mentions_area(f, area)]
    subs = [d for d in spec.sub_folder_names if mentions_area(d, area)]
    if not files and not subs:
        return None
    spec.file_names, spec.sub_folder_names = files, subs
    return spec


def split_list(text: str, pattern: re.Pattern = _SPLIT_LIST) -> List[str]:
    return [part.strip() for part in pattern.split(str(text or "")) if part.strip()]


def folder_id_from_url(value: str) -> str:
    value = str(value or "").strip()
    match = _FOLDER_ID.search(value)
    if match:
        return match.group(1)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value) else ""


_AREA_SUFFIX = re.compile(r"\s*[_\-]\s*(台北|台中|桃園|新竹|高雄|電器|家電|新北)$")


def dash_area(name: str) -> str:
    """新年度檔名地區前一律用連字號：「2027專員名冊與時數_台北」→「2027專員名冊與時數-台北」。
    也用來比對前一年度檔名（-地區 與 _地區 視為相同）。"""
    return _AREA_SUFFIX.sub(r"-\1", str(name or "").strip())


def replace_year(name: str, prev_year: int, new_year: int) -> str:
    return re.sub(rf"(?<!\d){prev_year}(?!\d)", str(new_year), name)


# 檔名比對時的同義寫法
NAME_ALIASES = {"家電": "電器"}


def new_year_file_name(name: str) -> str:
    """新年度檔名：家電區財報只有 2026 年叫「電器2026財報」，其他年度一律「家電YYYY財報」。"""
    return re.sub(r"^電器(?=20\d{2}財報$)", "家電", name)


def strip_year(name: str) -> str:
    """去掉年份、空白與結尾「檔」字並統一同義字，用來比對「2026台北財報」與
    「台北2026財報」、「2026家電財報」與「電器2026財報」、「2026目標及review檔」
    與「2026目標及review」這類寫法。"""
    text = re.sub(r"(?<!\d)20\d{2}(?!\d)", "", re.sub(r"\s+", "", dash_area(name)))
    for alias, canonical in NAME_ALIASES.items():
        text = text.replace(alias, canonical)
    return re.sub(r"檔$", "", text)


def parse_cleanup(text: str) -> Tuple[List[str], str]:
    match = _CLEANUP.search(str(text or ""))
    if not match:
        return [], "A2"
    return split_list(match.group(1), _SPLIT_SLASH), match.group(2)


def parse_row(row_number: int, row: List[str], prev_year: int) -> Optional[RowSpec]:
    cells = [str(c or "").strip() for c in row] + [""] * 8
    folder_id = folder_id_from_url(cells[1])
    if not folder_id:
        return None
    year_names: List[str] = []
    sub_names: List[str] = []
    for part in split_list(cells[3], _SPLIT_SLASH):
        (year_names if str(prev_year) in part else sub_names).append(part)
    cleanup_sheets, cleanup_start = parse_cleanup(cells[7])
    return RowSpec(
        row_number=row_number,
        name=cells[0],
        folder_id=folder_id,
        year_folder_names=year_names,
        sub_folder_names=sub_names,
        file_names=split_list(cells[4]),
        cleanup_sheets=cleanup_sheets,
        cleanup_start=cleanup_start,
        area=cells[5] if "/" not in cells[5] and "／" not in cells[5] else "",
    )


def parse_rows_arg(text: str, last_row: int) -> List[int]:
    text = str(text or "all").strip().lower()
    if text in ("", "all"):
        return list(range(2, last_row + 1))
    rows: List[int] = []
    for part in split_list(text):
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            rows.extend(range(start, end + 1))
        else:
            rows.append(int(part))
    return sorted(set(r for r in rows if r >= 2))


# ============================================================
# Google API
# ============================================================
class Rollover:
    def __init__(self, drive, sheets, spreadsheet_id: str, new_year: int,
                 dry_run: bool = False, fresh: bool = False, tidy: bool = False, log=print,
                 gift_threshold: Optional[float] = None, gift_downloader=None, gift_phone_lookup=None):
        self.drive = drive
        self.sheets = sheets
        self.spreadsheet_id = spreadsheet_id
        self.new_year = new_year
        self.prev_year = new_year - 1
        self.dry_run = dry_run
        self.fresh = fresh
        self.tidy = tidy  # 行銷費用總管理／清潔用品：已存在的新年度檔不重建，直接再整理一次
        self.log = log
        self.gift_threshold = gift_threshold  # 春節送禮名單產生：畫面輸入的門檻金額
        self.gift_downloader = gift_downloader  # 測試用：取代後台下載 (city, prev_year, log) -> (內容, 副檔名, 筆數)
        self.gift_phone_lookup = gift_phone_lookup  # 測試用：取代手機查近期訂單 (phone) -> 新竹／高雄／""
        self.stamp = datetime.now(TZ).strftime("%Y%m%d%H%M")

    # ---------- Drive ----------
    def children(self, folder_id: str) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        token = None
        while True:
            res = self.drive.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id,name,mimeType,shortcutDetails)",
                pageToken=token, pageSize=1000,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            files.extend(res.get("files", []))
            token = res.get("nextPageToken")
            if not token:
                return files

    def _retire(self, item: Dict[str, Any]) -> None:
        new_name = f"{item['name']}_舊版{self.stamp}"
        self.log(f"  --fresh：既有「{item['name']}」改名為「{new_name}」")
        if not self.dry_run:
            self.drive.files().update(
                fileId=item["id"], body={"name": new_name}, supportsAllDrives=True,
            ).execute()

    def ensure_folder(self, parent_id: str, name: str) -> Dict[str, Any]:
        existing = [f for f in self.children(parent_id)
                    if f["mimeType"] == FOLDER_MIME and f["name"] == name]
        if existing and not self.fresh:
            self.log(f"  資料夾已存在，沿用：{name}")
            return existing[0]
        for item in existing:
            self._retire(item)
        self.log(f"  建立資料夾：{name}")
        if self.dry_run:
            return {"id": f"(dry-run:{name})", "name": name}
        return self.drive.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id,name", supportsAllDrives=True,
        ).execute()

    def ensure_copy(self, source: Dict[str, Any], parent_id: str, name: str) -> Tuple[Dict[str, Any], bool]:
        existing = [] if parent_id.startswith("(dry-run") else [
            f for f in self.children(parent_id)
            if f["mimeType"] != FOLDER_MIME and f["name"] == name
        ]
        if existing and not self.fresh:
            self.log(f"  檔案已存在，沿用：{name}")
            return existing[0], False
        for item in existing:
            self._retire(item)
        self.log(f"  複製檔案：{source['name']} → {name}")
        if self.dry_run:
            return {"id": f"(dry-run:{name})", "name": name, "mimeType": source.get("mimeType")}, True
        copied = self.drive.files().copy(
            fileId=source["id"], body={"name": name, "parents": [parent_id]},
            fields="id,name,mimeType", supportsAllDrives=True,
        ).execute()
        return copied, True

    # ---------- 執行 Log ----------
    def append_run_log(self, func: str, area: str, records: List[Dict[str, str]],
                       error: str = "") -> None:
        """在主控表「生成新年度Log」追加：一列摘要＋每個資料夾／檔案一列（分頁不存在就建立）。"""
        try:
            meta = self.sheets.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title").execute()
            titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
            if RUN_LOG_SHEET not in titles:
                self.sheets.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={
                    "requests": [{"addSheet": {"properties": {"title": RUN_LOG_SHEET}}}]}).execute()
                self.sheets.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id, range=f"'{RUN_LOG_SHEET}'!A1",
                    valueInputOption="RAW", body={"values": [RUN_LOG_HEADERS]}).execute()
            now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")
            mode = "預覽" if self.dry_run else "執行"
            fresh = "是" if self.fresh else ""
            missing = [r for r in records if r.get("new_name") == "找不到來源"]
            if error:
                status, message = "失敗", error
            elif missing:
                status, message = "部分失敗", f"找不到或失敗 {len(missing)} 項"
            else:
                status, message = "成功", f"共 {len(records)} 項"
            rows = [[now, self.new_year, func, area, mode, fresh, "摘要", "", "", "", "", status, message]]
            for r in records:
                failed = r.get("new_name") == "找不到來源"
                state = "失敗" if failed else ("新建" if r.get("created") else
                                             ("沿用" if r["kind"] == "檔案" else ""))
                rows.append([now, self.new_year, func, area, mode, fresh, r["kind"],
                             r.get("old_name", ""), r.get("old_id", ""),
                             "" if failed else r.get("new_name", ""), r.get("new_id", ""), state, ""])
            self.sheets.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id, range=f"'{RUN_LOG_SHEET}'!A1",
                valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": rows}).execute()
        except Exception as exc:  # Log 失敗不影響主流程
            self.log(f"  ⚠ 寫入「{RUN_LOG_SHEET}」失敗：{exc}")
            return
        for label, sid in SYSTEM_LOG_SPREADSHEETS.items():
            try:
                self.sheets.spreadsheets().values().append(
                    spreadsheetId=sid, range=f"'{RUN_LOG_SHEET}'!A1", valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS", body={"values": rows}).execute()
            except Exception as exc:
                self.log(f"  ⚠ 寫入 {label}「{RUN_LOG_SHEET}」失敗：{exc}")
        if not self.dry_run and not error:
            for sid in [self.spreadsheet_id, *SYSTEM_LOG_SPREADSHEETS.values()]:
                try:
                    self.ensure_year_columns(sid)
                except Exception as exc:
                    self.log(f"  ⚠ 新增「{ID_TABLE_SHEET}」{self.new_year} 年度欄位失敗：{exc}")

    def ensure_year_columns(self, spreadsheet_id: str) -> None:
        """在「年度工作檔ID_GID總表」最右側加上新年度 4 欄（已存在就略過）。

        新欄位以公式從本檔「生成新年度Log」帶入：用前一年度 ID（或檔名）比對 Log 的前一年度 ID，
        取該年度最近一次執行的新年度名稱／ID 與生成時間；GID 沿用前一年度（複製檔案 GID 不變）。
        只加欄位，不改 K1 參照年度，生效 ID／GID 由使用者切換參照年度才啟用。
        """
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
        props = next((s["properties"] for s in meta.get("sheets", [])
                      if s["properties"].get("title") == ID_TABLE_SHEET), None)
        if not props:
            return
        row = ID_TABLE_HEADER_ROW
        header = (self.sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{ID_TABLE_SHEET}'!{row}:{row}"
        ).execute().get("values") or [[]])[0]

        def find(prefix: str) -> Optional[int]:
            return next((i for i, h in enumerate(header) if str(h).startswith(prefix)), None)

        if find(f"{self.new_year} ID") is not None:
            return
        prev_name, prev_id, prev_gid = (find(f"{self.prev_year} 檔"), find(f"{self.prev_year} ID"),
                                        find(f"{self.prev_year} GID"))
        if None in (prev_name, prev_id, prev_gid):
            self.log(f"  ⚠ {ID_TABLE_SHEET} 找不到 {self.prev_year} 年度欄位，未新增 {self.new_year} 欄位")
            return
        start = len(header)
        grid_cols = props.get("gridProperties", {}).get("columnCount", 0)
        if grid_cols < start + len(YEAR_BLOCK_FIELDS):
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [
                {"appendDimension": {"sheetId": props["sheetId"], "dimension": "COLUMNS",
                                     "length": start + len(YEAR_BLOCK_FIELDS) - grid_cols}}]}).execute()

        first = row + 1
        pid, pnm, pg = (f"{col_letters(c)}{first}:{col_letters(c)}" for c in (prev_id, prev_name, prev_gid))
        new_id_col = f"{col_letters(start + 1)}{first}:{col_letters(start + 1)}"
        log = f"'{RUN_LOG_SHEET}'"

        def pick(log_col: str) -> str:
            return (f"=MAP({pid}, {pnm}, LAMBDA(id, nm, IF(AND(id=\"\", nm=\"\"), \"\", IFERROR(CHOOSEROWS("
                    f"FILTER({log}!${log_col}$2:${log_col}, IF(id<>\"\", {log}!$I$2:$I=id, {log}!$H$2:$H=nm), "
                    f"{log}!$B$2:$B={self.new_year}, {log}!$E$2:$E=\"執行\", {log}!$K$2:$K<>\"\"), -1), \"\"))))")

        values = [
            [f"{self.new_year} {f}" for f in YEAR_BLOCK_FIELDS],
            [pick("J"), pick("K"), f"=MAP({pg}, {new_id_col}, LAMBDA(g, n, IF(n=\"\", \"\", g)))", pick("A")],
        ]
        rng = f"'{ID_TABLE_SHEET}'!{col_letters(start)}{row}:{col_letters(start + 3)}{first}"
        self.sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=rng, valueInputOption="USER_ENTERED",
            body={"values": values}).execute()
        self.log(f"  {ID_TABLE_SHEET} 新增 {self.new_year} 年度欄位（{rng}）；"
                 f"確認後改 K1 參照年度為 {self.new_year} 才會啟用")

    # ---------- Sheets ----------
    def read(self, a1: str) -> List[List[str]]:
        res = self.sheets.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=a1,
        ).execute()
        return res.get("values", [])

    def write(self, a1: str, values: List[List[Any]]) -> None:
        if self.dry_run:
            return
        self.sheets.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id, range=a1,
            valueInputOption="RAW", body={"values": values},
        ).execute()

    def cleanup_sheets(self, file_id: str, sheet_names: List[str], start: str) -> None:
        if not sheet_names or self.dry_run:
            if sheet_names:
                self.log(f"  （dry-run）會清除 {'/'.join(sheet_names)} 的 {start} 以下資料")
            return
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties.title",
        ).execute()
        titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
        ranges = [f"'{name}'!{start}:ZZZ" for name in sheet_names if name in titles]
        if ranges:
            self.sheets.spreadsheets().values().batchClear(
                spreadsheetId=file_id, body={"ranges": ranges},
            ).execute()
            self.log(f"  已清除：{', '.join(ranges)}")

    # ---------- 主流程 ----------
    def find_year_folders(self, spec: RowSpec) -> List[Dict[str, Any]]:
        folders = [f for f in self.children(spec.folder_id) if f["mimeType"] == FOLDER_MIME]
        if spec.year_folder_names:
            wanted = set(spec.year_folder_names)
            found = [f for f in folders if f["name"] in wanted]
            missing = wanted - {f["name"] for f in found}
            if missing:
                raise RuntimeError(f"找不到前一年度資料夾：{'、'.join(sorted(missing))}")
            return found
        pattern = re.compile(rf"(?<!\d){self.prev_year}(?!\d)")
        found = [f for f in folders if pattern.search(f["name"])]
        if len(found) > 1:
            # 有多個時優先取「2026」或「2026年」這種純年度資料夾
            exact = [f for f in found if re.fullmatch(rf"{self.prev_year}年?", f["name"])]
            found = exact or found
        return found[:1]

    def process_row(self, spec: RowSpec) -> List[Dict[str, str]]:
        """回傳生成紀錄：[{kind, old_name, old_id, new_name, new_id}]"""
        records: List[Dict[str, str]] = []
        year_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

        for old in self.find_year_folders(spec):
            new = self.ensure_folder(spec.folder_id, replace_year(old["name"], self.prev_year, self.new_year))
            year_pairs.append((old, new))
            records.append({"kind": "資料夾", "old_name": old["name"], "old_id": old["id"],
                            "new_name": new["name"], "new_id": new["id"]})

        # 前一年度資料夾裡的子資料夾（D 欄不含年份的名稱）
        sub_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for old_year, new_year in year_pairs:
            if not spec.sub_folder_names:
                continue
            old_subs = {f["name"]: f for f in self.children(old_year["id"]) if f["mimeType"] == FOLDER_MIME}
            for sub_name in spec.sub_folder_names:
                if new_year["id"].startswith("(dry-run"):
                    self.log(f"  建立子資料夾：{new_year['name']}/{sub_name}")
                    new_sub = {"id": f"(dry-run:{sub_name})", "name": sub_name}
                else:
                    new_sub = self.ensure_folder(new_year["id"], sub_name)
                records.append({"kind": "子資料夾", "old_name": sub_name,
                                "old_id": old_subs.get(sub_name, {}).get("id", ""),
                                "new_name": f"{new_year['name']}/{sub_name}", "new_id": new_sub["id"]})
                if sub_name in old_subs:
                    sub_pairs.append((old_subs[sub_name], new_sub))

        if spec.name in SERVICE_ROW_NAMES:
            old_subs_all = [old for old, _ in sub_pairs]
            for old_sub, new_sub in sub_pairs:
                records += self.copy_service_files(old_sub, new_sub, old_subs_all, func="服務分潤表")

        # 檔案：先找年度資料夾、再找子資料夾、最後找上層資料夾
        search_pairs = year_pairs + sub_pairs + [({"id": spec.folder_id}, {"id": spec.folder_id})]
        listing_cache: Dict[str, List[Dict[str, Any]]] = {}
        for file_name in spec.file_names:
            source, target_parent = None, None
            for old_parent, new_parent in search_pairs:
                files = listing_cache.setdefault(old_parent["id"], self.children(old_parent["id"]))
                match = next((f for f in files if f["mimeType"] != FOLDER_MIME
                              and dash_area(f["name"]) == dash_area(file_name)), None)
                if match:
                    source, target_parent = match, new_parent
                    break
            if not source:
                self.log(f"  ⚠ 找不到檔案：{file_name}")
                records.append({"kind": "檔案", "old_name": file_name, "old_id": "",
                                "new_name": "找不到來源", "new_id": ""})
                continue
            new_name = new_year_file_name(dash_area(replace_year(file_name, self.prev_year, self.new_year)))
            copied, created = self.ensure_copy(source, target_parent["id"], new_name)
            if (created and copied.get("mimeType") == SHEET_MIME
                    and spec.name not in FINANCE_ROW_NAMES | OFFICE_ROW_NAMES | STAFF_ROW_NAMES
                    | SERVICE_ROW_NAMES):
                self.cleanup_sheets(copied["id"], spec.cleanup_sheets, spec.cleanup_start)
            records.append({"kind": "檔案", "old_name": source["name"], "old_id": source["id"],
                            "new_name": copied["name"], "new_id": copied["id"], "created": created})
        if spec.name in FINANCE_ROW_NAMES:
            self.finance_post_process(records)
        if spec.name in OFFICE_ROW_NAMES:
            self.office_post_process(spec, records)
        if spec.name in STAFF_ROW_NAMES:
            self.staff_post_process(records)
        return records

    # ---------- 專員名冊／薪資檔專用整理 ----------
    def _sheet_map(self, file_id: str) -> Dict[str, int]:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties(sheetId,title)",
        ).execute()
        return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

    @staticmethod
    def _keep_latest_month(sheet_ids: Dict[str, int], pattern: re.Pattern) -> List[str]:
        monthly = sorted((m.group(1), t) for t in sheet_ids if (m := pattern.match(t)))
        return [t for _, t in monthly[:-1]]

    def _apply(self, file_id: str, clear: List[str], values: List[Dict[str, Any]],
               requests: List[Dict[str, Any]]) -> None:
        if clear:
            self.sheets.spreadsheets().values().batchClear(
                spreadsheetId=file_id, body={"ranges": clear}).execute()
        if values:
            self._values_batch(file_id, values)
        if requests:
            self.sheets.spreadsheets().batchUpdate(
                spreadsheetId=file_id, body={"requests": requests}).execute()

    def staff_post_process(self, records: List[Dict[str, str]]) -> None:
        files = [r for r in records if r["kind"] == "檔案" and r.get("new_id")]
        roster = next((r for r in files if ROSTER_KEYWORD in r["new_name"]), None)
        salary = next((r for r in files if SALARY_KEYWORD in r["new_name"]), None)

        if roster and roster.get("created"):
            old_sched = ROSTER_SCHEDULE_SHEET.format(year=self.prev_year)
            new_sched = ROSTER_SCHEDULE_SHEET.format(year=self.new_year)
            self.log(f"  {roster['new_name']}：{old_sched}→{new_sched}（各月地區～備註第 5 列以下清除，"
                     f"未勾班名單保留）；清除 {'、'.join(ROSTER_CLEAR_RANGES)}；只留最近一個月專員名冊")
            if not self.dry_run:
                ids = self._sheet_map(roster["new_id"])
                requests: List[Dict[str, Any]] = []
                clear = [r for r in ROSTER_CLEAR_RANGES if r.split("!")[0].strip("'") in ids]
                if old_sched in ids:
                    requests.append({"updateSheetProperties": {
                        "properties": {"sheetId": ids[old_sched], "title": new_sched}, "fields": "title"}})
                    header = self.sheets.spreadsheets().values().get(
                        spreadsheetId=roster["new_id"],
                        range=f"'{old_sched}'!A4:{ROSTER_SCHEDULE_LAST_COL}4").execute().get("values", [[]])
                    for start in month_block_starts(header[0] if header else []):
                        clear.append(f"'{old_sched}'!{col_letters(start)}5:{col_letters(start + 8)}")
                stale = self._keep_latest_month(ids, ROSTER_MONTHLY_SHEET)
                requests += [{"deleteSheet": {"sheetId": ids[t]}} for t in stale]
                if stale:
                    self.log(f"  刪除分頁：{'、'.join(stale)}")
                self._apply(roster["new_id"], clear, [], requests)

        if salary and salary.get("created"):
            self.log(f"  {salary['new_name']}：清除 {'、'.join(SALARY_CLEAR_RANGES)}；"
                     f"{'、'.join(SALARY_YEAR_CELLS)} → {self.new_year}；工具包押金 G 欄有值的列刪除；"
                     f"只留最近一個月調薪資料"
                     + (f"；公式中專員名冊 ID {roster['old_id']} → {roster['new_id']}" if roster else ""))
            if not self.dry_run:
                ids = self._sheet_map(salary["new_id"])
                clear = [r for r in SALARY_CLEAR_RANGES if r.split("!")[0].strip("'") in ids]
                values = [{"range": c, "values": [[self.new_year]]}
                          for c in SALARY_YEAR_CELLS if c.split("!")[0].strip("'") in ids]
                requests = []
                if SALARY_DEPOSIT_SHEET in ids:
                    col = self.sheets.spreadsheets().values().get(
                        spreadsheetId=salary["new_id"],
                        range=f"'{SALARY_DEPOSIT_SHEET}'!{SALARY_DEPOSIT_COL}2:{SALARY_DEPOSIT_COL}",
                    ).execute().get("values", [])
                    rows = [i + 1 for i, v in enumerate(col, start=1) if v and str(v[0]).strip()]
                    for row in sorted(rows, reverse=True):  # 由下往上刪，列號才不會位移
                        requests.append({"deleteDimension": {"range": {
                            "sheetId": ids[SALARY_DEPOSIT_SHEET], "dimension": "ROWS",
                            "startIndex": row - 1, "endIndex": row}}})
                    if rows:
                        self.log(f"  工具包押金刪除 {len(rows)} 列")
                stale = self._keep_latest_month(ids, SALARY_MONTHLY_SHEET)
                monthly = {t for t in ids if SALARY_MONTHLY_SHEET.match(t)}
                stale += [t for t in ids if SALARY_MONTHLY_EXTRA in t and t not in monthly]
                requests += [{"deleteSheet": {"sheetId": ids[t]}} for t in stale]
                if stale:
                    self.log(f"  刪除分頁：{'、'.join(stale)}")
                if roster and roster.get("old_id") and roster["old_id"] != roster["new_id"]:
                    requests.append({"findReplace": {
                        "find": roster["old_id"], "replacement": roster["new_id"],
                        "allSheets": True, "includeFormulas": True}})
                self._apply(salary["new_id"], clear, values, requests)

    # ---------- 服務分潤表 ----------
    def get_meta(self, file_id: str) -> Dict[str, Any]:
        return self.drive.files().get(
            fileId=file_id, fields="id,name,mimeType,parents", supportsAllDrives=True,
        ).execute()

    def copy_service_files(self, old_sub: Dict[str, Any], new_sub: Dict[str, Any],
                           all_old_subs: Optional[List[Dict[str, Any]]] = None,
                           func: str = "服務分潤表") -> List[Dict[str, str]]:
        area = next((a for a in _ALL if mentions_area(old_sub["name"], a)), "")
        if not area:
            return []
        if not hasattr(self, "_service_cache"):
            self._service_cache: Dict[str, List[Dict[str, Any]]] = {}

        def listing(folder: Dict[str, Any]) -> List[Dict[str, Any]]:
            if folder["id"] not in self._service_cache:
                self._service_cache[folder["id"]] = [
                    f for f in self.children(folder["id"]) if f["mimeType"] in (SHEET_MIME, SHORTCUT_MIME)]
            return self._service_cache[folder["id"]]

        # 本區優先，其次其他地區資料夾
        files = listing(old_sub) + [f for o in (all_old_subs or []) if o["id"] != old_sub["id"]
                                    for f in listing(o)]
        records: List[Dict[str, str]] = []
        staff_records: List[Dict[str, str]] = []
        for rule in SERVICE_FILE_RULES:
            if rule["func"] != func or area not in rule["areas"]:
                continue
            target_name = rule["target"].format(Y=self.new_year, a=area)
            source = None
            for pattern in rule["sources"]:
                regex = re.compile(pattern.format(y=self.prev_year, a=area))
                source = next((f for f in files if regex.fullmatch(dash_area(f["name"]))), None)
                if source:
                    break
            if not source:
                self.log(f"  ⚠ {old_sub['name']} 找不到：{target_name.replace(str(self.new_year), str(self.prev_year))}")
                records.append({"kind": "檔案", "old_name": f"{old_sub['name']}/{target_name}",
                                "old_id": "", "new_name": "找不到來源", "new_id": ""})
                continue
            try:
                old = (self.get_meta(source["shortcutDetails"]["targetId"])
                       if source["mimeType"] == SHORTCUT_MIME else source)
                copied, created = self.ensure_copy(old, new_sub["id"], target_name)
                record = {"kind": "檔案", "old_name": old["name"], "old_id": old["id"],
                          "new_name": copied["name"], "new_id": copied["id"], "created": created}
                shortcut = rule.get("shortcut")
                if shortcut and area in shortcut["folders"]:
                    self.ensure_shortcut(shortcut["folders"][area],
                                         shortcut["name"].format(Y=self.new_year, a=area), copied)
                if created:
                    ops = INVOICE_OPS if rule.get("ops") == "invoice" else rule.get("ops")
                    if ops:
                        need_fin = "{fin}" in str(ops) or "replace_finance_id_in" in ops
                        fin = self.find_finance_file(area) if need_fin else None
                        old_fin = (self.find_finance_file(area, self.prev_year)
                                   if "replace_finance_id_in" in ops else None)
                        self.apply_ops(record, ops, fin_id=fin["id"] if fin else "",
                                       old_fin_id=old_fin["id"] if old_fin else "", area=area)
                    if rule.get("post") == "staff":
                        staff_records.append(record)
            except Exception as exc:  # 單一檔案失敗（例如沒有原始檔權限）不影響其他檔案
                self.log(f"  ✗ {source['name']} 失敗：{exc}")
                records.append({"kind": "檔案", "old_name": source["name"], "old_id": source["id"],
                                "new_name": "找不到來源", "new_id": ""})
                continue
            records.append(record)
        if staff_records:  # 名冊與薪資檔一起整理，薪資檔公式中的名冊 ID 才能替換
            self.staff_post_process(staff_records)
        return records

    def ensure_shortcut(self, folder_id: str, name: str, target: Dict[str, Any]) -> None:
        """在 folder_id 建立指向 target 的捷徑；同名項目已存在就略過。"""
        existing = [f for f in self.children(folder_id) if f["name"] == name]
        if existing:
            self.log(f"  捷徑已存在，略過：{name}")
            return
        self.log(f"  建立捷徑：{name} → {target.get('name', target['id'])}")
        if self.dry_run or str(target["id"]).startswith("(dry-run"):
            return
        self.drive.files().create(
            body={"name": name, "mimeType": SHORTCUT_MIME, "parents": [folder_id],
                  "shortcutDetails": {"targetId": target["id"]}},
            fields="id", supportsAllDrives=True,
        ).execute()

    # ---------- 行銷費用總管理 ----------
    def run_marketing(self) -> List[Dict[str, str]]:
        """同一個檔：複製「{y}廣告」「{y}總表」為新年度工作表（已存在就略過），清空實際數字。"""
        file_id = MARKETING_FILE_ID
        ids = self._sheet_map(file_id) if not self.dry_run else {}
        records: List[Dict[str, str]] = []
        renames = {}
        for tpl in (MARKETING_AD_SHEET, MARKETING_TOTAL_SHEET):
            old_title, new_title = self._fmt(tpl), tpl.replace("{y}", str(self.new_year))
            renames[old_title] = new_title
            record = {"kind": "工作表", "old_name": old_title, "old_id": file_id,
                      "new_name": new_title, "new_id": file_id, "created": False}
            records.append(record)
            if self.dry_run:
                self.log(f"  複製工作表：{old_title} → {new_title}")
                continue
            if new_title in ids and not self.fresh and self.tidy:
                self.log(f"  工作表已存在，只整理：{new_title}")
                record["tidied"] = True
                continue
            if new_title in ids and not self.fresh:
                self.log(f"  工作表已存在，沿用：{new_title}")
                continue
            if new_title in ids:  # 重新生成：既有工作表改名為舊版
                retired = f"{new_title}_舊版{self.stamp}"
                self.log(f"  --fresh：既有「{new_title}」改名為「{retired}」")
                self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
                    {"updateSheetProperties": {"properties": {"sheetId": ids[new_title], "title": retired},
                                               "fields": "title"}}]}).execute()
                ids[retired] = ids.pop(new_title)
            if old_title not in ids:
                raise RuntimeError(f"行銷費用總管理找不到「{old_title}」工作表")
            self.log(f"  複製工作表：{old_title} → {new_title}")
            res = self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
                {"duplicateSheet": {"sourceSheetId": ids[old_title], "newSheetName": new_title}}]}).execute()
            ids[new_title] = res["replies"][0]["duplicateSheet"]["properties"]["sheetId"]
            record["created"] = True
        if self.dry_run:
            return records
        created = {r["new_name"] for r in records if r["created"] or r.get("tidied")}
        # 新工作表公式中引用前一年度工作表名稱 → 新年度
        requests = [{"findReplace": {"find": old, "replacement": new, "sheetId": ids[title],
                                     "includeFormulas": True}}
                    for title in created for old, new in renames.items()]
        if requests:
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": requests}).execute()
        self._update_year_header(file_id, sorted(created))
        ad = renames[self._fmt(MARKETING_AD_SHEET)]
        if ad in created:
            cols = self.sheets.spreadsheets().values().get(
                spreadsheetId=file_id, range=f"'{ad}'!{MARKETING_AD_ITEM_COLS}").execute().get("values", [])
            items = set(MARKETING_AD_ITEMS)
            rows = [i for i, r in enumerate(cols, start=1) if any(str(v).strip() in items for v in r)]
            ranges = [f"'{ad}'!{c}{r}" for r in rows for c in MARKETING_AD_COLUMNS]
            self.log(f"  {ad}：清空 {len(rows)} 個項目的各月實際欄位")
            self._clear_constants(file_id, ranges)
        total = renames[self._fmt(MARKETING_TOTAL_SHEET)]
        if total in created:
            self.log(f"  {total}：清空 {'、'.join(MARKETING_TOTAL_CLEAR)}")
            self._clear_constants(file_id, [f"'{total}'!{r}" for r in MARKETING_TOTAL_CLEAR])
        return records

    def _update_year_header(self, file_id: str, titles: List[str]) -> None:
        """新工作表第 1 列與 A 欄等於前一年度的儲存格（總表 A1／A40、廣告 E1 的 2026）改為新年度，
        月份標題公式（=$A1&".01"）跟著變。"""
        data = []
        for title in titles:
            get = lambda rng: self.sheets.spreadsheets().values().get(
                spreadsheetId=file_id, range=f"'{title}'!{rng}", valueRenderOption="FORMULA",
            ).execute().get("values", [])
            row = get("1:1")
            cells = {f"{col_letters(i)}1" for i, v in enumerate(row[0] if row else [])
                     if str(v).strip() == str(self.prev_year)}
            cells |= {f"A{i}" for i, r in enumerate(get("A:A"), start=1)
                      if r and str(r[0]).strip() == str(self.prev_year)}
            data += [{"range": f"'{title}'!{c}", "values": [[self.new_year]]}
                     for c in sorted(cells, key=lambda c: (len(c), c))]
        if data:
            self.log(f"  年度標題 {self.prev_year} → {self.new_year}：{'、'.join(d['range'] for d in data)}")
            self._values_batch(file_id, data)

    def _clear_constants(self, file_id: str, ranges: List[str]) -> None:
        """清空範圍內的數值（含 =87369 這類只有數字的公式），引用儲存格的公式保留。"""
        if not ranges:
            return
        res = self.sheets.spreadsheets().values().batchGet(
            spreadsheetId=file_id, ranges=ranges, valueRenderOption="FORMULA").execute()
        data = []
        for rng, vr in zip(ranges, res.get("valueRanges", [])):
            rows = vr.get("values", [])
            if not rows:
                continue
            kept = [[v if isinstance(v, str) and v.startswith("=") and not _LITERAL_FORMULA.fullmatch(v)
                     else "" for v in row] for row in rows]
            if kept != rows:
                data.append({"range": rng, "values": kept})
        if data:
            self._values_batch(file_id, data)

    # ---------- 清潔用品 ----------
    def run_supply(self) -> List[Dict[str, str]]:
        """根目錄下的「{y}清潔用品訂購記錄-四店共用new」複製成新年度並清空（只在新建立時清空；已存在要重做請勾重新生成）。"""
        old_name = self._fmt(SUPPLY_FILE_NAME)
        new_name = SUPPLY_FILE_NAME.replace("{y}", str(self.new_year))
        source = next((f for f in self.children(SUPPLY_FOLDER_ID)
                       if f["mimeType"] == SHEET_MIME and dash_area(f["name"]) == dash_area(old_name)), None)
        if not source:
            self.log(f"  ⚠ 找不到檔案：{old_name}")
            return [{"kind": "檔案", "old_name": old_name, "old_id": "", "new_name": "找不到來源", "new_id": ""}]
        copied, created = self.ensure_copy(source, SUPPLY_FOLDER_ID, new_name)
        record = {"kind": "檔案", "old_name": source["name"], "old_id": source["id"],
                  "new_name": copied["name"], "new_id": copied["id"], "created": created}
        if not created and self.tidy:
            self.log(f"  檔案已存在，只整理：{copied['name']}")
            if not self.dry_run:
                self.supply_post_process(copied["id"])
        elif created and not self.dry_run:
            self.supply_post_process(copied["id"])
        elif created:
            self.log(f"  整理 {new_name}：清空進貨明細表、各地區庫存統計表第 2 列與期初庫存")
        return [record]

    def supply_post_process(self, new_id: str) -> None:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=new_id, fields="sheets.properties(sheetId,title,gridProperties)").execute()
        props = {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}
        first, last = SUPPLY_STOCK_COLS
        clear, requests = [], []
        for title, p in props.items():
            if not title.startswith(SUPPLY_PURCHASE_PREFIX):
                continue
            clear += [f"'{title}'!{r}" for r in SUPPLY_PURCHASE_CLEAR]
            col = col_index(SUPPLY_PURCHASE_FALSE_COL)
            requests.append({"repeatCell": {
                "range": {"sheetId": p["sheetId"], "startRowIndex": 2,
                          "endRowIndex": p.get("gridProperties", {}).get("rowCount", 1000),
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "cell": {"userEnteredValue": {"boolValue": False}}, "fields": "userEnteredValue"}})
            self.log(f"  {title}：清空 {'、'.join(SUPPLY_PURCHASE_CLEAR)}，{SUPPLY_PURCHASE_FALSE_COL}3:{SUPPLY_PURCHASE_FALSE_COL} 設為 FALSE")
        data, stock_ranges = [], []
        for title in props:
            if not title.startswith(SUPPLY_STOCK_PREFIX):
                continue
            col_b = self.sheets.spreadsheets().values().get(
                spreadsheetId=new_id, range=f"'{title}'!B:B").execute().get("values", [])
            rows = [2] + [i for i, r in enumerate(col_b, start=1)
                          if r and str(r[0]).strip() == SUPPLY_STOCK_OPENING]
            stock_ranges += [f"'{title}'!{first}{r}:{last}{r}" for r in rows]
            self.log(f"  {title}：清空第 2 列與 {len(rows) - 1} 列期初庫存的 {first}:{last}")
            data += self._stock_year_labels(new_id, title)
        if clear:
            self.sheets.spreadsheets().values().batchClear(
                spreadsheetId=new_id, body={"ranges": clear}).execute()
        if requests:
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=new_id, body={"requests": requests}).execute()
        self._clear_constants(new_id, stock_ranges)  # 數字清空，引用儲存格的公式保留
        if data:
            self._values_batch(new_id, data)

    def _stock_year_labels(self, file_id: str, title: str) -> List[Dict[str, Any]]:
        """庫存統計表 A 欄年月標籤（2026、2025.12、2026.01…）各加一年；公式（=A$1&".01"）不動。
        當月進貨／領貨的 SUMIF 用這些標籤對進貨明細表，不換就抓不到新年度資料。"""
        col_a = self.sheets.spreadsheets().values().get(
            spreadsheetId=file_id, range=f"'{title}'!A:A", valueRenderOption="FORMULA",
        ).execute().get("values", [])
        data = []
        for i, r in enumerate(col_a, start=1):
            m = re.fullmatch(r"(\d{4})(\.\d{2})?", str(r[0]).strip()) if r else None
            if m and int(m.group(1)) in (self.prev_year, self.prev_year - 1):
                data.append({"range": f"'{title}'!A{i}", "values": [[f"{int(m.group(1)) + 1}{m.group(2) or ''}"]]})
        if data:
            self.log(f"  {title}：A 欄 {len(data)} 個年月標籤改為新年度")
        return data

    # ---------- 春節禮品名單 ----------
    def _gift_folder(self, create: bool) -> Optional[Dict[str, Any]]:
        """根目錄下的 {Y} 資料夾（已存在就沿用，重新生成也不改名，來源檔放在裡面）。"""
        Y = str(self.new_year)
        folder = next((f for f in self.children(GIFT_ROOT_FOLDER_ID)
                       if f["mimeType"] == FOLDER_MIME and f["name"] == Y), None)
        if folder:
            self.log(f"  資料夾已存在，沿用：{Y}")
        elif create:
            self.log(f"  建立資料夾：{Y}")
            folder = ({"id": f"(dry-run:{Y})", "name": Y} if self.dry_run else self.drive.files().create(
                body={"name": Y, "mimeType": FOLDER_MIME, "parents": [GIFT_ROOT_FOLDER_ID]},
                fields="id,name", supportsAllDrives=True).execute())
        return folder

    def run_gift_stats(self) -> List[Dict[str, str]]:
        """第一階段：建立 {Y} 資料夾，各區後台下載儲值金結算，建立「{Y}名單統整」：轉檔貼入「{a}儲值金」
        （高雄從新竹拆出）、建樞紐、「{Y}送禮名單」公式，並回報各金額區間的人數，供第二階段決定金額。"""
        Y = str(self.new_year)
        folder = self._gift_folder(create=True)
        records = [{"kind": "資料夾", "old_name": "", "old_id": "", "new_name": Y, "new_id": folder["id"]}]

        if self.dry_run:
            self.log(f"  （dry-run）會登入各區後台下載儲值金結算存成「{Y}{GIFT_SOURCE_KEYWORD}-地區」")
        else:
            self._gift_download(folder)
        listing = [] if folder["id"].startswith("(dry-run") else self.children(folder["id"])
        sources = self._gift_sources(listing)
        for area, parent in GIFT_SPLIT_FROM.items():
            if area not in sources and parent in sources:
                sources[area] = {**sources[parent], "split_from": parent}
        for area in GIFT_AREAS:
            if area in sources:
                split = sources[area].get("split_from")
                self.log(f"  {area}來源：{sources[area]['name']}" + (f"（從{split}拆出）" if split else ""))
            else:
                self.log(f"  ⚠ {Y} 資料夾沒有{area}{GIFT_SOURCE_KEYWORD}來源檔，略過")
        if not sources:
            self.log(f"  ⚠ 後台下載都失敗，「春節禮品/{Y}」也沒有各地區儲值金結算（例：{Y}{GIFT_SOURCE_KEYWORD}-台北.xls）")
            return records + [{"kind": "檔案", "old_name": f"{Y}{GIFT_SOURCE_KEYWORD}-地區", "old_id": "",
                               "new_name": "找不到來源", "new_id": ""}]

        summary, created = self._ensure_spreadsheet(folder["id"], self._fmt(GIFT_SUMMARY_FILE), listing)
        records.append({"kind": "檔案", "old_name": "", "old_id": "", "new_name": summary["name"],
                        "new_id": summary["id"], "created": created})
        if self.dry_run:
            self.log(f"  （dry-run）會轉檔貼入 {'、'.join(GIFT_AREA_SHEET.format(a=a) for a in sources)}、"
                     f"建立樞紐與「{self._fmt(GIFT_LIST_SHEET)}」")
            return records

        blocks = self._gift_build_summary(summary["id"], sources, created)
        self._gift_report_stats(summary["id"], blocks)
        self.log(f"  第一階段完成：決定門檻金額後，執行「{GIFT_LIST_FUNCTION}」並在畫面輸入金額")
        return records

    def _gift_report_stats(self, file_id: str, blocks: List[Tuple[str, str, str, str]]) -> None:
        """「區間統計」工作表寫入各地區區間人數公式，並讀樞紐結果在執行紀錄列出各區間人數。"""
        if not blocks:
            return
        table = gift_stats_table(blocks)
        if GIFT_STAT_SHEET not in self._sheet_map(file_id):
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
                {"addSheet": {"properties": {"title": GIFT_STAT_SHEET}}}]}).execute()
        self.sheets.spreadsheets().values().batchClear(
            spreadsheetId=file_id, body={"ranges": [f"'{GIFT_STAT_SHEET}'!A:Z"]}).execute()
        self._values_batch(file_id, [{"range": f"'{GIFT_STAT_SHEET}'!A1", "values": table}])

        res = self.sheets.spreadsheets().values().batchGet(
            spreadsheetId=file_id, valueRenderOption="UNFORMATTED_VALUE",
            ranges=[f"'{t}'!{n}2:{m}" for _, t, n, m in blocks]).execute()
        labels = gift_band_labels()
        totals: List[float] = []
        for (area, _, _, _), vr in zip(blocks, res.get("valueRanges", [])):
            amounts = []
            for row in vr.get("values", []):
                if len(row) > 1 and str(row[0]).strip():
                    try:
                        amount = float(row[1])
                    except (TypeError, ValueError):
                        continue
                    if amount > 0:
                        amounts.append(amount)
            totals += amounts
            self.log(f"  {area}：{self.prev_year} 年 {len(amounts)} 位，加總 {sum(amounts):,.0f}；"
                     + "、".join(f"{lb} {n} 位" for lb, n in zip(labels, gift_band_counts(amounts))))
        self.log(f"  全區：{len(totals)} 位；"
                 + "、".join(f"{lb} {n} 位" for lb, n in zip(labels, gift_band_counts(totals)))
                 + f"（明細見「{GIFT_STAT_SHEET}」工作表）")

    def run_gift_list(self) -> List[Dict[str, str]]:
        """第二階段：畫面輸入的門檻金額寫入「設定」B1，讀「{Y}送禮名單」；複製前一年度「{y}春節送禮名單」
        為新年度檔，清空後從第 21 列貼上送禮名單；根目錄「春節送禮名單」複製分頁為新年度分頁，
        A1 IMPORTRANGE 改指新檔。"""
        threshold = self.gift_threshold
        if threshold is None or threshold <= 0:
            raise ValueError("請在畫面輸入送禮門檻金額（大於 0）")
        threshold = int(threshold) if float(threshold).is_integer() else threshold
        folder = self._gift_folder(create=False)
        if not folder:
            raise RuntimeError(f"找不到「春節禮品/{self.new_year}」資料夾，請先執行「{GIFT_STAT_FUNCTION}」")
        listing = self.children(folder["id"])
        summary_name = self._fmt(GIFT_SUMMARY_FILE)
        summary = next((f for f in listing if f["mimeType"] == SHEET_MIME and f["name"] == summary_name), None)
        if not summary:
            raise RuntimeError(f"找不到「{summary_name}」，請先執行「{GIFT_STAT_FUNCTION}」")
        if self.dry_run:
            self.log(f"  （dry-run）會把門檻 {threshold:,} 寫入「{GIFT_SETTING_SHEET}」{GIFT_THRESHOLD_CELL}，"
                     f"產生 {self.new_year}春節送禮名單並貼上名單")
            return []
        self.sheets.spreadsheets().values().update(
            spreadsheetId=summary["id"], range=f"'{GIFT_SETTING_SHEET}'!{GIFT_THRESHOLD_CELL}",
            valueInputOption="RAW", body={"values": [[threshold]]}).execute()
        rows = self.sheets.spreadsheets().values().get(
            spreadsheetId=summary["id"], range=f"'{self._fmt(GIFT_LIST_SHEET)}'!A2:G").execute().get("values", [])
        rows = [r + [""] * (len(GIFT_LIST_HEADERS) - len(r)) for r in rows if len(r) > 1 and str(r[1]).strip()]
        self.log(f"  門檻 {threshold:,}：{self._fmt(GIFT_LIST_SHEET)} 共 {len(rows)} 位")
        if not rows:
            self.log("  ⚠ 沒有客戶達到門檻，未產生春節送禮名單；請調低金額再執行")
            return []
        return self._gift_send_file(folder, listing, rows)

    def _gift_download(self, folder: Dict[str, Any]) -> None:
        """各區登入後台下載儲值金結算（月排程同一份會員匯出），上傳為「{Y}儲值金結算-{a}.xls(x)」
        （同名舊檔移到垃圾桶）；高雄從新竹拆出，不另外下載。單一地區失敗只警告，資料夾裡已有的檔案照用。"""
        from googleapiclient.http import MediaInMemoryUpload

        downloader = self.gift_downloader
        if downloader is None:
            from tools.annual_rollover.gift_download import download_gift_settlement as downloader
        for area in [a for a in GIFT_AREAS if a not in GIFT_SPLIT_FROM]:
            base = f"{self.new_year}{GIFT_SOURCE_KEYWORD}-{area}"
            try:
                content, ext, count = downloader(area, self.prev_year, self.log)
            except Exception as exc:
                self.log(f"  ⚠ {area} 後台下載失敗：{exc}（改用資料夾裡已有的檔案）")
                continue
            for old in self.children(folder["id"]):
                if old["mimeType"] != SHEET_MIME and re.sub(r"\.(xlsx?|csv)$", "", old["name"], flags=re.I) == base:
                    self.drive.files().update(fileId=old["id"], body={"trashed": True},
                                              supportsAllDrives=True).execute()
            mime = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx"
                    else "application/vnd.ms-excel")
            self.drive.files().create(
                body={"name": f"{base}.{ext}", "mimeType": mime, "parents": [folder["id"]]},
                media_body=MediaInMemoryUpload(content, mimetype=mime, resumable=False),
                fields="id,name", supportsAllDrives=True).execute()
            self.log(f"  {area}：後台下載儲值金結算 {count} 筆 → {base}.{ext}")

    def _gift_phone_area(self, phone: str) -> str:
        """新竹／高雄分不出來時用手機查新竹後台近期訂單（同一支手機只查一次）；查不到或無法登入回傳空字串。"""
        key = "".join(ch for ch in str(phone or "") if ch.isdigit())
        cache = self.__dict__.setdefault("_gift_phone_cache", {})
        if key in cache:
            return cache[key]
        if not key or self.__dict__.get("_gift_phone_error"):
            return ""
        try:
            if self.gift_phone_lookup is None:
                import requests
                from tools.scheduled_monthly import half_month_orders as hm

                acc = hm.load_accounts().get("新竹") or {}
                session = requests.Session()
                hm.login(session, acc.get("email", ""), acc.get("password", ""))
                self.gift_phone_lookup = lambda p: hm.area_by_phone(session, p)
            cache[key] = self.gift_phone_lookup(key) or ""
        except Exception as exc:
            self._gift_phone_error = str(exc)
            self.log(f"  ⚠ 手機查訂單失敗，分不出新竹／高雄的列沿用原規則：{exc}")
            return ""
        return cache[key]

    def _gift_split_kaohsiung(self, values: List[List[str]]) -> Tuple[List[List[str]], List[List[str]]]:
        """新竹結算拆成 (新竹, 高雄)，規則同儲值金結算轉檔：方案名稱／地址判斷，分不出來用手機查近期訂單，
        再不行沿用原規則（高雄方案或地址含高雄／台南才算高雄）。"""
        from config.vip_config import KAOHSIUNG_ADDRESS_KEYWORDS, KAOHSIUNG_FILTER_VALUES
        from services.vip_workflow import classify_hsinchu_row

        hsinchu, kaohsiung = [values[0]], [values[0]]
        plan_i, addr_i, phone_i = (col_index(GIFT_PLAN_COL), col_index(GIFT_ADDRESS_COL),
                                   col_index(GIFT_PHONE_COL))
        for row in values[1:]:
            cell = lambda i: str(row[i]).strip() if len(row) > i else ""
            plan, address = cell(plan_i), cell(addr_i)
            area = classify_hsinchu_row(plan, address) or self._gift_phone_area(cell(phone_i))
            if not area:
                area = "高雄" if plan in KAOHSIUNG_FILTER_VALUES or any(
                    k in address for k in KAOHSIUNG_ADDRESS_KEYWORDS) else "新竹"
            (kaohsiung if area == "高雄" else hsinchu).append(row)
        return hsinchu, kaohsiung

    def _gift_sources(self, listing: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """各地區來源檔：檔名含「儲值金」與地區（名單統整／送禮名單除外）；同地區有原始匯出檔
        （xls/xlsx/csv）時優先用它，其次才用已轉好的 Google 試算表。"""
        skip = (self._fmt(GIFT_SUMMARY_FILE), "送禮名單")
        sources: Dict[str, Dict[str, Any]] = {}
        for f in listing:
            name = f["name"]
            if f["mimeType"] in (FOLDER_MIME, SHORTCUT_MIME) or GIFT_SOURCE_KEYWORD not in name \
                    or any(s in name for s in skip):
                continue
            if not (f["mimeType"] == SHEET_MIME or re.search(r"\.(xlsx?|csv)$", name, re.I)):
                continue
            area = next((a for a in GIFT_AREAS if a in name), "")
            if not area:
                continue
            exact = re.sub(r"\.(xlsx?|csv)$", "", name, flags=re.I) == f"{self.new_year}{GIFT_SOURCE_KEYWORD}-{area}"
            current = sources.get(area)
            if (current is None or current["mimeType"] == SHEET_MIME and f["mimeType"] != SHEET_MIME
                    or exact and f["mimeType"] != SHEET_MIME):
                sources[area] = f
        return sources

    def _ensure_spreadsheet(self, parent_id: str, name: str,
                            listing: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
        existing = [f for f in listing if f["mimeType"] == SHEET_MIME and f["name"] == name]
        if existing and not self.fresh:
            self.log(f"  檔案已存在，沿用：{name}")
            return existing[0], False
        for item in existing:
            self._retire(item)
        self.log(f"  建立檔案：{name}")
        if self.dry_run:
            return {"id": f"(dry-run:{name})", "name": name}, True
        return self.drive.files().create(
            body={"name": name, "mimeType": SHEET_MIME, "parents": [parent_id]},
            fields="id,name", supportsAllDrives=True).execute(), True

    def _gift_read_source(self, source: Dict[str, Any]) -> List[List[str]]:
        """讀來源檔第一個分頁；xls/xlsx/csv 先轉成暫存 Google 試算表，讀完移到垃圾桶。"""
        temp = None
        file_id = source["id"]
        if source["mimeType"] != SHEET_MIME:
            temp = self.drive.files().copy(
                fileId=source["id"], fields="id",
                body={"name": f"{re.sub(r'[.][^.]+$', '', source['name'])}_轉檔暫存", "mimeType": SHEET_MIME},
                supportsAllDrives=True).execute()
            file_id = temp["id"]
        try:
            meta = self.sheets.spreadsheets().get(
                spreadsheetId=file_id, fields="sheets.properties.title").execute()
            title = meta["sheets"][0]["properties"]["title"]
            return self.sheets.spreadsheets().values().get(
                spreadsheetId=file_id, range=f"'{title}'").execute().get("values", [])
        finally:
            if temp:
                self.drive.files().update(fileId=temp["id"], body={"trashed": True},
                                          supportsAllDrives=True).execute()

    def _gift_build_summary(self, file_id: str, sources: Dict[str, Dict[str, Any]],
                            created: bool) -> List[Tuple[str, str, str, str]]:
        """名單統整：設定／郵遞區號／各地區儲值金（每次重貼、樞紐重建）／送禮名單公式；
        回傳 [(地區, 工作表, 樞紐姓名欄, 樞紐加總欄)]。"""
        from tools.annual_rollover.tw_zipcodes import TW_ZIPCODES

        ids = self._sheet_map(file_id)
        area_titles = [GIFT_AREA_SHEET.format(a=a) for a in sources]
        list_title = self._fmt(GIFT_LIST_SHEET)
        # 地區工作表整張重建（舊樞紐位置會隨欄數變動，直接刪掉重建最乾淨）
        requests: List[Dict[str, Any]] = [{"deleteSheet": {"sheetId": ids[t]}} for t in area_titles if t in ids]
        new_titles = [t for t in [GIFT_SETTING_SHEET, GIFT_ZIP_SHEET, list_title] if t not in ids] + area_titles
        requests += [{"addSheet": {"properties": {"title": t}}} for t in new_titles]
        res = self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": requests}).execute()
        for reply in res.get("replies", []):
            if "addSheet" in reply:
                p = reply["addSheet"]["properties"]
                ids[p["title"]] = p["sheetId"]
        if created:  # 新檔預設的空白分頁刪掉
            blank = [t for t in ids if t not in new_titles and t not in area_titles]
            if blank:
                self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
                    {"deleteSheet": {"sheetId": ids[t]}} for t in blank]}).execute()

        data = [{"range": f"'{GIFT_SETTING_SHEET}'!A1:A2", "values": [
            ["送禮門檻金額"], [f"儲值金結算中 {self.prev_year} 年購買的儲值金（不含轉換、購物金）加總 ≥ B1 的客戶列入「{list_title}」"
                         f"（B1 由「{GIFT_LIST_FUNCTION}」依畫面輸入的金額寫入，也可直接修改）"]]}]
        data.append({"range": f"'{GIFT_ZIP_SHEET}'!A1:B{len(TW_ZIPCODES) + 1}",
                     "values": [["郵遞區號", "行政區"]] + [list(z) for z in TW_ZIPCODES]})
        data.append({"range": f"'{list_title}'!A1:G1", "values": [GIFT_LIST_HEADERS]})
        self.sheets.spreadsheets().values().batchUpdate(spreadsheetId=file_id, body={
            "valueInputOption": "RAW", "data": data}).execute()

        blocks, pivots = [], []
        read_cache: Dict[str, List[List[str]]] = {}
        split_cache: Dict[str, Tuple[List[List[str]], List[List[str]]]] = {}
        for area, source in sources.items():
            title = GIFT_AREA_SHEET.format(a=area)
            if source["id"] not in read_cache:
                read_cache[source["id"]] = self._gift_read_source(source)
            values = read_cache[source["id"]]
            # 新竹與從新竹拆出的高雄共用同一份結算，拆一次後各取一半
            shared = [a for a, src in sources.items() if src["id"] == source["id"]]
            if values and len(shared) > 1:
                if source["id"] not in split_cache:
                    split_cache[source["id"]] = self._gift_split_kaohsiung(values)
                hsinchu, kaohsiung = split_cache[source["id"]]
                values = kaohsiung if source.get("split_from") else hsinchu
            if not values:
                self.log(f"  ⚠ {source['name']} 沒有資料")
                continue
            width = max(len(r) for r in values)
            header = gift_headers(values[0] + [""] * (width - len(values[0])))
            body = [[gift_cell(v) for v in r] + [""] * (width - len(r)) for r in values[1:]]
            sid = ids[title]
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
                {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {
                    "rowCount": max(len(values) + 10, 1000), "columnCount": width + 4,
                    "frozenRowCount": 1}}, "fields": "gridProperties"}}]}).execute()
            self._values_batch(file_id, [{"range": f"'{title}'!A1", "values": [header] + body}])
            pivots.append({"updateCells": {
                "start": {"sheetId": sid, "rowIndex": 0, "columnIndex": width + 1}, "fields": "pivotTable",
                "rows": [{"values": [{"pivotTable": gift_pivot(
                    sid, len(values), width, self.prev_year, header[col_index(GIFT_DATE_COL)])}]}]}})
            blocks.append((area, title, col_letters(width + 1), col_letters(width + 2)))
            self.log(f"  {title}：貼入 {len(body)} 筆，樞紐在 {col_letters(width + 1)}1（{GIFT_NAME_COL} 姓名／"
                     f"{GIFT_DATE_COL} 購買日期為 {self.prev_year} 年／{GIFT_PLAN_COL} 為{GIFT_PLAN_PREFIX}／加總 {GIFT_AMOUNT_COL}）")
        if pivots:
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": pivots}).execute()
        self.sheets.spreadsheets().values().batchClear(
            spreadsheetId=file_id, body={"ranges": [f"'{list_title}'!A2:G"]}).execute()
        self._values_batch(file_id, [{"range": f"'{list_title}'!A2",
                                      "values": [[gift_list_formula(blocks, len(TW_ZIPCODES))]]}])
        return blocks

    def _gift_send_file(self, folder: Dict[str, Any], listing: List[Dict[str, Any]],
                        rows: List[List[str]]) -> List[Dict[str, str]]:
        """新年度「{Y}春節送禮名單」檔（新建立或勾「只整理」時才清空貼上）＋根目錄總表新年度分頁。"""
        root_files = self.children(GIFT_ROOT_FOLDER_ID)
        root = next((f for f in root_files if f["mimeType"] == SHEET_MIME and f["name"] == GIFT_ROOT_FILE), None)
        if not root:
            raise RuntimeError(f"春節禮品根目錄找不到「{GIFT_ROOT_FILE}」")
        old_tab, new_tab = self._fmt(GIFT_ROOT_TAB), GIFT_ROOT_TAB.replace("{y}", str(self.new_year))
        root_ids = self._sheet_map(root["id"])
        if old_tab not in root_ids:
            raise RuntimeError(f"「{GIFT_ROOT_FILE}」找不到「{old_tab}」分頁")
        formula = (self.sheets.spreadsheets().values().get(
            spreadsheetId=root["id"], range=f"'{old_tab}'!A1", valueRenderOption="FORMULA",
        ).execute().get("values") or [[""]])[0][0]
        match = _IMPORTRANGE.search(str(formula))
        tab = match.group(2).strip("'") if match else ""

        prev_folder = next((f for f in root_files if f["mimeType"] == FOLDER_MIME
                            and f["name"] == str(self.prev_year)), None)
        old_name, new_name = self._fmt(GIFT_SEND_FILE), GIFT_SEND_FILE.replace("{y}", str(self.new_year))
        source = next((f for f in self.children(prev_folder["id"]) if f["mimeType"] == SHEET_MIME
                       and f["name"] == old_name), None) if prev_folder else None
        if not source:
            raise RuntimeError(f"找不到前一年度「{self.prev_year}/{old_name}」")
        send, created = self.ensure_copy(source, folder["id"], new_name)
        records = [{"kind": "檔案", "old_name": source["name"], "old_id": source["id"],
                    "new_name": send["name"], "new_id": send["id"], "created": created}]
        if created or self.tidy:
            self._gift_fill_send(send["id"], tab, rows)
        else:
            self.log(f"  {new_name} 已存在，不重貼（要重新清空貼上請勾「只整理已存在檔案」）")

        record = {"kind": "工作表", "old_name": old_tab, "old_id": root["id"],
                  "new_name": new_tab, "new_id": root["id"], "created": False}
        if new_tab in root_ids and self.fresh:
            retired = f"{new_tab}_舊版{self.stamp}"
            self.log(f"  --fresh：既有「{new_tab}」改名為「{retired}」")
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=root["id"], body={"requests": [
                {"updateSheetProperties": {"properties": {"sheetId": root_ids.pop(new_tab), "title": retired},
                                           "fields": "title"}}]}).execute()
        if new_tab not in root_ids:
            index = next(s["properties"]["index"] for s in self.sheets.spreadsheets().get(
                spreadsheetId=root["id"], fields="sheets.properties(title,index)").execute()["sheets"]
                if s["properties"]["title"] == old_tab)
            self.log(f"  {GIFT_ROOT_FILE}：複製分頁 {old_tab} → {new_tab}")
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=root["id"], body={"requests": [
                {"duplicateSheet": {"sourceSheetId": root_ids[old_tab], "newSheetName": new_tab,
                                    "insertSheetIndex": index}}]}).execute()
            record["created"] = True
        if match:
            new_formula = str(formula).replace(match.group(1), send["id"], 1)
            self._values_batch(root["id"], [{"range": f"'{new_tab}'!A1", "values": [[new_formula]]}])
            self.log(f"  {new_tab}!A1 → IMPORTRANGE {new_name}（第一次開啟若顯示 #REF! 請按「允許存取」）")
        else:
            self.log(f"  ⚠ {old_tab}!A1 不是 IMPORTRANGE，{new_tab} 未改公式")
        return records + [record]

    def _gift_fill_send(self, file_id: str, tab: str, rows: List[List[str]]) -> None:
        """送禮名單檔：A1 年度改新年度；清空 J2:L 與第 18 列以下（F 欄勾選框重設為未勾選），
        第 21 列起貼上送禮名單（A～E→A～E、電話→G、LINE→H）。"""
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties(sheetId,title,gridProperties)").execute()
        props = {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}
        tab = tab if tab in props else next(iter(props))
        p = props[tab]
        last_col = col_letters(p["gridProperties"].get("columnCount", 26) - 1)
        row_count = p["gridProperties"].get("rowCount", 1000)
        self.log(f"  {tab}：清空 {GIFT_SEND_CLEAR}、第 {GIFT_SEND_CLEAR_ROW} 列以下，"
                 f"第 {GIFT_SEND_PASTE_ROW} 列起貼上 {len(rows)} 位")
        self.sheets.spreadsheets().values().batchClear(spreadsheetId=file_id, body={"ranges": [
            f"'{tab}'!{GIFT_SEND_CLEAR}", f"'{tab}'!A{GIFT_SEND_CLEAR_ROW}:{last_col}"]}).execute()
        needed = GIFT_SEND_PASTE_ROW + len(rows)
        requests: List[Dict[str, Any]] = []
        if needed > row_count:
            requests.append({"appendDimension": {"sheetId": p["sheetId"], "dimension": "ROWS",
                                                 "length": needed - row_count}})
            row_count = needed
        col = col_index(GIFT_SEND_CHECKBOX_COL)
        requests.append({"repeatCell": {
            "range": {"sheetId": p["sheetId"], "startRowIndex": GIFT_SEND_CLEAR_ROW - 1, "endRowIndex": row_count,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredValue": {"boolValue": False}}, "fields": "userEnteredValue"}})
        self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": requests}).execute()
        title = (self.sheets.spreadsheets().values().get(
            spreadsheetId=file_id, range=f"'{tab}'!A1").execute().get("values") or [[""]])[0][0]
        data = []
        if str(self.prev_year) in str(title):
            data.append({"range": f"'{tab}'!A1", "values": [[str(title).replace(str(self.prev_year),
                                                                                 str(self.new_year))]]})
        end = GIFT_SEND_PASTE_ROW + len(rows) - 1
        for i, target in enumerate(GIFT_SEND_COLUMNS):
            if rows:
                data.append({"range": f"'{tab}'!{target}{GIFT_SEND_PASTE_ROW}:{target}{end}",
                             "values": [[r[i]] for r in rows]})
        if data:  # RAW：電話保留開頭的 0
            self.sheets.spreadsheets().values().batchUpdate(spreadsheetId=file_id, body={
                "valueInputOption": "RAW", "data": data}).execute()

    # ---------- 分頁整理（規則表） ----------
    def _fmt(self, text: str, **extra: str) -> str:
        text = str(text).replace("{y}", str(self.prev_year)).replace("{Y}", str(self.new_year))
        for key, value in extra.items():
            text = text.replace("{" + key + "}", value)
        return text

    def find_file_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        res = self.drive.files().list(
            q=f"name = '{name}' and mimeType = '{SHEET_MIME}' and trashed = false",
            fields="files(id,name)", pageSize=5,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = res.get("files", [])
        return files[0] if files else None

    def apply_ops(self, record: Dict[str, str], ops: Dict[str, Any], fin_id: str = "",
                  old_fin_id: str = "", area: str = "") -> None:
        """依規則整理新建立檔案的分頁（只在新建立時呼叫）：
        year_columns   財報總表：前一年度 12 個月欄位右邊插入新年度欄位（複製公式、表頭改新年度），
                       前一年度欄位貼上為值
        keep_only／keep_contains／keep_latest  只保留這些分頁（keep_latest 符合者只留最新一張）
        delete_regex   刪除符合的分頁
        rename_year    分頁名稱中的前一年度改成新年度
        replace_year_in 分頁內容（含公式）中的前一年度改成新年度
        clear／clear_months  清除範圍（分頁名稱以 * 結尾＝開頭相符）
        clear_constants 範圍內是值的清空、是公式的保留
        values         填入值或公式（{Y}＝新年度、{fin}＝新年度該區財報 ID、lookup 定義的名稱＝該檔 ID）
        lookup         {名稱: 檔名樣板}，用檔名找檔案 ID 給 values 使用（找不到則該公式不寫入）
        replace_finance_id_in 分頁公式中前一年度該區財報 ID 換成新年度該區財報 ID
        """
        name, file_id = record["new_name"], record["new_id"]
        self.log(f"  整理 {name}：{', '.join(ops)}")
        if self.dry_run:
            return
        y, Y = str(self.prev_year), str(self.new_year)
        ids = self._sheet_map(file_id)

        # 1. 財報總表年度欄位（要在刪除其他分頁前，先把前一年度貼成值）
        if ops.get("year_columns"):
            title = self._fmt(ops["year_columns"])
            if title in ids:
                header = self.sheets.spreadsheets().values().get(
                    spreadsheetId=file_id, range=f"'{title}'!1:1").execute().get("values", [[]])
                header = header[0] if header else []
                months = [i for i, v in enumerate(header) if re.fullmatch(rf"{y}\.\d{{2}}", str(v).strip())]
                if months:
                    start, end = months[0], months[-1] + 1
                    width = end - start
                    src = {"sheetId": ids[title], "startColumnIndex": start, "endColumnIndex": end}
                    dst = {"sheetId": ids[title], "startColumnIndex": end, "endColumnIndex": end + width}
                    self.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": [
                        {"insertDimension": {"range": {"sheetId": ids[title], "dimension": "COLUMNS",
                                                       "startIndex": end, "endIndex": end + width},
                                             "inheritFromBefore": True}},
                        {"copyPaste": {"source": src, "destination": dst, "pasteType": "PASTE_NORMAL"}},
                        {"copyPaste": {"source": src, "destination": src, "pasteType": "PASTE_VALUES"}},
                    ]}).execute()
                    new_header = [str(header[i]).replace(y, Y) for i in months]
                    self._values_batch(file_id, [{
                        "range": f"'{title}'!{col_letters(end)}1:{col_letters(end + width - 1)}1",
                        "values": [new_header]}])
                    self.log(f"  {title}：{col_letters(start)}:{col_letters(end - 1)} 貼上為值，"
                             f"右側插入 {Y} 欄位")

        # 2. 決定刪除的分頁
        delete: set = set()
        keep_only = [self._fmt(t) for t in ops.get("keep_only", [])]
        keep_contains = ops.get("keep_contains", [])
        latest_patterns = [re.compile(self._fmt(p)) for p in ops.get("keep_latest", [])]
        if keep_only or keep_contains:
            for t in ids:
                if t in keep_only or any(k in t for k in keep_contains) \
                        or any(p.search(t) for p in latest_patterns):
                    continue
                delete.add(t)
        for pattern in latest_patterns:
            matched = sorted(t for t in ids if pattern.search(t))
            delete.update(matched[:-1])
        for pattern in ops.get("delete_regex", []):
            delete.update(t for t in ids if re.search(self._fmt(pattern), t))
        if len(delete) >= len(ids):
            delete = set()
        if delete:
            self.log(f"  刪除分頁：{'、'.join(sorted(delete))}")

        # 3. 改名、內容年份替換、底色等結構性請求
        requests: List[Dict[str, Any]] = [{"deleteSheet": {"sheetId": ids[t]}} for t in sorted(delete)]
        final: Dict[str, int] = {t: i for t, i in ids.items() if t not in delete}
        for pattern in ops.get("rename_year", []):
            regex = re.compile(self._fmt(pattern))
            for t in list(final):
                if regex.search(t) and y in t:
                    new_title = t.replace(y, Y, 1)
                    requests.append({"updateSheetProperties": {
                        "properties": {"sheetId": final[t], "title": new_title}, "fields": "title"}})
                    final[new_title] = final.pop(t)
        for t in ops.get("replace_year_in", []):
            title = self._fmt(t)
            if title in final:
                requests.append({"findReplace": {"find": y, "replacement": Y, "sheetId": final[title],
                                                 "includeFormulas": True}})

        for t in ops.get("replace_finance_id_in", []):
            title = self._fmt(t)
            if title not in final:
                continue
            if fin_id and old_fin_id:
                requests.append({"findReplace": {"find": old_fin_id, "replacement": fin_id,
                                                 "sheetId": final[title], "includeFormulas": True}})
            else:
                self.log(f"  ⚠ 找不到前一年度或新年度財報 ID，{title} 公式未更新（請先跑「財務報表」）")

        def resolve(rng: str) -> Optional[str]:
            sheet, a1 = self._fmt(rng).rsplit("!", 1)
            sheet = sheet.strip("'")
            if sheet.endswith("*"):
                sheet = next((t for t in final if t.startswith(sheet[:-1])), "")
            return f"'{sheet}'!{a1}" if sheet in final else None

        # clear_constants：先讀公式，之後把公式寫回、值清空
        constants: List[Dict[str, Any]] = []
        for rng in ops.get("clear_constants", []):
            target = resolve(rng)
            if not target:
                continue
            original = target
            for old_title, sid in ids.items():  # 改名前的名稱讀取
                for new_title, nsid in final.items():
                    if nsid == sid and target.startswith(f"'{new_title}'!"):
                        original = target.replace(f"'{new_title}'!", f"'{old_title}'!", 1)
            rows = self.sheets.spreadsheets().values().get(
                spreadsheetId=file_id, range=original, valueRenderOption="FORMULA",
            ).execute().get("values", [])
            kept = [[v if isinstance(v, str) and v.startswith("=") else "" for v in row] for row in rows]
            if rows:
                constants.append({"range": target, "values": kept})

        if requests:
            self.sheets.spreadsheets().batchUpdate(
                spreadsheetId=file_id, body={"requests": requests}).execute()

        # 4. 清除與填值（用改名後的名稱）
        clear = [r for r in (resolve(c) for c in ops.get("clear", [])) if r]
        for pattern, a1 in ops.get("clear_months", {}).items():
            clear += [f"'{t}'!{a1}" for t in final if re.search(self._fmt(pattern), t)]
        if clear:
            self.sheets.spreadsheets().values().batchClear(
                spreadsheetId=file_id, body={"ranges": clear}).execute()
        lookups: Dict[str, str] = {}
        for key, tpl in ops.get("lookup", {}).items():
            name = self._fmt(tpl).replace("{a}", area)
            found = self.find_file_by_name(name)
            lookups[key] = found["id"] if found else ""
            if not found:
                self.log(f"  ⚠ 找不到「{name}」，引用它的公式未寫入（請先跑對應功能）")
        values = constants + [
            {"range": r, "values": [[self._fmt(v, fin=fin_id, **lookups)]]}
            for c, v in ops.get("values", {}).items()
            if (r := resolve(c)) and not any(f"{{{k}}}" in v and not lookups[k] for k in lookups)]
        if "{fin}" in str(ops.get("values", {})) and not fin_id:
            values = [v for v in values if "importrange" not in str(v["values"])]
            self.log(f"  ⚠ 找不到新年度財報 ID，公式未更新（請先跑「財務報表」）")
        if values:
            self._values_batch(file_id, values)

    # ---------- 服務分潤表年度資料夾（其他功能共用） ----------
    def service_area_folders(self, areas: List[str]) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]],
                                                               List[Dict[str, Any]]]:
        """回傳 ([(前一年度地區資料夾, 新年度地區資料夾)], 前一年度所有地區資料夾)；
        新年度年度／地區資料夾不存在就建立（只建 areas 的地區）。"""
        values = self.read(f"'{GENERATE_SHEET}'!A1:H")
        row_number = next((i for i, row in enumerate(values, start=1)
                           if row and str(row[0]).strip() in SERVICE_ROW_NAMES), 0)
        if not row_number:
            raise RuntimeError(f"「{GENERATE_SHEET}」找不到服務分潤表列")
        spec = parse_row(row_number, values[row_number - 1], self.prev_year)
        pairs, all_old = [], []
        for old_year in self.find_year_folders(spec):
            new_year = self.ensure_folder(spec.folder_id, replace_year(old_year["name"], self.prev_year, self.new_year))
            old_subs = [f for f in self.children(old_year["id"]) if f["mimeType"] == FOLDER_MIME]
            all_old += old_subs
            for old_sub in old_subs:
                if not any(mentions_area(old_sub["name"], a) for a in areas):
                    continue
                if new_year["id"].startswith("(dry-run"):
                    new_sub = {"id": f"(dry-run:{old_sub['name']})", "name": old_sub["name"]}
                else:
                    new_sub = self.ensure_folder(new_year["id"], old_sub["name"])
                pairs.append((old_sub, new_sub))
        return pairs, all_old

    def run_service_rules(self, func: str, area: str) -> List[Dict[str, str]]:
        areas = sorted({a for r in SERVICE_FILE_RULES if r["func"] == func for a in r["areas"]})
        if area != ALL_AREA:
            areas = [a for a in areas if a == area]
        if not areas:
            return []
        self.log(f"{func}：服務分潤表資料夾內 {'、'.join(areas)} 的檔案")
        pairs, all_old = self.service_area_folders(areas)
        records: List[Dict[str, str]] = []
        for old_sub, new_sub in pairs:
            records += self.copy_service_files(old_sub, new_sub, all_old, func=func)
        return records

    # ---------- 財務報表：其他年度資料夾 ----------
    def clone_year_folder(self, job: Dict[str, Any]) -> List[Dict[str, str]]:
        meta = self.get_meta(job["folder_id"])
        if str(self.prev_year) in meta["name"]:
            old_year, parent = meta, (meta.get("parents") or [""])[0]
        else:
            folders = [f for f in self.children(job["folder_id"]) if f["mimeType"] == FOLDER_MIME
                       and re.search(rf"(?<!\d){self.prev_year}", f["name"])]
            folders.sort(key=lambda f: (f["name"] != str(self.prev_year), f["name"]))
            if not folders:
                raise RuntimeError(f"{job['label']} 找不到 {self.prev_year} 年度資料夾")
            old_year, parent = folders[0], job["folder_id"]
        new_year = self.ensure_folder(parent, replace_year(old_year["name"], self.prev_year, self.new_year))
        records = [{"kind": "資料夾", "old_name": old_year["name"], "old_id": old_year["id"],
                    "new_name": new_year["name"], "new_id": new_year["id"]}]

        def year_name(n: str) -> str:
            return dash_area(re.sub(rf"(?<!\d){self.prev_year}", str(self.new_year), n))

        def copy_tree(old_id: str, new_id: str) -> None:  # 子資料夾照建、不複製檔案
            for f in self.children(old_id):
                if f["mimeType"] == FOLDER_MIME:
                    if new_id.startswith("(dry-run"):
                        self.log(f"  建立資料夾：{year_name(f['name'])}")
                        continue
                    copy_tree(f["id"], self.ensure_folder(new_id, year_name(f["name"]))["id"])

        for f in self.children(old_year["id"]):
            if f["mimeType"] == FOLDER_MIME:
                if new_year["id"].startswith("(dry-run"):
                    self.log(f"  建立資料夾：{year_name(f['name'])}")
                else:
                    copy_tree(f["id"], self.ensure_folder(new_year["id"], year_name(f["name"]))["id"])
            elif f["mimeType"] in (SHEET_MIME, SHORTCUT_MIME) and str(self.prev_year) in f["name"]:
                old = self.get_meta(f["shortcutDetails"]["targetId"]) if f["mimeType"] == SHORTCUT_MIME else f
                copied, created = self.ensure_copy(old, new_year["id"], year_name(f["name"]))
                record = {"kind": "檔案", "old_name": old["name"], "old_id": old["id"],
                          "new_name": copied["name"], "new_id": copied["id"], "created": created}
                ops = next((o for pat, o in job["files"].items() if re.search(pat, f["name"])), None)
                if created and ops:
                    self.apply_ops(record, ops)
                records.append(record)
        return records

    # ---------- 內勤表單專用整理 ----------
    def find_finance_file(self, area: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """找該區財報（預設新年度，例：台北2027財報），新年度需先跑過「財務報表」。"""
        year = year or self.new_year
        names = [f"{area}{year}財報"] + [
            f"{alias}{year}財報" for alias, canon in NAME_ALIASES.items() if canon == area]
        for name in names:
            res = self.drive.files().list(
                q=f"name = '{name}' and mimeType = '{SHEET_MIME}' and trashed = false",
                fields="files(id,name)", pageSize=5,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            files = res.get("files", [])
            if files:
                return files[0]
        return None

    def office_post_process(self, spec: RowSpec, records: List[Dict[str, str]]) -> None:
        """只處理這次新建立的內勤工作表單：
        0. 只保留 OFFICE_KEEP_SHEETS（＋目標分頁），其餘分頁刪除
        1. 「{前一年度}目標」分頁改名為「{新年度}目標」
        2. 清除 OFFICE_CLEAR_RANGES；清潔異動 A2:AE 清內容與底色
        3. ATM!A2 改成 FILTER(IMPORTRANGE(新年度該區財報 富邦更新))
        """
        targets = [r for r in records if r["kind"] == "檔案" and r.get("created") and r.get("new_id")]
        if not targets:
            return
        finance = self.find_finance_file(spec.area) if spec.area else None
        if spec.area and not finance:
            self.log(f"  ⚠ 找不到 {spec.area}{self.new_year}財報，ATM!A2 不更新（請先跑「財務報表」）")
        for r in targets:
            self.log(f"  {r['new_name']}：{self.prev_year}目標→{self.new_year}目標；清除 "
                     f"{'、'.join(OFFICE_CLEAR_RANGES)}、清潔異動 A2:AE 底色（K 欄公式保留）"
                     + (f"；ATM!A2 → {finance['name']}" if finance else ""))
            if self.dry_run:
                continue
            meta = self.sheets.spreadsheets().get(
                spreadsheetId=r["new_id"], fields="sheets.properties(sheetId,title)",
            ).execute()
            sheet_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

            requests: List[Dict[str, Any]] = []
            old_goal = f"{self.prev_year}目標"
            keep = set(OFFICE_KEEP_SHEETS) | {old_goal}
            removed = [t for t in sheet_ids
                       if t not in keep and not any(k in t for k in OFFICE_KEEP_CONTAINS)]
            if len(removed) < len(sheet_ids):  # 至少留一張才刪
                requests += [{"deleteSheet": {"sheetId": sheet_ids[t]}} for t in removed]
                if removed:
                    self.log(f"  刪除分頁：{'、'.join(removed)}")
                sheet_ids = {t: i for t, i in sheet_ids.items() if t not in removed}
            if old_goal in sheet_ids:
                requests.append({"updateSheetProperties": {
                    "properties": {"sheetId": sheet_ids[old_goal], "title": f"{self.new_year}目標"},
                    "fields": "title"}})
            for title, a1 in OFFICE_CLEAR_WITH_FILL:
                if title in sheet_ids:
                    requests.append({"repeatCell": {
                        "range": a1_to_grid(sheet_ids[title], a1),
                        "cell": {"userEnteredFormat": {}},
                        "fields": "userEnteredFormat.backgroundColor"}})
            if requests:
                self.sheets.spreadsheets().batchUpdate(
                    spreadsheetId=r["new_id"], body={"requests": requests},
                ).execute()

            ranges = [rng for rng in OFFICE_CLEAR_RANGES if rng.split("!")[0].strip("'") in sheet_ids]
            if ranges:
                self.sheets.spreadsheets().values().batchClear(
                    spreadsheetId=r["new_id"], body={"ranges": ranges},
                ).execute()
            if finance and "ATM" in sheet_ids:
                self._values_batch(r["new_id"], [{
                    "range": OFFICE_ATM_CELL,
                    "values": [[OFFICE_ATM_FORMULA.format(fid=finance["id"])]],
                }])

    # ---------- 財務報表專用整理 ----------
    def _values_batch(self, file_id: str, data: List[Dict[str, Any]]) -> None:
        self.sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=file_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    def _sheet_titles(self, file_id: str) -> set:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties.title",
        ).execute()
        return {s["properties"]["title"] for s in meta.get("sheets", [])}

    def finance_post_process(self, records: List[Dict[str, str]]) -> None:
        """只處理這次新建立的檔案，避免重跑時清掉新年度已輸入的資料。
        1. 各區財報清除指定範圍（FINANCE_CLEAR_RANGES）
        2. 各區財報「股東損益表_財務」A241 改 IMPORTRANGE 前一年度同區財報
        3. 新年度 review 的各區財報分頁 A1 改 IMPORTRANGE 新年度同區財報
        """
        area_files: Dict[str, Dict[str, str]] = {}
        review: Optional[Dict[str, str]] = None
        for r in records:
            if r["kind"] != "檔案" or not r.get("new_id"):
                continue
            if FINANCE_REVIEW_KEYWORD in r["new_name"]:
                review = r
                continue
            area = finance_area(r["new_name"])
            if area:
                area_files[area] = r

        for area, r in area_files.items():
            if not r.get("created"):
                continue
            clear = FINANCE_CLEAR_RANGES["台北/台中" if area in ("台北", "台中") else "其他"]
            formula = (f'=IMPORTRANGE("{r["old_id"]}","{FINANCE_PL_SHEET}!$a$1:$aa200")')
            self.log(f"  {r['new_name']}：清除 {'、'.join(clear)}；"
                     f"{FINANCE_PL_SHEET}!{FINANCE_PL_CELL} → 前一年度 {r['old_id']}；"
                     f"{'、'.join(FINANCE_YEAR_CELLS)} → {self.new_year}")
            if self.dry_run:
                continue
            titles = self._sheet_titles(r["new_id"])
            ranges = [rng for rng in clear if rng.split("!")[0].strip("'") in titles]
            if ranges:
                self.sheets.spreadsheets().values().batchClear(
                    spreadsheetId=r["new_id"], body={"ranges": ranges},
                ).execute()
            data = [{"range": cell, "values": [[self.new_year]]}
                    for cell in FINANCE_YEAR_CELLS if cell.split("!")[0].strip("'") in titles]
            if FINANCE_PL_SHEET in titles:
                data.append({"range": f"'{FINANCE_PL_SHEET}'!{FINANCE_PL_CELL}", "values": [[formula]]})
            else:
                self.log(f"  ⚠ {r['new_name']} 沒有「{FINANCE_PL_SHEET}」分頁")
            if data:
                self._values_batch(r["new_id"], data)

        if review and review.get("created"):
            # 只跑 review（或部分地區）時，其餘地區的新年度財報從 Drive 找
            for area in FINANCE_AREAS:
                if area not in area_files:
                    found = self.find_finance_file(area)
                    if found:
                        area_files[area] = {"new_id": found["id"], "new_name": found["name"]}
            data = []
            for area, r in area_files.items():
                formula = f'=IMPORTRANGE("{r["new_id"]}","{FINANCE_PL_SHEET}!$A$1:$z$500")'
                data.append({"range": f"'{area}財報'!A1", "values": [[formula]]})
                self.log(f"  {review['new_name']}：{area}財報!A1 → {r['new_name']}")
            if data and not self.dry_run:
                titles = self._sheet_titles(review["new_id"])
                data = [d for d in data if d["range"].split("!")[0].strip("'") in titles]
                if data:
                    self._values_batch(review["new_id"], data)

    def record_results(self, spec: RowSpec, records: List[Dict[str, str]]) -> None:
        now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")
        lines = [f"{now} {self.new_year}{'（dry-run）' if self.dry_run else ''}"]
        lines += [f"{r['kind']} {r['new_name']}：{r['new_id']}" for r in records]
        self.write(f"'{GENERATE_SHEET}'!{RESULT_COL}{spec.row_number}", [["\n".join(lines)]])

    def run(self, rows_arg: str = "all", names: Optional[List[str]] = None,
            area: str = ALL_AREA) -> List[Dict[str, str]]:
        row_names = [n for n in (names or []) if n not in EXTRA_FUNCTIONS] or None
        records = self._run_rows(rows_arg, row_names, area) if row_names or not names else []
        funcs = {ROW_FUNCTIONS.get(n, n) for n in (names or [])}
        extra: List[Dict[str, str]] = []
        for func, job in (("行銷費用總管理", self.run_marketing), ("清潔用品", self.run_supply),
                          (GIFT_STAT_FUNCTION, self.run_gift_stats), (GIFT_LIST_FUNCTION, self.run_gift_list)):
            if func not in funcs:
                continue
            self.log(func)
            try:
                extra += job()
            except Exception as exc:
                self.log(f"  ✗ {func} 失敗：{exc}")
        for func in ("專員表單", "內勤表單", "財務報表"):
            if func not in funcs:
                continue
            try:
                extra += self.run_service_rules(func, area)
            except Exception as exc:
                self.log(f"  ✗ {func} 服務分潤表檔案失敗：{exc}")
        if "財務報表" in funcs:
            for job in FINANCE_YEAR_FOLDERS:
                if area != ALL_AREA and job["area"] != area:
                    continue
                self.log(f"財務報表：{job['label']}")
                try:
                    extra += self.clone_year_folder(job)
                except Exception as exc:
                    self.log(f"  ✗ {job['label']} 失敗：{exc}")
        return records + extra

    def _run_rows(self, rows_arg: str, names: Optional[List[str]], area: str) -> List[Dict[str, str]]:
        """rows_arg 指定列號；names 指定 A 欄名稱（例：["內勤表單"]），兩者都給時取交集；
        area 指定執行區域（全區＝不篩選）。"""
        values = self.read(f"'{GENERATE_SHEET}'!A1:H")
        row_numbers = parse_rows_arg(rows_arg, len(values))
        if names:
            wanted = set(names)
            row_numbers = [r for r in row_numbers
                           if r <= len(values) and values[r - 1] and str(values[r - 1][0]).strip() in wanted]
            if not row_numbers:
                self.log(f"「{GENERATE_SHEET}」A 欄沒有：{'、'.join(names)}")
        all_records: List[Dict[str, str]] = []
        for row_number in row_numbers:
            row = values[row_number - 1] if row_number <= len(values) else []
            spec = parse_row(row_number, row, self.prev_year)
            if not spec:
                self.log(f"第 {row_number} 列：沒有資料夾網址，略過")
                continue
            spec = filter_spec_by_area(spec, area)
            if not spec:
                continue
            self.log(f"第 {row_number} 列：{spec.name}" + (f"（{area}）" if area != ALL_AREA else ""))
            try:
                records = self.process_row(spec)
            except Exception as exc:  # 一列失敗不影響下一列
                self.log(f"  ✗ 失敗：{exc}")
                self.write(f"'{GENERATE_SHEET}'!{RESULT_COL}{row_number}",
                           [[f"{datetime.now(TZ):%Y/%m/%d %H:%M:%S} 失敗：{exc}"]])
                continue
            self.record_results(spec, records)
            self.log("  完成")
            all_records.extend(records)
        return all_records


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="依「生成新年度」複製新年度資料夾與檔案")
    parser.add_argument("--year", type=int, default=datetime.now(TZ).year + 1, help="新年度（預設明年）")
    parser.add_argument("--rows", default="all", help="要處理的列，例：2、2-8、2,4,6、all")
    parser.add_argument("--names", default="", help="只處理 A 欄為這些名稱的列，以逗號分隔，例：財務報表,內勤表單")
    parser.add_argument("--dry-run", action="store_true", help="只列出會做的事，不建立、不回寫")
    parser.add_argument("--fresh", action="store_true", help="已存在的新年度同名項目改名為舊版後重新生成")
    parser.add_argument("--spreadsheet-id", default=MASTER_SPREADSHEET_ID)
    parser.add_argument("--gift-threshold", type=float, default=None, help=f"{GIFT_LIST_FUNCTION}的送禮門檻金額")
    args = parser.parse_args(argv)

    from services.google_auth import get_drive_service, get_sheets_service

    rollover = Rollover(get_drive_service(), get_sheets_service(), args.spreadsheet_id,
                        args.year, dry_run=args.dry_run, fresh=args.fresh, gift_threshold=args.gift_threshold)
    names = split_list(args.names) or None
    try:
        records = rollover.run(args.rows, names=names)
    except Exception as exc:
        rollover.append_run_log("、".join(names or [f"列 {args.rows}"]), ALL_AREA, [], error=str(exc))
        raise
    rollover.append_run_log("、".join(names or [f"列 {args.rows}"]), ALL_AREA, records)
    print(f"共 {len(records)} 筆生成紀錄")
    return 1 if any(r["new_name"] == "找不到來源" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
