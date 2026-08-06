from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_GOOGLE_SHEET_FILE = Path.home() / "Bank account" / "google_sheet.json"
DEFAULT_GOOGLE_CREDENTIALS_FILE = (
    Path.home() / "Bank account" / "tool-495212-58cabfa2def5.json"
)


@dataclass(frozen=True)
class SheetTarget:
    area: str
    bank: str
    spreadsheet_id: str
    worksheet_gid: int


def load_sheet_target(
    area: str,
    bank: str,
    path: Path = DEFAULT_GOOGLE_SHEET_FILE,
) -> SheetTarget:
    raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    area_config = raw.get("areas", {}).get(area, {})
    spreadsheet_id = str(area_config.get("spreadsheet_id", "")).strip()
    gid_key = f"{bank}_gid"
    try:
        worksheet_gid = int(area_config.get(gid_key, 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{area} 的 {gid_key} 必須是數字") from exc
    if not spreadsheet_id or not worksheet_gid:
        raise ValueError(f"尚未設定 {area} 的 spreadsheet_id／{gid_key}")
    return SheetTarget(area, bank, spreadsheet_id, worksheet_gid)


def load_google_credentials(
    path: Path = DEFAULT_GOOGLE_CREDENTIALS_FILE,
) -> dict[str, object]:
    raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    missing = [
        key
        for key in ("type", "project_id", "private_key", "client_email", "token_uri")
        if not raw.get(key)
    ]
    if missing:
        raise ValueError(f"Google 服務帳號檔缺少欄位：{', '.join(missing)}")
    return raw
