from tools.invoice_center.pending_charge import DISPLAY_COLUMNS
from tools.invoice_center.pending_charge_ui import (
    _invoice_action_label,
    _invoice_editor_disabled_columns,
)


def test_agent_activity_does_not_lock_invoice_picker() -> None:
    disabled = _invoice_editor_disabled_columns()

    assert disabled == list(DISPLAY_COLUMNS)
    assert "選取" not in disabled
    assert "變更發票" not in disabled
    assert "發票對象" not in disabled
    assert "發票方式" not in disabled


def test_running_agent_uses_queue_label() -> None:
    assert _invoice_action_label(False) == "▶ 執行"
    assert _invoice_action_label(True) == "➕ 加入等待佇列"
