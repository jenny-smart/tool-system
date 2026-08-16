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
from typing import Any, Callable, Dict, List, Optional, Tuple

import time

from config.vip_config import (
    AREA_GROUPS,
    AREAS,
    HSINCHU_PRE_FILTER_BACKUP_SUFFIX,
    KAOHSIUNG_ADDRESS_KEYWORDS,
    KAOHSIUNG_FILTER_VALUES,
    LAST_COL_BY_TYPE,
    MASTER_EXECUTION_LOG_SHEET,
    MASTER_FORMULA_SHEET,
    MASTER_MONTHLY_LOG_SHEET,
    SUMMARY_FILE_NAME_TEMPLATE,
    TYPES,
)
from services.google_drive import (
    GOOGLE_SHEET_MIME,
    DriveService,
    is_source_spreadsheet_file,
    strip_spreadsheet_extension,
)
from services.google_sheets import (
    ExecutionLog,
    FormulaSettings,
    MasterLog,
    SheetsService,
    col_to_num,
    now_tw,
)


TW_TZ = ZoneInfo("Asia/Taipei")

KAOHSIUNG_PLAN_NAMES = KAOHSIUNG_FILTER_VALUES


@dataclass
class StepResult:
    step: str
    messages: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # 選填：每次 add_message/add_error 都會立刻呼叫一次，讓呼叫端（例如
    # UI 的執行日誌）可以在流程還在跑的當下就即時顯示每一小步的結果，
    # 不用等整個 StepResult 跑完才看得到。
    on_progress: Optional[Callable[[str, str], None]] = field(default=None, repr=False, compare=False)

    def add_message(self, msg: str) -> None:
        self.messages.append(msg)
        if self.on_progress:
            self.on_progress(msg, "info")

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        if self.on_progress:
            self.on_progress(msg, "error")


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

        execution_ws = sheets.get_or_create_ws(self.master, MASTER_EXECUTION_LOG_SHEET)
        self.execution_log = ExecutionLog(execution_ws)

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
        """根目錄（02.VIP儲值金）→ 年度資料夾（例如 2026）→ 期別資料夾（例如 202606），共三層。"""
        year_folder = self.drive.get_or_create_folder(self.root_folder_id, period[:4])
        return self.drive.get_or_create_folder(year_folder["id"], period)

    def summary_file_name(self, period: str) -> str:
        return SUMMARY_FILE_NAME_TEMPLATE.format(period=period)

    def find_monthly_summary_file(self, period: str) -> Optional[Dict[str, Any]]:
        folder = self.get_period_folder(period)
        name = self.summary_file_name(period)
        files = self.drive.find_google_sheet_by_name(folder["id"], name)
        return files[0] if files else None

    def get_monthly_summary(self, period: str):
        file = self.find_monthly_summary_file(period)
        if not file:
            raise FileNotFoundError(
                f"找不到 {self.summary_file_name(period)}，請先執行「複製期別檔案」建立當期彙整檔"
            )
        return self.sheets.open_by_id(file["id"])

    # ============================================================
    # 1. 建立當月彙整檔
    # ============================================================
    def create_monthly_summary(self, period: str) -> StepResult:
        result = StepResult("建立當月彙整檔")
        self.execution_log.append(period, "複製期別檔案", "開始", "")

        folder = self.get_period_folder(period)
        new_name = self.summary_file_name(period)

        # 覆蓋式建立：同名檔案（含重複的舊檔）先全部移到垃圾桶，再建立新的一份，
        # 避免同一期別留下多份同名彙整檔。
        trashed = self.drive.trash_google_sheet_by_name(folder["id"], new_name)
        if trashed:
            result.add_message(f"{new_name} 已存在（{trashed} 份），先移到垃圾桶再重新建立")

        prev = self.prev_period(period)
        prev_folder = self.get_period_folder(prev)
        prev_name = self.summary_file_name(prev)
        prev_files = self.drive.find_google_sheet_by_name(prev_folder["id"], prev_name)

        if not prev_files:
            msg = f"找不到前月彙整檔：{prev_name}"
            self.log.stamp(period, "彙整檔建立錯誤", msg)
            result.add_error(msg)
            self.execution_log.append(period, "複製期別檔案", "失敗", msg)
            return result

        file = self.drive.copy_file(prev_files[0]["id"], new_name, folder["id"])
        self.log.stamp(period, "彙整檔建立時間", now_tw())
        self.log.stamp(period, "來源彙整檔", prev_files[0].get("webViewLink", prev_files[0]["id"]))
        self.log.stamp(period, "當月彙整檔", file.get("webViewLink", file["id"]))

        result.add_message(f"已建立 {new_name}")
        self.execution_log.append(period, "複製期別檔案", "完成", f"已建立 {new_name}")
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
    def _finish_log(self, period: str, step: str, result: StepResult) -> StepResult:
        if result.errors:
            self.execution_log.append(period, step, "失敗", "；".join(result.errors))
        else:
            self.execution_log.append(period, step, "完成", "；".join(result.messages) or "無訊息")
        return result

    def resolve_target_areas(self, area: str) -> Optional[set]:
        """把下拉選單選到的地區（單一地區、地區組合如「新竹＋高雄」、或
        「全區」）轉成實際要處理的地區集合。回傳 None 代表不過濾（全區）。
        """
        if not area or area == "全區":
            return None
        return set(AREA_GROUPS.get(area, [area]))

    def convert_files(
        self,
        period: str,
        area: str = "全區",
        on_progress: Optional[Callable[[str, str], None]] = None,
    ) -> StepResult:
        result = StepResult("轉檔", on_progress=on_progress)
        self.execution_log.append(period, "轉檔", "開始", "")
        folder = self.get_period_folder(period)
        files = self.drive.list_children(folder["id"])
        target_areas = self.resolve_target_areas(area)

        # 只處理 xlsx/xls/csv，不處理已轉好的 Google Sheet。
        source_files = [f for f in files if is_source_spreadsheet_file(f)]

        # 依 base_name 去重：同名只處理一個來源檔
        # 若資料夾內同時有 xlsx 與 xls，優先使用最後掃到的檔案。
        source_by_base: Dict[str, Dict[str, Any]] = {}
        for file in source_files:
            base_name = strip_spreadsheet_extension(file["name"])
            file_area, typ = self.parse_area_type(base_name)
            if not file_area or not typ:
                continue
            if target_areas is not None and file_area not in target_areas:
                continue
            source_by_base[base_name] = file

        if not source_by_base:
            result.add_message("沒有找到需要轉檔的 xlsx/xls/csv")
            return self._finish_log(period, "轉檔", result)

        for base_name, file in source_by_base.items():
            file_area, typ = self.parse_area_type(base_name)
            if not file_area or not typ:
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

                count = None
                count_error = None
                for wait_seconds in (0, 8, 15):
                    if wait_seconds:
                        time.sleep(wait_seconds)  # 剛轉好偶爾需要再等一下
                    try:
                        count = self.count_valid_rows(converted["id"])
                        break
                    except Exception as count_exc:
                        count_error = count_exc

                if count is None:
                    # 讀不到筆數不代表真的是 0 筆，兩者要分開標記，
                    # 不然桃園這種明明有資料的頁籤，log 會誤顯示 0。
                    self.log.stamp_count_time(period, file_area, typ, "轉檔", f"讀取失敗：{count_error}")
                    result.add_error(f"{base_name} 轉檔完成但讀取筆數失敗：{count_error}")
                    time.sleep(1.5)
                    continue

                self.log.stamp_count_time(period, file_area, typ, "轉檔", count)
                replaced = converted.get("replaced_count", 0)
                if replaced:
                    result.add_message(f"{base_name} 轉檔完成：{count} 筆，已覆蓋舊檔 {replaced} 個")
                else:
                    result.add_message(f"{base_name} 轉檔完成：{count} 筆")

                time.sleep(1.5)

            except Exception as e:
                self.log.stamp_count_time(period, file_area, typ, "轉檔", f"轉檔失敗：{e}")
                result.add_error(f"{file['name']} 轉檔失敗：{e}")

        # 高雄/新竹結算彼此是連動的（高雄結算完全從新竹篩出來），只要其中
        # 一個在這次選的地區範圍內，就要跑這一步；只選跟新竹/高雄都無關
        # 的地區（例如只選台北）就不用跑。
        if target_areas is None or "新竹" in target_areas or "高雄" in target_areas:
            try:
                reconcile_msg = self.adjust_hsinchu_kaohsiung_settlement(period)
                result.add_message(reconcile_msg)
            except Exception as e:
                result.add_error(f"高雄/新竹結算資料整理失敗：{e}")

        # 高雄儲值金預收 = 高雄自己的來源檔 + 台南來源檔合併，只選跟高雄
        # 無關的地區就不用跑。
        if target_areas is None or "高雄" in target_areas:
            try:
                tainan_merged = self.merge_tainan_into_kaohsiung_prepaid(period)
                if tainan_merged is None:
                    result.add_message("沒有找到台南來源檔，高雄儲值金預收不需要合併")
                else:
                    result.add_message(f"已把台南 {tainan_merged} 筆資料合併進高雄儲值金預收")
            except Exception as e:
                result.add_error(f"合併台南到高雄儲值金預收失敗：{e}")

        return self._finish_log(period, "轉檔", result)

    # ============================================================
    # 高雄 / 新竹結算整理
    # ============================================================
    def adjust_hsinchu_kaohsiung_settlement(self, period: str) -> str:
        """高雄儲值金結算沒有自己的來源檔，完全是從新竹儲值金結算篩出來的：
        符合高雄條件（方案名稱或地址）的列歸高雄，其餘留在新竹。

        做法：先把新竹當下的完整內容複製一份當作高雄結算（覆蓋舊檔），
        複製完成後再分別過濾——新竹移除符合高雄條件的列、高雄的複製本
        只保留符合高雄條件的列。因為兩邊是從「同一份複製時間點的新竹
        資料」各自篩出互斥的子集，新竹被篩掉的列一定會完整出現在高雄，
        不會有資料整筆不見的問題（先前的寫法是分別過濾新竹跟一個必須
        預先存在的高雄檔案，兩者互不相通，新竹篩掉的列從沒被寫進高雄）。

        過濾完成後會核對「新竹留下的筆數 + 高雄留下的筆數」是否等於
        「複製當下新竹的原始筆數」，對不起來就直接丟例外，不會默默放過
        資料不見或重複的狀況。

        這步是「轉檔」main loop 之後才跑的最終覆蓋，所以完成後要重新把新竹／
        高雄的筆數／時間打卡蓋掉之前 main loop 留下的數字，不然主控表看起來
        會像「高雄沒有轉檔」。
        """
        folder = self.get_period_folder(period)

        hsinchu_name = f"{period}儲值金結算-新竹"
        kaohsiung_name = f"{period}儲值金結算-高雄"

        hsinchu_files = self.drive.find_google_sheet_by_name(folder["id"], hsinchu_name)
        if not hsinchu_files:
            raise FileNotFoundError(f"找不到 {hsinchu_name}")

        # 篩選前先留一份新竹當下完整內容的備份，不會被後面的篩選動到，
        # 方便事後回頭核對篩選結果對不對。
        backup_name = f"{hsinchu_name}{HSINCHU_PRE_FILTER_BACKUP_SUFFIX}"
        self.drive.trash_google_sheet_by_name(folder["id"], backup_name)
        self.drive.copy_file(
            file_id=hsinchu_files[0]["id"],
            new_name=backup_name,
            parent_folder_id=folder["id"],
        )

        self.drive.trash_google_sheet_by_name(folder["id"], kaohsiung_name)
        kaohsiung_file = self.drive.copy_file(
            file_id=hsinchu_files[0]["id"],
            new_name=kaohsiung_name,
            parent_folder_id=folder["id"],
        )
        time.sleep(3.0)

        hsinchu_count, original_count = self._filter_with_retry(
            spreadsheet_id=hsinchu_files[0]["id"],
            keep_matching=False,
        )
        self.log.stamp_count_time(period, "新竹", "儲值金結算", "轉檔", hsinchu_count)

        time.sleep(1.5)

        # 高雄這份是剛複製出來的新檔案，Drive/Sheets 剛複製完偶爾還沒完全
        # 就緒（尤其原始資料有幾百列時），這裡跟其他轉檔步驟一樣用重試
        # 機制，避免「複製完但讀不到/寫不進去」導致高雄整份還停在複製
        # 當下、完全沒篩選過的狀態（篩選失敗的例外會直接往外丟，高雄的
        # 內容就會停在複製時的原始快照，看起來就像完全沒篩選）。
        kaohsiung_count, _ = self._filter_with_retry(
            spreadsheet_id=kaohsiung_file["id"],
            keep_matching=True,
        )
        self.log.stamp_count_time(period, "高雄", "儲值金結算", "轉檔", kaohsiung_count)

        if hsinchu_count + kaohsiung_count != original_count:
            raise ValueError(
                f"新竹/高雄結算筆數對不起來：新竹 {hsinchu_count} 筆 + 高雄 "
                f"{kaohsiung_count} 筆 = {hsinchu_count + kaohsiung_count} 筆，"
                f"但原始新竹筆數是 {original_count} 筆"
            )

        return (
            f"新竹/高雄結算筆數核對正確：新竹 {hsinchu_count} 筆 + 高雄 "
            f"{kaohsiung_count} 筆 = 原始新竹 {original_count} 筆"
        )

    # ============================================================
    # 高雄儲值金預收 = 高雄自己的來源檔 + 台南來源檔合併
    # ============================================================
    def merge_tainan_into_kaohsiung_prepaid(self, period: str) -> Optional[int]:
        """把台南儲值金預收的資料合併進高雄儲值金預收（列疊加）。

        台南不是正式地區（不會出現在搬運/套公式的地區清單），只在這裡當作
        高雄儲值金預收的合併來源。資料夾裡沒有台南來源檔時就跳過，不算錯誤。
        """
        folder = self.get_period_folder(period)
        tainan_name = f"{period}儲值金預收-台南"
        kaohsiung_name = f"{period}儲值金預收-高雄"

        # 台南來源檔可能還是 xlsx/xls/csv（未轉檔），也可能已經轉成
        # Google Sheet，檔名不會帶副檔名，所以不能用精確比對，要用
        # list_children 掃資料夾比對「去掉副檔名後的檔名」。
        tainan_file = None
        for file in self.drive.list_children(folder["id"]):
            if strip_spreadsheet_extension(file["name"]) == tainan_name:
                tainan_file = file
                break
        if not tainan_file:
            return None

        if tainan_file.get("mimeType") != GOOGLE_SHEET_MIME:
            tainan_file = self.drive.replace_google_sheet_from_source(
                source_file_id=tainan_file["id"],
                source_name=tainan_file["name"],
                parent_folder_id=folder["id"],
            )
            time.sleep(2.5)

        tainan_ws = self.sheets.open_by_id(tainan_file["id"]).worksheets()[0]
        tainan_values = tainan_ws.get_all_values()
        tainan_rows = tainan_values[1:] if len(tainan_values) > 1 else []
        if not tainan_rows:
            return 0

        kaohsiung_files = self.drive.find_google_sheet_by_name(folder["id"], kaohsiung_name)
        if not kaohsiung_files:
            raise FileNotFoundError(f"找不到 {kaohsiung_name}（無法合併台南資料，高雄儲值金預收自己的來源檔可能還沒轉檔）")

        kaohsiung_ws = self.sheets.open_by_id(kaohsiung_files[0]["id"]).worksheets()[0]
        kaohsiung_existing_rows = max(len(kaohsiung_ws.col_values(1)) - 1, 0)

        self.sheets.write_values(kaohsiung_ws, kaohsiung_existing_rows + 2, 1, tainan_rows)

        total = kaohsiung_existing_rows + len(tainan_rows)
        self.log.stamp_count_time(period, "高雄", "儲值金預收", "轉檔", total)
        return len(tainan_rows)

    def _filter_with_retry(self, spreadsheet_id: str, keep_matching: bool) -> Tuple[int, int]:
        """剛複製出來的 Google Sheet，Drive/Sheets 端偶爾需要幾秒才會完全
        就緒，直接讀寫容易失敗。跟轉檔讀筆數一樣用重試機制包一層，避免
        單次失敗就整個丟例外、讓表格停在複製當下、完全沒篩選過的狀態。
        """
        last_error: Optional[Exception] = None
        for wait_seconds in (0, 5, 10, 20):
            if wait_seconds:
                time.sleep(wait_seconds)
            try:
                return self.filter_rows_by_h_column(spreadsheet_id, keep_matching)
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"篩選失敗（已重試多次）：{last_error}") from last_error

    def filter_rows_by_h_column(self, spreadsheet_id: str, keep_matching: bool) -> Tuple[int, int]:
        """
        H 欄（第 8 欄，方案名稱）符合高雄方案名單，或是 E 欄（第 5 欄，地址）
        包含「高雄」「台南」關鍵字，都算高雄的列（像 儲值金50000 這種各地
        共用的方案名稱，就是靠地址才分得出來是不是高雄）。
        - keep_matching=False：移除符合高雄的列（新竹）
        - keep_matching=True：只保留符合高雄的列（高雄）

        回傳 (過濾後留下的筆數, 過濾前的原始筆數)，讓呼叫端可以核對
        「新竹留下的 + 高雄留下的」是否等於「原始筆數」，確保沒有資料
        在過濾過程中憑空消失或重複。
        """
        ss = self.sheets.open_by_id(spreadsheet_id)
        ws = ss.worksheets()[0]
        values = ws.get_all_values()

        if len(values) <= 1:
            return 0, 0

        header = values[0]
        rows = values[1:]
        original_count = len(rows)

        filtered = []
        for row in rows:
            h_value = row[7].strip() if len(row) >= 8 else ""
            e_value = row[4].strip() if len(row) >= 5 else ""
            matched = h_value in KAOHSIUNG_PLAN_NAMES or any(
                keyword in e_value for keyword in KAOHSIUNG_ADDRESS_KEYWORDS
            )

            if keep_matching and matched:
                filtered.append(row)
            elif not keep_matching and not matched:
                filtered.append(row)

        # 清空後重寫，A1 放原標題，A2 開始資料
        ws.clear()
        ws.update("A1", [header], value_input_option="USER_ENTERED")

        if filtered:
            self.sheets.write_values(ws, 2, 1, filtered)

        return len(filtered), original_count

    # ============================================================
    # 3. 搬運
    # ============================================================
    def move_files(
        self,
        period: str,
        area: str = "全區",
        on_progress: Optional[Callable[[str, str], None]] = None,
    ) -> StepResult:
        result = StepResult("搬運", on_progress=on_progress)
        self.execution_log.append(period, "搬運", "開始", "")
        folder = self.get_period_folder(period)
        summary = self.get_monthly_summary(period)
        target_areas = self.resolve_target_areas(area)

        files = self.drive.list_children(folder["id"])
        google_files = [f for f in files if f.get("mimeType") == GOOGLE_SHEET_MIME]

        for file in google_files:
            name = file["name"]

            if name == self.summary_file_name(period):
                continue

            # 新竹篩選前備份只是留著回頭核對用，檔名仍帶「新竹」「儲值金
            # 結算」字樣會被 parse_area_type 誤判成正常來源，這裡要明確
            # 排除，不然會把備份也搬進新竹儲值金結算頁籤，跟已經搬過的
            # 正式資料重複。
            if name.endswith(HSINCHU_PRE_FILTER_BACKUP_SUFFIX):
                continue

            file_area, typ = self.parse_area_type(name)
            if not file_area or not typ:
                continue
            if target_areas is not None and file_area not in target_areas:
                continue

            try:
                source_ss = self.sheets.open_by_id(file["id"])
                source_ws = source_ss.worksheets()[0]
                values = source_ws.get_all_values()

                if len(values) <= 1:
                    self.log.stamp_count_time(period, file_area, typ, "搬運", 0)
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

                target_sheet_name = f"{file_area}{typ}"
                target_ws = self.sheets.get_or_create_ws(summary, target_sheet_name)

                self.sheets.clear_from_row(target_ws, start_row=2, start_col=1, end_col=end_col)

                if data:
                    self.sheets.write_values(target_ws, 2, 1, data)

                self.log.stamp_count_time(period, file_area, typ, "搬運", len(data))
                result.add_message(f"{target_sheet_name} 搬運完成：{len(data)} 筆")

                time.sleep(1.2)

            except Exception as e:
                self.log.stamp_count_time(period, file_area, typ, "搬運", 0)
                result.add_error(f"{name} 搬運失敗：{e}")

        return self._finish_log(period, "搬運", result)

    # ============================================================
    # 4. 計算 / 套公式
    # ============================================================
    def apply_formulas(
        self,
        period: str,
        area: str = "全區",
        on_progress: Optional[Callable[[str, str], None]] = None,
    ) -> StepResult:
        result = StepResult("計算", on_progress=on_progress)
        self.execution_log.append(period, "計算", "開始", "")
        summary = self.get_monthly_summary(period)
        target_areas = self.resolve_target_areas(area)

        rows = self.formulas.read_enabled()
        if target_areas is not None:
            rows = [r for r in rows if r.get("區域") in target_areas]
        if not rows:
            result.add_message("公式設定無啟用項目（請確認「儲值金公式設定」的啟用欄已填 TRUE，且選的地區有對應的公式設定）")
            return self._finish_log(period, "計算", result)

        # 同一個頁籤常常會有好幾列公式設定（不同欄位），worksheet 物件跟
        # 最後一列列數只要抓一次、快取起來重複用，避免每一列公式設定都
        # 重新打一次 API（40 列設定＝原本要 80 次讀取，很容易撞到 Sheets
        # API 的「每分鐘讀取次數」限制）。
        ws_cache: dict[str, Any] = {}
        last_row_cache: dict[str, int] = {}

        for item in rows:
            try:
                target_sheet_name = item.get("目標頁籤") or f"{item.get('區域','')}{item.get('類型','')}"
                target_col = item.get("目標欄位")
                formula = item.get("公式")
                start_row = int(item.get("套用起始列") or 2)

                if not target_sheet_name or not target_col or not formula:
                    continue

                if target_sheet_name not in ws_cache:
                    ws_cache[target_sheet_name] = self.sheets.get_or_create_ws(summary, target_sheet_name)
                    self.sheets.clear_basic_filter(ws_cache[target_sheet_name])
                    last_row_cache[target_sheet_name] = len(ws_cache[target_sheet_name].col_values(1))

                ws = ws_cache[target_sheet_name]
                last_row = last_row_cache[target_sheet_name]
                if last_row < start_row:
                    count = 0
                    self.formulas.stamp_formula_result(item["_row"], count)
                    continue

                col_num = col_to_num(target_col)
                formula_text = str(formula).strip()
                if not formula_text.startswith("="):
                    formula_text = "=" + formula_text

                count = last_row - start_row + 1
                self.sheets.copy_formula_down(ws, start_row, col_num, last_row, formula_text)

                self.formulas.stamp_formula_result(item["_row"], count)
                result.add_message(f"{target_sheet_name} {target_col} 欄公式完成：{count} 筆")

                time.sleep(1.0)

            except Exception as e:
                result.add_error(f"公式套用失敗：{item} / {e}")

        self.log.stamp(period, "計算完成時間", now_tw())
        return self._finish_log(period, "計算", result)

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
        return self._finish_log(period, "彙整金額", result)
