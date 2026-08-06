from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.local_agent_queue import (
    LOG_SHEET_NAME,
    append_task_log,
    claim_next_task,
    default_agent_id,
    now_text,
    update_task,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CommandBuilder = Callable[[dict[str, Any]], list[str]]
ACTION_HANDLERS: dict[str, CommandBuilder] = {}


def register_action(action: str, builder: CommandBuilder) -> None:
    if not action.strip():
        raise ValueError("action 不可為空")
    ACTION_HANDLERS[action] = builder


def _common_invoice_args(params: dict[str, Any]) -> tuple[str, str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    return area, cdp_url


def build_cetustek_login(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    command = [
        sys.executable,
        "-m",
        "tools.invoice_center.cetustek_login_only",
        "--cdp-url",
        cdp_url,
    ]
    if area and area != "全區":
        command.extend(["--area", area])
    return command


def build_cetustek_download(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    month = str(params.get("month") or "").strip()
    start_date = str(params.get("start_date") or "").strip()
    end_date = str(params.get("end_date") or "").strip()
    if not month and not (start_date and end_date):
        raise ValueError("下載任務必須提供月份或日期區間")
    command = [
        sys.executable,
        "-m",
        "tools.invoice_center.ei_export_all",
        "--cdp-url",
        cdp_url,
        "--format",
        str(params.get("format") or "csv"),
    ]
    if month:
        command.append(month)
    else:
        command.extend(["--start-date", start_date, "--end-date", end_date])
    if bool(params.get("detail")):
        command.append("--detail")
    if area and area != "全區":
        command.extend(["--area", area])
    return command


register_action("cetustek.login", build_cetustek_login)
register_action("cetustek.download", build_cetustek_download)


def parse_params(task: dict[str, str]) -> dict[str, Any]:
    try:
        data = json.loads(task.get("params_json") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("params_json 格式錯誤") from exc
    if not isinstance(data, dict):
        raise ValueError("params_json 必須是物件")
    return data


def command_for(task: dict[str, str]) -> list[str]:
    action = task.get("action", "")
    builder = ACTION_HANDLERS.get(action)
    if builder is None:
        raise ValueError(f"不支援的任務：{action}")
    return builder(parse_params(task))


def run_task(task: dict[str, str], *, service: Any, spreadsheet_id: str) -> int:
    row_number = int(task["_row"])
    task_id = task["task_id"]
    preview = ""
    pending_log = ""
    next_seq = 1

    def record(text: str) -> None:
        nonlocal preview, pending_log
        line = text if text.endswith("\n") else text + "\n"
        preview = (preview + line)[-45000:]
        pending_log += line

    def flush_full_log() -> None:
        nonlocal pending_log, next_seq
        if pending_log:
            next_seq = append_task_log(
                task_id,
                pending_log,
                start_seq=next_seq,
                service=service,
                spreadsheet_id=spreadsheet_id,
            )
            pending_log = ""

    try:
        command = command_for(task)
        record(f"[{now_text()}] START {task['action']}")
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        last_upload = 0.0
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            print(line, flush=True)
            record(line)
            if time.monotonic() - last_upload >= 3:
                flush_full_log()
                update_task(
                    row_number,
                    {"log": preview, "message": line[-500:] or "執行中"},
                    service=service,
                    spreadsheet_id=spreadsheet_id,
                )
                last_upload = time.monotonic()
        return_code = process.wait()
        status = "completed" if return_code == 0 else "failed"
        message = "執行完成" if return_code == 0 else f"執行失敗（exit {return_code}）"
        record(f"[{now_text()}] {status.upper()} {message}")
        flush_full_log()
        update_task(
            row_number,
            {
                "status": status,
                "finished_at": now_text(),
                "message": message,
                "log": preview,
                "result_json": json.dumps(
                    {"exit_code": return_code, "log_sheet": LOG_SHEET_NAME},
                    ensure_ascii=False,
                ),
            },
            service=service,
            spreadsheet_id=spreadsheet_id,
        )
        return return_code
    except Exception as exc:
        record(f"[{now_text()}] FAILED {type(exc).__name__}: {exc}")
        record(traceback.format_exc())
        flush_full_log()
        update_task(
            row_number,
            {
                "status": "failed",
                "finished_at": now_text(),
                "message": str(exc)[:500],
                "log": preview,
                "result_json": json.dumps(
                    {"error": str(exc), "log_sheet": LOG_SHEET_NAME},
                    ensure_ascii=False,
                ),
            },
            service=service,
            spreadsheet_id=spreadsheet_id,
        )
        print(preview, file=sys.stderr, flush=True)
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tool System 共用本機 Agent")
    parser.add_argument("--once", action="store_true", help="只處理一筆任務後結束")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--agent-id", default=default_agent_id())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    print(
        f"Local Agent ready: {args.agent_id}; actions={','.join(sorted(ACTION_HANDLERS))}",
        flush=True,
    )
    while True:
        task = claim_next_task(
            agent_id=args.agent_id,
            service=service,
            spreadsheet_id=spreadsheet_id,
        )
        if task:
            run_task(task, service=service, spreadsheet_id=spreadsheet_id)
            if args.once:
                return 0
        elif args.once:
            print("No pending task", flush=True)
            return 0
        else:
            time.sleep(max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
