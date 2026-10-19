"""財政部字軌下載的「注意事項」燈箱：頁面上有多份隱藏的同樣燈箱，只能點看得到的「是」。"""
import importlib.util
import sys
from pathlib import Path

import pytest

_saved = {name: sys.modules.pop(name) for name in list(sys.modules) if name.split(".")[0] == "playwright"}
try:
    sync_api = pytest.importorskip("playwright.sync_api")
    from tools.invoice_center import mof_serial  # noqa: E402
    if not hasattr(mof_serial, "_click_visible_confirm"):  # 被其他測試換成假模組時，從檔案載入
        _spec = importlib.util.spec_from_file_location(
            "_real_mof_serial", Path(__file__).resolve().parents[1] / "tools" / "invoice_center" / "mof_serial.py")
        mof_serial = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(mof_serial)
finally:
    for name in [n for n in sys.modules if n.split(".")[0] == "playwright"]:
        del sys.modules[name]
    sys.modules.update(_saved)

HIDDEN = '<div class="modal" style="display:none"><p>下載檔案編碼為UTF-8</p><div class="btn" onclick="window.clicked=\'hidden\'">是</div></div>'
SHOWN = '<div class="modal"><p>下載檔案編碼為UTF-8</p><div class="btn" onclick="window.clicked=\'shown\'"> 是 </div></div>'


def _page(playwright, html):
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:  # 測試環境沒有瀏覽器
        pytest.skip(f"no browser: {exc}")
    page = browser.new_page()
    page.set_content(html)
    return browser, page


def test_clicks_only_the_visible_confirm():
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, HIDDEN * 21 + SHOWN)
        mof_serial._click_visible_confirm(page, timeout_seconds=3)
        assert page.evaluate("window.clicked") == "shown"
        browser.close()


def test_reports_counts_when_no_visible_confirm():
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, HIDDEN * 3)
        with pytest.raises(RuntimeError, match="看得到的 0 個"):
            mof_serial._click_visible_confirm(page, timeout_seconds=1)
        browser.close()
