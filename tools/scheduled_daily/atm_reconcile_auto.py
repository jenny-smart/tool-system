from __future__ import annotations

from tools.memo_system import atm, memo

REGIONS = ["台北", "台中"]


def find_new_rows(rows: list[list[str]]) -> list[int]:
    """只挑「有訂單編號（J）且尚未對帳（T 欄空白）」的列，避免動到已處理過的原資料列。"""
    new_rows: list[int] = []
    for sheet_row, row in enumerate(rows[1:], start=2):
        order_no = memo.safe_cell(row, atm.COL_ORDER_NO)
        recon_status = memo.safe_cell(row, atm.COL_RECON_STATUS)
        if order_no and not recon_status:
            new_rows.append(sheet_row)
    return new_rows


def main() -> None:
    cli_email = str(memo.secret_value("LEMON_EMAIL", ""))
    cli_password = str(memo.secret_value("LEMON_PASSWORD", ""))
    memo.set_runtime_credentials(cli_email, cli_password)

    session = memo.login()

    for region in REGIONS:
        worksheet = atm.get_atm_worksheet(region)
        rows = memo.with_retry(worksheet.get_all_values)
        new_rows = find_new_rows(rows)

        if not new_rows:
            print(f"{region}：沒有新增待對帳列。", flush=True)
            continue

        row_spec = ",".join(str(r) for r in new_rows)
        print(f"{region}：發現新增列 {row_spec}，開始執行系統對帳。", flush=True)

        result = atm.process_atm_rows(
            region=region,
            row_spec=row_spec,
            do_mark_paid=True,
            do_issue_invoice=True,
            do_send_mail=True,
            session=session,
        )
        print(f"{region} 對帳結果：{result}", flush=True)


if __name__ == "__main__":
    main()
