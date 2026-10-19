"""檸檬後台共用登入：取 _token → 送帳密 → 確認沒被退回登入頁；程式結束時登出。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.common import lemon_backend_login as lbl

LOGIN = "https://backend.lemonclean.com.tw/login"


def _session(final_url):
    session = MagicMock()
    session.get.return_value = SimpleNamespace(text='<input name="_token" value="abc">', url=LOGIN, raise_for_status=lambda: None)
    session.post.return_value = SimpleNamespace(text="", url=final_url, raise_for_status=lambda: None)
    return session


def test_login_posts_token_and_registers_logout():
    lbl._ACTIVE.clear()
    session = _session("https://backend.lemonclean.com.tw/purchase")
    lbl.login(session, "a@b.c", "pw", login_url=LOGIN, headers={"User-Agent": "x"})
    assert session.post.call_args.kwargs["data"] == {"_token": "abc", "email": "a@b.c", "password": "pw"}
    assert lbl._ACTIVE and lbl._ACTIVE[0][1] == "https://backend.lemonclean.com.tw/logout"
    lbl._logout_all()
    session.get.assert_called_with("https://backend.lemonclean.com.tw/logout", headers={"User-Agent": "x"},
                                   allow_redirects=True, timeout=10)
    assert lbl._ACTIVE == []


def test_login_failure_raises():
    with pytest.raises(RuntimeError, match="登入失敗"):
        lbl.login(_session(LOGIN), "a@b.c", "pw", login_url=LOGIN)
    with pytest.raises(RuntimeError, match="未設定"):
        lbl.login(_session(LOGIN), "", "pw", login_url=LOGIN)


def test_logout_removes_session_and_never_raises():
    lbl._ACTIVE.clear()
    session = _session("https://backend.lemonclean.com.tw/purchase")
    lbl.login(session, "a@b.c", "pw", login_url=LOGIN)
    session.get.side_effect = OSError("offline")
    lbl.logout(session, LOGIN)
    assert lbl._ACTIVE == []


def test_all_tool_system_backend_logins_use_shared_flow():
    root = Path(__file__).resolve().parents[1]
    files = [f"tools/scheduled_daily/{n}.py" for n in ("staff_schedule", "performance_report", "staff_info",
                                                        "orders_report", "performance_report_runner", "schedule_report")]
    files += [f"tools/scheduled_monthly/{n}.py" for n in ("refund_report", "stored_value_settlement", "prepaid_report",
                                                          "stored_value_prepaid", "half_month_orders")]
    files += ["services/backend_auth.py", "tools/lemon_backend/auth.py", "tools/service_management/stored_value.py"]
    for name in files:
        source = (root / name).read_text(encoding="utf-8")
        assert "lemon_backend_login import login as backend_login" in source, name
        assert 'find("input", {"name": "_token"})' not in source, name
