"""財報富邦更新篩選規則引擎：規則放在主控試算表的「財報篩選規則」分頁，
不寫死在程式裡——之後要新增/調整規則，直接改那張表，不用改程式碼。

財報欄位（1-based，沿用既有配置）：
  D=4 摘要　E=5 收入　F=6 支出　H=8 附註/對象
  I=9 收支類別（下拉選單）　J=10 人工標記　K=11 記帳日期　L=12 分類標籤
  Q=17 更新時間　R=18 更新內容——只要規則有改到某一列（不管是更新原列
  還是插入的新列），就會自動打上這兩欄，記錄「什麼時候、改了什麼」；沒被
  任何規則改到的列不會動 Q／R。

規則分頁欄位（A起，第一列是標題，從第二列開始是規則，由上到下依序套用）：
  A 啟用（TRUE/FALSE）
  B 規則名稱（純備註用）
  C 條件1欄位（例如 D／H／L）
  D 條件1比對（包含／等於／小於／小於等於／大於／大於等於／介於，也接受
           </></=/>= 這種寫法）
  E 條件1值（逗號分隔＝多值 OR，比對 C 欄那個財報欄位；比對是小於/大於類時，
           值可以填「10日」這種格式，比對 C 欄那個日期欄位的「日」，也可以
           直接填日期；比對是「介於」時，值填「15日-20日」這種區間，
           表示日期是 15 號到 20 號之間（含頭尾））
  F 條件2欄位（留空表示沒有條件2；有值時跟條件1是 OR，例如 H含新訓 或 L含新訓）
  G 條件2比對
  H 條件2值
  I 設定I欄（符合就把財報 I 欄設成這個值）
  J L欄月份位移（相對月份來源，例如 -2 表示往前兩個月；留空表示不改 L 欄）
  K L欄後綴文字（L 欄標記＝該月份 "YYYY.MM"＋"-"＋這個後綴；留空表示不改 L 欄）
  L 客訴金額搬移（TRUE＝把 F 欄金額搬到 E 欄且轉負數，E=-F，並把 F 欄清成 0，
           避免同一筆金額 E、F 兩欄都算到）
  M 列處理（更新原列／插入新列——插入新列時原列不動，複製一份新列在下面，
           只改新列的 I／L／E，其餘欄位照抄原列）
  N 金額分攤份數（例如 2／6；同一筆命中的分攤規則必須全部填相同份數，程式
           會把 E 欄原始金額平均分成指定份數。不能整除時會把餘數分配到後面的
           列，所有分攤列加總仍等於原始金額。舊規則填 E 的寫法仍相容）
  O 條件關係（且／或，留空當「或」——例如 H含新訓 或 L含新訓；要「且」就填「且」，
           只有同時有條件1和條件2時才有意義）
  P L欄年月來源（K＝依 K 欄日期；J＝讀取 J 欄開頭的 YYYYMM，亦接受
           YYYY.MM／YYYY-MM／YYYY/MM；留空時若 J 有 YYYYMM 就優先用 J，否則用 K）
  Q 設定E欄固定金額（例如 -2406；留空表示保留原金額。可用於新增固定費用列）

新增欄位一律加在既有欄位最後面（不要插在中間），避免手動插入分頁欄位時
把既有欄位的資料位置擠掉、讓已經對好的規則跑錯。分頁已存在時，程式會自動把
少的欄位補在最後面，不用手動調整。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.finance_management.execution_log import log_execution
from tools.finance_management.statement_registry import resolve_statement_location

RULES_SHEET_NAME = "財報篩選規則"

RULES_HEADER = [
    "啟用", "規則名稱", "條件1欄位", "條件1比對", "條件1值",
    "條件2欄位", "條件2比對", "條件2值",
    "設定I欄", "L欄月份位移", "L欄後綴", "客訴金額搬移(E=-F)", "列處理", "金額分攤份數",
    "條件關係(且/或)", "L欄年月來源", "設定E欄固定金額",
]

# 預設規則（第一次建立分頁時寫入，之後只從分頁讀取，不再看這份清單）。
# N 欄舊版使用金額欄位代號（例如 E）；解析器保留相容，避免既有規則失效。
DEFAULT_RULES: list[list[object]] = [
    [True, "LC匯款收入", "L", "包含", "LC", "", "", "", "匯款收入", "", "", False, "更新原列", "", ""],
    [True, "客訴退費", "L", "包含", "客訴", "", "", "", "清潔-客訴退費(損壞、細膩度等)", "", "", True, "更新原列", "", ""],
    [True, "藍新科技代收代付", "H", "包含", "藍新科技", "", "", "", "代收代付-收入", 0, "藍新科技", False, "更新原列", "", ""],
    [True, "新訓工具包押金", "H", "包含", "新訓", "L", "包含", "新訓", "工具包押金", 0, "工具包押金", False, "更新原列", "", "或"],
    [True, "電信費", "D", "等於", "電信費", "", "", "", "電話費", 0, "電話費", False, "更新原列", "", ""],
    [True, "利息收入", "D", "等於", "定存息,利息", "", "", "", "利息收入", 0, "利息收入", False, "更新原列", "", ""],
    [True, "內勤勞保費", "D", "等於", "勞保費", "", "", "", "內勤勞保費", -2, "內勤勞保費", False, "更新原列", "", ""],
    [True, "內勤退休金", "D", "等於", "勞退", "", "", "", "內勤退休金", -3, "內勤退休金", False, "更新原列", "", ""],
    [True, "水費(前2月)", "D", "等於", "市水水費", "", "", "", "水費", -2, "水費", False, "更新原列", "E", ""],
    [True, "水費(前1月)", "D", "等於", "市水水費", "", "", "", "水費", -1, "水費", False, "插入新列", "E", ""],
    [True, "電費(前2月)", "D", "等於", "電費", "", "", "", "電費", -2, "電費", False, "更新原列", "E", ""],
    [True, "電費(前1月)", "D", "等於", "電費", "", "", "", "電費", -1, "電費", False, "插入新列", "E", ""],
]

INSERT_ACTION = "插入新列"
TW_TZ = ZoneInfo("Asia/Taipei")
COL_Q, COL_R = 17, 18

_COMPARATOR_ALIASES = {
    "<": "小於", "＜": "小於",
    "<=": "小於等於", "≦": "小於等於", "=<": "小於等於",
    ">": "大於", "＞": "大於",
    ">=": "大於等於", "≧": "大於等於", "=>": "大於等於",
    "=": "等於", "==": "等於",
}


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


def _to_number_or_none(value: object) -> float | None:
    text = ("" if value is None else str(value)).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_bool(value: object) -> bool:
    text = str(value or "").strip().upper()
    return text in ("TRUE", "1", "YES", "Y")


def _day_number(text: str) -> int | None:
    text = text.strip()
    if text.endswith("日"):
        text = text[:-1]
    try:
        return int(text)
    except ValueError:
        return None


def _to_int_or_none(value: object) -> int | None:
    text = ("" if value is None else str(value)).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _parse_date(value: object):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    text = str(value or "").strip().split(" ", 1)[0]
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _statement_row_date(row: list[object]):
    """B 欄若只有 MM/DD，改由 K 或 C 欄取得含年份的完整日期。"""
    for col in (2, 11, 3):
        parsed = _parse_date(_cell(row, col))
        if parsed is not None:
            return parsed
    return None


def _month_from_j(row: list[object]) -> tuple[int, int] | None:
    marker = str(_cell_by_letter(row, "J") or "").strip()
    match = re.match(r"^(\d{4})[./-]?(0[1-9]|1[0-2])", marker)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _split_integer_amount(total: float, parts: int) -> list[float]:
    """把整數金額分成指定份數，總和保持不變；非整數則平均分配到各份。"""
    if parts < 2:
        raise ValueError("金額分攤份數至少要 2")
    if float(total).is_integer():
        integer_total = int(total)
        sign = -1 if integer_total < 0 else 1
        absolute_total = abs(integer_total)
        base, remainder = divmod(absolute_total, parts)
        shares = [base] * parts
        for i in range(parts - remainder, parts):
            shares[i] += 1
        return [float(sign * value) for value in shares]
    share = total / parts
    shares = [share] * parts
    shares[-1] += total - sum(shares)
    return shares


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

        raw_split = str(_cell(raw, 14) or "").strip().upper()
        numeric_parts = _to_int_or_none(raw_split)
        self.split_parts = numeric_parts if numeric_parts is not None and numeric_parts >= 2 else None
        # 舊版 N 欄填 E／F 等欄位代號仍可繼續使用。
        self.split_column = "" if self.split_parts else "".join(ch for ch in raw_split if ch.isalpha())

        self.relation = str(_cell(raw, 15) or "").strip() or "或"
        # 保留空白，讓 l_value() 可以自動判斷：J 有 YYYYMM 就用 J，否則用 K。
        self.month_source = str(_cell(raw, 16) or "").strip().upper()
        self.set_e = _to_number_or_none(_cell(raw, 17))

    def _one_condition_matches(self, row: list[object], col: str, cmp_: str, values: list[str]) -> bool:
        if not col or not values:
            return False
        text = str(_cell_by_letter(row, col) or "").strip()
        op = _COMPARATOR_ALIASES.get(cmp_.strip(), cmp_.strip())
        if op == "等於":
            return text in values
        if op in ("小於", "小於等於", "大於", "大於等於"):
            row_date = _parse_date(text)
            if row_date is None:
                return False
            for raw_value in values:
                value = raw_value.strip()
                if value.endswith("日"):
                    try:
                        day = int(value[:-1])
                    except ValueError:
                        continue
                    lhs, rhs = row_date.day, day
                else:
                    threshold = _parse_date(value)
                    if threshold is None:
                        continue
                    lhs, rhs = row_date, threshold
                if op == "小於" and lhs < rhs:
                    return True
                if op == "小於等於" and lhs <= rhs:
                    return True
                if op == "大於" and lhs > rhs:
                    return True
                if op == "大於等於" and lhs >= rhs:
                    return True
            return False
        if op == "介於":
            row_date = _parse_date(text)
            if row_date is None:
                return False
            for raw_value in values:
                parts = re.split(r"[~\-]", raw_value.strip())
                if len(parts) != 2:
                    continue
                low, high = (_day_number(p) for p in parts)
                if low is None or high is None:
                    continue
                if low <= row_date.day <= high:
                    return True
            return False
        return any(v in text for v in values)  # 包含（含「等於/包含」等自訂文字時也採包含）

    def matches(self, row: list[object]) -> bool:
        cond1 = self._one_condition_matches(row, self.col1, self.cmp1, self.values1)
        if not self.col2:
            return cond1
        cond2 = self._one_condition_matches(row, self.col2, self.cmp2, self.values2)
        if self.relation == "且":
            return cond1 and cond2
        return cond1 or cond2

    def l_value(self, row: list[object]) -> str | None:
        if self.month_offset is None or not self.suffix:
            return None

        base_year = base_month = None
        if self.month_source == "J":
            parsed_month = _month_from_j(row)
            if parsed_month:
                base_year, base_month = parsed_month
        elif self.month_source == "K":
            base_date = _parse_date(_cell_by_letter(row, "K"))
            if base_date is not None:
                base_year, base_month = base_date.year, base_date.month
        else:
            # 新增／人工科目列通常只有 J 欄會寫 202609台北營業稅 這類月份標記；
            # 銀行匯入資料則以 K 欄記帳日期為主。空白時自動兼容兩種資料來源。
            parsed_month = _month_from_j(row)
            if parsed_month:
                base_year, base_month = parsed_month
            else:
                base_date = _parse_date(_cell_by_letter(row, "K"))
                if base_date is not None:
                    base_year, base_month = base_date.year, base_date.month

        if base_year is None or base_month is None:
            return None
        month_index = base_year * 12 + (base_month - 1) + self.month_offset
        if month_index < 0:
            return None
        year, month = divmod(month_index, 12)
        return f"{year}.{month + 1:02d}-{self.suffix}"


def _ensure_rules_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if RULES_SHEET_NAME in titles:
        _migrate_rules_sheet(service, spreadsheet_id)
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


def _migrate_rules_sheet(service, spreadsheet_id: str) -> None:
    """分頁已存在但欄位比目前 RULES_HEADER 少時，只往最後面補欄位，不移動既有資料。"""
    last_col = _column_letter(len(RULES_HEADER))
    header_res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{RULES_SHEET_NAME}'!A1:{last_col}1"
    ).execute()
    existing_header = (header_res.get("values") or [[]])[0]

    # N 欄只是標題語意由「對半欄位」改為「分攤份數」，位置不變，可安全更新標題；
    # 既有 N=E 的資料仍由 Rule 保留相容，不改內容。
    if len(existing_header) >= 14 and str(existing_header[13]).strip() != RULES_HEADER[13]:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{RULES_SHEET_NAME}'!N1",
            valueInputOption="RAW",
            body={"values": [[RULES_HEADER[13]]]},
        ).execute()

    if len(existing_header) >= len(RULES_HEADER):
        return

    missing_start = len(existing_header) + 1
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{RULES_SHEET_NAME}'!{_column_letter(missing_start)}1:{last_col}1",
        valueInputOption="RAW",
        body={"values": [RULES_HEADER[missing_start - 1:]]},
    ).execute()

    rows_res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{RULES_SHEET_NAME}'!A2:B"
    ).execute()
    default_by_name = {str(r[1]).strip(): r for r in DEFAULT_RULES}
    fill_updates = []
    for row_idx, row in enumerate(rows_res.get("values") or [], start=2):
        name = str(row[1]).strip() if len(row) > 1 else ""
        default_row = default_by_name.get(name)
        if not default_row:
            continue
        missing_values = default_row[missing_start - 1:]
        if any(str(v) != "" for v in missing_values):
            fill_updates.append({
                "range": f"'{RULES_SHEET_NAME}'!{_column_letter(missing_start)}{row_idx}:{last_col}{row_idx}",
                "values": [missing_values],
            })
    if fill_updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": fill_updates},
        ).execute()


def load_rules() -> list[Rule]:
    """讀「財報篩選規則」分頁；分頁不存在就先用預設規則建立一份。"""
    service = get_sheets_service()
    master_id = get_master_spreadsheet_id()
    _ensure_rules_sheet(service, master_id)
    last_col = _column_letter(len(RULES_HEADER))
    res = service.spreadsheets().values().get(
        spreadsheetId=master_id, range=f"'{RULES_SHEET_NAME}'!A2:{last_col}"
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


def apply_rules(area: str, start_date=None, end_date=None) -> dict[str, int]:
    """套用所有啟用規則到指定地區的財報「富邦更新」分頁。

    目標位置一律透過「財報設定」解析：例如執行地區＝台北，就只讀寫台北財報
    的富邦更新；台中則讀寫台中財報，不共用或寫死任何試算表 ID/GID。
    """
    date_range = f"{start_date or ''}~{end_date or ''}" if (start_date or end_date) else "全部"
    log_execution("財報富邦更新套用規則", area, "開始", f"日期區間：{date_range}")
    try:
        result = _apply_rules_impl(area, start_date, end_date)
    except Exception as exc:
        log_execution("財報富邦更新套用規則", area, "失敗", str(exc))
        raise
    log_execution(
        "財報富邦更新套用規則", area, "完成",
        f"更新 {result['updated_rows']} 格，插入 {result['inserted_rows']} 列",
    )
    return result


def _apply_rules_impl(area: str, start_date=None, end_date=None) -> dict[str, int]:
    rules = [r for r in load_rules() if r.enabled]
    spreadsheet_id, title = resolve_statement_location(area, "富邦更新")
    values = _read_values(spreadsheet_id, title)
    if len(values) < 2:
        return {"updated_rows": 0, "inserted_rows": 0}

    service = get_sheets_service()
    value_updates: list[dict[str, object]] = []
    pending_inserts: list[tuple[int, list[list[object]]]] = []

    for row_idx, row in enumerate(values[1:], start=2):
        if str(_cell(row, COL_Q)).strip():
            continue

        if start_date or end_date:
            row_date = _statement_row_date(row)
            if row_date is None:
                continue
            if start_date and row_date < start_date:
                continue
            if end_date and row_date > end_date:
                continue

        in_place_rule = None
        insert_rules = []
        for rule in rules:
            if not rule.matches(row):
                continue
            if rule.action == INSERT_ACTION:
                insert_rules.append(rule)
            else:
                in_place_rule = rule

        # 分攤規則：新版 N 欄填份數（2／6），固定分攤 E 欄；舊版 N=E 仍依欄位代號處理。
        split_targets: list[tuple[str, int | None]] = []
        split_column = None
        expected_parts = None

        if in_place_rule is not None and (in_place_rule.split_parts or in_place_rule.split_column):
            expected_parts = in_place_rule.split_parts
            split_column = "E" if in_place_rule.split_parts else in_place_rule.split_column
            split_targets.append(("in_place", None))

        for i, rule in enumerate(insert_rules):
            if not (rule.split_parts or rule.split_column):
                continue
            rule_column = "E" if rule.split_parts else rule.split_column
            if split_column is not None and rule_column != split_column:
                continue
            if expected_parts is not None and rule.split_parts not in (None, expected_parts):
                raise RuntimeError(
                    f"第 {row_idx} 列命中的金額分攤規則份數不一致：{expected_parts} 與 {rule.split_parts}"
                )
            expected_parts = expected_parts or rule.split_parts
            split_column = split_column or rule_column
            split_targets.append(("insert", i))

        split_values: dict[tuple[str, int | None], float] = {}
        if split_column and len(split_targets) >= 2:
            parts = expected_parts or len(split_targets)
            if expected_parts is not None and len(split_targets) != expected_parts:
                matched_names = []
                if in_place_rule is not None and (in_place_rule.split_parts or in_place_rule.split_column):
                    matched_names.append(in_place_rule.name)
                matched_names.extend(
                    rule.name for rule in insert_rules if rule.split_parts or rule.split_column
                )
                raise RuntimeError(
                    f"第 {row_idx} 列金額分攤設定為 {expected_parts} 份，但只命中 {len(split_targets)} 條規則："
                    + "、".join(matched_names)
                )
            total = _to_number(_cell(row, _letter_to_index(split_column)))
            shares = _split_integer_amount(total, parts)
            for target, share in zip(split_targets, shares):
                split_values[target] = share

        if in_place_rule is not None:
            changes: list[str] = []
            if in_place_rule.set_i:
                value_updates.append(
                    {"range": f"'{title}'!I{row_idx}", "values": [[in_place_rule.set_i]]}
                )
                changes.append(f"I={in_place_rule.set_i}")
            l_value = in_place_rule.l_value(row)
            if l_value is not None:
                value_updates.append(
                    {"range": f"'{title}'!L{row_idx}", "values": [[l_value]]}
                )
                changes.append(f"L={l_value}")
            if in_place_rule.move_complaint_amount:
                f_value = _to_number(_cell(row, 6))
                value_updates.append(
                    {"range": f"'{title}'!E{row_idx}", "values": [[-f_value]]}
                )
                value_updates.append(
                    {"range": f"'{title}'!F{row_idx}", "values": [[0]]}
                )
                changes.append(f"E={-f_value}, F=0")
            if ("in_place", None) in split_values:
                share = split_values[("in_place", None)]
                value_updates.append({
                    "range": f"'{title}'!{split_column}{row_idx}",
                    "values": [[share]],
                })
                changes.append(f"{split_column}={share}")
            if in_place_rule.set_e is not None:
                value_updates.append(
                    {"range": f"'{title}'!E{row_idx}", "values": [[in_place_rule.set_e]]}
                )
                changes.append(f"E={in_place_rule.set_e}")
            if changes:
                now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
                value_updates.append({"range": f"'{title}'!Q{row_idx}", "values": [[now_text]]})
                value_updates.append(
                    {"range": f"'{title}'!R{row_idx}", "values": [["；".join(changes)]]}
                )

        if insert_rules:
            new_rows = []
            for i, rule in enumerate(insert_rules):
                new_row = list(row)
                changes = []
                if rule.set_i:
                    while len(new_row) < 9:
                        new_row.append("")
                    new_row[8] = rule.set_i
                    changes.append(f"I={rule.set_i}")
                l_value = rule.l_value(row)
                if l_value is not None:
                    while len(new_row) < 12:
                        new_row.append("")
                    new_row[11] = l_value
                    changes.append(f"L={l_value}")
                if rule.move_complaint_amount:
                    while len(new_row) < 6:
                        new_row.append("")
                    new_row[4] = -_to_number(_cell(row, 6))
                    new_row[5] = 0
                    changes.append(f"E={new_row[4]}, F=0")
                if ("insert", i) in split_values:
                    col_index = _letter_to_index(split_column)
                    while len(new_row) < col_index:
                        new_row.append("")
                    new_row[col_index - 1] = split_values[("insert", i)]
                    changes.append(f"{split_column}={split_values[('insert', i)]}")
                if rule.set_e is not None:
                    while len(new_row) < 5:
                        new_row.append("")
                    new_row[4] = rule.set_e
                    changes.append(f"E={rule.set_e}")
                if changes:
                    while len(new_row) < COL_R:
                        new_row.append("")
                    new_row[COL_Q - 1] = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
                    new_row[COL_R - 1] = "；".join(changes) + "（新增列）"
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
            after_index = row_idx
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
