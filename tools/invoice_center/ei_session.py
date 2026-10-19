"""鯨躍登入的唯一共用入口：接續目前畫面，不一律從第一層重來。

順序：
  1. 第二層已登入這區帳號（核對虛實合一 ID）→ 直接沿用
  2. 第二層是別區帳號或已失效 → 只換／重登第二層
  3. 第二層不能用 → 從現有第一層分頁進第二層
  4. 都不行 → 登入第一層再進第二層
所有鯨躍功能（下載、字軌匯入配號、紙本、中獎、折讓、開立發票）都應呼叫 open_ei_page()。
"""
from __future__ import annotations

from tools.invoice_center.cetustek_login_only import ensure_expected_ei_login
from tools.invoice_center.ei_export_all import EI_HOME_URL, login_portal, open_second_login


def prepare_download_session(context, portal_page, ei_page, accounts, credentials):
    """回傳 (portal_page, 已登入這區第二層的頁面, 是否沿用原本的登入狀態)。"""
    existing = ei_page is not None
    candidate = ei_page if existing else context.new_page()
    try:
        if not existing:
            candidate.goto(EI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
        reused = ensure_expected_ei_login(candidate, credentials)
        print(f"[{credentials.label}] 第二層已驗證，略過第一層登入")
        return portal_page, candidate, bool(existing and reused is not False)
    except Exception as exc:
        print(f"[{credentials.label}] 目前第二層無法使用，改檢查第一層入口：{exc}")
        if not existing:
            candidate.close()

    if portal_page is not None:
        try:
            candidate = open_second_login(context, portal_page)
            ensure_expected_ei_login(candidate, credentials)
            print(f"[{credentials.label}] 已從現有入口進入第二層")
            return portal_page, candidate, False
        except Exception as exc:
            print(f"[{credentials.label}] 現有入口無法進入第二層，重新登入第一層：{exc}")
    else:
        portal_page = context.new_page()

    login_portal(portal_page, accounts)
    candidate = open_second_login(context, portal_page)
    ensure_expected_ei_login(candidate, credentials)
    return portal_page, candidate, False


def open_ei_page(context, area: str, accounts=None):
    """找出目前的鯨躍分頁並接續登入這區，回傳 (第二層頁面, 是否沿用原本的登入狀態)。"""
    from tools.invoice_center.chrome_cdp import find_invoice_pages
    from tools.invoice_center.ei_export_all import credentials_for, load_accounts

    accounts = accounts if accounts is not None else load_accounts(None)
    credentials = credentials_for(area, accounts)
    portal_page, ei_page = find_invoice_pages(context)
    _portal, page, reused = prepare_download_session(context, portal_page, ei_page, accounts, credentials)
    return page, reused
