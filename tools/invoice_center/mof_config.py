# 財政部電子發票整合服務平台（einvoice.nat.gov.tw）字軌號碼取號帳密設定。
#
# 帳密只存在本機（不進 git），格式與既有 ~/EI account/ei_accounts.json 相同慣例：
#
# {
#   "台北": {"ubn": "42627791", "account": "42627791", "password": "..."},
#   "台中": {"ubn": "82830399", "account": "82830399", "password": "..."}
# }
#
# 登入頁「統一編號」「帳號」是兩個各自獨立的欄位（畫面上剛好常常同值，但不保證一定
# 相同），未設定 account 時才會回退用 ubn 頂替。未設定 password 時，程式只會預填
# 統一編號／帳號，密碼與圖形驗證碼一律由使用者手動輸入。
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MOF_LOGIN_URL = "https://www.einvoice.nat.gov.tw/accounts/login/b"
MOF_AREA_LABELS = ["台北", "台中", "桃園", "新竹", "高雄"]

DEFAULT_ACCOUNT_PATHS = [
    Path.home() / "MOF account" / "mof_accounts.json",
    Path(__file__).with_name("mof_accounts.json"),
]


@dataclass(frozen=True)
class MOFCredentials:
    area: str
    ubn: str
    account: str
    password: str = ""

    @property
    def masked_ubn(self) -> str:
        if len(self.ubn) <= 4:
            return "*" * len(self.ubn)
        return f"{self.ubn[:2]}***{self.ubn[-2:]}"


def load_accounts(path: Path | None = None) -> dict[str, Any]:
    candidates = [path.expanduser()] if path else DEFAULT_ACCOUNT_PATHS
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    searched = "\n".join(str(item) for item in DEFAULT_ACCOUNT_PATHS)
    raise FileNotFoundError(f"找不到財政部電子發票帳密檔：\n{searched}")


def credentials_for(area: str, accounts: dict[str, Any]) -> MOFCredentials:
    if area not in MOF_AREA_LABELS:
        raise ValueError(f"不支援的區域：{area}，目前支援：{'、'.join(MOF_AREA_LABELS)}")
    row = accounts.get(area) or {}
    ubn = str(row.get("ubn") or row.get("userid") or row.get("user") or "").strip()
    if not ubn:
        raise RuntimeError(f"{area} 缺財政部統一編號（ubn）")
    account = str(row.get("account") or "").strip() or ubn
    password = str(row.get("password") or "")
    return MOFCredentials(area=area, ubn=ubn, account=account, password=password)


def configured_areas(accounts: dict[str, Any]) -> list[str]:
    result = [
        area
        for area in MOF_AREA_LABELS
        if str((accounts.get(area) or {}).get("ubn") or (accounts.get(area) or {}).get("userid") or "").strip()
    ]
    if not result:
        raise RuntimeError("財政部電子發票帳密檔沒有任何已設定區域")
    return result
