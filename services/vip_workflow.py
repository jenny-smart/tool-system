from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import gspread

from config.vip_config import (
    AREAS, TYPES, SUMMARY_FILE_NAME_TEMPLATE, PERIOD_FOLDER_TEMPLATE,
    LAST_COL_BY_TYPE, MASTER_FORMULA_SHEET, MASTER_MONTHLY_LOG_SHEET,
    MASTER_AMOUNT_SHEET, MASTER_EXECUTION_LOG_SHEET,
    KAOHSIUNG_DERIVED_AREA, KAOHSIUNG_DERIVED_FILE_TEMPLATE,
    KAOHSIUNG_FILTER_COLUMN, KAOHSIUNG_FILTER_VALUES,
    KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA, KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE,
)
from services.google_drive import DriveService, GOOGLE_SHEETS_MIME
from services.google_sheets import SheetsService, MasterLog, now_tw, col_to_num


@dataclass
class StepResult:
    step: str
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


class VipStoredValueWorkflow:
    def __init__(self, drive: DriveService, sheets: SheetsService, master_id: str, root_folder_id: str):
        self.drive = drive
        self.sheets = sheets
        self.master = sheets.open_by_id(master_id)
        self.root_folder_id = root_folder_id
        self.log = MasterLog(sheets, self.master, MASTER_MONTHLY_LOG_SHEET)

    @staticmethod
    def prev_period(period: str) -> str:
        y, m = int(period[:4]), int(period[4:])
        if m == 1:
            return f"{y-1}12"
        return f"{y}{m-1:02d}"

    def period_folder(self, period: str) -> dict:
        return self.drive.get_or_create_folder(self.root_folder_id, PERIOD_FOLDER_TEMPLATE.format(period=period))

    def create_monthly_summary(self, period: str) -> StepResult:
        result = StepResult("建立彙整檔")
        folder = self.period_folder(period)
        prev = self.prev_period(period)
        prev_folder = self.period_folder(prev)
        prev_name = SUMMARY_FILE_NAME_TEMPLATE.format(period=prev)
        new_name = SUMMARY_FILE_NAME_TEMPLATE.format(period=period)

        existing = self.drive.find_files_by_name(folder["id"], new_name)
        if existing:
            file = existing[0]
            result.add(f"已存在：{new_name}")
        else:
            prev_files = self.drive.find_files_by_name(prev_folder["id"], prev_name)
            if not prev_files:
                result.error(f"找不到前月彙整檔：{prev_name}")
                return result
            file = self.drive.copy_file(prev_files[0]["id"], new_name, folder["id"])
            result.add(f"已複製：{prev_name} → {new_name}")

        self.log.stamp(period, "彙整檔建立時間", now_tw())
        self.log.stamp(period, "來源彙整檔", prev_name)
        self.log.stamp(period, "當月彙整檔", file.get("webViewLink", file["name"]))
        self.log.stamp(period, "當月資料夾", folder.get("webViewLink", folder["name"]))
        return result

    def find_monthly_summary(self, period: str) -> dict:
        folder = self.period_folder(period)
        name = SUMMARY_FILE_NAME_TEMPLATE.format(period=period)
        files = self.drive.find_files_by_name(folder["id"], name)
        if not files:
            raise FileNotFoundError(f"找不到當月彙整檔：{name}，請先建立彙整檔")
        return files[0]

    def detect_area_type(self, filename: str) -> tuple[Optional[str], Optional[str]]:
        area = next((a for a in AREAS if a in filename), None)
        typ = next((t for t in TYPES if t in filename), None)
        return area, typ

    def count_valid_rows_by_b(self, spreadsheet_id: str) -> int:
        sh = self.sheets.open_by_id(spreadsheet_id)
        ws = sh.get_worksheet(0)
        values = ws.col_values(2)[1:]
        count = 0
        for v in values:
            if v == "":
                break
            count += 1
        return count

    def convert_files(self, period: str) -> StepResult:
        result = StepResult("轉檔")
        folder = self.period_folder(period)
        for file in self.drive.list_files(folder["id"]):
            area, typ = self.detect_area_type(file["name"])
            if not area or not typ:
                continue
            try:
                gs_file = self.drive.ensure_google_sheet(file, folder["id"])
                count = self.count_valid_rows_by_b(gs_file["id"])
                self.log.stamp_count_time(period, area, typ, "轉檔", count)
                result.add(f"{area}{typ} 轉檔完成：{count} 筆")
            except Exception as exc:
                msg = f"{file['name']} 轉檔失敗：{exc}"
                self.log.stamp_count_time(period, area, typ, "轉檔", 0)
                result.error(msg)
        # 高雄儲值金結算由新竹檔 H 欄拆出
        kaohsiung_result = self.create_kaohsiung_from_hsinchu(period)
        result.messages.extend(kaohsiung_result.messages)
        result.errors.extend(kaohsiung_result.errors)
        return result

    def create_kaohsiung_from_hsinchu(self, period: str) -> StepResult:
        """
        高雄資料整理規則：
        1. 期別儲值金結算-新竹：移除 H 欄為指定儲值金方案的資料列。
        2. 期別儲值金結算-高雄：只保留 H 欄為指定儲值金方案的資料列，其餘資料列移除。

        注意：這裡不再由新竹另開高雄檔；高雄檔必須已在當月資料夾中。
        """
        result = StepResult("高雄/新竹結算資料整理")
        folder = self.period_folder(period)
        h_idx = col_to_num(KAOHSIUNG_FILTER_COLUMN) - 1

        source = self.drive.find_file_contains(
            folder["id"],
            [period, KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE, KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA],
        )
        target = self.drive.find_file_contains(
            folder["id"],
            [period, KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE, KAOHSIUNG_DERIVED_AREA],
        )

        if not source:
            result.error(f"找不到新竹來源檔：{period}{KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE}-{KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA}")
            self.log.stamp_count_time(period, KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA, KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE, "轉檔", 0)
        if not target:
            result.error(f"找不到高雄來源檔：{period}{KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE}-{KAOHSIUNG_DERIVED_AREA}")
            self.log.stamp_count_time(period, KAOHSIUNG_DERIVED_AREA, KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE, "轉檔", 0)
        if not source or not target:
            return result

        try:
            source_gs = self.drive.ensure_google_sheet(source, folder["id"])
            source_sh = self.sheets.open_by_id(source_gs["id"])
            source_ws = source_sh.get_worksheet(0)
            source_rows = source_ws.get_all_values()[1:]
            hsinchu_rows = [
                r for r in source_rows
                if not (len(r) > h_idx and str(r[h_idx]).strip() in KAOHSIUNG_FILTER_VALUES)
            ]
            end_col = LAST_COL_BY_TYPE[KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE]
            self.sheets.clear_data_area(source_ws, end_col, start_row=2)
            if hsinchu_rows:
                self.sheets.update_values(source_ws, "A2", hsinchu_rows)
            self.log.stamp_count_time(
                period,
                KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA,
                KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE,
                "轉檔",
                len(hsinchu_rows),
            )
            result.add(f"新竹儲值金結算整理完成：移除指定 H 欄方案後保留 {len(hsinchu_rows)} 筆")

            target_gs = self.drive.ensure_google_sheet(target, folder["id"])
            target_sh = self.sheets.open_by_id(target_gs["id"])
            target_ws = target_sh.get_worksheet(0)
            target_rows = target_ws.get_all_values()[1:]
            kaohsiung_rows = [
                r for r in target_rows
                if len(r) > h_idx and str(r[h_idx]).strip() in KAOHSIUNG_FILTER_VALUES
            ]
            self.sheets.clear_data_area(target_ws, end_col, start_row=2)
            if kaohsiung_rows:
                self.sheets.update_values(target_ws, "A2", kaohsiung_rows)
            self.log.stamp_count_time(
                period,
                KAOHSIUNG_DERIVED_AREA,
                KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE,
                "轉檔",
                len(kaohsiung_rows),
            )
            result.add(f"高雄儲值金結算整理完成：只保留指定 H 欄方案 {len(kaohsiung_rows)} 筆")
        except Exception as exc:
            self.log.stamp_count_time(period, KAOHSIUNG_FROM_HSINCHU_SOURCE_AREA, KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE, "轉檔", 0)
            self.log.stamp_count_time(period, KAOHSIUNG_DERIVED_AREA, KAOHSIUNG_FROM_HSINCHU_SOURCE_TYPE, "轉檔", 0)
            result.error(f"高雄/新竹結算資料整理失敗：{exc}")
        return result

    def move_data(self, period: str) -> StepResult:
        result = StepResult("搬運")
        folder = self.period_folder(period)
        summary_file = self.find_monthly_summary(period)
        summary = self.sheets.open_by_id(summary_file["id"])
        files = self.drive.list_files(folder["id"])
        for area in AREAS:
            for typ in TYPES:
                source = self.drive.find_file_contains(folder["id"], [period, typ, area])
                if not source:
                    result.error(f"找不到來源檔：{period}{typ}-{area}")
                    self.log.stamp_count_time(period, area, typ, "搬運", 0)
                    continue
                try:
                    gs_file = self.drive.ensure_google_sheet(source, folder["id"])
                    source_sh = self.sheets.open_by_id(gs_file["id"])
                    source_ws = source_sh.get_worksheet(0)
                    end_col = LAST_COL_BY_TYPE[typ]
                    rows = source_ws.get(f"A2:{end_col}")
                    rows = [r for r in rows if any(str(c).strip() for c in r)]
                    target_name = area + typ
                    target_ws = self.sheets.worksheet(summary, target_name)
                    self.sheets.clear_data_area(target_ws, end_col, start_row=2)
                    if rows:
                        self.sheets.update_values(target_ws, "A2", rows)
                    self.log.stamp_count_time(period, area, typ, "搬運", len(rows))
                    result.add(f"{target_name} 搬運完成：{len(rows)} 筆")
                except Exception as exc:
                    self.log.stamp_count_time(period, area, typ, "搬運", 0)
                    result.error(f"{area}{typ} 搬運失敗：{exc}")
        return result

    def read_formula_settings(self) -> list[dict[str, Any]]:
        ws = self.sheets.worksheet(self.master, MASTER_FORMULA_SHEET)
        rows = ws.get_all_records()
        out = []
        for i, row in enumerate(rows, start=2):
            enabled = str(row.get("啟用", "TRUE")).strip().upper() not in {"FALSE", "0", "否", "N"}
            if not enabled:
                continue
            if not row.get("區域") or not row.get("類型") or not row.get("目標欄位") or not row.get("公式"):
                continue
            row["__row"] = i
            out.append(row)
        return out

    def apply_formulas(self, period: str) -> StepResult:
        result = StepResult("計算")
        summary_file = self.find_monthly_summary(period)
        summary = self.sheets.open_by_id(summary_file["id"])
        formula_ws = self.sheets.worksheet(self.master, MASTER_FORMULA_SHEET)
        formula_rows = self.read_formula_settings()
        counters: dict[tuple[str, str], int] = {(a, t): 0 for a in AREAS for t in TYPES}
        for row in formula_rows:
            area = str(row.get("區域")).strip()
            typ = str(row.get("類型")).strip()
            sheet_name = str(row.get("目標頁籤") or (area + typ)).strip()
            col = str(row.get("目標欄位")).strip()
            formula = str(row.get("公式")).strip()
            start_row = int(row.get("套用起始列") or 2)
            try:
                ws = summary.worksheet(sheet_name)
                row_count = max(len(ws.col_values(1)) - start_row + 1, 0)
                if row_count <= 0:
                    row_count = max(ws.row_count - start_row + 1, 0)
                self.sheets.write_formula_and_copy_down(summary_file["id"], ws.id, col, formula, start_row, row_count)
                # 公式也在主控表打卡
                formula_ws.update_cell(row["__row"], self._ensure_formula_col(formula_ws, "最後套用期別"), period)
                formula_ws.update_cell(row["__row"], self._ensure_formula_col(formula_ws, "套用完成時間"), now_tw())
                formula_ws.update_cell(row["__row"], self._ensure_formula_col(formula_ws, "套用筆數"), row_count)
                counters[(area, typ)] = counters.get((area, typ), 0) + 1
            except Exception as exc:
                result.error(f"公式套用失敗：{sheet_name} {col} 欄，{exc}")
        for (area, typ), count in counters.items():
            self.log.stamp(period, f"{area}{typ}計算公式數", count)
            self.log.stamp(period, f"{area}{typ}計算時間", now_tw())
        result.add("公式計算完成")
        return result

    def _ensure_formula_col(self, ws: gspread.Worksheet, header: str) -> int:
        headers = ws.row_values(1)
        if header in headers:
            return headers.index(header) + 1
        col = len(headers) + 1
        ws.update_cell(1, col, header)
        return col

    def summarize_amounts(self, period: str) -> StepResult:
        result = StepResult("彙整金額")
        summary_file = self.find_monthly_summary(period)
        summary = self.sheets.open_by_id(summary_file["id"])
        cfg_ws = self.sheets.worksheet(self.master, MASTER_AMOUNT_SHEET)
        cfg_rows = cfg_ws.get_all_records()
        if not cfg_rows:
            result.add("金額統整設定為空，已略過。請在主控表設定金額欄位。")
            return result
        for row in cfg_rows:
            enabled = str(row.get("啟用", "TRUE")).strip().upper() not in {"FALSE", "0", "否", "N"}
            if not enabled:
                continue
            area = str(row.get("區域") or "").strip()
            typ = str(row.get("類型") or "").strip()
            sheet_name = str(row.get("目標頁籤") or (area + typ)).strip()
            amount_col = str(row.get("金額欄位") or "").strip()
            if not area or not typ or not amount_col:
                continue
            try:
                ws = summary.worksheet(sheet_name)
                col_num = col_to_num(amount_col)
                values = ws.col_values(col_num)[1:]
                total = 0.0
                for v in values:
                    s = str(v).replace(",", "").replace("$", "").strip()
                    if not s:
                        continue
                    try:
                        total += float(s)
                    except ValueError:
                        continue
                self.log.stamp(period, f"{area}{typ}總金額", total)
                self.log.stamp(period, f"{area}{typ}彙整金額時間", now_tw())
                result.add(f"{area}{typ}總金額：{total:,.0f}")
            except Exception as exc:
                result.error(f"{sheet_name} 金額彙整失敗：{exc}")
        return result

    def run_all(self, period: str) -> list[StepResult]:
        results = []
        # 依指定順序：建立彙整檔 -> 轉檔 -> 搬運 -> 計算 -> 彙整金額
        results.append(self.create_monthly_summary(period))
        results.append(self.convert_files(period))
        results.append(self.move_data(period))
        results.append(self.apply_formulas(period))
        results.append(self.summarize_amounts(period))
        return results
