"""外場排程系統的雲端排程（GitHub Actions scheduled_field.yml）。

2026-11-05 Jenny：
- 每月 15 日 06:00：【月排程】新增專員名冊、新增專員調薪（台北、台中，期別＝當月，例 11/15 建 202611）
- 每月 15 日、月底 06:00：【專員請款】請款資料審核（台北、台中）
  處理 AJ 給付期別空白、有資料的列；15 日填「當月-1」，月底填「當月-2」（例 202611-1／202611-2）
- 每週五 06:00：【新訓退款】新訓退訓實境費用（台北、台中）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

AREAS = ("台北", "台中")
TW = ZoneInfo("Asia/Taipei")


def _today() -> date:
    return datetime.now(TW).date()


def claim_period(day: date) -> str:
    """15 日（含之前）＝當月-1；之後（月底）＝當月-2。"""
    return f"{day:%Y%m}-{1 if day.day <= 15 else 2}"


def pending_claim_blocks(header: list[Any], rows: list[list[Any]], first_row: int = 2) -> list[tuple[int, int]]:
    """AJ 給付期別空白、有服務日期或顧客姓名的列 → 連續區段 [(起始列, 結束列)]。
    已有給付期別的列會切斷區段，避免 run_claim_batch 把範圍內已填的期別改掉；空白列不影響。"""
    from tools.field_management.staff_claim import CLAIM_HEADERS, _clean

    cleaned = [_clean(h) for h in header]
    idx = {key: cleaned.index(CLAIM_HEADERS[key]) for key in ("date", "customer", "period")}

    def cell(row, key):
        i = idx[key]
        return _clean(row[i]) if i < len(row) else ""

    blocks: list[tuple[int, int]] = []
    start = end = None
    for offset, row in enumerate(rows):
        row_no = first_row + offset
        has_data = bool(cell(row, "date") or cell(row, "customer"))
        if has_data and cell(row, "period"):
            if start is not None:
                blocks.append((start, end))
            start = end = None
        elif has_data:
            start = row_no if start is None else start
            end = row_no
    if start is not None:
        blocks.append((start, end))
    return blocks


def run_claim_review(day: date, log: Callable[[str], None] = print) -> int:
    from tools.field_management import staff_claim as sc

    period = claim_period(day)
    service = sc.get_sheets_service()
    failed = 0
    for area in AREAS:
        try:
            sid = sc.resolve_spreadsheet_id(area)
            title, _gid = sc.resolve_claim_sheet(service, sid, area)
            values = sc._read(service, sid, f"'{title}'!A:AZ")
            if not values:
                log(f"{area}：{title} 沒有資料")
                continue
            blocks = pending_claim_blocks(values[0], values[1:])
            if not blocks:
                log(f"{area}：沒有 AJ 給付期別空白的列")
                continue
            for start, end in blocks:
                result = sc.run_claim_batch(area, start, end, period, service=service, on_progress=log)
                log(f"{area}｜第 {start}～{end} 列｜" + sc.format_batch_result(result))
        except Exception as exc:
            failed += 1
            log(f"❌ {area} 請款資料審核失敗：{exc}")
    return 1 if failed else 0


def run_roster_raise(day: date, log: Callable[[str], None] = print) -> int:
    from tools.field_management import roster_raise as rr

    ym = f"{day:%Y%m}"
    failed = 0
    for area in AREAS:
        for label, func in (("專員名冊", rr.create_roster_sheet), ("調薪資料", rr.create_raise_sheet)):
            try:
                result = func(area, ym, run_type="排程")
                log(f"✅ {area} {label}：{result.get('source')} → {result.get('new')}"
                    + (f"｜離職名冊新增 {result['moved']} 筆" if "moved" in result else ""))
            except Exception as exc:
                if "已存在" in str(exc):
                    log(f"略過 {area} {label}：{exc}")
                    continue
                failed += 1
                log(f"❌ {area} {label}失敗：{exc}")
    return 1 if failed else 0


def run_trainee_fee(day: date, log: Callable[[str], None] = print) -> int:
    from tools.finance_management.trainee_field_fee import update_refund_and_fee_sheet

    failed = 0
    for area in AREAS:
        try:
            result = update_refund_and_fee_sheet(area)
            log(f"✅ {area}：退款金額 H 欄更新 {result['h_updated']} 列；新訓退訓實境費用新增 {result['fee_rows']} 人，"
                f"身分證打勾 {result['id_checked']} 人" + (f"；注意：{'；'.join(result['warnings'])}" if result.get("warnings") else ""))
        except Exception as exc:
            failed += 1
            log(f"❌ {area} 新訓退訓實境費用失敗：{exc}")
    return 1 if failed else 0


TARGETS = {
    "field_roster_raise": run_roster_raise,
    "field_claim_review": run_claim_review,
    "field_trainee_fee": run_trainee_fee,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="外場排程系統雲端排程")
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--date", default="", help="YYYY-MM-DD，預設今天（台北時間）")
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.date) if args.date else _today()
    return TARGETS[args.target](day, log=lambda text: print(text, flush=True))


if __name__ == "__main__":
    sys.exit(main())
