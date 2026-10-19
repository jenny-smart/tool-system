"""富邦：沿用登入前到「我的存款」核對是這區帳號；不是就先登出再登入；轉帳做完一律登出。"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

agent = pytest.importorskip("tools.bank_statement.fubon_agent")
from tools.bank_statement.accounts import BankAccount  # noqa: E402

TAIPEI = BankAccount(bank="fubon", area="台北", login_id="x", user_code="y", password="z", bank_account="00709120003320")
TAICHUNG = BankAccount(bank="fubon", area="台中", login_id="x", user_code="y", password="z", bank_account="00200102520512")


def _page(text):
    page = MagicMock()
    page.frames = []
    page.locator.return_value.inner_text.return_value = text
    return page


def test_page_shows_full_account_number():
    page = _page("帳號\n00709120003320 請勿輸入個資 活期存款 松高分行\n82120000041950")
    assert agent.page_shows_account(page, TAIPEI)
    assert not agent.page_shows_account(page, TAICHUNG)


def test_missing_bank_account_setting_stops():
    with pytest.raises(RuntimeError, match="bank_account"):
        agent.page_shows_account(_page(""), BankAccount("fubon", "台中", "x", "y", "z", ""))


def test_reuses_session_when_logged_in_as_same_area(monkeypatch):
    page = _page("")
    monkeypatch.setattr(agent, "existing_page", lambda context: page)
    monkeypatch.setattr(agent, "is_fubon_logged_in", lambda p: True)
    monkeypatch.setattr(agent, "verify_logged_in_account", lambda c, p, a: (True, p))
    logout = MagicMock()
    monkeypatch.setattr(agent, "logout_and_close", logout)
    assert agent.ensure_login(MagicMock(), TAICHUNG) is page
    logout.assert_not_called()


def test_logs_out_and_logs_in_again_when_other_area_is_logged_in(monkeypatch):
    old, new = _page(""), _page("")
    context = MagicMock()
    context.new_page.return_value = new
    monkeypatch.setattr(agent, "existing_page", lambda c: old)
    monkeypatch.setattr(agent, "is_fubon_logged_in", lambda p: p is old)
    results = iter([(False, old), (True, new)])
    monkeypatch.setattr(agent, "verify_logged_in_account", lambda c, p, a: next(results))
    logout = MagicMock()
    monkeypatch.setattr(agent, "logout_and_close", logout)
    monkeypatch.setattr(agent, "fill_fubon", lambda p, a: p)
    monkeypatch.setattr(agent, "wait_fubon_login", lambda c, p: p)
    assert agent.ensure_login(context, TAICHUNG) is new
    logout.assert_called_once_with(context, old)


def test_stops_when_login_is_not_this_area(monkeypatch):
    page = _page("")
    context = MagicMock()
    context.new_page.return_value = page
    monkeypatch.setattr(agent, "existing_page", lambda c: None)
    monkeypatch.setattr(agent, "is_fubon_logged_in", lambda p: False)
    monkeypatch.setattr(agent, "verify_logged_in_account", lambda c, p, a: (False, p))
    monkeypatch.setattr(agent, "fill_fubon", lambda p, a: p)
    monkeypatch.setattr(agent, "wait_fubon_login", lambda c, p: p)
    with pytest.raises(RuntimeError, match="找不到台中帳號"):
        agent.ensure_login(context, TAICHUNG)


def test_every_transfer_logs_out_when_done():
    root = Path(__file__).resolve().parents[1] / "tools" / "bank_statement"
    for name in ("fubon_atm_refund", "fubon_payment_request", "fubon_deposit_refund",
                 "fubon_supply_purchase", "fubon_fixed_expense_transfer"):
        assert "cleanup.callback(logout_quietly, context, page)" in (root / f"{name}.py").read_text(encoding="utf-8"), name


def test_verify_uses_account_row_finder(monkeypatch):
    page = _page("")
    monkeypatch.setattr(agent, "open_my_deposits", lambda c, p: p)
    monkeypatch.setattr(agent, "find_account_row", lambda p, a, timeout_ms: object())
    assert agent.verify_logged_in_account(MagicMock(), page, TAIPEI) == (True, page)
    monkeypatch.setattr(agent, "find_account_row", lambda p, a, timeout_ms: None)
    assert agent.verify_logged_in_account(MagicMock(), _page("82120000041950"), TAIPEI)[0] is False


def test_visible_account_numbers_for_error_message():
    assert agent.visible_account_numbers(_page("00709120003320 松高 82120000041950 4,385,453.00")) == [
        "00709120003320", "82120000041950"]
