# ============================================================
# tools/orders_system/ui.py
#
# 這支檔案「不會」被 GitHub Actions 覆蓋，是手動維護、長期不動的檔案。
# 真正的畫面邏輯全部在 ordersapp.py（每次 orders-system 更新，
# GitHub Actions 會自動把最新的 ordersapp.py 複製到這個資料夾覆蓋掉）。
#
# 這裡只是把 ordersapp.py 裡的 render_orders_page 轉個名字，
# 讓 pages/訂單系統.py 原本的呼叫方式完全不用改：
#     from tools.orders_system.ui import render_orders_system
# ============================================================
from .ordersapp import render_orders_page as render_orders_system

__all__ = ["render_orders_system"]
