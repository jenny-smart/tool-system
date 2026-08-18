# -*- coding: utf-8 -*-
"""大掃除保留單規劃與測試機批次建單。

流程：
1. 從後台 /schedule 讀指定日期各時段的「未配班」人數。
2. 依日期區間 AM/PM 保留率計算應建立的 2 人保留單張數。
3. 使用既有 quick_order.quick_create_order 真正建立訂單。
4. 每張送出前重新讀班表；成功後寫入客服備註並留下批次 log。
5. 可一次執行整段日期範圍；每個日期/時段仍各自保留市場安全人數。
6. 第一版只允許 dev 測試機，避免誤送正式機。

注意：
- 定期客「檸檬人換真人」需在執行本功能前完成；本模組不會自動動既有客戶訂單。
- 第一版讓後台依地址/班表自動選合法專員，不強制指定配對；等測試機確認
  schedule/edit 的「可服務此區」標記 HTML 後，再加入最佳化指定真人配對。
"""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup

import quick_order as qo


RESERVE_PHONE_DEFAULT = "0939592628"
PERIOD_HOURS = {
    "08:30-12:30": 4,
    "09:00-11:00": 2,
    "09:00-12:00": 3,
    "14:00-16:00": 2,
    "14:00-17:00": 3,
    "14:00-18:00": 4,
    "09:00-16:00": 6,
    "09:00-18:00": 8,
}
AM_PERIODS = {"08:30-12:30", "09:00-11:00", "09:00-12:00", "09:00-16:00", "09:00-18:00"}
PM_PERIODS = {"14:00-16:00", "14:00-17:00", "14:00-18:00"}


@dataclass(frozen=True)
class ReserveRule:
    start: date
    end: date
    am_rate: float
    pm_rate: float
    label: str = ""

    def rate_for(self, service_date: date, period: str) -> Optional[float]:
        if not (self.start <= service_date <= self.end):
            return None
        return self.am_rate if period in AM_PERIODS else self.pm_rate


@dataclass
class SlotSnapshot:
    service_date: str
    period: str
    unassigned_people: int
    reserve_rate: float
    reserve_people_target: int
    reserve_order_target: int
    market_people_target: int
    raw_text: str = ""


def _assert_dev(env_name: str) -> None:
    env = str(env_name or "").strip().lower()
    if env not in {"dev", "test", "backend-dev"}:
        raise RuntimeError("大掃除保留單第一版只允許在 dev 測試機執行，禁止送正式機。")


def daterange(start: date, end: date) -> Iterable[date]:
    if end < start:
        raise ValueError("結束日期不可早於開始日期")
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def normalize_rate(value: float) -> float:
    rate = float(value)
    if rate > 1:
        rate = rate / 100.0
    if rate < 0 or rate > 1:
        raise ValueError("保留率必須介於 0% 到 100%")
    return rate


def resolve_rate(service_date: date, period: str, rules: Sequence[ReserveRule], default_rate: float = 0.0) -> float:
    for rule in rules:
        rate = rule.rate_for(service_date, period)
        if rate is not None:
            return normalize_rate(rate)
    return normalize_rate(default_rate)


def calculate_reserve_target(unassigned_people: int, reserve_rate: float) -> Tuple[int, int, int]:
    """回傳 (保留人數, 2人保留單張數, 對外剩餘人數)。

    保留單固定 2 人，所以先用 floor 保守取整，避免為了湊比例多鎖一組。
    奇數人會留在市場側，不會硬湊保留單。
    """
    people = max(0, int(unassigned_people or 0))
    rate = normalize_rate(reserve_rate)
    reserve_orders = int(math.floor((people * rate) / 2.0))
    reserve_people = reserve_orders * 2
    market_people = max(0, people - reserve_people)
    return reserve_people, reserve_orders, market_people


def login_reserve_member(env_name: str, backend_email: str, backend_password: str, phone: str = RESERVE_PHONE_DEFAULT):
    _assert_dev(env_name)
    result = qo.quick_lookup_member(env_name, backend_email, backend_password, phone, clean_type_id="1")
    if not result.get("member_payload"):
        raise RuntimeError(f"保留單手機 {phone} 查無會員，請先在測試機建立此會員。")
    return result


def member_addresses(lookup_result) -> List[str]:
    payload = lookup_result.get("member_payload") or {}
    member = payload.get("member") or {}
    rows = member.get("memberAddressList") or []
    addresses: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        addr = str(row.get("address") or "").strip()
        if addr and addr not in addresses:
            addresses.append(addr)
    last = payload.get("lastPurchase") or {}
    last_addr = str(last.get("address") or "").strip() if isinstance(last, dict) else ""
    if last_addr and last_addr not in addresses:
        addresses.insert(0, last_addr)
    return addresses


def fetch_schedule_html(lookup_result, service_date: str) -> str:
    session = lookup_result["session"]
    base_url = lookup_result["base_url"]
    resp = session.get(
        f"{base_url}/schedule",
        params={"date": service_date, "staffId": ""},
        headers=getattr(qo, "HEADERS", {}),
        allow_redirects=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"讀取班表失敗：{service_date} HTTP {resp.status_code}")
    return resp.text or ""


def _period_from_text(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    for period in sorted(PERIOD_HOURS, key=len, reverse=True):
        if period in compact:
            return period
    return ""


def parse_schedule_unassigned(html_text: str, service_date: str = "") -> Dict[str, Dict[str, object]]:
    """解析 schedule table 每個時段的「未配班 N 人」。"""
    soup = BeautifulSoup(html_text or "", "html.parser")
    result: Dict[str, Dict[str, object]] = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        periods = [_period_from_text(c.get_text(" ", strip=True)) for c in header_cells]
        if not any(periods):
            continue
        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            row_text = row.get_text(" ", strip=True)
            if service_date:
                mmdd = service_date[5:]
                if mmdd not in row_text and service_date not in row_text and len(rows) > 2:
                    continue
            for idx, cell in enumerate(cells):
                if idx >= len(periods) or not periods[idx]:
                    continue
                text = cell.get_text(" ", strip=True)
                m = re.search(r"未配班\s*(\d+)\s*人", text)
                if m:
                    result[periods[idx]] = {
                        "unassigned_people": int(m.group(1)),
                        "raw_text": text,
                    }

    if not result:
        plain = soup.get_text(" ", strip=True)
        for period in PERIOD_HOURS:
            pos = plain.find(period)
            if pos < 0:
                continue
            window = plain[pos: pos + 1600]
            m = re.search(r"未配班\s*(\d+)\s*人", window)
            if m:
                result[period] = {"unassigned_people": int(m.group(1)), "raw_text": window[:500]}
    return result


def snapshot_day(lookup_result, service_date: date, rules: Sequence[ReserveRule], periods: Sequence[str]) -> List[SlotSnapshot]:
    date_s = service_date.isoformat()
    parsed = parse_schedule_unassigned(fetch_schedule_html(lookup_result, date_s), date_s)
    rows: List[SlotSnapshot] = []
    for period in periods:
        info = parsed.get(period) or {}
        people = int(info.get("unassigned_people") or 0)
        rate = resolve_rate(service_date, period, rules)
        reserve_people, reserve_orders, market_people = calculate_reserve_target(people, rate)
        rows.append(SlotSnapshot(
            service_date=date_s,
            period=period,
            unassigned_people=people,
            reserve_rate=rate,
            reserve_people_target=reserve_people,
            reserve_order_target=reserve_orders,
            market_people_target=market_people,
            raw_text=str(info.get("raw_text") or ""),
        ))
    return rows


def build_period_plan(lookup_result, start: date, end: date, rules: Sequence[ReserveRule], periods: Sequence[str]) -> List[SlotSnapshot]:
    plan: List[SlotSnapshot] = []
    for d in daterange(start, end):
        plan.extend(snapshot_day(lookup_result, d, rules, periods))
    return plan


def _build_batch_id() -> str:
    return f"reserve_{date.today().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"


def create_reserve_orders_for_slot(
    *,
    env_name: str,
    lookup_result,
    region: str,
    address: str,
    service_date: str,
    period: str,
    reserve_rate: float,
    target_orders: int,
    payway: str,
    clean_type_id: str = "1",
    batch_id: str = "",
    stop_when_market_people_below: int = 2,
    sleep_seconds: float = 1.0,
) -> dict:
    """真正建立某日某時段的保留單；每張前重新讀班表。"""
    _assert_dev(env_name)
    if period not in PERIOD_HOURS:
        raise ValueError(f"不支援時段：{period}")
    if not address:
        raise ValueError("請先選擇保留會員的服務地址")
    target_orders = max(0, int(target_orders or 0))
    batch_id = batch_id or _build_batch_id()
    results = []

    for idx in range(target_orders):
        current = parse_schedule_unassigned(fetch_schedule_html(lookup_result, service_date), service_date)
        current_people = int((current.get(period) or {}).get("unassigned_people") or 0)
        if current_people < max(2, int(stop_when_market_people_below) + 2):
            results.append({
                "success": False,
                "stopped": True,
                "message": f"即時未配班只剩 {current_people} 人，已停止後續保留單，避免吃掉市場安全人力。",
                "service_date": service_date,
                "period": period,
            })
            break

        try:
            result = qo.quick_create_order(
                env_name=env_name,
                payway=payway,
                region=region,
                lookup_result=lookup_result,
                address=address,
                clean_type_id=clean_type_id,
                date_s=service_date,
                period_s=period,
                hour=str(PERIOD_HOURS[period]),
                person="2",
                allow_auto_lemon_shift=False,
                extra_fields={"notice": f"大掃除檸檬保留單 {batch_id}"},
            )
            note_ok = False
            note_msg = ""
            try:
                base_url = lookup_result.get("base_url") or qo._configure_environment(env_name)
                note_ok, note_msg = qo._update_order_note(
                    result["session"], base_url, result["order_no"],
                    f"大掃除檸檬保留單｜batch={batch_id}｜保留率={int(round(normalize_rate(reserve_rate) * 100))}%｜{idx + 1}/{target_orders}",
                )
            except Exception as note_exc:
                note_msg = f"訂單已成立，但備註寫入失敗：{note_exc}"

            results.append({
                "success": True,
                "order_no": result.get("order_no"),
                "staff": result.get("staff"),
                "service_date": service_date,
                "period": period,
                "note_ok": note_ok,
                "note_message": note_msg,
            })
        except Exception as exc:
            results.append({"success": False, "message": str(exc), "service_date": service_date, "period": period})
            break
        if sleep_seconds:
            time.sleep(float(sleep_seconds))

    return {
        "batch_id": batch_id,
        "service_date": service_date,
        "period": period,
        "target_orders": target_orders,
        "success_count": sum(1 for r in results if r.get("success")),
        "results": results,
    }


def create_reserve_orders_for_plan(
    *,
    env_name: str,
    lookup_result,
    region: str,
    address: str,
    plan_rows: Sequence[dict],
    payway: str,
    clean_type_id: str = "1",
    continue_after_slot_error: bool = True,
    sleep_seconds: float = 1.0,
) -> dict:
    """依預覽計畫一次建立整段期間的保留單。

    每個日期/時段使用該列自己的 reserve_order_target 與 market_people_target。
    某一時段因人力變動提前停止時，預設仍繼續下一個日期/時段，並完整回報結果。
    """
    _assert_dev(env_name)
    batch_id = _build_batch_id()
    slot_results = []
    flat_results = []
    total_target = 0

    for row in plan_rows or []:
        target_orders = max(0, int(row.get("reserve_order_target") or 0))
        if target_orders <= 0:
            continue
        total_target += target_orders
        service_date = str(row.get("service_date") or "")
        period = str(row.get("period") or "")
        try:
            slot_result = create_reserve_orders_for_slot(
                env_name=env_name,
                lookup_result=lookup_result,
                region=region,
                address=address,
                service_date=service_date,
                period=period,
                reserve_rate=float(row.get("reserve_rate") or 0),
                target_orders=target_orders,
                payway=payway,
                clean_type_id=clean_type_id,
                batch_id=batch_id,
                stop_when_market_people_below=int(row.get("market_people_target") or 0),
                sleep_seconds=sleep_seconds,
            )
        except Exception as exc:
            slot_result = {
                "batch_id": batch_id,
                "service_date": service_date,
                "period": period,
                "target_orders": target_orders,
                "success_count": 0,
                "results": [{
                    "success": False,
                    "service_date": service_date,
                    "period": period,
                    "message": str(exc),
                }],
            }
        slot_results.append(slot_result)
        flat_results.extend(slot_result.get("results") or [])
        has_failure = any(not r.get("success") for r in (slot_result.get("results") or []))
        if has_failure and not continue_after_slot_error:
            break

    return {
        "batch_id": batch_id,
        "target_orders": total_target,
        "success_count": sum(1 for r in flat_results if r.get("success")),
        "slot_count": len(slot_results),
        "slot_results": slot_results,
        "results": flat_results,
    }
