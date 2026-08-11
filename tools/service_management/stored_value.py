"""客服排程的儲值功能獨立執行入口。

最後更新：2026-08-12

Change Log：
- 2026-08-12：建立儲值功能獨立入口，轉交既有儲值結算與 VIP 對帳流程。

目前實際流程仍由既有模組提供，以保留既有排程與匯出行為；Tools App
應使用本模組，避免把儲值功能誤稱為 CRM。
"""

from tools.service_management.crm_export import main


if __name__ == "__main__":
    main()
