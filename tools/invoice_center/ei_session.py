"""Prefer an authorized EI session before asking for portal authentication."""
from __future__ import annotations

from tools.invoice_center.cetustek_login_only import ensure_expected_ei_login
from tools.invoice_center.ei_export_all import EI_HOME_URL, login_portal, open_second_login


def prepare_download_session(context, portal_page, ei_page, accounts, credentials):
    existing = ei_page is not None
    candidate = ei_page if existing else context.new_page()
    try:
        if not existing:
            candidate.goto(EI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
        ensure_expected_ei_login(candidate, credentials)
        print(f"[{credentials.label}] 第二層已驗證，略過第一層登入")
        return portal_page, candidate, existing
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
