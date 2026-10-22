"""
tools/annual_rollover/spring_banquet.py

生成新年度「春酒專區」：尾牙春酒專用區 →「{Y}春酒」資料夾。

1. 從「{Y-1}春酒」複製（已存在就沿用）
   - 表單：{Y-1}春酒參與統計表單、{Y-2}年度投票（地區） → 新年度名稱
   - 回覆試算表：{Y-1}春酒參與統計表單（試算表）／-範本／(回覆) → {Y}春酒參與統計表單
   - 子資料夾照建（年份換新、不複製內容），春酒抽籤.docx、紅包袋模板.docx 直接複製
2. 回覆試算表新建時整理（prepare_response_file）：
   - 「得獎人提詞」去年得獎人（地區/姓名）記到「歷年得獎人」
   - 「YYYY春酒專員名冊／內勤名冊」分頁改為新年度
   - 表單回覆與待填欄位清空並設黃底；標題、到職付費年度等文字改新年度
3. 每次執行更新名冊（update_roster）：
   - 「{Y}春酒專員名冊」：A 地區 B 姓名 C 電話 D 到職日期 E 狀態，F～Q 1～12 月時數，
     R 總時數 S 月份 T 平均時數 U 達門檻（AB1 時數門檻） V/W 去年／前年得獎 X/Y 參加／投票回覆
     名單＝檸檬後台專員個資狀態「正常」，排除姓名含檸檬人／高雄／台南；
     登入沿用月排程「儲值金結算」load_accounts()，匯出沿用日排程「專員個資」；
     時數取各地區檔案「{Y-1}排班統計表」
   - 「春酒參與統計」L/M 由專員＋內勤名冊公式帶入；N～Z 依姓名查詢，T～V 眷屬人數依姓名保留
4. Google 表單（OAuth 需 forms.body，專案需啟用 Google Forms API）
   - 參與統計表單：說明「春酒時間」與出席題目換成執行畫面設定的日期與時段
   - 各區投票表單：姓名、得獎題目的選項換成名冊姓名；排除內勤，以及
     「歷年得獎人」中 G1 年內得過「同一獎項」的人

表單回覆要連到哪個試算表，Forms API 無法設定，仍需在表單「回覆」頁手動連結；
連結後 Google 會新增工作表；再執行一次春酒專區，程式會依標題列自動改名為 RESPONSE_TABS 的名稱
（參與回應、投票回應-地區；投票表單姓名題目會先加上（地區）以便分辨）。
"""

from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
import requests

SPRING_ROOT_FOLDER_ID = "1q5IeRNtI4a6tUwWCglmzPHj3Ff7hBCxq"  # 尾牙春酒專用區
YEAR_FOLDER_NAME = "{Y}春酒"
ATTEND_FORM_NAME = "{Y}春酒參與統計表單"
VOTE_FORM_NAME = "{V}年度投票（{a}）"          # V = 投票年度（春酒前一年）
RESPONSE_FILE_NAME = "{Y}春酒參與統計表單"      # 試算表，與表單同名
# 回覆試算表來源（依序找第一個）：前一年度的試算表、範本、舊命名
RESPONSE_SOURCES = ["{P}春酒參與統計表單", "{P}春酒參與統計表單-範本", "{P}春酒參與統計表單 (回覆)"]
COPY_FILES = ["春酒抽籤", "紅包袋模板"]     # 每年沿用的檔案（檔名不含年份；Word 檔複製時轉成 Google 文件）
DOC_MIME = "application/vnd.google-apps.document"
ROSTER_AREAS = ["台北", "台中", "桃園", "新竹"]
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
FORM_MIME = "application/vnd.google-apps.form"
FOLDER_MIME = "application/vnd.google-apps.folder"

# ---------- 回覆試算表裡的工作表 ----------
ROSTER_SHEET = "{Y}春酒專員名冊"
INNER_SHEET = "{Y}春酒內勤名冊"
ROSTER_SHEET_PATTERN = re.compile(r"^\d{4}春酒(專員|內勤)名冊$")
ROSTER_HEADER = (["地區", "姓名", "電話", "到職日期", "狀態"] + [str(m) for m in range(1, 13)]
                 + ["總時數", "月份", "平均時數", "達門檻", "去年得獎（{y1}）", "前年得獎（{y2}）",
                    "參加回覆", "投票回覆"])
THRESHOLD_LABEL_CELL, THRESHOLD_CELL, DEFAULT_THRESHOLD = "AA1", "AB1", 48
ATTEND_SHEET = "春酒參與統計"
GIFT_SHEET = "眷屬收費"
WINNERS_SHEET = "歷年得獎人"
WINNERS_HEADER = ["投票年度", "地區", "獎項", "姓名"]
EXCLUDE_YEARS_CELL, DEFAULT_EXCLUDE_YEARS = "G1", 1
PROMPT_SHEET = "得獎人提詞"                              # 第 1 列獎項、下方「地區/姓名」＝去年得獎人

ACTIVE_STATUS = "正常"
EXCLUDE_NAME_KEYWORDS = ("檸檬人", "高雄", "台南")        # 測試帳號（含南區檸檬人）與高雄／台南人員不列入
ROSTER_COLUMNS = ["姓名", "電話", "到職日期", "狀態"]   # 後台匯出的 A/B/G/N 欄
ROSTER_COLUMN_INDEX = [0, 1, 6, 13]                    # 標題對不上時的備援欄位位置
YELLOW = {"red": 1, "green": 0.95, "blue": 0.6}         # 新年度要填的欄位底色

# 春酒統計表：A～O 每人明細（A/B 專員＋內勤名冊、I～K 眷屬人數人工填），Q 欄起人數統計與前 5 名
STATS_SHEET = "春酒統計表"
STATS_ROWS = 300
STATS_MANUAL = "I2:K"                                   # 眷屬 6歲以下／7-12／13以上，依姓名保留
SUMMARY_COL = "Q"
TOP_N = 5
VOTE_AWARD_COLS = [("家事達人獎", "E"), ("最佳進步獎", "F"), ("良師益友獎", "G"), ("最佳人緣獎", "H")]
ERROR_MARKS = ("#REF!", "#VALUE!", "#NAME?", "#ERROR!", "#DIV/0!")

# 表單連到試算表時 Google 一定會新增工作表；請把它改名為下列名稱，原工作表 A 欄起用公式帶入
RESPONSE_TABS = {ATTEND_SHEET: ("參與回應", "A2:G"),
                 **{f"春酒獎項投票-{a}": (f"投票回應-{a}", "A2:H") for a in ["台北", "台中", "桃園", "新竹"]}}

# 新建回覆試算表時清空的範圍：(工作表, 範圍, 是否設黃底提醒填入)
TEMPLATE_CLEAR: List[Tuple[str, str, bool]] = (
    [(sheet, rng, False) for sheet, (_, rng) in RESPONSE_TABS.items()] + [
        (STATS_SHEET, f"{STATS_MANUAL}{STATS_ROWS}", True),
        (GIFT_SHEET, "G2:H300", True),
        ("活動流程", "A3:F3", True), ("活動流程", "G5:G24", True), ("活動流程", "J5:M24", True),
        ("主持人流程台詞", "A3:E3", True), ("主持人流程台詞", "E5:E25", True),
        (PROMPT_SHEET, "A2:D13", True),
        ("中獎/簽名表", "C4:C40", True), ("中獎/簽名表", "F4:F40", True),
        ("中獎/簽名表", "I4:I40", True), ("中獎/簽名表", "L4:L40", True),
        ("中獎/簽名表", "AA2:AC150", True), ("中獎/簽名表", "AE2:AE150", True),
        ("分桌/獎項", "F2:H12", True),
        ("工作分配", "A2:B20", True), ("工作分配", "F2:F20", True), ("工作分配", "J2:J20", True),
    ])
TEMPLATE_TEXT = {
    "'活動流程'!A1": "{Y} 春酒活動",
    "'主持人流程台詞'!A1": "{Y} 春酒活動",
    "'中獎/簽名表'!A1": "{Y}檸檬春酒-中獎清單",
    "'中獎/簽名表'!N1": "{Y}檸檬春酒-中獎清單",
    "'春酒統計表'!L1": "{Y}年到職（本人付費）",
    "'眷屬收費'!C1": "成人(1200，含{Y}到職本人)",
    "'春酒統計表'!Z2": "{Y}到職本人付費",
}
DATE_LABEL_ROWS = {"活動流程": ["日期：{d}", "", "", "時間：", "", "地點："],
                   "主持人流程台詞": ["日期：{d}", "", "時間：", "", "地點："]}

FORMS_SCOPE = "https://www.googleapis.com/auth/forms.body"
ATTEND_DATE = re.compile(r"(方便於)\s*\d{1,2}/\d{1,2}\s*[(（][^)）]*[)）][^，,？?]*")
ATTEND_DESC = re.compile(r"(春酒時間[:：]\s*)\d{4}/\d{1,2}/\d{1,2}\s*[(（][^)）]*[)）][^\n]*")
VOTE_NAME_KEYWORDS = ("姓名", "得獎")                 # 問題2 姓名、問題3～6「應由誰得獎」
WEEKDAYS = "一二三四五六日"

# 各地區「專員名冊與時數／薪資」檔可能的檔名（第一個找到的為準）
HOURS_FILE_NAMES = ["{y}專員名冊與時數-{a}", "{y}專員名冊薪資-{a}", "{y}專員薪資相關-{a}", "{y}{a}專員薪資(外場)"]
HOURS_SHEET = "{y}排班統計表"
MONTH_HEADER = re.compile(r"^(\d{1,2})月?$")


# ---------- 小工具 ----------
def a1_to_grid(sheet_id: int, a1: str) -> Dict[str, int]:
    from tools.annual_rollover.new_year_generate import a1_to_grid as _a1

    return _a1(sheet_id, a1)


def sheet_ids(rollover, file_id: str) -> Dict[str, int]:
    meta = rollover.sheets.spreadsheets().get(spreadsheetId=file_id, fields="sheets.properties").execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def read(rollover, file_id: str, a1: str) -> List[List[str]]:
    return rollover.sheets.spreadsheets().values().get(spreadsheetId=file_id, range=a1).execute().get("values", [])


def batch_update(rollover, file_id: str, requests_: List[Dict[str, Any]]) -> None:
    if requests_:
        rollover.sheets.spreadsheets().batchUpdate(spreadsheetId=file_id, body={"requests": requests_}).execute()


def yellow(sheet_id: int, a1: str) -> Dict[str, Any]:
    return {"repeatCell": {"range": a1_to_grid(sheet_id, a1),
                           "cell": {"userEnteredFormat": {"backgroundColorStyle": {"rgbColor": YELLOW}}},
                           "fields": "userEnteredFormat.backgroundColorStyle"}}


def to_hours(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def excluded_name(name: str) -> bool:
    return any(k in name for k in EXCLUDE_NAME_KEYWORDS)


# ---------- 專員個資 ----------
def export_staff_frame(area: str, accounts: Dict[str, Dict[str, str]]) -> pd.DataFrame:
    """用該地區帳號登入後台，下載專員個資並讀成 DataFrame（全部欄位為字串）。"""
    from tools.common.lemon_backend_login import login as backend_login
    from tools.scheduled_daily.staff_info import EXPORT_URL, HEADERS, LOGIN_URL

    account = accounts.get(area) or {}
    session = requests.Session()
    backend_login(session, account.get("email", ""), account.get("password", ""),
                  login_url=LOGIN_URL, headers=HEADERS, timeout=60)
    res = session.get(EXPORT_URL, headers=HEADERS, allow_redirects=True, timeout=120)
    res.raise_for_status()
    content_type = res.headers.get("Content-Type", "").lower()
    if "excel" not in content_type and "octet-stream" not in content_type:
        raise RuntimeError(f"{area} 專員個資回傳不是 Excel，Content-Type={content_type}")
    try:
        # 後台實際給的是舊版 xls（副檔名是 xlsx），xlrd 會誤判檔案損毀，需略過檢查
        return pd.read_excel(BytesIO(res.content), dtype=str, engine="xlrd",
                             engine_kwargs={"ignore_workbook_corruption": True}).fillna("")
    except Exception:
        return pd.read_excel(BytesIO(res.content), dtype=str).fillna("")


def active_rows(df: pd.DataFrame) -> List[List[str]]:
    """狀態「正常」且姓名不含檸檬人／高雄／台南的 姓名／電話／到職日期／狀態；電話加 ' 保留開頭的 0。"""
    if all(col in df.columns for col in ROSTER_COLUMNS):
        picked = df[ROSTER_COLUMNS]
    else:
        picked = df.iloc[:, ROSTER_COLUMN_INDEX]
    rows = []
    for name, phone, onboard, status in picked.astype(str).values.tolist():
        name = name.strip()
        if status.strip() != ACTIVE_STATUS or excluded_name(name):
            continue
        phone = phone.strip()
        rows.append([name, f"'{phone}" if phone else "", onboard.strip(), status.strip()])
    return rows


# ---------- 排班時數 ----------
def parse_hours(values: List[List[str]]) -> Dict[str, List[float]]:
    """排班統計表 → {姓名: 1～12 月時數}；姓名欄為第 1 列「外勤」那一欄，月份欄為 1～12 或 1月～12月。"""
    if not values:
        return {}
    header = [str(v).strip() for v in values[0]]
    name_col = header.index("外勤") if "外勤" in header else 1
    months = {int(m.group(1)): i for i, v in enumerate(header)
              if (m := MONTH_HEADER.match(v)) and 1 <= int(m.group(1)) <= 12}
    hours: Dict[str, List[float]] = {}
    for row in values[1:]:
        name = row[name_col].strip() if len(row) > name_col else ""
        if not name or name in hours:
            continue
        hours[name] = [to_hours(row[months[m]]) if m in months and months[m] < len(row) else 0.0
                       for m in range(1, 13)]
    return hours


def load_hours(rollover, area: str) -> Dict[str, List[float]]:
    y = rollover.prev_year
    for pattern in HOURS_FILE_NAMES:
        found = rollover.find_file_by_name(pattern.format(y=y, a=area))
        if found:
            sheet = HOURS_SHEET.format(y=y)
            try:
                return parse_hours(read(rollover, found["id"], f"'{sheet}'!A1:Z500"))
            except Exception as exc:
                rollover.log(f"  ⚠ {found['name']} 讀不到「{sheet}」：{exc}")
                return {}
    rollover.log(f"  ⚠ 找不到 {area} 的 {y} 專員名冊／薪資檔，時數以 0 計")
    return {}


# ---------- 回覆試算表 ----------
def award_formula(row: int, year: int) -> str:
    w = f"'{WINNERS_SHEET}'"
    return (f"=IFERROR(TEXTJOIN(\"、\",TRUE,FILTER({w}!$C$2:$C,{w}!$D$2:$D=$B{row},"
            f"TO_TEXT({w}!$A$2:$A)=\"{year}\")),\"\")")


def roster_rows(area: str, staff: List[List[str]], hours: Dict[str, List[float]], start_row: int,
                new_year: int) -> List[List[Any]]:
    """專員名冊每列：A～E 個資、F～Q 月時數、R～U 公式、V/W 去年／前年得獎（投票年度 Y-2、Y-3）、
    X 參加回覆（春酒參與統計 G）、Y 投票回覆（該區春酒獎項投票 D 欄姓名）。"""
    z = "$" + re.sub(r"(\d+)", r"$\1", THRESHOLD_CELL)
    rows = []
    for offset, (name, phone, onboard, status) in enumerate(staff):
        r = start_row + offset
        rows.append([area, name, phone, onboard, status, *hours.get(name, [0] * 12),
                     f"=SUM(F{r}:Q{r})", f"=COUNTIF(F{r}:Q{r},\"<>0\")", f"=IFERROR(R{r}/S{r},0)",
                     f"=IF(T{r}>{z},\"Y\",\"\")", award_formula(r, new_year - 2), award_formula(r, new_year - 3),
                     f"=IF($B{r}=\"\",\"\",IFERROR(XLOOKUP($B{r},'{ATTEND_SHEET}'!$B:$B,"
                     f"'{ATTEND_SHEET}'!$G:$G),\"未回覆\"))",
                     f"=IF($B{r}=\"\",\"\",IFERROR(IF(COUNTIF(INDIRECT(\"'春酒獎項投票-\"&$A{r}&\"'!D:D\"),"
                     f"$B{r})>0,\"已投票\",\"未投票\"),\"\"))"])
    return rows


def attend_order(roster_names: List[List[str]], inner: List[List[str]]) -> List[str]:
    """春酒統計表 A/B 公式算出的姓名順序：專員（排除也在內勤名冊的人）＋內勤。"""
    inner_names = {r[1] for r in inner if len(r) > 1}
    return [r[1] for r in roster_names if len(r) > 1 and r[1] and r[1] not in inner_names] + \
           [r[1] for r in inner if len(r) > 1 and r[1]]


def stats_person_formulas(new_year: int) -> Dict[str, List[List[str]]]:
    """春酒統計表 A～O 公式：{起始儲存格: 列資料}（I～K 眷屬人數為人工欄，不在此）。"""
    rs, inn, at = (f"'{ROSTER_SHEET.format(Y=new_year)}'", f"'{INNER_SHEET.format(Y=new_year)}'",
                   f"'{ATTEND_SHEET}'")
    stack = (f"={{FILTER({rs}!A2:B,{rs}!B2:B<>\"\",ISNA(MATCH({rs}!B2:B,{inn}!B2:B,0)));"
             f"FILTER({inn}!A2:B,{inn}!B2:B<>\"\")}}")
    left, right = [], []
    for r in range(2, STATS_ROWS + 1):
        g = lambda x: f'=IF($B{r}="","",{x})'
        look = lambda c: g(f'IFERROR(XLOOKUP($B{r},{at}!$B:$B,{at}!${c}:${c}),"")')
        left.append([g(f'IF(COUNTIF({at}!$B:$B,$B{r})>0,"已回覆","未回覆")'),
                     g(f'IFERROR(XLOOKUP($B{r},{rs}!$B:$B,{rs}!$D:$D),"")'),
                     look("G"), look("D"), look("E"), look("F")])
        right.append([f'=IF(IFERROR($D{r}>=DATE({new_year},1,1),FALSE),"Y","")',
                      *[g(f'IFERROR(XLOOKUP($B{r},{rs}!$B:$B,{rs}!${c}:${c}),"")') for c in "UTV"]])
    return {"A2": [[stack]], "C2": left, "L2": right}


def stats_summary_formulas(new_year: int) -> Dict[str, List[List[str]]]:
    """春酒統計表 Q 欄起：參加人數（Q1～）與各區各獎項前 5 名（Q18～）。"""
    st, gift, rs = f"'{STATS_SHEET}'", f"'{GIFT_SHEET}'", f"'{ROSTER_SHEET.format(Y=new_year)}'"
    out: Dict[str, List[List[str]]] = {
        "Q1": [["參加人數"],
               ["類別", "名單人數", "參加", "不參加", "未回覆", "眷屬成人(13以上)", "眷屬小孩(7-12)",
                "眷屬幼兒(6以下)", "素食", f"{new_year}到職本人付費", "應收費用"],
               ["合計", *[f"=SUM({c}4:{c}15)" for c in "RSTUVWXYZ"], "=SUM(AA4:AA15)"]],
    }
    rows = []
    for r in range(4, 16):
        a = f"$Q{r}"
        g = lambda x: f'=IF({a}="","",{x})'
        cnt = lambda *cond: g(f"COUNTIFS({st}!$A$2:$A,{a}" + "".join(f",{st}!${c}$2:${c},\"{v}\"" for c, v in cond) + ")")
        sm = lambda col: g(f'SUMIFS({st}!${col}$2:${col},{st}!$A$2:$A,{a},{st}!$E$2:$E,"是")')
        rows.append([f"=UNIQUE(FILTER({st}!A2:A,{st}!A2:A<>\"\"))" if r == 4 else "",
                     cnt(), cnt(("E", "是")), cnt(("E", "否")), g(f"R{r}-S{r}-T{r}"),
                     sm("K"), sm("J"), sm("I"), cnt(("E", "是"), ("F", "素*")), cnt(("E", "是"), ("L", "Y")),
                     g(f"SUMIFS({gift}!$F:$F,{gift}!$A:$A,{a})")])
    out["Q4"] = rows
    out["Q16"] = [["總出席人數（含眷屬）", "=S3+V3+W3+X3"]]
    out["Q18"] = [[f"投票前{TOP_N}名（勤奮獎依名冊總時數）"]]
    for i, area in enumerate(ROSTER_AREAS):
        r = 19 + i * (TOP_N + 2)
        vote = f"'春酒獎項投票-{area}'"
        header, formulas = [area], [""]
        for award, c in VOTE_AWARD_COLS:
            header += [award, "票數"]
            formulas += [f"=IFERROR(ARRAY_CONSTRAIN(QUERY({vote}!{c}2:{c},\"select {c}, count({c}) where {c} is "
                         f"not null group by {c} order by count({c}) desc label count({c}) ''\",0),{TOP_N},2),\"\")", ""]
        header += ["勤奮獎", "總時數"]
        formulas += [f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({{{rs}!B2:B,{rs}!R2:R}},{rs}!A2:A=\"{area}\"),2,FALSE),"
                     f"{TOP_N},2),\"\")", ""]
        out[f"Q{r}"] = [header, formulas]
    return out


def other_formulas() -> Dict[str, List[List[str]]]:
    """眷屬收費、抽獎籤、表單回應帶入公式：{'工作表'!儲存格: 值}。"""
    st = f"'{STATS_SHEET}'"
    n = lambda c: f"N({st}!{c}2:{c}{STATS_ROWS})"
    pay = f'({st}!L2:L{STATS_ROWS}="Y")'
    out = {
        f"'{GIFT_SHEET}'!A1": [["店別", "夥伴姓名", "成人(1200，含新年度到職本人)", "小孩7-12歲(600)",
                                "幼兒6歲以下(0)", "小計"]],
        f"'{GIFT_SHEET}'!A2": [[f"=IFERROR(FILTER({{{st}!A2:A{STATS_ROWS},{st}!B2:B{STATS_ROWS},{n('K')}+{pay},"
                                f"{n('J')},{n('I')},({n('K')}+{pay})*1200+{n('J')}*600}},{st}!E2:E{STATS_ROWS}=\"是\","
                                f"{n('I')}+{n('J')}+{n('K')}+{pay}>0),\"\")"]],
        "'抽獎籤'!A1": [["地區", "姓名", "參加否", "", "", "", "列印區（每列 5 格：上格地區、下格姓名）"]],
        "'抽獎籤'!A2": [[f"=IFERROR(FILTER({{{st}!A2:A,{st}!B2:B,{st}!E2:E}},{st}!E2:E=\"是\"),\"\")"]],
        "'抽獎籤'!G2": [["=LET(n,COUNTIF(A2:A,\"?*\"),IF(n=0,\"\",MAKEARRAY(ROUNDUP(n/5)*2,5,LAMBDA(r,c,"
                         "LET(i,(ROUNDUP(r/2)-1)*5+c,IF(i>n,\"\",IF(ISODD(r),INDEX(A2:A,i),INDEX(B2:B,i))))))))"]],
    }
    for sheet, (tab, rng) in RESPONSE_TABS.items():
        out[f"'{sheet}'!A2"] = [[response_formula(tab, rng)]]
    return out


def archive_winners(rollover, file_id: str, vote_year: int) -> None:
    """「得獎人提詞」第 1 列獎項、下方「地區/姓名」→ 加到「歷年得獎人」。"""
    values = read(rollover, file_id, f"'{PROMPT_SHEET}'!A1:H40")
    if not values:
        return
    awards = [a.strip("『』 ") for a in values[0]]
    rows = []
    for row in values[1:]:
        for award, cell in zip(awards, row):
            m = re.fullmatch(r"\s*([^/\s]+)\s*/\s*(\S+)\s*", cell or "")
            if award and m:
                rows.append([f"'{vote_year}", m.group(1), award, m.group(2)])
    rollover.log(f"  {PROMPT_SHEET} {vote_year} 年度得獎人 {len(rows)} 筆 → {WINNERS_SHEET}")
    if rows:
        rollover.sheets.spreadsheets().values().append(
            spreadsheetId=file_id, range=f"'{WINNERS_SHEET}'!A1", valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS", body={"values": rows}).execute()


def response_formula(tab: str, rng: str) -> str:
    return f"=IFERROR(FILTER('{tab}'!{rng},'{tab}'!A2:A<>\"\"),\"\")"


def response_tab_target(header: List[str]) -> Optional[str]:
    """依新增回應工作表的標題列判斷是哪份表單的回應：參與表單有「飲食習慣」，投票表單姓名題目帶（地區）。"""
    text = " ".join(header)
    if "家事達人獎" in text:
        for a in ROSTER_AREAS:
            if f"（{a}）" in text:
                return RESPONSE_TABS[f"春酒獎項投票-{a}"][0]
        return None
    if "飲食習慣" in text or "參與春酒" in text:
        return RESPONSE_TABS[ATTEND_SHEET][0]
    return None


def link_response_tabs(rollover, file_id: str) -> None:
    """表單連結試算表後 Google 新增的工作表自動改成固定名稱，並重寫帶入公式。"""
    ids = sheet_ids(rollover, file_id)
    known = set(RESPONSE_TABS) | {tab for tab, _ in RESPONSE_TABS.values()}
    reqs, renamed = [], []
    for title, sid in ids.items():
        if title in known or not re.match(r"^(表單回應|表單回覆|Form Responses)\s*\d*$", title):
            continue
        header = (read(rollover, file_id, f"'{title}'!A1:Z1") or [[]])[0]
        target = response_tab_target(header)
        if not target:
            rollover.log(f"  ⚠ 無法判斷「{title}」是哪份表單的回應，請檢查投票表單姓名題目是否帶地區")
        elif target in ids or target in renamed:
            rollover.log(f"  ⚠ 「{target}」已存在，「{title}」未改名（可能重複連結表單）")
        else:
            reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "title": target},
                                                   "fields": "title"}})
            renamed.append(target)
            rollover.log(f"  回應工作表「{title}」→「{target}」")
    if rollover.dry_run or not reqs:
        return
    batch_update(rollover, file_id, reqs)
    ids = sheet_ids(rollover, file_id)
    rollover._values_batch(file_id, [{"range": f"'{s}'!A2", "values": [[response_formula(tab, rng)]]}
                                     for s, (tab, rng) in RESPONSE_TABS.items() if s in ids])


def prepare_response_file(rollover, file_id: str, event: Dict[str, Any]) -> None:
    """新建的回覆試算表：名冊分頁改年度、記錄去年得獎人、清空待填欄位並設黃底、文字與公式改新年度。"""
    y = rollover.new_year
    ids = sheet_ids(rollover, file_id)
    reqs: List[Dict[str, Any]] = []
    for title, sid in ids.items():
        m = ROSTER_SHEET_PATTERN.match(title)
        target = (ROSTER_SHEET if m and m.group(1) == "專員" else INNER_SHEET).format(Y=y) if m else ""
        if m and title != target:
            reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "title": target},
                                                   "fields": "title"}})
    if WINNERS_SHEET not in ids:
        reqs.append({"addSheet": {"properties": {"title": WINNERS_SHEET}}})
    # 去年連結表單產生的回應工作表（複製後已不再連結）刪掉，新年度連結後再改成同名
    reqs += [{"deleteSheet": {"sheetId": ids[tab]}} for tab, _ in RESPONSE_TABS.values() if tab in ids]
    batch_update(rollover, file_id, reqs)
    ids = sheet_ids(rollover, file_id)
    if not read(rollover, file_id, f"'{WINNERS_SHEET}'!A1:G1"):
        rollover.sheets.spreadsheets().values().update(
            spreadsheetId=file_id, range=f"'{WINNERS_SHEET}'!A1", valueInputOption="RAW",
            body={"values": [WINNERS_HEADER + ["", "排除幾年內得獎人", DEFAULT_EXCLUDE_YEARS]]}).execute()
    if PROMPT_SHEET in ids:
        archive_winners(rollover, file_id, y - 2)

    clear = [(s, rng, bg) for s, rng, bg in TEMPLATE_CLEAR if s in ids]
    rollover.log(f"  清空去年資料：{'、'.join(f'{s}!{rng}' for s, rng, _ in clear)}")
    rollover.sheets.spreadsheets().values().batchClear(
        spreadsheetId=file_id, body={"ranges": [f"'{s}'!{rng}" for s, rng, _ in clear]}).execute()
    batch_update(rollover, file_id, [yellow(ids[s], rng) for s, rng, bg in clear if bg]
                 + [yellow(ids[WINNERS_SHEET], "G1:G1")])

    when = ""
    if event.get("date"):
        d = event["date"]
        when = f"{d.year}年{d:%m}月{d:%d}日({WEEKDAYS[d.weekday()]})"
    data = [{"range": cell, "values": [[text.format(Y=y)]]} for cell, text in TEMPLATE_TEXT.items()
            if cell.split("!")[0].strip("'") in ids]
    data += [{"range": f"'{s}'!A3", "values": [[c.format(d=when) for c in row]]}
             for s, row in DATE_LABEL_ROWS.items() if s in ids]
    data += [{"range": f"'{s}'!A2", "values": [[response_formula(tab, rng)]]}
             for s, (tab, rng) in RESPONSE_TABS.items() if s in ids]
    rollover._values_batch(file_id, data)


def update_roster(rollover, file_id: str, roster: Dict[str, List[List[str]]]) -> None:
    """寫入「{Y}春酒專員名冊」，並讓春酒參與統計的人工欄（眷屬人數）跟著同一人。"""
    y = rollover.new_year
    sheet = ROSTER_SHEET.format(Y=y)
    ids = sheet_ids(rollover, file_id)
    if sheet not in ids:
        batch_update(rollover, file_id, [{"addSheet": {"properties": {
            "title": sheet, "index": 0, "gridProperties": {"frozenRowCount": 1}}}}])
        ids = sheet_ids(rollover, file_id)
    current = read(rollover, file_id, f"'{sheet}'!A2:E1000")
    threshold = (read(rollover, file_id, f"'{sheet}'!{THRESHOLD_CELL}") or [[DEFAULT_THRESHOLD]])[0][0]

    rows: List[List[Any]] = []
    for area in ROSTER_AREAS:
        if area in roster:
            staff = roster[area]
        else:  # 這次沒匯出的地區保留原資料
            staff = [[r[1], f"'{r[2]}" if len(r) > 2 and r[2] else "", *(r[3:5] + [""] * (5 - len(r)))[:2]]
                     for r in current if r and r[0] == area and len(r) > 1 and not excluded_name(r[1])]
        if staff:
            rows += roster_rows(area, staff, load_hours(rollover, area), len(rows) + 2, y)
    header = [h.format(y1=y - 2, y2=y - 3) for h in ROSTER_HEADER]
    values = rollover.sheets.spreadsheets().values()
    values.batchClear(spreadsheetId=file_id, body={"ranges": [f"'{sheet}'!A1:AB1000"]}).execute()
    rollover._values_batch(file_id, [
        {"range": f"'{sheet}'!A1", "values": [header]},
        {"range": f"'{sheet}'!A2", "values": rows},
        {"range": f"'{sheet}'!{THRESHOLD_LABEL_CELL}", "values": [["時數門檻", threshold]]},
    ])
    batch_update(rollover, file_id, [yellow(ids[sheet], f"{THRESHOLD_CELL}:{THRESHOLD_CELL}")])
    rollover.log(f"  {sheet}：{len(rows)} 人")


def manual_snapshot(rollover, file_id: str) -> Dict[str, List[str]]:
    """春酒統計表人工填的眷屬人數（I～K），依姓名記住；要在名冊更新前讀，名冊一改 B 欄順序就會變。"""
    try:
        existing = read(rollover, file_id, f"'{STATS_SHEET}'!B2:K{STATS_ROWS}")
    except Exception:
        return {}
    return {r[0]: (r[7:10] + [""] * 3)[:3] for r in existing if r and r[0]}


def repair_formulas(rollover, file_id: str, manual: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """公式檢查與修復：春酒統計表、眷屬收費、抽獎籤、表單回應帶入公式全部重寫成標準版本，
    眷屬人數（I～K）依姓名保留；最後掃描錯誤值，回傳有錯誤的工作表範圍。"""
    y = rollover.new_year
    ids = sheet_ids(rollover, file_id)
    sheet, inner_sheet = ROSTER_SHEET.format(Y=y), INNER_SHEET.format(Y=y)
    values = rollover.sheets.spreadsheets().values()
    data = [{"range": cell, "values": v} for cell, v in other_formulas().items()
            if cell.split("!")[0].strip("'") in ids]
    if STATS_SHEET in ids:
        manual = manual_snapshot(rollover, file_id) if manual is None else manual
        roster = read(rollover, file_id, f"'{sheet}'!A2:B1000") if sheet in ids else []
        inner = read(rollover, file_id, f"'{inner_sheet}'!A2:B200") if inner_sheet in ids else []
        order = attend_order(roster, inner)
        values.batchClear(spreadsheetId=file_id, body={"ranges": [
            f"'{STATS_SHEET}'!A2:H{STATS_ROWS}", f"'{STATS_SHEET}'!{STATS_MANUAL}{STATS_ROWS}",
            f"'{STATS_SHEET}'!L2:O{STATS_ROWS}", f"'{STATS_SHEET}'!{SUMMARY_COL}1:AA60"]}).execute()
        data += [{"range": f"'{STATS_SHEET}'!{cell}", "values": v} for cell, v in
                 {**stats_person_formulas(y), **stats_summary_formulas(y)}.items()]
        data.append({"range": f"'{STATS_SHEET}'!I2", "values": [manual.get(n, ["", "", ""]) for n in order]})
        data.append({"range": f"'{STATS_SHEET}'!A1", "values": [[
            "地區", "姓名", "回覆", "到職日", "參加否", "葷/素食", "攜伴否", "攜伴人數", "眷屬6歲以下",
            "眷屬7-12歲", "眷屬13歲以上", f"{y}年到職（本人付費）", "達時數門檻", "平均時數", "去年得獎獎項"]]})
    rollover._values_batch(file_id, data)
    rollover.log(f"  公式檢查與修復：{STATS_SHEET}、{GIFT_SHEET}、抽獎籤、表單回應帶入公式已重寫")

    problems = []
    for name, rng in [(STATS_SHEET, "A1:AA60"), (sheet, "A1:AB60"), (GIFT_SHEET, "A1:H60"), ("抽獎籤", "A1:K20")]:
        if name not in ids:
            continue
        for i, row in enumerate(read(rollover, file_id, f"'{name}'!{rng}"), start=1):
            bad = [v for v in row if str(v).strip() in ERROR_MARKS]
            if bad:
                problems.append(f"{name} 第 {i} 列（{'、'.join(sorted(set(bad)))}）")
    for p_ in problems[:20]:
        rollover.log(f"  ⚠ 公式錯誤：{p_}")
    if not problems:
        rollover.log("  公式檢查：沒有錯誤值")
    return problems


def vote_exclusions(rollover, file_id: str) -> Tuple[Dict[str, Set[str]], Set[str]]:
    """({獎項: 近 N 年得獎人}, 內勤姓名)；N 為「歷年得獎人」G1。"""
    winners = read(rollover, file_id, f"'{WINNERS_SHEET}'!A1:G1000")
    try:
        years = int(float(winners[0][6]))
    except (IndexError, ValueError):
        years = DEFAULT_EXCLUDE_YEARS
    vote_year = rollover.prev_year
    excluded: Dict[str, Set[str]] = {}
    for row in winners[1:]:
        if len(row) < 4 or not row[0].strip().isdigit():
            continue
        if vote_year - years <= int(row[0]) < vote_year:
            excluded.setdefault(row[2].strip("『』 "), set()).add(row[3].strip())
    inner = read(rollover, file_id, f"'{INNER_SHEET.format(Y=rollover.new_year)}'!B2:B200")
    return excluded, {r[0] for r in inner if r}


# ---------- 複製前一年度的檔案與資料夾 ----------
def copy_from_prev(rollover, prev_files: List[Dict[str, Any]], folder: Dict[str, Any],
                   old_names: List[str], new_name: str, mime: str, target_mime: str = "") -> Dict[str, str]:
    """依序找 old_names 中第一個存在且類型相符的檔案，複製成 new_name（同名同類型已存在就沿用）。
    target_mime 有值時複製同時轉檔（例：Word → Google 文件）。"""
    source = next((f for n in old_names for f in prev_files if f["name"] == n and f["mimeType"] == mime), None)
    mime = target_mime or mime
    if not source:
        rollover.log(f"  ✗ 前一年度找不到：{'／'.join(old_names)}")
        return {"kind": "檔案", "old_name": old_names[0], "old_id": "", "new_name": "找不到來源", "new_id": ""}
    existing = [] if folder["id"].startswith("(dry-run") else [
        f for f in rollover.children(folder["id"]) if f["name"] == new_name and f["mimeType"] == mime]
    if existing and not rollover.fresh:
        rollover.log(f"  檔案已存在，沿用：{new_name}")
        new, created = existing[0], False
    else:
        for item in existing:
            rollover._retire(item)
        rollover.log(f"  複製檔案：{source['name']} → {new_name}")
        new = {"id": f"(dry-run:{new_name})", "name": new_name} if rollover.dry_run else \
            rollover.drive.files().copy(fileId=source["id"], fields="id,name", supportsAllDrives=True,
                                        body={"name": new_name, "parents": [folder["id"]],
                                              **({"mimeType": target_mime} if target_mime else {})}).execute()
        created = True
    return {"kind": "檔案", "old_name": source["name"], "old_id": source["id"],
            "new_name": new["name"], "new_id": new["id"], "created": created}


def copy_subfolders_and_files(rollover, prev_files: List[Dict[str, Any]], folder: Dict[str, Any]
                              ) -> List[Dict[str, str]]:
    """前一年度的子資料夾照建（名稱年份換新，不複製內容），COPY_FILES 複製成 Google 文件。"""
    y, py = str(rollover.new_year), str(rollover.prev_year)
    records = []
    for f in prev_files:
        if f["mimeType"] == FOLDER_MIME:
            sub = rollover.ensure_folder(folder["id"], f["name"].replace(py, y))
            records.append({"kind": "資料夾", "old_name": f["name"], "old_id": f["id"],
                            "new_name": sub["name"], "new_id": sub["id"]})
    for name in COPY_FILES:
        source = next((f for f in prev_files if f["name"] in (name, f"{name}.docx")), None)
        if source:
            records.append(copy_from_prev(rollover, prev_files, folder, [source["name"]], name,
                                          source["mimeType"], DOC_MIME))
    return records


# ---------- Google 表單 ----------
def get_forms_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    from tools.common.google_auth import get_google_credentials

    base = get_google_credentials()
    creds = Credentials(token=None, refresh_token=base.refresh_token, token_uri=base.token_uri,
                        client_id=base.client_id, client_secret=base.client_secret,
                        scopes=[FORMS_SCOPE])
    return build("forms", "v1", credentials=creds, cache_discovery=False)


def attendance_title(title: str, event_date: date, period: str) -> Optional[str]:
    """把「…方便於03/14(六)中午，參與春酒？」的日期與時段換掉；不是出席題目回傳 None。"""
    if "春酒" not in title or not ATTEND_DATE.search(title):
        return None
    when = f"{event_date:%m/%d}({WEEKDAYS[event_date.weekday()]}){period}"
    return ATTEND_DATE.sub(lambda m: m.group(1) + when, title, count=1)


def vote_choice_updates(form: Dict[str, Any], names: List[str],
                        excluded: Optional[Dict[str, Set[str]]] = None) -> List[Dict[str, Any]]:
    """姓名、得獎題目（選擇題）的選項換成 names；得獎題目再排除近年得過同一獎項的人；保留「其他」。"""
    names = [n for n in dict.fromkeys(names) if n and not excluded_name(n)]
    requests_: List[Dict[str, Any]] = []
    for index, item in enumerate(form.get("items", [])):
        title = item.get("title", "")
        choice = item.get("questionItem", {}).get("question", {}).get("choiceQuestion")
        if not choice or not any(k in title for k in VOTE_NAME_KEYWORDS):
            continue
        skip = set().union(*[v for award, v in (excluded or {}).items() if award and award in title])
        choice["options"] = ([{"value": n} for n in names if n not in skip]
                             + [o for o in choice.get("options", []) if o.get("isOther")])
        requests_.append({"updateItem": {
            "item": item, "location": {"index": index},
            "updateMask": "questionItem.question.choiceQuestion.options",
        }})
    return requests_


def attendance_description(text: str, event_date: date, period: str) -> Optional[str]:
    """表單說明「春酒時間：2026/03/14(六)-中午」換成新日期；找不到回傳 None。"""
    if not ATTEND_DESC.search(text or ""):
        return None
    when = f"{event_date:%Y/%m/%d}({WEEKDAYS[event_date.weekday()]})-{period}"
    return ATTEND_DESC.sub(lambda m: m.group(1) + when, text, count=1)


def update_attendance_form(rollover, forms, form_id: str, event_date: date, period: str) -> None:
    form = forms.forms().get(formId=form_id).execute()
    desc = attendance_description(form.get("info", {}).get("description", ""), event_date, period)
    if desc:
        rollover.log(f"  參與統計表單說明：春酒時間 → {event_date:%Y/%m/%d}-{period}")
        if not rollover.dry_run:
            forms.forms().batchUpdate(formId=form_id, body={"requests": [{"updateFormInfo": {
                "info": {"description": desc}, "updateMask": "description"}}]}).execute()
    else:
        rollover.log("  ⚠ 參與統計表單說明找不到「春酒時間：YYYY/MM/DD(週)-時段」，說明未更新")
    for index, item in enumerate(form.get("items", [])):
        new_title = attendance_title(item.get("title", ""), event_date, period)
        if not new_title:
            continue
        rollover.log(f"  參與統計表單：{item['title']} → {new_title}")
        if not rollover.dry_run:
            item["title"] = new_title
            forms.forms().batchUpdate(formId=form_id, body={"requests": [{"updateItem": {
                "item": item, "location": {"index": index}, "updateMask": "title"}}]}).execute()
        return
    rollover.log("  ⚠ 參與統計表單找不到「方便於MM/DD(週)…參與春酒」的題目，日期未更新")


def region_tag_updates(form: Dict[str, Any], area: str) -> List[Dict[str, Any]]:
    """投票表單「姓名」題目尾端加上（地區），連結試算表後才能從標題列分辨是哪一區的回應。"""
    tag = f"（{area}）"
    reqs = []
    for index, item in enumerate(form.get("items", [])):
        title = item.get("title", "")
        if "姓名" in title and "questionItem" in item and not title.rstrip().endswith(tag):
            item = dict(item, title=title.rstrip() + tag)
            reqs.append({"updateItem": {"item": item, "location": {"index": index}, "updateMask": "title"}})
    return reqs


def update_vote_form(rollover, forms, form_id: str, form_name: str, names: List[str],
                     excluded: Dict[str, Set[str]], area: str = "") -> None:
    form = forms.forms().get(formId=form_id).execute()
    reqs = vote_choice_updates(form, names, excluded)
    if area:
        reqs += region_tag_updates(form, area)
    for req in reqs:
        item = req["updateItem"]["item"]
        count = len(item["questionItem"]["question"]["choiceQuestion"]["options"])
        rollover.log(f"  {form_name}：{item['title'].splitlines()[0]} → {count} 個選項")
    if reqs and not rollover.dry_run:
        forms.forms().batchUpdate(formId=form_id, body={"requests": reqs}).execute()


# ---------- 主流程 ----------
def export_roster(rollover, areas: List[str]) -> Tuple[Dict[str, List[List[str]]], List[Dict[str, str]]]:
    from tools.scheduled_monthly.stored_value_settlement import load_accounts

    accounts = load_accounts()
    roster: Dict[str, List[List[str]]] = {}
    failed = []
    for a in areas:
        try:
            roster[a] = active_rows(export_staff_frame(a, accounts))
            rollover.log(f"  {a}：狀態「{ACTIVE_STATUS}」{len(roster[a])} 人（已排除姓名含{'／'.join(EXCLUDE_NAME_KEYWORDS)}）")
        except Exception as exc:  # 一區失敗不影響其他地區
            rollover.log(f"  ✗ {a} 專員個資匯出失敗：{exc}")
            failed.append({"kind": "檔案", "old_name": f"專員個資-{a}", "old_id": "",
                           "new_name": "找不到來源", "new_id": ""})
    return roster, failed


def run_spring_area(rollover, area: str = "全區") -> List[Dict[str, str]]:
    """生成／更新「{Y}春酒」專區；area 為「全區」時處理台北、台中、桃園、新竹。"""
    areas = ROSTER_AREAS if area in ("", "全區") else [a for a in ROSTER_AREAS if a == area]
    if not areas:
        rollover.log(f"  春酒專區沒有「{area}」地區，略過")
        return []
    if rollover.fresh and areas != ROSTER_AREAS:
        # 重新生成會把整個「{Y}春酒」資料夾改名為舊版，只跑單一地區會讓其他地區的表單遺失
        rollover.log("  ✗ 春酒專區「重新生成」只能選全區，已略過")
        return []
    y, py = rollover.new_year, rollover.prev_year
    event = getattr(rollover, "spring_event", None) or {}
    folder = rollover.ensure_folder(SPRING_ROOT_FOLDER_ID, YEAR_FOLDER_NAME.format(Y=y))
    records: List[Dict[str, str]] = [{"kind": "資料夾", "old_name": YEAR_FOLDER_NAME.format(Y=py), "old_id": "",
                                      "new_name": folder["name"], "new_id": folder["id"]}]

    prev = next((f for f in rollover.children(SPRING_ROOT_FOLDER_ID)
                 if f["name"] == YEAR_FOLDER_NAME.format(Y=py)), None)
    prev_files = rollover.children(prev["id"]) if prev else []
    if not prev:
        rollover.log(f"  ✗ 找不到前一年度資料夾：{YEAR_FOLDER_NAME.format(Y=py)}")

    attend = copy_from_prev(rollover, prev_files, folder, [ATTEND_FORM_NAME.format(Y=py)],
                            ATTEND_FORM_NAME.format(Y=y), FORM_MIME)
    votes = {a: copy_from_prev(rollover, prev_files, folder, [VOTE_FORM_NAME.format(V=py - 1, a=a)],
                               VOTE_FORM_NAME.format(V=py, a=a), FORM_MIME) for a in areas}
    response = copy_from_prev(rollover, prev_files, folder, [n.format(P=py) for n in RESPONSE_SOURCES],
                              RESPONSE_FILE_NAME.format(Y=y), SHEET_MIME)
    records += [attend, *votes.values(), response]
    records += copy_subfolders_and_files(rollover, prev_files, folder)

    def ready(rec: Dict[str, str]) -> bool:  # 預覽模式下新複製的檔案還不存在，無法讀寫
        return rec["new_name"] != "找不到來源" and not rec["new_id"].startswith("(dry-run")

    roster, failed = export_roster(rollover, areas)
    records += failed
    excluded: Dict[str, Set[str]] = {}
    inner: Set[str] = set()
    if ready(response):
        if rollover.dry_run:
            rollover.log(f"  （預覽）{response['new_name']}：會寫入 {ROSTER_SHEET.format(Y=y)}"
                         + ("、清空去年資料" if response.get("created") else ""))
        else:
            if response.get("created"):
                prepare_response_file(rollover, response["new_id"], event)
            manual = manual_snapshot(rollover, response["new_id"])
            update_roster(rollover, response["new_id"], roster)
        try:
            link_response_tabs(rollover, response["new_id"])
        except Exception as exc:
            rollover.log(f"  ⚠ 回應工作表改名失敗：{exc}")
        if not rollover.dry_run:
            try:
                repair_formulas(rollover, response["new_id"], manual)
            except Exception as exc:
                rollover.log(f"  ✗ 公式檢查與修復失敗：{exc}")
        try:
            excluded, inner = vote_exclusions(rollover, response["new_id"])
        except Exception as exc:
            rollover.log(f"  ⚠ 讀不到「{WINNERS_SHEET}」／內勤名單，投票選項不排除：{exc}")

    if not event.get("date"):
        rollover.log("  未設定春酒日期，參與統計表單日期未更新")
    targets = [a for a, v in votes.items() if ready(v) and roster.get(a)]
    jobs = []  # (表單名稱, 更新函式)；每份表單各自處理，一份失敗不影響其他
    if event.get("date") and ready(attend):
        jobs.append((attend["new_name"], lambda forms: update_attendance_form(
            rollover, forms, attend["new_id"], event["date"], event.get("period") or "中午")))
    for a in targets:
        names = [r[0] for r in roster[a] if r[0] not in inner]
        jobs.append((votes[a]["new_name"], lambda forms, a=a, names=names: update_vote_form(
            rollover, forms, votes[a]["new_id"], votes[a]["new_name"], names, excluded, a)))
    forms = None
    if jobs:
        try:
            forms = get_forms_service()
        except Exception as exc:
            rollover.log(f"  ✗ 無法連線 Google 表單（確認已啟用 Forms API、OAuth 含 forms.body）：{exc}")
    for name, job in jobs:
        try:
            if forms is None:
                raise RuntimeError("Google 表單服務無法使用")
            job(forms)
        except Exception as exc:
            rollover.log(f"  ✗ 更新表單「{name}」失敗：{exc}")
            records.append({"kind": "表單", "old_name": name, "old_id": "", "new_name": "找不到來源", "new_id": ""})
    rollover.log("  ⚠ 各表單「回覆」頁連結至「" + RESPONSE_FILE_NAME.format(Y=y)
                 + "」後，請再執行一次春酒專區，程式會自動把新增的回應工作表改名")
    return records
