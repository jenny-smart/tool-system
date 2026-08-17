from __future__ import annotations

# ============================================================
# tools/field_management/roster_raise.py
#
# 從「新增名冊及調薪.gs」（GAS，📋 新增名冊選單）移植而來：
#   1. 新增專員名冊（複製上期名冊，把離職人員移到離職名冊）
#   2. 新增調薪資料（複製上期調薪資料，重建 B/K/N 欄公式）
#   3. 調薪工作表轉為值
#
# GAS 用 SpreadsheetApp 的 UI（ui.prompt / sheet.copyTo）操作試算表；
# 這裡改用 Google Sheets API（batchUpdate duplicateSheet / values），
# 由 tools/field_management/ui.py 提供 Streamlit 介面呼叫。
# ============================================================

import argparse
import re
from datetime import datetime
from typing import Any

try:
    from .staff_profile import (
        clear_range,
        get_sheets_service,
        get_spreadsheet_id,
        load_system_config,
        write_values,
    )
except ImportError:
    from staff_profile import (
        clear_range,
        get_sheets_service,
        get_spreadsheet_id,
        load_system_config,
        write_values,
    )

try:
    from .google_sheet_reader import execute_with_retry
except ImportError:
    from google_sheet_reader import execute_with_retry

try:
    from .logger import log
except ImportError:
    from logger import log

from tools.common.log_to_sheet import write_target_log


SYSTEM_NAME = "外場排程系統"

HEADER_ROWS = 1
LEAVE_SHEET_NAME = "離職名冊"
ROSTER_SHEET_SUFFIX = "專員名冊"
RAISE_SHEET_SUFFIX = "調薪資料"
RAISE_IMPORT_MAX_ROW = 150
RAISE_FORMULA_COL_LETTER = "N"
RAISE_VALUE_END_COL_LETTER = "AE"

_RAISE_FORMULA_ARG_PATTERN = re.compile(r",\s*(\d+)\s*,\s*false\s*\)", re.IGNORECASE)


# ────────────────────────────────────────────────────────────
# 共用工具
# ────────────────────────────────────────────────────────────

def today_ym() -> str:
    return datetime.now().strftime("%Y%m")


def get_previous_ym(ym: str) -> str:
    year = int(ym[:4])
    month = int(ym[4:6]) - 1

    if month < 1:
        month = 12
        year -= 1

    return f"{year}{month:02d}"


def validate_ym(ym: str) -> None:
    if not re.fullmatch(r"\d{6}", str(ym or "")):
        raise ValueError(f"月份格式錯誤，請輸入 6 碼，例如：202604（收到：{ym}）")


def get_sheet_props_list(sheets, spreadsheet_id: str) -> list[dict[str, Any]]:
    meta = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,index))",
        )
    )
    return [s["properties"] for s in meta.get("sheets", [])]


def find_sheet_props(props_list: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    for props in props_list:
        if props.get("title") == title:
            return props
    return None


def duplicate_sheet(
    sheets,
    spreadsheet_id: str,
    source_sheet_id: int,
    new_name: str,
    insert_index: int,
) -> int:
    """在同一份試算表內複製工作表（對應 GAS sheet.copyTo(ss).setName(...)），
    並直接插入到 insert_index（對應 ss.moveActiveSheet 把新表移到來源表左方）。
    """
    res = execute_with_retry(
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "duplicateSheet": {
                            "sourceSheetId": source_sheet_id,
                            "insertSheetIndex": insert_index,
                            "newSheetName": new_name,
                        }
                    }
                ]
            },
        )
    )
    return int(res["replies"][0]["duplicateSheet"]["properties"]["sheetId"])


def get_values(
    sheets,
    spreadsheet_id: str,
    range_name: str,
    value_render_option: str = "FORMATTED_VALUE",
) -> list[list[Any]]:
    res = execute_with_retry(
        sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueRenderOption=value_render_option,
        )
    )
    return res.get("values", [])


def append_values(sheets, spreadsheet_id: str, range_name: str, values: list[list[Any]]) -> None:
    if not values:
        return
    execute_with_retry(
        sheets.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        )
    )


def delete_rows(sheets, spreadsheet_id: str, sheet_id: int, row_numbers_1indexed: list[int]) -> None:
    """刪除多列（1-indexed），由下而上刪除以避免索引位移。"""
    if not row_numbers_1indexed:
        return

    requests = []
    for row_number in sorted(set(row_numbers_1indexed), reverse=True):
        requests.append(
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_number - 1,
                        "endIndex": row_number,
                    }
                }
            }
        )

    execute_with_retry(
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        )
    )


# ────────────────────────────────────────────────────────────
# 專員名冊
# ────────────────────────────────────────────────────────────

def process_leave_rows(
    sheets,
    spreadsheet_id: str,
    roster_sheet_id: int,
    roster_sheet_name: str,
    leave_sheet_name: str,
) -> int:
    """把 F 欄（離職日）有值的列搬到離職名冊，並從專員名冊刪除。回傳搬移筆數。"""
    values = get_values(sheets, spreadsheet_id, f"'{roster_sheet_name}'!A{HEADER_ROWS + 1}:ZZ")

    if not values:
        return 0

    last_col = max((len(row) for row in values), default=0)
    rows_to_move: list[list[Any]] = []
    row_numbers_to_delete: list[int] = []

    for i, row in enumerate(values):
        f_value = row[5] if len(row) > 5 else ""
        if str(f_value).strip() != "":
            padded_row = row + [""] * (last_col - len(row))
            rows_to_move.append(padded_row)
            row_numbers_to_delete.append(HEADER_ROWS + 1 + i)

    if rows_to_move:
        append_values(sheets, spreadsheet_id, f"'{leave_sheet_name}'!A1", rows_to_move)

    delete_rows(sheets, spreadsheet_id, roster_sheet_id, row_numbers_to_delete)

    return len(rows_to_move)


def create_roster_sheet(area: str, target_ym: str, run_type: str = "手動") -> dict[str, Any]:
    validate_ym(target_ym)

    cfg = load_system_config(SYSTEM_NAME)
    prev_ym = get_previous_ym(target_ym)
    source_name = f"{prev_ym}{ROSTER_SHEET_SUFFIX}"
    new_name = f"{target_ym}{ROSTER_SHEET_SUFFIX}"
    spreadsheet_id = get_spreadsheet_id(cfg, "roster", area)

    status = "失敗"
    message = ""

    try:
        sheets = get_sheets_service()
        props_list = get_sheet_props_list(sheets, spreadsheet_id)

        source_props = find_sheet_props(props_list, source_name)
        if not source_props:
            raise RuntimeError(f"找不到來源工作表：{source_name}")

        if find_sheet_props(props_list, new_name):
            raise RuntimeError(f"目標工作表已存在：{new_name}")

        leave_props = find_sheet_props(props_list, LEAVE_SHEET_NAME)
        if not leave_props:
            raise RuntimeError(f"找不到工作表：{LEAVE_SHEET_NAME}")

        new_sheet_id = duplicate_sheet(
            sheets, spreadsheet_id, source_props["sheetId"], new_name, source_props["index"]
        )

        moved_count = process_leave_rows(sheets, spreadsheet_id, new_sheet_id, new_name, LEAVE_SHEET_NAME)

        status = "成功"
        message = f"{source_name} → {new_name}｜已處理離職名冊（{moved_count} 筆）並移到前月左方"
        log(f"完成專員名冊：{message}")

        return {"area": area, "source": source_name, "new": new_name, "moved": moved_count}

    except Exception as exc:
        status = "失敗"
        message = str(exc)
        log(f"新增專員名冊失敗：{message}")
        raise

    finally:
        write_target_log(
            target_spreadsheet_id=spreadsheet_id,
            system_name=SYSTEM_NAME,
            function_name="新增專員名冊",
            run_type=run_type,
            area=area,
            date=target_ym,
            target_location=new_name,
            source_file=source_name,
            status=status,
            message=message,
        )


# ────────────────────────────────────────────────────────────
# 調薪資料
# ────────────────────────────────────────────────────────────

def get_raise_last_row_by_column_b(sheets, spreadsheet_id: str, sheet_name: str) -> int:
    values = get_values(sheets, spreadsheet_id, f"'{sheet_name}'!B3:B")
    for i, row in enumerate(values):
        cell = row[0] if row else ""
        if str(cell).strip() == "":
            return i + 2
    return len(values) + 2


def clear_raise_input_area(sheets, spreadsheet_id: str, sheet_name: str) -> None:
    clear_range(sheets, spreadsheet_id, f"'{sheet_name}'!B3:H")


def update_raise_core_formulas(
    sheets,
    spreadsheet_id: str,
    sheet_name: str,
    target_ym: str,
    prev_ym: str,
    roster_spreadsheet_id: str,
) -> None:
    b_formula = (
        f'=IMPORTRANGE("{roster_spreadsheet_id}","'
        f"{target_ym}{ROSTER_SHEET_SUFFIX}!B2:H{RAISE_IMPORT_MAX_ROW}\")"
    )
    write_values(sheets, spreadsheet_id, f"'{sheet_name}'!B3", [[b_formula]])

    last_row = get_raise_last_row_by_column_b(sheets, spreadsheet_id, sheet_name)
    if last_row < 3:
        return

    k_formulas = [
        [f"=IFNA(VLOOKUP($B{row},'{prev_ym}{RAISE_SHEET_SUFFIX}'!$B:$T,11,false),280)"]
        for row in range(3, last_row + 1)
    ]
    write_values(sheets, spreadsheet_id, f"'{sheet_name}'!K3:K{last_row}", k_formulas)


def update_raise_formula_column_and_clear_zero(
    sheets,
    spreadsheet_id: str,
    sheet_name: str,
    target_col_letter: str = RAISE_FORMULA_COL_LETTER,
) -> None:
    end_row = get_raise_last_row_by_column_b(sheets, spreadsheet_id, sheet_name)
    if end_row < 3:
        return

    range_name = f"'{sheet_name}'!{target_col_letter}3:{target_col_letter}{end_row}"
    row_count = end_row - 2

    formulas = get_values(sheets, spreadsheet_id, range_name, value_render_option="FORMULA")
    formulas = (formulas + [[] for _ in range(row_count - len(formulas))])[:row_count]

    updated: list[list[Any]] = []
    for row in formulas:
        f = row[0] if row else ""
        if not f:
            updated.append([""])
            continue

        new_formula = _RAISE_FORMULA_ARG_PATTERN.sub(
            lambda m: f",{int(m.group(1)) + 1},false)", f
        )
        updated.append([new_formula])

    write_values(sheets, spreadsheet_id, range_name, updated)

    displays = get_values(sheets, spreadsheet_id, range_name, value_render_option="FORMATTED_VALUE")
    displays = (displays + [[] for _ in range(row_count - len(displays))])[:row_count]

    ranges_to_clear = []
    for i, row in enumerate(displays):
        value = str(row[0]).strip() if row else ""
        if value == "0":
            ranges_to_clear.append(f"'{sheet_name}'!{target_col_letter}{i + 3}")

    if ranges_to_clear:
        execute_with_retry(
            sheets.spreadsheets().values().batchClear(
                spreadsheetId=spreadsheet_id,
                body={"ranges": ranges_to_clear},
            )
        )


def create_raise_sheet(area: str, target_ym: str, run_type: str = "手動") -> dict[str, Any]:
    validate_ym(target_ym)

    cfg = load_system_config(SYSTEM_NAME)
    prev_ym = get_previous_ym(target_ym)
    source_name = f"{prev_ym}{RAISE_SHEET_SUFFIX}"
    new_name = f"{target_ym}{RAISE_SHEET_SUFFIX}"

    salary_spreadsheet_id = get_spreadsheet_id(cfg, "salary", area)
    roster_spreadsheet_id = get_spreadsheet_id(cfg, "roster", area)

    status = "失敗"
    message = ""

    try:
        sheets = get_sheets_service()
        props_list = get_sheet_props_list(sheets, salary_spreadsheet_id)

        source_props = find_sheet_props(props_list, source_name)
        if not source_props:
            raise RuntimeError(f"找不到來源工作表：{source_name}")

        if find_sheet_props(props_list, new_name):
            raise RuntimeError(f"目標工作表已存在：{new_name}")

        duplicate_sheet(sheets, salary_spreadsheet_id, source_props["sheetId"], new_name, source_props["index"])

        clear_raise_input_area(sheets, salary_spreadsheet_id, new_name)
        update_raise_core_formulas(
            sheets, salary_spreadsheet_id, new_name, target_ym, prev_ym, roster_spreadsheet_id
        )
        update_raise_formula_column_and_clear_zero(sheets, salary_spreadsheet_id, new_name)

        status = "成功"
        message = f"{source_name} → {new_name}｜已清空 B3:H、更新 B3/K欄/N欄、移至前月左方"
        log(f"完成調薪資料：{message}")

        return {"area": area, "source": source_name, "new": new_name}

    except Exception as exc:
        status = "失敗"
        message = str(exc)
        log(f"新增調薪資料失敗：{message}")
        raise

    finally:
        write_target_log(
            target_spreadsheet_id=salary_spreadsheet_id,
            system_name=SYSTEM_NAME,
            function_name="新增調薪資料",
            run_type=run_type,
            area=area,
            date=target_ym,
            target_location=new_name,
            source_file=source_name,
            status=status,
            message=message,
        )


def convert_raise_sheet_to_values(area: str, ym: str, run_type: str = "手動") -> dict[str, Any]:
    validate_ym(ym)

    cfg = load_system_config(SYSTEM_NAME)
    salary_spreadsheet_id = get_spreadsheet_id(cfg, "salary", area)
    sheet_name = f"{ym}{RAISE_SHEET_SUFFIX}"

    status = "失敗"
    message = ""
    end_row = 0

    try:
        sheets = get_sheets_service()
        props_list = get_sheet_props_list(sheets, salary_spreadsheet_id)
        if not find_sheet_props(props_list, sheet_name):
            raise RuntimeError(f"找不到工作表：{sheet_name}")

        col_a = get_values(sheets, salary_spreadsheet_id, f"'{sheet_name}'!A:A")
        end_row = 0
        for i, row in enumerate(col_a):
            value = str(row[0]).strip() if row else ""
            if value != "":
                end_row = i + 1

        if end_row < 3:
            message = "A3 以下沒有可轉為值的資料"
            status = "成功"
            log(f"調薪工作表轉為值：{message}（{sheet_name}）")
            return {"area": area, "sheet": sheet_name, "converted_rows": 0}

        range_name = f"'{sheet_name}'!A3:{RAISE_VALUE_END_COL_LETTER}{end_row}"
        values = get_values(sheets, salary_spreadsheet_id, range_name, value_render_option="UNFORMATTED_VALUE")

        execute_with_retry(
            sheets.spreadsheets().values().update(
                spreadsheetId=salary_spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": values},
            )
        )

        status = "成功"
        message = f"A3:{RAISE_VALUE_END_COL_LETTER}{end_row} 轉為值"
        log(f"完成調薪工作表轉為值：{sheet_name} / {message}")

        return {"area": area, "sheet": sheet_name, "converted_rows": max(0, end_row - 2)}

    except Exception as exc:
        status = "失敗"
        message = str(exc)
        log(f"調薪工作表轉為值失敗：{message}")
        raise

    finally:
        write_target_log(
            target_spreadsheet_id=salary_spreadsheet_id,
            system_name=SYSTEM_NAME,
            function_name="調薪工作表轉為值",
            run_type=run_type,
            area=area,
            date=ym,
            target_location=f"{sheet_name}!A3:{RAISE_VALUE_END_COL_LETTER}{end_row}",
            source_file=sheet_name,
            status=status,
            message=message,
        )


# ────────────────────────────────────────────────────────────
# CLI（手動測試用；主要介面在 tools/field_management/ui.py）
# ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="外場排程系統：新增名冊及調薪")
    parser.add_argument(
        "--action",
        required=True,
        choices=["create-roster", "create-raise", "convert-raise"],
    )
    parser.add_argument("--area", required=True, choices=["台北", "台中"])
    parser.add_argument("--ym", required=True, help="期別，格式：YYYYMM")
    parser.add_argument("--run-type", default="手動")
    args = parser.parse_args()

    if args.action == "create-roster":
        result = create_roster_sheet(args.area, args.ym, args.run_type)
    elif args.action == "create-raise":
        result = create_raise_sheet(args.area, args.ym, args.run_type)
    else:
        result = convert_raise_sheet_to_values(args.area, args.ym, args.run_type)

    log(f"執行結果：{result}")


if __name__ == "__main__":
    main()
