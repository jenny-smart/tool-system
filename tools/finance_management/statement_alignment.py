"""財報「更新」分頁的維護：補公式、盤點未標記、把對應分頁 A–I 對齊到更新分頁。

三件事、依序執行（run_statement_maintenance）：
  1. 補公式：更新分頁有銀行資料、但公式欄（富邦 K:P／元大 L:Q）是空的列，補上公式
     （公式沿用 tools.bank_statement.sheet_filter 寫新明細時的同一套）。
  2. 套篩選規則：富邦更新沿用 statement_rules.apply_rules（規則在主控表「財報篩選規則」）。
     這一步、以及「代墊費用拆分」都會在更新分頁「插入新列」——插列會讓對應分頁的
     相對參照整段錯位，所以第 3 步一定要排在它們後面。
  3. 對齊對應分頁：對應分頁（台北/台中＝富邦公司戶／元大公司戶；其他區＝富邦／元大）
     的 A–I 一列一列參照更新分頁。目標是「更新分頁每一列有內容的資料都剛好對到一列、
     依序、不重複不漏」，等同手動把 A–I 往下拉到跟更新分頁同樣的列數。
       - 對應分頁第一個參照到的更新列之前的列，視為刻意不放（例如前一年轉過來的列）。
       - 帳務日屬於前一年的列一律不放（例如 2025/12/31 李維芯那筆）。
       - 只從「第一個對不上的列」開始改寫；前面已經對齊的列完全不動。
       - 要改寫的範圍內若有人工輸入的值（非公式，例如發票號碼、手續費），
         會錯位到別筆交易，所以整張分頁停下來不寫，只回報需要人工處理。
盤點（dry_run=True）只讀不寫，回報每個步驟「會做什麼」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
AREAS = ("台北", "台中", "桃園", "新竹", "高雄")
UPDATE_SHEETS = ("富邦更新", "元大更新")

# 對應分頁名稱：台北／台中叫「公司戶」，其他區直接叫富邦／元大。找不到分頁就跳過。
MIRROR_SHEETS: Dict[str, Dict[str, str]] = {
    "台北": {"富邦更新": "富邦公司戶", "元大更新": "元大公司戶"},
    "台中": {"富邦更新": "富邦公司戶", "元大更新": "元大公司戶"},
}
DEFAULT_MIRROR_SHEETS = {"富邦更新": "富邦", "元大更新": "元大"}

# 註記「不標」的列不列入待確認（例如客戶轉錯又沖正的那幾筆）。
SKIP_MARK = "不標"


@dataclass(frozen=True)
class Layout:
    """更新分頁欄位（1-based）。"""
    category_col: int        # 科目（下拉選單）
    label_col: int           # 分類標籤
    formula_first_col: int   # 公式欄起點（富邦 K、元大 L）
    formula_count: int       # 公式欄數（6）
    note_col: int            # 人工標記／說明欄（寫「不標」可排除）


LAYOUTS = {
    "富邦更新": Layout(category_col=9, label_col=12, formula_first_col=11, formula_count=6, note_col=10),
    "元大更新": Layout(category_col=10, label_col=13, formula_first_col=12, formula_count=6, note_col=11),
}

MIRROR_LAST_COL = 9  # 對應分頁 A–I


def mirror_sheet_name(area: str, update_sheet: str) -> str:
    return MIRROR_SHEETS.get(area, DEFAULT_MIRROR_SHEETS)[update_sheet]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _cell(row: Sequence[object], col: int) -> str:
    idx = col - 1
    if idx < len(row) and row[idx] is not None:
        return str(row[idx]).strip()
    return ""


def is_formula(value: object) -> bool:
    return isinstance(value, str) and value.startswith("=")


def row_year(row: Sequence[object]) -> Optional[int]:
    """B／C 欄（帳務日、交易時間）的年份，取較早的那個：2025/12/31 晚上交易、
    2026/01/01 入帳的列算 2025 年（資料屬於它發生的年度）。手動列沒有日期回傳 None。"""
    years = []
    for col in (2, 3):
        match = re.match(r"\s*(\d{4})[/-]", _cell(row, col))
        if match:
            years.append(int(match.group(1)))
    return min(years) if years else None


def has_bank_data(row: Sequence[object]) -> bool:
    """銀行匯入的列：B 欄有日期。"""
    return row_year(row) is not None


def has_content(row: Sequence[object], layout: Layout) -> bool:
    """更新分頁「有內容」的列：B–I 任一格有值，或已經填了科目（手動新增的列可能沒有日期）。"""
    if any(_cell(row, col) for col in range(2, 10)):
        return True
    return bool(_cell(row, layout.category_col))


_EMPTY_LABEL = re.compile(r"^(\d{4}\.\d{2}[- ]?|-+,*|--,,|#N/A|#REF!|#VALUE!)?$")


def label_missing(value: str) -> bool:
    """L（元大 M）欄沒有標好：空白、#N/A、只有「2026.05-」、「--,,」這類。"""
    return bool(_EMPTY_LABEL.match(value.strip()))


# ---------------------------------------------------------------------------
# 1. 補公式
# ---------------------------------------------------------------------------

def plan_formula_backfill(values: List[List[object]], update_sheet: str) -> List[int]:
    """回傳需要補公式的列號：有銀行資料（B 有日期）但公式欄第一格是空的。"""
    layout = LAYOUTS[update_sheet]
    rows = []
    for row_number, row in enumerate(values[1:], start=2):
        if has_bank_data(row) and not _cell(row, layout.formula_first_col):
            rows.append(row_number)
    return rows


# ---------------------------------------------------------------------------
# 2. 盤點未標記
# ---------------------------------------------------------------------------

@dataclass
class UnlabeledRow:
    row: int
    date: str
    summary: str
    amount_out: str
    amount_in: str
    note: str
    category: str
    label: str

    def describe(self) -> str:
        amount = f"支出 {self.amount_out}" if self.amount_out else f"存入 {self.amount_in}"
        missing = []
        if not self.category:
            missing.append("科目")
        if label_missing(self.label):
            missing.append("標記")
        return (f"第 {self.row} 列 {self.date} {self.summary} {amount} "
                f"{self.note}｜缺{'、'.join(missing)}")


def find_unlabeled(values: List[List[object]], update_sheet: str, year: int,
                   from_row: int = 2) -> List[UnlabeledRow]:
    """from_row：對應分頁第一個用公式抓到的更新列。更前面的列（例如桃園、新竹年初那段）
    是直接在對應分頁手打科目，更新分頁本來就空著，不列入待確認。"""
    layout = LAYOUTS[update_sheet]
    if update_sheet == "富邦更新":
        out_col, in_col, note_col, summary_col = 5, 6, 8, 4
    else:
        out_col, in_col, note_col, summary_col = 6, 7, 9, 4
    result = []
    for row_number, row in enumerate(values[1:], start=2):
        if row_number < from_row:
            continue
        if not has_bank_data(row) or row_year(row) != year:
            continue
        if SKIP_MARK in _cell(row, layout.note_col):
            continue
        category = _cell(row, layout.category_col)
        label = _cell(row, layout.label_col)
        if category and not label_missing(label):
            continue
        if not _cell(row, out_col) and not _cell(row, in_col):
            continue  # 拆單的延續列（金額空白）跟著第一列，不另外列
        result.append(UnlabeledRow(
            row=row_number,
            date=_cell(row, 2)[:10],
            summary=_cell(row, summary_col),
            amount_out=_cell(row, out_col),
            amount_in=_cell(row, in_col),
            note=_cell(row, note_col).replace("\n", " ")[:20],
            category=category,
            label=label,
        ))
    return result


# ---------------------------------------------------------------------------
# 3. 對齊對應分頁
# ---------------------------------------------------------------------------

_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.$])"
    r"(?P<sheet>(?:'(?:[^']|'')+'|[A-Za-z0-9_一-鿿]+)!)?"
    r"(?P<col_abs>\$?)(?P<col>[A-Z]{1,3})(?P<row_abs>\$?)(?P<row>\d+)"
    r"(?![A-Za-z0-9_(])"
)


def _sheet_of(prefix: Optional[str]) -> Optional[str]:
    if not prefix:
        return None
    name = prefix[:-1]
    if name.startswith("'") and name.endswith("'"):
        name = name[1:-1].replace("''", "'")
    return name


def _split_quoted(formula: str) -> List[Tuple[bool, str]]:
    """把公式切成（是否在雙引號字串內, 片段），字串內的內容不當參照處理。"""
    parts: List[Tuple[bool, str]] = []
    for piece in re.split(r'("(?:[^"]|"")*")', formula):
        if piece:
            parts.append((piece.startswith('"') and piece.endswith('"') and len(piece) >= 2, piece))
    return parts


def referenced_update_row(formula: object, update_sheet: str, column: Optional[str] = None) -> Optional[int]:
    """公式裡參照到更新分頁（可指定欄）的列號；沒有就 None。"""
    if not is_formula(formula):
        return None
    for quoted, piece in _split_quoted(str(formula)):
        if quoted:
            continue
        for match in _REF_RE.finditer(piece):
            if _sheet_of(match.group("sheet")) != update_sheet:
                continue
            if column and match.group("col") != column:
                continue
            return int(match.group("row"))
    return None


def shift_formula(formula: str, update_sheet: str, *, row_delta: int, update_delta: int) -> str:
    """把範本列的公式搬到另一列：同分頁（及其他分頁）的相對列號位移 row_delta，
    參照更新分頁的相對列號位移 update_delta；有 $ 的絕對列號不動。"""
    def replace(match: "re.Match[str]") -> str:
        if match.group("row_abs"):
            return match.group(0)
        sheet = _sheet_of(match.group("sheet"))
        delta = update_delta if sheet == update_sheet else row_delta
        new_row = int(match.group("row")) + delta
        if new_row < 1:
            raise ValueError(f"公式位移後列號小於 1：{formula}")
        return (f"{match.group('sheet') or ''}{match.group('col_abs')}{match.group('col')}"
                f"{match.group('row_abs')}{new_row}")

    return "".join(piece if quoted else _REF_RE.sub(replace, piece)
                   for quoted, piece in _split_quoted(formula))


@dataclass
class AlignmentPlan:
    update_sheet: str
    mirror_sheet: str
    ok: bool = True
    message: str = ""
    first_divergent_row: Optional[int] = None
    template_row: Optional[int] = None
    writes: List[Tuple[int, List[object]]] = field(default_factory=list)  # (對應分頁列號, A–I 新值)
    missing_update_rows: List[int] = field(default_factory=list)          # 目前沒被抓到的更新列
    duplicate_update_rows: List[int] = field(default_factory=list)        # 被抓兩次以上的更新列
    blocked_rows: List[str] = field(default_factory=list)                 # 有人工值、不能自動改的地方

    @property
    def needs_write(self) -> bool:
        return self.ok and bool(self.writes)


def target_update_rows(update_values: List[List[object]], update_sheet: str,
                       first_ref: int, year: int) -> List[int]:
    """更新分頁中，對應分頁應該要依序抓到的列：從第一個被參照的列開始、有內容、
    不是前一年的資料。"""
    layout = LAYOUTS[update_sheet]
    rows = []
    for row_number, row in enumerate(update_values[1:], start=2):
        if row_number < first_ref:
            continue
        if not has_content(row, layout):
            continue
        y = row_year(row)
        if y is not None and y < year:
            continue
        rows.append(row_number)
    return rows


def mirror_refs(mirror_formulas: List[List[object]], update_sheet: str) -> List[Tuple[int, Optional[int]]]:
    """對應分頁每一列（第 2 列起）參照的更新列號；沒有參照（空白或手打值）是 None。"""
    category_letter = column_letter(LAYOUTS[update_sheet].category_col)
    refs: List[Tuple[int, Optional[int]]] = []
    for row_number, row in enumerate(mirror_formulas, start=1):
        if row_number == 1:
            continue
        ref = None
        for col in range(1, MIRROR_LAST_COL + 1):
            value = row[col - 1] if col - 1 < len(row) else ""
            ref = referenced_update_row(value, update_sheet, category_letter)
            if ref is not None:
                break
        refs.append((row_number, ref))
    return refs


def first_referenced_row(mirror_formulas: List[List[object]], update_sheet: str) -> Optional[int]:
    return next((u for _, u in mirror_refs(mirror_formulas, update_sheet) if u is not None), None)


def plan_alignment(update_values: List[List[object]], mirror_formulas: List[List[object]],
                   update_sheet: str, mirror_sheet: str, year: int) -> AlignmentPlan:
    """比對對應分頁 A–I（公式）與更新分頁內容列，算出要改寫哪些列。"""
    plan = AlignmentPlan(update_sheet=update_sheet, mirror_sheet=mirror_sheet)
    category_letter = column_letter(LAYOUTS[update_sheet].category_col)
    refs = mirror_refs(mirror_formulas, update_sheet)

    referenced = [(m, u) for m, u in refs if u is not None]
    if not referenced:
        plan.ok = False
        plan.message = f"「{mirror_sheet}」A–I 沒有任何公式參照「{update_sheet}」，無法判斷怎麼對齊"
        return plan

    first_ref = referenced[0][1]
    targets = target_update_rows(update_values, update_sheet, first_ref, year)
    target_set = set(targets)

    seen: Dict[int, int] = {}
    for _, u in referenced:
        seen[u] = seen.get(u, 0) + 1
    plan.missing_update_rows = [u for u in targets if u not in seen]
    plan.duplicate_update_rows = sorted(u for u, n in seen.items() if n > 1 and u in target_set)

    # 已對齊的前段：對應分頁從第一個參照列起，依序抓 targets[0], targets[1]...
    # （中間沒有公式的空白列也算對不上——對齊後不該有空白列夾在中間）
    start_index = next(i for i, (_, u) in enumerate(refs) if u is not None)
    divergent_index = None
    template_index = None
    for offset, (mirror_row, u) in enumerate(refs[start_index:]):
        expected = targets[offset] if offset < len(targets) else None
        if expected is None:
            break  # 目標都對到了；後面是預先拉好的尾巴
        if u != expected:
            divergent_index = start_index + offset
            break
        template_index = start_index + offset
    else:
        if len(refs) - start_index < len(targets):
            divergent_index = len(refs)  # 對應分頁列數不夠，要往下長

    if divergent_index is None:
        # 前段全對；檢查預先拉好的尾巴有沒有指回已對到的列（會重複計算）
        tail = [u for _, u in refs[start_index + len(targets):] if u is not None]
        last_target = targets[-1] if targets else first_ref
        bad_tail = [u for u in tail if u <= last_target]
        if not bad_tail:
            plan.message = "已對齊"
            return plan
        divergent_index = start_index + len(targets)

    if template_index is None:
        plan.ok = False
        plan.message = f"「{mirror_sheet}」第一列參照就對不上，請先手動確認開頭"
        return plan

    template_row, template_ref = refs[template_index]
    template = list(mirror_formulas[template_row - 1][:MIRROR_LAST_COL])
    template += [""] * (MIRROR_LAST_COL - len(template))
    plan.template_row = template_row
    first_divergent_row = refs[divergent_index][0] if divergent_index < len(refs) else len(mirror_formulas) + 1
    plan.first_divergent_row = first_divergent_row

    remaining_targets = targets[divergent_index - start_index:]
    existing_last_row = len(mirror_formulas)
    # 尾巴：原本已經預先拉好的列保留同樣長度，接在最後一個目標後面（指向空白列）
    last_target = targets[-1] if targets else template_ref
    write_until = max(existing_last_row, first_divergent_row + len(remaining_targets) - 1)

    formula_cols = [i for i, v in enumerate(template) if is_formula(v)]
    tail_ref = last_target
    for offset, mirror_row in enumerate(range(first_divergent_row, write_until + 1)):
        if offset < len(remaining_targets):
            new_ref = remaining_targets[offset]
        else:
            tail_ref += 1
            new_ref = tail_ref
        current = list(mirror_formulas[mirror_row - 1]) if mirror_row - 1 < len(mirror_formulas) else []
        current += [""] * (MIRROR_LAST_COL - len(current))
        current_ref = None
        for col in range(MIRROR_LAST_COL):
            current_ref = referenced_update_row(current[col], update_sheet, category_letter)
            if current_ref is not None:
                break
        if current_ref == new_ref:
            continue
        # 人工輸入的值會跟著列錯位，停下來不寫
        manual = [f"{column_letter(c + 1)}{mirror_row}" for c in range(MIRROR_LAST_COL)
                  if str(current[c]).strip() and not is_formula(current[c])]
        if manual:
            plan.blocked_rows.append("、".join(manual))
            continue
        new_values: List[object] = []
        for c in range(MIRROR_LAST_COL):
            if c in formula_cols:
                new_values.append(shift_formula(
                    str(template[c]), update_sheet,
                    row_delta=mirror_row - template_row,
                    update_delta=new_ref - template_ref,
                ))
            else:
                new_values.append(current[c])  # 範本是空白／文字的欄位不動
        plan.writes.append((mirror_row, new_values))

    if plan.blocked_rows:
        plan.ok = False
        plan.message = (f"「{mirror_sheet}」從第 {first_divergent_row} 列起要重新對齊，"
                        f"但這些格子是人工輸入的值，改寫會錯位：{'；'.join(plan.blocked_rows[:10])}"
                        + ("…" if len(plan.blocked_rows) > 10 else ""))
        plan.writes = []
        return plan

    plan.message = (f"從第 {first_divergent_row} 列起重新對齊 {len(plan.writes)} 列"
                    f"（範本：第 {template_row} 列）")
    return plan


# ---------------------------------------------------------------------------
# Google Sheets 讀寫（薄薄一層，邏輯都在上面的純函式）
# ---------------------------------------------------------------------------

def _read(service, spreadsheet_id: str, rng: str, render: str) -> List[List[object]]:
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=rng,
        valueRenderOption=render, dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return res.get("values", [])


def _sheet_titles(service, spreadsheet_id: str) -> set:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    return {s["properties"]["title"] for s in meta.get("sheets", [])}


def _formula_rows(update_sheet: str, row_number: int, atm_id: str) -> List[str]:
    from tools.bank_statement.sheet_filter import _fubon_formulas, _yuanta_formulas

    if update_sheet == "富邦更新":
        return _fubon_formulas(row_number, atm_id)
    return _yuanta_formulas(row_number)


def maintain_sheet(area: str, update_sheet: str, *, dry_run: bool, year: Optional[int] = None,
                   service=None, apply_rules_fn: Optional[Callable[[str], Dict[str, int]]] = None,
                   atm_lookup: Optional[Callable[[str, int], str]] = None) -> List[str]:
    """一個地區、一個更新分頁：補公式 →（富邦）套規則 → 對齊對應分頁 → 盤點未標記。"""
    from tools.common.config_loader import get_sheets_service
    from tools.finance_management.statement_registry import resolve_statement_location
    from tools.finance_management.statement_years import taipei_year

    service = service or get_sheets_service()
    year = year or taipei_year()
    spreadsheet_id, title = resolve_statement_location(area, update_sheet, service=service,
                                                       year=None if year == taipei_year() else year)
    lines: List[str] = []
    prefix = f"{area}｜{update_sheet}"

    # 1. 補公式
    values = _read(service, spreadsheet_id, f"'{title}'!A:Z", "FORMATTED_VALUE")
    backfill = plan_formula_backfill(values, update_sheet)
    if backfill:
        layout = LAYOUTS[update_sheet]
        first_col = column_letter(layout.formula_first_col)
        last_col = column_letter(layout.formula_first_col + layout.formula_count - 1)
        if dry_run:
            lines.append(f"{prefix}：{len(backfill)} 列缺公式（{first_col}:{last_col}），"
                         f"第 {backfill[0]}～{backfill[-1]} 列")
        else:
            if atm_lookup is None:
                from tools.bank_statement.sheet_filter import atm_sheet_id as atm_lookup  # type: ignore
            atm_ids: Dict[int, str] = {}
            data = []
            for r in backfill:
                y = row_year(values[r - 1]) or year
                if update_sheet == "富邦更新" and y not in atm_ids:
                    atm_ids[y] = atm_lookup(area, y)
                data.append({"range": f"'{title}'!{first_col}{r}:{last_col}{r}",
                             "values": [_formula_rows(update_sheet, r, atm_ids.get(y, ""))]})
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            ).execute()
            lines.append(f"{prefix}：補公式 {len(backfill)} 列")

    # 2. 套篩選規則（只有富邦更新有規則引擎；會插列，所以排在對齊前面）
    if update_sheet == "富邦更新" and not dry_run and apply_rules_fn is not None:
        result = apply_rules_fn(area)
        lines.append(f"{prefix}：套用篩選規則，更新 {result.get('updated_rows', 0)} 格、"
                     f"插入 {result.get('inserted_rows', 0)} 列")

    if not dry_run:
        values = _read(service, spreadsheet_id, f"'{title}'!A:Z", "FORMATTED_VALUE")

    # 3. 對齊對應分頁
    mirror = mirror_sheet_name(area, update_sheet)
    audit_from = 2
    if mirror not in _sheet_titles(service, spreadsheet_id):
        lines.append(f"{prefix}：沒有「{mirror}」分頁，略過對齊")
    else:
        mirror_formulas = _read(service, spreadsheet_id, f"'{mirror}'!A:I", "FORMULA")
        audit_from = first_referenced_row(mirror_formulas, update_sheet) or 2
        plan = plan_alignment(values, mirror_formulas, update_sheet, mirror, year)
        detail = []
        if plan.missing_update_rows:
            detail.append(f"漏抓更新第 {_ranges(plan.missing_update_rows)} 列")
        if plan.duplicate_update_rows:
            detail.append(f"重複抓更新第 {_ranges(plan.duplicate_update_rows)} 列")
        suffix = f"（{'；'.join(detail)}）" if detail else ""
        if not plan.ok:
            lines.append(f"{prefix}：⚠ {plan.message}{suffix}")
        elif not plan.writes:
            lines.append(f"{prefix}：「{mirror}」{plan.message}")
        elif dry_run:
            lines.append(f"{prefix}：「{mirror}」{plan.message}{suffix}")
        else:
            data = [{"range": f"'{mirror}'!A{r}:I{r}", "values": [vals]} for r, vals in plan.writes]
            for i in range(0, len(data), 500):
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"valueInputOption": "USER_ENTERED", "data": data[i:i + 500]},
                ).execute()
            lines.append(f"{prefix}：「{mirror}」{plan.message}{suffix}")

    # 4. 盤點未標記（寫入後重讀，盤點的是最新結果）
    if not dry_run:
        values = _read(service, spreadsheet_id, f"'{title}'!A:Z", "FORMATTED_VALUE")
    unlabeled = find_unlabeled(values, update_sheet, year, from_row=audit_from)
    if unlabeled:
        lines.append(f"{prefix}：待確認 {len(unlabeled)} 筆")
        lines.extend("    " + item.describe() for item in unlabeled[:30])
        if len(unlabeled) > 30:
            lines.append(f"    …另有 {len(unlabeled) - 30} 筆")
    else:
        lines.append(f"{prefix}：沒有未標記的列")
    return lines


def _ranges(rows: List[int]) -> str:
    """[3,4,5,9] → 3–5、9"""
    if not rows:
        return ""
    parts = []
    start = prev = rows[0]
    for r in rows[1:]:
        if r == prev + 1:
            prev = r
            continue
        parts.append(f"{start}–{prev}" if start != prev else str(start))
        start = prev = r
    parts.append(f"{start}–{prev}" if start != prev else str(start))
    return "、".join(parts[:15]) + ("…" if len(parts) > 15 else "")


def run_statement_maintenance(area: str, *, dry_run: bool,
                              on_progress: Optional[Callable[[str, str], None]] = None) -> str:
    """Tools App 入口：area＝單一地區或「全區」。"""
    import time

    from tools.finance_management.execution_log import log_execution

    targets = list(AREAS) if area == "全區" else [area]
    if any(a not in AREAS for a in targets):
        raise ValueError("請選擇台北／台中／桃園／新竹／高雄／全區")
    apply_rules_fn = None
    if not dry_run:
        from tools.finance_management.statement_rules import apply_rules

        apply_rules_fn = apply_rules
    tool_name = "財報更新盤點" if dry_run else "財報更新補公式＋對齊"
    lines: List[str] = []
    for i, city in enumerate(targets):
        for update_sheet in UPDATE_SHEETS:
            if lines:
                time.sleep(2)  # 避免短時間打太多 Sheets API
            try:
                result = maintain_sheet(city, update_sheet, dry_run=dry_run, apply_rules_fn=apply_rules_fn)
                lines.extend(result)
                if on_progress:
                    on_progress(result[0] if result else f"{city}｜{update_sheet}：完成", "success")
            except Exception as exc:  # 一區失敗不影響其他區
                message = f"{city}｜{update_sheet}：❌ {exc}"
                lines.append(message)
                if on_progress:
                    on_progress(message, "error")
        log_execution(tool_name, city, "完成", "")
    stamp = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M")
    header = f"{'盤點' if dry_run else '執行'}時間：{stamp}"
    return "\n".join([header, *lines])
