"""排程預約條件：暫時性的分階段預約（例如年節大掃除期間）。

在客服排程系統執行「建立VIP排程工作表」或「全跑」時，畫面勾選「套用預約條件」才會套用；
期間跟著畫面的匯出起迄日。確認檔案、工作表與各欄位也在畫面設定，存在主控試算表
「排程預約條件」工作表（每個地區一列），下次自動帶入。

套用時，匯出範圍內的未成單排程列，在 AG 右側寫六欄：
  回覆確認：確認表「確認欄」的值（例如 V 欄 TRUE／FALSE）；找不到客人寫「查無」
  確認備註：確認表「備註欄」的值（例如 W 欄）
  改加標記：「排程排除欄」（例如 A 欄）或「確認表排除欄」含的排除關鍵字（例如 改、加）
  已確認：「已確認欄」（例如排程 B 欄＝日曆備註）含「已確認關鍵字」
  人時：人數 × 時數（人數取 A 欄「N人」，沒寫時 2 人）
  預約條件：依序判斷 改加 → 人時不足（未滿最低人時）→ 已確認 → 可約（回覆確認在確認值內）→ 未回覆
狀態欄不改；orders-system 批次成單依畫面勾選的「本次預約對象」決定約哪些階段。
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

RULE_SHEET_NAME = "排程預約條件"
RULE_HEADERS = [
    "地區", "確認檔案ID", "確認工作表", "姓名欄", "電話欄", "地址欄", "確認欄", "確認值", "備註欄",
    "排程排除欄", "排除關鍵字", "確認表排除欄", "已確認欄", "已確認關鍵字", "最低人時", "更新時間",
]
# 畫面第一次開啟時的預設值；之後一律以工作表內容為準。
DEFAULT_SETTINGS = {
    "確認檔案ID": "", "確認工作表": "", "姓名欄": "C", "電話欄": "D", "地址欄": "F",
    "確認欄": "V", "確認值": "TRUE", "備註欄": "W", "排程排除欄": "A", "排除關鍵字": "改,加",
    "確認表排除欄": "", "已確認欄": "B", "已確認關鍵字": "已確認", "最低人時": "6",
}
AREA_DEFAULTS = {
    "台北": {"確認檔案ID": "1bNcJuFuP--jdpNo2zJKOpvuq-5rSHW3LgGE8HEepf44", "確認工作表": "2026大掃除"},
}
# 工作表標題的滑鼠提示。
HEADER_NOTES = {
    "地區": "一個地區一列；由客服排程系統執行畫面儲存。是否套用與期間在執行畫面決定（期間＝匯出起迄日）。",
    "確認檔案ID": "確認表所在的試算表 ID，或直接貼整個網址。",
    "確認工作表": "確認表的工作表名稱，例如 2026大掃除。",
    "姓名欄": "確認表中客人姓名所在欄位字母，用來對應排程的客人。",
    "電話欄": "確認表中電話所在欄位字母。",
    "地址欄": "確認表中地址所在欄位字母；同電話不同地址會分開對應。",
    "確認欄": "確認表中客人「已回覆確認」所在欄位字母，例如 V。值會帶入排程「回覆確認」欄。",
    "確認值": "確認欄的值是這些才算已回覆確認（預約條件＝可約），可填多個，用逗號分隔，例如 TRUE。",
    "備註欄": "確認表中備註所在欄位字母，例如 W。值會帶入排程「確認備註」欄，只供參考。",
    "排程排除欄": "排程工作表中要檢查排除關鍵字的欄位字母，例如 A（日曆標題第一個《》內容）。",
    "排除關鍵字": "含這些字，預約條件＝改加，可填多個，用逗號分隔，例如 改,加。找到的字會帶入排程「改加標記」欄。",
    "確認表排除欄": "選填。確認表中也要檢查排除關鍵字的欄位字母；空白＝不檢查。",
    "已確認欄": "排程工作表中判斷「原本每月確認、改成已確認」的欄位字母，例如 B（日曆標題 <> 備註）。空白＝不檢查。",
    "已確認關鍵字": "已確認欄含這些字，預約條件＝已確認，可填多個，用逗號分隔，例如 已確認。",
    "最低人時": "人數 × 時數未滿此數，預約條件＝人時不足（例如 6：1人4小時、2人2小時先不約）。"
               "人數取 A 欄「2人」等寫法，沒寫時為 2 人；時數為開始到結束時間。空白＝不檢查。",
    "更新時間": "最後一次從執行畫面儲存的時間（台北時間）。",
}
# 預約條件的階段，依判斷順序；批次成單畫面勾選本次要約哪幾個階段。
STAGE_CHANGE, STAGE_SHORT, STAGE_CONFIRMED, STAGE_READY, STAGE_NO_REPLY = "改加", "人時不足", "已確認", "可約", "未回覆"
STAGES = (STAGE_CHANGE, STAGE_SHORT, STAGE_CONFIRMED, STAGE_READY, STAGE_NO_REPLY)
NOT_FOUND = "查無"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _words(value: Any) -> list[str]:
    return [w.strip() for w in re.split(r"[,，、;；\s]+", _text(value)) if w.strip()]


def column_index(letter: Any) -> int | None:
    """A→0、V→21、AA→26；空白回傳 None。"""
    letter = _text(letter).upper()
    if not letter:
        return None
    if not re.fullmatch(r"[A-Z]{1,3}", letter):
        raise ValueError(f"欄位格式錯誤：{letter}（請填欄位英文字母，例如 V）")
    index = 0
    for char in letter:
        index = index * 26 + ord(char) - 64
    return index - 1


def _date(value: Any) -> str:
    text = _text(value).replace(".", "/")
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _spreadsheet_id(value: Any) -> str:
    text = _text(value)
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else text


def _name(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value))


def _phone(value: Any) -> str:
    digits = re.sub(r"\D", "", _text(value))
    return "0" + digits if len(digits) == 9 and digits.startswith("9") else digits


def _address(value: Any) -> str:
    text = re.sub(r"\s+", "", _text(value))
    return re.sub(r"[－–—]", "-", text).replace("臺", "台")


def _number(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"最低人時格式錯誤：{text}（請填數字，例如 6）")


def _clock(value: Any) -> float | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", _text(value))
    return int(match.group(1)) + int(match.group(2)) / 60 if match else None


def person_hours(values: list[Any]) -> float | None:
    """人數 × 時數：人數取 A 欄「N人」（沒寫時 2 人，與匯出計價相同），時數取開始到結束時間。"""
    start, end = _clock(values[6] if len(values) > 6 else ""), _clock(values[7] if len(values) > 7 else "")
    if start is None or end is None or end <= start:
        return None
    people = re.search(r"(\d+)\s*人", _text(values[0]))
    return (int(people.group(1)) if people else 2) * (end - start)


def _hours_text(value: float | None) -> str:
    return "" if value is None else (str(int(value)) if value == int(value) else f"{value:g}")


def parse_rule(record: dict[str, Any], start: Any = "", end: Any = "") -> dict[str, Any]:
    """設定列＋本次匯出起迄日 → 規則；起迄日空白代表該側不限制。"""
    rule = {header: _text(record.get(header)) for header in RULE_HEADERS}
    start, end = _date(start), _date(end)
    if start and end and start > end:
        raise ValueError("預約條件期間錯誤：起日晚於迄日")
    if not rule["確認檔案ID"] or not rule["確認工作表"] or not rule["確認欄"]:
        raise ValueError(f"「{RULE_SHEET_NAME}」{rule['地區']} 需填確認檔案ID、確認工作表與確認欄")
    return {
        "area": rule["地區"],
        "start": start,
        "end": end,
        "spreadsheet_id": _spreadsheet_id(rule["確認檔案ID"]),
        "sheet": rule["確認工作表"],
        "name_col": column_index(rule["姓名欄"] or "C"),
        "phone_col": column_index(rule["電話欄"] or "D"),
        "address_col": column_index(rule["地址欄"] or "F"),
        "confirm_col": column_index(rule["確認欄"]),
        "confirm_letter": rule["確認欄"].upper(),
        "confirm_values": [w.upper() for w in _words(rule["確認值"] or "TRUE")],
        "note_col": column_index(rule["備註欄"]),
        "schedule_exclude_col": column_index(rule["排程排除欄"]),
        "schedule_exclude_letter": rule["排程排除欄"].upper(),
        "exclude_words": _words(rule["排除關鍵字"]),
        "source_exclude_col": column_index(rule["確認表排除欄"]),
        "source_exclude_letter": rule["確認表排除欄"].upper(),
        # 舊版規則表沒有這兩欄時，預設看排程 B 欄（日曆備註）是否含「已確認」。
        "confirmed_col": column_index(rule["已確認欄"] if "已確認欄" in record else "B"),
        "confirmed_words": _words(rule["已確認關鍵字"] if "已確認關鍵字" in record else "已確認"),
        # 舊版規則表沒有最低人時時預設 6；明確留空＝不檢查。
        "min_person_hours": _number(rule["最低人時"] if "最低人時" in record else "6"),
    }


def build_gate(rule: dict[str, Any], source_rows: list[list[Any]]) -> Callable[[list[Any]], Any]:
    """回傳 gate(排程列) →（回覆確認, 確認備註, 改加標記, 已確認, 人時, 預約條件）；None 代表期間外。"""
    def cell(row, index):
        return _text(row[index]) if index is not None and index < len(row) else ""

    entries = []
    for row in source_rows:
        name, phone, address = (cell(row, rule["name_col"]), cell(row, rule["phone_col"]),
                                cell(row, rule["address_col"]))
        if not (name or phone):
            continue
        entries.append({
            "keys": {"name_phone_address": (_name(name), _phone(phone), _address(address)),
                     "phone_address": (_phone(phone), _address(address)),
                     "name_phone": (_name(name), _phone(phone)),
                     "name_address": (_name(name), _address(address))},
            "confirmed": cell(row, rule["confirm_col"]).upper() in rule["confirm_values"],
            "confirm_raw": cell(row, rule["confirm_col"]) or "空白",
            "note": cell(row, rule["note_col"]),
            "excluded": cell(row, rule["source_exclude_col"]),
        })
    indexes = defaultdict(lambda: defaultdict(list))
    for entry in entries:
        for kind, key in entry["keys"].items():
            if all(key):
                indexes[kind][key].append(entry)

    def find(values):
        name, phone, address = _name(values[2]), _phone(values[3]), _address(values[4])
        for kind, key in (("name_phone_address", (name, phone, address)), ("phone_address", (phone, address)),
                          ("name_phone", (name, phone)), ("name_address", (name, address))):
            if all(key) and indexes[kind].get(key):
                return indexes[kind][key]
        return []

    def marks(text):
        return [word for word in rule["exclude_words"] if word in text]

    def gate(values):
        service_date = _date(values[5])
        if not service_date or not ((rule["start"] or service_date) <= service_date <= (rule["end"] or service_date)):
            return None
        matched = find(values)
        confirm = "、".join(dict.fromkeys(e["confirm_raw"] for e in matched)) if matched else NOT_FOUND
        found = marks(cell(values, rule["schedule_exclude_col"]))
        found += [m for e in matched for m in marks(e["excluded"])]
        mark = "、".join(dict.fromkeys(found))
        note = "；".join(dict.fromkeys(e["note"] for e in matched if e["note"]))
        confirmed_text = cell(values, rule["confirmed_col"])
        confirmed_note = "、".join(w for w in rule["confirmed_words"] if w in confirmed_text)
        replied = bool(matched) and all(e["confirmed"] for e in matched)
        hours = person_hours(values)
        short = rule["min_person_hours"] is not None and hours is not None and hours < rule["min_person_hours"] - 1e-9
        stage = (STAGE_CHANGE if mark else STAGE_SHORT if short else STAGE_CONFIRMED if confirmed_note
                 else STAGE_READY if replied else STAGE_NO_REPLY)
        return confirm, note, mark, confirmed_note, _hours_text(hours), stage

    return gate


def is_hold(stage: str) -> bool:
    """可約以外的階段，預設批次成單不約。"""
    return _text(stage) in STAGES and _text(stage) != STAGE_READY


def combine_gates(gates: list[Callable[[list[Any]], Any]]) -> Callable[[list[Any]], Any] | None:
    if not gates:
        return None

    def gate(values):
        results = [result for result in (g(values) for g in gates) if result]
        if not results:
            return None
        join = lambda items: "、".join(dict.fromkeys(item for item in items if item))
        # Several rules: the strictest stage wins; 可約 only when every rule says so.
        stage = next(s for s in (STAGE_CHANGE, STAGE_SHORT, STAGE_CONFIRMED, STAGE_NO_REPLY, STAGE_READY)
                     if any(r[-1] == s for r in results))
        return tuple(join(r[i] for r in results) for i in range(5)) + (stage,)

    return gate


def _master_spreadsheet(gc):
    from tools.common.config_loader import get_master_spreadsheet_id
    master_id = os.environ.get("TOOLS_APP_LOG_SPREADSHEET_ID", "").strip() or get_master_spreadsheet_id()
    if not master_id:
        raise EnvironmentError("TOOLS_APP_LOG_SPREADSHEET_ID 未設定")
    return gc.open_by_key(master_id)


def _write_header_notes(ss, sheet) -> None:
    """標題列加上滑鼠提示；失敗不影響排程更新。"""
    try:
        ss.batch_update({"requests": [{"updateCells": {
            "range": {"sheetId": sheet.id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": len(RULE_HEADERS)},
            "rows": [{"values": [{"note": HEADER_NOTES.get(name, "")} for name in RULE_HEADERS]}],
            "fields": "note",
        }}]})
    except Exception:
        pass


def _settings_records(gc, worksheet_not_found=Exception):
    ss = _master_spreadsheet(gc)
    try:
        sheet = ss.worksheet(RULE_SHEET_NAME)
    except worksheet_not_found:
        return ss, None, []
    values = sheet.get_all_values()
    header = [_text(v) for v in (values[0] if values else [])]
    records = [{name: (row[i] if i < len(row) else "") for i, name in enumerate(header) if name}
               for row in values[1:] if any(_text(v) for v in row)]
    return ss, sheet, records


def load_settings(gc, area_name: str, worksheet_not_found=Exception) -> dict[str, str]:
    """該地區的設定；沒有時回傳預設值（台北預設 2026大掃除）。舊版規則表欄位一樣讀得到。"""
    _, _, records = _settings_records(gc, worksheet_not_found)
    record = next((r for r in records if _text(r.get("地區")) == area_name), None)
    settings = {**DEFAULT_SETTINGS, **AREA_DEFAULTS.get(area_name, {})}
    if record:
        # 舊版規則表沒有的欄位（例如已確認欄）沿用預設值。
        settings.update({k: _text(v) for k, v in record.items() if k in DEFAULT_SETTINGS})
    return {"地區": area_name, **settings}


def save_settings(gc, area_name: str, settings: dict[str, Any], worksheet_not_found=Exception) -> None:
    """寫回該地區一列；其他地區保留；工作表一律整理成目前欄位。"""
    parse_rule({**settings, "地區": area_name})  # validate before saving
    ss, sheet, records = _settings_records(gc, worksheet_not_found)
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    row = {**{k: _text(settings.get(k)) for k in DEFAULT_SETTINGS}, "地區": area_name, "更新時間": now}
    others = [r for r in records if _text(r.get("地區")) and _text(r.get("地區")) != area_name]
    rows = [[_text(r.get(h)) for h in RULE_HEADERS] for r in others] + [[row[h] for h in RULE_HEADERS]]
    if sheet is None:
        sheet = ss.add_worksheet(title=RULE_SHEET_NAME, rows=50, cols=len(RULE_HEADERS))
    else:
        sheet.clear()
        if getattr(sheet, "col_count", len(RULE_HEADERS)) < len(RULE_HEADERS):
            sheet.resize(cols=len(RULE_HEADERS))
    sheet.update(values=[RULE_HEADERS] + rows, range_name="A1", value_input_option="RAW")
    _write_header_notes(ss, sheet)


def _open_source(gc, spreadsheet_id: str):
    try:
        return gc.open_by_key(spreadsheet_id)
    except Exception:
        # 內勤表單屬於檸檬 Google 帳號，服務帳號沒權限時改用同帳號 OAuth。
        from services.google_auth import get_gspread_client
        return get_gspread_client().open_by_key(spreadsheet_id)


def load_area_gate(gc, area_name: str, start: Any = "", end: Any = "",
                   worksheet_not_found=Exception) -> tuple[Callable | None, list[str]]:
    """依該地區設定與本次匯出起迄日建立判斷；確認表讀取失敗時直接報錯，避免誤約。"""
    rule = parse_rule(load_settings(gc, area_name, worksheet_not_found), start, end)
    try:
        source = _open_source(gc, rule["spreadsheet_id"]).worksheet(rule["sheet"])
        rows = source.get_all_values()[1:]
    except Exception as exc:
        raise RuntimeError(f"無法讀取預約確認表 {rule['sheet']}（{rule['spreadsheet_id']}）：{exc}；"
                           "未更新排程，避免預約未確認的客人") from exc
    return combine_gates([build_gate(rule, rows)]), [f"{rule['sheet']} {rule['start']}～{rule['end']}"]
