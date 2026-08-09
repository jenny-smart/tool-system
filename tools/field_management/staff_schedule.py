from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from tools.common.google_auth import get_google_credentials
from googleapiclient.discovery import build

try:
    from .google_sheet_reader import read_drive_spreadsheet_values
except ImportError:
    from google_sheet_reader import read_drive_spreadsheet_values

try:
    from .logger import log
except ImportError:
    from logger import log

from tools.common.log_to_sheet import write_target_log


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


# ============================================================
# 日期
# ============================================================

def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def add_month_same_day_yyyymmdd(
    date_key: str,
    months: int = 1,
) -> str:
    year = int(date_key[:4])
    month = int(date_key[4:6]) + months
    day = int(date_key[6:8])

    while month > 12:
        year += 1
        month -= 12

    while month < 1:
        year -= 1
        month += 12

    return f"{year}{month:02d}{day:02d}"


# ============================================================
# 檔名
# ============================================================

def normalize_file_name(name: str) -> str:
    name = str(name or "")
    name = re.sub(
        r"\.(xlsx|xls|csv)$",
        "",
        name,
        flags=re.I,
    )
    name = (
        name
        .replace("－", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    name = re.sub(r"\s+", "", name)

    return name.strip()


# ============================================================
# Google Service Account
# ============================================================

def get_service_account_info() -> dict[str, Any]:
    raw = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT",
        "",
    ).strip()

    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass

    raw_json = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "",
    ).strip()

    if raw_json:
        return json.loads(raw_json)

    path = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "",
    ).strip()

    if path and Path(path).exists():
        return json.loads(
            Path(path).read_text(
                encoding="utf-8",
            )
        )

    try:
        import streamlit as st

        for key in [
            "GOOGLE_SERVICE_ACCOUNT",
            "gcp_service_account",
        ]:
            try:
                info = dict(st.secrets[key])

                if info:
                    return info

            except Exception:
                pass

    except Exception:
        pass

    raise RuntimeError(
        "找不到 GOOGLE_SERVICE_ACCOUNT 設定"
    )


def get_credentials():
    return get_google_credentials()


def get_drive_service():
    return build(
        "drive",
        "v3",
        credentials=get_credentials(),
        cache_discovery=False,
    )


def get_sheets_service():
    return build(
        "sheets",
        "v4",
        credentials=get_credentials(),
        cache_discovery=False,
    )


# ============================================================
# 系統設定
# ============================================================

def load_system_config(
    system_name: str = "外場日排程系統",
) -> dict[str, Any]:

    config_path = Path(
        "config/systems.yaml"
    )

    if not config_path.exists():
        raise RuntimeError(
            "找不到 config/systems.yaml"
        )

    data = yaml.safe_load(
        config_path.read_text(
            encoding="utf-8",
        )
    ) or {}

    for system in data.get(
        "systems",
        [],
    ):
        if (
            system.get("name")
            == system_name
        ):
            return system

    raise RuntimeError(
        f"systems.yaml 找不到系統："
        f"{system_name}"
    )


def get_folder_id(
    cfg: dict[str, Any],
    folder_type: str,
    area: str | None = None,
) -> str:

    value = (
        cfg
        .get("folder_ids", {})
        .get(folder_type)
    )

    if isinstance(
        value,
        dict,
    ):
        value = value.get(
            area or ""
        )

    if not value:
        raise RuntimeError(
            f"尚未設定資料夾 ID："
            f"{folder_type} / {area}"
        )

    return str(value).strip()


def get_spreadsheet_id(
    cfg: dict[str, Any],
    sheet_type: str,
    area: str,
) -> str:

    value = (
        cfg
        .get("spreadsheet_ids", {})
        .get(sheet_type, {})
        .get(area)
    )

    if not value:
        raise RuntimeError(
            f"尚未設定試算表 ID："
            f"{sheet_type} / {area}"
        )

    return str(value).strip()


def area_list_from_config(
    cfg: dict[str, Any],
) -> list[str]:

    if cfg.get("areas"):
        return list(
            cfg["areas"]
        )

    for key in [
        "roster",
        "salary",
        "office",
    ]:
        mapping = (
            cfg
            .get("spreadsheet_ids", {})
            .get(key)
        )

        if isinstance(
            mapping,
            dict,
        ):
            return list(
                mapping.keys()
            )

    return [
        "台北",
        "台中",
    ]


# ============================================================
# Drive 檔案
# ============================================================

def list_files_in_folder(
    drive,
    folder_id: str,
) -> list[dict[str, Any]]:

    files: list[
        dict[str, Any]
    ] = []

    token = None

    while True:
        res = (
            drive
            .files()
            .list(
                q=(
                    f"'{folder_id}' "
                    f"in parents "
                    f"and trashed=false"
                ),
                fields=(
                    "nextPageToken,"
                    "files("
                    "id,"
                    "name,"
                    "mimeType,"
                    "webViewLink,"
                    "modifiedTime"
                    ")"
                ),
                pageSize=1000,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )

        files.extend(
            res.get(
                "files",
                [],
            )
        )

        token = res.get(
            "nextPageToken"
        )

        if not token:
            return files


def find_file_by_possible_names(
    drive,
    folder_id: str,
    possible_names: list[str],
) -> dict[str, Any]:

    targets = [
        normalize_file_name(name)
        for name in possible_names
    ]

    candidates: list[str] = []

    for file in list_files_in_folder(
        drive,
        folder_id,
    ):
        name = file.get(
            "name",
            "",
        )

        candidates.append(name)

        if (
            normalize_file_name(name)
            in targets
        ):
            return file

    raise RuntimeError(
        "找不到來源檔案："
        + " / ".join(
            possible_names
        )
        + "；資料夾內目前檔案："
        + "、".join(
            candidates[:80]
        )
    )


# ============================================================
# Sheet 資料整理
# ============================================================

def ensure_rectangular(
    values: list[list[Any]],
    cols: int | None = None,
) -> list[list[Any]]:

    if not values:
        return []

    max_cols = (
        cols
        or max(
            len(row)
            for row in values
        )
    )

    output: list[
        list[Any]
    ] = []

    for row in values:
        new_row = list(
            row[:max_cols]
        )

        while (
            len(new_row)
            < max_cols
        ):
            new_row.append("")

        output.append(
            new_row
        )

    return output


def read_file_values(
    drive,
    sheets,
    file: dict[str, Any],
) -> list[list[Any]]:

    return (
        read_drive_spreadsheet_values(
            drive,
            sheets,
            file,
        )
    )


def is_blank_row(
    row: list[Any],
) -> bool:

    return all(
        str(value).strip() == ""
        for value in row
    )


# ============================================================
# Google Sheets 寫入
# ============================================================

def quote_sheet_name(
    sheet_name: str,
) -> str:
    """
    Google Sheets A1 notation 安全引用工作表名稱。
    """

    escaped = sheet_name.replace(
        "'",
        "''",
    )

    return f"'{escaped}'"


def clear_range(
    sheets,
    spreadsheet_id: str,
    range_name: str,
) -> None:

    (
        sheets
        .spreadsheets()
        .values()
        .clear(
            spreadsheetId=(
                spreadsheet_id
            ),
            range=range_name,
            body={},
        )
        .execute()
    )


def write_values(
    sheets,
    spreadsheet_id: str,
    range_name: str,
    values: list[list[Any]],
) -> None:

    if not values:
        return

    (
        sheets
        .spreadsheets()
        .values()
        .update(
            spreadsheetId=(
                spreadsheet_id
            ),
            range=range_name,
            valueInputOption=(
                "USER_ENTERED"
            ),
            body={
                "values": values
            },
        )
        .execute()
    )


def clear_and_write_values(
    sheets,
    spreadsheet_id: str,
    range_name: str,
    values: list[list[Any]],
) -> None:

    clear_range(
        sheets,
        spreadsheet_id,
        range_name,
    )

    write_values(
        sheets,
        spreadsheet_id,
        range_name,
        values,
    )


# ============================================================
# 專員班表工作表名稱
# ============================================================

def sheet_names_for_date(
    date_key: str,
) -> tuple[str, str]:

    date = datetime.strptime(
        date_key,
        "%Y%m%d",
    )

    mmdd = date.strftime(
        "%m%d"
    )

    return (
        f"專員班表查詢本月_{mmdd}",
        f"專員班表查詢次月_{mmdd}",
    )


def ensure_daily_sheet_name(
    sheets,
    spreadsheet_id: str,
    target_name: str,
    prefix: str,
) -> str:
    """
    確保指定類型的專員班表工作表
    名稱為今天的 MMDD。

    例如：
    專員班表查詢本月_0806
    →
    專員班表查詢本月_0807

    跨月亦相同：
    專員班表查詢本月_0831
    →
    專員班表查詢本月_0901

    不新增 Sheet，只修改現有 Sheet 名稱。
    """

    metadata = (
        sheets
        .spreadsheets()
        .get(
            spreadsheetId=(
                spreadsheet_id
            ),
            fields=(
                "sheets.properties("
                "sheetId,"
                "title,"
                "index"
                ")"
            ),
        )
        .execute()
    )

    properties = [
        sheet["properties"]
        for sheet
        in metadata.get(
            "sheets",
            [],
        )
    ]

    # --------------------------------------------------------
    # 已經是今天名稱
    # --------------------------------------------------------

    for props in properties:
        if (
            props.get("title")
            == target_name
        ):
            log(
                f"目標分頁已是當日名稱："
                f"{target_name}"
            )

            return target_name

    # --------------------------------------------------------
    # 找目前存在的同類工作表
    # --------------------------------------------------------

    candidates = [
        props
        for props in properties
        if (
            props
            .get("title", "")
            .startswith(prefix)
        )
    ]

    if not candidates:
        raise RuntimeError(
            f"找不到可改名的專員班表分頁："
            f"{prefix}*"
        )

    if len(candidates) > 1:
        names = [
            props.get(
                "title",
                "",
            )
            for props
            in candidates
        ]

        raise RuntimeError(
            "找到多個同類專員班表分頁，"
            "為避免誤改已停止執行："
            + "、".join(names)
        )

    source = candidates[0]

    old_name = source[
        "title"
    ]

    sheet_id = source[
        "sheetId"
    ]

    log(
        f"更新專員班表分頁名稱："
        f"{old_name} → {target_name}"
    )

    (
        sheets
        .spreadsheets()
        .batchUpdate(
            spreadsheetId=(
                spreadsheet_id
            ),
            body={
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": (
                                    sheet_id
                                ),
                                "title": (
                                    target_name
                                ),
                            },
                            "fields": (
                                "title"
                            ),
                        }
                    }
                ]
            },
        )
        .execute()
    )

    log(
        f"專員班表分頁名稱更新完成："
        f"{target_name}"
    )

    return target_name


# ============================================================
# 單區專員班表
# ============================================================

def run_staff_schedule_for_area(
    cfg: dict[str, Any],
    area: str,
    date_key: str,
    run_type: str = "手動",
) -> None:

    drive = get_drive_service()
    sheets = get_sheets_service()

    source_folder_id = get_folder_id(
        cfg,
        "staff_schedule",
        area,
    )

    target_spreadsheet_id = (
        get_spreadsheet_id(
            cfg,
            "office",
            area,
        )
    )

    current_sheet, next_sheet = (
        sheet_names_for_date(
            date_key
        )
    )

    jobs = [
        {
            "file_date_key": (
                date_key
            ),
            "target_sheet": (
                current_sheet
            ),
            "sheet_prefix": (
                "專員班表查詢本月_"
            ),
        },
        {
            "file_date_key": (
                add_month_same_day_yyyymmdd(
                    date_key,
                    1,
                )
            ),
            "target_sheet": (
                next_sheet
            ),
            "sheet_prefix": (
                "專員班表查詢次月_"
            ),
        },
    ]

    for job in jobs:

        file_date_key = job[
            "file_date_key"
        ]

        target_sheet = job[
            "target_sheet"
        ]

        sheet_prefix = job[
            "sheet_prefix"
        ]

        file_base = (
            f"{file_date_key}"
            f"專員班表-{area}"
        )

        source_file_name = ""

        status = "失敗"

        message = ""

        target_location = (
            f"{quote_sheet_name(target_sheet)}"
            f"!A:G"
        )

        try:
            # ------------------------------------------------
            # 1. 先確認來源檔案
            # ------------------------------------------------

            log(
                f"開始處理專員班表："
                f"{file_base}"
            )

            file = (
                find_file_by_possible_names(
                    drive,
                    source_folder_id,
                    [
                        file_base,
                        f"{file_base}.xlsx",
                        f"{file_base}.xls",
                        f"{file_base}.csv",
                    ],
                )
            )

            source_file_name = (
                file.get(
                    "name",
                    "",
                )
            )

            # ------------------------------------------------
            # 2. 先讀取來源
            #    確認來源正常後才改工作表名稱
            # ------------------------------------------------

            raw_values = (
                read_file_values(
                    drive,
                    sheets,
                    file,
                )
            )

            values = ensure_rectangular(
                [
                    list(row[:7])
                    for row
                    in raw_values
                ],
                7,
            )

            if not values:
                raise RuntimeError(
                    f"專員班表沒有資料："
                    f"{file_base}"
                )

            # ------------------------------------------------
            # 3. 自動更新工作表名稱
            # ------------------------------------------------

            target_sheet = (
                ensure_daily_sheet_name(
                    sheets,
                    target_spreadsheet_id,
                    target_sheet,
                    sheet_prefix,
                )
            )

            target_location = (
                f"{quote_sheet_name(target_sheet)}"
                f"!A:G"
            )

            # ------------------------------------------------
            # 4. 清除 A:G 並寫入新資料
            # ------------------------------------------------

            clear_and_write_values(
                sheets,
                target_spreadsheet_id,
                target_location,
                values,
            )

            status = "成功"

            message = (
                f"rows={len(values)}"
            )

            log(
                f"完成："
                f"{source_file_name}"
                f" → "
                f"{target_location}"
                f" / "
                f"{message}"
            )

        except Exception as e:

            status = "失敗"

            message = str(e)

            log(
                f"失敗："
                f"{file_base}"
                f" / "
                f"{message}"
            )

            raise

        finally:

            # ------------------------------------------------
            # 目標執行檔打卡
            # ------------------------------------------------

            write_target_log(
                target_spreadsheet_id=(
                    target_spreadsheet_id
                ),
                system_name=(
                    "外場排程系統"
                ),
                function_name=(
                    "外場專員班表"
                ),
                run_type=run_type,
                area=area,
                date=file_date_key,
                target_location=(
                    target_location
                ),
                source_file=(
                    source_file_name
                ),
                status=status,
                message=message,
            )


# ============================================================
# Main
# ============================================================

def main(
    date_key: str | None = None,
    area: str | None = None,
    system_name: str = (
        "外場日排程系統"
    ),
    run_type: str = "手動",
) -> None:

    cfg = load_system_config(
        system_name
    )

    date_key = (
        date_key
        or today_yyyymmdd()
    )

    areas = (
        [area]
        if area
        else area_list_from_config(
            cfg
        )
    )

    for current_area in areas:

        run_staff_schedule_for_area(
            cfg,
            current_area,
            date_key,
            run_type,
        )

    log(
        "staff_schedule.py 全部完成"
    )


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    parser = (
        argparse.ArgumentParser()
    )

    parser.add_argument(
        "--date",
        default=(
            today_yyyymmdd()
        ),
    )

    parser.add_argument(
        "--area",
        default="",
    )

    parser.add_argument(
        "--system-name",
        default=(
            "外場日排程系統"
        ),
    )

    parser.add_argument(
        "--run-type",
        default="手動",
    )

    args = parser.parse_args()

    main(
        args.date,
        args.area or None,
        args.system_name,
        args.run_type,
    )
