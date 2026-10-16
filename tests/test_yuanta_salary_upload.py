import io
import inspect
import json

import pytest
from openpyxl import Workbook

from tools.bank_statement.yuanta_salary_upload import (
    _normalized_text,
    upload_salary_files,
    xlsx_total,
)
from tools.local_agent import build_yuanta_salary_upload


def _workbook_bytes(*amounts):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "元大"
    for offset, amount in enumerate(amounts, start=3):
        worksheet.cell(offset, 4, amount)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_xlsx_total_reads_column_d_from_row_3():
    assert xlsx_total(_workbook_bytes(100, 250, "1,200")) == 1550


def test_yuanta_option_text_ignores_bank_spacing():
    assert _normalized_text("臺幣薪轉格式 Excel") == "臺幣薪轉格式Excel"


def test_yuanta_upload_uses_bank_page_button_order():
    source = inspect.getsource(upload_salary_files)
    steps = [
        '_click_text(page, "確定")',
        '_select_option_containing(page, payment_account)',
        '_select_option_containing(page, item.summary, "摘要")',
        '_click_text(page, "確定匯入")',
        '_click_text(page, "送審")',
        '_click_text(page, "確認送審")',
    ]
    positions = [source.index(step) for step in steps]
    assert positions == sorted(positions)


def test_agent_command_preserves_preview_and_selected_steps():
    expected = [{"name": "202610-1元大承攬費-台北.xlsx", "amount": 12345}]
    command = build_yuanta_salary_upload({
        "area": "台北",
        "period": "202610-1",
        "include_contract": True,
        "include_internal": True,
        "transfer_fubon": True,
        "expected_files": expected,
    })

    assert "--internal" in command
    assert "--fubon-transfer" in command
    assert json.loads(command[command.index("--expected-plan") + 1]) == expected


def test_agent_command_requires_at_least_one_source():
    with pytest.raises(ValueError, match="至少選擇"):
        build_yuanta_salary_upload({
            "area": "台北",
            "period": "202610-1",
            "include_contract": False,
            "include_internal": False,
        })
