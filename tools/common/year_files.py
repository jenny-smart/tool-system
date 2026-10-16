"""跨年度檔案定位。

由目前設定的檔案 ID 找「同一份檔案的另一個年度版本」：
1. 檔名年份換成目標年度（- 與 _ 視為相同）；
2. 先找同一資料夾；
3. 找不到時往上找含年份的資料夾（例：2026專員承攬服務費），換成目標年度的同名資料夾，
   再依相同的子資料夾路徑（例：03.桃園專員）往下找。

例：設定檔指向「2026專員承攬服務費/03.桃園專員/2026專員名冊薪資-桃園」，
    要 2027 年版時會找「2027專員承攬服務費/03.桃園專員/2027專員名冊薪資-桃園」。
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

FOLDER_MIME = "application/vnd.google-apps.folder"
YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
MAX_LEVELS = 4

_CACHE: Dict[Tuple[str, int], str] = {}


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).replace("_", "-")


def _replace_year(name: str, year: int) -> str | None:
    m = YEAR_RE.search(name or "")
    if not m:
        return None
    return name[:m.start()] + str(year) + name[m.end():]


def _get(drive, file_id: str) -> dict:
    return drive.files().get(
        fileId=file_id, fields="id,name,parents,mimeType", supportsAllDrives=True
    ).execute()


def _children(drive, parent_id: str) -> list[dict]:
    items, token = [], None
    while True:
        res = drive.files().list(
            q=f"'{parent_id}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType)",
            pageSize=1000, pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        items.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            return items


def _find(drive, parent_id: str, name: str, mime: str) -> dict | None:
    target = _norm(name)
    hits = [f for f in _children(drive, parent_id)
            if _norm(f.get("name")) == target and f.get("mimeType", mime) == mime]
    return hits[0] if len(hits) == 1 else None


def file_year(drive, file_id: str) -> int | None:
    m = YEAR_RE.search(_get(drive, file_id).get("name", ""))
    return int(m.group(1)) if m else None


def file_for_year(drive, file_id: str, year: int) -> str:
    """回傳 file_id 的 year 年度版本 ID；同年度回傳原 ID，找不到則 raise。"""
    year = int(year)
    key = (file_id, year)
    if key in _CACHE:
        return _CACHE[key]

    meta = _get(drive, file_id)
    name = meta.get("name", "")
    m = YEAR_RE.search(name)
    if not m or int(m.group(1)) == year:
        _CACHE[key] = file_id
        return file_id
    target_name = _replace_year(name, year)
    mime = meta.get("mimeType", "")

    # 1. 同資料夾
    parent_id = (meta.get("parents") or [""])[0]
    if parent_id:
        hit = _find(drive, parent_id, target_name, mime)
        if hit:
            _CACHE[key] = hit["id"]
            return hit["id"]

    # 2. 往上找年度資料夾，換成目標年度後依相同子路徑往下找
    path: list[str] = []
    folder_id = parent_id
    for _ in range(MAX_LEVELS):
        if not folder_id:
            break
        folder = _get(drive, folder_id)
        folder_name = folder.get("name", "")
        grand_id = (folder.get("parents") or [""])[0]
        new_folder_name = _replace_year(folder_name, year)
        if new_folder_name and grand_id:
            node = _find(drive, grand_id, new_folder_name, FOLDER_MIME)
            for sub in reversed(path):
                if not node:
                    break
                node = _find(drive, node["id"], _replace_year(sub, year) or sub, FOLDER_MIME)
            if node:
                hit = _find(drive, node["id"], target_name, mime)
                if hit:
                    _CACHE[key] = hit["id"]
                    return hit["id"]
            break
        path.append(folder_name)
        folder_id = grand_id

    raise FileNotFoundError(
        f"找不到 {year} 年度檔案「{target_name}」；請先在 tool-system 執行「生成新年度」"
    )
