"""
services/google_drive.py

Google Drive Service
- 找資料夾
- 建立資料夾
- 搜尋檔案
- 複製檔案
- xlsx/xls/csv 轉 Google Sheet
- 同名 Google Sheet 覆蓋：先移到垃圾桶，再重新轉檔

注意：
- 需要 Google Drive API v3
- Service Account 需要對根目錄與檔案有編輯權限
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"

EXCEL_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "application/csv",
}


class DriveService:
    def __init__(self, service):
        self.service = service

    # ============================================================
    # 基礎工具
    # ============================================================
    def _escape_query_value(self, value: str) -> str:
        return str(value).replace("\\", "\\\\").replace("'", "\\'")

    def _execute(self, request):
        return request.execute()

    # ============================================================
    # Folder
    # ============================================================
    def get_file(self, file_id: str) -> Dict[str, Any]:
        return (
            self.service.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,webViewLink,parents",
                supportsAllDrives=True,
            )
            .execute()
        )

    def list_children(self, folder_id: str) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:
            res = (
                self.service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id,name,mimeType,webViewLink,parents)",
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000,
                )
                .execute()
            )
            files.extend(res.get("files", []))
            page_token = res.get("nextPageToken")
            if not page_token:
                break

        return files

    def find_folder(self, parent_folder_id: str, folder_name: str) -> Optional[Dict[str, Any]]:
        folder_name = self._escape_query_value(folder_name)
        q = (
            f"'{parent_folder_id}' in parents and "
            f"name = '{folder_name}' and "
            f"mimeType = 'application/vnd.google-apps.folder' and "
            f"trashed = false"
        )

        res = (
            self.service.files()
            .list(
                q=q,
                fields="files(id,name,mimeType,webViewLink)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageSize=10,
            )
            .execute()
        )

        files = res.get("files", [])
        return files[0] if files else None

    def get_or_create_folder(self, parent_folder_id: str, folder_name: str) -> Dict[str, Any]:
        existing = self.find_folder(parent_folder_id, folder_name)
        if existing:
            return existing

        body = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_folder_id],
        }

        return (
            self.service.files()
            .create(
                body=body,
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )

    # ============================================================
    # File search
    # ============================================================
    def find_files_by_name(
        self,
        folder_id: str,
        file_name: str,
        mime_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        safe_name = self._escape_query_value(file_name)

        q = (
            f"'{folder_id}' in parents and "
            f"name = '{safe_name}' and "
            f"trashed = false"
        )

        if mime_type:
            q += f" and mimeType = '{mime_type}'"

        res = (
            self.service.files()
            .list(
                q=q,
                fields="files(id,name,mimeType,webViewLink,parents)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageSize=100,
            )
            .execute()
        )

        return res.get("files", [])

    def find_google_sheet_by_name(self, folder_id: str, file_name: str) -> List[Dict[str, Any]]:
        return self.find_files_by_name(
            folder_id=folder_id,
            file_name=file_name,
            mime_type=GOOGLE_SHEET_MIME,
        )

    # ============================================================
    # Trash / delete
    # ============================================================
    def trash_file(self, file_id: str) -> Dict[str, Any]:
        return (
            self.service.files()
            .update(
                fileId=file_id,
                body={"trashed": True},
                fields="id,name,trashed",
                supportsAllDrives=True,
            )
            .execute()
        )

    def trash_google_sheet_by_name(self, folder_id: str, file_name: str) -> int:
        """
        將指定資料夾內，同名 Google Sheet 全部移到垃圾桶。
        用於轉檔覆蓋：
        - 若 202604儲值金結算-台北 Google Sheet 已存在
        - 先 trash 舊檔
        - 再由 xlsx 重新轉成同名 Google Sheet
        """
        files = self.find_google_sheet_by_name(folder_id, file_name)

        count = 0
        for file in files:
            self.trash_file(file["id"])
            count += 1
            time.sleep(0.2)

        return count

    # ============================================================
    # Copy / convert
    # ============================================================
    def copy_file(self, file_id: str, new_name: str, parent_folder_id: str) -> Dict[str, Any]:
        body = {
            "name": new_name,
            "parents": [parent_folder_id],
        }

        try:
            return (
                self.service.files()
                .copy(
                    fileId=file_id,
                    body=body,
                    fields="id,name,mimeType,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as e:
            raise RuntimeError(
                f"複製檔案失敗：file_id={file_id}, "
                f"new_name={new_name}, "
                f"parent_folder_id={parent_folder_id}, "
                f"error={e}"
            ) from e

    def convert_to_google_sheet(
        self,
        source_file_id: str,
        new_name: str,
        parent_folder_id: str,
    ) -> Dict[str, Any]:
        """
        將 xlsx/xls/csv 轉成 Google Sheet。
        """
        body = {
            "name": new_name,
            "mimeType": GOOGLE_SHEET_MIME,
            "parents": [parent_folder_id],
        }

        try:
            return (
                self.service.files()
                .copy(
                    fileId=source_file_id,
                    body=body,
                    fields="id,name,mimeType,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as e:
            raise RuntimeError(
                f"轉檔失敗：source_file_id={source_file_id}, "
                f"new_name={new_name}, "
                f"parent_folder_id={parent_folder_id}, "
                f"error={e}"
            ) from e

    def replace_google_sheet_from_source(
        self,
        source_file_id: str,
        source_name: str,
        parent_folder_id: str,
    ) -> Dict[str, Any]:
        """
        覆蓋式轉檔：
        1. 用來源檔名去掉 .xlsx/.xls/.csv 得到 base_name
        2. 刪除同名 Google Sheet
        3. 重新轉檔
        """
        base_name = strip_spreadsheet_extension(source_name)
        trashed_count = self.trash_google_sheet_by_name(parent_folder_id, base_name)
        converted = self.convert_to_google_sheet(
            source_file_id=source_file_id,
            new_name=base_name,
            parent_folder_id=parent_folder_id,
        )
        converted["replaced_count"] = trashed_count
        return converted


def strip_spreadsheet_extension(name: str) -> str:
    lower = name.lower()
    for ext in [".xlsx", ".xls", ".csv"]:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def is_source_spreadsheet_file(file: Dict[str, Any]) -> bool:
    """
    判斷是不是需要轉檔的來源檔。
    只處理 xlsx/xls/csv，不把已轉好的 Google Sheet 再當來源。
    """
    name = file.get("name", "").lower()
    mime_type = file.get("mimeType", "")

    if mime_type == GOOGLE_SHEET_MIME:
        return False

    if name.endswith((".xlsx", ".xls", ".csv")):
        return True

    if mime_type in EXCEL_MIMES:
        return True

    return False
