# ============================================================
# tools/orders_system/__init__.py
#
# 目的：讓從 orders-system repo 自動同步過來的 ordersapp.py / orders.py /
# quick_order.py / accounts.py 完全不用修改任何 import 語句就能在
# tool-system 裡正常運作。
#
# ordersapp.py 裡面寫的是：
#     from orders import run_process_web, ...
#     from accounts import ACCOUNTS
#     import quick_order as qo
# 這些是「絕對匯入」，Python 會去 sys.path 裡找同名的頂層模組。
# 只要把 tools/orders_system 這個資料夾本身加進 sys.path，
# 上面這些絕對匯入就能直接找到同資料夾內的 orders.py / accounts.py /
# quick_order.py，不需要改成相對匯入。
# ============================================================
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


# 服務異動／ATM（memo_system，自 orders-system 同步）在沒有 Streamlit secrets 時
# （例如 GitHub 排程），改用 tool-system 的「清潔異動設定」與服務帳號
try:
    from memo_system import host as _memo_host
    from tools.memo_system.host_config import register as _register_memo_host

    _register_memo_host(_memo_host)
except Exception:  # memo_system 尚未同步時不影響其他功能
    pass

# 年度根目錄找檔改用 Jenny OAuth（資料夾擁有者）；服務帳號看不到未分享的資料夾
try:
    import year_roots as _year_roots
    from tools.common.google_auth import get_drive_service as _oauth_drive

    if getattr(_year_roots, "drive_factory", "missing") is None:
        _year_roots.drive_factory = _oauth_drive
except Exception:  # 舊版 year_roots 或沒有 OAuth 設定時沿用服務帳號
    pass


# 本機 Agent 子程式沒有 Streamlit secrets，orders.get_service_account_info 會找不到憑證，
# 讀不到「年度根目錄設定」；找不到時改用 tool-system 的服務帳號來源（含本機 secrets.toml）
def _with_host_service_account(original):
    def get_service_account_info():
        try:
            return original()
        except Exception:
            from tools.common.config_loader import get_service_account_info as _host_info

            return _host_info()

    get_service_account_info._host_fallback = True
    return get_service_account_info


def _patch_orders_credentials() -> None:
    import orders as _orders

    current = _orders.get_service_account_info
    if not getattr(current, "_host_fallback", False):
        _orders.get_service_account_info = _with_host_service_account(current)


try:
    _plain_get_year_roots = _year_roots.get_year_roots

    def _get_year_roots():
        try:
            _patch_orders_credentials()
        except Exception:
            pass
        return _plain_get_year_roots()

    _year_roots.get_year_roots = _get_year_roots
except Exception:
    pass
