from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ACCOUNTS_FILE = Path.home() / "Bank account" / "bank_accounts.json"
SUPPORTED_BANKS = {"fubon", "yuanta"}


@dataclass(frozen=True)
class BankAccount:
    bank: str
    area: str
    login_id: str
    user_code: str
    password: str
    bank_account: str = ""


def load_account(bank: str, area: str, path: Path = DEFAULT_ACCOUNTS_FILE) -> BankAccount:
    bank = str(bank).strip().lower()
    area = str(area).strip()
    if bank not in SUPPORTED_BANKS:
        raise ValueError(f"不支援的銀行：{bank}")
    if not path.exists():
        raise FileNotFoundError(f"找不到帳密檔：{path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get(bank, []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        raise ValueError(f"{path} 的 {bank} 必須是陣列")

    for row in rows:
        if not isinstance(row, dict) or str(row.get("area", "")).strip() != area:
            continue
        login_key = "id_number" if bank == "fubon" else "company_id"
        user_key = "user_code" if bank == "fubon" else "account"
        missing = [key for key in (login_key, user_key, "password") if not str(row.get(key, "")).strip()]
        if missing:
            raise ValueError(f"{bank}/{area} 缺少設定：{', '.join(missing)}")
        return BankAccount(
            bank=bank,
            area=area,
            login_id=str(row[login_key]).strip(),
            user_code=str(row[user_key]).strip(),
            password=str(row["password"]),
            bank_account=str(row.get("bank_account", "")).strip(),
        )

    raise ValueError(f"帳密檔找不到銀行／區域：{bank}/{area}")
