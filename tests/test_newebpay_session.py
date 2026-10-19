"""藍新：頁首「lemon25您好」的會員帳號要是這區（帳密檔 account）才沿用；不是就先登出再登入。"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

reports = pytest.importorskip("tools.newebpay.download_reports")

TAIPEI = reports.AreaAccount(area="台北", company_id="42627791", account="lemon25", password="x")
TAICHUNG = reports.AreaAccount(area="台中", company_id="12345678", account="lemon26", password="x")
HEADER = "lemon25您好，您管理的公司為 檸檬專業清潔有限公司 | 離登出時間還有：589秒"


def _page(text, url="https://www.newebpay.com/sale/Sell_center/search_transaction"):
    page = MagicMock()
    page.url = url
    page.locator.return_value.inner_text.return_value = text
    return page


def test_reads_member_and_compares_with_area():
    page = _page(HEADER)
    assert reports.logged_in_member(page, 100) == "lemon25"
    assert reports.is_expected_member(page, TAIPEI)
    assert not reports.is_expected_member(page, TAICHUNG)


def test_reuses_when_same_member(monkeypatch):
    page = _page(HEADER)
    fill = MagicMock()
    monkeypatch.setattr(reports, "fill_enterprise_login", fill)
    assert reports.login(page, TAIPEI) == []
    fill.assert_not_called()


def test_other_member_logs_out_first(monkeypatch):
    page = _page(HEADER)
    calls = []
    monkeypatch.setattr(reports, "logout", lambda p, area: calls.append("logout"))
    def fill(p, account):
        calls.append("fill")
        raise RuntimeError("stop here")
    monkeypatch.setattr(reports, "fill_enterprise_login", fill)
    with pytest.raises(RuntimeError, match="stop here"):
        reports.login(page, TAICHUNG)
    assert calls == ["logout", "fill"]


def test_after_login_other_member_stops():
    with pytest.raises(RuntimeError, match="不是台中"):
        reports.check_member_after_login(_page(HEADER), TAICHUNG)


def test_express_payment_check_logs_out_when_done():
    source = (Path(__file__).resolve().parents[1] / "tools/newebpay/express_payment_check.py").read_text(encoding="utf-8")
    assert "logout(page, area)" in source
