from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


# 舊版放在本機的服務帳號金鑰；雲端／本機 secrets 都讀不到時才用（相容用）
LEGACY_GOOGLE_CREDENTIALS_FILE = (
    Path.home() / "Bank account" / "tool-495212-58cabfa2def5.json"
)
# 財報裡各銀行明細的分頁（依名稱找，不用 GID）
REPORT_WORKSHEET_TITLES = {"fubon": "富邦更新", "yuanta": "元大更新"}


@dataclass(frozen=True)
class SheetTarget:
    area: str
    bank: str
    spreadsheet_id: str
    worksheet_title: str


def load_sheet_target(area: str, bank: str, year: int | None = None) -> SheetTarget:
    """依年度根目錄「財報」找當年度的「{地區}{YYYY}財報」，分頁依名稱找；找不到就報錯。"""
    title = REPORT_WORKSHEET_TITLES.get(bank)
    if not title:
        raise ValueError(f"不支援的銀行：{bank}")
    from tools.common.year_roots import root_file_id

    spreadsheet_id = root_file_id("財報", area, year)
    return SheetTarget(area, bank, spreadsheet_id, title)


def load_google_credentials(path: Path = LEGACY_GOOGLE_CREDENTIALS_FILE) -> dict[str, object]:
    """服務帳號：先用雲端 secrets（GitHub／Streamlit／本機 secrets.toml），都沒有才讀舊金鑰檔。"""
    try:
        from tools.common.config_loader import get_service_account_info

        raw = get_service_account_info()
    except Exception as exc:
        if not path.expanduser().exists():
            raise RuntimeError(f"找不到 Google 服務帳號設定：{exc}") from exc
        raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    missing = [
        key
        for key in ("type", "project_id", "private_key", "client_email", "token_uri")
        if not raw.get(key)
    ]
    if missing:
        raise ValueError(f"Google 服務帳號設定缺少欄位：{', '.join(missing)}")
    return raw
