"""
tools/annual_rollover/spring_banquet.py

生成新年度「春酒專區」：尾牙春酒專用區 →「{Y}春酒」資料夾，依序：

1. 從「{Y-1}春酒」複製表單與回覆試算表（已存在就沿用）
   - {Y-1}春酒參與統計表單 → {Y}春酒參與統計表單
   - {Y-2}年度投票（地區） → {Y-1}年度投票（地區）
   - {Y-1}春酒參與統計表單 (回覆) → {Y}春酒參與統計表單 (回覆)（新建時清掉去年的回覆）
2. 「{Y}春酒專員名冊」：各地區分頁，內容為檸檬後台專員個資中狀態「正常」的
   姓名／電話／到職日期／狀態（A/B/G/N 欄）
   - 各地區登入帳號：沿用月排程「儲值金結算」的 load_accounts()
   - 匯出方式：沿用日排程「專員個資」的後台匯出網址
3. Google 表單（需 OAuth 含 forms.body 權限，且專案已啟用 Google Forms API）
   - 參與統計表單：出席題目的日期改成執行畫面設定的春酒日期與時段
   - 各區投票表單：姓名與獎項題目的選項換成該地區名冊姓名

表單回覆要連到哪個試算表，Forms API 無法設定，仍需在表單「回覆」頁手動連結。
"""

from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

SPRING_ROOT_FOLDER_ID = "1q5IeRNtI4a6tUwWCglmzPHj3Ff7hBCxq"  # 尾牙春酒專用區
YEAR_FOLDER_NAME = "{Y}春酒"
ROSTER_FILE_NAME = "{Y}春酒專員名冊"
ATTEND_FORM_NAME = "{Y}春酒參與統計表單"
VOTE_FORM_NAME = "{V}年度投票（{a}）"          # V = 投票年度（春酒前一年）
RESPONSE_FILE_NAME = "{Y}春酒參與統計表單 (回覆)"
ROSTER_AREAS = ["台北", "台中", "桃園", "新竹"]
ROSTER_COLUMNS = ["姓名", "電話", "到職日期", "狀態"]   # 後台匯出的 A/B/G/N 欄
ROSTER_COLUMN_INDEX = [0, 1, 6, 13]                    # 標題對不上時的備援欄位位置
ACTIVE_STATUS = "正常"
TEST_ACCOUNT_PREFIX = "檸檬人"                          # 後台測試帳號，不放進投票選項
SHEET_MIME = "application/vnd.google-apps.spreadsheet"

# 回覆試算表新建時清掉的去年資料（表單回覆、得獎人）；計票與統計公式保留
RESPONSE_CLEAR_RANGES = [f"'春酒獎項投票-{a}'!A2:H" for a in ROSTER_AREAS] + [
    "'春酒參與統計'!A2:G",
    "'統計'!B2:F9",
]

FORMS_SCOPE = "https://www.googleapis.com/auth/forms.body"
ATTEND_DATE = re.compile(r"(方便於)\s*\d{1,2}/\d{1,2}\s*[(（][^)）]*[)）][^，,？?]*")
VOTE_NAME_KEYWORDS = ("姓名", "得獎")                 # 問題2 姓名、問題3～6「應由誰得獎」
WEEKDAYS = "一二三四五六日"


# ---------- 專員名冊 ----------
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
    """篩出狀態「正常」的專員，取 姓名／電話／到職日期／狀態；電話加 ' 保留開頭的 0。"""
    if all(col in df.columns for col in ROSTER_COLUMNS):
        picked = df[ROSTER_COLUMNS]
    else:
        picked = df.iloc[:, ROSTER_COLUMN_INDEX]
    rows = []
    for name, phone, onboard, status in picked.astype(str).values.tolist():
        if status.strip() != ACTIVE_STATUS:
            continue
        phone = phone.strip()
        rows.append([name.strip(), f"'{phone}" if phone else "", onboard.strip(), status.strip()])
    return rows


def ensure_roster_file(rollover, folder_id: str, name: str) -> tuple[Dict[str, Any], bool]:
    existing = [] if folder_id.startswith("(dry-run") else [
        f for f in rollover.children(folder_id) if f["mimeType"] == SHEET_MIME and f["name"] == name
    ]
    if existing:
        rollover.log(f"  檔案已存在，沿用：{name}")
        return existing[0], False
    rollover.log(f"  建立檔案：{name}")
    if rollover.dry_run:
        return {"id": f"(dry-run:{name})", "name": name}, True
    created = rollover.drive.files().create(
        body={"name": name, "mimeType": SHEET_MIME, "parents": [folder_id]},
        fields="id,name", supportsAllDrives=True,
    ).execute()
    return created, True


def write_area_sheet(rollover, file_id: str, area: str, rows: List[List[str]]) -> None:
    sheets = rollover.sheets.spreadsheets()
    meta = sheets.get(spreadsheetId=file_id, fields="sheets.properties").execute()
    props = [s["properties"] for s in meta.get("sheets", [])]
    titles = {p["title"] for p in props}
    if area not in titles:
        # 新建檔案的預設「工作表1」直接改名給第一個地區用
        blank = next((p for p in props if p["title"] not in ROSTER_AREAS), None) if len(props) == 1 else None
        if blank:
            req = {"updateSheetProperties": {"properties": {"sheetId": blank["sheetId"], "title": area},
                                             "fields": "title"}}
        else:
            req = {"addSheet": {"properties": {"title": area}}}
        sheets.batchUpdate(spreadsheetId=file_id, body={"requests": [req]}).execute()
    sheets.values().clear(spreadsheetId=file_id, range=f"'{area}'!A:D").execute()
    sheets.values().update(
        spreadsheetId=file_id, range=f"'{area}'!A1", valueInputOption="USER_ENTERED",
        body={"values": [ROSTER_COLUMNS] + rows},
    ).execute()


def run_staff_roster(rollover, folder: Dict[str, Any], areas: List[str]
                     ) -> tuple[List[Dict[str, str]], Dict[str, List[str]]]:
    """寫入「{Y}春酒專員名冊」；回傳 (records, {地區: 正常專員姓名})。"""
    from tools.scheduled_monthly.stored_value_settlement import load_accounts

    file, created = ensure_roster_file(rollover, folder["id"], ROSTER_FILE_NAME.format(Y=rollover.new_year))
    accounts = load_accounts()
    names: Dict[str, List[str]] = {}
    failed = []
    for a in areas:
        try:
            rows = active_rows(export_staff_frame(a, accounts))
        except Exception as exc:  # 一區失敗不影響其他地區
            rollover.log(f"  ✗ {a} 專員個資匯出失敗：{exc}")
            failed.append(a)
            continue
        rollover.log(f"  {a}：狀態「{ACTIVE_STATUS}」{len(rows)} 人 → {file['name']}!{a}")
        names[a] = [r[0] for r in rows]
        if not rollover.dry_run:
            write_area_sheet(rollover, file["id"], a, rows)

    records = [{"kind": "檔案", "old_name": "專員個資（" + "、".join(areas) + "）", "old_id": "",
                "new_name": file["name"], "new_id": file["id"], "created": created}]
    for a in failed:
        records.append({"kind": "檔案", "old_name": f"專員個資-{a}", "old_id": "",
                        "new_name": "找不到來源", "new_id": ""})
    return records, names


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


def clear_responses(rollover, file_id: str) -> None:
    titles = rollover._sheet_titles(file_id)
    ranges = [r for r in RESPONSE_CLEAR_RANGES if r.split("!")[0].strip("'") in titles]
    rollover.log(f"  清除去年回覆：{'、'.join(ranges)}")
    if ranges:
        rollover.sheets.spreadsheets().values().batchClear(
            spreadsheetId=file_id, body={"ranges": ranges}).execute()


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


def vote_choice_updates(form: Dict[str, Any], names: List[str]) -> List[Dict[str, Any]]:
    """姓名、得獎題目（選擇題）的選項換成 names；保留「其他」選項。"""
    options = [{"value": n} for n in dict.fromkeys(names) if n and not n.startswith(TEST_ACCOUNT_PREFIX)]
    requests_: List[Dict[str, Any]] = []
    for index, item in enumerate(form.get("items", [])):
        choice = item.get("questionItem", {}).get("question", {}).get("choiceQuestion")
        if not choice or not any(k in item.get("title", "") for k in VOTE_NAME_KEYWORDS):
            continue
        choice["options"] = options + [o for o in choice.get("options", []) if o.get("isOther")]
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


def update_vote_form(rollover, forms, form_id: str, form_name: str, names: List[str]) -> None:
    form = forms.forms().get(formId=form_id).execute()
    reqs = vote_choice_updates(form, names)
    count = len(reqs[0]["updateItem"]["item"]["questionItem"]["question"]["choiceQuestion"]["options"]) if reqs else 0
    rollover.log(f"  {form_name}：{len(reqs)} 題選項換成名冊 {count} 人")
    if reqs and not rollover.dry_run:
        forms.forms().batchUpdate(formId=form_id, body={"requests": reqs}).execute()


# ---------- 主流程 ----------
def run_spring_area(rollover, area: str = "全區") -> List[Dict[str, str]]:
    """生成／更新「{Y}春酒」專區；area 為「全區」時處理台北、台中、桃園、新竹。"""
    areas = ROSTER_AREAS if area in ("", "全區") else [a for a in ROSTER_AREAS if a == area]
    if not areas:
        rollover.log(f"  春酒專區沒有「{area}」地區，略過")
        return []
    y, py = rollover.new_year, rollover.prev_year
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
    if response.get("created") and not rollover.dry_run:
        clear_responses(rollover, response["new_id"])
    records += [attend, *votes.values(), response]

    roster_records, names = run_staff_roster(rollover, folder, areas)
    records += roster_records

    def ready(rec: Dict[str, str]) -> bool:  # 預覽模式下新複製的表單還不存在，無法讀取
        return rec["new_name"] != "找不到來源" and not rec["new_id"].startswith("(dry-run")

    event = getattr(rollover, "spring_event", None) or {}
    if not event.get("date"):
        rollover.log("  未設定春酒日期，參與統計表單日期未更新")
    targets = [a for a, v in votes.items() if ready(v) and names.get(a)]
    if (event.get("date") and ready(attend)) or targets:
        try:
            forms = get_forms_service()
            if event.get("date") and ready(attend):
                update_attendance_form(rollover, forms, attend["new_id"], event["date"],
                                       event.get("period") or "中午")
            for a in targets:
                update_vote_form(rollover, forms, votes[a]["new_id"], votes[a]["new_name"], names[a])
        except Exception as exc:
            rollover.log(f"  ✗ 更新 Google 表單失敗（確認已啟用 Forms API、OAuth 含 forms.body）：{exc}")
    rollover.log("  ⚠ 表單回覆連到「" + RESPONSE_FILE_NAME.format(Y=y) + "」需在各表單「回覆」頁手動設定")
    return records
