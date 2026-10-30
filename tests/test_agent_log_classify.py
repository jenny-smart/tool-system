"""本機 Agent Log 圖示：「失敗 0 筆」是統計，不是錯誤。"""
import ast
import re
from pathlib import Path


def _classifier():
    tree = ast.parse(Path("toolapp.py").read_text(encoding="utf-8"))
    wanted = {"_ZERO_FAILURES_RE", "_classify_agent_log_line"}
    nodes = [node for node in tree.body
             if (isinstance(node, ast.FunctionDef) and node.name in wanted)
             or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") in wanted for t in node.targets))]
    namespace = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "toolapp.py", "exec"), namespace)
    return namespace["_classify_agent_log_line"]


def test_zero_failures_summary_is_success():
    classify = _classifier()
    assert classify("【台北】回寫 1 筆，失敗 0 筆") == "success"
    assert classify("【桃園】回寫 0 筆，失敗0筆") == "success"


def test_real_failures_are_still_errors():
    classify = _classifier()
    assert classify("【桃園】回寫 0 筆，失敗 1 筆") == "error"
    assert classify("【台北】回寫 2 筆，失敗 10 筆") == "error"
    assert classify("❌ 【台北】開立發票失敗（exit 1）") == "error"
    assert classify("===== 【台北】回寫系統 =====") == "info"
