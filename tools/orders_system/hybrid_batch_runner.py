# -*- coding: utf-8 -*-
"""Hybrid batch runner.

Rules:
- Recovery first: if backend already has the order, write it back and never recreate.
- Optimized modes: groups with 2+ rows run first through grouped submit.
- Single-row groups run directly one by one, without grouped_orders orchestration.
- Google Calendar is lazy: booking/writeback happens first; Calendar is initialized only
  for rows that actually need calendar sync after an order number is available.
"""
from __future__ import annotations

from collections import defaultdict
import requests

import orders as _orders
import batch_booking_safety as _safety
from accounts import ACCOUNTS


def _without_calendar(actions):
    return [x for x in (actions or []) if x != "改 Google 日曆"]


def _selected_work(df, selected_rows, start_row, end_row, region):
    if selected_rows is None:
        work = df[(df["__sheet_row__"] >= start_row) & (df["__sheet_row__"] <= end_row)].copy()
    else:
        wanted = {int(x) for x in selected_rows}
        work = df[df["__sheet_row__"].isin(wanted)].copy()
    if work.empty:
        return work
    return work[
        work.apply(
            lambda row: _orders.get_region_by_address(str(row.get("地址", "")), ACCOUNTS) == region,
            axis=1,
        )
    ].copy()


def _lazy_calendar_sync(row, result, region, state, logger):
    """Only called after the order result has already been written to Sheet."""
    order_no = str((result or {}).get("訂單編號", "") or "").strip()
    if not order_no:
        return result
    if state.get("service") is None and not state.get("failed"):
        try:
            state["service"] = _orders.build_gcal_service()
            logger("Google Calendar 已啟用（成單回填後才初始化）")
        except Exception as exc:
            state["failed"] = True
            logger(f"⚠️ Google Calendar 初始化失敗：{exc}")
    if state.get("service") is None:
        return result

    calendar_info = _orders.stage_calendar_color(row, state["service"], region)
    merged = dict(result or {})
    merged.update(calendar_info)
    try:
        merged.update(_orders.stage_update_status(order_no, merged, calendar_info, merged))
    except Exception as exc:
        logger(f"⚠️ {order_no} 日曆後續狀態同步失敗：{exc}")
    return merged


def _run_one_direct(
    session, ws, row_no, row, region, selected_actions, allow_auto_lemon_shift,
    used_order_nos, gcal_state, logger,
):
    """One row only. No grouped_orders/build_group_key orchestration."""
    core_actions = _without_calendar(selected_actions)
    token = _orders.get_csrf_token(session)

    def single_logger(message):
        logger(str(message).replace("本組", "單筆"))

    row_results = _orders.process_one_group(
        session,
        [(row_no, row)],
        token,
        None,
        region,
        None,
        core_actions,
        allow_auto_lemon_shift=allow_auto_lemon_shift,
        used_order_nos=used_order_nos,
        logger=single_logger,
        group_no=None,
    )
    result = row_results.get(row_no, {})
    # First durable writeback. A crash after this point still preserves the order number.
    _orders.update_sheet_rows(ws, {row_no: result})

    if "改 Google 日曆" in (selected_actions or []) and str(result.get("訂單編號", "")).strip():
        result = _lazy_calendar_sync(row, result, region, gcal_state, logger)
        _orders.update_sheet_rows(ws, {row_no: result})
    return result


def _run_existing_direct(session, ws, row_no, row, region, selected_actions, gcal_state, logger):
    core_actions = _without_calendar(selected_actions)
    result = _orders.process_existing_order_only(row, None, region, session, core_actions)
    _orders.update_sheet_rows(ws, {row_no: result})
    if "改 Google 日曆" in (selected_actions or []) and str(result.get("訂單編號", "")).strip():
        result = _lazy_calendar_sync(row, result, region, gcal_state, logger)
        _orders.update_sheet_rows(ws, {row_no: result})
    return result


def _result_stats(row_no, row, result, failed_records):
    if str((result or {}).get("結果", "")) == "失敗":
        failed_records.append({
            "row": row_no,
            "name": str(row.get("姓名", "")).strip(),
            "error": str((result or {}).get("原因", "")),
        })
        return 0, 1
    return 1, 0


def run_process_web_hybrid(
    *, env_name, region, backend_email, backend_password, sheet_name,
    start_row, end_row, selected_actions=None, logger=print,
    allow_auto_lemon_shift=False, selected_rows=None,
):
    selected_actions = selected_actions or ["建單", "寄確認信", "改 Google 日曆"]
    core_actions = _without_calendar(selected_actions)
    _safety._configure_runtime(env_name)

    # Recovery deliberately excludes Calendar. Calendar is handled only after durable Sheet writeback.
    ws, remaining, recovered, blocked, _ = _safety._recover_before_create(
        env_name, region, backend_email, backend_password, sheet_name,
        start_row, end_row, selected_rows, core_actions, logger,
    )

    ws, df = _orders.load_worksheet(sheet_name)
    work = _selected_work(df, selected_rows, start_row, end_row, region)
    recovered_set = set(recovered)
    blocked_set = set(blocked)
    remaining_set = set(int(x) for x in remaining)

    existing_rows = []
    create_rows = []
    recovered_rows = []
    for _, row in work.iterrows():
        row_no = int(row["__sheet_row__"])
        if row_no in blocked_set:
            continue
        if row_no in recovered_set:
            recovered_rows.append((row_no, row))
            continue
        order_no = str(row.get("訂單編號", "") or "").strip()
        if order_no:
            existing_rows.append((row_no, row))
        elif row_no in remaining_set:
            create_rows.append((row_no, row))

    session = requests.Session()
    if not _orders.login(session, backend_email, backend_password):
        raise RuntimeError("後台登入失敗，無法執行批次建單")

    gcal_state = {"service": None, "failed": False}
    used_order_nos = set()
    failed_records = []
    success_count = len(recovered)
    fail_count = len(blocked)
    processed = len(recovered) + len(blocked)

    # Recovered rows were already written back; only now, if requested, sync Calendar lazily.
    if "改 Google 日曆" in selected_actions:
        for row_no, row in recovered_rows:
            order_no = str(row.get("訂單編號", "") or "").strip()
            if not order_no:
                continue
            result = _orders.build_row_result(order_no=order_no, result="中斷復原", reason="")
            result = _lazy_calendar_sync(row, result, region, gcal_state, logger)
            _orders.update_sheet_rows(ws, {row_no: result})

    # Existing orders: direct sync, no grouping.
    for row_no, row in existing_rows:
        try:
            result = _run_existing_direct(session, ws, row_no, row, region, selected_actions, gcal_state, logger)
        except Exception as exc:
            result = _orders.build_row_result(
                result="失敗", reason=f"既有訂單同步失敗: {exc}", status_value="",
                staff="無人力", service_status="未處理", fare="0",
            )
            _orders.update_sheet_rows(ws, {row_no: result})
        ok, bad = _result_stats(row_no, row, result, failed_records)
        success_count += ok
        fail_count += bad
        processed += 1
        logger(f"✅ 既有訂單第 {row_no} 列已同步並回填。")

    grouped = defaultdict(list)
    for row_no, row in create_rows:
        grouped[_orders.build_group_key(row)].append((row_no, row))
    multi_groups = [items for items in grouped.values() if len(items) >= 2]
    single_rows = [items[0] for items in grouped.values() if len(items) == 1]
    multi_groups.sort(key=lambda items: (-len(items), min(row_no for row_no, _ in items)))
    single_rows.sort(key=lambda item: item[0])

    logger(
        f"混合優化：待建 {len(create_rows)} 筆；多筆組 {len(multi_groups)} 組／"
        f"{sum(len(x) for x in multi_groups)} 筆，單筆 {len(single_rows)} 筆。先多筆組，再單筆。"
    )

    # Multi-row groups first: one section lookup / multi-date submit where possible.
    for group_no, rows_with_idx in enumerate(multi_groups, 1):
        row_numbers = [row_no for row_no, _ in rows_with_idx]
        _safety._checkpoint(ws, row_numbers, message="多筆組已建立建單斷點；本組完成後立即回填")
        logger(f"▶ 多筆組 {group_no}/{len(multi_groups)}：列號 {'、'.join(map(str, row_numbers))}")
        try:
            token = _orders.get_csrf_token(session)
            row_results = _orders.process_one_group(
                session, rows_with_idx, token, None, region, None, core_actions,
                allow_auto_lemon_shift=allow_auto_lemon_shift,
                used_order_nos=used_order_nos, logger=logger, group_no=group_no,
            )
        except Exception as exc:
            row_results = {
                row_no: _orders.build_row_result(
                    result="失敗", reason=str(exc), status_value="",
                    staff="無人力", service_status="未處理", fare="0",
                )
                for row_no, _ in rows_with_idx
            }

        # First write all order numbers/results before any Calendar calls.
        _orders.update_sheet_rows(ws, row_results)
        logger(f"✅ 多筆組 {group_no}/{len(multi_groups)} 建單結果已先回填 Google Sheet。")

        if "改 Google 日曆" in selected_actions:
            for row_no, row in rows_with_idx:
                result = row_results.get(row_no, {})
                if str(result.get("訂單編號", "")).strip():
                    result = _lazy_calendar_sync(row, result, region, gcal_state, logger)
                    row_results[row_no] = result
                    _orders.update_sheet_rows(ws, {row_no: result})

        for row_no, row in rows_with_idx:
            ok, bad = _result_stats(row_no, row, row_results.get(row_no, {}), failed_records)
            success_count += ok
            fail_count += bad
            processed += 1

    # Singleton groups: direct one-by-one path, no grouped_orders orchestration.
    for index, (row_no, row) in enumerate(single_rows, 1):
        _safety._checkpoint(ws, [row_no], message="單筆已建立建單斷點；完成後立即回填")
        logger(f"▶ 單筆 {index}/{len(single_rows)}：第 {row_no} 列")
        try:
            result = _run_one_direct(
                session, ws, row_no, row, region, selected_actions,
                allow_auto_lemon_shift, used_order_nos, gcal_state, logger,
            )
        except Exception as exc:
            result = _orders.build_row_result(
                result="失敗", reason=str(exc), status_value="",
                staff="無人力", service_status="未處理", fare="0",
            )
            _orders.update_sheet_rows(ws, {row_no: result})
        ok, bad = _result_stats(row_no, row, result, failed_records)
        success_count += ok
        fail_count += bad
        processed += 1
        logger(f"✅ 單筆第 {row_no} 列已完成並回填。")

    return {
        "success": fail_count == 0,
        "sheet_name": sheet_name,
        "region": region,
        "env": env_name,
        "success_count": success_count,
        "fail_count": fail_count,
        "total_processed": processed,
        "recovered_count": len(recovered),
        "blocked_count": len(blocked),
        "failed_records": failed_records,
    }


def run_process_web_direct_single(**kwargs):
    """Original batch mode: force every requested row onto the direct single path."""
    kwargs = dict(kwargs)
    selected_rows = kwargs.get("selected_rows")
    if selected_rows is None:
        selected_rows = list(range(int(kwargs["start_row"]), int(kwargs["end_row"]) + 1))
    merged = {
        "success": True, "success_count": 0, "fail_count": 0,
        "total_processed": 0, "recovered_count": 0, "blocked_count": 0,
        "failed_records": [],
    }
    for row_no in sorted({int(x) for x in selected_rows}):
        one = dict(kwargs)
        one["start_row"] = row_no
        one["end_row"] = row_no
        one["selected_rows"] = [row_no]
        result = run_process_web_hybrid(**one) or {}
        for key in ("success_count", "fail_count", "total_processed", "recovered_count", "blocked_count"):
            merged[key] += int(result.get(key, 0) or 0)
        merged["failed_records"].extend(result.get("failed_records", []) or [])
    merged["success"] = merged["fail_count"] == 0
    return merged
