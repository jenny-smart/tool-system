from __future__ import annotations

import argparse
from pathlib import Path

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.native_chrome import wait_for_manual_login
from tools.bank_statement.native_yuanta import wait_for_yuanta_login


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只登入銀行，不查詢或擷取明細")
    parser.add_argument(
        "--bank",
        choices=("fubon", "yuanta"),
        default="fubon",
        help="fubon=富邦；yuanta=元大（預設：fubon）",
    )
    parser.add_argument(
        "--area",
        required=True,
        choices=("台北", "台中", "桃園", "新竹", "高雄"),
    )
    parser.add_argument("--accounts-file", default=str(DEFAULT_ACCOUNTS_FILE))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account = load_account(args.bank, args.area, Path(args.accounts_file).expanduser())
    if args.bank == "yuanta":
        wait_for_yuanta_login(account)
        bank_name = "元大"
    else:
        wait_for_manual_login(account)
        bank_name = "富邦"
    print(f"{bank_name}／{args.area} 登入完成；Chrome 將保持開啟。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
