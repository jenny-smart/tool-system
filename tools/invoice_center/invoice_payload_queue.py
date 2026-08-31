from __future__ import annotations

import re
from typing import Any

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.local_agent_queue import now_text
from tools.lemon_backend.stored_value_sheet import get_worksheet


SHEET_NAME = "發票開立佇列"
HEADERS = ["created_at", "created_by", "area", "order_no", "payload_json", "status", "message", "source_row"]



def _is_order_number_value(value: str, order_no: str) -> bool:
    """Detect the known bad value where O contains its own order number."""
    normalized_value = re.sub(r"\s+", "", str(value or "")).upper()
    order_base = re.sub(
        r"-\d+$",
        "",
        re.sub(r"\s+", "", str(order_no or "")).upper(),
    )
    return bool(normalized_value and order_base and normalized_value == order_base)


def _ensure_sheet(service: Any, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    titles = {sheet.get("properties", {}).get("title") for sheet in meta.get("sheets", [])}
    if SHEET_NAME not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME, "gridProperties": {"frozenRowCount": 1}}}}]},
        ).execute()
    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A1:H1",
    ).execute().get("values", [])
    if not current or current[0][: len(HEADERS)] != HEADERS:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()


def _all_rows(service: Any, spreadsheet_id: str) -> list[dict[str, Any]]:
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A2:H",
    ).execute().get("values", [])
    result: list[dict[str, Any]] = []
    for row_no, row in enumerate(rows, start=2):
        values = list(row) + [""] * (len(HEADERS) - len(row))
        item = dict(zip(HEADERS, values[: len(HEADERS)]))
        item["_row"] = row_no
        result.append(item)
    return result


def enqueue_payload(
    area: str,
    order_no: str,
    payload_json: str,
    created_by: str = "Tool System",
    source_row: int = 0,
) -> int:
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(service, spreadsheet_id)

    # 同一地區／訂單只保留一筆 pending。若之前失敗或重複送出，直接覆蓋最新 Payload，
    # 避免 Agent 一次撈到歷史 pending 而顯示 9 筆、重複開立。
    normalized_area = str(area).strip()
    normalized_order = str(order_no).strip()
    for item in reversed(_all_rows(service, spreadsheet_id)):
        if str(item.get("area", "")).strip() != normalized_area:
            continue
        if str(item.get("order_no", "")).strip() != normalized_order:
            continue
        if str(item.get("status", "")).strip() != "pending":
            continue
        row_no = int(item.get("_row") or 0)
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!A{row_no}:H{row_no}",
            valueInputOption="RAW",
            body={"values": [[now_text(), created_by, normalized_area, normalized_order, payload_json, "pending", "等待鯨躍貼入", int(source_row or 0)]]},
        ).execute()
        return row_no

    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A:H",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[now_text(), created_by, normalized_area, normalized_order, payload_json, "pending", "等待鯨躍貼入", int(source_row or 0)]]},
    ).execute()
    updated = result.get("updates", {}).get("updatedRange", "")
    try:
        return int(updated.rsplit("!A", 1)[1].split(":", 1)[0])
    except Exception:
        return 0


def list_pending_payloads(area: str) -> list[dict[str, Any]]:
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(service, spreadsheet_id)
    rows = _all_rows(service, spreadsheet_id)

    # awaiting_save 代表可能已開立但尚未回填，也必須交回 Agent 復原；
    # 否則中止舊程序後會永久略過，重新送出又有重複開票風險。
    actionable_statuses = {"pending", "awaiting_save"}
    latest_by_order: dict[str, dict[str, Any]] = {}
    duplicates: list[int] = []
    for item in rows:
        if str(item.get("area", "")).strip() != area:
            continue
        if str(item.get("status", "")).strip() not in actionable_statuses:
            continue
        order_no = str(item.get("order_no", "")).strip()
        if not order_no:
            continue
        previous = latest_by_order.get(order_no)
        if previous is not None:
            duplicates.append(int(previous.get("_row") or 0))
        latest_by_order[order_no] = item

    for row_no in duplicates:
        if row_no:
            update_payload_status(row_no, "superseded", "同訂單有較新的待處理 Payload，已略過")

    pending = sorted(latest_by_order.values(), key=lambda item: int(item.get("_row") or 0))
    if not pending:
        return []

    # 舊程序可能已成功回填 O 欄，但來不及把 Payload 改成 completed。
    # 在交給 Agent 前核對來源列；訂單相同且已有發票即自動結案，
    # 避免歷史 awaiting_save 擋住真正的新單。
    worksheet = get_worksheet(area)
    actionable: list[dict[str, Any]] = []
    for item in pending:
        source_row = int(item.get("source_row") or 0)
        order_no = str(item.get("order_no") or "").strip()
        if source_row >= 2:
            values = worksheet.get(f"G{source_row}:O{source_row}")
            source = list(values[0] if values else []) + [""] * 9
            current_order = str(source[0] or "").strip()
            current_invoice = str(source[8] or "").strip().upper()
            if (
                current_order == order_no
                and current_invoice
                and not _is_order_number_value(current_invoice, current_order)
            ):
                write_invoice_result(area, source_row, order_no, current_invoice)
                update_payload_status(
                    int(item.get("_row") or 0),
                    "completed",
                    f"來源 O 欄已有發票，佇列自動結案：{current_invoice}",
                )
                continue
        actionable.append(item)

    return actionable


def update_payload_status(row_no: int, status: str, message: str = "") -> None:
    if not row_no:
        return
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(service, spreadsheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!F{row_no}:G{row_no}",
        valueInputOption="RAW",
        body={"values": [[status, message]]},
    ).execute()


def write_invoice_result(area: str, source_row: int, order_no: str, invoice_no: str) -> None:
    """Write O/AA and mark B collected only when M is non-empty, after row validation."""
    row_no = int(source_row or 0)
    if row_no < 2:
        raise ValueError("Payload 缺少清潔異動來源列號，禁止猜測回填列")

    normalized_order = str(order_no or "").strip()
    normalized_invoice = str(invoice_no or "").strip().upper()
    if not normalized_invoice:
        raise ValueError("發票號碼空白")

    ws = get_worksheet(area)
    values = ws.get(f"B{row_no}:AA{row_no}")
    row = list(values[0] if values else []) + [""] * 26
    current_status = str(row[0] or "").strip()
    current_order = str(row[5] or "").strip()
    payment_marker = str(row[11] or "").strip()
    current_invoice = str(row[13] or "").strip().upper()
    current_time = str(row[25] or "").strip()

    if current_order != normalized_order:
        raise RuntimeError(
            f"清潔異動第 {row_no} 列訂單已變更：預期 {normalized_order}，實際 {current_order or '空白'}"
        )
    mistaken_order_value = _is_order_number_value(current_invoice, current_order)
    if current_invoice and current_invoice != normalized_invoice and not mistaken_order_value:
        raise RuntimeError(
            f"清潔異動第 {row_no} 列 O 欄已有其他發票號碼 {current_invoice}，禁止覆蓋"
        )

    updates = []
    if not current_invoice or mistaken_order_value:
        updates.append({"range": f"O{row_no}", "values": [[normalized_invoice]]})
    if not current_time:
        updates.append({"range": f"AA{row_no}", "values": [[now_text()]]})
    if payment_marker and current_status != "已收款":
        updates.append({"range": f"B{row_no}", "values": [["已收款"]]})
    if updates:
        ws.batch_update(updates)
