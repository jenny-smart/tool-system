import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMON_PATH = ROOT / "tools" / "bank_statement" / "fubon_transfer_common.py"
ATM_PATH = ROOT / "tools" / "bank_statement" / "fubon_atm_refund.py"


def _load_time_parser():
    tree = ast.parse(COMMON_PATH.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.FunctionDef))
        and (
            isinstance(node, ast.FunctionDef)
            and node.name == "extract_completed_transfer_time"
            or isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in {"TRANSFER_SUCCESS_MARKERS", "TRANSFER_TIME_LABELS"}
                for target in node.targets
            )
        )
    ]
    namespace = {"re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(COMMON_PATH), "exec"), namespace)
    return namespace["extract_completed_transfer_time"]


def test_extract_completed_transfer_time_requires_success_and_named_time() -> None:
    parse = _load_time_parser()

    assert parse("交易成功\n交易時間\n2026/08/25 18:06:07") == "2026-08-25 18:06:07"
    assert parse("轉帳成功\n交易時間 115/08/25 18:06") == "2026-08-25 18:06:00"
    assert parse("交易成功\n2026/08/25 18:06:07") is None
    assert parse("交易時間\n2026/08/25 18:06:07") is None


def test_atm_refund_updates_after_completion_then_downloads_statement() -> None:
    source = ATM_PATH.read_text(encoding="utf-8")

    wait_index = source.index("require_completed_at=True")
    update_index = source.index("worksheet.batch_update(")
    download_index = source.index("page = run_download(")

    assert wait_index < update_index < download_index
    assert 'f"AC{item[\'sheet_row\']}"' in source
    assert '"values": [["已退款"]]' in source
