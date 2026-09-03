# -*- coding: utf-8 -*-
"""Cloud/background runner for continuously finalizing Google Sheet orders."""
from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict

from accounts import ACCOUNTS
from batch_booking_optimized import _load_candidates, _text
from orders import get_region_by_address, run_process_web

DEFAULT_ACTIONS = ["建單", "寄確認信", "改 Google 日曆"]


def _credentials_for_region(region: str) -> tuple[str, str]:
    account = ACCOUNTS.get(region) or {}
    email = str(account.get("email") or "").strip()
    password = str(account.get("password") or "").strip()
    if not email or not password:
        raise RuntimeError(f"{region} 尚未設定後台帳號密碼環境變數")
    return email, password


def _pending_rows(sheet_name: str, limit: int | None = None) -> list[tuple[int, str]]:
    candidates = _load_candidates(sheet_name)
    rows: list[tuple[int, str]] = []
    for _, row in candidates.sort_values("__sheet_row__").iterrows():
        row_no = int(row["__sheet_row__"])
        address = _text(row.get("地址"))
        region = get_region_by_address(address, ACCOUNTS)
        if not region:
            print(f"SKIP row={row_no}: 無法依地址判斷地區：{address}", flush=True)
            continue
        rows.append((row_no, region))
        if limit and len(rows) >= limit:
            break
    return rows


def run(sheet_name: str, env: str = "prod", chunk_size: int = 50, pause_seconds: int = 5, max_rows: int = 0) -> int:
    total_success = 0
    total_fail = 0
    attempted: set[int] = set()
    started = time.monotonic()

    while True:
        remaining_limit = max_rows - len(attempted) if max_rows else None
        if remaining_limit is not None and remaining_limit <= 0:
            break
        scan_limit = min(chunk_size, remaining_limit) if remaining_limit else chunk_size
        pending = [(r, region) for r, region in _pending_rows(sheet_name, scan_limit) if r not in attempted]
        if not pending:
            break

        by_region: dict[str, list[int]] = defaultdict(list)
        for row_no, region in pending:
            by_region[region].append(row_no)
            attempted.add(row_no)

        for region, row_numbers in by_region.items():
            email, password = _credentials_for_region(region)
            print(f"START {region}: {len(row_numbers)} rows ({min(row_numbers)}-{max(row_numbers)})", flush=True)
            try:
                result = run_process_web(
                    env_name=env,
                    region=region,
                    backend_email=email,
                    backend_password=password,
                    sheet_name=sheet_name,
                    start_row=min(row_numbers),
                    end_row=max(row_numbers),
                    selected_actions=DEFAULT_ACTIONS if env == "prod" else ["建單"],
                    logger=lambda msg: print(str(msg), flush=True),
                    allow_auto_lemon_shift=False,
                    selected_rows=row_numbers,
                ) or {}
                success = int(result.get("success_count", 0) or 0)
                fail = int(result.get("fail_count", 0) or 0)
                total_success += success
                total_fail += fail
                print(f"DONE {region}: success={success} fail={fail}", flush=True)
            except Exception as exc:
                total_fail += len(row_numbers)
                print(f"ERROR {region}: {exc}", flush=True)

        elapsed = max(time.monotonic() - started, 0.001)
        rate = len(attempted) / elapsed * 60
        print(f"PROGRESS attempted={len(attempted)} success={total_success} fail={total_fail} rate={rate:.1f}/min", flush=True)
        if pause_seconds:
            time.sleep(pause_seconds)

    print(f"FINISH attempted={len(attempted)} success={total_success} fail={total_fail}", flush=True)
    return 0 if total_fail == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="批次成單雲端背景執行")
    parser.add_argument("--sheet", default=os.getenv("BATCH_FINALIZE_SHEET", "").strip(), help="工作表分頁名稱，例如台北202609")
    parser.add_argument("--env", choices=["prod", "dev"], default=os.getenv("BATCH_FINALIZE_ENV", "prod"))
    parser.add_argument("--chunk-size", type=int, default=int(os.getenv("BATCH_FINALIZE_CHUNK_SIZE", "50")))
    parser.add_argument("--pause-seconds", type=int, default=int(os.getenv("BATCH_FINALIZE_PAUSE_SECONDS", "5")))
    parser.add_argument("--max-rows", type=int, default=int(os.getenv("BATCH_FINALIZE_MAX_ROWS", "0")))
    args = parser.parse_args()
    if not args.sheet:
        parser.error("必須提供 --sheet")
    if args.chunk_size < 1:
        parser.error("--chunk-size 必須 >= 1")
    return run(args.sheet, args.env, args.chunk_size, max(args.pause_seconds, 0), max(args.max_rows, 0))


if __name__ == "__main__":
    raise SystemExit(main())
