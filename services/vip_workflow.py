"""
services/vip_workflow.py

VIP 儲值金管理 workflow

流程：
1. 建立當月彙整檔
2. 轉檔＋高雄/新竹彙整
3. 搬運
4. 計算
5. 彙整金額

本版重點：
- 轉檔時只處理 xlsx/xls/csv 來源檔
- 若同名 Google Sheet 已存在，不忽略，而是先移到垃圾桶再重轉
- 高雄/新竹結算整理：
  新竹儲值金結算：移除 H 欄為 儲值金18900 / 儲值金36000 / 儲值金9900 的列
  高雄儲值金結算：只保留 H 欄為上述三種儲值金的列
- 打卡順序：轉檔 → 搬運 → 計算 → 彙整金額
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

import time

from services.google_drive import (
    GOOGLE_SHEET_MIME,
    DriveService,
    is_source_spreadsheet_file,
    strip_spreadsheet_extension,
)
from services.google_sheets import SheetsService, MasterLog, FormulaSettings, now_tw, col_to_num


TW_TZ = ZoneInfo("Asia/Taipei")

MASTER_MONTHLY_LOG_SHEET = "月度作業紀錄"
MASTER_FORMULA_SHEET = "公式設定"

AREAS = ["台北", "台中", "桃園", "新竹", "高雄"]
TYPES = ["儲值金結算", "儲值金預收"]

KAOHSIUNG_PLAN_NAMES = {"儲值金18900", "儲值金36000", "儲值金9900"}

LAST_COL_BY_TYPE = {
    "儲值金結算": "T",
    "儲值金預收": "BJ",
}


@dataclass
class StepResult:
    step: str
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def add_message(self, msg: str) -> None:
        self.messages.append(msg)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)


class VipStoredValueWorkflow:
    def __init__(
        self,
        drive: DriveService,
        sheets: SheetsService,
        master_spreadsheet_id: str,
        root_folder_id: str,
        system_name: str = "儲值金管理",
    ):
        self.drive = drive
        self.sheets = sheets
        self.master_spreadsheet_id = master_spreadsheet_id
        self.root_folder_id = root_folder_id
        self.system_name = system_name

        self.master = sheets.open_by_id(master_spreadsheet_id)
        self.log = MasterLog(sheets, self.master, MASTER_MONTHLY_LOG_SHEET)

        formula_ws = sheets.get_or_create_ws(self.master, MASTER_FORMULA_SHEET)
        self.formulas = FormulaSettings(formula_ws)

    # ============================================================
    # Period helpers
    # ============================================================
    def prev_period(self, period: str) -> str:
        year = int(period[:4])
        month = int(period[4:6])
        if month == 1:
            return f"{year - 1}12"
        return f"{year}{month - 1:02d}"

    def get_period_folder(self, period: str) -> Dict[str, Any]:
        return self.drive.get_or_create_folder(self.root_folder_id, period)

    def find_monthly_summary_file(self, period: str) -> Optional[Dict[str, Any]]:
        folder = self.get_period_folder(period)
        name = f"{period}VIP預收款彙整"
        files = self.drive.find_google_sheet_by_name(folder["id"], name)
        return files[0] if files else None

    def get_monthly_summary(self, period: str):
        file = self.find_monthly_summary_file(period)
        if not file:
            raise FileNotFoundError(f"找不到 {period}VIP預收款彙整")
        return self.sheets.open_by_id(file["id"])

    # ============================================================
    # 1. 建立當月彙整檔
    # ============================================================
    def create_monthly_summary(self, period: str) -> StepResult:
        result = StepResult("建立當月彙整檔")

        folder = self.get_period_folder(period)
        new_name = f"{period}VIP預收款彙整"

        existing = self.drive.find_google_sheet_by_name(folder["id"], new_name)
        if existing:
            self.log.stamp(period, "當月彙整檔", existing[0].get("webViewLink", existing[0]["id"]))
            result.add_message(f"{new_name} 已存在，不重複建立")
            return result

        prev = self.prev_period(period)
        prev_folder = self.get_period_folder(prev)
        prev_name = f"{prev}VIP預收款彙整"
        prev_files = self.drive.find_google_sheet_by_name(prev_folder["id"], prev_name)

        if not prev_files:
            msg = f"找不到前月彙整檔：{prev_name}"
            self.log.stamp(period, "彙整檔建立錯誤", msg)
            result.add_error(msg)
            return result

        file = self.drive.copy_file(prev_files[0]["id"], new_name, folder["id"])
        self.log.stamp(period, "彙整檔建立時間", now_tw())
        self.log.stamp(period, "來源彙整檔", prev_files[0].get("webViewLink", prev_files[0]["id"]))
        self.log.stamp(period, "當月彙整檔", file.get("webViewLink", file["id"]))

        result.add_message(f"已建立 {new_name}")
        return result

    # ============================================================
    # File classify
    # ============================================================
    def parse_area_type(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        area = None
        typ = None

        for a in AREAS:
            if a in name:
                area = a
                break

        for t in TYPES:
            if t in name:
                typ = t
                break

        return area, typ

    def count_valid_rows(self, spreadsheet_id: str) -> int:
        ss = self.sheets.open_by_id(spreadsheet_id)
        ws = ss.worksheets()[0]
        values = ws.col_values(2)
        return len([v for v in values[1:] if str(v).strip()])

    # ============================================================
    # 2. 轉檔＋高雄/新竹彙整
    # ============================================================
    def convert_files(self, period: str) -> StepResult:
        result = StepResult("轉檔")
        folder = self.get_period_folder(period)
        files = self.drive.list_children(folder["id"])

        # 只處理 xlsx/xls/csv，不處理已轉好的 Google Sheet。
        source_files = [f for f in files if is_source_spreadsheet_file(f)]

        # 依 base_name 去重：同名只處理一個來源檔
        # 若資料夾內同時有 xlsx 與 xls，優先使用最後掃到的檔案。
        source_by_base: Dict[str, Dict[str, Any]] = {}
        for file in source_files:
            base_name = strip_spreadsheet_extension(file["name"])
            area, typ = self.parse_area_type(base_name)
            if not area or not typ:
                continue
            source_by_base[base_name] = file

        if not source_by_base:
            result.add_message("沒有找到需要轉檔的 xlsx/xls/csv")
            return result

        for base_name, file in source_by_base.items():
            area, typ = self.parse_area_type(base_name)
            if not area or not typ:
                continue

            try:
                # 覆蓋式轉檔：先 trash 同名 Google Sheet，再重轉
                converted = self.drive.replace_google_sheet_from_source(
                    source_file_id=file["id"],
                    source_name=file["name"],
                    parent_folder_id=folder["id"],
                )

                # Drive 轉檔後稍等，避免 Sheets 端尚未可讀
                time.sleep(2.5)

                try:
                    count = self.count_valid_rows(converted["id"])
                except Exception:
                    # 剛轉好偶爾需要再等一下
                    time.sleep(8)
                    count = self.count_valid_rows(converted["id"])

                self.log.stamp_count_time(period, area, typ, "轉檔", count)
                replaced = converted.get("replaced_count", 0)
                if replaced:
                    result.add_message(f"{base_name} 轉檔完成：{count} 筆，已覆蓋舊檔 {replaced} 個")
                else:
                    result.add_message(f"{base_name} 轉檔完成：{count} 筆")

                time.sleep(1.5)

            except Exception as e:
                self.log.stamp_count_time(period, area, typ, "轉檔", 0)
                result.add_error(f"{file['name']} 轉檔失敗：{e}")

        # 高雄 / 新竹結算資料整理
        try:
            self.adjust_hsinchu_kaohsiung_settlement(period)
            result.add_message("高雄/新竹結算資料整理完成")
        except Exception as e:
            result.add_error(f"高雄/新竹結算資料整理失敗：{e}")

        return result

    # ============================================================
    # 高雄 / 新竹結算整理
    # ============================================================
    def adjust_hsinchu_kaohsiung_settlement(self, period: str) -> None:
        folder = self.get_period_folder(period)

        hsinchu_name = f"{period}儲值金結算-新竹"
        kaohsiung_name = f"{period}儲值金結算-高雄"

        hsinchu_files = self.drive.find_google_sheet_by_name(folder["id"], hsinchu_name)
        kaohsiung_files = self.drive.find_google_sheet_by_name(folder["id"], kaohsiung_name)

        if not hsinchu_files:
            raise FileNotFoundError(f"找不到 {hsinchu_name}")
        if not kaohsiung_files:
            raise FileNotFoundError(f"找不到 {kaohsiung_name}")

        self.filter_rows_by_h_column(
            spreadsheet_id=hsinchu_files[0]["id"],
            keep_matching=False,
        )

        time.sleep(1.5)

        self.filter_rows_by_h_column(
            spreadsheet_id=kaohsiung_files[0]["id"],
            keep_matching=True,
        )

    def filter_rows_by_h_column(self, spreadsheet_id: str, keep_matching: bool) -> int:
        """
        H 欄是第 8 欄。
        - keep_matching=False：移除 H 欄符合高雄方案的列（新竹）
        - keep_matching=True：只保留 H 欄符合高雄方案的列（高雄）
        """
        ss = self.sheets.open_by_id(spreadsheet_id)
        ws = ss.worksheets()[0]
        values = ws.get_all_values()

        if len(values) <= 1:
            return 0

        header = values[0]
        rows = values[1:]

        filtered = []
        for row in rows:
            h_value = row[7].strip() if len(row) >= 8 else ""
            matched = h_value in KAOHSIUNG_PLAN_NAMES

            if keep_matching and matched:
                filtered.append(row)
            elif not keep_matching and not matched:
                filtered.append(row)

        # 清空後重寫，A1 放原標題，A2 開始資料
        ws.clear()
        ws.update("A1", [header], value_input_option="USER_ENTERED")

        if filtered:
            end_col = len(header)
            start = "A2"
            self.sheets.write_values(ws, 2, 1, filtered)

        return len(filtered)

    # ============================================================
    # 3. 搬運
    # ============================================================
    def move_files(self, period: str) -> StepResult:
        result = StepResult("搬運")
        folder = self.get_period_folder(period)
        summary = self.get_monthly_summary(period)

        files = self.drive.list_children(folder["id"])
        google_files = [f for f in files if f.get("mimeType") == GOOGLE_SHEET_MIME]

        for file in google_files:
            name = file["name"]

            if name == f"{period}VIP預收款彙整":
                continue

            area, typ = self.parse_area_type(name)
            if not area or not typ:
                continue

            try:
                source_ss = self.sheets.open_by_id(file["id"])
                source_ws = source_ss.worksheets()[0]
                values = source_ws.get_all_values()

                if len(values) <= 1:
                    self.log.stamp_count_time(period, area, typ, "搬運", 0)
                    result.add_message(f"{name} 無資料可搬運")
                    continue

                end_col_letter = LAST_COL_BY_TYPE.get(typ, "T")
                end_col = col_to_num(end_col_letter)
                data = []
                for row in values[1:]:
                    trimmed = row[:end_col]
                    if any(str(v).strip() for v in trimmed):
                        trimmed = trimmed + [""] * (end_col - len(trimmed))
                        data.append(trimmed)

                target_sheet_name = f"{area}{typ}"
                target_ws = self.sheets.get_or_create_ws(summary, target_sheet_name)

                self.sheets.clear_from_row(target_ws, start_row=2, start_col=1, end_col=end_col)

                if data:
                    self.sheets.write_values(target_ws, 2, 1, data)

                self.log.stamp_count_time(period, area, typ, "搬運", len(data))
                result.add_message(f"{target_sheet_name} 搬運完成：{len(data)} 筆")

                time.sleep(1.2)

            except Exception as e:
                self.log.stamp_count_time(period, area, typ, "搬運", 0)
                result.add_error(f"{name} 搬運失敗：{e}")

        return result

    # ============================================================
    # 4. 計算 / 套公式
    # ============================================================
    def apply_formulas(self, period: str) -> StepResult:
        result = StepResult("計算")
        summary = self.get_monthly_summary(period)

        rows = self.formulas.read_enabled()
        if not rows:
            result.add_message("公式設定無啟用項目")
            return result

        for item in rows:
            try:
                target_sheet_name = item.get("目標頁籤") or f"{item.get('區域','')}{item.get('類型','')}"
                target_col = item.get("目標欄位")
                formula = item.get("公式")
                start_row = int(item.get("套用起始列") or 2)

                if not target_sheet_name or not target_col or not formula:
                    continue

                ws = self.sheets.get_or_create_ws(summary, target_sheet_name)
                last_row = len(ws.col_values(1))
                if last_row < start_row:
                    count = 0
                    self.formulas.stamp_formula_result(item["_row"], count)
                    continue

                col_num = col_to_num(target_col)
                formula_text = str(formula).strip()
                if not formula_text.startswith("="):
                    formula_text = "=" + formula_text

                count = last_row - start_row + 1
                formulas = [[formula_text] for _ in range(count)]
                self.sheets.write_values(ws, start_row, col_num, formulas)

                self.formulas.stamp_formula_result(item["_row"], count)
                result.add_message(f"{target_sheet_name} {target_col} 欄公式完成：{count} 筆")

                time.sleep(1.0)

            except Exception as e:
                result.add_error(f"公式套用失敗：{item} / {e}")

        self.log.stamp(period, "計算完成時間", now_tw())
        return result

    # ============================================================
    # 5. 彙整金額
    # ============================================================
    def summarize_amounts(self, period: str) -> StepResult:
        """
        金額統整目前採保守版：
        - 若主控表有「金額統整設定」可再擴充
        - 先打卡彙整完成時間
        """
        result = StepResult("彙整金額")
        self.log.stamp(period, "彙整金額完成時間", now_tw())
        result.add_message("彙整金額完成時間已打卡")
        return result
