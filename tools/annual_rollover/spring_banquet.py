"""
tools/annual_rollover/spring_banquet.py

生成新年度「春酒專區」：尾牙春酒專用區 →「{Y}春酒」資料夾。

1. 從「{Y-1}春酒」複製表單與回覆試算表（已存在就沿用）
   - {Y-1}春酒參與統計表單 → {Y}春酒參與統計表單
   - {Y-2}年度投票（地區） → {Y-1}年度投票（地區）
   - {Y-1}春酒參與統計表單 (回覆) → {Y}春酒參與統計表單 (回覆)
2. 回覆試算表新建時整理（prepare_response_file）：
   - 「統計」去年得獎人先記到「歷年得獎人」，再清空
   - 「{Y-1}春酒專員名冊」分頁改名為「{Y}春酒專員名冊」
   - 表單回覆清空；工作分配、活動流程等要重填的欄位清空並設黃底；標題改新年度
3. 每次執行更新名冊（update_roster）：
   - 「{Y}春酒專員名冊」：A 地區 B 姓名 C 電話 D 到職日期 E 狀態，F～Q 1～12 月時數，
     R 總時數 S 月份 T 平均時數 U 達門檻（X1 時數門檻）
     名單＝檸檬後台專員個資狀態「正常」（排除「檸檬人」測試帳號）；
     登入沿用月排程「儲值金結算」load_accounts()，匯出沿用日排程「專員個資」；
     時數取各地區檔案「{Y-1}排班統計表」
   - 「初步統計」A～B 名單：專員換成名冊，內勤列保留
4. Google 表單（OAuth 需 forms.body，專案需啟用 Google Forms API）
   - 參與統計表單：出席題目換成執行畫面設定的春酒日期與時段
   - 各區投票表單：姓名、得獎題目的選項換成名冊姓名；排除內勤，以及
     「歷年得獎人」中 G1 年內得過「同一獎項」的人

表單回覆要連到哪個試算表，Forms API 無法設定，仍需在表單「回覆」頁手動連結。
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
RESPONSE_FILE_NAME = "{Y}春酒參與統計表單 (回覆)"
ROSTER_AREAS = ["台北", "台中", "桃園", "新竹"]
SHEET_MIME = "application/vnd.google-apps.spreadsheet"

# ---------- 回覆試算表裡的工作表 ----------
ROSTER_SHEET = "{Y}春酒專員名冊"
ROSTER_HEADER = (["地區", "姓名", "電話", "到職日期", "狀態"] + [str(m) for m in range(1, 13)]
                 + ["總時數", "月份", "平均時數", "達門檻"])
THRESHOLD_LABEL_CELL, THRESHOLD_CELL, DEFAULT_THRESHOLD = "W1", "X1", 48
PRELIM_SHEET = "初步統計"
ATTEND_SHEET = "春酒參與統計"
STATS_SHEET = "統計"
WINNERS_SHEET = "歷年得獎人"
WINNERS_HEADER = ["投票年度", "地區", "獎項", "姓名"]
EXCLUDE_YEARS_CELL, DEFAULT_EXCLUDE_YEARS = "G1", 1

ACTIVE_STATUS = "正常"
TEST_ACCOUNT_PREFIX = "檸檬人"                          # 後台測試帳號，所有地區都排除
ROSTER_COLUMNS = ["姓名", "電話", "到職日期", "狀態"]   # 後台匯出的 A/B/G/N 欄
ROSTER_COLUMN_INDEX = [0, 1, 6, 13]                    # 標題對不上時的備援欄位位置
YELLOW = {"red": 1, "green": 0.95, "blue": 0.6}         # 新年度要填的欄位底色

# 新建回覆試算表時清空的範圍：(工作表, 範圍, 是否設黃底提醒填入)
TEMPLATE_CLEAR: List[Tuple[str, str, bool]] = (
    [(f"春酒獎項投票-{a}", "A2:H", False) for a in ROSTER_AREAS] + [
        (ATTEND_SHEET, "A2:G", False),
        (STATS_SHEET, "B2:F9", True),
        (PRELIM_SHEET, "G2:I300", True), (PRELIM_SHEET, "L2:L300", True),
        ("活動流程", "A3:F3", True), ("活動流程", "G5:G24", True), ("活動流程", "J5:M24", True),
        ("主持人流程台詞", "A3:E3", True), ("主持人流程台詞", "E5:E25", True),
        ("得獎人提詞", "A2:D13", True),
        ("眷屬收費", "A2:H80", True),
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
}
DATE_LABEL_ROWS = {"活動流程": ["日期：{d}", "", "", "時間：", "", "地點："],
                   "主持人流程台詞": ["日期：{d}", "", "時間：", "", "地點："]}

FORMS_SCOPE = "https://www.googleapis.com/auth/forms.body"
ATTEND_DATE = re.compile(r"(方便於)\s*\d{1,2}/\d{1,2}\s*[(（][^)）]*[)）][^，,？?]*")
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


def to_hours(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


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
    """狀態「正常」且非測試帳號的 姓名／電話／到職日期／狀態；電話加 ' 保留開頭的 0。"""
    if all(col in df.columns for col in ROSTER_COLUMNS):
        picked = df[ROSTER_COLUMNS]
    else:
        picked = df.iloc[:, ROSTER_COLUMN_INDEX]
    rows = []
    for name, phone, onboard, status in picked.astype(str).values.tolist():
        name = name.strip()
        if status.strip() != ACTIVE_STATUS or name.startswith(TEST_ACCOUNT_PREFIX):
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
def roster_rows(area: str, staff: List[List[str]], hours: Dict[str, List[float]], start_row: int) -> List[List[Any]]:
    rows = []
    for offset, (name, phone, onboard, status) in enumerate(staff):
        r = start_row + offset
        rows.append([area, name, phone, onboard, status, *hours.get(name, [0] * 12),
                     f"=SUM(F{r}:Q{r})", f"=COUNTIF(F{r}:Q{r},\"<>0\")", f"=IFERROR(R{r}/S{r},0)",
                     f"=IF(T{r}>${THRESHOLD_CELL[0]}${THRESHOLD_CELL[1:]},\"Y\",\"\")"])
    return rows


def prelim_rows(existing: List[List[str]], roster: Dict[str, List[str]]) -> List[List[str]]:
    """初步統計 A～B：依原本的分組順序，有新名冊的「X專員」換成名冊（排除內勤名字），其餘保留。"""
    inner = {r[1] for r in existing if len(r) > 1 and r[0].endswith("內勤")}
    order: List[str] = []
    groups: Dict[str, List[str]] = {}
    for r in existing:
        if len(r) < 2 or not r[0]:
            continue
        if r[0] not in groups:
            order.append(r[0])
            groups[r[0]] = []
        groups[r[0]].append(r[1])
    for area, names in roster.items():
        label = f"{area}專員"
        if label not in groups:
            order.append(label)
        groups[label] = [n for n in names if n not in inner]
    return [[label, n] for label in order for n in groups[label]]


def prelim_formulas(rows: List[List[str]], roster_sheet: str) -> List[List[str]]:
    """初步統計 A～K 每列（G～I 眷屬人數留白給人填）。"""
    rs = f"'{roster_sheet}'"
    out = []
    for i, (label, name) in enumerate(rows):
        r = i + 2
        look = [f"=xlookup($B{r},'{ATTEND_SHEET}'!$B:$B,'{ATTEND_SHEET}'!{c}:{c})" for c in "GDEF"]
        if label.endswith("內勤"):
            jk = ["", f"={rs}!${THRESHOLD_CELL[0]}${THRESHOLD_CELL[1:]}"]
        else:
            jk = [f"=IFERROR(XLOOKUP($B{r},{rs}!$B:$B,{rs}!$D:$D),\"\")",
                  f"=IFERROR(XLOOKUP($B{r},{rs}!$B:$B,{rs}!$T:$T),0)"]
        out.append([label, name, *look, "", "", "", *jk])
    return out


def attend_formulas(count: int, roster_sheet: str) -> List[List[str]]:
    """春酒參與統計 N～P：是否已回覆、到職日、是否達門檻（L～M 由 ARRAYFORMULA 帶初步統計名單）。"""
    rs = f"'{roster_sheet}'"
    return [[f"=vlookup(M{r},B:B,1,false)", f"=xlookup(M{r},{rs}!B:B,{rs}!D:D)",
             f"=xlookup(M{r},{rs}!B:B,{rs}!U:U)"] for r in range(2, count + 2)]


def archive_winners(rollover, file_id: str, vote_year: int) -> None:
    """把「統計」B2:F9 的得獎人（A 欄地區、第 1 列獎項）加到「歷年得獎人」。"""
    values = read(rollover, file_id, f"'{STATS_SHEET}'!A1:F9")
    if not values:
        return
    awards = [a.strip("『』 ") for a in values[0][1:]]
    rows, area = [], ""
    for row in values[1:]:
        area = (row[0].strip() if row and row[0].strip() else area)
        for award, name in zip(awards, row[1:]):
            if award and name.strip():
                rows.append([f"'{vote_year}", area, award, name.strip()])
    rollover.log(f"  {STATS_SHEET} {vote_year} 年度得獎人 {len(rows)} 筆 → {WINNERS_SHEET}")
    if rows:
        rollover.sheets.spreadsheets().values().append(
            spreadsheetId=file_id, range=f"'{WINNERS_SHEET}'!A1", valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS", body={"values": rows}).execute()


def prepare_response_file(rollover, file_id: str, event: Dict[str, Any]) -> None:
    """新建的回覆試算表：記錄去年得獎人、名冊分頁改年度、清空待填欄位並設黃底、標題改新年度。"""
    y = rollover.new_year
    ids = sheet_ids(rollover, file_id)
    reqs: List[Dict[str, Any]] = []
    if WINNERS_SHEET not in ids:
        reqs.append({"addSheet": {"properties": {"title": WINNERS_SHEET}}})
    old_roster = ROSTER_SHEET.format(Y=rollover.prev_year)
    if old_roster in ids:
        reqs.append({"updateSheetProperties": {"properties": {"sheetId": ids[old_roster],
                     "title": ROSTER_SHEET.format(Y=y)}, "fields": "title"}})
    batch_update(rollover, file_id, reqs)
    ids = sheet_ids(rollover, file_id)
    if not read(rollover, file_id, f"'{WINNERS_SHEET}'!A1:G1"):
        rollover.sheets.spreadsheets().values().update(
            spreadsheetId=file_id, range=f"'{WINNERS_SHEET}'!A1", valueInputOption="RAW",
            body={"values": [WINNERS_HEADER + ["", "排除幾年內得獎人", DEFAULT_EXCLUDE_YEARS]]}).execute()
    if STATS_SHEET in ids:
        archive_winners(rollover, file_id, y - 2)

    clear = [(s, rng, bg) for s, rng, bg in TEMPLATE_CLEAR if s in ids]
    rollover.log(f"  清空去年資料：{'、'.join(f'{s}!{rng}' for s, rng, _ in clear)}")
    rollover.sheets.spreadsheets().values().batchClear(
        spreadsheetId=file_id, body={"ranges": [f"'{s}'!{rng}" for s, rng, _ in clear]}).execute()
    reqs = [{"repeatCell": {"range": a1_to_grid(ids[s], rng),
                            "cell": {"userEnteredFormat": {"backgroundColorStyle": {"rgbColor": YELLOW}}},
                            "fields": "userEnteredFormat.backgroundColorStyle"}}
            for s, rng, bg in clear if bg]
    reqs.append({"repeatCell": {"range": a1_to_grid(ids[WINNERS_SHEET], "G1:G1"),
                                "cell": {"userEnteredFormat": {"backgroundColorStyle": {"rgbColor": YELLOW}}},
                                "fields": "userEnteredFormat.backgroundColorStyle"}})
    batch_update(rollover, file_id, reqs)

    when = ""
    if event.get("date"):
        d = event["date"]
        when = f"{d.year}年{d:%m}月{d:%d}日({WEEKDAYS[d.weekday()]})"
    data = [{"range": cell, "values": [[text.format(Y=y)]]} for cell, text in TEMPLATE_TEXT.items()
            if cell.split("!")[0].strip("'") in ids]
    data += [{"range": f"'{s}'!A3", "values": [[c.format(d=when) for c in row]]}
             for s, row in DATE_LABEL_ROWS.items() if s in ids]
    rollover._values_batch(file_id, data)


def update_roster(rollover, file_id: str, roster: Dict[str, List[List[str]]]) -> None:
    """寫入「{Y}春酒專員名冊」，並把初步統計／春酒參與統計的名單換成新名冊。"""
    sheet = ROSTER_SHEET.format(Y=rollover.new_year)
    ids = sheet_ids(rollover, file_id)
    if sheet not in ids:
        batch_update(rollover, file_id, [{"addSheet": {"properties": {
            "title": sheet, "index": 0, "gridProperties": {"frozenRowCount": 1}}}}])
        ids = sheet_ids(rollover, file_id)
    current = read(rollover, file_id, f"'{sheet}'!A2:E1000")
    kept = [r for r in current if r and r[0] not in roster]          # 這次沒匯出的地區保留原資料
    threshold = (read(rollover, file_id, f"'{sheet}'!{THRESHOLD_CELL}") or [[DEFAULT_THRESHOLD]])[0][0]

    rows: List[List[Any]] = []
    for area in ROSTER_AREAS:
        if area in roster:
            staff = roster[area]
            hours = load_hours(rollover, area)
        else:
            staff = [[r[1], f"'{r[2]}" if len(r) > 2 and r[2] else "", *(r[3:5] + [""] * (5 - len(r)))[:2]]
                     for r in kept if r[0] == area and len(r) > 1]
            if not staff:
                continue
            hours = load_hours(rollover, area)
        rows += roster_rows(area, staff, hours, len(rows) + 2)
    values = rollover.sheets.spreadsheets().values()
    values.batchClear(spreadsheetId=file_id, body={"ranges": [f"'{sheet}'!A1:U1000"]}).execute()
    rollover._values_batch(file_id, [
        {"range": f"'{sheet}'!A1", "values": [ROSTER_HEADER]},
        {"range": f"'{sheet}'!A2", "values": rows},
        {"range": f"'{sheet}'!{THRESHOLD_LABEL_CELL}", "values": [["時數門檻", threshold]]},
    ])
    batch_update(rollover, file_id, [{"repeatCell": {
        "range": a1_to_grid(ids[sheet], f"{THRESHOLD_CELL}:{THRESHOLD_CELL}"),
        "cell": {"userEnteredFormat": {"backgroundColorStyle": {"rgbColor": YELLOW}}},
        "fields": "userEnteredFormat.backgroundColorStyle"}}])
    rollover.log(f"  {sheet}：{len(rows)} 人")

    if PRELIM_SHEET in ids:
        names = {a: [r[0] for r in staff] for a, staff in roster.items()}
        prelim = prelim_rows(read(rollover, file_id, f"'{PRELIM_SHEET}'!A2:B300"), names)
        values.batchClear(spreadsheetId=file_id, body={"ranges": [
            f"'{PRELIM_SHEET}'!A2:F300", f"'{PRELIM_SHEET}'!J2:K300"]}).execute()
        formulas = prelim_formulas(prelim, sheet)
        rollover._values_batch(file_id, [
            {"range": f"'{PRELIM_SHEET}'!A2", "values": [r[:6] for r in formulas]},
            {"range": f"'{PRELIM_SHEET}'!J2", "values": [r[9:] for r in formulas]},
        ])
        rollover.log(f"  {PRELIM_SHEET}：名單 {len(prelim)} 人（內勤保留）")
        if ATTEND_SHEET in ids:
            values.batchClear(spreadsheetId=file_id, body={"ranges": [f"'{ATTEND_SHEET}'!N2:P300"]}).execute()
            rollover._values_batch(file_id, [{"range": f"'{ATTEND_SHEET}'!N2",
                                              "values": attend_formulas(len(prelim), sheet)}])


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
    prelim = read(rollover, file_id, f"'{PRELIM_SHEET}'!A2:B300")
    inner = {r[1] for r in prelim if len(r) > 1 and r[0].endswith("內勤")}
    return excluded, inner


# ---------- 複製表單與回覆試算表 ----------
def copy_from_prev(rollover, prev_files: List[Dict[str, Any]], folder: Dict[str, Any],
                   old_name: str, new_name: str) -> Dict[str, str]:
    source = next((f for f in prev_files if f["name"] == old_name), None)
    if not source:
        rollover.log(f"  ✗ 前一年度找不到：{old_name}")
        return {"kind": "檔案", "old_name": old_name, "old_id": "", "new_name": "找不到來源", "new_id": ""}
    new, created = rollover.ensure_copy(source, folder["id"], new_name)
    return {"kind": "檔案", "old_name": old_name, "old_id": source["id"],
            "new_name": new["name"], "new_id": new["id"], "created": created}


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
    names = [n for n in dict.fromkeys(names) if n and not n.startswith(TEST_ACCOUNT_PREFIX)]
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


def update_attendance_form(rollover, forms, form_id: str, event_date: date, period: str) -> None:
    form = forms.forms().get(formId=form_id).execute()
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


def update_vote_form(rollover, forms, form_id: str, form_name: str, names: List[str],
                     excluded: Dict[str, Set[str]]) -> None:
    form = forms.forms().get(formId=form_id).execute()
    reqs = vote_choice_updates(form, names, excluded)
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
            rollover.log(f"  {a}：狀態「{ACTIVE_STATUS}」{len(roster[a])} 人（已排除{TEST_ACCOUNT_PREFIX}）")
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

    attend = copy_from_prev(rollover, prev_files, folder, ATTEND_FORM_NAME.format(Y=py),
                            ATTEND_FORM_NAME.format(Y=y))
    votes = {a: copy_from_prev(rollover, prev_files, folder, VOTE_FORM_NAME.format(V=py - 1, a=a),
                               VOTE_FORM_NAME.format(V=py, a=a)) for a in areas}
    response = copy_from_prev(rollover, prev_files, folder, RESPONSE_FILE_NAME.format(Y=py),
                              RESPONSE_FILE_NAME.format(Y=y))
    records += [attend, *votes.values(), response]

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
            update_roster(rollover, response["new_id"], roster)
        try:
            excluded, inner = vote_exclusions(rollover, response["new_id"])
        except Exception as exc:
            rollover.log(f"  ⚠ 讀不到「{WINNERS_SHEET}」／內勤名單，投票選項不排除：{exc}")

    if not event.get("date"):
        rollover.log("  未設定春酒日期，參與統計表單日期未更新")
    targets = [a for a, v in votes.items() if ready(v) and roster.get(a)]
    if (event.get("date") and ready(attend)) or targets:
        try:
            forms = get_forms_service()
            if event.get("date") and ready(attend):
                update_attendance_form(rollover, forms, attend["new_id"], event["date"],
                                       event.get("period") or "中午")
            for a in targets:
                names = [r[0] for r in roster[a] if r[0] not in inner]
                update_vote_form(rollover, forms, votes[a]["new_id"], votes[a]["new_name"], names, excluded)
        except Exception as exc:
            rollover.log(f"  ✗ 更新 Google 表單失敗（確認已啟用 Forms API、OAuth 含 forms.body）：{exc}")
    rollover.log("  ⚠ 表單回覆連到「" + RESPONSE_FILE_NAME.format(Y=y) + "」需在各表單「回覆」頁手動設定")
    return records
