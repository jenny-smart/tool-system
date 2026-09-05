# -*- coding: utf-8 -*-
"""批次建單斷點保護與中斷復原。

- 原批次建單：逐列執行，一列完成/回填後才處理下一列。
- 優化/雲端批次：保留既有多人多日期批次核心，但改成每一組完成即回填。
- 重跑時先把後台已成立、Sheet 尚未回填的訂單補回，避免重複成單。
- 「每月確認」只做 Sheet 狀態防呆：Google 日曆不改色，表單狀態寫成「待確認」。
"""
from __future__ import annotations

import inspect
import re
from collections import defaultdict

import requests

import orders as _orders
from accounts import ACCOUNTS

_BASE_RUN_PROCESS_WEB = _orders.run_process_web
_BASE_STAGE_CALENDAR_COLOR = _orders.stage_calendar_color
_BASE_UPDATE_SHEET_ROWS = _orders.update_sheet_rows
_CHECKPOINT_RESULT = "處理中"
_RECOVERED_RESULT = "中斷復原"
_BLOCKED_RESULT = "待確認"


def _is_monthly_confirm_event(event) -> bool:
    text = " ".join([
        str((event or {}).get("summary") or ""),
        str((event or {}).get("description") or ""),
    ])
    return "每月確認/自行預約" in text or "每月確認" in text


def _find_row_calendar_event(row, gcal_service, region):
    if gcal_service is None:
        return None
    calendar_id = _orders.GOOGLE_CALENDAR_MAP.get(region)
    if not calendar_id:
        return None
    try:
        return _orders.find_matching_calendar_event(
            gcal_service,
            calendar_id,
            str(row.get("地址", "")).strip(),
            row.get("日期"),
            str(row.get("開始時間", "")).strip(),
            str(row.get("結束時間", "")).strip(),
        )
    except Exception:
        return None


def _stage_calendar_color_with_sheet_guard(row, gcal_service, region):
    """每月確認事件不動日曆，只把 Sheet 狀態交給回填層改成待確認。"""
    event = _find_row_calendar_event(row, gcal_service, region)
    if event and _is_monthly_confirm_event(event):
        old_color = _orders.color_name_from_id(event.get("colorId", ""))
        return {
            "日曆改色結果": "未改",
            "日曆改色原因": "每月確認：保留 Google 日曆原狀；表單狀態改為待確認",
            "日曆原色": old_color,
            "日曆新色": old_color,
            "狀態": "待確認",
        }
    return _BASE_STAGE_CALENDAR_COLOR(row, gcal_service, region)


def _update_sheet_rows_with_pending_guard(ws, row_results):
    """沿用既有回填；僅額外允許『待確認』覆寫表單狀態，不改其他既有規則。"""
    _BASE_UPDATE_SHEET_ROWS(ws, row_results)
    pending_rows = [
        int(row_num)
        for row_num, info in (row_results or {}).items()
        if str((info or {}).get("狀態", "")).strip() == "待確認"
    ]
    if not pending_rows:
        return

    headers = _orders.ensure_columns_in_sheet(ws)
    if "狀態" not in headers:
        return
    status_col = headers.index("狀態") + 1
    updates = [
        {
            "range": _orders.gspread.utils.rowcol_to_a1(row_num, status_col),
            "values": [["待確認"]],
        }
        for row_num in pending_rows
    ]
    if updates:
        ws.batch_update(updates)


# 所有批次入口共用同一層防呆；不修改 Google 日曆本身。
_orders.stage_calendar_color = _stage_calendar_color_with_sheet_guard
_orders.update_sheet_rows = _update_sheet_rows_with_pending_guard


def _configure_runtime(env_name: str) -> None:
    if env_name == "dev":
        _orders.BASE_URL = _orders.BASE_URL_DEV
        _orders.ORDER_PREFIX = _orders.ORDER_PREFIX_DEV
    else:
        _orders.BASE_URL = _orders.BASE_URL_PROD
        _orders.ORDER_PREFIX = _orders.ORDER_PREFIX_PROD
    _orders.LOGIN_URL = f"{_orders.BASE_URL}/login"
    _orders.BOOKING_URL = f"{_orders.BASE_URL}/booking/stored_value_routine"
    _orders.PURCHASE_URL = f"{_orders.BASE_URL}/purchase"
    _orders.GET_MEMBER_URL = f"{_orders.BASE_URL}/ajax/get_member"
    _orders.CHECK_CONTAIN_URL = f"{_orders.BASE_URL}/ajax/check_contain"
    _orders.CALCULATE_HOUR_URL = f"{_orders.BASE_URL}/ajax/calculate_hour"
    _orders.GET_SECTION_URL = f"{_orders.BASE_URL}/ajax/get_section"
    _orders.MAIL_SUCCESS_URL = f"{_orders.BASE_URL}/purchase/mail_success/{{order_no}}"


def _selected_df(df, selected_rows, start_row, end_row, region):
    if selected_rows is None:
        work = df[(df["__sheet_row__"] >= start_row) & (df["__sheet_row__"] <= end_row)].copy()
    else:
        wanted = {int(x) for x in selected_rows}
        work = df[df["__sheet_row__"].isin(wanted)].copy()
    if work.empty:
        return work
    return work[work.apply(lambda row: _orders.get_region_by_address(str(row.get("地址", "")), ACCOUNTS) == region, axis=1)]


def _row_key(row):
    try:
        date_s = _orders.get_date_str(row["日期"])
        actual_period = _orders.normalize_period_text(row["開始時間"], row["結束時間"])
        system_period = _orders.map_to_system_slot(
            row["開始時間"], row["結束時間"], row.get("服務人時", "")
        )["system_slot"]
    except Exception:
        return None
    return (
        _orders.normalize_phone(row.get("電話", "")),
        _orders.normalize_addr_for_match(row.get("地址", "")),
        date_s,
        actual_period.replace(" ", ""),
        system_period.replace(" ", ""),
    )


def _block_key(block):
    lines = block.get("lines", [])
    joined = "\n".join(lines)
    phone = ""
    for line in lines:
        raw = str(line).strip()
        if re.fullmatch(r"09\d{8}", raw):
            phone = _orders.normalize_phone(raw)
            break
    try:
        service_date, service_period = _orders._calchk_service_date_time_from_lines(lines)
    except Exception:
        service_date = service_period = ""
    try:
        address = _orders._calchk_address_from_lines(lines)
    except Exception:
        address = ""
    return {
        "order_no": str(block.get("order_no") or "").strip(),
        "phone": phone,
        "date": service_date,
        "period": str(service_period or "").replace(" ", ""),
        "address": _orders.normalize_addr_for_match(address),
        "joined_address": _orders.normalize_addr_for_match(joined),
    }


def _address_matches(target: str, parsed: dict) -> bool:
    if not target:
        return True
    core = target[:10] if len(target) >= 10 else target
    return bool(core) and (core in parsed.get("address", "") or core in parsed.get("joined_address", ""))


def _fetch_phone_blocks(session, phone: str, max_pages: int = 8):
    blocks = []
    for page in range(1, max_pages + 1):
        params = dict(_orders.PURCHASE_FILTER_PARAMS_TEMPLATE)
        params["phone"] = phone
        params["page"] = str(page)
        resp = session.get(_orders.PURCHASE_URL, params=params, headers=_orders.HEADERS, allow_redirects=True)
        if resp.status_code != 200:
            break
        page_blocks = _orders.extract_order_cards_from_purchase_html(resp.text)
        if not page_blocks:
            break
        blocks.extend(page_blocks)
        if len(page_blocks) < 20:
            break
    return blocks


def _calendar_recover(row, region, gcal_service):
    if gcal_service is None:
        return {
            "日曆改色結果": "未執行",
            "日曆改色原因": "Google Calendar 未啟用或初始化失敗",
            "日曆原色": "",
            "日曆新色": "",
        }
    info = _orders.stage_calendar_color(row, gcal_service, region)
    if str(info.get("狀態", "")).strip() == "待確認":
        return info
    old_color = str(info.get("日曆原色", "")).strip()
    new_color = str(info.get("日曆新色", "")).strip()
    if old_color == "香蕉黃" or new_color == "香蕉黃":
        return {
            "日曆改色結果": "成功",
            "日曆改色原因": "中斷復原：日曆已是香蕉黃，無需重複修改",
            "日曆原色": old_color or "香蕉黃",
            "日曆新色": "香蕉黃",
        }
    return info


def _checkpoint(ws, row_numbers, message="已建立建單斷點；若程式中斷，下次會先反查後台再決定是否建單"):
    payload = {
        int(row_no): {"結果": _CHECKPOINT_RESULT, "原因": message}
        for row_no in row_numbers
    }
    if payload:
        _orders.update_sheet_rows(ws, payload)


def _recover_before_create(
    env_name, region, backend_email, backend_password, sheet_name,
    start_row, end_row, selected_rows, selected_actions, logger,
):
    _configure_runtime(env_name)
    ws, df = _orders.load_worksheet(sheet_name)
    work = _selected_df(df, selected_rows, start_row, end_row, region)
    if work.empty:
        return ws, [], [], [], 0

    all_recorded = {
        str(value).strip()
        for value in df.get("訂單編號", []).tolist()
        if str(value).strip() not in ("", "nan", "None")
    }

    blanks = []
    for _, row in work.iterrows():
        row_no = int(row["__sheet_row__"])
        order_no = str(row.get("訂單編號", "") or "").strip()
        if order_no:
            continue
        key = _row_key(row)
        if key:
            blanks.append((row_no, row, key))

    if not blanks:
        return ws, [], [], [], 0

    session = requests.Session()
    if not _orders.login(session, backend_email, backend_password):
        raise RuntimeError("後台登入失敗，無法執行批次建單斷點檢查")

    gcal_service = None
    if "改 Google 日曆" in (selected_actions or []):
        try:
            gcal_service = _orders.build_gcal_service()
        except Exception as exc:
            logger(f"⚠️ 中斷復原：Google Calendar 初始化失敗：{exc}")

    rows_by_key = defaultdict(list)
    for row_no, row, key in blanks:
        rows_by_key[key].append((row_no, row))

    blocks_by_phone = {}
    recovered = []
    blocked = []
    remaining = []

    for key, row_items in rows_by_key.items():
        phone, address, date_s, actual_period, system_period = key
        if phone not in blocks_by_phone:
            blocks_by_phone[phone] = [_block_key(b) for b in _fetch_phone_blocks(session, phone)]

        candidates = []
        for parsed in blocks_by_phone[phone]:
            order_no = parsed.get("order_no")
            if not order_no or order_no in all_recorded:
                continue
            if parsed.get("phone") and parsed.get("phone") != phone:
                continue
            if parsed.get("date") != date_s:
                continue
            if parsed.get("period") not in {actual_period, system_period}:
                continue
            if not _address_matches(address, parsed):
                continue
            candidates.append(order_no)

        candidates = list(dict.fromkeys(candidates))
        if len(candidates) > len(row_items):
            msg = (
                f"後台找到 {len(candidates)} 筆未回填訂單，但工作表只有 {len(row_items)} 筆相同條件空白列；"
                "為避免誤配或重複成單，已停止這些列，請先人工確認。"
            )
            for row_no, _ in row_items:
                blocked.append(row_no)
                _orders.update_sheet_rows(ws, {row_no: {"結果": _BLOCKED_RESULT, "原因": msg}})
            logger(f"⚠️ {msg}")
            continue

        for (row_no, row), order_no in zip(row_items, candidates):
            meta = _orders.fetch_order_meta_by_order_no(session, order_no)
            result = _orders.build_row_result(
                order_no=order_no,
                result=_RECOVERED_RESULT,
                reason="偵測到後台已成立但工作表尚未回填；已補回訂單編號，避免重複成單。",
                staff=meta.get("服務人員", "無人力"),
                service_status=meta.get("服務狀態", "未處理"),
                fare=meta.get("車馬費", "0"),
            )
            if "寄確認信" in (selected_actions or []):
                result["確認信"] = "待確認（中斷復原不自動重寄）"
            if "改 Google 日曆" in (selected_actions or []):
                result.update(_calendar_recover(row, region, gcal_service))
            _orders.update_sheet_rows(ws, {row_no: result})
            recovered.append(row_no)
            all_recorded.add(order_no)
            logger(f"♻️ 第 {row_no} 列：後台已有 {order_no}，已補回工作表；不重複建單、不自動重寄確認信。")

        recovered_set = set(recovered)
        blocked_set = set(blocked)
        for row_no, _ in row_items:
            if row_no not in recovered_set and row_no not in blocked_set:
                remaining.append(row_no)

    return ws, sorted(remaining), sorted(recovered), sorted(blocked), len(blanks)


def _merge_counts(results, recovered_count=0, blocked_count=0):
    success = recovered_count
    fail = blocked_count
    processed = recovered_count + blocked_count
    failed_records = []
    for result in results:
        result = result or {}
        success += int(result.get("success_count", 0) or 0)
        fail += int(result.get("fail_count", 0) or 0)
        processed += int(result.get("total_processed", 0) or 0)
        failed_records.extend(result.get("failed_records", []) or [])
    return {
        "success": fail == 0,
        "success_count": success,
        "fail_count": fail,
        "total_processed": processed,
        "recovered_count": recovered_count,
        "blocked_count": blocked_count,
        "failed_records": failed_records,
    }


def _optimized_groups(sheet_name, remaining, region):
    """依 orders.run_process_web 相同 group key 切組，讓每一組獨立呼叫核心並立即回填。"""
    _, df = _orders.load_worksheet(sheet_name)
    wanted = set(int(x) for x in remaining)
    work = df[df["__sheet_row__"].isin(wanted)]
    groups = defaultdict(list)
    for _, row in work.iterrows():
        if _orders.get_region_by_address(str(row.get("地址", "")), ACCOUNTS) != region:
            continue
        row_no = int(row["__sheet_row__"])
        if row_no not in wanted:
            continue
        groups[_orders.build_group_key(row)].append(row_no)
    return [sorted(rows) for rows in groups.values() if rows]


def _safe_run(
    *, optimized: bool, env_name, region, backend_email, backend_password, sheet_name,
    start_row, end_row, selected_actions=None, logger=print,
    allow_auto_lemon_shift=False, selected_rows=None,
):
    selected_actions = selected_actions or ["建單", "寄確認信", "改 Google 日曆"]
    ws, remaining, recovered, blocked, original_blank_count = _recover_before_create(
        env_name, region, backend_email, backend_password, sheet_name,
        start_row, end_row, selected_rows, selected_actions, logger,
    )

    if not remaining:
        if original_blank_count == 0:
            return _BASE_RUN_PROCESS_WEB(
                env_name=env_name, region=region, backend_email=backend_email,
                backend_password=backend_password, sheet_name=sheet_name,
                start_row=start_row, end_row=end_row, selected_actions=selected_actions,
                logger=logger, allow_auto_lemon_shift=allow_auto_lemon_shift,
                selected_rows=selected_rows,
            )
        return _merge_counts([], len(recovered), len(blocked))

    results = []
    if optimized:
        groups = _optimized_groups(sheet_name, remaining, region)
        logger(f"優化批次：{len(remaining)} 列分成 {len(groups)} 組；改為每組完成立即回填。")
        for group_no, group_rows in enumerate(groups, 1):
            _checkpoint(ws, group_rows, message="本組已建立建單斷點；本組完成後立即回填")
            logger(f"▶ 優化第 {group_no}/{len(groups)} 組：列號 {'、'.join(map(str, group_rows))}")
            result = _BASE_RUN_PROCESS_WEB(
                env_name=env_name, region=region, backend_email=backend_email,
                backend_password=backend_password, sheet_name=sheet_name,
                start_row=min(group_rows), end_row=max(group_rows), selected_actions=selected_actions,
                logger=logger, allow_auto_lemon_shift=allow_auto_lemon_shift,
                selected_rows=group_rows,
            )
            results.append(result)
            logger(f"✅ 優化第 {group_no}/{len(groups)} 組已完成並回填 Google Sheet。")
    else:
        for row_no in remaining:
            _checkpoint(ws, [row_no])
            logger(f"▶ 單筆預約：第 {row_no} 列")
            results.append(_BASE_RUN_PROCESS_WEB(
                env_name=env_name, region=region, backend_email=backend_email,
                backend_password=backend_password, sheet_name=sheet_name,
                start_row=row_no, end_row=row_no, selected_actions=selected_actions,
                logger=logger, allow_auto_lemon_shift=allow_auto_lemon_shift,
                selected_rows=[row_no],
            ))

    merged = _merge_counts(results, len(recovered), len(blocked))
    merged.update({"sheet_name": sheet_name, "region": region, "env": env_name})
    return merged


def run_process_web_single(**kwargs):
    """原「批次建單」專用：逐列單筆預約＋斷點復原。"""
    return _safe_run(optimized=False, **kwargs)


def run_process_web_optimized(**kwargs):
    """「批次建單優化」與雲端版專用：每組即時回填＋斷點復原。"""
    return _safe_run(optimized=True, **kwargs)


def install_streamlit_batch_hooks() -> None:
    """將既有 Streamlit 入口接到安全 runner；不改其他建單功能。"""
    try:
        import batch_booking_optimized as batch_opt
        batch_opt.run_process_web = run_process_web_optimized
    except Exception:
        pass

    for frame_info in inspect.stack()[1:12]:
        g = frame_info.frame.f_globals
        filename = str(g.get("__file__", ""))
        if filename.endswith("ordersapp.py") and "run_process_web" in g:
            g["run_process_web"] = run_process_web_single
            break
