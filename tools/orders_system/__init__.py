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
