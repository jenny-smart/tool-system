"""客服 CRM 的獨立執行入口。

最後更新：2026-08-12

Change Log：
- 2026-08-12：建立 CRM 獨立入口及全區／台北／台中參數，避免誤執行儲值流程。

CRM 商業邏輯尚待依「排程決策報表／三個月未排／Raw 排序」的正式欄位
規格實作。本入口刻意不呼叫儲值功能，避免再次把兩套流程混在一起。
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="檸檬家事客服 CRM 系統")
    parser.add_argument("--step", type=int, choices=[4, 5, 6], required=True)
    parser.add_argument("--area", choices=["全區", "台北", "台中"], default="全區")
    args = parser.parse_args()
    raise SystemExit(
        f"CRM Step {args.step}（{args.area}）已與儲值功能拆分，"
        "但尚未取得正式欄位與處理規格，因此未執行任何資料寫入。"
    )


if __name__ == "__main__":
    main()
