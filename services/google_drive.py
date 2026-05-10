from __future__ import annotations

import io
import os
import re
from typing import Iterable, Optional

from googleapiclient.http import (
    MediaFileUpload,
    MediaIoBaseDownload,
    MediaIoBaseUpload,
)

GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"
SUPPORTED_SOURCE_EXTENSIONS = (".xls", ".xlsx", ".csv")


def q_escape(value: str) -> str:
    return value.replace("'", "\\'")


class DriveService:
    def __init__(self, drive):
        self.drive = drive

    def get_or_create_folder(self, parent_id: str, name: str) -> dict:
        q = (
            f"'{q_escape(parent_id)}' in parents and "
            f"mimeType='{FOLDER_MIME}' and "
            f"name='{q_escape(name)}' and trashed=false"
        )

        res = self.drive.files().list(
            q=q,
            fields="files(id,name,mimeType,webViewLink)",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        files = res.get("files", [])
        if files:
            return files[0]

        meta = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }

        return self.drive.files().create(
            body=meta,
            fields="id,name,mimeType,webViewLink",
            supportsAllDrives=True,
        ).execute()

    def list_files(self, folder_id: str) -> list[dict]:
        files: list[dict] = []
        token = None

        while True:
            res = self.drive.files().list(
                q=f"'{q_escape(folder_id)}' in parents and trashed=false",
                fields="nextPageToken,files(id,name,mimeType,webViewLink,size)",
                pageSize=1000,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            files.extend(res.get("files", []))
            token = res.get("nextPageToken")

            if not token:
                break

        return files

    def find_files_by_name(self, folder_id: str, name: str) -> list[dict]:
        q = (
            f"'{q_escape(folder_id)}' in parents and "
            f"name='{q_escape(name)}' and trashed=false"
        )

        res = self.drive.files().list(
            q=q,
            fields="files(id,name,mimeType,webViewLink)",
            pageSize=20,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        return res.get("files", [])

    def find_file_contains(
        self,
        folder_id: str,
        keywords: Iterable[str],
    ) -> Optional[dict]:
        keys = [k for k in keywords if k]

        for file in self.list_files(folder_id):
            name = file["name"]

            if all(k in name for k in keys):
                return file

        return None

    def copy_file(
        self,
        file_id: str,
        new_name: str,
        parent_folder_id: str,
    ) -> dict:
        meta = {
            "name": new_name,
            "parents": [parent_folder_id],
        }

        return self.drive.files().copy(
            fileId=file_id,
            body=meta,
            fields="id,name,mimeType,webViewLink",
            supportsAllDrives=True,
        ).execute()

    def create_blank_spreadsheet_in_folder(
        self,
        name: str,
        folder_id: str,
    ) -> dict:
        meta = {
            "name": name,
            "mimeType": GOOGLE_SHEETS_MIME,
            "parents": [folder_id],
        }

        return self.drive.files().create(
            body=meta,
            fields="id,name,mimeType,webViewLink",
            supportsAllDrives=True,
        ).execute()

    def trash_duplicates_named_google_sheet(
        self,
        folder_id: str,
        base_name: str,
    ) -> None:
        for file in self.find_files_by_name(folder_id, base_name):
            if file.get("mimeType") == GOOGLE_SHEETS_MIME:
                self.drive.files().update(
                    fileId=file["id"],
                    body={"trashed": True},
                    supportsAllDrives=True,
                ).execute()

    def download_file_bytes(self, file_id: str) -> bytes:
        request = self.drive.files().get_media(fileId=file_id)
        buf = io.BytesIO()

        downloader = MediaIoBaseDownload(buf, request)
        done = False

        while not done:
            _, done = downloader.next_chunk()

        return buf.getvalue()

    def upload_bytes_as_google_sheet(
        self,
        content: bytes,
        name: str,
        folder_id: str,
        mime_type: str,
    ) -> dict:
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype=mime_type,
            resumable=True,
        )

        meta = {
            "name": name,
            "mimeType": GOOGLE_SHEETS_MIME,
            "parents": [folder_id],
        }

        return self.drive.files().create(
            body=meta,
            media_body=media,
            fields="id,name,mimeType,webViewLink",
            supportsAllDrives=True,
        ).execute()

    def upload_file(
        self,
        local_path: str,
        folder_id: str,
    ) -> dict:
        meta = {
            "name": os.path.basename(local_path),
            "parents": [folder_id],
        }

        media = MediaFileUpload(
            local_path,
            resumable=True,
        )

        return self.drive.files().create(
            body=meta,
            media_body=media,
            fields="id,name,mimeType,webViewLink",
            supportsAllDrives=True,
        ).execute()

    def ensure_google_sheet(
        self,
        file: dict,
        target_folder_id: str,
    ) -> dict:
        name = file["name"]

        if file["mimeType"] == GOOGLE_SHEETS_MIME:
            return file

        base_name = re.sub(
            r"\.(xls|xlsx|csv)$",
            "",
            name,
            flags=re.I,
        )

        if not name.lower().endswith(SUPPORTED_SOURCE_EXTENSIONS):
            raise ValueError(f"不支援的檔案格式：{name}")

        self.trash_duplicates_named_google_sheet(
            target_folder_id,
            base_name,
        )

        mime_type = (
            "text/csv"
            if name.lower().endswith(".csv")
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        content = self.download_file_bytes(file["id"])

        return self.upload_bytes_as_google_sheet(
            content=content,
            name=base_name,
            folder_id=target_folder_id,
            mime_type=mime_type,
        )
