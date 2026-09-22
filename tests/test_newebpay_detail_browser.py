"""Run with RUN_BROWSER_TESTS=1 when local Chrome is available."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="requires local Chrome"
)


def test_detail_sections_and_full_payment_row():
    from playwright.sync_api import sync_playwright
    from tools.newebpay.express_payment_check import _read_current_page

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        try:
            page = browser.new_page()
            page.set_content('''
                <table><tr><td>TEST商店</td>
                <td><a onclick="setTimeout(() => document.querySelector('.modal').style.display='block', 100)">26092219000625189</a><br>TEST訂單</td>
                <td>清潔服務</td><td>信用卡</td><td>付款成功</td></tr></table>
                <div class="modal" style="display:none">
                <button class="close" onclick="this.parentElement.style.display='none'">關閉</button>
                <table><thead><tr><th colspan="2">交易序號</th><th>金額</th></tr></thead>
                <tbody><tr><td colspan="2">26092219000625189</td><td>NT$ 14,400<br>含運費：NT$ 0</td></tr></tbody></table>
                <table><thead><tr><th><span>付款人</span></th><th>聯絡電話</th></tr></thead>
                <tbody><tr><td>測試付款人</td><td>0912345678</td></tr></tbody>
                <thead><tr><th>收貨人</th><th>聯絡電話</th></tr></thead>
                <tbody><tr><td>測試收貨人</td><td>0999999999</td></tr></tbody></table>
                </div>
            ''')
            rows = [payment.as_row() for payment in _read_current_page(page)]
            assert rows == [["TEST商店", "26092219000625189\nTEST訂單", "清潔服務",
                             "信用卡", "付款成功", "測試付款人", "0912345678", 14400]]
            assert not page.locator('.modal').is_visible()
        finally:
            browser.close()
