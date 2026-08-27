from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tools.service_management import stored_value


def _set_backend_accounts(monkeypatch, accounts):
    backend = ModuleType("services.backend")
    backend.ACCOUNTS = accounts
    monkeypatch.setitem(sys.modules, "services.backend", backend)


def _clear_taipei_env(monkeypatch):
    monkeypatch.delenv("TAIPEI_EMAIL", raising=False)
    monkeypatch.delenv("TAIPEI_PASSWORD", raising=False)


def test_area_credentials_use_environment_first(monkeypatch):
    monkeypatch.setenv("TAIPEI_EMAIL", "env@example.com")
    monkeypatch.setenv("TAIPEI_PASSWORD", "env-password")
    _set_backend_accounts(
        monkeypatch,
        {"台北": {"email": "secret@example.com", "password": "secret-password"}},
    )

    assert stored_value.get_area_credentials("台北") == (
        "env@example.com",
        "env-password",
    )


def test_area_credentials_fall_back_to_shared_backend_settings(monkeypatch):
    _clear_taipei_env(monkeypatch)
    _set_backend_accounts(
        monkeypatch,
        {"台北": {"email": "secret@example.com", "password": "secret-password"}},
    )

    assert stored_value.get_area_credentials("台北") == (
        "secret@example.com",
        "secret-password",
    )


def test_area_credentials_report_missing_shared_settings(monkeypatch):
    _clear_taipei_env(monkeypatch)
    _set_backend_accounts(monkeypatch, {})

    with pytest.raises(EnvironmentError, match="客服系統後台帳密設定"):
        stored_value.get_area_credentials("台北")
