from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
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
    write_agent_heartbeat,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CommandBuilder = Callable[[dict[str, Any]], list[str]]
ACTION_HANDLERS: dict[str, CommandBuilder] = {}
AGENT_VERSION = "1"


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


def build_newebpay_login(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("藍新登入請選擇單一區域")
    return [
        sys.executable,
        "-m",
        "tools.newebpay.login_only",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
    ]


def build_newebpay_download(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    start_date = str(params.get("start_date") or "").strip()
    end_date = str(params.get("end_date") or "").strip()
    if not start_date or not end_date:
        raise ValueError("藍新下載缺少開始日期或結束日期")
    command = [
        sys.executable,
        "-m",
        "tools.newebpay.download_reports",
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--cdp-url",
        cdp_url,
    ]
    if area and area != "全區":
        command.extend(["--area", area])
    return command


def build_newebpay_invoice_amounts(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    months = str(params.get("months") or "").strip()
    if not months:
        raise ValueError("藍新手續費發票金額缺少月份")
    output = PROJECT_ROOT / "outputs" / f"newebpay_invoice_amounts_{months}.csv"
    command = [
        sys.executable,
        "-m",
        "tools.newebpay.invoice_amounts",
        months,
        "--cdp-url",
        cdp_url,
        "--output",
        str(output),
    ]
    if area and area != "全區":
        command.extend(["--area", area])
    return command


def build_newebpay_refund_pending(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("藍新信用卡退款請選擇單一區域")
    selected_rows = params.get("selected_rows") or []
    if not selected_rows:
        raise ValueError("請先勾選要退款的資料")
    return [
        sys.executable, "-m", "tools.newebpay.refund_pending",
        "--area", area, "--cdp-url", cdp_url,
        "--rows", ",".join(str(int(row)) for row in selected_rows),
    ]


def build_fubon_login(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    if not area or area == "全區":
        raise ValueError("富邦登入請選擇單一區域")
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_agent",
        "login",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
    ]


def build_fubon_download(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    start_date = str(params.get("start_date") or "").strip()
    end_date = str(params.get("end_date") or "").strip()
    if not area or area == "全區":
        raise ValueError("富邦明細下載請選擇單一區域")
    if not start_date or not end_date:
        raise ValueError("富邦明細下載缺少開始日期或結束日期")
    output = PROJECT_ROOT / "outputs" / f"fubon_{area}_{start_date}_{end_date}.csv"
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_agent",
        "download",
        "--area",
        area,
        "--start",
        start_date,
        "--end",
        end_date,
        "--cdp-url",
        cdp_url,
        "--output",
        str(output),
    ]


def build_fubon_captcha(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    resume = str(params.get("resume") or "login").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    if not area or area == "全區":
        raise ValueError("富邦手機驗證請選擇單一區域")
    command = [
        sys.executable, "-m", "tools.bank_statement.fubon_mobile", "capture",
        "--area", area, "--resume", resume, "--cdp-url", cdp_url,
    ]
    if resume == "download":
        start_date = str(params.get("start_date") or "").strip()
        end_date = str(params.get("end_date") or "").strip()
        if not start_date or not end_date:
            raise ValueError("富邦明細下載缺少日期")
        output = PROJECT_ROOT / "outputs" / f"fubon_{area}_{start_date}_{end_date}.csv"
        command.extend(["--start", start_date, "--end", end_date, "--output", str(output)])
    return command


def build_fubon_verify(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    resume = str(params.get("resume") or "login").strip()
    token = str(params.get("session_token") or "").strip()
    captcha = str(params.get("captcha") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    if not area or not token or not captcha:
        raise ValueError("富邦驗證任務缺少區域、Session 或驗證碼")
    command = [
        sys.executable, "-m", "tools.bank_statement.fubon_mobile", "verify",
        "--area", area, "--resume", resume, "--session-token", token,
        "--captcha", captcha, "--cdp-url", cdp_url,
    ]
    if resume == "download":
        start_date = str(params.get("start_date") or "").strip()
        end_date = str(params.get("end_date") or "").strip()
        output = PROJECT_ROOT / "outputs" / f"fubon_{area}_{start_date}_{end_date}.csv"
        command.extend(["--start", start_date, "--end", end_date, "--output", str(output)])
    return command


def build_yuanta_login(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    if not area or area == "全區":
        raise ValueError("元大登入請選擇單一區域")
    return [sys.executable, "-m", "tools.bank_statement.yuanta_agent", "login", "--area", area]


def build_yuanta_download(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    start_date = str(params.get("start_date") or "").strip()
    end_date = str(params.get("end_date") or "").strip()
    if not area or area == "全區":
        raise ValueError("元大明細查詢請選擇單一區域")
    if not start_date or not end_date:
        raise ValueError("元大明細查詢缺少開始日期或結束日期")
    output = PROJECT_ROOT / "outputs" / f"yuanta_{area}_{start_date}_{end_date}.csv"
    return [
        sys.executable, "-m", "tools.bank_statement.yuanta_agent", "download",
        "--area", area, "--start", start_date, "--end", end_date,
        "--output", str(output),
    ]


def build_yuanta_salary_status(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    if not area or area == "全區":
        raise ValueError("元大薪資付款狀態請選擇單一區域")
    return [
        sys.executable, "-m", "tools.bank_statement.yuanta_salary_status",
        "--area", area, "--cdp-url", cdp_url,
    ]


register_action("cetustek.login", build_cetustek_login)
register_action("cetustek.download", build_cetustek_download)
register_action("newebpay.login", build_newebpay_login)
register_action("newebpay.download", build_newebpay_download)
register_action("newebpay.invoice_amounts", build_newebpay_invoice_amounts)
register_action("newebpay.refund_pending", build_newebpay_refund_pending)
register_action("fubon.login", build_fubon_login)
register_action("fubon.download", build_fubon_download)
register_action("fubon.captcha", build_fubon_captcha)
register_action("fubon.verify", build_fubon_verify)
register_action("yuanta.login", build_yuanta_login)
register_action("yuanta.download", build_yuanta_download)
register_action("yuanta.salary_status", build_yuanta_salary_status)


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
    result_files: list[str] = []
    special_result: dict[str, Any] = {}

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
            if line.startswith("CAPTCHA_RESULT:"):
                special_result.update(json.loads(line.split(":", 1)[1]))
                continue
            print(line, flush=True)
            record(line)
            if line.startswith("RESULT_FILE:"):
                file_path = line.split(":", 1)[1].strip()
                if file_path and file_path not in result_files:
                    result_files.append(file_path)
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
        if return_code == 0 and special_result.get("image_base64"):
            message = "驗證碼圖片已擷取，請在手機輸入驗證碼"
        elif return_code == 0 and special_result.get("already_logged_in"):
            message = "富邦已登入；任務已接續執行"
        if return_code == 0 and result_files:
            message += "；檔案：" + "、".join(result_files)
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
                    {
                        "exit_code": return_code,
                        "files": result_files,
                        "log_sheet": LOG_SHEET_NAME,
                        **special_result,
                    },
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
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--agent-id", default=default_agent_id())
    return parser.parse_args()


def heartbeat_loop(agent_id: str, stop_event: threading.Event, interval: float = 10) -> None:
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    actions = sorted(ACTION_HANDLERS)
    while not stop_event.is_set():
        try:
            write_agent_heartbeat(
                agent_id,
                status="online",
                pid=os.getpid(),
                actions=actions,
                version=AGENT_VERSION,
                service=service,
                spreadsheet_id=spreadsheet_id,
            )
        except Exception as exc:
            print(f"Heartbeat 失敗：{exc}", file=sys.stderr, flush=True)
        stop_event.wait(max(3.0, interval))
    try:
        write_agent_heartbeat(
            agent_id,
            status="offline",
            pid=os.getpid(),
            actions=actions,
            version=AGENT_VERSION,
            service=service,
            spreadsheet_id=spreadsheet_id,
        )
    except Exception:
        pass


def main() -> int:
    args = parse_args()
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    print(
        f"Local Agent ready: {args.agent_id}; actions={','.join(sorted(ACTION_HANDLERS))}",
        flush=True,
    )
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    heartbeat = threading.Thread(
        target=heartbeat_loop,
        args=(args.agent_id, stop_event),
        name="local-agent-heartbeat",
        daemon=True,
    )
    heartbeat.start()
    while not stop_event.is_set():
        try:
            task = claim_next_task(
                agent_id=args.agent_id,
                service=service,
                spreadsheet_id=spreadsheet_id,
            )
        except Exception as exc:
            print(f"Queue 連線失敗，{args.poll_seconds:g} 秒後重試：{exc}", file=sys.stderr, flush=True)
            if args.once:
                return 1
            stop_event.wait(max(1.0, args.poll_seconds))
            continue
        if task:
            run_task(task, service=service, spreadsheet_id=spreadsheet_id)
            if args.once:
                return 0
        elif args.once:
            print("No pending task", flush=True)
            return 0
        else:
            stop_event.wait(max(1.0, args.poll_seconds))
    stop_event.set()
    heartbeat.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
