# ============================================================
# 墊片檔案（不會被自動同步覆蓋，手動維護，內容長期不變）。
#
# ordersapp.py 裡原封不動寫的是：
#     from memo_system.ui import render_memo_system
# 因為 tools/orders_system 已經被加進 sys.path（見同資料夾的 __init__.py），
# Python 會在這裡找到 memo_system 這個「墊片套件」，
# 這支檔案再把呼叫轉給 tool-system 真正的備忘系統模組。
#
# 好處：orders-system 那邊的 ordersapp.py 永遠不用因為「被搬到 tool-system」
# 而修改 import 路徑，GitHub Actions 才能單純做「複製檔案」，不用夾帶
# sed 改寫指令，同步流程比較不容易壞掉。
# ============================================================
from tools.memo_system.ui import render_memo_system  # noqa: F401

__all__ = ["render_memo_system"]
