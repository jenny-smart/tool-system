"""ATM 對帳只處理最近 7 天入帳、P／T 都空白的列（避免舊資料重發確認信）。"""
from datetime import date

from tools.orders_system import atm_reconcile as rec


def row(b="2026/10/15 09:00:00", j="LC1", p="", t=""):
    values = [""] * 20
    values[1], values[9], values[15], values[19] = b, j, p, t
    return values


def test_only_recent_unreconciled_rows():
    today = date(2026, 10, 18)
    assert rec.is_new_row(row(), today)
    assert not rec.is_new_row(row(b="2026/1/2 8:54:30"), today)       # 舊資料
    assert not rec.is_new_row(row(t="已更新系統"), today)
    assert not rec.is_new_row(row(p="2026/10/17 22:55:53"), today)
    assert not rec.is_new_row(row(j=""), today)
    assert not rec.is_new_row(row(b=""), today)
    assert rec.is_new_row(row(b="2026/10/11"), today)
    assert not rec.is_new_row(row(b="2026/10/10"), today)
