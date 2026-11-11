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
  - 待處理發票（2026-11 Jenny：不再需人工）：車馬費發票 → 開立發票（同待收款，金額取 J 欄「車馬費 $金額」）；
    異動發票／二聯改三聯（C 或 K 有「改三聯」）→ 二聯改三聯（作廢或全額折讓原票＋開新三聯，同【鯨躍發票】二聯改三聯）；
    K 有「修正發票金額$金額」（不論 C 欄）→ 修正發票金額（同二聯改三聯流程：原票全額折讓＋開新發票，
    原發票設定照舊、只改金額；舊金額以鯨躍原發票可折讓額核對 S 欄，新金額＝K 欄並核對後台訂單金額）
  - 回寫系統：B 欄為 已退款／VIP已退券／已收款／已扣儲值金／已返儲值金／已處理發票，
    且 AE 欄還不是「更新系統（B欄：同狀態）」→ 回填檸檬後台，成功後寫 AD／AE。
    B 欄還是待處理、但已在別處處理好的列（見 ready_target）也直接回寫，成功後 B 欄改成完成狀態。
  - 未完全完成也先回寫（2026-10-29 Jenny 確認）：待收款已開發票（O）、待退款／VIP待退券已開折讓單或已退款（AB／AC），且 AE 還不是「更新系統（B欄：同狀態）」→ 以目前狀態回寫已完成的欄位，
    B 欄不變；之後完成時再回寫一次。待扣／待返儲值金回寫會直接改成完成狀態，不做部分回寫。
  - 盤點時順手補公式：待退款／VIP待退券 Z（未稅金額）不是公式就帶 =S{n}/1.05；T（匯款金額）S 有值且空白才補。
  - 盤點時自動補齊（2026-11-12 Jenny）：除 ATM 待退款的 P／Q 外，缺的資料自檸檬後台查詢補上空白格（見 fill_from_backend）；
    待退款／待收款且 R＝ATM → 同步補進同地區 ATM 工作表待配對區（同訂單同金額不重複，見 sync_atm_sheet）。
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
RECORD_CALENDAR_ID = "jenny@hers.com.tw"

DONE_STATUSES = ("已退款", "VIP已退券", "已收款", "已扣儲值金", "已返儲值金", "已處理發票")
REFUND_STATUSES = {"待退款", "VIP待退券"}
MISSING_BANK = "缺退款銀行代碼／帳號（P／Q）"  # 只擋退款，不擋折讓單
STORED_VALUE_STATUSES = {"待扣儲值金", "待返儲值金"}
ATM_REFUND_REGIONS = {"台北", "台中"}
# 只列出、不自動處理的狀態（已完成或不需處理）
IGNORED_STATUSES = {"異動不處理", ""}
# 未完全完成也可先回寫目前進度的狀態（回寫後 B 欄不變）
PARTIAL_SYNC_STATUSES = {"待收款", "待退款", "VIP待退券"}

# 動作 → (顯示名稱, 本機 Agent action)
ACTIONS = {
    "invoice": ("開立發票", "cetustek.login"),
    "allowance": ("開立折讓單", "cetustek.allowance"),
    "atm_refund": ("富邦 ATM 退款", "fubon.atm_refund"),
    "credit_refund": ("藍新信用卡退款", "newebpay.refund_pending"),
    "stored_value": ("異動儲值金", "lemon.stored_value_adjustment"),
    "triplicate": ("二聯改三聯", "cetustek.triplicate"),
    "correction": ("修正發票金額", "cetustek.triplicate"),  # 同二聯改三聯 runner（原票全額折讓→開新票）
    "sync": ("回寫系統", ""),
}
# 每區依序執行：鯨躍（折讓單＋發票＋二聯改三聯）→ 退款（藍新信用卡、富邦 ATM）→ 儲值金，最後回寫系統
ACTION_ORDER = ("allowance", "invoice", "triplicate", "correction", "credit_refund", "atm_refund", "stored_value")
# 鯨躍步驟：同一區沿用同一次登入，全部做完才登出（2026-11 Jenny：不要登出再登入）
CETUSTEK_ACTIONS = ("allowance", "invoice", "triplicate", "correction")
REISSUE_ACTIONS = ("triplicate", "correction")  # 原票處理＋開新票，每列各一個計畫
CETUSTEK_LOGOUT = "cetustek_logout"
TRIPLICATE_DETAILS = ("異動發票", "二聯轉三聯", "二聯改三聯")
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


def row_orders(row: list) -> list[str]:
    """合併開立要回寫發票號碼的訂單：G 欄原訂單＋J／K 欄列出的所有訂單（2026-11-05 Jenny）；不是合併開立只有 G 欄。"""
    from tools.invoice_center.merged import MERGED_INVOICE_DETAILS, merged_order_numbers

    return merged_order_numbers(_cell(row, "G"), _cell(row, "J"), _cell(row, "K"),
                                merged=_cell(row, "C") in MERGED_INVOICE_DETAILS)


def row_synced_marker(row: list, status: str) -> str:
    """合併開立（多張訂單）回寫後 AE 會加「｜合併N張」；舊的只回寫 G 欄那張的列沒有這段，會重新列出補回寫。"""
    count = len(row_orders(row))
    return synced_marker(status) + (f"｜合併{count}張" if count >= 2 else "")


def needs_sync(row: list) -> bool:
    status = _cell(row, "B")
    if status not in DONE_STATUSES:
        return False
    # 合併開立的列（G 多張或 J／K 列出訂單）AE 要有「｜合併N張」才算每張都回寫過；
    # 舊標記可能是 G 欄補齊訂單前、只回寫一張時留下的，重新列出補回寫（重寫同一發票號碼不影響）
    return _cell(row, "AE") != row_synced_marker(row, status)


def _mark_merged(item: dict, orders: list[str]) -> None:
    """合併開立：開完發票一定接著回寫，G 欄原訂單＋J／K 欄所有訂單都寫發票號碼及財務備註。"""
    item["merged_orders"] = list(orders)
    item["progress"].append(f"合併開立：開立後回寫 {len(orders)} 張訂單發票號碼（{'、'.join(orders)}）")


def needs_invoice_after_paid(row: list) -> bool:
    """已收款、O 欄還沒有發票，而且是異動發票／合併開立（G 欄多張訂單）→ 要先開發票。"""
    from tools.invoice_center.merged import MERGED_INVOICE_DETAILS

    if _cell(row, "B") != "已收款" or _cell(row, "O"):
        return False
    detail = _cell(row, "C")
    return detail == "異動發票" or detail in MERGED_INVOICE_DETAILS or len(row_orders(row)) >= 2


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


def partial_sync_due(row: list) -> bool:
    """還沒完成、但已有進度且尚未以目前狀態回寫過的列 → 先回寫目前進度。"""
    status = _cell(row, "B")
    if status not in PARTIAL_SYNC_STATUSES or _cell(row, "AE") in (synced_marker(status), row_synced_marker(row, status)):
        return False
    if status == "待收款":
        return bool(_cell(row, "O"))
    # 退款類回寫只寫折讓單／退款欄位（VIP 的 M 歸零時間不會寫入後台），有 AB／AC 才回寫
    return bool(_cell(row, "AB") or _cell(row, "AC"))


def _add_partial_sync(item: dict, row: list) -> None:
    """B 欄還沒完成的列一律留在盤點清單；已先回寫過的標示出來，完成後要再回寫，避免漏掉。"""
    if partial_sync_due(row):
        item["actions"].append("sync")
        item["target_status"] = item["status"]
        item["progress"].append("先回寫目前進度，完成後再回寫一次")
    elif _cell(row, "AE") in (synced_marker(item["status"]), row_synced_marker(row, item["status"])):
        when = _cell(row, "AD")
        item["progress"].append(f"已先回寫目前進度{f'（{when}）' if when else ''}，B 欄完成後需再回寫")


def fare_amount(text: str) -> str:
    """J 欄「車馬費 $300」「車馬費發票$200」→ "300"／"200"；只有數字時直接用；找不到回傳空字串。"""
    from tools.invoice_center.fare import fare_amount as parse

    amount = parse(text)
    return str(amount) if amount > 0 else ""


def triplicate_blocker(row: list, row_no: int) -> str:
    """二聯改三聯還不能執行的原因（空字串＝可以執行）。檢查同【鯨躍發票】二聯改三聯。"""
    from tools.invoice_center.triplicate import INVOICE_RE, candidate, parse_requirements

    item = candidate(list(row), row_no)
    if item is None:
        return "K 欄或 C 欄沒有寫「改三聯」，不知道要怎麼處理這張發票"
    try:
        parse_requirements(item["note"])
    except ValueError as exc:
        return str(exc)
    if not INVOICE_RE.fullmatch(item["old_invoice"]) or "二聯" not in item["old_type"]:
        return "X 欄原發票號碼或 Y 欄二聯式資料不完整"
    return ""


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
    if status == "已收款" and needs_invoice_after_paid(row):
        # 2026-11 Jenny：已收款但還沒開發票（O 空白）的異動發票／合併開立 → 先開立發票，開完再回寫
        orders = row_orders(row)
        item["amount"] = _amount(_cell(row, "N")) or (f"{len(orders)} 張訂單" if len(orders) >= 2 else "")
        item["actions"].append("invoice")
        item["progress"].append("已收款、尚未開發票：先開立發票" + ("（合併開立，以第一張訂單資料）" if len(orders) >= 2 else ""))
        if len(orders) >= 2:
            _mark_merged(item, orders)
        return item
    if status in DONE_STATUSES:
        if not needs_sync(row):
            return None
        item["amount"] = _amount(_cell(row, "S") or _cell(row, "N"))
        item["actions"].append("sync")
        item["target_status"] = status
        orders = row_orders(row)
        if len(orders) >= 2 and _cell(row, "AE") == synced_marker(status):
            item["progress"].append(f"合併開立：之前沒有每張訂單都回寫，補回寫 {len(orders)} 張訂單的發票號碼")
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
        _add_partial_sync(item, row)
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
            if len(orders := row_orders(row)) >= 2:
                _mark_merged(item, orders)
        else:
            item["progress"].append("發票 ✓")
            if not _cell(row, "AA"):
                item["manual"].append("AA 發票開立時間空白，請確認發票後補上")
        _add_partial_sync(item, row)
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
        if item["detail"] == "車馬費發票":
            # 2026-11 Jenny：車馬費發票直接開立發票（同待收款流程，金額只取 J 欄「車馬費 $金額」），
            # Agent 填表、人工按下一步及儲存
            item["amount"] = fare_amount(_cell(row, "J"))
            if not item["amount"]:
                item["missing"].append("J 欄找不到車馬費金額（要寫「車馬費 $300」或「車馬費發票$300」）")
            else:
                item["actions"].append("invoice")
            return item
        from tools.invoice_center.merged import MERGED_INVOICE_DETAILS
        from tools.invoice_center.triplicate import correction_blocker, correction_candidate, is_correction

        if is_correction(row):
            # 2026-11 Jenny：修正發票金額＝原票全額折讓 → 以原發票設定開修正金額的新發票（同二聯改三聯流程）
            source = correction_candidate(row, row_no)
            item["amount"] = (source or {}).get("new_total", "")
            reason = correction_blocker(source)
            if reason:
                item["missing"].append(reason)
            else:
                item["actions"].append("correction")
                item["progress"].append(f"原發票 {source['old_invoice']} 全額折讓 ${source['old_total']} → 新發票 ${source['new_total']}")
            return item
        orders = row_orders(row)
        is_triplicate = item["detail"] in TRIPLICATE_DETAILS and re.search(
            r"(?:改|轉)\s*三聯", f"{item['detail']} {_cell(row, 'K')}")
        if item["detail"] in MERGED_INVOICE_DETAILS or (len(orders) >= 2 and not is_triplicate):
            # 2026-11 Jenny：合併開立發票——以第一張訂單的發票資料開一張（品項＝各訂單合計），回寫時每張訂單都寫發票號碼
            if len(orders) < 2:
                item["missing"].append("合併開立發票：G／J／K 欄要有兩張以上訂單編號")
            else:
                item["amount"] = f"{len(orders)} 張訂單"
                item["actions"].append("invoice")
                _mark_merged(item, orders)
            return item
        if item["detail"] in TRIPLICATE_DETAILS:
            # 2026-11 Jenny：二聯改三聯直接執行（作廢或全額折讓原票 → 開新三聯發票），不再到【鯨躍發票】逐筆處理
            reason = triplicate_blocker(row, row_no)
            if reason:
                item["missing"].append(reason)
            else:
                item["actions"].append("triplicate")
            return item
        item["manual"].append(f"「{item['detail'] or '空白'}」沒有對應的自動處理方式，請確認 C 欄細項")
        return item

    item["manual"].append(f"狀態「{status}」需人工處理")
    return item


def formula_fixes(values: list[list], z_formulas: Optional[list[list]] = None) -> list[dict[str, Any]]:
    """待退款／VIP待退券列補公式：
    - Z 未稅金額：只要不是公式（空白或手打數字）就帶 =S{n}/1.05（2026-11 Jenny，S 還沒填也先帶）
    - T 匯款金額：S 有值且 T 空白才補
    z_formulas：Z 欄以公式讀出的值（ws.get("Z1:Z", value_render_option="FORMULA")）；沒給時只補空白格。"""
    updates = []
    for row_no, row in enumerate(values[1:], start=2):
        if _cell(row, "B") not in REFUND_STATUSES:
            continue
        reg_date = parse_reg_date(_cell(row, "E"))
        if reg_date is None or reg_date < CUTOFF:
            continue
        if z_formulas is not None:
            z = z_formulas[row_no - 1] if row_no - 1 < len(z_formulas) else []
            z_text = str(z[0]).strip() if z else ""
            z_missing = not z_text.startswith("=")
        else:
            z_missing = not _cell(row, "Z")
        if z_missing:
            updates.append({"range": f"Z{row_no}", "values": [[Z_FORMULA.format(n=row_no)]]})
        if _amount(_cell(row, "S")) and not _cell(row, "T"):
            updates.append({"range": f"T{row_no}", "values": [[T_FORMULA.format(n=row_no)]]})
    return updates


def _change_order():
    from tools.memo_system import change_order

    return change_order


# ============================================================
# 盤點自動補齊（2026-11-12 Jenny）：除 ATM 待退款的 P／Q（銀行代碼／帳號）外，
# 缺的資料都可自檸檬後台查詢 → 盤點時只補空白格（不覆蓋人工資料），欄位對照 order-system 建立異動：
#   後台訂單列表：D LINE 連結、F 客戶類別、H 客人姓名、I 服務時間、
#                X 原發票號碼＋Y 二聯／三聯（退款類、待處理發票）、
#                R 付款方式（待退款／VIP待退券，訂單付款方式為 ATM 或信用卡時）、
#                J 車馬費金額（車馬費發票且 J 空白）
#   後台訂單編輯頁（purchase JSON）：
#                待收款／待扣儲值金 N＝加收金額；待收款 M＝加收日期（後台已標記已收款才補）、R＝加收金流
#                待退款／VIP待退券／待返儲值金 S＝退款金額
# ============================================================
FILL_STATUSES = REFUND_STATUSES | STORED_VALUE_STATUSES | {"待收款", "待處理發票"}
INVOICE_NO_RE = re.compile(r"[A-Z]{2}\d{8}")
FILL_LETTERS = ("D", "F", "H", "I", "J", "M", "N", "R", "S", "X", "Y")


def _fill_due(row: list) -> bool:
    """這列要補齊：G 有訂單、登記日期在起算日後、狀態還沒完成。"""
    if not _cell(row, "G") or _cell(row, "B") not in FILL_STATUSES:
        return False
    reg_date = parse_reg_date(_cell(row, "E"))
    return reg_date is not None and reg_date >= CUTOFF


def _wanted(row: list) -> set[str]:
    """這列有哪些空白欄位可以從後台補（只看空白格）。"""
    status, detail = _cell(row, "B"), _cell(row, "C")
    letters = {"D", "F", "H", "I"}
    if status in REFUND_STATUSES or status == "待處理發票":
        letters |= {"X", "Y"}
    if status in REFUND_STATUSES:
        letters |= {"R", "S"}
    if status == "待返儲值金":
        letters.add("S")
    if status in ("待收款", "待扣儲值金"):
        letters.add("N")
    if status == "待收款":
        letters |= {"M", "R"}
    if status == "待處理發票" and detail == "車馬費發票":
        letters.add("J")
    return {letter for letter in letters if not _cell(row, letter)}


def _needs_purchase_state(row: list, letters: set[str]) -> bool:
    """S／N／M（及待收款的 R 加收金流）要讀訂單編輯頁；其他欄位訂單列表就有。"""
    return bool(letters & {"M", "N", "S"} or ("R" in letters and _cell(row, "B") == "待收款"))


def _sheet_date(value: str) -> str:
    match = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", str(value or ""))
    return f"{match.group(1)}/{int(match.group(2)):02d}/{int(match.group(3)):02d}" if match else ""


def _money(value) -> Optional[int]:
    amount = _amount(str(value or ""))
    if not amount:
        return None
    number = float(amount)
    return int(number) if number == int(number) else None


def backend_fill_values(row: list, order: Optional[dict], state: Optional[dict],
                        customer_type: str = "", service_text: str = "") -> dict[str, Any]:
    """依狀態決定要補的欄位 → {欄: 值}；只補空白格，後台沒有值就不補（不猜）。
    order＝後台訂單列表（fetch_order_basic），state＝訂單編輯頁 purchase JSON。"""
    wanted = _wanted(row)
    order, state = order or {}, state or {}
    status = _cell(row, "B")
    found: dict[str, Any] = {
        "D": str(order.get("line_url") or "").strip(),
        "F": customer_type,
        "H": str(order.get("customer_name") or "").strip(),
        "I": service_text,
    }
    invoice_no = str(order.get("invoice_no") or "").strip().upper()
    if INVOICE_NO_RE.fullmatch(invoice_no):  # 只認標準發票號碼，避免寫入「儲值金」等文字
        found["X"] = invoice_no
        found["Y"] = "三聯" if order.get("carrier_type") == "三聯式" else "二聯"
    payway = str(order.get("payway") or "")
    if status in REFUND_STATUSES:
        # 退款方式＝原訂單付款方式；只認 ATM／信用卡（儲值金、非儲值金不確定，不補）
        if "ATM" in payway.upper():
            found["R"] = "ATM"
        elif payway == "信用卡":
            found["R"] = "信用卡"
    if status in REFUND_STATUSES or status == "待返儲值金":
        refund = _money(state.get("refundAmount"))
        if refund and refund > 0:
            found["S"] = refund
    if status in ("待收款", "待扣儲值金"):
        charge = _money(state.get("chargeAmount"))
        if charge and charge > 0:
            found["N"] = charge
    if status == "待收款":
        # 後台「加收」已標記已收款（isCharge＝2）才補收款時間，避免把未付款當已付款
        if str(state.get("isCharge") or "") == "2":
            found["M"] = _sheet_date(state.get("chargeDate"))
        found["R"] = str(state.get("chargePayment") or "").strip()
    if status == "待處理發票":
        fare = _money(order.get("travel_fee"))
        if fare and fare > 0:
            found["J"] = f"車馬費 ${fare}"
    return {letter: found[letter] for letter in FILL_LETTERS
            if letter in wanted and found.get(letter) not in (None, "")}


def _purchase_state(co, session, order: dict) -> dict:
    from bs4 import BeautifulSoup

    purchase_id = str(order.get("purchase_id") or "")
    if not purchase_id:
        return {}
    resp = session.get(f"{co.BASE_URL}/purchase/edit/{purchase_id}", timeout=20)
    resp.raise_for_status()
    return co._extract_purchase_state(BeautifulSoup(resp.text, "html.parser"))


def fill_from_backend(region: str, ws, values: list[list], log: Callable[[str], None] = print,
                      session_factory: Optional[Callable] = None, dry_run: bool = False) -> int:
    """盤點時補齊可自檸檬後台查詢的空白欄位；回傳補了幾格。登入或查詢失敗只提醒，不影響盤點。
    dry_run：只查詢、列出會補的內容，不寫入試算表。"""
    targets = [(row_no, row, wanted) for row_no, row in enumerate(values[1:], start=2)
               if _fill_due(row) and (wanted := _wanted(row))]
    if not targets:
        return 0
    from tools.invoice_center.merged import split_order_numbers

    co = _change_order()
    try:
        session = (session_factory or _login_session)(region, ui_logger=None)
    except Exception as exc:
        log(f"⚠️ 【{region}】登入檸檬後台失敗，略過自動補齊 {len(targets)} 列：{exc}")
        return 0
    updates, filled_rows = [], []
    try:
        for row_no, row, wanted in targets:
            orders = split_order_numbers(_cell(row, "G"))
            order_no = orders[0] if orders else _cell(row, "G")  # 合併開立以第一張訂單為準
            try:
                order = co.fetch_order_basic(order_no, session=session, by="orderNo")
                if not order:
                    log(f"⚠️ 【{region}】第 {row_no} 列 {order_no} 後台查無訂單，未補齊")
                    continue
                state = _purchase_state(co, session, order) if _needs_purchase_state(row, wanted) else {}
                try:
                    customer_type = co._customer_type_from_order(order) if "F" in wanted else ""
                except Exception:
                    customer_type = ""
                service_text = (co._format_service_datetime(order.get("service_date"), order.get("period_text", ""))
                                if "I" in wanted else "")
                fills = backend_fill_values(row, order, state, customer_type, service_text)
            except Exception as exc:
                log(f"⚠️ 【{region}】第 {row_no} 列 {order_no} 後台查詢失敗：{exc}")
                continue
            for letter, value in fills.items():
                updates.append({"range": f"{letter}{row_no}", "values": [[value]]})
                row_values = values[row_no - 1]
                while len(row_values) <= COL[letter]:
                    row_values.append("")
                row_values[COL[letter]] = str(value)
            if fills:
                filled_rows.append(f"第{row_no}列 {_cell(row, 'G')} " + "、".join(f"{k}={v}" for k, v in fills.items()))
    finally:
        _logout_session(co, session, region, log)
    if updates and dry_run:
        log(f"【{region}】〔預覽〕會自檸檬後台補齊 {len(updates)} 格：\n　" + "\n　".join(filled_rows))
    elif updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        log(f"【{region}】已自檸檬後台補齊 {len(updates)} 格：{'；'.join(filled_rows)}")
    return len(updates)


# ============================================================
# 盤點同步 ATM 工作表（2026-11-12 Jenny）：待退款／待收款且 R 付款方式為 ATM → 補進同地區 ATM 工作表待配對區
# 寫法同 order-system 建立異動（I 年月、J 訂單、K 姓名、L 金額；待退款金額負數＝S，N/O＝清潔／退款；
# 待收款 L＝N，N/O 依異動內容＝清潔／異動費或工具組）。已退款（AC）／已收款（M）不補。
# 去重：ATM 工作表整欄 J 已有同訂單、同金額（不分正負）的列就不再新增（建立異動時已同步過的不會重複）。
# ============================================================
ATM_REFUND_FEE = ("清潔", "退款")


def _atm_service_fee(kind: str, change_text: str) -> tuple:
    if kind == "refund":
        return ATM_REFUND_FEE
    text = str(change_text or "")
    return ("清潔", "工具組") if ("工具組" in text or "工具包" in text) else ("清潔", "異動費")


def atm_candidates(values: list[list]) -> list[dict]:
    """清潔異動待退款／待收款、R＝ATM、金額有值且尚未退款／收款的列 → ATM 工作表要有的資料。"""
    rows = []
    for row in values[1:]:
        status = _cell(row, "B")
        if status not in ("待退款", "待收款") or "ATM" not in _cell(row, "R").upper() or not _cell(row, "G"):
            continue
        reg_date = parse_reg_date(_cell(row, "E"))
        if reg_date is None or reg_date < CUTOFF:
            continue
        is_refund = status == "待退款"
        if _cell(row, "AC" if is_refund else "M"):
            continue  # 已退款／已收款，不再列待配對
        amount = _money(_cell(row, "S" if is_refund else "N"))
        if not amount:
            continue
        month = re.search(r"(\d{4})\D(\d{1,2})", _cell(row, "I"))
        rows.append({
            "year_month": f"{month.group(1)}.{int(month.group(2)):02d}" if month else "",
            "order_no": _cell(row, "G"), "name": _cell(row, "H"),
            "amount": -abs(amount) if is_refund else amount,
            "kind": "refund" if is_refund else "charge",
            "change_text": f"{_cell(row, 'C')} {_cell(row, 'J')}",
        })
    return rows


def _atm_existing_keys(atm_values: list[list]) -> set[tuple[str, int]]:
    keys = set()
    for row in atm_values:
        order_no = str(row[9] if len(row) > 9 else "").strip().upper()
        amount = _money(row[11] if len(row) > 11 else "")
        if order_no and amount is not None:
            keys.add((order_no, abs(amount)))
    return keys


def new_atm_rows(candidates: list[dict], atm_values: list[list]) -> list[dict]:
    """去掉 ATM 工作表已有（同訂單、同金額）的資料。"""
    seen = _atm_existing_keys(atm_values)
    result = []
    for row in candidates:
        key = (str(row["order_no"]).strip().upper(), abs(int(row["amount"])))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def sync_atm_sheet(region: str, values: list[list], log: Callable[[str], None] = print,
                   dry_run: bool = False) -> int:
    """盤點時把 ATM 待退款／待收款補進同地區 ATM 工作表；回傳新增筆數。失敗只提醒，不影響盤點。
    dry_run：只列出會新增的資料，不寫入。"""
    candidates = atm_candidates(values)
    if not candidates:
        return 0
    from tools.memo_system import atm, memo

    ws = atm.get_atm_worksheet(region)
    atm_values = memo.with_retry(ws.get_all_values)
    rows = new_atm_rows(candidates, atm_values)
    if not rows:
        return 0
    # 新增位置同 order-system：A 欄最後一筆＋5 列，且接在待配對區（I:L 有值）最後一列之後
    last_a_row = max((idx for idx, row in enumerate(atm_values, start=1) if row and str(row[0]).strip()), default=0)
    last_unpaid_row = max((idx for idx, row in enumerate(atm_values, start=1)
                           if any(str(value).strip() for value in row[8:12])), default=0)
    start_row = max(last_a_row + 5, last_unpaid_row + 1)
    end_row = start_row + len(rows) - 1
    if dry_run:
        log(f"【{region}】〔預覽〕會新增 ATM 工作表 I{start_row}:O{end_row}：\n　" + "\n　".join(
            f"{r['year_month']} {r['order_no']} {r['name']} {r['amount']} {'／'.join(_atm_service_fee(r['kind'], r['change_text']))}"
            for r in rows))
        return len(rows)
    row_count = int(getattr(ws, "row_count", 0) or len(atm_values))
    if end_row > row_count:
        memo.with_retry(ws.add_rows, end_row - row_count)
    memo.with_retry(ws.update, values=[[r["year_month"], r["order_no"], r["name"], r["amount"]] for r in rows],
                    range_name=f"I{start_row}:L{end_row}", value_input_option="RAW")
    memo.with_retry(ws.update, values=[list(_atm_service_fee(r["kind"], r["change_text"])) for r in rows],
                    range_name=f"N{start_row}:O{end_row}", value_input_option="RAW")
    ensure_format = getattr(atm, "ensure_candidate_row_format", None)
    if ensure_format:  # order-system 同步後才有：補 G 公式、I/N/O 下拉選單
        try:
            ensure_format(ws, list(range(start_row, end_row + 1)), log=log)
        except Exception as exc:
            log(f"⚠️ 【{region}】ATM 工作表補 G 欄公式／下拉選單失敗：{exc}")
    refunds = sum(1 for r in rows if r["kind"] == "refund")
    log(f"【{region}】已同步 ATM 工作表 I{start_row}:O{end_row}：待收款 {len(rows) - refunds} 筆、待退款 {refunds} 筆"
        f"（{'、'.join(r['order_no'] for r in rows)}）")
    return len(rows)


def scan_region(region: str, fix_formulas: bool = True, log: Callable[[str], None] = print,
                fill_backend: bool = True, sync_atm: bool = True, dry_run: bool = False) -> list[dict]:
    """dry_run：只預覽會補的資料／會新增的 ATM 列，不寫入任何試算表（也不補 T／Z 公式）。"""
    ws = _change_order().get_worksheet(region)
    values = ws.get_all_values()
    if dry_run:
        fix_formulas = False
    if fill_backend:
        try:
            if fill_from_backend(region, ws, values, log, dry_run=dry_run) and not dry_run:
                values = ws.get_all_values()
        except Exception as exc:
            log(f"⚠️ 【{region}】自檸檬後台補齊失敗：{exc}")
    if fix_formulas:
        try:
            z_formulas = ws.get("Z1:Z", value_render_option="FORMULA")
        except Exception:
            z_formulas = None
        updates = formula_fixes(values, z_formulas)
        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            log(f"【{region}】已補 T／Z 公式 {len(updates)} 格")
            values = ws.get_all_values()
    if sync_atm:
        try:
            sync_atm_sheet(region, values, log, dry_run=dry_run)
        except Exception as exc:
            log(f"⚠️ 【{region}】同步 ATM 工作表失敗：{exc}")
    items = []
    for row_no, row in enumerate(values[1:], start=2):
        item = plan_row(region, row_no, row)
        if item:
            items.append(item)
    return items


def scan(regions=None, fix_formulas: bool = True, log: Callable[[str], None] = print,
         fill_backend: bool = True, sync_atm: bool = True) -> list[dict]:
    items = []
    for region in regions or REGIONS:
        try:
            items.extend(scan_region(region, fix_formulas, log, fill_backend, sync_atm))
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
        # 合併開立發票：開立後一定回寫（每張訂單都寫發票號碼），不受「執行完後回寫系統」勾選影響
        merged_rows = sorted(int(item["row"]) for item in region_items
                             if item.get("merged_orders") and "invoice" in item.get("selected", item["actions"]))
        sync_rows = rows if do_sync else merged_rows
        if steps or sync_rows:
            plan.append({"region": region, "steps": steps, "sync_rows": sync_rows})
    return plan


def queue_actions(items: list[dict], do_sync: bool, create_task: Callable, created_by: str = "清潔異動整合執行",
                  log: Callable[[str], None] = print,
                  prepare_invoices: Optional[Callable[[str, list[int]], Any]] = None,
                  prepare_triplicates: Optional[Callable[[str, list[int]], dict]] = None) -> Optional[dict]:
    """整批只排一筆本機 Agent 任務（逐區依序執行，見 run_plan），不再每種動作各排一筆讓 Agent 同時跑。

    開立發票要先在網頁端組好發票資料（同【鯨躍發票】開立發票），由 prepare_invoices(地區, 列號) 排入 Payload。
    二聯改三聯要先讀檸檬後台組好新發票資料，由 prepare_triplicates(地區, 列號) 回傳 {列號: 計畫}；
    修正發票金額同樣用 prepare_triplicates(地區, 列號, kind="correction")。"""
    plan = build_plan(items, do_sync)
    for action in REISSUE_ACTIONS:
        label = ACTIONS[action][0]
        for entry in plan:
            rows = entry["steps"].get(action)
            if not rows:
                continue
            try:
                if prepare_triplicates is None:
                    raise RuntimeError("需在 Tools App 執行（要先讀檸檬後台組新發票資料）")
                plans = (prepare_triplicates(entry["region"], rows) if action == "triplicate"
                         else prepare_triplicates(entry["region"], rows, kind=action))
                entry[f"{action}_plans"] = {str(r): plans[r] for r in rows if r in plans}
                skipped = [r for r in rows if r not in plans]
                if skipped:
                    log(f"⚠️ {entry['region']} {label}第 {'、'.join(map(str, skipped))} 列未排入")
                entry["steps"][action] = [r for r in rows if r in plans]
                if not entry["steps"][action]:
                    entry["steps"].pop(action)
            except Exception as exc:
                entry["steps"].pop(action)
                log(f"⚠️ {entry['region']} {label}未排入（{len(rows)} 筆）：{exc}")
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
        orders = row_orders(row)
        try:
            change_order.sync_one_to_purchase_edit(sync_item, session=session, ui_logger=log)
            if _cell(row, "B") != status:
                ws.update_acell(f"B{row_no}", status)
            extra = {"merged_count": len(orders)} if len(orders) >= 2 else {}
            change_order.mark_sheet_row_done(region, row_no, status, ui_logger=log, **extra)
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
                                                calendar_id=RECORD_CALENDAR_ID, attendee="", reminder_minutes=[],
                                                replace_prefix="本日清潔異動已回寫")
    log(f"已在 {(event or {}).get('_calendar_id', RECORD_CALENDAR_ID)} 行事曆留紀錄：{title}")
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

    if action == CETUSTEK_LOGOUT:
        return [sys.executable, "-m", "tools.invoice_center.cetustek_logout_only", "--area", region, "--cdp-url", cdp_url]
    return ACTION_HANDLERS[ACTIONS[action][1]]({"area": region, "selected_rows": rows, "cdp_url": cdp_url})


def _run_step(action: str, region: str, rows: list[int], cdp_url: str) -> int:
    """在同一個 Agent 任務內依序執行各步驟子程式；輸出直接接到本任務 Log。"""
    sys.stdout.flush()
    return subprocess.run(_step_command(action, region, rows, cdp_url)).returncode


def run_triplicates(region: str, rows: list[int], plans: dict, cdp_url: str,
                    run: Callable[[list[str]], int] = lambda cmd: subprocess.run(cmd).returncode,
                    label: str = "二聯改三聯") -> int:
    """二聯改三聯／修正發票金額逐列執行（每列一張新發票，人工按下一步及儲存）；一列失敗就停。"""
    from tools.local_agent import ACTION_HANDLERS

    for row in rows:
        plan = plans.get(str(row))
        if not plan:
            print(f"❌ 第 {row} 列沒有{label}資料", flush=True)
            return 1
        sys.stdout.flush()
        code = run(ACTION_HANDLERS[ACTIONS["triplicate"][1]]({"area": region, "plan": plan, "cdp_url": cdp_url}))
        if code != 0:
            return code
    return 0


def run_plan(plan: list[dict], log: Callable[[str], None] = print, cdp_url: str = "http://127.0.0.1:9222",
             run_step: Callable[[str, str, list[int], str], int] = _run_step) -> int:
    """逐區依序：鯨躍（折讓單→發票→二聯改三聯，同一次登入，做完才登出）→ 藍新／富邦退款 → 儲值金 → 回寫系統。
    某一步失敗就不做該區後面的步驟（鯨躍仍會登出），但仍回寫該區已完成的列；最後留一筆行事曆紀錄。"""
    import os
    from contextlib import contextmanager

    KEEP_LOGIN_ENV = "CETUSTEK_KEEP_LOGIN"  # 同 tools.invoice_center.ei_session.KEEP_LOGIN_ENV

    @contextmanager
    def keep_login():
        previous = os.environ.get(KEEP_LOGIN_ENV)
        os.environ[KEEP_LOGIN_ENV] = "1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(KEEP_LOGIN_ENV, None)
            else:
                os.environ[KEEP_LOGIN_ENV] = previous

    def logout(region: str) -> None:
        log(f"===== 【{region}】鯨躍登出（這區鯨躍步驟都做完）=====")
        try:
            run_step(CETUSTEK_LOGOUT, region, [], cdp_url)
        except Exception as exc:
            log(f"⚠️ 【{region}】鯨躍登出失敗，下次執行前請手動登出：{exc}")

    failed = False
    results: dict[str, dict] = {}
    for entry in plan:
        region = entry["region"]
        logged_in = False  # 這區已經跑過鯨躍步驟、還沒登出
        for action in ACTION_ORDER:
            rows = entry["steps"].get(action)
            if not rows:
                continue
            if logged_in and action not in CETUSTEK_ACTIONS:
                logout(region)
                logged_in = False
            log(f"===== 【{region}】{ACTIONS[action][0]}：第 {'、'.join(map(str, rows))} 列 =====")
            try:
                if action in CETUSTEK_ACTIONS:
                    logged_in = True
                    with keep_login():
                        if action in REISSUE_ACTIONS:
                            code = run_triplicates(region, rows, entry.get(f"{action}_plans") or {}, cdp_url,
                                                   label=ACTIONS[action][0])
                        else:
                            code = run_step(action, region, rows, cdp_url)
                else:
                    code = run_step(action, region, rows, cdp_url)
            except Exception as exc:
                log(f"❌ 【{region}】{ACTIONS[action][0]}無法執行：{exc}")
                code = 1
            if code != 0:
                failed = True
                log(f"❌ 【{region}】{ACTIONS[action][0]}失敗（exit {code}），本區後面的步驟先不做")
                break
        if logged_in:
            logout(region)
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


def scan_report(items: list[dict]) -> tuple[list[dict], str]:
    """排程盤點：可執行（有動作）的列與提醒內容。"""
    ready = [i for i in items if i["actions"]]
    lines = []
    for region in REGIONS:
        hit = [i for i in ready if i["region"] == region]
        if not hit:
            continue
        lines.append(f"【{region}】{len(hit)} 筆")
        for i in hit:
            acts = "、".join(ACTIONS[a][0] for a in i["actions"])
            lines.append(f"　第{i['row']}列 {i['status']} {i['detail']} {i['order_no']} {i['amount']}：{acts}")
    waiting = [i for i in items if not i["actions"] and (i["missing"] or i["manual"])]
    if waiting:
        lines.append(f"\n未備妥／需人工 {len(waiting)} 筆（每日 10:00 檢查會另外提醒）")
    return ready, "\n".join(lines)


def scheduled_scan(log: Callable[[str], None] = print) -> int:
    """排程（排程設定 change_order_scan）：盤點 → 依執行方式排入本機 Agent 或只建提醒等你確認。"""
    from tools.scheduling.schedule_settings import MODE_CONFIRM, MODE_QUEUE, get_mode

    items = scan(log=log)
    ready, report = scan_report(items)
    log(report or "沒有可執行的清潔異動")
    if not ready:
        return 0
    from tools.scheduling.schedule_settings import MODE_CONFIRM as _CONFIRM, MODE_QUEUE as _QUEUE, get_mode as _mode

    if _mode("change_order_scan", _CONFIRM) != _QUEUE:
        try:  # 2026-11-06 Jenny：存盤點結果，Tools App 跳視窗、停在這次盤點結果接著執行
            from tools.scheduling import scan_results

            scan_results.save("change_order_scan", items, f"可執行 {len(ready)} 筆（共 {len(items)} 列）")
            log("已存盤點結果，Tools App 會跳視窗")
        except Exception as exc:
            log(f"⚠️ 盤點結果存檔失敗：{exc}")
    from tools.orders_system import calendar_notify

    now = datetime.now(TW_TZ)
    if get_mode("change_order_scan", MODE_CONFIRM) == MODE_QUEUE:
        from tools.local_agent_queue import create_task

        # 排程沒有網頁端，開立發票（含車馬費）無法先組 Payload，會略過並在提醒裡列出，請到 Tools App 執行
        queue_actions(ready, True, create_task, created_by="排程系統", log=log)
        title = f"【清潔異動】已排入本機 Agent：{len(ready)} 筆"
        report += "\n\n開立發票需在 Tools App【整合執行】清潔異動執行（要先組發票資料）。"
    else:
        title = f"【清潔異動】盤點：可執行 {len(ready)} 筆，請確認後執行"
        report += "\n\n打開 Tools App 會跳視窗，按「前往處理」直接停在這次盤點結果，勾選後執行（不用重新盤點）。"
    from datetime import timedelta

    event = calendar_notify.create_notify_event(summary=title, description=report, start=now + timedelta(minutes=30),
                                                calendar_id=RECORD_CALENDAR_ID, attendee="", reminder_minutes=[20, 10],
                                                replace_prefix="【清潔異動】")
    log(f"已在 {(event or {}).get('_calendar_id', RECORD_CALENDAR_ID)} 建立提醒：{title}")
    try:  # 2026-11-06 Jenny：盤點完 Mac 跳視窗提醒去確認及執行
        from tools.local_agent_queue import queue_popup

        queue_popup(title, report, system="財務管理", function="【整合執行】清潔異動已備妥", scan_key="change_order_scan")
        log("已排入 Mac 提醒視窗")
    except Exception as exc:
        log(f"⚠️ Mac 提醒視窗排入失敗：{exc}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="清潔異動整合執行：逐區依序執行／回寫系統")
    parser.add_argument("command", choices=["sync", "run", "scan", "preview"],
                        help="preview：試跑盤點（自後台補齊、同步 ATM 工作表），只列出結果不寫入")
    parser.add_argument("--regions", default=",".join(REGIONS))
    parser.add_argument("--rows", default="", help='JSON：{"台北": [238, 389]}，只回寫這些列')
    parser.add_argument("--plan", default="", help="run 用：build_plan 產生的 JSON")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = parser.parse_args(argv)
    if args.command == "preview":
        log = lambda text: print(text, flush=True)  # noqa: E731
        for region in [r for r in args.regions.split(",") if r]:
            log(f"===== 【{region}】預覽 =====")
            try:
                scan_region(region, log=log, dry_run=True)
            except Exception as exc:
                log(f"❌ 【{region}】讀取清潔異動失敗：{exc}")
        return 0
    if args.command == "scan":
        return scheduled_scan(log=lambda text: print(text, flush=True))
    if args.command == "run":
        log = lambda text: print(text, flush=True)  # noqa: E731
        return run_plan(json.loads(args.plan), log=log, cdp_url=args.cdp_url)
    regions = [r for r in args.regions.split(",") if r]
    rows = json.loads(args.rows) if args.rows else None
    results = sync(regions, rows=rows)
    return 1 if any(r["failed"] for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
