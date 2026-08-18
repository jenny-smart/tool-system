# -*- coding: utf-8 -*-
"""Helpers for system reserve-order tagging and safe cancellation."""

from __future__ import annotations

import re
from typing import List, Optional

import cancel_order as co
from reserve_sheet_log import append_log_rows

SYSTEM_RESERVE_MEMO = "系統保留單"


def _assert_supported_env(env_name: str) -> None:
    env = str(env_name or "").strip().lower()
    if env not in {"dev", "prod"}:
        raise RuntimeError("保留單功能只支援 prod 正式機或 dev 測試機。")


def _purchase_id_from_order_no(order_no: str) -> str:
    digits = re.sub(r"\D", "", str(order_no or ""))
    return str(int(digits)) if digits else ""


def mark_system_reserve_order(env_name: str, backend_email: str, backend_password: str, order_no: str) -> dict:
    _assert_supported_env(env_name)
    purchase_id = _purchase_id_from_order_no(order_no)
    if not purchase_id:
        return {"ok": False, "order_no": order_no, "message": "無法取得 purchase_id"}
    session, base_url = co._new_logged_in_session(env_name, backend_email, backend_password)
    ok, msg = co._update_cancel_notes(
        session, base_url, purchase_id,
        customer_memo=SYSTEM_RESERVE_MEMO, charge_note="", refund_note="",
    )
    return {"ok": bool(ok), "order_no": order_no, "message": msg or ("客人備註已註記系統保留單" if ok else "客人備註註記失敗")}


def _memo_matches_filter(memo: str, memo_filter: str) -> bool:
    memo = str(memo or "").strip()
    if memo_filter == "僅系統保留單":
        return SYSTEM_RESERVE_MEMO in memo
    if memo_filter == "僅空白":
        return memo == ""
    if memo_filter == "系統保留單或空白":
        return memo == "" or SYSTEM_RESERVE_MEMO in memo
    if memo_filter == "全部（僅供查看）":
        return True
    raise ValueError(f"未知客人備註篩選：{memo_filter}")


def find_reserve_orders(
    env_name: str, backend_email: str, backend_password: str, phone: str,
    clean_date_s: str, clean_date_e: str,
    memo_filter: str = "系統保留單或空白", periods: Optional[List[str]] = None,
) -> List[dict]:
    _assert_supported_env(env_name)
    periods_set = {str(p).replace(" ", "") for p in (periods or []) if str(p).strip()}
    candidates = []
    seen = set()

    for payment_status in ("已付款", "待付款"):
        rows = co.find_orders_for_cancel(
            env_name, backend_email, backend_password, phone,
            clean_date_s, clean_date_e, payment_status=payment_status,
        )
        for row in rows:
            pid = str(row.get("purchase_id") or "")
            if not pid or pid in seen:
                continue
            if periods_set:
                period = str(row.get("period") or "").replace(" ", "")
                if period not in periods_set:
                    continue
            seen.add(pid)
            item = dict(row)
            item["payment_status"] = payment_status
            candidates.append(item)

    session, base_url = co._new_logged_in_session(env_name, backend_email, backend_password)
    found = []
    for row in candidates:
        try:
            detail = co.fetch_order_cancel_details(session, base_url, row["purchase_id"])
        except Exception as exc:
            item = dict(row)
            item["customer_memo"] = ""
            item["memo_read_error"] = str(exc)
            item["cancel_eligible"] = False
            if memo_filter == "全部（僅供查看）":
                found.append(item)
            continue

        memo = str(detail.get("memo") or "").strip()
        if not _memo_matches_filter(memo, memo_filter):
            continue
        item = dict(row)
        item["customer_memo"] = memo
        item["cancel_eligible"] = (memo == "" or SYSTEM_RESERVE_MEMO in memo)
        for key in ("amount", "total_amount", "order_amount", "price", "staff", "staff_names", "service_staff"):
            if key not in item and detail.get(key) not in (None, ""):
                item[key] = detail.get(key)
        found.append(item)

    found.sort(key=lambda x: (x.get("service_date", ""), x.get("period", ""), x.get("order_no", "")))
    return found


def cancel_reserve_orders(
    env_name: str, backend_email: str, backend_password: str,
    reserve_orders: List[dict], cancel_count: int,
) -> List[dict]:
    _assert_supported_env(env_name)
    count = max(0, int(cancel_count or 0))
    if count <= 0:
        raise ValueError("取消張數必須大於 0")

    ordered = sorted(
        [dict(r) for r in (reserve_orders or [])],
        key=lambda x: (x.get("service_date", ""), x.get("period", ""), x.get("order_no", "")),
    )
    selected = ordered[:count]
    if not selected:
        raise ValueError("目前沒有可取消的保留單")

    session, base_url = co._new_logged_in_session(env_name, backend_email, backend_password)
    safe_rows = []
    skipped = []
    for row in selected:
        try:
            detail = co.fetch_order_cancel_details(session, base_url, row["purchase_id"])
            current_memo = str(detail.get("memo") or "").strip()
        except Exception as exc:
            skipped.append({**row, "ok": False, "message": f"取消前讀取客人備註失敗，已跳過：{exc}"})
            continue
        if current_memo and SYSTEM_RESERVE_MEMO not in current_memo:
            skipped.append({
                **row, "ok": False, "customer_memo": current_memo,
                "message": "客人備註已有其他內容，判定可能是人工替客人保留，已跳過不取消。",
            })
            continue
        safe_rows.append(row)

    cancelled = []
    if safe_rows:
        cancelled = co.cancel_orders(
            env_name, backend_email, backend_password, safe_rows,
            cancel_status="不需退款", customer_memo="取消系統保留單",
            charge_note="", refund_note="",
        )

        source_by_order = {str(r.get("order_no") or ""): r for r in safe_rows}
        enriched = []
        for result in cancelled:
            source = source_by_order.get(str(result.get("order_no") or ""), {})
            item = dict(source)
            item.update(result)
            enriched.append(item)
        cancelled = enriched

        try:
            append_log_rows("取消", cancelled)
        except Exception as log_exc:
            for row in cancelled:
                row["sheet_log_error"] = str(log_exc)

    return cancelled + skipped
