"""Compare calendar exports with schedules without overwriting booked rows."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

FIELDS = ["服務人時", "備註", "姓名", "電話", "地址", "日期", "開始時間", "結束時間", "狀態"]
META_HEADERS = ["日曆事件ID", "日曆異動", "日曆異動內容", "更新日期"]
COLORS = {
    "新增": {"red": 1, "green": .96, "blue": .72},
    "未成單異動": {"red": 1, "green": .96, "blue": .72},
    "已成單異動": {"red": 1, "green": .84, "blue": .67},
    "需人工核對": {"red": 1, "green": .84, "blue": .67},
    "本次日曆未見": {"red": 1, "green": .84, "blue": .84},
}
REPORT_HEADERS = ["比對時間", "工作表", "排程列號", "異動類型", "姓名", "電話", "原日期", "日曆日期",
                  "訂單編號", "差異內容", "處理方式", "日曆事件ID", "比對識別碼"]


def _text(value):
    return str(value or "").strip()


def _canonical(value, column):
    text = _text(value)
    if column in (0, 2, 4, 8):
        return re.sub(r"\s+", "", text)
    if column == 3:
        digits = re.sub(r"\D", "", text)
        return "0" + digits if len(digits) == 9 and digits.startswith("9") else digits
    if column == 5:
        for pattern in ("%Y/%m/%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
            except ValueError:
                pass
    if column in (6, 7):
        for pattern in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(text, pattern).strftime("%H:%M")
            except ValueError:
                pass
    return text


def _key(values, columns):
    return tuple(_canonical(values[c], c) for c in columns)


def _diff(old, new):
    # Booking writes 已安排 to Sheets; calendar 未安排 is not a service change.
    ignored = {8} if _text(old[12]) and _canonical(old[8], 8) == "已安排" and _canonical(new[8], 8) == "未安排" else set()
    return "；".join(f"{FIELDS[c]}：{_text(old[c]) or '空白'} → {_text(new[c]) or '空白'}"
                    for c in range(9) if c not in ignored and _canonical(old[c], c) != _canonical(new[c], c))


def plan_schedule(existing, sources, sheet_name, foreign_events=None, coverage=None):
    """Return cell updates and a review report; ambiguous matches never create a new row."""
    foreign_events = foreign_events or {}
    old = {i + 2: (list(row) + [""] * 33)[:33] for i, row in enumerate(existing) if any(_text(v) for v in row)}
    incoming = []
    for row in sources:
        if not any(_text(v) for v in row[:9]):
            continue
        padded = (list(row) + [""] * 19)[:19]
        values = padded[:9] + [""] * 24
        values[3] = _canonical(values[3], 3)
        values[30] = _text(padded[18])
        incoming.append(values)
    event_counts = Counter(row[30] for row in incoming if row[30])
    if any(count > 1 for count in event_counts.values()):
        raise RuntimeError("日曆匯出有重複事件ID，未更新排程，請重新匯出核對")
    unmatched_old, unmatched_new = set(old), set(range(len(incoming)))
    pairs = {}

    def match(key_old, key_new, allow_recreated=False):
        left, right = defaultdict(list), defaultdict(list)
        for row in sorted(unmatched_old):
            key = key_old(old[row])
            if key: left[key].append(row)
        for index in sorted(unmatched_new):
            key = key_new(incoming[index])
            if key: right[key].append(index)
        for key in left.keys() & right.keys():
            if len(left[key]) == len(right[key]) == 1:
                row, index = left[key][0], right[key][0]
                # Known IDs must not be paired with a different known event.
                if not allow_recreated and old[row][30] and incoming[index][30] and old[row][30] != incoming[index][30]:
                    continue
                pairs[index] = row
                unmatched_old.remove(row)
                unmatched_new.remove(index)

    match(lambda v: v[30], lambda v: v[30])
    # Retrofit existing sheets conservatively, before their first event-ID snapshot.
    for columns in ((2, 3, 4, 5, 6, 7), (2, 3, 4, 5), (2, 3, 4, 6, 7), (2, 3, 4), (2, 3)):
        match(lambda v, c=columns: _key(v, c), lambda v, c=columns: _key(v, c),
              allow_recreated=columns in ((2, 3, 4, 5, 6, 7), (2, 3, 4, 5)))

    updates, changes = [], []

    def record(row, kind, before, after, detail, action, target_sheet=sheet_name):
        signature = [target_sheet, row, kind, before[:9], after[:9], before[12], after[30]]
        fingerprint = hashlib.sha256(json.dumps(signature, ensure_ascii=False, default=str).encode()).hexdigest()
        changes.append({"row": row, "sheet": target_sheet, "kind": kind, "before": before,
                        "after": after, "detail": detail, "action": action, "id": fingerprint})

    for index, row in sorted(pairs.items(), key=lambda pair: pair[1]):
        before, after = old[row], incoming[index]
        detail = _diff(before, after)
        values = before.copy()
        if after[30]: values[30] = after[30]
        kind = ""
        if detail:
            kind = "已成單異動" if _text(before[12]) else "未成單異動"
            values[:9] = after[:9]
            if _text(before[12]) and before[8] == "已安排" and after[8] == "未安排":
                values[8] = before[8]
            values[31:33] = [kind, detail]
            record(row, kind, before, after, detail,
                   "更新 A:J、保留 K:AG 成單資訊；已有訂單須人工確認是否同步異動" if _text(before[12]) else "更新 A:J，保留 K:AG 既有作業結果")
        if not detail and values[31] in ("需人工核對", "本次日曆未見"):
            values[31:33] = ["", ""]
        if values != before:
            updates.append({"row": row, "values": values, "old": before, "kind": kind})

    ambiguous = set()
    proposed = defaultdict(list)
    next_row = max(len(existing) + 2, max(old, default=1) + 1)
    for index in sorted(unmatched_new):
        after = incoming[index]
        event_id = after[30]
        if event_id and event_id in foreign_events:
            for foreign_sheet, row, before in foreign_events[event_id]:
                record(row, "跨月異動", before, after, _diff(before, after),
                       "另一月份已有同一日曆事件；不新增排程，請人工核對", foreign_sheet)
            continue
        related = [row for row in unmatched_old if _canonical(after[8], 8) != "暫停" and _key(old[row], (2, 3, 4)) == _key(after, (2, 3, 4))]
        if related:
            ambiguous.update(related)
            for candidate in related:
                proposed[candidate].append(after)
            record("", "需人工核對", old[related[0]], after,
                   "同一客戶有多筆未對應排程，無法唯一判定日期／時段異動",
                   "不新增待成單列；請依日曆事件ID核對原列")
            continue
        # A different known event for an existing booked customer can also be a deleted/recreated event.
        booked_related = [row for row in old if _canonical(after[8], 8) != "暫停" and _text(old[row][12]) and _key(old[row], (2, 3)) == _key(after, (2, 3))]
        if booked_related:
            ambiguous.update(booked_related)
            for candidate in booked_related:
                proposed[candidate].append(after)
            record("", "需人工核對", old[booked_related[0]], after,
                   "同一客戶已有成單，新增事件可能是刪除重建或額外服務",
                   "不新增待成單列，人工確認後再加入排程")
            continue
        values = after.copy()
        values[31:33] = ["新增", "本次日曆新增排程"]
        updates.append({"row": next_row, "values": values, "old": None, "kind": "新增"})
        record(next_row, "新增", [""] * 33, after, "本次日曆新增排程", "新增待處理排程；仍依狀態與單號判定是否可建單")
        next_row += 1

    for row in sorted(unmatched_old | ambiguous):
        before = old[row]
        if row not in ambiguous and coverage:
            date = _canonical(before[5], 5)
            if not (coverage[0] <= date <= coverage[1]):
                continue
        values = before.copy()
        kind = "需人工核對" if row in ambiguous else "本次日曆未見"
        detail = ("同一客戶有多筆排程，尚無法唯一對應日曆；保留原訂單，請核對此列日期與時段" if row in ambiguous
                  else "本次日曆匯出未包含此列；可能取消、移至其他月份或匯出範圍不同")
        if row in ambiguous:
            candidates = [f"日曆候選 {_text(item[5])} {_text(item[6])}–{_text(item[7])}：{_diff(before, item) or 'A:I 相同'}"
                          for item in proposed[row]]
            detail += "；" + "；".join(dict.fromkeys(candidates))
        if not _text(before[12]) and _canonical(before[8], 8) == "未安排": values[8] = "待確認"
        values[31:33] = [kind, detail]
        if values != before:
            updates.append({"row": row, "values": values, "old": before, "kind": kind})
        record(row, kind, before, [""] * 33, detail,
               "保留已成單資料，不自動刪除／重新成單" if _text(before[12]) else "保留原列，改待確認以停止自動成單")
    return {"updates": updates, "changes": changes, "count": len(old) + sum(u["old"] is None for u in updates)}


def _column(index):
    result = ""
    index += 1
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def _tracking_columns(header, rows):
    # Blank headers can still have user data underneath; append after all used cells.
    used = max([33] + [i + 1 for row in [header] + list(rows)
                           for i, value in enumerate(row) if _text(value)])
    columns = []
    for name in META_HEADERS:
        matches = [i for i, value in enumerate(header) if i >= 33 and value == name]
        if len(matches) > 1:
            raise RuntimeError(f"追蹤欄位重複：{name}，未更新排程")
        if matches:
            columns.append(matches[0])
        else:
            columns.append(used)
            used += 1
    return columns


def _read_tracking_columns(header, write_columns):
    # Read legacy AE:AG snapshots once; all new writes go after AG.
    result = []
    for name, fallback in zip(META_HEADERS, write_columns):
        matches = [i for i, value in enumerate(header) if i >= 30 and value == name]
        result.append(next((i for i in matches if i >= 33), matches[0] if matches else fallback))
    return result


def _internal_rows(rows, columns):
    return [(list(row[:30]) + [""] * max(0, 30 - len(row)) +
             [row[col] if col < len(row) else "" for col in columns[:3]]) for row in rows]


def _same_day_rows(existing, updates):
    rows = {i + 2: row for i, row in enumerate(existing)}
    rows.update({item["row"]: item["values"] for item in updates})
    groups = defaultdict(list)
    for row_num, values in rows.items():
        key = _key(values, (2, 4, 5))
        if all(key): groups[key].append(row_num)
    return sorted(row for group in groups.values() if len(group) > 1 for row in group)


def _positive_amount(value):
    try:
        return float(_text(value).replace(",", "").replace("NT$", "").replace("$", "")) > 0
    except (ValueError, TypeError):
        return False


def _balance_attention_rows(existing, updates, sources):
    rows = {i + 2: row for i, row in enumerate(existing)}
    rows.update({item["row"]: item["values"] for item in updates})
    by_id, by_slot, customer_q = defaultdict(list), defaultdict(list), {}
    for source in sources:
        if len(source) < 17: continue
        customer = _key(source, (2, 3))
        # Export displays the monthly Q amount on the customer's first row only.
        if _text(source[16]): customer_q[customer] = source[16]
        if len(source) > 18 and _text(source[18]): by_id[_text(source[18])].append(source)
        by_slot[_key(source, (2, 3, 4, 5, 6, 7))].append(source)
    highlighted = []
    for row_num, values in rows.items():
        if _text(values[12]) or "系統未產生新訂單編號" not in _text(values[14]): continue
        candidates = by_id.get(_text(values[30]), []) or by_slot.get(_key(values, (2, 3, 4, 5, 6, 7)), [])
        if any(_positive_amount(source[16] if _text(source[16]) else customer_q.get(_key(source, (2, 3)), ""))
               for source in candidates):
            highlighted.append(row_num)
    return sorted(highlighted)


def sync_schedule(ss, target_name, source_rows, headers, worksheet_not_found, compare_only=False, coverage=None):
    """Update calendar A:J; preserve existing K:AG and never clear a schedule."""
    period = target_name[-6:]
    in_month = []
    for source_row in source_rows:
        if len(source_row) < 6 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _canonical(source_row[5], 5)):
            raise RuntimeError("日曆匯出日期無法解析，未更新排程")
        if _canonical(source_row[5], 5).replace("-", "")[:6] == period:
            in_month.append(source_row)
    source_rows = in_month
    export_range = None
    if coverage and len(coverage[0]) >= 2:
        start, end = (_canonical(value, 5) for value in coverage[0][:2])
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) and start <= end:
            export_range = (start, end)
    created = False
    try:
        target = ss.worksheet(target_name)
        last_column = _column(target.col_count - 1)
        existing_range = f"A2:{last_column}"
        physical_rows = target.get(existing_range, value_render_option="FORMULA", date_time_render_option="FORMATTED_STRING")
        header_range = f"A1:{last_column}1"
        current_header = target.get(header_range, value_render_option="FORMULA", date_time_render_option="FORMATTED_STRING")
        header = current_header[0] if current_header else []
        columns = _tracking_columns(header, physical_rows)
        read_columns = _read_tracking_columns(header, columns)
        existing = _internal_rows(physical_rows, read_columns)
        if len(header) >= 30: headers = header[:30]
    except worksheet_not_found:
        target, existing = None, []
        columns = [33, 34, 35, 36]
        created = True
    # Detect moves to another month by stable event identity, avoiding duplicate orders.
    area = target_name[:-6]
    source_ids = {_text(row[18]) for row in source_rows if len(row) > 18 and _text(row[18])}
    foreign = defaultdict(list)
    if source_ids:
        for sheet in ss.worksheets():
            if sheet.title == target_name or sheet.col_count < 31 or not re.fullmatch(re.escape(area) + r"\d{6}", sheet.title): continue
            foreign_header = sheet.get(f"A1:{_column(sheet.col_count - 1)}1", value_render_option="FORMULA", date_time_render_option="FORMATTED_STRING")
            foreign_header = foreign_header[0] if foreign_header else []
            if META_HEADERS[0] not in foreign_header[30:]: continue
            foreign_rows = sheet.get(f"A2:{_column(sheet.col_count - 1)}", value_render_option="FORMULA", date_time_render_option="FORMATTED_STRING")
            foreign_columns = _tracking_columns(foreign_header, foreign_rows)
            for row_num, values in enumerate(_internal_rows(foreign_rows, _read_tracking_columns(foreign_header, foreign_columns)), 2):
                if values[30] in source_ids: foreign[values[30]].append((sheet.title, row_num, values))
    plan = plan_schedule(existing, source_rows, target_name, foreign, coverage=export_range)
    if not compare_only:
        if target is not None and (target.get(existing_range, value_render_option="FORMULA", date_time_render_option="FORMATTED_STRING") != physical_rows or
                                   target.get(header_range, value_render_option="FORMULA", date_time_render_option="FORMATTED_STRING") != current_header):
            raise RuntimeError("排程在比對期間已有更新，未覆寫；請待成單完成後重新比對")
        required_rows = max([len(existing) + 1, 2] + [u["row"] for u in plan["updates"]])
        required_cols = max(columns) + 1
        if target is None:
            target = ss.add_worksheet(title=target_name, rows=max(required_rows + 10, 200), cols=required_cols)
        elif target.col_count < required_cols or target.row_count < required_rows:
            target.resize(rows=max(target.row_count, required_rows), cols=max(target.col_count, required_cols))
        if created:
            target.update(values=[list(headers[:30])], range_name="A1:AD1", value_input_option="RAW")
        for column, name in zip(columns, META_HEADERS):
            target.update(values=[[name]], range_name=f"{_column(column)}1", value_input_option="RAW")
        year, month = int(target_name[-6:-2]), int(target_name[-2:])
        previous = f"{year - 1}12" if month == 1 else f"{year}{month - 1:02d}"
        lookup_sheet = f"{area}{previous}"
        updated_at = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        updates, phones, colors = [], [], []
        for change in plan["updates"]:
            row, values, before = change["row"], change["values"], change["old"]
            if before is None:
                values[9] = f"=xlookup(E{row},'{lookup_sheet}'!E:E,'{lookup_sheet}'!J:J)"
                updates.append({"range": f"A{row}:AD{row}", "values": [values[:30]]})
                phones.append({"range": f"D{row}", "values": [[values[3]]]})
            else:
                if values[:9] != before[:9]:
                    if _canonical(values[4], 4) != _canonical(before[4], 4):
                        values[9] = f"=xlookup(E{row},'{lookup_sheet}'!E:E,'{lookup_sheet}'!J:J)"
                        if values[9] != before[9]:
                            values[32] += f"；購買項目：{_text(before[9]) or '空白'} → 上月地址查詢公式"
                            for item in plan["changes"]:
                                if item["sheet"] == target_name and item["row"] == row:
                                    item["detail"] = values[32]
                    updates.append({"range": f"A{row}:J{row}", "values": [values[:10]]})
                    phones.append({"range": f"D{row}", "values": [[values[3]]]})
            for column, value in zip(columns[:3], values[30:33]):
                updates.append({"range": f"{_column(column)}{row}", "values": [[value]]})
            if before is None or values[:10] != before[:10] or values[31:33] != before[31:33]:
                updates.append({"range": f"{_column(columns[3])}{row}", "values": [[updated_at]]})
            if change["kind"]:
                colors.append({"range": f"A{row}:{_column(max(target.col_count - 1, max(columns)))}{row}", "format": {"backgroundColor": COLORS[change["kind"]]}})
        if not created and read_columns[:3] != columns[:3]:
            for row_num, values in enumerate(existing, 2):
                if any(u["row"] == row_num for u in plan["updates"]): continue
                for column, value in zip(columns[:3], values[30:33]):
                    if value: updates.append({"range": f"{_column(column)}{row_num}", "values": [[value]]})
        # Both active and paused calendar events can legitimately share a day.
        # Highlight every member of the group, without changing order data/status.
        for row in _same_day_rows(existing, plan["updates"]):
            colors.append({"range": f"A{row}:{_column(max(target.col_count - 1, max(columns)))}{row}",
                           "format": {"backgroundColor": {"red": .90, "green": .85, "blue": 1}}})
        for row in _balance_attention_rows(existing, plan["updates"], source_rows):
            colors.append({"range": f"A{row}:{_column(max(target.col_count - 1, max(columns)))}{row}",
                           "format": {"backgroundColor": {"red": 1, "green": .80, "blue": .80}}})
        if updates: target.batch_update(updates, value_input_option="USER_ENTERED")
        if phones: target.batch_update(phones, value_input_option="RAW")
        if colors: target.batch_format(colors)
        target.freeze(rows=1)
    report_name = f"排程差異_{target_name}"
    try:
        report = ss.worksheet(report_name)
    except worksheet_not_found:
        report = ss.add_worksheet(title=report_name, rows=max(len(plan["changes"]) + 10, 200), cols=13)
    report_rows = report.get("A2:M")
    known = {_text(row[12]) for row in report_rows if len(row) > 12}
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S %z")
    entries = []
    for item in plan["changes"]:
        report_id = item["id"] + (":preview" if compare_only else ":updated")
        if report_id in known: continue
        before, after = item["before"], item["after"]
        entries.append([timestamp, item["sheet"], item["row"], item["kind"], after[2] or before[2],
                        after[3] or before[3], before[5], after[5], before[12], item["detail"],
                        ("只比對，未更新排程。" if compare_only else "") + item["action"], after[30] or before[30], report_id])
        known.add(report_id)
    report.update(values=[REPORT_HEADERS], range_name="A1:M1", value_input_option="RAW")
    if entries: report.append_rows(entries, value_input_option="RAW")
    report.freeze(rows=1)
    counts = dict(Counter(item["kind"] for item in plan["changes"]))
    report_url = f"https://docs.google.com/spreadsheets/d/{ss.id}/edit#gid={report.id}" if getattr(ss, "id", "") and getattr(report, "id", "") else ""
    return {"report_url": report_url, "sheet": target_name, "count": plan["count"], "ok": True, "compare_only": compare_only,
            "differences": counts, "report": report_name, "created": created and not compare_only}
