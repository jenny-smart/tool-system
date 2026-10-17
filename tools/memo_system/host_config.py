"""tool-system 提供給 orders-system 服務異動／ATM 的備援設定。

orders-system 的 memo_system 原本只讀 Streamlit secrets；tool-system 的排程（GitHub
Actions）沒有那些 secrets，所以在這裡提供：
  - 清潔異動試算表：主控表「清潔異動設定」分頁（地區、試算表ID、GID、啟用），讀不到時用台北／台中內建值
  - ATM 試算表：台北／台中內勤工作表單
  - 服務帳號：tools.common.config_loader.get_service_account_info()
由 tools/orders_system/__init__.py 註冊到 memo_system.host。
"""
from __future__ import annotations

REGION_CONFIG_SHEET = "清潔異動設定"
_DEFAULT_CHANGE_SHEETS = {
    "台北": {"spreadsheet_id": "1bNcJuFuP--jdpNo2zJKOpvuq-5rSHW3LgGE8HEepf44", "gid": 759897417},
    "台中": {"spreadsheet_id": "1AlsgBL7uAooiU8hb0v-02J2MdBgDVJtGHgvD3U84hCM", "gid": 759897417},
}
_ATM_SHEETS = {
    "台北": {"spreadsheet_id": "1bNcJuFuP--jdpNo2zJKOpvuq-5rSHW3LgGE8HEepf44"},
    "台中": {"spreadsheet_id": "1AlsgBL7uAooiU8hb0v-02J2MdBgDVJtGHgvD3U84hCM"},
}
_change_cache = None


def change_sheets(force_reload: bool = False) -> dict:
    """地區 → {spreadsheet_id, gid}；主控表「清潔異動設定」啟用的列優先，讀不到時用內建台北／台中。"""
    global _change_cache
    if _change_cache is not None and not force_reload:
        return _change_cache
    sheets = dict(_DEFAULT_CHANGE_SHEETS)
    try:
        from tools.common.config_loader import is_enabled, read_sheet_records

        loaded = {}
        for record in read_sheet_records(REGION_CONFIG_SHEET):
            region = (record.get("地區") or "").strip()
            sid = (record.get("試算表ID") or "").strip()
            if not region or not sid or not is_enabled(record.get("啟用", "")):
                continue
            gid_text = (record.get("GID") or "").strip()
            loaded[region] = {"spreadsheet_id": sid, "gid": int(gid_text) if gid_text.isdigit() else None}
        if loaded:
            sheets = loaded
    except Exception:
        pass
    _change_cache = sheets
    return sheets


def region_sheet(kind: str, region: str):
    if kind == "CHANGE":
        return change_sheets().get(region)
    if kind == "ATM":
        return _ATM_SHEETS.get(region)
    return None


def regions(kind: str):
    if kind == "CHANGE":
        return list(change_sheets())
    if kind == "ATM":
        return list(_ATM_SHEETS)
    return None


def service_account():
    from tools.common.config_loader import get_service_account_info

    return get_service_account_info()


def register(host) -> None:
    """只補上尚未註冊的項目，不覆蓋部署端其他設定。"""
    host.region_sheet = host.region_sheet or region_sheet
    host.regions = host.regions or regions
    host.service_account = host.service_account or service_account
