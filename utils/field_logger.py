"""
utils/field_logger.py

外場排程系統 — 兩層打卡工具
-------------------------------
Layer 1：中央打卡
  - 目標：log_spreadsheet_id（systems.yaml 設定）
  - 工作表：外場排程執行Log

Layer 2：目標執行檔打卡
  - 目標：各流程操作的 roster / salary / office 試算表
  - 工作表：外場排程系統執行Log（不存在時自動建立）

欄位順序（兩層相同）：
  執行時間 | 系統 | 功能 | 執行方式 | 區域 | 期別/日期 | 目標位置 | 來源檔名 | 結果 | 訊息
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")

SYSTEM_NAME = "外場排程系統"
CENTRAL_LOG_SHEET = "外場排程執行Log"
TARGET_LOG_SHEET = "外場排程系統執行Log"

LOG_HEADERS = [
    "執行時間",
    "系統",
    "功能",
    "執行方式",
    "區域",
    "期別/日期",
    "目標位置",
    "來源檔名",
    "結果",
    "訊息",
]


def _now_str() -> str:
    return datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_sheet_with_header(sheets, spreadsheet_id: str, sheet_name: str) -> None:
    """
    確保工作表存在且第一列是欄位標題。
    若工作表不存在，先建立再寫標題。
    若已存在但第一列是空的，補寫標題。
    """
    meta = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.title",
    ).execute()

    existing = [
        s["properties"]["title"]
        for s in meta.get("sheets", [])
    ]

    if sheet_name not in existing:
        # 建立新工作表
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {"title": sheet_name}
                        }
                    }
                ]
            },
        ).execute()

    # 檢查第一列是否已有標題
    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:J1",
    ).execute()

    first_row = result.get("values", [[]])[0] if result.get("values") else []

    if not first_row or first_row[0] != "執行時間":
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="RAW",
            body={"values": [LOG_HEADERS]},
        ).execute()


def _append_row(sheets, spreadsheet_id: str, sheet_name: str, row: list) -> None:
    """在指定工作表最後一列追加一筆記錄。"""
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def write_log(
    sheets,
    *,
    # Layer 1 中央打卡
    central_spreadsheet_id: str,
    # Layer 2 目標執行檔打卡（沒有就傳 None 或空字串）
    target_spreadsheet_id: str | None = None,
    # 欄位內容
    function_name: str,
    run_type: str,
    area: str,
    date_string: str,
    target_location: str = "",
    source_file_name: str = "",
    status: str,
    message: str = "",
) -> None:
    """
    同時寫入中央打卡 + 目標執行檔打卡。

    Parameters
    ----------
    sheets
        Google Sheets API resource（已授權）
    central_spreadsheet_id
        中央打卡試算表 ID（來自 systems.yaml log_spreadsheet_id）
    target_spreadsheet_id
        目標執行檔試算表 ID（roster / salary / office 等）
        傳 None 或空字串時跳過 Layer 2
    function_name
        功能名稱，例如「外場排班統計表」
    run_type
        執行方式：「手動」或「自動」
    area
        區域：「台北」、「台中」等
    date_string
        期別或日期，例如「20260529」
    target_location
        目標位置，例如貼入的工作表名稱或儲存格
    source_file_name
        來源檔名
    status
        結果：「成功」、「失敗」、「略過」
    message
        訊息說明
    """
    now = _now_str()
    row = [
        now,
        SYSTEM_NAME,
        function_name,
        run_type,
        area,
        date_string,
        target_location,
        source_file_name,
        status,
        message,
    ]

    # ── Layer 1：中央打卡 ──────────────────────────────────
    if central_spreadsheet_id:
        try:
            _ensure_sheet_with_header(sheets, central_spreadsheet_id, CENTRAL_LOG_SHEET)
            _append_row(sheets, central_spreadsheet_id, CENTRAL_LOG_SHEET, row)
        except Exception as e:
            # 打卡失敗不應中斷主流程，只印 warning
            print(f"[field_logger] 中央打卡失敗：{e}")

    # ── Layer 2：目標執行檔打卡 ────────────────────────────
    if target_spreadsheet_id:
        try:
            _ensure_sheet_with_header(sheets, target_spreadsheet_id, TARGET_LOG_SHEET)
            _append_row(sheets, target_spreadsheet_id, TARGET_LOG_SHEET, row)
        except Exception as e:
            print(f"[field_logger] 目標執行檔打卡失敗：{e}")
