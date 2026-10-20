"""排程預約條件：暫時性的「確認後才可預約」規則（例如年節大掃除期間）。

規則全部放在主控試算表「排程預約條件」工作表，一列一條規則，可隨時啟用／停用、
改檔案、改工作表、改欄位或改排除字，不需改程式：

  啟用 | 地區 | 適用起日 | 適用迄日 | 確認檔案ID | 確認工作表 | 姓名欄 | 電話欄 | 地址欄 |
  確認欄 | 確認值 | 備註欄 | 排程排除欄 | 排除關鍵字 | 確認表排除欄 | 說明

- 只影響服務日期落在「適用起日～適用迄日」的排程列；期間外與平時完全不受影響。
- 「確認欄」的值不在「確認值」內（例如 V 欄不是 TRUE）→ 先不約。
- 「排程排除欄」（排程工作表，例如 A 欄＝日曆標題《》內容）或「確認表排除欄」
  含任一「排除關鍵字」（例如 改、加）→ 先不約，留待最後再成單。
- 先不約的列：狀態維持不變（仍是「未安排」），只在排程「預約條件」欄寫「先不約：原因」；
  orders-system 批次成單看到「先不約」開頭就跳過。條件解除後重新更新排程，
  該欄改為「可預約」即可成單。已有訂單編號的列不寫。
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

RULE_SHEET_NAME = "排程預約條件"
RULE_HEADERS = [
    "啟用", "地區", "適用起日", "適用迄日", "確認檔案ID", "確認工作表", "姓名欄", "電話欄", "地址欄",
    "確認欄", "確認值", "備註欄", "排程排除欄", "排除關鍵字", "確認表排除欄", "說明",
]
# 首次建立工作表時的範例列；之後一律以工作表內容為準。
EXAMPLE_RULES = [
    ["TRUE", "台北", "2026-12-15", "2027-02-04", "1bNcJuFuP--jdpNo2zJKOpvuq-5rSHW3LgGE8HEepf44",
     "2026大掃除", "C", "D", "F", "V", "TRUE", "W", "A", "改,加", "",
     "年節大掃除：V 欄 TRUE 才預約；排程 A 欄含改／加先不約"],
]
HOLD_PREFIX = "先不約"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _enabled(value: Any) -> bool:
    return _text(value).upper() in ("TRUE", "1", "是", "啟用", "V", "Y", "YES")


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


def parse_rule(record: dict[str, Any]) -> dict[str, Any]:
    rule = {header: _text(record.get(header)) for header in RULE_HEADERS}
    start, end = _date(rule["適用起日"]), _date(rule["適用迄日"])
    if not start or not end or start > end:
        raise ValueError(f"「{RULE_SHEET_NAME}」{rule['地區']} 適用起迄日格式錯誤（YYYY-MM-DD）")
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
    }


def build_gate(rule: dict[str, Any], source_rows: list[list[Any]]) -> Callable[[list[Any]], str]:
    """回傳 gate(排程列) → 原因；空字串代表期間外、不受此規則影響。"""
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
        if not service_date or not (rule["start"] <= service_date <= rule["end"]):
            return ""
        matched = find(values)
        notes = "；".join(dict.fromkeys(e["note"] for e in matched if e["note"]))
        suffix = f"（{rule['sheet']} 備註：{notes}）" if notes else ""
        schedule_marks = marks(cell(values, rule["schedule_exclude_col"]))
        if schedule_marks:
            return (f"先不約：排程 {rule['schedule_exclude_letter']} 欄標記「{'、'.join(schedule_marks)}」，"
                    f"留待最後成單{suffix}")
        if not matched:
            return f"先不約：{rule['sheet']} 找不到此客戶，尚未確認"
        source_marks = [m for e in matched for m in marks(e["excluded"])]
        if source_marks:
            return (f"先不約：{rule['sheet']} {rule['source_exclude_letter']} 欄標記"
                    f"「{'、'.join(dict.fromkeys(source_marks))}」，留待最後成單{suffix}")
        if not all(e["confirmed"] for e in matched):
            raw = "、".join(dict.fromkeys(e["confirm_raw"] for e in matched))
            return f"先不約：{rule['sheet']} {rule['confirm_letter']} 欄為 {raw}，尚未確認{suffix}"
        return f"可預約：{rule['sheet']} {rule['confirm_letter']} 欄已確認{suffix}"

    return gate


def is_hold(reason: str) -> bool:
    return _text(reason).startswith(HOLD_PREFIX)


def combine_gates(gates: list[Callable[[list[Any]], str]]) -> Callable[[list[Any]], str] | None:
    if not gates:
        return None

    def gate(values):
        reasons = [reason for reason in (g(values) for g in gates) if reason]
        held = [reason for reason in reasons if is_hold(reason)]
        return "；".join(held or reasons)

    return gate


def _master_spreadsheet(gc):
    from tools.common.config_loader import get_master_spreadsheet_id
    master_id = os.environ.get("TOOLS_APP_LOG_SPREADSHEET_ID", "").strip() or get_master_spreadsheet_id()
    if not master_id:
        raise EnvironmentError("TOOLS_APP_LOG_SPREADSHEET_ID 未設定")
    return gc.open_by_key(master_id)


def load_rules(gc, area_name: str, worksheet_not_found=Exception) -> list[dict[str, Any]]:
    ss = _master_spreadsheet(gc)
    try:
        sheet = ss.worksheet(RULE_SHEET_NAME)
    except worksheet_not_found:
        sheet = ss.add_worksheet(title=RULE_SHEET_NAME, rows=50, cols=len(RULE_HEADERS))
        sheet.update(values=[RULE_HEADERS] + EXAMPLE_RULES, range_name="A1", value_input_option="USER_ENTERED")
    values = sheet.get_all_values()
    header = [_text(v) for v in (values[0] if values else [])]
    rules = []
    for row in values[1:]:
        record = {name: (row[i] if i < len(row) else "") for i, name in enumerate(header)}
        if not _enabled(record.get("啟用")):
            continue
        if _text(record.get("地區")) not in (area_name, "全區", "共用"):
            continue
        rules.append(parse_rule(record))
    return rules


def _open_source(gc, spreadsheet_id: str):
    try:
        return gc.open_by_key(spreadsheet_id)
    except Exception:
        # 內勤表單屬於檸檬 Google 帳號，服務帳號沒權限時改用同帳號 OAuth。
        from services.google_auth import get_gspread_client
        return get_gspread_client().open_by_key(spreadsheet_id)


def load_area_gate(gc, area_name: str, worksheet_not_found=Exception) -> tuple[Callable | None, list[str]]:
    """讀取該地區所有啟用規則；確認表讀取失敗時直接報錯，避免誤約未確認的客人。"""
    gates, descriptions = [], []
    for rule in load_rules(gc, area_name, worksheet_not_found):
        try:
            source = _open_source(gc, rule["spreadsheet_id"]).worksheet(rule["sheet"])
            rows = source.get_all_values()[1:]
        except Exception as exc:
            raise RuntimeError(f"無法讀取預約確認表 {rule['sheet']}（{rule['spreadsheet_id']}）：{exc}；"
                               "未更新排程，避免預約未確認的客人") from exc
        gates.append(build_gate(rule, rows))
        descriptions.append(f"{rule['sheet']} {rule['start']}～{rule['end']}")
    return combine_gates(gates), descriptions
