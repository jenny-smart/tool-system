import ast
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "toolapp.py"


def test_stream_subprocess_forces_taipei_timezone():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "stream_subprocess"
    )
    assignments = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "child_env"
            for target in node.targets
        )
    ]
    assert any(
        isinstance(node.value, ast.Constant)
        and node.value.value == "Asia/Taipei"
        for node in assignments
    )

    popen = next(
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    )
    env_keyword = next(keyword for keyword in popen.keywords if keyword.arg == "env")
    assert isinstance(env_keyword.value, ast.Name)
    assert env_keyword.value.id == "child_env"
