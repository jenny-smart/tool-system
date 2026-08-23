from __future__ import annotations

from typing import Any

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.local_agent_queue import now_text
from tools.memo_system.change_order import get_worksheet


SHEET_NAME = "發票開立佇列"
HEADERS = ["created_at", "created_by", "area", "order_no", "payload_json", "status", "message", "source_row"]


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


def enqueue_payload(\n    area: str,\n    order_no: str,\n    payload_json: str,\n    created_by: str = "Tool System",\n    source_row: int = 0,\n) -> int:
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

    # 只回傳每個訂單最新一筆 pending；舊的重複 pending 自動標記 superseded。
    latest_by_order: dict[str, dict[str, Any]] = {}
    duplicates: list[int] = []
    for item in rows:
        if str(item.get("area", "")).strip() != area:
            continue
        if str(item.get("status", "")).strip() != "pending":
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

    return sorted(latest_by_order.values(), key=lambda item: int(item.get("_row") or 0))


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
    """Write the issued invoice number to O and creation time to AA without guessing the row."""
    row_no = int(source_row or 0)
    if row_no < 2:
        raise ValueError("Payload 缺少清潔異動來源列號，禁止猜測回填列")

    normalized_order = str(order_no or "").strip()
    normalized_invoice = str(invoice_no or "").strip().upper()
    if not normalized_invoice:
        raise ValueError("發票號碼空白")

    ws = get_worksheet(area)
    values = ws.get(f"G{row_no}:AA{row_no}")
    row = list(values[0] if values else []) + [""] * 21
    current_order = str(row[0] or "").strip()
    current_invoice = str(row[8] or "").strip().upper()
    current_time = str(row[20] or "").strip()

    if current_order != normalized_order:
        raise RuntimeError(
            f"清潔異動第 {row_no} 列訂單已變更：預期 {normalized_order}，實際 {current_order or '空白'}"
        )
    if current_invoice and current_invoice != normalized_invoice:
        raise RuntimeError(
            f"清潔異動第 {row_no} 列 O 欄已有其他發票號碼 {current_invoice}，禁止覆蓋"
        )

    updates = []
    if not current_invoice:
        updates.append({"range": f"O{row_no}", "values": [[normalized_invoice]]})
    if not current_time:
        updates.append({"range": f"AA{row_no}", "values": [[now_text()]]})
    if updates:
        ws.batch_update(updates)
