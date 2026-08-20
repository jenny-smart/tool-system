from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tools.memo_system import atm, memo

TZ = ZoneInfo("Asia/Taipei")
REGIONS = ["台北", "台中"]


def main() -> None:
    """只負責「新增」：把最新的 ATM 待付款清單貼進工作表下方。

    不執行 process_atm_rows（按已付款／開發票／寄確認信）——那一步會直接
    影響客戶收到的信件與訂單付款狀態，且只該套用在已經透過銀行明細比對
    確認（T=已配對）的列，屬於另外的自動化範圍，目前先保留給人工／
    後續另行處理，這裡不動。
    """
    cli_email = str(memo.secret_value("LEMON_EMAIL", ""))
    cli_password = str(memo.secret_value("LEMON_PASSWORD", ""))
    memo.set_runtime_credentials(cli_email, cli_password)

    session = memo.login()
    date_until = datetime.now(TZ).strftime("%Y-%m-%d")

    for region in REGIONS:
        unpaid_rows = atm.search_atm_unpaid_orders(session=session, date_until=date_until)
        paste_result = atm.paste_atm_unpaid_list(region=region, rows=unpaid_rows)

        if paste_result.get("errors"):
            print(f"{region}：新增失敗：{'；'.join(paste_result['errors'])}", flush=True)
            continue

        print(
            f"{region}：新增 {paste_result.get('pasted', 0)} 筆待付款候選"
            f"（略過 {paste_result.get('skipped_existing', 0)} 筆已存在）",
            flush=True,
        )


if __name__ == "__main__":
    main()
