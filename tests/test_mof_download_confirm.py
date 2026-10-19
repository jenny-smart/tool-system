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


PAGE = (
    HIDDEN * 21
    + '<div class="card"><table><tr><td><button title="下載" class="btn"><svg></svg></button></td></tr></table>'
    '<div class="card-footer bg-light have_btn"><div class="btn_function"><ul><li>'
    '<button title="下載" class="btn btn-sm btn-falcon" onclick="window.clicks=(window.clicks||0)+1;'
    'if(window.clicks>=2){document.body.insertAdjacentHTML(\'beforeend\', ' + repr(SHOWN).replace('"', '&quot;') + ')}">'
    '<svg></svg> 下載</button></li></ul></div></div></div>'
)


def test_clicks_footer_download_and_retries_until_dialog_appears():
    with sync_api.sync_playwright() as p:
        browser, page = _page(p, PAGE)
        mof_serial._click_download_button(page)
        found, _total, _shown = mof_serial._wait_visible_confirm(page, 1)
        assert not found  # 第一次按沒有出燈箱（模擬沒按到）
        mof_serial._click_download_button(page)
        found, _total, _shown = mof_serial._wait_visible_confirm(page, 2)
        assert found
        page.locator("[data-mof-confirm='0']").first.click()
        assert page.evaluate("window.clicks") == 2
        assert page.evaluate("window.clicked") == "shown"
        browser.close()
