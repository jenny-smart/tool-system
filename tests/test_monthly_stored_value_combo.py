"""月排程「儲值金結算＋儲值金預收」：同參數依序跑兩支（2026-11 Jenny）。"""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "toolapp.py").read_text(encoding="utf-8")


def _assigned(name):
    tree = ast.parse(SOURCE)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def test_combo_listed_and_runs_both_scripts_in_stored_value_folder():
    combo = _assigned("STORED_VALUE_COMBO")
    assert combo == "儲值金結算＋儲值金預收"
    assert _assigned("STORED_VALUE_COMBO_STEPS") == ("儲值金結算", "儲值金預收")
    assert combo in _assigned("STORED_VALUE_FUNCTIONS")  # 放財務的儲值金根目錄
    assert f'"{combo}",' in SOURCE.split('"monthly_scheduler": [', 1)[1].split("]", 1)[0]  # 月排程選單有這項
