"""
tools/annual_rollover/new_year_generate.py

換年度：依主控表「生成新年度」工作表，一列一列複製前一年度的資料夾與檔案，
產生新年度版本，並把新檔 ID 記到「新年度ID」工作表，供 12 月底置換程式設定。

「生成新年度」欄位（第 1 列為標題）：
  A 名稱
  B 資料夾（上層資料夾網址）
  C 說明
  D 前一年度資料夾範例：
      - 含年份的名稱（例：2026、2026專員回報表單）→ 指定要複製的年度資料夾
      - 不含年份的名稱（例：01.台北專員/02.台中專員、台北/桃園）→ 年度資料夾底下
        要一併建立的子資料夾
      - 空白 → 自動找名稱含前一年度的資料夾（例：2026年）
  E 檔案：要複製的前一年度檔名，以「，」或「,」分隔；檔名中的年份會換成新年度
  F 台北/台中、G 非台北/台中：說明用，程式不讀
  H 工作表整理：例「移除富邦更新/元大更新的A2:」→ 新檔中這些工作表清掉 A2 以下資料
  I 生成結果：程式回寫（時間、新資料夾與檔案 ID）

「新年度ID」：A 地區、B 前一年度檔名。程式會在 B 名稱（去掉年份後）與
複製來源相符的列，回寫 C 新年度 ID、D 新年度檔名、E 生成時間。

用法：
  python -m tools.annual_rollover.new_year_generate --year 2027 --rows 2 --dry-run
  python -m tools.annual_rollover.new_year_generate --year 2027 --rows 2-8
  python -m tools.annual_rollover.new_year_generate --year 2027 --rows all --fresh

預設可重複執行：新年度資料夾／檔案已存在就沿用並記錄 ID，不會重複建立。
加 --fresh 會把已存在的同名新年度資料夾／檔案改名為「<原名>_舊版<時間>」後重新生成
（只改名，不刪除）。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.vip_config import MASTER_SPREADSHEET_ID

GENERATE_SHEET = "生成新年度"
NEW_ID_SHEET = "新年度ID"
RESULT_COL = "I"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
TZ = ZoneInfo("Asia/Taipei")

_SPLIT_LIST = re.compile(r"[，,、]")
_SPLIT_SLASH = re.compile(r"[/／]")
_FOLDER_ID = re.compile(r"/folders/([A-Za-z0-9_-]+)")
# 「生成新年度」A 欄為這些名稱時，套用財務報表專用整理（取代 H 欄的通用清除）
FINANCE_ROW_NAMES = {"財務報表"}
# 「內勤表單」：新建立的內勤工作表單整理（取代 H 欄的通用清除）
OFFICE_ROW_NAMES = {"內勤表單"}
OFFICE_CLEAR_RANGES = ["'信用卡'!A2:J", "'專員收現'!A2:P", "'專員回報'!A2:P",
                       "'專員請款'!A2:AS", "'清潔客訴'!A3:AN"]
OFFICE_CLEAR_WITH_FILL = [("清潔異動", "A2:AE")]  # 清除內容＋底色
# 新內勤工作表單只保留這些分頁（加上「{前一年度}目標」改名後的「{新年度}目標」），其餘刪除；
# 原檔沒有的分頁略過
OFFICE_KEEP_SHEETS = [
    "總覽", "專員班表查詢本月_1009", "專員班表查詢次月_1009", "刷卡連結", "評價預約",
    "信用卡", "ATM", "清潔異動", "專員收現", "專員回報", "專員請款", "清潔客訴",
    "客訴統計表", "專員個人資料", "偏遠區域個案服務規範與流程", "報價單", "工具組內容",
    "專員跨區表", "裝細評估", "搬家打包評估", "系數參數", "外場排程系統執行Log", "週末提醒",
]
OFFICE_KEEP_CONTAINS = ["儲值金不足"]  # 分頁名稱含這些字也保留（例：202611儲值金不足）
OFFICE_ATM_CELL = "'ATM'!A2"

# 「專員名冊/薪資檔」：新建立的專員名冊與時數、專員薪資相關整理（取代 H 欄的通用清除）
STAFF_ROW_NAMES = {"專員名冊/薪資檔"}
ROSTER_KEYWORD = "專員名冊與時數"
SALARY_KEYWORD = "專員薪資相關"
ROSTER_SCHEDULE_SHEET = "{year}排班統計表"   # 改名為新年度；每月「地區」～「備註」清除第 5 列以下
ROSTER_SCHEDULE_LAST_COL = "GB"
ROSTER_CLEAR_RANGES = ["'應徵履歷統計'!A2:K", "'104應徵履歷'!A2:G", "'面試紀錄'!A2:K"]
ROSTER_MONTHLY_SHEET = re.compile(r"^(\d{6})專員名冊$")      # 只留最近一個月
SALARY_CLEAR_RANGES = ["'場次和時數'!E2:AN", "'教育訓練簽到名單'!A2:AE", "'工具包押金退款'!A2:I",
                       "'新人實境'!A2:K", "'新人實習'!A2:K", "'外場現金出入記錄'!A2:J"]
SALARY_YEAR_CELLS = ["'場次和時數'!A2", "'外場現金出入記錄'!A1"]
SALARY_DEPOSIT_SHEET = "工具包押金"   # G 欄非空白（已全數提領）的列刪除
SALARY_DEPOSIT_COL = "G"
SALARY_MONTHLY_SHEET = re.compile(r"^(\d{6})調薪資料$")      # 只留最近一個月
OFFICE_ATM_FORMULA = (
    '=filter({{filter(importrange("{fid}","富邦更新!$A2:$H"),'
    'importrange("{fid}","富邦更新!$A2:A"))}},{{0,1,1,1,1,1,0,1}})'
)
FINANCE_AREAS = ["台北", "台中", "桃園", "新竹", "高雄", "電器"]
FINANCE_REVIEW_KEYWORD = "目標及review"
FINANCE_PL_SHEET = "股東損益表_財務"
FINANCE_PL_CELL = "A241"
FINANCE_YEAR_CELLS = ["'科目對照表'!E1"]  # 新年度檔案中改成新年度年份
FINANCE_CLEAR_RANGES = {
    "台北/台中": ["'請款記錄'!A2:J", "'富邦更新'!A2:H", "'元大更新'!A2:I", "'零用金'!A3:H"],
    "其他": ["'富邦更新'!A2:H", "'元大更新'!A2:I"],
}


def finance_area(name: str) -> str:
    return next((area for area in FINANCE_AREAS if str(name).startswith(area)), "")


_CLEANUP = re.compile(r"移除(.+?)的([A-Z]+\d+):?([A-Z]*\d*)")


# ============================================================
# 解析
# ============================================================
@dataclass
class RowSpec:
    row_number: int
    name: str
    folder_id: str
    year_folder_names: List[str] = field(default_factory=list)
    sub_folder_names: List[str] = field(default_factory=list)
    file_names: List[str] = field(default_factory=list)
    cleanup_sheets: List[str] = field(default_factory=list)
    cleanup_start: str = "A2"
    area: str = ""  # F 欄（台北/台中…），單一地區時用來找該區財報


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_letters(index: int) -> str:
    """0-based 欄號 → 欄名（0→A、17→R）。"""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def month_block_starts(header: List[str]) -> List[int]:
    """排班統計表第 4 列中每個月「地區」標題的欄號（0-based）。"""
    return [i for i, v in enumerate(header) if str(v).strip() == "地區"]


def a1_to_grid(sheet_id: int, a1: str) -> Dict[str, int]:
    """「A2:AE」→ GridRange（0-based、結束不含；沒寫結束列＝到底）。"""
    start, end = a1.split(":")
    s_col, s_row = re.match(r"([A-Z]+)(\d+)", start).groups()
    e_col, e_row = re.match(r"([A-Z]+)(\d*)", end).groups()
    grid = {"sheetId": sheet_id, "startRowIndex": int(s_row) - 1,
            "startColumnIndex": col_index(s_col), "endColumnIndex": col_index(e_col) + 1}
    if e_row:
        grid["endRowIndex"] = int(e_row)
    return grid


ALL_AREA = "全區"


def mentions_area(text: str, area: str) -> bool:
    names = [area] + [alias for alias, canon in NAME_ALIASES.items() if canon == area]
    return any(n in text for n in names)


def filter_spec_by_area(spec: "RowSpec", area: str) -> Optional["RowSpec"]:
    """依執行區域篩選：列有 F 欄單一地區時整列比對；否則只留檔名／子資料夾含該地區的項目
    （例：選「台北」只複製「台北2026財報」，選「目標及review」只複製 review）。"""
    if not area or area == ALL_AREA:
        return spec
    if spec.area:
        return spec if spec.area == area else None
    files = [f for f in spec.file_names if mentions_area(f, area)]
    subs = [d for d in spec.sub_folder_names if mentions_area(d, area)]
    if not files and not subs:
        return None
    spec.file_names, spec.sub_folder_names = files, subs
    return spec


def split_list(text: str, pattern: re.Pattern = _SPLIT_LIST) -> List[str]:
    return [part.strip() for part in pattern.split(str(text or "")) if part.strip()]


def folder_id_from_url(value: str) -> str:
    value = str(value or "").strip()
    match = _FOLDER_ID.search(value)
    if match:
        return match.group(1)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value) else ""


def replace_year(name: str, prev_year: int, new_year: int) -> str:
    return re.sub(rf"(?<!\d){prev_year}(?!\d)", str(new_year), name)


# 「新年度ID」B 欄與實際檔名的同義寫法
NAME_ALIASES = {"家電": "電器"}


def strip_year(name: str) -> str:
    """去掉年份、空白與結尾「檔」字並統一同義字，用來比對「2026台北財報」與
    「台北2026財報」、「2026家電財報」與「電器2026財報」、「2026目標及review檔」
    與「2026目標及review」這類寫法。"""
    text = re.sub(r"(?<!\d)20\d{2}(?!\d)", "", re.sub(r"\s+", "", str(name or "")))
    for alias, canonical in NAME_ALIASES.items():
        text = text.replace(alias, canonical)
    return re.sub(r"檔$", "", text)


def parse_cleanup(text: str) -> Tuple[List[str], str]:
    match = _CLEANUP.search(str(text or ""))
    if not match:
        return [], "A2"
    return split_list(match.group(1), _SPLIT_SLASH), match.group(2)


def parse_row(row_number: int, row: List[str], prev_year: int) -> Optional[RowSpec]:
    cells = [str(c or "").strip() for c in row] + [""] * 8
    folder_id = folder_id_from_url(cells[1])
    if not folder_id:
        return None
    year_names: List[str] = []
    sub_names: List[str] = []
    for part in split_list(cells[3], _SPLIT_SLASH):
        (year_names if str(prev_year) in part else sub_names).append(part)
    cleanup_sheets, cleanup_start = parse_cleanup(cells[7])
    return RowSpec(
        row_number=row_number,
        name=cells[0],
        folder_id=folder_id,
        year_folder_names=year_names,
        sub_folder_names=sub_names,
        file_names=split_list(cells[4]),
        cleanup_sheets=cleanup_sheets,
        cleanup_start=cleanup_start,
        area=cells[5] if "/" not in cells[5] and "／" not in cells[5] else "",
    )


def parse_rows_arg(text: str, last_row: int) -> List[int]:
    text = str(text or "all").strip().lower()
    if text in ("", "all"):
        return list(range(2, last_row + 1))
    rows: List[int] = []
    for part in split_list(text):
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            rows.extend(range(start, end + 1))
        else:
            rows.append(int(part))
    return sorted(set(r for r in rows if r >= 2))


# ============================================================
# Google API
# ============================================================
class Rollover:
    def __init__(self, drive, sheets, spreadsheet_id: str, new_year: int,
                 dry_run: bool = False, fresh: bool = False, log=print):
        self.drive = drive
        self.sheets = sheets
        self.spreadsheet_id = spreadsheet_id
        self.new_year = new_year
        self.prev_year = new_year - 1
        self.dry_run = dry_run
        self.fresh = fresh
        self.log = log
        self.stamp = datetime.now(TZ).strftime("%Y%m%d%H%M")

    # ---------- Drive ----------
    def children(self, folder_id: str) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        token = None
        while True:
            res = self.drive.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id,name,mimeType)",
                pageToken=token, pageSize=1000,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            files.extend(res.get("files", []))
            token = res.get("nextPageToken")
            if not token:
                return files

    def _retire(self, item: Dict[str, Any]) -> None:
        new_name = f"{item['name']}_舊版{self.stamp}"
        self.log(f"  --fresh：既有「{item['name']}」改名為「{new_name}」")
        if not self.dry_run:
            self.drive.files().update(
                fileId=item["id"], body={"name": new_name}, supportsAllDrives=True,
            ).execute()

    def ensure_folder(self, parent_id: str, name: str) -> Dict[str, Any]:
        existing = [f for f in self.children(parent_id)
                    if f["mimeType"] == FOLDER_MIME and f["name"] == name]
        if existing and not self.fresh:
            self.log(f"  資料夾已存在，沿用：{name}")
            return existing[0]
        for item in existing:
            self._retire(item)
        self.log(f"  建立資料夾：{name}")
        if self.dry_run:
            return {"id": f"(dry-run:{name})", "name": name}
        return self.drive.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id,name", supportsAllDrives=True,
        ).execute()

    def ensure_copy(self, source: Dict[str, Any], parent_id: str, name: str) -> Tuple[Dict[str, Any], bool]:
        existing = [] if parent_id.startswith("(dry-run") else [
            f for f in self.children(parent_id)
            if f["mimeType"] != FOLDER_MIME and f["name"] == name
        ]
        if existing and not self.fresh:
            self.log(f"  檔案已存在，沿用：{name}")
            return existing[0], False
        for item in existing:
            self._retire(item)
        self.log(f"  複製檔案：{source['name']} → {name}")
        if self.dry_run:
            return {"id": f"(dry-run:{name})", "name": name, "mimeType": source.get("mimeType")}, True
        copied = self.drive.files().copy(
            fileId=source["id"], body={"name": name, "parents": [parent_id]},
            fields="id,name,mimeType", supportsAllDrives=True,
        ).execute()
        return copied, True

    # ---------- Sheets ----------
    def read(self, a1: str) -> List[List[str]]:
        res = self.sheets.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=a1,
        ).execute()
        return res.get("values", [])

    def write(self, a1: str, values: List[List[Any]]) -> None:
        if self.dry_run:
            return
        self.sheets.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id, range=a1,
            valueInputOption="RAW", body={"values": values},
        ).execute()

    def cleanup_sheets(self, file_id: str, sheet_names: List[str], start: str) -> None:
        if not sheet_names or self.dry_run:
            if sheet_names:
                self.log(f"  （dry-run）會清除 {'/'.join(sheet_names)} 的 {start} 以下資料")
            return
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties.title",
        ).execute()
        titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
        ranges = [f"'{name}'!{start}:ZZZ" for name in sheet_names if name in titles]
        if ranges:
            self.sheets.spreadsheets().values().batchClear(
                spreadsheetId=file_id, body={"ranges": ranges},
            ).execute()
            self.log(f"  已清除：{', '.join(ranges)}")

    # ---------- 主流程 ----------
    def find_year_folders(self, spec: RowSpec) -> List[Dict[str, Any]]:
        folders = [f for f in self.children(spec.folder_id) if f["mimeType"] == FOLDER_MIME]
        if spec.year_folder_names:
            wanted = set(spec.year_folder_names)
            found = [f for f in folders if f["name"] in wanted]
            missing = wanted - {f["name"] for f in found}
            if missing:
                raise RuntimeError(f"找不到前一年度資料夾：{'、'.join(sorted(missing))}")
            return found
        pattern = re.compile(rf"(?<!\d){self.prev_year}(?!\d)")
        found = [f for f in folders if pattern.search(f["name"])]
        if len(found) > 1:
            # 有多個時優先取「2026」或「2026年」這種純年度資料夾
            exact = [f for f in found if re.fullmatch(rf"{self.prev_year}年?", f["name"])]
            found = exact or found
        return found[:1]

    def process_row(self, spec: RowSpec) -> List[Dict[str, str]]:
        """回傳生成紀錄：[{kind, old_name, old_id, new_name, new_id}]"""
        records: List[Dict[str, str]] = []
        year_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

        for old in self.find_year_folders(spec):
            new = self.ensure_folder(spec.folder_id, replace_year(old["name"], self.prev_year, self.new_year))
            year_pairs.append((old, new))
            records.append({"kind": "資料夾", "old_name": old["name"], "old_id": old["id"],
                            "new_name": new["name"], "new_id": new["id"]})

        # 前一年度資料夾裡的子資料夾（D 欄不含年份的名稱）
        sub_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for old_year, new_year in year_pairs:
            if not spec.sub_folder_names:
                continue
            old_subs = {f["name"]: f for f in self.children(old_year["id"]) if f["mimeType"] == FOLDER_MIME}
            for sub_name in spec.sub_folder_names:
                if new_year["id"].startswith("(dry-run"):
                    self.log(f"  建立子資料夾：{new_year['name']}/{sub_name}")
                    new_sub = {"id": f"(dry-run:{sub_name})", "name": sub_name}
                else:
                    new_sub = self.ensure_folder(new_year["id"], sub_name)
                records.append({"kind": "子資料夾", "old_name": sub_name,
                                "old_id": old_subs.get(sub_name, {}).get("id", ""),
                                "new_name": f"{new_year['name']}/{sub_name}", "new_id": new_sub["id"]})
                if sub_name in old_subs:
                    sub_pairs.append((old_subs[sub_name], new_sub))

        # 檔案：先找年度資料夾、再找子資料夾、最後找上層資料夾
        search_pairs = year_pairs + sub_pairs + [({"id": spec.folder_id}, {"id": spec.folder_id})]
        listing_cache: Dict[str, List[Dict[str, Any]]] = {}
        for file_name in spec.file_names:
            source, target_parent = None, None
            for old_parent, new_parent in search_pairs:
                files = listing_cache.setdefault(old_parent["id"], self.children(old_parent["id"]))
                match = next((f for f in files if f["mimeType"] != FOLDER_MIME and f["name"] == file_name), None)
                if match:
                    source, target_parent = match, new_parent
                    break
            if not source:
                self.log(f"  ⚠ 找不到檔案：{file_name}")
                records.append({"kind": "檔案", "old_name": file_name, "old_id": "",
                                "new_name": "找不到來源", "new_id": ""})
                continue
            new_name = replace_year(file_name, self.prev_year, self.new_year)
            copied, created = self.ensure_copy(source, target_parent["id"], new_name)
            if (created and copied.get("mimeType") == SHEET_MIME
                    and spec.name not in FINANCE_ROW_NAMES | OFFICE_ROW_NAMES | STAFF_ROW_NAMES):
                self.cleanup_sheets(copied["id"], spec.cleanup_sheets, spec.cleanup_start)
            records.append({"kind": "檔案", "old_name": source["name"], "old_id": source["id"],
                            "new_name": copied["name"], "new_id": copied["id"], "created": created})
        if spec.name in FINANCE_ROW_NAMES:
            self.finance_post_process(records)
        if spec.name in OFFICE_ROW_NAMES:
            self.office_post_process(spec, records)
        if spec.name in STAFF_ROW_NAMES:
            self.staff_post_process(records)
        return records

    # ---------- 專員名冊／薪資檔專用整理 ----------
    def _sheet_map(self, file_id: str) -> Dict[str, int]:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties(sheetId,title)",
        ).execute()
        return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

    @staticmethod
    def _keep_latest_month(sheet_ids: Dict[str, int], pattern: re.Pattern) -> List[str]:
        monthly = sorted((m.group(1), t) for t in sheet_ids if (m := pattern.match(t)))
        return [t for _, t in monthly[:-1]]

    def _apply(self, file_id: str, clear: List[str], values: List[Dict[str, Any]],
               requests: List[Dict[str, Any]]) -> None:
        if clear:
            self.sheets.spreadsheets().values().batchClear(
                spreadsheetId=file_id, body={"ranges": clear}).execute()
        if values:
            self._values_batch(file_id, values)
        if requests:
            self.sheets.spreadsheets().batchUpdate(
                spreadsheetId=file_id, body={"requests": requests}).execute()

    def staff_post_process(self, records: List[Dict[str, str]]) -> None:
        files = [r for r in records if r["kind"] == "檔案" and r.get("new_id")]
        roster = next((r for r in files if ROSTER_KEYWORD in r["new_name"]), None)
        salary = next((r for r in files if SALARY_KEYWORD in r["new_name"]), None)

        if roster and roster.get("created"):
            old_sched = ROSTER_SCHEDULE_SHEET.format(year=self.prev_year)
            new_sched = ROSTER_SCHEDULE_SHEET.format(year=self.new_year)
            self.log(f"  {roster['new_name']}：{old_sched}→{new_sched}（各月地區～備註第 5 列以下清除，"
                     f"未勾班名單保留）；清除 {'、'.join(ROSTER_CLEAR_RANGES)}；只留最近一個月專員名冊")
            if not self.dry_run:
                ids = self._sheet_map(roster["new_id"])
                requests: List[Dict[str, Any]] = []
                clear = [r for r in ROSTER_CLEAR_RANGES if r.split("!")[0].strip("'") in ids]
                if old_sched in ids:
                    requests.append({"updateSheetProperties": {
                        "properties": {"sheetId": ids[old_sched], "title": new_sched}, "fields": "title"}})
                    header = self.sheets.spreadsheets().values().get(
                        spreadsheetId=roster["new_id"],
                        range=f"'{old_sched}'!A4:{ROSTER_SCHEDULE_LAST_COL}4").execute().get("values", [[]])
                    for start in month_block_starts(header[0] if header else []):
                        clear.append(f"'{old_sched}'!{col_letters(start)}5:{col_letters(start + 8)}")
                stale = self._keep_latest_month(ids, ROSTER_MONTHLY_SHEET)
                requests += [{"deleteSheet": {"sheetId": ids[t]}} for t in stale]
                if stale:
                    self.log(f"  刪除分頁：{'、'.join(stale)}")
                self._apply(roster["new_id"], clear, [], requests)

        if salary and salary.get("created"):
            self.log(f"  {salary['new_name']}：清除 {'、'.join(SALARY_CLEAR_RANGES)}；"
                     f"{'、'.join(SALARY_YEAR_CELLS)} → {self.new_year}；工具包押金 G 欄有值的列刪除；"
                     f"只留最近一個月調薪資料"
                     + (f"；公式中專員名冊 ID {roster['old_id']} → {roster['new_id']}" if roster else ""))
            if not self.dry_run:
                ids = self._sheet_map(salary["new_id"])
                clear = [r for r in SALARY_CLEAR_RANGES if r.split("!")[0].strip("'") in ids]
                values = [{"range": c, "values": [[self.new_year]]}
                          for c in SALARY_YEAR_CELLS if c.split("!")[0].strip("'") in ids]
                requests = []
                if SALARY_DEPOSIT_SHEET in ids:
                    col = self.sheets.spreadsheets().values().get(
                        spreadsheetId=salary["new_id"],
                        range=f"'{SALARY_DEPOSIT_SHEET}'!{SALARY_DEPOSIT_COL}2:{SALARY_DEPOSIT_COL}",
                    ).execute().get("values", [])
                    rows = [i + 1 for i, v in enumerate(col, start=1) if v and str(v[0]).strip()]
                    for row in sorted(rows, reverse=True):  # 由下往上刪，列號才不會位移
                        requests.append({"deleteDimension": {"range": {
                            "sheetId": ids[SALARY_DEPOSIT_SHEET], "dimension": "ROWS",
                            "startIndex": row - 1, "endIndex": row}}})
                    if rows:
                        self.log(f"  工具包押金刪除 {len(rows)} 列")
                stale = self._keep_latest_month(ids, SALARY_MONTHLY_SHEET)
                requests += [{"deleteSheet": {"sheetId": ids[t]}} for t in stale]
                if stale:
                    self.log(f"  刪除分頁：{'、'.join(stale)}")
                if roster and roster.get("old_id") and roster["old_id"] != roster["new_id"]:
                    requests.append({"findReplace": {
                        "find": roster["old_id"], "replacement": roster["new_id"],
                        "allSheets": True, "includeFormulas": True}})
                self._apply(salary["new_id"], clear, values, requests)

    # ---------- 內勤表單專用整理 ----------
    def find_finance_file(self, area: str) -> Optional[Dict[str, Any]]:
        """找新年度該區財報（例：台北2027財報），需先跑過「財務報表」。"""
        names = [f"{area}{self.new_year}財報"] + [
            f"{alias}{self.new_year}財報" for alias, canon in NAME_ALIASES.items() if canon == area]
        for name in names:
            res = self.drive.files().list(
                q=f"name = '{name}' and mimeType = '{SHEET_MIME}' and trashed = false",
                fields="files(id,name)", pageSize=5,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            files = res.get("files", [])
            if files:
                return files[0]
        return None

    def office_post_process(self, spec: RowSpec, records: List[Dict[str, str]]) -> None:
        """只處理這次新建立的內勤工作表單：
        0. 只保留 OFFICE_KEEP_SHEETS（＋目標分頁），其餘分頁刪除
        1. 「{前一年度}目標」分頁改名為「{新年度}目標」
        2. 清除 OFFICE_CLEAR_RANGES；清潔異動 A2:AE 清內容與底色
        3. ATM!A2 改成 FILTER(IMPORTRANGE(新年度該區財報 富邦更新))
        """
        targets = [r for r in records if r["kind"] == "檔案" and r.get("created") and r.get("new_id")]
        if not targets:
            return
        finance = self.find_finance_file(spec.area) if spec.area else None
        if spec.area and not finance:
            self.log(f"  ⚠ 找不到 {spec.area}{self.new_year}財報，ATM!A2 不更新（請先跑「財務報表」）")
        for r in targets:
            self.log(f"  {r['new_name']}：{self.prev_year}目標→{self.new_year}目標；清除 "
                     f"{'、'.join(OFFICE_CLEAR_RANGES)}、清潔異動 A2:AE（含底色）"
                     + (f"；ATM!A2 → {finance['name']}" if finance else ""))
            if self.dry_run:
                continue
            meta = self.sheets.spreadsheets().get(
                spreadsheetId=r["new_id"], fields="sheets.properties(sheetId,title)",
            ).execute()
            sheet_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}

            requests: List[Dict[str, Any]] = []
            old_goal = f"{self.prev_year}目標"
            keep = set(OFFICE_KEEP_SHEETS) | {old_goal}
            removed = [t for t in sheet_ids
                       if t not in keep and not any(k in t for k in OFFICE_KEEP_CONTAINS)]
            if len(removed) < len(sheet_ids):  # 至少留一張才刪
                requests += [{"deleteSheet": {"sheetId": sheet_ids[t]}} for t in removed]
                if removed:
                    self.log(f"  刪除分頁：{'、'.join(removed)}")
                sheet_ids = {t: i for t, i in sheet_ids.items() if t not in removed}
            if old_goal in sheet_ids:
                requests.append({"updateSheetProperties": {
                    "properties": {"sheetId": sheet_ids[old_goal], "title": f"{self.new_year}目標"},
                    "fields": "title"}})
            for title, a1 in OFFICE_CLEAR_WITH_FILL:
                if title in sheet_ids:
                    requests.append({"repeatCell": {
                        "range": a1_to_grid(sheet_ids[title], a1),
                        "cell": {"userEnteredFormat": {}},
                        "fields": "userEnteredValue,userEnteredFormat.backgroundColor"}})
            if requests:
                self.sheets.spreadsheets().batchUpdate(
                    spreadsheetId=r["new_id"], body={"requests": requests},
                ).execute()

            ranges = [rng for rng in OFFICE_CLEAR_RANGES if rng.split("!")[0].strip("'") in sheet_ids]
            if ranges:
                self.sheets.spreadsheets().values().batchClear(
                    spreadsheetId=r["new_id"], body={"ranges": ranges},
                ).execute()
            if finance and "ATM" in sheet_ids:
                self._values_batch(r["new_id"], [{
                    "range": OFFICE_ATM_CELL,
                    "values": [[OFFICE_ATM_FORMULA.format(fid=finance["id"])]],
                }])

    # ---------- 財務報表專用整理 ----------
    def _values_batch(self, file_id: str, data: List[Dict[str, Any]]) -> None:
        self.sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=file_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    def _sheet_titles(self, file_id: str) -> set:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties.title",
        ).execute()
        return {s["properties"]["title"] for s in meta.get("sheets", [])}

    def finance_post_process(self, records: List[Dict[str, str]]) -> None:
        """只處理這次新建立的檔案，避免重跑時清掉新年度已輸入的資料。
        1. 各區財報清除指定範圍（FINANCE_CLEAR_RANGES）
        2. 各區財報「股東損益表_財務」A241 改 IMPORTRANGE 前一年度同區財報
        3. 新年度 review 的各區財報分頁 A1 改 IMPORTRANGE 新年度同區財報
        """
        area_files: Dict[str, Dict[str, str]] = {}
        review: Optional[Dict[str, str]] = None
        for r in records:
            if r["kind"] != "檔案" or not r.get("new_id"):
                continue
            if FINANCE_REVIEW_KEYWORD in r["new_name"]:
                review = r
                continue
            area = finance_area(r["new_name"])
            if area:
                area_files[area] = r

        for area, r in area_files.items():
            if not r.get("created"):
                continue
            clear = FINANCE_CLEAR_RANGES["台北/台中" if area in ("台北", "台中") else "其他"]
            formula = (f'=IMPORTRANGE("{r["old_id"]}","{FINANCE_PL_SHEET}!$a$1:$aa200")')
            self.log(f"  {r['new_name']}：清除 {'、'.join(clear)}；"
                     f"{FINANCE_PL_SHEET}!{FINANCE_PL_CELL} → 前一年度 {r['old_id']}；"
                     f"{'、'.join(FINANCE_YEAR_CELLS)} → {self.new_year}")
            if self.dry_run:
                continue
            titles = self._sheet_titles(r["new_id"])
            ranges = [rng for rng in clear if rng.split("!")[0].strip("'") in titles]
            if ranges:
                self.sheets.spreadsheets().values().batchClear(
                    spreadsheetId=r["new_id"], body={"ranges": ranges},
                ).execute()
            data = [{"range": cell, "values": [[self.new_year]]}
                    for cell in FINANCE_YEAR_CELLS if cell.split("!")[0].strip("'") in titles]
            if FINANCE_PL_SHEET in titles:
                data.append({"range": f"'{FINANCE_PL_SHEET}'!{FINANCE_PL_CELL}", "values": [[formula]]})
            else:
                self.log(f"  ⚠ {r['new_name']} 沒有「{FINANCE_PL_SHEET}」分頁")
            if data:
                self._values_batch(r["new_id"], data)

        if review and review.get("created"):
            # 只跑 review（或部分地區）時，其餘地區的新年度財報從 Drive 找
            for area in FINANCE_AREAS:
                if area not in area_files:
                    found = self.find_finance_file(area)
                    if found:
                        area_files[area] = {"new_id": found["id"], "new_name": found["name"]}
            data = []
            for area, r in area_files.items():
                formula = f'=IMPORTRANGE("{r["new_id"]}","{FINANCE_PL_SHEET}!$A$1:$z$500")'
                data.append({"range": f"'{area}財報'!A1", "values": [[formula]]})
                self.log(f"  {review['new_name']}：{area}財報!A1 → {r['new_name']}")
            if data and not self.dry_run:
                titles = self._sheet_titles(review["new_id"])
                data = [d for d in data if d["range"].split("!")[0].strip("'") in titles]
                if data:
                    self._values_batch(review["new_id"], data)

    def record_results(self, spec: RowSpec, records: List[Dict[str, str]]) -> None:
        now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")
        lines = [f"{now} {self.new_year}{'（dry-run）' if self.dry_run else ''}"]
        lines += [f"{r['kind']} {r['new_name']}：{r['new_id']}" for r in records]
        self.write(f"'{GENERATE_SHEET}'!{RESULT_COL}{spec.row_number}", [["\n".join(lines)]])

    def record_new_ids(self, records: List[Dict[str, str]]) -> int:
        files = {strip_year(r["old_name"]): r for r in records if r["kind"] == "檔案" and r["new_id"]}
        if not files:
            return 0
        rows = self.read(f"'{NEW_ID_SHEET}'!A1:D")
        stamp = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")
        updated = 0
        for index, row in enumerate(rows, start=1):
            old_label = (row[1] if len(row) > 1 else "").split("／")[0]
            record = files.get(strip_year(old_label))
            if not record:
                continue
            self.write(f"'{NEW_ID_SHEET}'!C{index}:E{index}",
                       [[record["new_id"], record["new_name"], stamp]])
            updated += 1
        return updated

    def run(self, rows_arg: str = "all", names: Optional[List[str]] = None,
            area: str = ALL_AREA) -> List[Dict[str, str]]:
        """rows_arg 指定列號；names 指定 A 欄名稱（例：["內勤表單"]），兩者都給時取交集；
        area 指定執行區域（全區＝不篩選）。"""
        values = self.read(f"'{GENERATE_SHEET}'!A1:H")
        row_numbers = parse_rows_arg(rows_arg, len(values))
        if names:
            wanted = set(names)
            row_numbers = [r for r in row_numbers
                           if r <= len(values) and values[r - 1] and str(values[r - 1][0]).strip() in wanted]
            if not row_numbers:
                raise RuntimeError(f"「{GENERATE_SHEET}」A 欄找不到：{'、'.join(names)}")
        all_records: List[Dict[str, str]] = []
        for row_number in row_numbers:
            row = values[row_number - 1] if row_number <= len(values) else []
            spec = parse_row(row_number, row, self.prev_year)
            if not spec:
                self.log(f"第 {row_number} 列：沒有資料夾網址，略過")
                continue
            spec = filter_spec_by_area(spec, area)
            if not spec:
                continue
            self.log(f"第 {row_number} 列：{spec.name}" + (f"（{area}）" if area != ALL_AREA else ""))
            try:
                records = self.process_row(spec)
            except Exception as exc:  # 一列失敗不影響下一列
                self.log(f"  ✗ 失敗：{exc}")
                self.write(f"'{GENERATE_SHEET}'!{RESULT_COL}{row_number}",
                           [[f"{datetime.now(TZ):%Y/%m/%d %H:%M:%S} 失敗：{exc}"]])
                continue
            self.record_results(spec, records)
            count = self.record_new_ids(records)
            self.log(f"  完成，新年度ID 回寫 {count} 列")
            all_records.extend(records)
        return all_records


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="依「生成新年度」複製新年度資料夾與檔案")
    parser.add_argument("--year", type=int, default=datetime.now(TZ).year + 1, help="新年度（預設明年）")
    parser.add_argument("--rows", default="all", help="要處理的列，例：2、2-8、2,4,6、all")
    parser.add_argument("--names", default="", help="只處理 A 欄為這些名稱的列，以逗號分隔，例：財務報表,內勤表單")
    parser.add_argument("--dry-run", action="store_true", help="只列出會做的事，不建立、不回寫")
    parser.add_argument("--fresh", action="store_true", help="已存在的新年度同名項目改名為舊版後重新生成")
    parser.add_argument("--spreadsheet-id", default=MASTER_SPREADSHEET_ID)
    args = parser.parse_args(argv)

    from services.google_auth import get_drive_service, get_sheets_service

    rollover = Rollover(get_drive_service(), get_sheets_service(), args.spreadsheet_id,
                        args.year, dry_run=args.dry_run, fresh=args.fresh)
    records = rollover.run(args.rows, names=split_list(args.names) or None)
    print(f"共 {len(records)} 筆生成紀錄")
    return 1 if any(r["new_name"] == "找不到來源" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
