"""服務異動／ATM 統一使用 orders-system 版（tools/orders_system/memo_system）。"""
import importlib


def test_tool_memo_modules_are_orders_modules():
    for name in ("change_order", "atm", "memo", "shift", "ui", "env"):
        tool = importlib.import_module(f"tools.memo_system.{name}")
        orders = importlib.import_module(f"memo_system.{name}")
        assert tool is orders
        assert tool.__file__.replace("\\\\", "/").endswith(f"tools/orders_system/memo_system/{name}.py")


def test_tool_host_config_registered(monkeypatch):
    import tools.orders_system  # noqa: F401
    from memo_system import atm, host
    from tools.memo_system import host_config

    assert host.region_sheet is host_config.region_sheet
    assert host.service_account is host_config.service_account
    monkeypatch.setattr(atm, "_secret_text", lambda key: "")
    monkeypatch.setattr(atm, "_year_root_id", lambda kind, region, sid: sid)
    assert atm._atm_sheet_config("台北")["spreadsheet_id"] == host_config._ATM_SHEETS["台北"]["spreadsheet_id"]


def test_scheduled_jobs_import():
    importlib.import_module("tools.orders_system.change_order_daily_check")
    importlib.import_module("tools.orders_system.atm_reconcile")


def test_year_roots_uses_oauth_drive_in_tool_system():
    import tools.orders_system  # noqa: F401
    import year_roots
    from tools.common.google_auth import get_drive_service

    assert year_roots.drive_factory is get_drive_service
