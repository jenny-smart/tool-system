"""新年度盤點與切換（toolapp「新年度盤點與切換」用）。

三個動作：
1. 新年度盤點：年度參數是否已是新年度、「年度根目錄設定」每列新年度檔是否都找得到、
   哪些列可以切換根目錄，以及「換年檢查清單」還沒完成的項目。只讀（另把找檔結果寫入 M 欄）。
2. 一鍵更新年度參數：主控表「參數設定」裡跟年度有關的設定值（年度彙總列代號、名冊預設年度）改成新年度。
3. 一鍵切換根目錄：「年度根目錄設定」K 欄仍是「比對」、且新年度檔都找得到、L 欄沒有不一致的列，
   一次改成「根目錄」；有缺檔或不一致的列不動，並列出原因。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from tools.common import year_roots

PARAM_SHEET = "參數設定"
CHECKLIST_SHEET = "換年檢查清單"

# 參數設定裡要跟著換年度的項目：(執行系統, 執行功能, 設定項目)
YEAR_PARAM_ITEMS: List[Tuple[str, str, str]] = [
    ("財務管理", "年度review", "年度彙總列代號"),
    ("外場排程系統", "外場排班統計表", "名冊預設年度"),
]

_OK_CHECK_PREFIXES = ("找到", "依期別", "適用起年前")


def _cell(row: List[str], idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) else ""


# ---------- 參數設定 ----------
def year_param_rows(values: List[List[str]]) -> List[Dict[str, str]]:
    """找出參數設定裡的年度參數列（第 1 列是標題）。回傳 [{列, 項目, 目前值}]。"""
    wanted = {item: item for item in YEAR_PARAM_ITEMS}
    found: List[Dict[str, str]] = []
    for row_no, row in enumerate(values[1:], start=2):
        key = (_cell(row, 0), _cell(row, 1), _cell(row, 3))
        if key in wanted:
            found.append({"列": str(row_no), "項目": "｜".join(key), "目前值": _cell(row, 4)})
    return found


def update_year_params(sheets, spreadsheet_id: str, year: int) -> List[Dict[str, str]]:
    """把年度參數 E 欄改成 year；回傳每列的變更（已是新年度的列不寫）。"""
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{PARAM_SHEET}'!A1:E"
    ).execute().get("values", [])
    rows = year_param_rows(values)
    missing = {"｜".join(i) for i in YEAR_PARAM_ITEMS} - {r["項目"] for r in rows}
    data = []
    for r in rows:
        if r["目前值"] == str(year):
            r["結果"] = "已是新年度"
            continue
        data.append({"range": f"'{PARAM_SHEET}'!E{r['列']}", "values": [[str(year)]]})
        r["結果"] = f"{r['目前值'] or '（空白）'} → {year}"
    for name in sorted(missing):
        rows.append({"列": "", "項目": name, "目前值": "", "結果": "參數設定找不到這一列"})
    if data:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body={
            "valueInputOption": "RAW", "data": data}).execute()
    return rows


def read_param(system: str, function: str, item: str, default: str = "") -> str:
    """讀主控表「參數設定」單一值（啟用欄 FALSE 或讀不到時用 default）。"""
    try:
        from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

        values = get_sheets_service().spreadsheets().values().get(
            spreadsheetId=get_master_spreadsheet_id(), range=f"'{PARAM_SHEET}'!A1:F"
        ).execute().get("values", [])
    except Exception as exc:  # 讀不到主控表時用程式預設
        print(f"⚠ 讀取「{PARAM_SHEET}」{item} 失敗，沿用預設值：{exc}", flush=True)
        return default
    for row in values[1:]:
        if (_cell(row, 0), _cell(row, 1), _cell(row, 3)) == (system, function, item):
            if _cell(row, 5).upper() == "FALSE":
                return default
            return _cell(row, 4) or default
    return default


# ---------- 年度根目錄 ----------
def plan_root_switch(roots: year_roots.YearRoots, year: int,
                     check_rows: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """每列判斷能否切換為「根目錄」：模式是比對、新年度檔都找得到、L 欄沒有不一致。"""
    check_rows = check_rows if check_rows is not None else year_roots.check_year(roots, year)
    by_row: Dict[str, List[Dict[str, str]]] = {}
    for r in check_rows:
        by_row.setdefault(r["列"], []).append(r)
    plan = []
    for rule in roots.rules:
        items = by_row.get(str(rule.row), [])
        missing = [f"{r['地區']} {r['檔名規則']}" for r in items
                   if not r["結果"].startswith(_OK_CHECK_PREFIXES)]
        status = year_roots.row_status(rule)
        if rule.mode == year_roots.MODE_ROOT:
            verdict, reason = "已是根目錄", ""
        elif rule.mode != year_roots.MODE_COMPARE:
            verdict, reason = "不切換", f"模式是「{rule.mode}」（先改成「比對」確認）"
        elif missing:
            verdict, reason = "不切換", f"{year} 找不到：" + "、".join(missing)
        elif status == "有不一致":
            verdict, reason = "不切換", "L 欄比對有不一致（根目錄找到的檔和原設定 ID 不同）"
        else:
            verdict, reason = "可切換", "" if status == "全部一致" else "L 欄尚未比對，以新年度找檔結果為準"
        plan.append({"列": str(rule.row), "類別": rule.category, "地區": "／".join(rule.areas),
                     "模式": rule.mode, "比對狀態": status, "判斷": verdict, "原因": reason})
    return plan


def switch_ready_rows(sheets, spreadsheet_id: str, plan: List[Dict[str, str]]) -> List[int]:
    rows = [int(p["列"]) for p in plan if p["判斷"] == "可切換"]
    if rows:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body={
            "valueInputOption": "RAW",
            "data": [{"range": f"'{year_roots.ROOT_SHEET}'!K{row}", "values": [[year_roots.MODE_ROOT]]}
                     for row in rows]}).execute()
        year_roots._LOADED_AT = 0.0  # 下次重讀設定
    return rows


# ---------- 換年檢查清單 ----------
def open_checklist_items(values: List[List[str]]) -> List[Dict[str, str]]:
    """換年檢查清單 H 欄（已完成）不是 TRUE 的項目。"""
    items = []
    for row in values[1:]:
        if not _cell(row, 0) or _cell(row, 7).upper() == "TRUE":
            continue
        items.append({"序號": _cell(row, 0), "類別": _cell(row, 1), "項目": _cell(row, 2),
                      "何時做": _cell(row, 4), "備註": _cell(row, 9)})
    return items


# ---------- 整合盤點 ----------
def inventory(sheets, spreadsheet_id: str, roots: year_roots.YearRoots, year: int
              ) -> Tuple[str, List[Dict[str, str]]]:
    rows: List[Dict[str, str]] = []

    params = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{PARAM_SHEET}'!A1:E"
    ).execute().get("values", [])
    param_rows = year_param_rows(params)
    params_todo = [p for p in param_rows if p["目前值"] != str(year)]
    for p in param_rows:
        rows.append({"區塊": "參數設定", "項目": p["項目"],
                     "狀態": "OK" if p["目前值"] == str(year) else "待更新",
                     "說明": f"目前 {p['目前值'] or '（空白）'}"})

    check_rows = year_roots.check_year(roots, year)
    year_roots.write_check_column(sheets, spreadsheet_id, year, check_rows)
    plan = plan_root_switch(roots, year, check_rows)
    for p in plan:
        rows.append({"區塊": "年度根目錄", "項目": f"第 {p['列']} 列 {p['類別']}（{p['地區']}）",
                     "狀態": p["判斷"], "說明": p["原因"] or f"模式 {p['模式']}，{p['比對狀態']}"})

    checklist = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{CHECKLIST_SHEET}'!A1:J"
    ).execute().get("values", [])
    open_items = open_checklist_items(checklist)
    for item in open_items:
        rows.append({"區塊": "換年檢查清單", "項目": f"{item['序號']}. {item['項目']}",
                     "狀態": "未完成", "說明": "；".join(x for x in (item["何時做"], item["備註"]) if x)})

    count = lambda verdict: sum(1 for p in plan if p["判斷"] == verdict)  # noqa: E731
    missing_files = sum(1 for r in check_rows if r["結果"].startswith("找不到"))
    message = (
        f"{year} 新年度盤點：參數設定待更新 {len(params_todo)} 項；"
        f"年度根目錄 {len(plan)} 列（已是根目錄 {count('已是根目錄')}、可切換 {count('可切換')}、"
        f"不切換 {count('不切換')}），新年度找不到檔 {missing_files} 個（明細寫入 {year_roots.CHECK_COLUMN} 欄）；"
        f"換年檢查清單未完成 {len(open_items)} 項"
    )
    return message, rows
