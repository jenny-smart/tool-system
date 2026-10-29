"""用目前的 Python 編譯 repo 內所有 .py，確認沒有語法錯誤（Mac Agent 用 Python 3.9）。

用法：python3 -m tools.syntax_check
有錯誤時印出檔案與行號，exit code 1。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def tracked_python_files(root: Path = PROJECT_ROOT) -> list[Path]:
    output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "*.py"],
        check=True, stdout=subprocess.PIPE,
    ).stdout.decode("utf-8")
    return [root / name for name in output.split("\0") if name]


def find_syntax_errors(files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        if not path.exists():
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            errors.append(f"{path}:{exc.lineno}: {exc.msg}")
    return errors


def main() -> int:
    files = tracked_python_files()
    errors = find_syntax_errors(files)
    version = ".".join(str(part) for part in sys.version_info[:3])
    if errors:
        print(f"❌ Python {version} 語法檢查失敗（{len(errors)} 個檔案）：")
        for line in errors:
            print(f"  {line}")
        return 1
    print(f"✅ Python {version} 語法檢查通過（{len(files)} 個檔案）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
