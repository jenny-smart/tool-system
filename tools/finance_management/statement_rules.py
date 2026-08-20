"""財報富邦更新篩選規則引擎：規則放在主控試算表的「財報篩選規則」分頁，
不寫死在程式裡——之後要新增/調整規則，直接改那張表，不用改程式碼。

財報欄位（1-based，沿用既有配置）：
  D=4 摘要　E=5 收入　F=6 支出　H=8 附註/對象
  I=9 收支類別（下拉選單）　K=11 記帳日期（L欄月份標記依這欄算）　L=12 分類標籤

規則分頁欄位（A起，第一列是標題，從第二列開始是規則，由上到下依序套用）：
  A 啟用（TRUE/FALSE）
  B 規則名稱（純備註用）
  C 條件1欄位（例如 D／H／L）
  D 條件1比對（包含／等於）
  E 條件1值（逗號分隔＝多值 OR，比對 C 欄那個財報欄位）
  F 條件2欄位（留空表示沒有條件2；有值時跟條件1是 OR，例如 H含新訓 或 L含新訓）
  G 條件2比對
  H 條件2值
  I 設定I欄（符合就把財報 I 欄設成這個值）
  J L欄月份位移（相對 K 欄的月份，例如 -2 表示往前兩個月；留空表示不改 L 欄）
  K L欄後綴文字（L 欄標記＝該月份 "YYYY.MM"＋"-"＋這個後綴；留空表示不改 L 欄）
  L 客訴金額搬移（TRUE＝把 F 欄金額搬到 E 欄且轉負數，E=-F）
  M 列處理（更新原列／插入新列——插入新列時原列不動，複製一份新列在下面，
           只改新列的 I／L／E，其餘欄位照抄原列）
"""

from __future__ import annotations

from datetime import datetime

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.statement_registry import resolve_statement_location

RULES_SHEET_NAME = "財報篩選規則"

RULES_HEADER = [
    "啟用", "規則名稱", "條件1欄位", "條件1比對", "條件1值",
    "條件2欄位", "條件2比對", "條件2值",
    "設定I欄", "L欄月份位移", "L欄後綴", "客訴金額搬移(E=-F)", "列處理",
]

# 預設規則（第一次建立分頁時寫入，之後只從分頁讀取，不再看這份清單）。
DEFAULT_RULES: list[list[object]] = [
    [True, "LC匯款收入", "L", "包含", "LC", "", "", "", "匯款收入", "", "", False, "更新原列"],
    [True, "客訴退費", "L", "包含", "客訴", "", "", "", "清潔-客訴退費(損壞、細膩度等)", "", "", True, "更新原列"],
    [True, "藍新科技代收代付", "H", "包含", "藍新科技", "", "", "", "代收代付-收入", 0, "藍新科技", False, "更新原列"],
    [True, "新訓工具包押金", "H", "包含", "新訓", "L", "包含", "新訓", "工具包押金", 0, "工具包押金", False, "更新原列"],
    [True, "電信費", "D", "等於", "電信費", "", "", "", "電話費", 0, "電話費", False, "更新原列"],
    [True, "利息收入", "D", "等於", "定存息,利息", "", "", "", "利息收入", 0, "利息收入", False, "更新原列"],
    [True, "內勤勞保費", "D", "等於", "勞保費", "", "", "", "內勤勞保費", -2, "內勤勞保費", False, "更新原列"],
    [True, "內勤退休金", "D", "等於", "勞退", "", "", "", "內勤退休金", -3, "內勤退休金", False, "更新原列"],
    [True, "水費(前2月)", "D", "等於", "市水水費", "", "", "", "水費", -2, "水費", False, "插入新列"],
    [True, "水費(前1月)", "D", "等於", "市水水費", "", "", "", "水費", -1, "水費", False, "插入新列"],
    [True, "電費(前2月)", "D", "等於", "電費", "", "", "", "電費", -2, "電費", False, "插入新列"],
    [True, "電費(前1月)", "D", "等於", "電費", "", "", "", "電費", -1, "電費", False, "插入新列"],
]

INSERT_ACTION = "插入新列"


def _letter_to_index(letter: str) -> int:
    letter = letter.strip().upper()
    index = 0
    for ch in letter:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _cell(row: list[object], col: int) -> object:
    idx = col - 1
    return row[idx] if idx < len(row) else ""


def _cell_by_letter(row: list[object], letter: str) -> object:
    if not letter:
        return ""
    return _cell(row, _letter_to_index(letter))


def _to_number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _to_bool(value: object) -> bool:
    text = str(value or "").strip().upper()
    return text in ("TRUE", "1", "YES", "Y")


def _to_int_or_none(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_date(value: object):
    text = str(value or "").strip().split(" ", 1)[0]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class Rule:
    def __init__(self, raw: list[object]):
        self.enabled = _to_bool(_cell(raw, 1))
        self.name = str(_cell(raw, 2) or "")
        self.col1 = str(_cell(raw, 3) or "").strip().upper()
        self.cmp1 = str(_cell(raw, 4) or "").strip()
        self.values1 = [v.strip() for v in str(_cell(raw, 5) or "").split(",") if v.strip()]
        self.col2 = str(_cell(raw, 6) or "").strip().upper()
        self.cmp2 = str(_cell(raw, 7) or "").strip()
        self.values2 = [v.strip() for v in str(_cell(raw, 8) or "").split(",") if v.strip()]
        self.set_i = str(_cell(raw, 9) or "").strip()
        self.month_offset = _to_int_or_none(_cell(raw, 10))
        self.suffix = str(_cell(raw, 11) or "").strip()
        self.move_complaint_amount = _to_bool(_cell(raw, 12))
        self.action = str(_cell(raw, 13) or "").strip() or "更新原列"

    def _one_condition_matches(self, row: list[object], col: str, cmp_: str, values: list[str]) -> bool:
        if not col or not values:
            return False
        text = str(_cell_by_letter(row, col) or "").strip()
        if cmp_ == "等於":
            return text in values
        return any(v in text for v in values)

    def matches(self, row: list[object]) -> bool:
        if self._one_condition_matches(row, self.col1, self.cmp1, self.values1):
            return True
        if self.col2:
            return self._one_condition_matches(row, self.col2, self.cmp2, self.values2)
        return False

    def l_value(self, row: list[object]) -> str | None:
        if self.month_offset is None or not self.suffix:
            return None
        k_date = _parse_date(_cell_by_letter(row, "K"))
        if k_date is None:
            return None
        month_index = k_date.year * 12 + (k_date.month - 1) + self.month_offset
        year, month = divmod(month_index, 12)
        return f"{year}.{month + 1:02d}-{self.suffix}"


def _ensure_rules_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if RULES_SHEET_NAME in titles:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": RULES_SHEET_NAME}}}]},
    ).execute()
    rows = [RULES_HEADER, *DEFAULT_RULES]
    last_col = _column_letter(len(RULES_HEADER))
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{RULES_SHEET_NAME}'!A1:{last_col}{len(rows)}",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()


def load_rules() -> list[Rule]:
    """讀「財報篩選規則」分頁；分頁不存在就先用預設規則建立一份。"""
    service = get_sheets_service()
    master_id = get_master_spreadsheet_id()
    _ensure_rules_sheet(service, master_id)
    res = service.spreadsheets().values().get(
        spreadsheetId=master_id, range=f"'{RULES_SHEET_NAME}'!A2:M"
    ).execute()
    return [Rule(row) for row in res.get("values", []) if any(str(c).strip() for c in row)]


def _read_values(spreadsheet_id: str, title: str) -> list[list[object]]:
    service = get_sheets_service()
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A:Z",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return res.get("values", [])


def _sheet_id_for_title(service, spreadsheet_id: str, title: str) -> int:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties"
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == title:
            return props["sheetId"]
    raise RuntimeError(f"試算表 {spreadsheet_id} 找不到分頁「{title}」")


def apply_rules(area: str) -> dict[str, int]:
    """套用「財報篩選規則」分頁裡的所有啟用規則到指定地區的財報富邦更新分頁。"""
    rules = [r for r in load_rules() if r.enabled]
    spreadsheet_id, title = resolve_statement_location(area, "富邦更新")
    values = _read_values(spreadsheet_id, title)
    if len(values) < 2:
        return {"updated_rows": 0, "inserted_rows": 0}

    service = get_sheets_service()
    value_updates: list[dict[str, object]] = []
    # (row_idx, [new_row_values, ...])，row_idx 由大到小處理避免插入後索引跑掉
    pending_inserts: list[tuple[int, list[list[object]]]] = []

    for row_idx, row in enumerate(values[1:], start=2):
        in_place_rule = None
        insert_rules = []
        for rule in rules:
            if not rule.matches(row):
                continue
            if rule.action == INSERT_ACTION:
                insert_rules.append(rule)
            else:
                in_place_rule = rule  # 同一列多條「更新原列」規則命中時，以最後一條為準

        if in_place_rule is not None:
            if in_place_rule.set_i:
                value_updates.append(
                    {"range": f"'{title}'!I{row_idx}", "values": [[in_place_rule.set_i]]}
                )
            l_value = in_place_rule.l_value(row)
            if l_value is not None:
                value_updates.append(
                    {"range": f"'{title}'!L{row_idx}", "values": [[l_value]]}
                )
            if in_place_rule.move_complaint_amount:
                f_value = _to_number(_cell(row, 6))
                value_updates.append(
                    {"range": f"'{title}'!E{row_idx}", "values": [[-f_value]]}
                )

        if insert_rules:
            new_rows = []
            for rule in insert_rules:
                new_row = list(row)
                if rule.set_i:
                    while len(new_row) < 9:
                        new_row.append("")
                    new_row[8] = rule.set_i
                l_value = rule.l_value(row)
                if l_value is not None:
                    while len(new_row) < 12:
                        new_row.append("")
                    new_row[11] = l_value
                if rule.move_complaint_amount:
                    while len(new_row) < 5:
                        new_row.append("")
                    new_row[4] = -_to_number(_cell(row, 6))
                new_rows.append(new_row)
            pending_inserts.append((row_idx, new_rows))

    if value_updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": value_updates},
        ).execute()

    inserted_rows = 0
    if pending_inserts:
        sheet_id = _sheet_id_for_title(service, spreadsheet_id, title)
        for row_idx, new_rows in sorted(pending_inserts, key=lambda item: item[0], reverse=True):
            after_index = row_idx  # 0-based 索引正好等於「原列（1-based row_idx）之後」
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": after_index,
                            "endIndex": after_index + len(new_rows),
                        },
                        "inheritFromBefore": True,
                    }
                }]},
            ).execute()
            last_col = _column_letter(max(len(r) for r in new_rows))
            start_row = after_index + 1
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{title}'!A{start_row}:{last_col}{start_row + len(new_rows) - 1}",
                valueInputOption="USER_ENTERED",
                body={"values": new_rows},
            ).execute()
            inserted_rows += len(new_rows)

    return {"updated_rows": len(value_updates), "inserted_rows": inserted_rows}
