"""本機 Agent：在 Mac 跳出提醒視窗（排程盤點完請你確認及執行）。

2026-11-06 Jenny：【整合執行】清潔異動盤點、富邦內部請款盤點完要跳視窗提醒去確認及執行。
視窗按「前往執行」會用瀏覽器開 Tools App 並直接選好該功能（網址：Mac secrets.toml／環境變數 TOOLS_APP_URL）。
視窗另外開一個程序顯示，Agent 不必等你按（不會卡住後面的工作）；1 小時沒按會自動關閉。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_MESSAGE = 1200


def tools_app_url() -> str:
    url = os.environ.get("TOOLS_APP_URL", "").strip()
    if url:
        return url
    try:
        import tomllib

        for path in (PROJECT_ROOT / ".streamlit" / "secrets.toml", Path.home() / ".streamlit" / "secrets.toml"):
            if path.exists():
                url = str(tomllib.loads(path.read_text(encoding="utf-8")).get("TOOLS_APP_URL", "")).strip()
                if url:
                    return url
    except Exception:
        pass
    return ""


def deep_link(base: str, system: str = "", function: str = "") -> str:
    if not base:
        return ""
    query = {k: v for k, v in (("system", system), ("fn", function)) if v}
    return base.rstrip("/") + ("/?" + urlencode(query) if query else "")


def _applescript_text(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_script(title: str, message: str, url: str) -> str:
    message = message if len(message) <= MAX_MESSAGE else message[:MAX_MESSAGE] + "\n…（完整內容請看 Tools App）"
    buttons = '{"稍後", "前往執行"}' if url else '{"知道了"}'
    default = '"前往執行"' if url else '"知道了"'
    lines = [
        "tell application \"System Events\" to activate",
        f"set r to button returned of (display dialog {_applescript_text(message)} with title {_applescript_text(title)}"
        f" buttons {buttons} default button {default} with icon caution giving up after 3600)",
    ]
    if url:
        lines.append(f'if r is "前往執行" then do shell script "open " & quoted form of {_applescript_text(url)}')
    return "\n".join(lines)


def show(title: str, message: str, system: str = "", function: str = "") -> int:
    url = deep_link(tools_app_url(), system, function)
    script = build_script(title, message, url)
    print(f"跳出提醒視窗：{title}", flush=True)
    if not url:
        print("（沒有設定 TOOLS_APP_URL，視窗只有「知道了」，不會開 Tools App）", flush=True)
    if sys.platform != "darwin":
        print(message, flush=True)
        return 0
    subprocess.Popen(["osascript", "-e", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mac 提醒視窗")
    parser.add_argument("--title", required=True)
    parser.add_argument("--message", default="")
    parser.add_argument("--system", default="")
    parser.add_argument("--function", default="")
    args = parser.parse_args(argv)
    return show(args.title, args.message, args.system, args.function)


if __name__ == "__main__":
    sys.exit(main())
