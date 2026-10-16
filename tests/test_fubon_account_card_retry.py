from pathlib import Path


COMMON_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "bank_statement"
    / "fubon_transfer_common.py"
)


def test_account_card_uses_real_click_and_retries_when_ajax_does_not_update():
    source = COMMON_PATH.read_text(encoding="utf-8")

    assert "card.click(timeout=3_000)" in source
    assert "except AccountSelectionNotUpdated:" in source
    assert 'page.keyboard.press("Escape")' in source
    assert "attempt == max_attempts" in source
    assert "def transfer_page_stage" in source
    assert 'return "input"' in source
    assert 'return "confirmation"' in source
    assert 'return "completed"' in source
