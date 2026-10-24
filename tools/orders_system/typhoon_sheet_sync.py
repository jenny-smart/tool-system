# -*- coding: utf-8 -*-
"""颱風假訂單移轉：讀客服的 Google 表單，查後台目前日期／時段，寫回「新日期／新時段」欄。

流程：
1. 依設定（試算表 ID、工作表名稱、訂單編號／原日期／原時段／新日期／新時段欄、起始列）
   讀取表單。
2. 逐筆查後台目前的服務日期／時段，和表單的新日期／新時段比對（compare_sheet_orders）。
3. 客服確認後才寫入（apply_sheet_updates）：寫入前重讀這些格子，若表單在比對後被
   手動改過就跳過，不覆蓋。只寫新日期／新時段兩欄，其他欄位不動。

設定存在主控試算表「颱風移轉設定」分頁，每個後台帳號一列。
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import orders
import quick_order as qo
import typhoon_transfer as tt


SETTINGS_SHEET_TITLE = "颱風移轉設定"
SETTING_FIELDS = [
    ("spreadsheet_id", "試算表ID"),
    ("worksheet", "工作表名稱"),
    ("order_col", "訂單編號欄"),
    ("orig_date_col", "原日期欄"),
    ("orig_period_col", "原時段欄"),
    ("new_date_col", "新日期欄"),
    ("new_period_col", "新時段欄"),
    ("start_row", "起始列"),
]
SETTINGS_HEADERS = ["後台帳號"] + [label for _key, label in SETTING_FIELDS] + ["更新時間"]
DEFAULT_SETTINGS = {
    "spreadsheet_id": "", "worksheet": "", "order_col": "A",
    "orig_date_col": "F", "orig_period_col": "G",
    "new_date_col": "I", "new_period_col": "J", "start_row": "2",
}


def _now_text():
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")


def spreadsheet_id_from_text(text):
    """接受試算表網址或 ID。"""
    text = str(text or "").strip()
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else text


def column_index(letter):
    """欄位英文字母 → 0 起算的索引（A=0、AA=26）。"""
    letter = str(letter or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", letter):
        raise ValueError(f"欄位請輸入英文字母（例如 A、I、AA），收到：{letter or '空白'}")
    index = 0
    for ch in letter:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def validate_settings(settings):
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    settings["spreadsheet_id"] = spreadsheet_id_from_text(settings["spreadsheet_id"])
    if not settings["spreadsheet_id"]:
        raise ValueError("請輸入 Google 試算表 ID 或網址")
    if not str(settings["worksheet"]).strip():
        raise ValueError("請輸入工作表名稱")
    for key in ("order_col", "new_date_col", "new_period_col"):
        column_index(settings[key])
    for key in ("orig_date_col", "orig_period_col"):
        if str(settings[key]).strip():
            column_index(settings[key])
    if len({str(settings[k]).strip().upper() for k in ("order_col", "new_date_col", "new_period_col")}) != 3:
        raise ValueError("訂單編號欄、新日期欄、新時段欄不可是同一欄")
    try:
        start_row = int(settings["start_row"])
    except Exception:
        raise ValueError("起始列請輸入數字")
    if start_row < 1:
        raise ValueError("起始列需大於 0")
    settings["start_row"] = str(start_row)
    return settings


# ---------------------------------------------------------
# 設定暫存（主控試算表「颱風移轉設定」分頁）
# ---------------------------------------------------------

def _settings_worksheet(client=None):
    client = client or orders.build_gsheet_client()
    spreadsheet = client.open_by_key(orders.GOOGLE_SHEET_ID)
    try:
        worksheet = spreadsheet.worksheet(SETTINGS_SHEET_TITLE)
    except orders.gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SETTINGS_SHEET_TITLE, rows=100, cols=len(SETTINGS_HEADERS))
        worksheet.update(range_name="A1", values=[SETTINGS_HEADERS])
    return worksheet


def load_settings(account, client=None):
    """讀這個後台帳號上次存的設定；沒有就回傳預設值。"""
    values = _settings_worksheet(client).get_all_values()
    for row in values[1:]:
        if row and row[0].strip() == str(account or "").strip():
            padded = row + [""] * (len(SETTINGS_HEADERS) - len(row))
            saved = {key: padded[i + 1] for i, (key, _label) in enumerate(SETTING_FIELDS)}
            return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if v != ""}}
    return dict(DEFAULT_SETTINGS)


def save_settings(account, settings, client=None):
    settings = validate_settings(settings)
    worksheet = _settings_worksheet(client)
    values = worksheet.get_all_values()
    row_values = [str(account or "").strip()] + [str(settings[key]) for key, _label in SETTING_FIELDS] + [_now_text()]
    for row_no, row in enumerate(values[1:], 2):
        if row and row[0].strip() == row_values[0]:
            worksheet.update(range_name=f"A{row_no}", values=[row_values])
            return settings
    worksheet.append_row(row_values, value_input_option="RAW")
    return settings


# ---------------------------------------------------------
# 比對與寫回
# ---------------------------------------------------------

def _open_target(settings, client=None):
    client = client or orders.build_gsheet_client()
    return client.open_by_key(settings["spreadsheet_id"]).worksheet(settings["worksheet"])


def _cell(row, index):
    return str(row[index]).strip() if index is not None and index < len(row) else ""


def read_sheet_orders(values, settings):
    """從工作表全部值取出要比對的列。"""
    start_row = int(settings["start_row"])
    idx = {key: column_index(settings[key]) if str(settings.get(key, "")).strip() else None
           for key in ("order_col", "orig_date_col", "orig_period_col", "new_date_col", "new_period_col")}
    items, seen = [], set()
    for row_no in range(start_row, len(values) + 1):
        row = values[row_no - 1]
        order_no = _cell(row, idx["order_col"])
        if not re.fullmatch(orders.ORDER_NO_REGEX, order_no) or order_no in seen:
            continue
        seen.add(order_no)
        items.append({
            "row": row_no,
            "order_no": order_no,
            "orig_date": _cell(row, idx["orig_date_col"]),
            "orig_period": _cell(row, idx["orig_period_col"]),
            "sheet_date": _cell(row, idx["new_date_col"]),
            "sheet_period": _cell(row, idx["new_period_col"]),
        })
    return items


def _same_date(left, right):
    return tt._normalize_date_text(left) == tt._normalize_date_text(right)


def compare_sheet_orders(env_name, backend_email, backend_password, settings, client=None, progress=None):
    """讀表單並逐筆查後台目前日期／時段；不寫入。"""
    settings = validate_settings(settings)
    worksheet = _open_target(settings, client)
    items = read_sheet_orders(worksheet.get_all_values(), settings)
    session, _base_url = tt._login(env_name, backend_email, backend_password)
    rows = []
    for index, item in enumerate(items, 1):
        if progress:
            progress(index, len(items), item)
        row = {**item, "now_date": "", "now_period": "", "payment_status": "", "status": "", "name": ""}
        try:
            block = qo._fetch_purchase_block_for_order_no(session, item["order_no"])
            lines = block.get("lines", [])
            joined = "\n".join(lines)
            now_date, now_period = qo._parse_service_date_time_loose(joined)
            row["now_date"], row["now_period"] = now_date, tt._compact_period(now_period)
            status_match = re.search(r"付款狀態[：:]\s*([^\n]+)", joined)
            row["payment_status"] = status_match.group(1).strip() if status_match else ""
            for i, line in enumerate(lines):
                if re.fullmatch(r"09\d{8}", str(line).strip()):
                    row["name"] = str(lines[i - 1]).strip() if i else ""
                    break
            if not row["now_date"] or not row["now_period"]:
                row["status"] = "查詢失敗：讀不到目前日期／時段"
            elif _same_date(row["now_date"], item["sheet_date"]) and row["now_period"] == tt._compact_period(item["sheet_period"]):
                row["status"] = "未變動"
            else:
                row["status"] = "待更新"
        except Exception as exc:
            row["status"] = f"查詢失敗：{exc}"
        rows.append(row)
    return rows


def apply_sheet_updates(settings, rows, client=None):
    """把「待更新」列的目前日期／時段寫進新日期／新時段欄。

    寫入前重讀這些格子；和比對時讀到的值不同（比對後被手動改過）就跳過。
    回傳每列結果。
    """
    settings = validate_settings(settings)
    worksheet = _open_target(settings, client)
    values = worksheet.get_all_values()
    date_idx = column_index(settings["new_date_col"])
    period_idx = column_index(settings["new_period_col"])
    order_idx = column_index(settings["order_col"])
    date_col = settings["new_date_col"].strip().upper()
    period_col = settings["new_period_col"].strip().upper()
    updates, results = [], []
    for row in rows:
        if row.get("status") != "待更新":
            continue
        current = values[row["row"] - 1] if row["row"] - 1 < len(values) else []
        if _cell(current, order_idx) != row["order_no"]:
            results.append({**row, "status": "已跳過：該列訂單編號已變動"})
            continue
        if _cell(current, date_idx) != row["sheet_date"] or _cell(current, period_idx) != row["sheet_period"]:
            results.append({**row, "status": "已跳過：比對後表單已被修改"})
            continue
        updates.append({"range": f"{date_col}{row['row']}", "values": [[row["now_date"]]]})
        updates.append({"range": f"{period_col}{row['row']}", "values": [[row["now_period"]]]})
        results.append({**row, "status": "已更新"})
    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")
    return results
