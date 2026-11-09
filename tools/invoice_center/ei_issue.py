"""鯨躍共用：開立折讓單、開立發票（2026-11 Jenny：所有流程共用同一套）。

使用的流程：
  - 【鯨躍發票】開立折讓單／清潔異動待退款（allowance_create.run）
  - 【鯨躍發票】開立發票／待收款／車馬費／合併開立（cetustek_invoice_paste.process_pending_invoice_payloads）
  - 二聯改三聯、修正發票金額（triplicate_runner：原票全額折讓 → 開新發票）

共同規則：
  - 開立前先到鯨躍查詢是否已開過（上次開了但沒回填）：有就沿用，不重開；查詢失敗直接停止，不在不確定時開立。
  - 折讓單存檔後讀不到單號：再查一次，查得到就沿用，查不到才算失敗。
  - 發票由 Agent 填表，人工按下一步及儲存，讀到新發票號碼才回傳。
各流程只負責前後的資料核對與回填（清潔異動表欄位、Payload 狀態、進度檔），鯨躍操作都走這裡。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, NamedTuple, Optional
from zoneinfo import ZoneInfo


class LookupFailed(RuntimeError):
    """開立前查詢鯨躍失敗：不確定有沒有開過，所以沒有開立（下次可重試）。"""


class AllowanceResult(NamedTuple):
    number: str
    date: str  # YYYY/MM/DD
    reused: bool  # True＝鯨躍原本就有，未重開


class InvoiceResult(NamedTuple):
    number: str
    reused: bool


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")


def issue_allowance(page: Any, invoice_no: str, untaxed: str, *, invoice_year: Optional[int] = None,
                    require_full: bool = False, not_before: str = "", check_existing: bool = True,
                    before_save: Optional[Callable[[], None]] = None) -> AllowanceResult:
    """原發票 invoice_no 開立折讓單（untaxed＝鯨躍折讓頁「應稅」金額；二聯為含稅、三聯為未稅）。

    require_full：一定要全額折讓（金額需等於原票可折讓額、明細只能一筆），不符就停。
    before_save：按存檔前呼叫（呼叫端記錄進度用；之後若失敗不可自動重試）。
    """
    from . import allowance_create as allowance
    from .cetustek_invoice_paste import _clear_dialog_handlers

    invoice_no = str(invoice_no or "").strip().upper()
    match_args = {"untaxed": str(untaxed), "not_before": str(not_before or "")}
    if check_existing:
        try:
            found = allowance.find_existing_allowance(page, invoice_no, **match_args)
        except Exception as exc:
            raise LookupFailed(str(exc)) from exc
        if found:
            found_date, number = found
            print(f"[鯨躍] {invoice_no} 已有折讓單 {number}，不重開", flush=True)
            return AllowanceResult(number, found_date or _today(), True)
    _clear_dialog_handlers(page)
    allowance._open_allowance(page)
    try:
        number = allowance._create_one(page, invoice_no, str(untaxed), invoice_year=invoice_year,
                                       require_full=require_full, before_save=before_save)
    except Exception as exc:
        # 存檔後讀不到單號：再查一次，有就沿用；查不到才算開立失敗
        try:
            found = allowance.find_existing_allowance(page, invoice_no, **match_args)
        except Exception:
            found = None
        if not found:
            raise RuntimeError(f"{invoice_no} 折讓單未開立成功：{exc}") from exc
        found_date, number = found
        print(f"[鯨躍] 存檔後未讀到單號，查詢確認已開立 {number}", flush=True)
        return AllowanceResult(number, found_date or _today(), False)
    return AllowanceResult(number, _today(), False)


def issue_invoice(page: Any, payload: Any, *, lookup_order_no: str = "", amount: str = "",
                  not_before: str = "", check_existing: bool = True,
                  before_fill: Optional[Callable[[], None]] = None,
                  after_fill: Optional[Callable[[], None]] = None) -> InvoiceResult:
    """以 payload（dict 或 JSON）開立發票：開立頁 → Agent 填表 → 等人工按下一步及儲存 → 回傳發票號碼。

    lookup_order_no：查詢已開立及讀新發票號碼用的訂單編號（預設 payload 的 orderid）。
    before_fill／after_fill：填表前後呼叫（呼叫端記錄「等待儲存」用）。
    """
    from . import cetustek_invoice_paste as paste

    payload_json = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    order_no = str(lookup_order_no or json.loads(payload_json).get("orderid") or "").strip()
    if not order_no:
        raise RuntimeError("Payload 缺少 orderid")
    if check_existing:
        try:
            existing = paste.find_existing_invoice(page, order_no, amount=amount, not_before=not_before)
        except Exception as exc:
            raise LookupFailed(str(exc)) from exc
        if existing:
            print(f"[鯨躍] {order_no} 已開立發票 {existing}，不重開", flush=True)
            return InvoiceResult(existing, True)
    # 每張發票按「否」後會回到查詢頁；每筆都重新進入開立頁
    paste._open_invoice_create(page)
    paste._clear_dialog_handlers(page)
    if before_fill is not None:
        before_fill()
    paste._paste_one(page, payload_json)
    if after_fill is not None:
        after_fill()
    # 有先查過沒開立，頁面上出現同訂單發票就是剛儲存的那張（new_order）
    wait_args = {"new_order": True} if check_existing else {}
    number = paste._wait_for_manual_save(page, order_no, **wait_args)
    return InvoiceResult(number, False)
