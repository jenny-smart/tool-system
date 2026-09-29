from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tools.invoice_center import ei_session as session


@pytest.fixture
def setup(monkeypatch):
    verify, portal_login, open_second = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(session, 'ensure_expected_ei_login', verify)
    monkeypatch.setattr(session, 'login_portal', portal_login)
    monkeypatch.setattr(session, 'open_second_login', open_second)
    return MagicMock(), MagicMock(), MagicMock(), {}, SimpleNamespace(label='台北', userid='42627791'), verify, portal_login, open_second


def test_existing_ei_wins_even_with_old_portal_tab(setup):
    context, portal, ei, accounts, credentials, verify, login, opening = setup
    assert session.prepare_download_session(context, portal, ei, accounts, credentials) == (portal, ei, True)
    verify.assert_called_once_with(ei, credentials)
    login.assert_not_called(); opening.assert_not_called()


def test_direct_second_layer_without_open_ei_tab(setup):
    context, portal, ei, accounts, credentials, verify, login, opening = setup
    result = session.prepare_download_session(context, portal, None, accounts, credentials)
    assert result[1] is context.new_page.return_value
    verify.assert_called_once_with(result[1], credentials)
    login.assert_not_called()


def test_existing_portal_link_before_login(setup):
    context, portal, ei, accounts, credentials, verify, login, opening = setup
    verify.side_effect = [RuntimeError('session expired'), True]
    result = session.prepare_download_session(context, portal, ei, accounts, credentials)
    assert result[1] is opening.return_value
    login.assert_not_called()
    opening.assert_called_once_with(context, portal)


def test_first_layer_only_after_both_attempts_fail(setup):
    context, portal, ei, accounts, credentials, verify, login, opening = setup
    events = []
    def check(*args):
        events.append('verify')
        if len(events) == 1:
            raise RuntimeError('expired')
    def enter(*args):
        events.append('open')
        if events.count('open') == 1:
            raise RuntimeError('no link')
        return ei
    verify.side_effect = check; opening.side_effect = enter
    login.side_effect = lambda *args: events.append('login')
    session.prepare_download_session(context, portal, ei, accounts, credentials)
    assert events == ['verify', 'open', 'login', 'open', 'verify']


def test_final_account_verification_failure_stops_download(setup):
    context, portal, ei, accounts, credentials, verify, login, opening = setup
    verify.side_effect = RuntimeError('wrong account')
    with pytest.raises(RuntimeError, match='wrong account'):
        session.prepare_download_session(context, portal, ei, accounts, credentials)
