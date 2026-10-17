"""轉接：服務異動／ATM 以 orders-system 為準。

實際程式自 orders-system 同步到 tools/orders_system/memo_system/change_order.py，
這裡讓 tools.memo_system.change_order 與 memo_system.change_order 是同一個模組（排程、Tools App 共用一份）。
"""
import importlib
import sys

import tools.orders_system  # noqa: F401  （把 tools/orders_system 加進 sys.path，並註冊 tool-system 備援設定）

sys.modules[__name__] = importlib.import_module("memo_system.change_order")
