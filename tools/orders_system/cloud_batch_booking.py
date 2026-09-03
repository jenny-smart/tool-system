# -*- coding: utf-8 -*-
"""雲端批次成單：完整沿用 batch_booking_optimized 的候選資料、分組與 orders 核心。"""
from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict

from accounts import ACCOUNTS
from batch_booking_optimized import _load_candidates, _text
from orders import get_region_by_address, run_process_web

ACTIONS = ["建單", "寄確認信", "改 Google 日曆"]


def load_pending(sheet_name: str, excluded: set[int] | None = None):
    """先排除本次已嘗試列，再取下一批，避免前批失敗卡住後續 51+ 列。"""
    excluded = excluded or set()
    df = _load_candidates(sheet_name).sort_values("__sheet_row__")
    result = []
    for _, row in df.iterrows():
        row_no = int(row["__sheet_row__"])
        if row_no in excluded:
            continue
        address = _text(row.get("地址"))
        region = get_region_by_address(address, ACCOUNTS)
        if not region:
            result.append((row_no, "", f"無法依地址判斷地區：{address}"))
        else:
            result.append((row_no, region, ""))
    return result


def run(sheet_name: str, chunk_size: int = 50, max_rows: int = 0, pause_seconds: int = 5) -> int:
    attempted: set[int] = set()
    success_total = 0
    fail_total = 0
    started = time.monotonic()

    while True:
        pending = load_pending(sheet_name, attempted)
        if max_rows:
            remaining = max_rows - len(attempted)
            if remaining <= 0:
                break
            pending = pending[:remaining]
        batch = pending[:chunk_size]
        if not batch:
            break

        by_region: dict[str, list[int]] = defaultdict(list)
        for row_no, region, error in batch:
            attempted.add(row_no)
            if error:
                fail_total += 1
                print(f"SKIP row={row_no}: {error}", flush=True)
                continue
            by_region[region].append(row_no)

        for region, row_numbers in by_region.items():
            account = ACCOUNTS.get(region) or {}
            email = str(account.get("email") or "").strip()
            password = str(account.get("password") or "").strip()
            try:
                if not email or not password:
                    raise RuntimeError(f"{region} 尚未設定後台帳號密碼")
                print(f"START {region}: rows={','.join(map(str, row_numbers))}", flush=True)
                result = run_process_web(
                    env_name="prod",
                    region=region,
                    backend_email=email,
                    backend_password=password,
                    sheet_name=sheet_name,
                    start_row=min(row_numbers),
                    end_row=max(row_numbers),
                    selected_actions=ACTIONS,
                    logger=lambda msg: print(str(msg), flush=True),
                    allow_auto_lemon_shift=False,
                    selected_rows=row_numbers,
                ) or {}
                success = int(result.get("success_count", 0) or 0)
                fail = int(result.get("fail_count", 0) or 0)
                success_total += success
                fail_total += fail
                print(f"DONE {region}: success={success} fail={fail}", flush=True)
            except Exception as exc:
                fail_total += len(row_numbers)
                print(f"ERROR {region}: {exc}", flush=True)

        elapsed = max(time.monotonic() - started, 0.001)
        print(
            f"PROGRESS attempted={len(attempted)} success={success_total} fail={fail_total} "
            f"rate={len(attempted) / elapsed * 60:.1f}/min",
            flush=True,
        )
        if pause_seconds and load_pending(sheet_name, attempted):
            time.sleep(pause_seconds)

    remaining = len(_load_candidates(sheet_name))
    print(
        f"FINISH attempted={len(attempted)} success={success_total} fail={fail_total} remaining={remaining}",
        flush=True,
    )
    return 0 if fail_total == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="批次建單優化＋雲端批次成單")
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--pause-seconds", type=int, default=5)
    args = parser.parse_args()
    if args.chunk_size < 1:
        parser.error("--chunk-size 必須 >= 1")
    return run(args.sheet.strip(), args.chunk_size, max(args.max_rows, 0), max(args.pause_seconds, 0))


if __name__ == "__main__":
    raise SystemExit(main())
