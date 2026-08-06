from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path


AREAS = ("台北", "台中", "新北", "桃園", "新竹", "高雄")
ACCOUNTS_FILE = Path.home() / "NewebPay account" / "newebpay_accounts.json"


def prompt(label: str, previous: str = "", secret: bool = False) -> str:
    suffix = "（直接 Enter 保留原值）" if previous else ""
    reader = getpass.getpass if secret else input
    value = reader(f"  {label}{suffix}：").strip()
    return value or previous


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="設定藍新各區企業登入帳密")
    parser.add_argument(
        "--area",
        nargs="+",
        choices=AREAS,
        help="只修改指定區域；未指定時設定全部區域",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected_areas = tuple(args.area) if args.area else AREAS
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, str]] = {}
    if ACCOUNTS_FILE.exists():
        raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        rows = raw.get("areas", []) if isinstance(raw, dict) else []
        if isinstance(rows, list):
            existing = {str(row.get("area")): row for row in rows if isinstance(row, dict)}
    print(f"所有區域帳密將集中儲存於：{ACCOUNTS_FILE}")
    print("密碼輸入時畫面不會顯示字元，這是正常的。\n")

    saved: list[dict[str, str]] = []
    for area in selected_areas:
        old = existing.get(area, {})
        print(f"[{area}]")
        company_id = prompt("公司統編", str(old.get("company_id", "")))
        account = prompt("管理者帳號", str(old.get("account", "")))
        password = prompt("管理者密碼", str(old.get("password", "")), secret=True)
        merchant_label = prompt("商店名稱（不指定請直接 Enter）", str(old.get("merchant_label", "")))
        if not company_id or not account or not password:
            print(f"  未儲存：{area} 的統編、帳號、密碼不可空白\n")
            continue
        saved.append(
            {
                "area": area,
                "company_id": company_id,
                "account": account,
                "password": password,
                "merchant_label": merchant_label,
            }
        )
        print(f"  已加入：{area}\n")

    # 只修改部分區域時，保留未選取區域的既有設定。
    if args.area:
        saved_areas = {row["area"] for row in saved}
        saved.extend(row for area, row in existing.items() if area not in saved_areas)
        order = {area: index for index, area in enumerate(AREAS)}
        saved.sort(key=lambda row: order.get(row["area"], len(order)))

    ACCOUNTS_FILE.write_text(
        json.dumps({"areas": saved}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(ACCOUNTS_FILE, 0o600)

    print(f"設定完成：{ACCOUNTS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
