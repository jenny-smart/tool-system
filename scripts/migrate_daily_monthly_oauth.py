from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GOOGLE_AUTH = '''from __future__ import annotations

import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_google_credentials() -> Credentials:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()

    missing = [
        name
        for name, value in [
            ("GOOGLE_OAUTH_CLIENT_ID", client_id),
            ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
            ("GOOGLE_OAUTH_REFRESH_TOKEN", refresh_token),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError("缺少 Google OAuth 設定：" + ", ".join(missing))

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def get_drive_service():
    return build(
        "drive",
        "v3",
        credentials=get_google_credentials(),
        cache_discovery=False,
    )


def get_sheets_service():
    return build(
        "sheets",
        "v4",
        credentials=get_google_credentials(),
        cache_discovery=False,
    )
'''


def remove_function_defs(text: str, names: set[str]) -> tuple[str, set[str]]:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    ranges: list[tuple[int, int]] = []
    removed: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            ranges.append((node.lineno - 1, node.end_lineno or node.lineno))
            removed.add(node.name)

    for start, end in sorted(ranges, reverse=True):
        del lines[start:end]
        while start < len(lines) and lines[start].strip() == "" and start > 0 and lines[start - 1].strip() == "":
            del lines[start]

    return "".join(lines), removed


def remove_service_account_imports(text: str) -> str:
    patterns = [
        r"^from google\.oauth2 import service_account\s*\n",
        r"^from google\.oauth2\.service_account import Credentials\s*\n",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.MULTILINE)
    return text


def add_import(text: str, import_line: str) -> str:
    if import_line in text:
        return text

    lines = text.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("from __future__ import"):
            insert_at = i + 1
            continue
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
            continue
        if insert_at and line.strip() == "":
            continue
        if insert_at:
            break

    lines.insert(insert_at, import_line + "\n")
    return "".join(lines)


def migrate_drive_file(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    text, removed = remove_function_defs(original, {"get_service_account_info", "get_drive_service"})

    if "get_drive_service" not in removed:
        return False

    text = remove_service_account_imports(text)
    text = add_import(text, "from tools.common.google_auth import get_drive_service")

    path.write_text(text, encoding="utf-8")
    print(f"updated drive auth: {path.relative_to(ROOT)}")
    return True


def migrate_log_to_sheet(path: Path) -> None:
    original = path.read_text(encoding="utf-8")
    text, removed = remove_function_defs(original, {"get_service_account_info", "get_sheets_service"})
    if "get_sheets_service" not in removed:
        raise RuntimeError("log_to_sheet.py 找不到 get_sheets_service()")

    text = remove_service_account_imports(text)
    text = add_import(text, "from tools.common.google_auth import get_sheets_service")
    path.write_text(text, encoding="utf-8")
    print("updated sheets auth: tools/common/log_to_sheet.py")


def replace_workflow_auth(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    replaced = False

    for line in lines:
        match = re.match(r"^(\s*)GOOGLE_SERVICE_ACCOUNT:\s*.*$", line)
        if match:
            indent = match.group(1)
            out.extend([
                f"{indent}GOOGLE_OAUTH_CLIENT_ID: ${{{{ secrets.GOOGLE_OAUTH_CLIENT_ID }}}}",
                f"{indent}GOOGLE_OAUTH_CLIENT_SECRET: ${{{{ secrets.GOOGLE_OAUTH_CLIENT_SECRET }}}}",
                f"{indent}GOOGLE_OAUTH_REFRESH_TOKEN: ${{{{ secrets.GOOGLE_OAUTH_REFRESH_TOKEN }}}}",
            ])
            replaced = True
        else:
            out.append(line)

    if not replaced:
        raise RuntimeError(f"{path} 找不到 GOOGLE_SERVICE_ACCOUNT env")

    text = "\n".join(out) + "\n"

    if path.name == "scheduled_daily.yml":
        text = re.sub(
            r"DAILY_ROOT_FOLDER_ID:\s*\$\{\{\s*secrets\.DAILY_ROOT_FOLDER_ID\s*\}\}",
            'DAILY_ROOT_FOLDER_ID: "1SSWWXmdAuOCXup-MylhnaWe9Z-H9hUso"',
            text,
        )
    elif path.name == "scheduled_monthly.yml":
        text = re.sub(
            r"MONTHLY_ROOT_FOLDER_ID:\s*\$\{\{\s*secrets\.MONTHLY_ROOT_FOLDER_ID\s*\}\}",
            'MONTHLY_ROOT_FOLDER_ID: "1TnV_tpDnaSwPDDvjq8AtGL6uRC0OwGUK"',
            text,
        )

    path.write_text(text, encoding="utf-8")
    print(f"updated workflow: {path.relative_to(ROOT)}")


def main() -> None:
    common_auth = ROOT / "tools/common/google_auth.py"
    common_auth.write_text(GOOGLE_AUTH, encoding="utf-8")
    print("created tools/common/google_auth.py")

    changed = 0
    for folder in [ROOT / "tools/scheduled_daily", ROOT / "tools/scheduled_monthly"]:
        for path in sorted(folder.glob("*.py")):
            if migrate_drive_file(path):
                changed += 1

    migrate_log_to_sheet(ROOT / "tools/common/log_to_sheet.py")
    replace_workflow_auth(ROOT / ".github/workflows/scheduled_daily.yml")
    replace_workflow_auth(ROOT / ".github/workflows/scheduled_monthly.yml")

    if changed < 2:
        raise RuntimeError(f"只更新到 {changed} 個 Drive 程式，數量異常")

    print(f"OAuth migration complete; drive files updated: {changed}")


if __name__ == "__main__":
    main()
