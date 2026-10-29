"""清潔異動整合執行：盤點各區清潔異動 → 勾選 → 交給本機 Agent 依序執行 → 回寫系統。

規則（2026-10 與 Jenny 確認）：
  - 只處理登記日期（E 欄）2026/10/16 以後的列。
  - 每列分三類：未備妥（missing，缺資料還不能處理，例如缺匯款帳號、客人尚未付款）、
    可自動執行（actions）、需人工（manual，資料齊全但要到其他功能逐筆處理）。
    未備妥的列不執行任何動作；例外：ATM 只缺退款銀行代碼／帳號（P／Q）時仍先開折讓單，只是不退款。
    每日檢查只提醒未備妥的列。
  - 待退款：① 開折讓單（有 X 原發票、Z 未稅金額，AB 空白；ATM 不論有無 P／Q 銀行代碼帳號都可先開）
            ② 退款（AC 空白）：R＝ATM → 富邦 ATM 退款（台北／台中）；R＝信用卡 → 藍新退款；其他付款方式 → 需人工
  - VIP待退券：① 【檸檬後台】異動儲值金：依儲值金歷程頁餘額把儲值金及購物金都扣到 0
                 （退款金額 S 大於儲值金時先扣 S、再扣剩餘餘額）；第一筆 Agent 直接送出，
                 剩餘餘額那筆 Agent 填表、人工確認按送出，完成後填 M 歸零時間
              ② 開折讓單（同待退款）
              ③ 退款（M 已歸零、AB 折讓單已開才退）：R＝信用卡 → 藍新退款（各區皆同）；
                 R＝ATM → 台北／台中富邦 ATM 退款；桃園／新竹／高雄 Agent 不處理，由財務退款後填 AC
              ④ M／AB／AC 都有值 → 回寫檸檬後台，B 欄改為 VIP已退券
  - 待收款：O 發票空白 → 開立發票（同【鯨躍發票】開立發票：沿用訂單發票設定、Agent 填表、
            人工按下一步及儲存，回填 O／AA）；M 收款時間空白也先開（2026-10-29 Jenny 確認），
            但仍列為未備妥讓每日檢查提醒收款
  - 待扣儲值金／待返儲值金 → 【檸檬後台】異動儲值金
  - 待處理發票（車馬費發票／異動發票／二聯改三聯）→ 需到鯨躍發票功能逐筆確認
  - 回寫系統：B 欄為 已退款／VIP已退券／已收款／已扣儲值金／已返儲值金／已處理發票，
    且 AE 欄還不是「更新系統（B欄：同狀態）」→ 回填檸檬後台，成功後寫 AD／AE。
    B 欄還是待處理、但已在別處處理好的列（見 ready_target）也直接回寫，成功後 B 欄改成完成狀態。
  - 盤點時順手補 T（匯款金額）、Z（未稅金額）公式（只補空白格）。
執行（2026-10-29 與 Jenny 確認）：整批只排一筆本機 Agent 任務（change_order.batch_run），
  依地區逐區、每區依序：① 鯨躍（開立折讓單＋開立發票）② 退款（信用卡登入藍新、ATM 登入富邦）
  ③ 儲值金 → 回寫系統。前一步失敗就不做該區後面的步驟（回寫仍只回寫已完成的列），避免順序錯亂。
回寫完成後在 Jenny 行事曆建一筆紀錄事件（不跳提醒）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
REGIONS = ["台北", "台中", "桃園", "新竹", "高雄"]
CUTOFF = date(2026, 10, 16)
RECORD_CALENDAR_ID = "jenny@lemonclean.com.tw"

DONE_STATUSES = ("已退款", "VIP已退券", "已收款", "已扣儲值金", "已返儲值金", "已處理發票")
REFUND_STATUSES = {"待退款", "VIP待退券"}
MISSING_BANK = "缺退款銀行代碼／帳號（P／Q）"  # 只擋退款，不擋折讓單
STORED_VALUE_STATUSES = {"待扣儲值金", "待返儲值金"}
ATM_REFUND_REGIONS = {"台北", "台中"}
# 只列出、不自動處理的狀態（已完成或不需處理）
IGNORED_STATUSES = {"異動不處理", ""}

# 動作 → (顯示名稱, 本機 Agent action)
ACTIONS = {
    "invoice": ("開立發票", "cetustek.login"),
    "allowance": ("開立折讓單", "cetustek.allowance"),
    "atm_refund": ("富邦 ATM 退款", "fubon.atm_refund"),
    "credit_refund": ("藍新信用卡退款", "newebpay.refund_pending"),
    "stored_value": ("異動儲值金", "lemon.stored_value_adjustment"),
    "sync": ("回寫系統", ""),
}
# 每區依序執行：鯨躍（折讓單＋發票）→ 退款（藍新信用卡、富邦 ATM）→ 儲值金，最後回寫系統
ACTION_ORDER = ("allowance", "invoice", "credit_refund", "atm_refund", "stored_value")
BATCH_ACTION = "change_order.batch_run"

COL = {name: index for index, name in enumerate(
    "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z AA AB AC AD AE".split())}
T_FORMULA = '=IF(R{n}="ATM",IF(P{n}="012",S{n},S{n}-15),0)'
Z_FORMULA = "=S{n}/1.05"


def _cell(row: list, letter: str) -> str:
    index = COL[letter]
    return str(row[index] if index < len(row) else "").strip()


def _amount(text: str) -> str:
    cleaned = re.sub(r"[^0-9.\-]", "", str(text or ""))
    try:
        value = float(cleaned)
    except ValueError:
        return ""
    return str(int(value)) if value == int(value) else str(value)


def parse_reg_date(text: str) -> Optional[date]:
    match = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", str(text or ""))
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def synced_marker(status: str) -> str:
    return f"更新系統（B欄：{status}）"


def needs_sync(row: list) -> bool:
    status = _cell(row, "B")
    return status in DONE_STATUSES and _cell(row, "AE") != synced_marker(status)


def ready_target(row: list) -> Optional[str]:
    """B 欄還是待處理、但折讓單／退款／收款／發票／儲值金已在別處處理好的列 → 回寫後的完成狀態。"""
    status = _cell(row, "B")
    if status == "待退款" and _cell(row, "AB") and _cell(row, "AC"):
        return "已退款"
    if status == "VIP待退券" and all(_cell(row, c) for c in ("M", "AB", "AC")):
        return "VIP已退券"  # 儲值金／購物金已歸零、折讓單、退款都完成
    if status == "待收款" and all(_cell(row, c) for c in ("M", "N", "O", "AA")):
        return "已收款"  # 收款時間、金額、發票號碼、發票開立時間都有值
    if status == "待扣儲值金" and _cell(row, "M"):
        return "已扣儲值金"
    if status == "待返儲值金" and _cell(row, "AC"):
        return "已返儲值金"
    if status == "待處理發票" and _cell(row, "O"):
        return "已處理發票"
    return None


def plan_row(region: str, row_no: int, row: list) -> Optional[dict[str, Any]]:
    """單列盤點結果；不需處理（已回寫、登記日期在起算日前、無訂單編號）回傳 None。"""
    status, order_no = _cell(row, "B"), _cell(row, "G")
    if not order_no or status in IGNORED_STATUSES:
        return None
    reg_date = parse_reg_date(_cell(row, "E"))
    if reg_date is None or reg_date < CUTOFF:
        return None
    item = {
        "region": region, "row": row_no, "status": status, "detail": _cell(row, "C"),
        "order_no": order_no, "customer": _cell(row, "H"), "payway": _cell(row, "R"),
        "amount": "", "progress": [], "actions": [], "missing": [], "manual": [], "note": _cell(row, "K"),
    }
    if status in DONE_STATUSES:
        if not needs_sync(row):
            return None
        item["amount"] = _amount(_cell(row, "S") or _cell(row, "N"))
        item["actions"].append("sync")
        item["target_status"] = status
        return item

    target = ready_target(row)
    if target:
        item["amount"] = _amount(_cell(row, "S") or _cell(row, "N"))
        item["actions"].append("sync")
        item["target_status"] = target
        item["progress"].append(f"已處理好，回寫後 B 欄改為「{target}」")
        return item

    if status in REFUND_STATUSES:
        is_vip = status == "VIP待退券"
        # missing＝缺資料還不能處理（未備妥）；manual＝資料齊全但要人工或到其他功能逐筆處理
        # 顯示實際會退的金額：ATM 是 T（扣手續費後的匯款金額），信用卡等其他方式是 S（退款金額）
        is_atm = "ATM" in item["payway"].upper()
        item["amount"] = _amount(_cell(row, "T")) if is_atm and _amount(_cell(row, "T")) else _amount(_cell(row, "S"))
        if not _amount(_cell(row, "S")):
            item["missing"].append("缺退款金額（S）")
        if is_vip:
            if _cell(row, "M"):
                item["progress"].append("儲值金／購物金歸零 ✓")
            else:
                item["actions"].append("stored_value")  # 儲值金及購物金歸零
        if _cell(row, "AB"):
            item["progress"].append("折讓單 ✓")
        elif not _cell(row, "X"):
            item["missing"].append("缺原發票號碼（X）")
        elif not _amount(_cell(row, "Z")):
            item["missing"].append("缺未稅金額（Z）")
        else:
            item["actions"].append("allowance")
        if _cell(row, "AC"):
            item["progress"].append("退款 ✓")
        else:
            payway = item["payway"].upper()
            if not payway:
                item["missing"].append("缺付款方式（R）")
            elif is_vip and "ATM" in payway and region not in ATM_REFUND_REGIONS:
                item["progress"].append("退款由財務處理（完成後填 AC 退款時間）")
            elif is_vip and not (_cell(row, "M") and _cell(row, "AB")):
                item["progress"].append("儲值金歸零及折讓單完成後才退款")
            elif "ATM" in payway:
                if not re.sub(r"\D", "", _cell(row, "P")) or not re.sub(r"\D", "", _cell(row, "Q")):
                    item["missing"].append(MISSING_BANK)
                elif region not in ATM_REFUND_REGIONS:
                    item["manual"].append(f"{region} ATM 退款需人工")
                elif _amount(_cell(row, "S")):
                    item["actions"].append("atm_refund")
            elif item["payway"] == "信用卡":
                if _amount(_cell(row, "S")):
                    item["actions"].append("credit_refund")
            else:
                item["manual"].append(f"付款方式「{item['payway']}」退款需人工")
        if any(m != MISSING_BANK for m in item["missing"]):
            item["actions"] = []  # 資料不齊的列先不執行，避免只做一半
        # 只缺銀行代碼／帳號：退款本來就不會排入，折讓單（及 VIP 歸零）照常先做
        return item

    if status == "待收款":
        item["amount"] = _amount(_cell(row, "N"))
        # 發票金額取自後台訂單，不需 M／N：客人還沒付款也先開發票，缺的部分照樣提醒
        if not _cell(row, "M"):
            item["missing"].append("客人尚未付款（M 收款時間空白）")
        elif not item["amount"]:
            item["missing"].append("缺收款金額（N）")
        if not _cell(row, "O"):
            item["actions"].append("invoice")
        else:
            item["progress"].append("發票 ✓")
            if not _cell(row, "AA"):
                item["manual"].append("AA 發票開立時間空白，請確認發票後補上")
        return item

    if status in STORED_VALUE_STATUSES:
        column = "N" if status == "待扣儲值金" else "S"
        item["amount"] = _amount(_cell(row, column))
        if item["amount"]:
            item["actions"].append("stored_value")
        else:
            item["missing"].append(f"缺儲值金金額（{column}）")
        return item

    if status == "待處理發票":
        target = "開立發票" if item["detail"] == "車馬費發票" else "二聯改三聯"
        item["manual"].append(f"請到【鯨躍發票】{target}逐筆處理")
        return item

    item["manual"].append(f"狀態「{status}」需人工處理")
    return item


def formula_fixes(values: list[list]) -> list[dict[str, Any]]:
    """S 退款金額有值、T 或 Z 空白的待退款／VIP待退券列 → 補公式（只補空白格）。"""
    updates = []
    for row_no, row in enumerate(values[1:], start=2):
        if _cell(row, "B") not in REFUND_STATUSES or not _amount(_cell(row, "S")):
            continue
        reg_date = parse_reg_date(_cell(row, "E"))
        if reg_date is None or reg_date < CUTOFF:
            continue
        if not _cell(row, "T"):
            updates.append({"range": f"T{row_no}", "values": [[T_FORMULA.format(n=row_no)]]})
        if not _cell(row, "Z"):
            updates.append({"range": f"Z{row_no}", "values": [[Z_FORMULA.format(n=row_no)]]})
    return updates


def _change_order():
    from tools.memo_system import change_order

    return change_order


def scan_region(region: str, fix_formulas: bool = True, log: Callable[[str], None] = print) -> list[dict]:
    ws = _change_order().get_worksheet(region)
    values = ws.get_all_values()
    if fix_formulas:
        updates = formula_fixes(values)
        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            log(f"【{region}】已補 T／Z 公式 {len(updates)} 格")
            values = ws.get_all_values()
    items = []
    for row_no, row in enumerate(values[1:], start=2):
        item = plan_row(region, row_no, row)
        if item:
            items.append(item)
    return items


def scan(regions=None, fix_formulas: bool = True, log: Callable[[str], None] = print) -> list[dict]:
    items = []
    for region in regions or REGIONS:
        try:
            items.extend(scan_region(region, fix_formulas, log))
        except Exception as exc:
            log(f"❌ 【{region}】讀取清潔異動失敗：{exc}")
    return items


def build_plan(items: list[dict], do_sync: bool) -> list[dict[str, Any]]:
    """勾選的列 → 逐區執行計畫：[{"region", "steps": {動作: [列號]}, "sync_rows": [列號]}]（地區依 REGIONS 順序）。"""
    plan = []
    for region, rows in selected_rows_by_region(items).items():
        region_items = [item for item in items if item["region"] == region]
        steps = {}
        for action in ACTION_ORDER:
            action_rows = sorted(int(item["row"]) for item in region_items
                                 if action in item.get("selected", item["actions"]))
            if action_rows:
                steps[action] = action_rows
        if steps or do_sync:
            plan.append({"region": region, "steps": steps, "sync_rows": rows if do_sync else []})
    return plan


def queue_actions(items: list[dict], do_sync: bool, create_task: Callable, created_by: str = "清潔異動整合執行",
                  log: Callable[[str], None] = print,
                  prepare_invoices: Optional[Callable[[str, list[int]], Any]] = None) -> Optional[dict]:
    """整批只排一筆本機 Agent 任務（逐區依序執行，見 run_plan），不再每種動作各排一筆讓 Agent 同時跑。

    開立發票要先在網頁端組好發票資料（同【鯨躍發票】開立發票），由 prepare_invoices(地區, 列號) 排入 Payload。"""
    plan = build_plan(items, do_sync)
    for entry in plan:
        rows = entry["steps"].get("invoice")
        if not rows:
            continue
        try:
            if prepare_invoices is None:
                raise RuntimeError("請到【鯨躍發票】開立發票")
            prepare_invoices(entry["region"], rows)
        except Exception as exc:
            entry["steps"].pop("invoice")
            log(f"⚠️ {entry['region']} 開立發票未排入（{len(rows)} 筆）：{exc}")
    plan = [entry for entry in plan if entry["steps"] or entry["sync_rows"]]
    if not plan:
        log("沒有可執行的動作")
        return None
    task = create_task(BATCH_ACTION, {"plan": plan, "cdp_url": "http://127.0.0.1:9222"}, created_by=created_by)
    for entry in plan:
        steps = "→".join(f"{ACTIONS[a][0]} {len(r)} 筆" for a, r in entry["steps"].items())
        steps = "→".join(filter(None, [steps, "回寫系統" if entry["sync_rows"] else ""]))
        log(f"已排入：{entry['region']} {steps}")
    log(f"本機 Agent 任務：{task.get('task_id', '')}（逐區依序執行）")
    return task


def selected_rows_by_region(items: list[dict]) -> dict[str, list[int]]:
    """勾選的列依地區分組（地區依 REGIONS 順序）；回寫只看這些列。"""
    rows: dict[str, list[int]] = {}
    for item in sorted(items, key=lambda i: (REGIONS.index(i["region"]), int(i["row"]))):
        rows.setdefault(item["region"], []).append(int(item["row"]))
    return rows


def _login_session(region: str, ui_logger=None):
    """用該地區自己的後台帳密登入（比照清潔異動每日檢查），一律正式站。"""
    from tools.lemon_backend.config import get_credentials
    from tools.memo_system import memo

    creds = get_credentials(region, required=False)
    if creds is None:
        raise RuntimeError(f"{region} 後台帳密未設定（需要對應的 *_EMAIL / *_PASSWORD）")
    memo.set_env("prod")
    _change_order().set_env("prod")
    memo.set_runtime_credentials(creds.email, creds.password)
    return memo.login(ui_logger=ui_logger)


def sync_region(region: str, log: Callable[[str], None] = print,
                rows: Optional[set[int]] = None) -> dict[str, list]:
    """回寫該區「已完成但 AE 未更新系統」的列（rows 有給時只看這些列）；
    回傳 {"done": [列號], "failed": [(列號, 錯誤)]}。"""
    change_order = _change_order()
    ws = change_order.get_worksheet(region)
    values = ws.get_all_values()
    targets = [(row_no, row, item["target_status"]) for row_no, row in enumerate(values[1:], start=2)
               if (rows is None or row_no in rows)
               and (item := plan_row(region, row_no, row)) and item["actions"] == ["sync"]]
    result: dict[str, list] = {"done": [], "failed": []}
    if not targets:
        return result
    session = _login_session(region, ui_logger=log)
    try:
        _sync_targets(change_order, ws, region, targets, session, result, log)
    finally:
        _logout_session(change_order, session, region, log)
    return result


def _logout_session(change_order, session, region: str, log: Callable[[str], None]) -> None:
    """回寫完一律登出這區檸檬後台（requests 連線，不影響 Chrome）；登出失敗只提醒。"""
    try:
        session.get(f"{change_order.BASE_URL}/logout", timeout=20)
        log(f"【{region}】檸檬後台已登出")
    except Exception as exc:
        log(f"⚠️ 【{region}】檸檬後台登出失敗：{exc}")


def _sync_targets(change_order, ws, region: str, targets: list, session, result: dict,
                  log: Callable[[str], None]) -> None:
    for row_no, row, status in targets:
        # 已處理好但 B 欄仍是待處理的列：以完成狀態回填後台，成功後才把 B 欄改成完成狀態
        sync_item = {"sheet_row": row_no, "kind": "refund" if "退" in status or "返" in status else "charge",
                     "status": status, "order_no": _cell(row, "G"), "raw": row}
        try:
            change_order.sync_one_to_purchase_edit(sync_item, session=session, ui_logger=log)
            if _cell(row, "B") != status:
                ws.update_acell(f"B{row_no}", status)
            change_order.mark_sheet_row_done(region, row_no, status, ui_logger=log)
            result["done"].append(row_no)
        except Exception as exc:
            result["failed"].append((row_no, str(exc)))
            log(f"❌ 【{region}】第 {row_no} 列 {sync_item['order_no']} 回寫失敗：{exc}")


def build_record_text(results: dict[str, dict], today: date) -> tuple[str, str]:
    done_total = sum(len(r["done"]) for r in results.values())
    lines = []
    for region in REGIONS:
        result = results.get(region)
        if not result or not (result["done"] or result["failed"]):
            continue
        if result["done"]:
            lines.append(f"{region}：{len(result['done'])}筆，列數：{'，'.join(map(str, result['done']))}")
        if result["failed"]:
            lines.append(f"{region} 失敗：" + "；".join(f"第{row}列 {err}" for row, err in result["failed"]))
    title = f"本日清潔異動已回寫：{done_total} 筆"
    return title, f"{today:%Y/%m/%d}\n" + "\n".join(lines)


def record_to_calendar(results: dict[str, dict], now: Optional[datetime] = None, log=print):
    """回寫完成後在 Jenny 行事曆留紀錄（不跳提醒）；沒有回寫任何列就不建。"""
    if not any(r["done"] or r["failed"] for r in results.values()):
        return None
    now = now or datetime.now(TW_TZ)
    title, body = build_record_text(results, now.date())
    from tools.orders_system import calendar_notify

    event = calendar_notify.create_notify_event(summary=title, description=body, start=now,
                                                calendar_id=RECORD_CALENDAR_ID, attendee="", reminder_minutes=[])
    log(f"已在 {RECORD_CALENDAR_ID} 行事曆留紀錄：{title}")
    return event


def sync(regions=None, log: Callable[[str], None] = print,
         rows: Optional[dict[str, list[int]]] = None) -> dict[str, dict]:
    """rows＝{地區: [列號]}：只回寫勾選的列；未給時回寫該區所有符合條件的列。"""
    results = {}
    for region in regions or REGIONS:
        try:
            only = set(rows.get(region, [])) if rows is not None else None
            results[region] = sync_region(region, log, only)
            log(f"【{region}】回寫 {len(results[region]['done'])} 筆，失敗 {len(results[region]['failed'])} 筆")
        except Exception as exc:
            results[region] = {"done": [], "failed": [(0, str(exc))]}
            log(f"❌ 【{region}】回寫失敗：{exc}")
    try:
        record_to_calendar(results, log=log)
    except Exception as exc:
        log(f"⚠️ 行事曆紀錄建立失敗：{exc}")
    return results


def _step_command(action: str, region: str, rows: list[int], cdp_url: str) -> list[str]:
    from tools.local_agent import ACTION_HANDLERS

    return ACTION_HANDLERS[ACTIONS[action][1]]({"area": region, "selected_rows": rows, "cdp_url": cdp_url})


def _run_step(action: str, region: str, rows: list[int], cdp_url: str) -> int:
    """在同一個 Agent 任務內依序執行各步驟子程式；輸出直接接到本任務 Log。"""
    sys.stdout.flush()
    return subprocess.run(_step_command(action, region, rows, cdp_url)).returncode


def run_plan(plan: list[dict], log: Callable[[str], None] = print, cdp_url: str = "http://127.0.0.1:9222",
             run_step: Callable[[str, str, list[int], str], int] = _run_step) -> int:
    """逐區依序：鯨躍（折讓單→發票）→ 藍新／富邦退款 → 儲值金 → 回寫系統。
    某一步失敗就不做該區後面的步驟，但仍回寫該區已完成的列；最後留一筆行事曆紀錄。"""
    failed = False
    results: dict[str, dict] = {}
    for entry in plan:
        region = entry["region"]
        for action in ACTION_ORDER:
            rows = entry["steps"].get(action)
            if not rows:
                continue
            log(f"===== 【{region}】{ACTIONS[action][0]}：第 {'、'.join(map(str, rows))} 列 =====")
            try:
                code = run_step(action, region, rows, cdp_url)
            except Exception as exc:
                log(f"❌ 【{region}】{ACTIONS[action][0]}無法執行：{exc}")
                code = 1
            if code != 0:
                failed = True
                log(f"❌ 【{region}】{ACTIONS[action][0]}失敗（exit {code}），本區後面的步驟先不做")
                break
        if entry.get("sync_rows"):
            log(f"===== 【{region}】回寫系統 =====")
            try:
                results[region] = sync_region(region, log, set(int(r) for r in entry["sync_rows"]))
            except Exception as exc:
                results[region] = {"done": [], "failed": [(0, str(exc))]}
            log(f"【{region}】回寫 {len(results[region]['done'])} 筆，失敗 {len(results[region]['failed'])} 筆")
            failed = failed or bool(results[region]["failed"])
    try:
        record_to_calendar(results, log=log)
    except Exception as exc:
        log(f"⚠️ 行事曆紀錄建立失敗：{exc}")
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="清潔異動整合執行：逐區依序執行／回寫系統")
    parser.add_argument("command", choices=["sync", "run"])
    parser.add_argument("--regions", default=",".join(REGIONS))
    parser.add_argument("--rows", default="", help='JSON：{"台北": [238, 389]}，只回寫這些列')
    parser.add_argument("--plan", default="", help="run 用：build_plan 產生的 JSON")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = parser.parse_args(argv)
    if args.command == "run":
        log = lambda text: print(text, flush=True)  # noqa: E731
        return run_plan(json.loads(args.plan), log=log, cdp_url=args.cdp_url)
    regions = [r for r in args.regions.split(",") if r]
    rows = json.loads(args.rows) if args.rows else None
    results = sync(regions, rows=rows)
    return 1 if any(r["failed"] for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
