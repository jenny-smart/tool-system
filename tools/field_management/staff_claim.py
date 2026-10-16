from __future__ import annotations

# ============================================================
# tools/field_management/staff_claim.py
#
# 從「檸檬專員作業系統」台北／台中 GAS 的「請款設定」選單移植而來
# （批次設定 AD/AE/AF/AG/AJ 欄、檢查專員資料對照），規格見
# docs/field-claim-gas.md。
#
# 試算表：「財務帳務設定」登記表的「清潔異動設定／地區」那一列的試算表ID
#（台北／台中內勤工作表單）；請款分頁用 CLAIM_SHEET_GIDS 的「專員請款」GID，
# 地址對照用同一份試算表的「專員個人資料」分頁。
#
# 與原 GAS 的差異：
#   - 選定列範圍執行時，AJ 給付期別必填，範圍內每列有資料的列都寫入。
#   - 車馬費且 N=案場到案場 時，AN 請款備註帶兩位顧客：MM/DD + E + "-" + O + 請款項目 + "-"。
#   - AD/AE 查不到地址時保留原值並列出列號，不再退回「只比日期」或寫入 D+E+F 文字。
#   - N 欄沒有跨區類型的列不改 AF/AG。
#   - 整段讀取，只寫有變動的儲存格。
# ============================================================

import re
from typing import Any, Callable, Optional
from urllib.parse import quote

from tools.bank_statement.internal_payment_registry import (
    CLEANING_CHANGE_TYPE,
    REGISTRY_SHEET_NAME,
    find_registry_row,
)
from tools.common.config_loader import get_sheets_service

CLAIM_SHEET_TITLE = "專員請款"
STAFF_SHEET_TITLE = "專員個人資料"
SUPPORTED_AREAS = ("台北", "台中")

# 「專員請款」分頁 GID（2026台北／台中內勤工作表單）
CLAIM_SHEET_GIDS = {"台北": 279541195, "台中": 749987965}

# 台北叫「跨區車馬費」，台中叫「車馬費」
TRAVEL_CLAIM_TYPES = {"跨區車馬費", "車馬費"}
SITE_TO_SITE = "案場到案場"

CLAIM_HEADERS = {
    "date": "服務日期",          # D
    "customer": "顧客姓名",      # E
    "phone": "顧客電話",         # F
    "claim_type": "請款項目",    # G
    "sub_item": "請款子項目",    # H
    "trip_type": "跨區類型",     # N
    "customer2": "第二位顧客姓名",  # O
    "phone2": "第二位顧客電話",     # P
    "staff_addr": "專員地址",    # AC
    "addr1": "顧客地址1",        # AD
    "addr2": "顧客地址2",        # AE
    "area_pair": "實際跨區1",    # AF
    "map_link": "實際跨區2",     # AG
    "period": "給付期別",        # AJ
    "remark": "請款備註",        # AN
}

STAFF_HEADERS = {
    "date": "服務日期",
    "customer": "客戶姓名",
    "phone": "聯絡電話",
    "address": "客戶地址",
}

PERIOD_PATTERN = re.compile(r"^\d{6}-\d+$")


# ---------------------------------------------------------------- 小工具

def column_letter(index: int) -> str:
    """0-based 欄位 index → A1 欄位字母。"""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def normalize_date(value: Any) -> str:
    """2026/1/2、2026-01-02、2026/01/02 12:00 → 2026/01/02；無法辨識就原樣回傳。"""
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", text)
    if not match:
        return text
    year, month, day = match.groups()
    return f"{year}/{int(month):02d}/{int(day):02d}"


def normalize_phone(value: Any) -> str:
    """只留數字並去掉開頭 0（試算表常把 0912… 存成數字 912…）。"""
    return re.sub(r"\D", "", str(value or "")).lstrip("0")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _header_index(headers: list[str], name: str, sheet_title: str) -> int:
    cleaned = [_clean(h) for h in headers]
    if name not in cleaned:
        raise RuntimeError(f"「{sheet_title}」第 1 列找不到「{name}」欄")
    return cleaned.index(name)


# ---------------------------------------------------------------- 計算規則

def trip_endpoints(trip_type: str, staff_addr: str, addr1: str, addr2: str) -> Optional[tuple[str, str]]:
    """依跨區類型決定起訖地址；未知類型回傳 None。"""
    trip_type = _clean(trip_type)
    if trip_type == "家到案場":
        return staff_addr, addr1
    if trip_type == "案場到家":
        return addr1, staff_addr
    if trip_type == SITE_TO_SITE:
        return addr1, addr2
    return None


def build_area_pair(trip_type: str, staff_addr: str, addr1: str, addr2: str) -> str:
    """AF 實際跨區1：起訖地址去空白後各取前 6 字，格式「起-訖」。"""
    endpoints = trip_endpoints(trip_type, staff_addr, addr1, addr2)
    if not endpoints:
        return ""
    start, end = (re.sub(r"\s+", "", _clean(a))[:6] for a in endpoints)
    if not start or not end:
        return ""
    return f"{start}-{end}"


def build_map_link(trip_type: str, staff_addr: str, addr1: str, addr2: str) -> str:
    """AG 實際跨區2：Google Maps 導航連結。"""
    if not _clean(trip_type):
        return "❌ 缺少跨區類型"
    endpoints = trip_endpoints(trip_type, staff_addr, addr1, addr2)
    if not endpoints:
        return "❌ 未知跨區類型"
    start, end = (_clean(a) for a in endpoints)
    if not start or not end:
        return "❌ 地址不完整"
    if start == end:
        return "📍 起點終點相同"
    # 與 GAS encodeURIComponent 相同：保留 -_.!~*'() 不編碼
    safe = "-_.!~*'()"
    return f"https://www.google.com/maps/dir/{quote(start, safe=safe)}/{quote(end, safe=safe)}"


def build_claim_remark(service_date: Any, customer: str, claim_type: str,
                       sub_item: str = "", trip_type: str = "", customer2: str = "") -> str:
    """AN 請款備註：MM/DD + 顧客姓名 + 請款項目 + "-"。

    車馬費且跨區類型為案場到案場時，帶兩位顧客：MM/DD + E + "-" + O + 請款項目 + "-"。
    主管專用時請款項目改用子項目。
    """
    parts = []
    match = re.match(r"^\d{4}[/\-.](\d{1,2})[/\-.](\d{1,2})", _clean(service_date))
    if match:
        parts.append(f"{int(match.group(1)):02d}/{int(match.group(2)):02d}")

    customer = _clean(customer)
    customer2 = _clean(customer2)
    claim_type = _clean(claim_type)
    if claim_type in TRAVEL_CLAIM_TYPES and _clean(trip_type) == SITE_TO_SITE and customer2:
        parts.append(f"{customer}-{customer2}" if customer else customer2)
    elif customer:
        parts.append(customer)

    item = _clean(sub_item) if claim_type == "主管專用" and _clean(sub_item) else claim_type
    if item:
        parts.append(item)
    return "".join(parts) + "-" if parts else ""


class AddressBook:
    """「專員個人資料」G～N 的訂單資料：服務日期＋客戶姓名＋聯絡電話 → 客戶地址。"""

    def __init__(self, rows: list[list[Any]]):
        if not rows:
            raise RuntimeError(f"「{STAFF_SHEET_TITLE}」沒有資料")
        headers = rows[0]
        idx = {key: _header_index(headers, name, STAFF_SHEET_TITLE) for key, name in STAFF_HEADERS.items()}
        self.exact: dict[tuple[str, str, str], str] = {}
        self.by_name: dict[tuple[str, str], str] = {}
        self.by_phone: dict[tuple[str, str], str] = {}
        self.count = 0
        for row in rows[1:]:
            def cell(key: str) -> str:
                i = idx[key]
                return _clean(row[i]) if i < len(row) else ""

            date, name, phone, address = (
                normalize_date(cell("date")), cell("customer"), normalize_phone(cell("phone")), cell("address"),
            )
            if not date or not address or not (name or phone):
                continue
            self.count += 1
            self.exact.setdefault((date, name, phone), address)
            if name:
                self.by_name.setdefault((date, name), address)
            if phone:
                self.by_phone.setdefault((date, phone), address)

    def lookup(self, service_date: Any, name: Any, phone: Any) -> str:
        """依序比對：日期＋姓名＋電話 → 日期＋姓名 → 日期＋電話；都不符回傳空字串。"""
        date, name, phone = normalize_date(service_date), _clean(name), normalize_phone(phone)
        if not date:
            return ""
        return (
            self.exact.get((date, name, phone))
            or (self.by_name.get((date, name)) if name else "")
            or (self.by_phone.get((date, phone)) if phone else "")
            or ""
        )


# ---------------------------------------------------------------- 試算表存取

def resolve_spreadsheet_id(area: str) -> str:
    if area not in SUPPORTED_AREAS:
        raise ValueError(f"專員請款目前只支援：{'、'.join(SUPPORTED_AREAS)}")
    record = find_registry_row(CLEANING_CHANGE_TYPE, area)
    spreadsheet_id = _clean(record.get("試算表ID"))
    if not spreadsheet_id:
        raise RuntimeError(f"「{REGISTRY_SHEET_NAME}」{CLEANING_CHANGE_TYPE}／{area} 缺少試算表ID")
    return spreadsheet_id


def resolve_claim_sheet_title(service, spreadsheet_id: str, area: str, gid: Optional[int] = None) -> str:
    """依 GID 找請款分頁標題（gid 未指定時用 CLAIM_SHEET_GIDS）。"""
    gid = CLAIM_SHEET_GIDS[area] if gid is None else int(gid)
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") == gid:
            return str(props.get("title", ""))
    raise RuntimeError(f"{area}試算表找不到 GID={gid} 的{CLAIM_SHEET_TITLE}分頁")


def _read(service, spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=a1_range, valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return res.get("values", [])


def plan_claim_updates(claim_header: list[Any], claim_rows: list[list[Any]], start_row: int,
                       period: str, address_book: AddressBook) -> dict[str, Any]:
    """計算要寫回的儲存格（不碰試算表，方便測試）。

    claim_rows 對應試算表第 start_row 列起的資料。
    回傳 {"updates": [(A1, value)], "missing_addr1": [...], "missing_addr2": [...], "rows": n}
    """
    idx = {key: _header_index(claim_header, name, CLAIM_SHEET_TITLE) for key, name in CLAIM_HEADERS.items()}
    updates: list[tuple[str, str]] = []
    missing_addr1: list[int] = []
    missing_addr2: list[int] = []
    counts = {"addr1": 0, "addr2": 0, "area_pair": 0, "map_link": 0, "period": 0, "remark": 0}

    for offset, row in enumerate(claim_rows):
        row_no = start_row + offset

        def get(key: str) -> str:
            i = idx[key]
            return _clean(row[i]) if i < len(row) else ""

        def put(key: str, value: str) -> None:
            if value != get(key):
                updates.append((f"{column_letter(idx[key])}{row_no}", value))
                counts[key] += 1

        # 空白列（沒有服務日期也沒有顧客）整列不動，避免範圍選太大時把期別寫進空列
        if not get("date") and not get("customer"):
            continue

        # AJ 給付期別：範圍內每列都寫
        put("period", period)

        # AD 顧客地址1
        addr1 = address_book.lookup(get("date"), get("customer"), get("phone"))
        if addr1:
            put("addr1", addr1)
        else:
            addr1 = get("addr1")
            missing_addr1.append(row_no)

        # AE 顧客地址2：O、P 都空就清空
        if get("customer2") or get("phone2"):
            addr2 = address_book.lookup(get("date"), get("customer2"), get("phone2"))
            if addr2:
                put("addr2", addr2)
            else:
                addr2 = get("addr2")
                missing_addr2.append(row_no)
        else:
            addr2 = ""
            put("addr2", "")

        # AF / AG：有跨區類型才處理
        trip_type = get("trip_type")
        if trip_type:
            staff_addr = get("staff_addr")
            put("area_pair", build_area_pair(trip_type, staff_addr, addr1, addr2))
            put("map_link", build_map_link(trip_type, staff_addr, addr1, addr2))

        # AN 請款備註：車馬費案場到案場一律重算（帶兩位顧客）；其他列只補空白
        is_site_to_site = get("claim_type") in TRAVEL_CLAIM_TYPES and trip_type == SITE_TO_SITE
        if is_site_to_site or not get("remark"):
            put("remark", build_claim_remark(
                get("date"), get("customer"), get("claim_type"), get("sub_item"), trip_type, get("customer2"),
            ))

    return {
        "updates": updates,
        "missing_addr1": missing_addr1,
        "missing_addr2": missing_addr2,
        "counts": counts,
        "rows": len(claim_rows),
    }


def run_claim_batch(area: str, start_row: int, end_row: int, period: str,
                    service=None, on_progress: Optional[Callable[[str], None]] = None,
                    claim_gid: Optional[int] = None) -> dict[str, Any]:
    """批次設定專員請款第 start_row～end_row 列的 AD/AE/AF/AG/AJ/AN 欄。"""
    log = on_progress or (lambda msg: None)
    period = _clean(period)
    if not PERIOD_PATTERN.match(period):
        raise ValueError("請輸入給付期別，格式：YYYYMM-期數（例如 202601-1）")
    start_row, end_row = int(start_row), int(end_row)
    if start_row < 2 or end_row < start_row:
        raise ValueError("列數範圍錯誤：起始列需 ≥ 2，結束列需 ≥ 起始列")

    service = service or get_sheets_service()
    spreadsheet_id = resolve_spreadsheet_id(area)
    claim_title = resolve_claim_sheet_title(service, spreadsheet_id, area, claim_gid)

    claim_header_rows = _read(service, spreadsheet_id, f"'{claim_title}'!1:1")
    if not claim_header_rows:
        raise RuntimeError(f"「{claim_title}」第 1 列沒有表頭")
    claim_header = claim_header_rows[0]
    last_col = column_letter(len(claim_header) - 1)
    claim_rows = _read(service, spreadsheet_id, f"'{claim_title}'!A{start_row}:{last_col}{end_row}")
    claim_rows += [[] for _ in range(end_row - start_row + 1 - len(claim_rows))]

    address_book = AddressBook(_read(service, spreadsheet_id, f"'{STAFF_SHEET_TITLE}'!A:Z"))
    log(f"{area}｜{claim_title} 第 {start_row}～{end_row} 列，地址對照 {address_book.count} 筆")

    plan = plan_claim_updates(claim_header, claim_rows, start_row, period, address_book)
    updates = plan["updates"]
    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "RAW",
                "data": [{"range": f"'{claim_title}'!{a1}", "values": [[v]]} for a1, v in updates],
            },
        ).execute()

    plan.update(area=area, spreadsheet_id=spreadsheet_id, sheet_title=claim_title, start_row=start_row, end_row=end_row, period=period)
    return plan


def format_batch_result(result: dict[str, Any]) -> str:
    counts = result["counts"]
    lines = [
        f"✅ {result['area']}專員請款 第 {result['start_row']}～{result['end_row']} 列完成"
        f"（共 {result['rows']} 列，更新 {len(result['updates'])} 格）",
        f"AJ 給付期別 {result['period']}：{counts['period']} 格｜AD：{counts['addr1']}｜AE：{counts['addr2']}｜"
        f"AF：{counts['area_pair']}｜AG：{counts['map_link']}｜AN：{counts['remark']}",
    ]
    if result["missing_addr1"]:
        lines.append(f"⚠️ AD 查無顧客地址（保留原值）：第 {'、'.join(map(str, result['missing_addr1']))} 列")
    if result["missing_addr2"]:
        lines.append(f"⚠️ AE 查無第二位顧客地址（保留原值）：第 {'、'.join(map(str, result['missing_addr2']))} 列")
    return "\n".join(lines)


def check_address_book(area: str, service=None, claim_gid: Optional[int] = None) -> str:
    """檢查「專員個人資料」的地址對照欄位與筆數（對應 GAS「檢查專員資料對照」）。"""
    service = service or get_sheets_service()
    spreadsheet_id = resolve_spreadsheet_id(area)
    rows = _read(service, spreadsheet_id, f"'{STAFF_SHEET_TITLE}'!A:Z")
    if not rows:
        raise RuntimeError(f"「{STAFF_SHEET_TITLE}」沒有資料")
    positions = [
        f"{column_letter(_header_index(rows[0], name, STAFF_SHEET_TITLE))}欄 {name}"
        for name in STAFF_HEADERS.values()
    ]
    book = AddressBook(rows)
    claim_title = resolve_claim_sheet_title(service, spreadsheet_id, area, claim_gid)
    claim_header = (_read(service, spreadsheet_id, f"'{claim_title}'!1:1") or [[]])[0]
    claim_positions = [
        f"{column_letter(_header_index(claim_header, name, CLAIM_SHEET_TITLE))} {name}"
        for name in CLAIM_HEADERS.values()
    ]
    return (
        f"✅ {area}地址對照：{'、'.join(positions)}｜有效 {book.count} 筆（共 {len(rows) - 1} 列）\n"
        f"專員請款欄位：{'、'.join(claim_positions)}"
    )
