# -*- coding: utf-8 -*-
"""批次建單優化＋雲端批次成單：沿用人工優化版核心，並加上中斷復原。"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict

from accounts import ACCOUNTS
import batch_booking_optimized as batch_opt
from orders import get_region_by_address
from batch_recovery_meta import install_patch as install_recovery_meta_patch
from selected_row_status_guard import install_patch as install_selected_row_status_guard
from batch_booking_safety import run_process_web_optimized

install_selected_row_status_guard()
install_recovery_meta_patch()

ACTIONS = ["建單", "寄確認信", "改 Google 日曆"]


def load_pending(sheet_name: str, excluded: set[int] | None = None):
    excluded = excluded or set()
    result = []
    for _, row in batch_opt._load_candidates(sheet_name).sort_values("__sheet_row__").iterrows():
        row_no = int(row["__sheet_row__"])
        if row_no in excluded:
            continue
        address = batch_opt._text(row.get("地址"))
        region = get_region_by_address(address, ACCOUNTS)
        result.append((row_no, region or "", "" if region else f"無法依地址判斷地區：{address}"))
    return result


def run(sheet_name: str, chunk_size: int = 50, max_rows: int = 0, pause_seconds: int = 5) -> int:
    attempted: set[int] = set()
    success_total = fail_total = 0
    started = time.monotonic()
    while True:
        pending = load_pending(sheet_name, attempted)
        if max_rows:
            left = max_rows - len(attempted)
            if left <= 0:
                break
            pending = pending[:left]
        batch = pending[:chunk_size]
        if not batch:
            break
        by_region: dict[str, list[int]] = defaultdict(list)
        for row_no, region, error in batch:
            attempted.add(row_no)
            if error:
                fail_total += 1
                print(f"SKIP row={row_no}: {error}", flush=True)
            else:
                by_region[region].append(row_no)
        for region, rows in by_region.items():
            account = ACCOUNTS.get(region) or {}
            try:
                email = str(account.get("email") or "").strip()
                password = str(account.get("password") or "").strip()
                if not email or not password:
                    raise RuntimeError(f"{region} 尚未設定後台帳號密碼")
                print(f"START {region}: rows={','.join(map(str, rows))}", flush=True)
                result = run_process_web_optimized(
                    env_name="prod", region=region, backend_email=email, backend_password=password,
                    sheet_name=sheet_name, start_row=min(rows), end_row=max(rows),
                    selected_actions=ACTIONS, logger=lambda msg: print(str(msg), flush=True),
                    allow_auto_lemon_shift=False, selected_rows=rows,
                ) or {}
                success = int(result.get("success_count", 0) or 0)
                fail = int(result.get("fail_count", 0) or 0)
                success_total += success
                fail_total += fail
                print(
                    f"DONE {region}: success={success} fail={fail} "
                    f"recovered={int(result.get('recovered_count', 0) or 0)} "
                    f"blocked={int(result.get('blocked_count', 0) or 0)}",
                    flush=True,
                )
            except Exception as exc:
                fail_total += len(rows)
                print(f"ERROR {region}: {exc}", flush=True)
        elapsed = max(time.monotonic() - started, .001)
        print(f"PROGRESS attempted={len(attempted)} success={success_total} fail={fail_total} rate={len(attempted)/elapsed*60:.1f}/min", flush=True)
        if pause_seconds and load_pending(sheet_name, attempted):
            time.sleep(pause_seconds)
    remaining = len(batch_opt._load_candidates(sheet_name))
    print(f"FINISH attempted={len(attempted)} success={success_total} fail={fail_total} remaining={remaining}", flush=True)
    return 0 if fail_total == 0 else 2


def main():
    p = argparse.ArgumentParser(description="批次建單優化＋雲端批次成單")
    p.add_argument("--sheet", required=True)
    p.add_argument("--chunk-size", type=int, default=50)
    p.add_argument("--max-rows", type=int, default=0)
    p.add_argument("--pause-seconds", type=int, default=5)
    a = p.parse_args()
    if a.chunk_size < 1:
        p.error("--chunk-size 必須 >= 1")
    return run(a.sheet.strip(), a.chunk_size, max(a.max_rows, 0), max(a.pause_seconds, 0))


if __name__ == "__main__":
    raise SystemExit(main())
