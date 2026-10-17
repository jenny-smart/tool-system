"""下載檔直接上傳到雲端「{YYYY}專員承攬服務費／{NN}.{地區}專員／{期別}」資料夾。

原本藍新、鯨躍（區間）下載是存到 Mac 的 Google Drive 同步資料夾，要靠 Mac
同步才會上雲端；改用 Drive API（Jenny OAuth）直接上傳，換電腦也不受影響。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from tools.invoice_center.invoice_archive import (
    AREA_FOLDER_NAMES,
    FOLDER_MIME,
    MONTHLY_ROOT_FOLDER_ID,
    _escape,
    get_or_create_folder,
    get_google_services,
    upload_replacing,
)

# 承攬費資料夾的地區順序（找不到既有資料夾時用來建立新的）
CONTRACTOR_AREA_FOLDERS = {**AREA_FOLDER_NAMES, "家電": "06.家電專員"}


def _area_folder(drive, year_folder_id: str, area: str) -> str:
    """找名稱以「.{地區}專員」結尾的資料夾；沒有時依固定編號建立。"""
    suffix = f".{area}專員"
    files = drive.files().list(
        q=f"'{_escape(year_folder_id)}' in parents and mimeType='{FOLDER_MIME}' and trashed=false",
        fields="files(id,name)", pageSize=100,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    matches = [item for item in files if str(item.get("name") or "").endswith(suffix)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        raise RuntimeError(f"「{area}」有 {len(matches)} 個承攬費資料夾：{[m['name'] for m in matches]}")
    name = CONTRACTOR_AREA_FOLDERS.get(area)
    if not name:
        raise RuntimeError(f"找不到「{area}」的承攬費資料夾（名稱需以「{suffix}」結尾）")
    return get_or_create_folder(drive, year_folder_id, name)


def period_folder_id(drive, area: str, period: str) -> tuple[str, str]:
    """回傳 (資料夾ID, 路徑說明)；period 例如「202610-2」。"""
    year_name = f"{period[:4]}專員承攬服務費"
    year_id = get_or_create_folder(drive, MONTHLY_ROOT_FOLDER_ID, year_name)
    area_id = _area_folder(drive, year_id, area)
    folder_id = get_or_create_folder(drive, area_id, period)
    return folder_id, f"{year_name}/{area}/{period}"


def upload_period_files(
    paths: Iterable[Path], area: str, period: str, log: Callable[[str], None] = print
) -> list[str]:
    """把檔案上傳（同名覆蓋）到該區該期別資料夾，回傳雲端檔案 ID。"""
    files = [Path(p) for p in paths if Path(p).is_file()]
    if not files:
        return []
    drive, _sheets = get_google_services()
    folder_id, label = period_folder_id(drive, area, period)
    ids = []
    for path in files:
        ids.append(upload_replacing(drive, path, folder_id))
        log(f"  已上傳雲端：{label}/{path.name}")
    return ids
