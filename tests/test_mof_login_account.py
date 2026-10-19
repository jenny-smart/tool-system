"""財政部字軌取號：沿用登入前要確認是這一區的帳號，否則先登出（避免台北資料存成台中）。"""
import sys
import types

try:  # 測試環境沒裝 playwright 時給一個假的，只測帳號判斷
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright  # noqa: F401
except Exception:
    fake = types.ModuleType("playwright.sync_api")
    fake.__getattr__ = lambda name: object
    fake.sync_playwright = lambda: None
    sys.modules["playwright.sync_api"] = fake
    sys.modules.pop("tools.invoice_center.chrome_cdp", None)

from tools.invoice_center import mof_serial  # noqa: E402
from tools.invoice_center.mof_config import MOFCredentials  # noqa: E402

if getattr(sys.modules.get("playwright.sync_api"), "__getattr__", None) is not None:
    del sys.modules["playwright.sync_api"]  # 假模組只給上面的 import 用，不影響其他測試


class _Page:
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        page = self

        class L:
            def inner_text(self, timeout=None):
                return page.text
        return L()


TAIPEI = MOFCredentials(area="台北", ubn="42627791", account="taipei01")
TAICHUNG = MOFCredentials(area="台中", ubn="55667788", account="tc01")


def test_reads_logged_in_account_and_compares_with_area():
    page = _Page("營業人/扣繳單位\n帳號：42627791\n姓名：蔡珍妮")
    account = mof_serial.logged_in_account(page)
    assert account == "42627791"
    assert mof_serial.is_expected_account(account, TAIPEI)
    assert not mof_serial.is_expected_account(account, TAICHUNG)
    assert not mof_serial.is_expected_account("", TAIPEI)


def test_wrong_account_stops_before_download():
    import pytest

    with pytest.raises(RuntimeError, match="不是 台中"):
        mof_serial.ensure_expected_login(_Page("帳號：42627791"), TAICHUNG)


class _LogoutPage(_Page):
    """按「登出」的假頁面：記錄去過的網址；logged_out 決定登出後畫面還有沒有帳號。"""

    def __init__(self, logged_out=True, has_button=True):
        super().__init__("帳號：42627791")
        self.logged_out, self.has_button, self.visited, self.url = logged_out, has_button, [], ""

    def once(self, event, handler):
        pass

    def locator(self, selector):
        page = self

        class L:
            first = None

            def click(self, timeout=None):
                if not page.has_button:
                    raise TimeoutError("no button")
                page.visited.append("click:" + selector)
                if page.logged_out:
                    page.text = "登入"

            def inner_text(self, timeout=None):
                return page.text
        loc = L()
        loc.first = loc
        return loc

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, ms):
        pass

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url
        if url == mof_serial.MOF_LOGOUT_URL and self.logged_out:
            self.text = "登入"


def test_logout_clicks_logout_link_and_checks_account_gone():
    page = _LogoutPage()
    mof_serial.logout_mof(page)
    assert "logoutClient/logout" in page.visited[0]


def test_logout_falls_back_to_logout_url_without_button():
    page = _LogoutPage(has_button=False)
    mof_serial.logout_mof(page)
    assert mof_serial.MOF_LOGOUT_URL in page.visited


def test_logout_raises_when_still_logged_in():
    import pytest

    with pytest.raises(RuntimeError, match="仍是登入狀態"):
        mof_serial.logout_mof(_LogoutPage(logged_out=False))
