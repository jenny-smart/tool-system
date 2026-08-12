from __future__ import annotations

import argparse
import base64
import json
import uuid
from datetime import datetime
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import existing_page, logout_and_close, run_download
from tools.bank_statement.open_login import LOGIN_URLS, fill_fubon, is_fubon_logged_in, wait_fubon_login
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


def parse_date(value: str):
    return datetime.strptime(value.replace("/", "-"), "%Y-%m-%d").date()


def _login_context(page: Page):
    for candidate_page in reversed(page.context.pages):
        if candidate_page.is_closed() or "taipeifubon" not in candidate_page.url:
            continue
        for context in [candidate_page, *candidate_page.frames]:
            try:
                label = context.get_by_text("請輸入右側驗證碼", exact=True).filter(visible=True).first
                if label.count() and label.is_visible():
                    return candidate_page, context, label
            except Exception:
                continue
    raise RuntimeError("找不到富邦驗證碼欄位")


def capture_captcha(page: Page) -> tuple[Page, bytes]:
    active_page, context, label = _login_context(page)
    row = label.locator("xpath=ancestor::tr[1]")
    images = row.locator("img,canvas")
    target = next(
        (images.nth(index) for index in range(images.count()) if images.nth(index).is_visible()),
        row,
    )
    return active_page, target.screenshot(type="jpeg", quality=70)


def submit_captcha(context: BrowserContext, session_token: str, code: str) -> Page:
    page = None
    for candidate in context.pages:
        if candidate.is_closed() or "taipeifubon" not in candidate.url:
            continue
        try:
            if candidate.evaluate("() => window.name") == f"fubon-mobile-{session_token}":
                page = candidate
                break
        except Exception:
            continue
    if page is None:
        raise RuntimeError("找不到原富邦驗證分頁，請重新擷取驗證碼")
    page, frame, label = _login_context(page)
    captcha = label.locator("xpath=ancestor::tr[1]//input[not(@type='hidden')]").first
    captcha.fill(code.strip())
    controls = frame.locator("a,button,input[type='button'],input[type='submit']")
    submit = None
    for index in range(controls.count()):
        item = controls.nth(index)
        if not item.is_visible():
            continue
        text = (item.get_attribute("value") or item.inner_text() or "").strip()
        if text == "登入":
            submit = item
    if submit is None:
        raise RuntimeError("找不到富邦登入送出按鈕")
    submit.click(timeout=5_000)
    return wait_fubon_login(context, page, timeout_ms=30_000)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="富邦手機圖形驗證碼流程")
    parser.add_argument("mode", choices=("capture", "verify"))
    parser.add_argument("--area", required=True)
    parser.add_argument("--resume", choices=("login", "download"), default="login")
    parser.add_argument("--start", type=parse_date)
    parser.add_argument("--end", type=parse_date)
    parser.add_argument("--session-token", default="")
    parser.add_argument("--captcha", default="")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account = load_account("fubon", args.area, args.accounts_file.expanduser())
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        if args.mode == "capture":
            page = existing_page(context) or context.new_page()
            if is_fubon_logged_in(page):
                if args.resume == "download":
                    if not args.start or not args.end:
                        raise ValueError("富邦明細下載缺少日期")
                    try:
                        page = run_download(context, page, account, args.start, args.end, args.output)
                    finally:
                        logout_and_close(context, page)
                print('CAPTCHA_RESULT:' + json.dumps({"already_logged_in": True}, ensure_ascii=False))
                return 0
            page.goto(LOGIN_URLS["fubon"], wait_until="domcontentloaded", timeout=30_000)
            page = fill_fubon(page, account)
            token = uuid.uuid4().hex
            page.evaluate("token => { window.name = 'fubon-mobile-' + token; }", token)
            page, image = capture_captcha(page)
            payload = {
                "session_token": token,
                "image_base64": base64.b64encode(image).decode("ascii"),
                "mime_type": "image/jpeg",
                "resume": args.resume,
                "area": args.area,
                "start_date": args.start.isoformat() if args.start else "",
                "end_date": args.end.isoformat() if args.end else "",
            }
            print("CAPTCHA_RESULT:" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return 0
        if not args.session_token or not args.captcha.strip():
            raise ValueError("缺少驗證 Session 或驗證碼")
        page = submit_captcha(context, args.session_token, args.captcha)
        if args.resume == "login":
            print(f"富邦／{args.area} 登入完成；沿用同一 Chrome Session。")
            return 0
        if not args.start or not args.end:
            raise ValueError("富邦明細下載缺少日期")
        try:
            page = run_download(context, page, account, args.start, args.end, args.output)
        finally:
            logout_and_close(context, page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
