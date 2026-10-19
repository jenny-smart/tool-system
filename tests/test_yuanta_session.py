"""元大：沿用登入前核對儀表板上的統編是這區；不是就先登出再登入；薪資上傳做完一律登出。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

ny = pytest.importorskip("tools.bank_statement.native_yuanta")

TAIPEI = SimpleNamespace(area="台北", login_id="42627791", user_code="u", password="p")
TAICHUNG = SimpleNamespace(area="台中", login_id="12345678", user_code="u", password="p")
DASHBOARD = "儀表板 帳戶查詢 登出\nJenny(lemon25)\n42627791 檸檬專業清潔有限公司"


def test_company_id_on_dashboard(monkeypatch):
    monkeypatch.setattr(ny, "_all_text", lambda: DASHBOARD)
    assert ny.is_expected_company(TAIPEI, timeout=0.1)
    assert ny.logged_in_company_ids() == ["42627791"]
    monkeypatch.setattr(ny, "_click_text", lambda *a, **k: None)
    assert not ny.is_expected_company(TAICHUNG, timeout=0.1)


def test_missing_login_id_stops():
    with pytest.raises(RuntimeError, match="login_id"):
        ny.is_expected_company(SimpleNamespace(area="台中", login_id=""), timeout=0.1)


def test_reuses_when_same_company(monkeypatch):
    monkeypatch.setattr(ny, "page_state", lambda: {"logged_in": True})
    monkeypatch.setattr(ny, "is_expected_company", lambda account, timeout=10.0: True)
    called = []
    monkeypatch.setattr(ny, "logout_yuanta", lambda: called.append("logout"))
    monkeypatch.setattr(ny, "fill_yuanta_login", lambda account: called.append("fill"))
    assert ny.wait_for_yuanta_login(TAIPEI) == {"logged_in": True}
    assert called == []


def test_other_company_logs_out_then_logs_in(monkeypatch):
    states = iter([{"logged_in": True}, {"logged_in": True}, {"logged_in": True}])
    monkeypatch.setattr(ny, "page_state", lambda: next(states))
    checks = iter([False, True])
    monkeypatch.setattr(ny, "is_expected_company", lambda account, timeout=10.0: next(checks))
    monkeypatch.setattr(ny, "logged_in_company_ids", lambda: ["42627791"])
    called = []
    monkeypatch.setattr(ny, "logout_yuanta", lambda: called.append("logout"))
    monkeypatch.setattr(ny, "_goto_login_page", lambda: called.append("goto"))
    monkeypatch.setattr(ny, "fill_yuanta_login", lambda account: called.append("fill"))
    monkeypatch.setattr(ny.time, "sleep", lambda s: None)
    ny.wait_for_yuanta_login(TAICHUNG, timeout=5)
    assert called == ["logout", "goto", "fill"]


def test_stops_when_login_is_other_company(monkeypatch):
    states = iter([{"prelogin": True}, {"logged_in": True}, {"logged_in": True}])
    monkeypatch.setattr(ny, "page_state", lambda: next(states))
    monkeypatch.setattr(ny, "is_expected_company", lambda account, timeout=10.0: False)
    monkeypatch.setattr(ny, "logged_in_company_ids", lambda: ["42627791"])
    monkeypatch.setattr(ny, "fill_yuanta_login", lambda account: None)
    monkeypatch.setattr(ny.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="不是台中"):
        ny.wait_for_yuanta_login(TAICHUNG, timeout=5)


def test_salary_upload_logs_out_when_done():
    source = (Path(__file__).resolve().parents[1] / "tools/bank_statement/yuanta_salary_upload.py").read_text(encoding="utf-8")
    assert "logout_yuanta_quietly()" in source
    assert "cleanup.callback(fubon_logout_quietly, context, page)" in source
