from __future__ import annotations

from typing import Any

from tools.common.config_loader import read_sheet_records, is_enabled


def load_systems_config() -> dict[str, Any]:
    records = read_sheet_records("系統設定")

    systems = []

    for record in records:
        name = record.get("系統名稱", "").strip()
        if not name:
            continue

        systems.append({
            "name": name,
            "type": record.get("type", "").strip(),
            "folder_id": (
                record.get("folder_id", "").strip()
                or record.get("共用雲端資料夾ID / 根目錄ID", "").strip()
                or record.get("月排程根目錄 ID", "").strip()
                or record.get("月排程根目錄ID", "").strip()
                or record.get("月排程總根目錄ID", "").strip()
            ),
            "enabled": is_enabled(record.get("啟用", "")),
            "raw": record,
        })

    return {"systems": systems}


def get_system_by_name(system_name: str) -> dict[str, Any]:
    cfg = load_systems_config()

    for system in cfg["systems"]:
        if system["name"] == system_name:
            return system

    raise RuntimeError(f"主控表找不到系統設定：{system_name}")
