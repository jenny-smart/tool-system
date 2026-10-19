"""orders.login／memo.login 改用共用的 lemon_backend_login（與 tool-system 正本同內容）。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import lemon_backend_login as lbl

ROOT = Path(__file__).resolve().parent


def _session(final_url):
    session = MagicMock()
    session.get.return_value = SimpleNamespace(text='<input name="_token" value="t">', url="x/login", raise_for_status=lambda: None)
    session.post.return_value = SimpleNamespace(text="", url=final_url, raise_for_status=lambda: None)
    return session


def test_orders_login_keeps_true_false_contract():
    import orders
    lbl._ACTIVE.clear()
    assert orders.login(_session("https://backend.lemonclean.com.tw/purchase"), "a@b.c", "pw") is True
    assert orders.login(_session("https://backend.lemonclean.com.tw/login"), "a@b.c", "pw") is False
    broken = _session("x")
    broken.get.side_effect = OSError("offline")
    assert orders.login(broken, "a@b.c", "pw") is False


def test_login_code_is_not_duplicated():
    for name in ("orders.py", "memo_system/memo.py", "member_phone_lookup.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "from lemon_backend_login import login as backend_login" in source, name
