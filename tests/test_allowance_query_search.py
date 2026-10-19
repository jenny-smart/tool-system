"""鯨躍折讓單查詢頁的「搜尋」是 <a>搜尋<span class="icon">›</span></a>，只能點看得到的那顆。"""
import importlib.util
import sys
from pathlib import Path

import pytest

_saved = {name: sys.modules.pop(name) for name in list(sys.modules) if name.split(".")[0] == "playwright"}
try:
    sync_api = pytest.importorskip("playwright.sync_api")
    _spec = importlib.util.spec_from_file_location(
        "_real_allowance_create", Path(__file__).resolve().parents[1] / "tools" / "invoice_center" / "allowance_create.py")
    allowance_create = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(allowance_create)
finally:
    for name in [n for n in sys.modules if n.split(".")[0] == "playwright"]:
        del sys.modules[name]
    sys.modules.update(_saved)

HIDDEN = '<div style="display:none"><a onclick="window.hit=\'hidden\'">搜尋<span class="icon">›</span></a></div>'
SHOWN = '<a href="#" onclick="window.hit=\'shown\';return false;">搜尋<span class="icon">›</span></a>'


def _page(playwright, html):
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:  # 測試環境沒有瀏覽器
        pytest.skip(f"no browser: {exc}")
    page = browser.new_page()
    page.set_content(html)
    return browser, page


def test_clicks_visible_search_with_icon():
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, HIDDEN + SHOWN)
        allowance_create._click_query_search(page)
        assert page.evaluate("window.hit") == "shown"
        browser.close()


def test_raises_when_no_search_button():
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, HIDDEN)
        with pytest.raises(RuntimeError, match="找不到「搜尋」按鈕"):
            allowance_create._click_query_search(page)
        browser.close()
