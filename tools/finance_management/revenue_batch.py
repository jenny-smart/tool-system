"""【整合執行】財報營收：salary-system 金流 ⑥～⑫＋營收總表貼財報。"""
from __future__ import annotations

import importlib
import __future__
import os
import re
import sys
from pathlib import Path
from typing import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
SALARY_MASTER_ID = "1GdW3FSZ0s3TGeYiNx3JtYvED_RRfJjiFYwLFeYHZ1hA"
STEPS = (
    ("convert", "⑥ 金流對帳轉檔"),
    ("refund", "⑦ 搬運退款＋預收"),
    ("invoice", "⑧ 搬運發票＋藍新"),
    ("atm", "⑨ 搬運ATM"),
    ("mark", "⑪ 立帳標記"),
    ("export", "⑫ 搬入營收總表"),
    ("statement", "營收總表→財報（財務／現金當月預估）"),
)
AUDIT_SHEET = "財報營收盤點"
AUDIT_HEADERS = ["完成時間", "地區", "期別", "功能", "狀態", "說明"]
LEGACY_TASKS = {
    "convert": ["期別發票解壓縮", "期別發票轉檔", "期別已退款全部加收轉檔", "期別已退款全部退款轉檔",
                "期別預收轉檔", "期別藍新收款轉檔", "期別藍新退款轉檔"],
    "refund": ["複製已退款全部加收", "複製已退款全部退款", "複製預收"],
    "invoice": ["複製發票", "複製藍新收款", "複製藍新退款"],
}


def normalize_period(period: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{4})(0[1-9]|1[0-2])(?:-([12]))?", str(period or "").strip())
    if not match:
        raise ValueError("期別請輸入 YYYYMM、YYYYMM-1 或 YYYYMM-2")
    month = match.group(1) + match.group(2)
    return (f"{month}-{match.group(3)}" if match.group(3) else f"{month}-2", month)


def _salary_module():
    """沿用 salary-system 已驗證的金流流程；可用 SALARY_SYSTEM_PATH 指定位置。"""
    try:
        return importlib.import_module("modules.payment_reconciliation")
    except ModuleNotFoundError:
        configured = os.getenv("SALARY_SYSTEM_PATH", "").strip()
        root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[3] / "salary-system"
        if not (root / "modules" / "payment_reconciliation.py").exists():
            raise RuntimeError("找不到 salary-system；請設定 SALARY_SYSTEM_PATH")
        sys.path.insert(0, str(root))
        # salary-system 的 drive_helper 使用 3.10 型別語法；Tools App 本機仍可能是 3.9。
        if sys.version_info < (3, 10):
            import types
            importlib.import_module("modules")
            importlib.import_module("modules.auth")
            importlib.import_module("modules.period_utils")
            name = "modules.drive_helper"
            sys.modules.pop(name, None)
            helper = types.ModuleType(name)
            helper.__file__ = str(root / "modules" / "drive_helper.py")
            helper.__package__ = "modules"
            sys.modules[name] = helper
            source = (root / "modules" / "drive_helper.py").read_text(encoding="utf-8")
            exec(compile(source, helper.__file__, "exec", flags=__future__.annotations.compiler_flag), helper.__dict__)
        return importlib.import_module("modules.payment_reconciliation")


def region_settings(service=None) -> dict[str, dict[str, str]]:
    from tools.common.config_loader import get_sheets_service

    service = service or get_sheets_service()
    rows = service.spreadsheets().values().get(
        spreadsheetId=SALARY_MASTER_ID, range="'地區設定'!A2:J"
    ).execute().get("values", [])
    result = {}
    for row in rows:
        row = list(row) + [""] * (10 - len(row))
        area = str(row[0]).strip()
        if area:
            result[area] = {"root_folder_id": str(row[1]).strip(), "allowance_id": str(row[2]).strip(),
                            "revenue_summary_id": str(row[9]).strip()}
    return result


def _ensure_audit_sheet(service) -> None:
    meta = service.spreadsheets().get(spreadsheetId=SALARY_MASTER_ID,
                                      fields="sheets.properties.title").execute()
    if AUDIT_SHEET not in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        service.spreadsheets().batchUpdate(spreadsheetId=SALARY_MASTER_ID, body={
            "requests": [{"addSheet": {"properties": {"title": AUDIT_SHEET}}}]
        }).execute()
    service.spreadsheets().values().update(
        spreadsheetId=SALARY_MASTER_ID, range=f"'{AUDIT_SHEET}'!A1:F1", valueInputOption="RAW",
        body={"values": [AUDIT_HEADERS]}).execute()


def record_status(area: str, period: str, step: str, status: str, detail: str = "", service=None) -> None:
    from tools.common.config_loader import get_sheets_service

    service = service or get_sheets_service()
    _ensure_audit_sheet(service)
    service.spreadsheets().values().append(
        spreadsheetId=SALARY_MASTER_ID, range=f"'{AUDIT_SHEET}'!A:F",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [[
            datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M:%S"), area, period,
            step, status, detail[:500],
        ]]}).execute()


def _legacy_done(values: list[list], period: str, step: str) -> bool:
    tasks = LEGACY_TASKS.get(step)
    if not tasks or not values:
        return False
    header = [str(v).strip() for v in values[0]]
    if period not in header:
        return False
    time_col = header.index(period) + 1
    by_task = {str(row[0]).strip(): row for row in values[2:] if row}
    return all(task in by_task and time_col < len(by_task[task]) and str(by_task[task][time_col]).strip()
               for task in tasks)


def scan(period: str, areas: list[str], service=None, audit_values=None,
         legacy_values=None) -> list[dict]:
    """盤點地區×功能；最新成功紀錄為完成，最新失敗或無紀錄為未完成。"""
    from tools.common.config_loader import get_sheets_service

    salary_period, _month = normalize_period(period)
    service = service or get_sheets_service()
    if audit_values is None:
        try:
            audit_values = service.spreadsheets().values().get(
                spreadsheetId=SALARY_MASTER_ID, range=f"'{AUDIT_SHEET}'!A2:F").execute().get("values", [])
        except Exception:
            audit_values = []
    latest = {}
    for row in audit_values:
        row = list(row) + [""] * (6 - len(row))
        if row[2] == salary_period:
            latest[(row[1], row[3])] = row
    results = []
    labels = dict(STEPS)
    for area in areas:
        if legacy_values is not None:
            old = legacy_values.get(area, [])
        else:
            try:
                old = service.spreadsheets().values().get(
                    spreadsheetId=SALARY_MASTER_ID, range=f"'{area}'!A:ZZ").execute().get("values", [])
            except Exception:
                old = []
        for step, label in STEPS:
            record = latest.get((area, step))
            if record:
                done = record[4] == "完成"
                note = f"{record[0]}：{record[4]}" + (f"；{record[5]}" if record[5] else "")
            elif _legacy_done(old, salary_period, step):
                done, note = True, "salary-system 原執行紀錄完整"
            else:
                done, note = False, "尚無完整完成紀錄"
            results.append({"area": area, "step": step, "label": label, "done": done,
                            "status": "✅ 完成" if done else "❌ 未完成", "note": note})
    return results


def _open_sheet(file_id: str):
    import gspread
    from tools.common.google_auth import get_google_credentials

    return gspread.authorize(get_google_credentials()).open_by_key(file_id)


def _wire_google(module) -> None:
    """讓移入 Tools App 的流程統一使用本系統 Jenny OAuth。"""
    from tools.common.google_auth import get_drive_service, get_google_credentials

    module.get_drive_service = get_drive_service
    module.get_credentials = get_google_credentials
    module.open_spreadsheet = _open_sheet
    drive_helper = sys.modules.get("modules.drive_helper")
    sheet_helper = sys.modules.get("modules.sheet_helper")
    if drive_helper:
        drive_helper.get_jenny_drive_service = get_drive_service
    if sheet_helper:
        sheet_helper.get_gspread_client = lambda: __import__("gspread").authorize(get_google_credentials())


def _mark_accounts(module, cfg: dict, period: str, area: str, log) -> dict:
    if hasattr(module, "mark_reconciliation_accounts"):
        return module.mark_reconciliation_accounts(cfg["root_folder_id"], period, area, log)
    reconciliation_id = module._get_period_file_id(cfg["root_folder_id"], period, "金流對帳", area)
    ws = _open_sheet(reconciliation_id).worksheet("金流對帳")
    managed = {"清潔本月", "清潔預收", "水洗本月", "水洗預收", "儲值金", "家電本月", "家電預收"}
    month = period[:4] + "/" + period[4:6]
    updates, counts = [], {}
    for row_no, row in enumerate(ws.get(f"A2:H{max(ws.row_count, 2)}") or [], start=2):
        row += [""] * (8 - len(row)); current, service, day = str(row[0]).strip(), str(row[4]).strip(), row[7]
        service_month = module._extract_year_month(day)
        suffix = "本月" if service_month == month else "預收"
        mark = ("儲值金" if "儲值金" in service else f"水洗{suffix}" if "3水洗" in service else
                f"家電{suffix}" if "4家電" in service else f"清潔{suffix}" if service == "1專業清潔" else None)
        new = mark or ("" if current in managed else current)
        if new != current:
            updates.append({"range": f"A{row_no}", "values": [[new]]})
        if mark:
            counts[mark] = counts.get(mark, 0) + 1
    module._batch_formula_updates(ws, updates)
    log(f"🏷️ 【{area}】立帳標記 {sum(counts.values())} 筆")
    return {"count": sum(counts.values()), "counts": counts, "updated": len(updates)}


def _export(module, cfg: dict, period: str, area: str, log) -> dict:
    if hasattr(module, "export_reconciliation_to_revenue"):
        return module.export_reconciliation_to_revenue(cfg["root_folder_id"], cfg["revenue_summary_id"], period, area, log)
    target_id = cfg.get("revenue_summary_id", "")
    if not target_id:
        raise ValueError("salary-system 地區設定 J 欄尚未填營收總表 ID")
    source_id = module._get_period_file_id(cfg["root_folder_id"], period, "金流對帳", area)
    data = _open_sheet(source_id).worksheet("金流對帳").get("A1:BJ", value_render_option="UNFORMATTED_VALUE") or []
    target = _open_sheet(target_id)
    title = period[:6]
    try:
        ws = target.worksheet(title); ws.batch_clear(["A:BJ"])
    except Exception as exc:
        if exc.__class__.__name__ != "WorksheetNotFound":
            raise
        ws = target.add_worksheet(title=title, rows=max(len(data) + 100, 1000), cols=62)
    if ws.row_count < len(data):
        ws.add_rows(len(data) - ws.row_count + 100)
    ws.update("A1", data, value_input_option="RAW")
    for col in ("C", "D", "H"):
        ws.format(f"{col}:{col}", {"numberFormat": {"type": "DATE", "pattern": "yyyy/mm/dd"}})
    log(f"✅ 【{area}】搬入營收總表「{title}」{len(data)} 列")
    return {"sheet": title, "count": len(data)}


def run(period: str, areas: list[str], steps: list[str], log: Callable[[str], None] = print,
        module=None, settings=None, audit_writer=record_status) -> list[str]:
    salary_period, month = normalize_period(period)
    if not areas:
        raise ValueError("請至少選一個地區")
    module = module or _salary_module()
    if module.__name__.startswith("modules."):
        _wire_google(module)
    settings = settings or region_settings()
    failures = []
    for area in areas:
        cfg = settings.get(area, {})
        if not cfg.get("root_folder_id"):
            failures.append(f"{area}：salary-system 地區設定缺少根目錄 ID"); continue
        for step in steps:
            try:
                if step == "convert": module.convert_payment_file(cfg["root_folder_id"], salary_period, area, log)
                elif step == "refund": module.move_refund_and_prepaid(cfg["root_folder_id"], salary_period, area, log)
                elif step == "invoice": module.move_invoice_and_bluenew(cfg["root_folder_id"], salary_period, area, log)
                elif step == "atm": module.move_atm_from_allowance(cfg.get("allowance_id", ""), cfg["root_folder_id"], salary_period, area, log)
                elif step == "mark": _mark_accounts(module, cfg, salary_period, area, log)
                elif step == "export": _export(module, cfg, salary_period, area, log)
                elif step == "statement":
                    from tools.finance_management.revenue_to_statement import sync_area
                    sync_area(area, month, log=log)
                try:
                    audit_writer(area, salary_period, step, "完成")
                except Exception as audit_exc:
                    log(f"⚠️ 【{area}】功能已完成，但盤點紀錄寫入失敗：{audit_exc}")
                log(f"✅ 【{area}】{dict(STEPS)[step]}完成")
            except Exception as exc:
                message = f"{area}／{dict(STEPS).get(step, step)}：{exc}"
                try:
                    audit_writer(area, salary_period, step, "失敗", str(exc))
                except Exception as audit_exc:
                    log(f"⚠️ 盤點紀錄寫入失敗：{audit_exc}")
                failures.append(message); log(f"❌ {message}")
    return failures
