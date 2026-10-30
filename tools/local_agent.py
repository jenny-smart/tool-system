from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.request import urlopen

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.local_agent_queue import (
    LOG_SHEET_NAME,
    append_task_log,
    claim_next_task,
    default_agent_id,
    now_text,
    read_task_status,
    update_task,
    write_agent_heartbeat,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CommandBuilder = Callable[[dict[str, Any]], list[str]]
ACTION_HANDLERS: dict[str, CommandBuilder] = {}
AGENT_VERSION = "2"
AGENT_CDP_URL = "http://127.0.0.1:9222"
AGENT_CHROME_PROFILE = Path.home() / "EI account" / "chrome_profile"


def agent_chrome_online() -> bool:
    try:
        with urlopen(f"{AGENT_CDP_URL}/json/version", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def start_agent_chrome() -> None:
    if agent_chrome_online():
        return
    AGENT_CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["open", "-na", "Google Chrome", "--args", "--remote-debugging-port=9222",
         f"--user-data-dir={AGENT_CHROME_PROFILE}"], check=False,
    )
    for _ in range(30):
        if agent_chrome_online():
            print("Agent Chrome ready: http://127.0.0.1:9222", flush=True)
            return
        time.sleep(0.5)
    print("Agent Chrome 啟動失敗；請在 Mac 開啟 Chrome", file=sys.stderr, flush=True)


def chrome_watchdog(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        start_agent_chrome()
        stop_event.wait(5)


def register_action(action: str, builder: CommandBuilder) -> None:
    if not action.strip():
        raise ValueError("action 不可為空")
    ACTION_HANDLERS[action] = builder


def _common_invoice_args(params: dict[str, Any]) -> tuple[str, str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    return area, cdp_url


def build_cetustek_triplicate(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    plan = params.get("plan")
    if not area or area == "全區" or not isinstance(plan, dict):
        raise ValueError("二聯改三聯需要指定地區及預覽資料")
    return [sys.executable, "-m", "tools.invoice_center.triplicate_runner",
            "--area", area, "--cdp-url", cdp_url,
            "--plan", json.dumps(plan, ensure_ascii=False)]


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


def build_cetustek_paper_update(params: dict[str, Any]) -> list[str]:
    from tools.invoice_center.ei_export_all import normalize_month

    area, cdp_url = _common_invoice_args(params)
    month = normalize_month(str(params.get("month") or ""))
    return [sys.executable, "-m", "tools.invoice_center.paper_update",
            "--area", area or "全區", "--month", month, "--cdp-url", cdp_url]


def build_cetustek_prize_update(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    period = str(params.get("period") or params.get("month") or "").strip()
    if not area or area == "全區":
        raise ValueError("中獎發票更新請選擇單一區域")
    if not period:
        raise ValueError("中獎發票更新請輸入 8 碼期別")
    return [
        sys.executable,
        "-m",
        "tools.invoice_center.prize_update",
        "--area",
        area,
        "--period",
        period,
        "--cdp-url",
        cdp_url,
    ]


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


def build_mofei_login(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("財政部電子發票登入任務必須指定區域")
    return [
        sys.executable,
        "-m",
        "tools.invoice_center.mof_serial",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
        "--login-only",
    ]


def build_mofei_download(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("財政部電子發票取號任務必須指定區域")
    command = [
        sys.executable,
        "-m",
        "tools.invoice_center.mof_serial",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
    ]
    period = str(params.get("month") or "").strip()
    if period:
        command.extend(["--period", period])
    return command


def build_cetustek_serial_import(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("鯨躍字軌匯入任務必須指定區域")
    command = [
        sys.executable,
        "-m",
        "tools.invoice_center.cetustek_serial",
        "import",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
    ]
    period = str(params.get("month") or "").strip()
    if period:
        command.extend(["--period", period])
    return command


def build_cetustek_serial_section_query(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("鯨躍字軌配號任務必須指定區域")
    qyear = str(params.get("qyear") or "").strip()
    qmonth = str(params.get("qmonth") or "").strip()
    if not qyear or not qmonth:
        raise ValueError("配號查詢任務必須提供查詢年份與查詢月份")
    return [
        sys.executable,
        "-m",
        "tools.invoice_center.cetustek_serial",
        "allocate",
        "--area",
        area,
        "--qyear",
        qyear,
        "--qmonth",
        qmonth,
        "--cdp-url",
        cdp_url,
    ]


def build_cetustek_import_and_allocate(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("匯入＋配號任務必須指定區域")
    qyear = str(params.get("qyear") or "").strip()
    qmonth = str(params.get("qmonth") or "").strip()
    if not qyear or not qmonth:
        raise ValueError("匯入＋配號需要提供鯨躍配號查詢年份與查詢月份")
    command = [
        sys.executable,
        "-m",
        "tools.invoice_center.cetustek_serial",
        "import-and-allocate",
        "--area",
        area,
        "--qyear",
        qyear,
        "--qmonth",
        qmonth,
        "--cdp-url",
        cdp_url,
    ]
    period = str(params.get("month") or "").strip()
    if period:
        command.extend(["--period", period])
    return command


def build_cetustek_full_pipeline(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("完整流程任務必須指定區域")
    qyear = str(params.get("qyear") or "").strip()
    qmonth = str(params.get("qmonth") or "").strip()
    if not qyear or not qmonth:
        raise ValueError("完整流程需要提供鯨躍配號查詢年份與查詢月份")
    command = [
        sys.executable,
        "-m",
        "tools.invoice_center.mof_to_cetustek_pipeline",
        "--area",
        area,
        "--qyear",
        qyear,
        "--qmonth",
        qmonth,
        "--cdp-url",
        cdp_url,
    ]
    period = str(params.get("month") or "").strip()
    if period:
        command.extend(["--period", period])
    return command


def build_cetustek_allowance(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    selected_rows = params.get("selected_rows") or []
    if not area or area == "全區":
        raise ValueError("開立折讓單請選擇單一區域")
    if not selected_rows:
        raise ValueError("請先勾選要開立折讓單的資料")
    return [sys.executable, "-m", "tools.invoice_center.allowance_create", "--area", area,
            "--cdp-url", cdp_url, "--rows", ",".join(str(int(row)) for row in selected_rows)]


def build_cetustek_paper_invoice_pdf(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    month = str(params.get("month") or "").strip()
    if not area or area == "全區":
        raise ValueError("紙本發票 PDF 建檔請選擇單一區域")
    if not month:
        raise ValueError("請輸入要建檔的月份")
    command = [
        sys.executable, "-m", "tools.invoice_center.paper_invoice_pdf", month,
        "--area", area, "--cdp-url", cdp_url,
    ]
    if not bool(params.get("upload", True)):
        command.append("--no-upload")
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


def build_newebpay_express_payment_check(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    if not area or area == "全區":
        raise ValueError("藍新快速收款查帳請選擇單一區域")
    return [
        sys.executable,
        "-m",
        "tools.newebpay.express_payment_check",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
    ]


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


def build_change_order_sync(params: dict[str, Any]) -> list[str]:
    regions = [str(r).strip() for r in params.get("regions") or [] if str(r).strip()]
    command = [sys.executable, "-m", "tools.finance_management.change_order_batch", "sync"]
    if regions:
        command.extend(["--regions", ",".join(regions)])
    rows = params.get("rows")
    if isinstance(rows, dict):
        command.extend(["--rows", json.dumps(rows, ensure_ascii=False)])
    return command


def build_change_order_batch_run(params: dict[str, Any]) -> list[str]:
    plan = params.get("plan")
    if not isinstance(plan, list) or not plan:
        raise ValueError("清潔異動整合執行缺少執行計畫")
    _area, cdp_url = _common_invoice_args(params)
    return [sys.executable, "-m", "tools.finance_management.change_order_batch", "run",
            "--plan", json.dumps(plan, ensure_ascii=False), "--cdp-url", cdp_url]


def build_internal_payment_record(params: dict[str, Any]) -> list[str]:
    items = params.get("items") or []
    if not items:
        raise ValueError("留紀錄缺少勾選的列")
    return [sys.executable, "-m", "tools.finance_management.internal_payment_batch", "record",
            "--items", json.dumps(items, ensure_ascii=False)]


def build_lemon_stored_value_adjustment(params: dict[str, Any]) -> list[str]:
    area, cdp_url = _common_invoice_args(params)
    selected_rows = params.get("selected_rows") or []
    if not area or area == "全區":
        raise ValueError("異動儲值金請選擇單一區域")
    if not selected_rows:
        raise ValueError("請先勾選要異動儲值金的資料")
    return [
        sys.executable, "-m", "tools.lemon_backend.stored_value_adjustment",
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
    from_sheet = params.get("start_from") == "sheet"
    if not end_date or not (start_date or from_sheet):
        raise ValueError("富邦明細下載缺少開始日期或結束日期")
    if from_sheet:
        # 開始日由 Agent 執行時讀財報最後一列決定
        start_date = ""
    start_label = start_date or "財報起"
    output = PROJECT_ROOT / "outputs" / f"fubon_{area}_{start_label}_{end_date}.csv"
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_agent",
        "download",
        "--area",
        area,
        "--end",
        end_date,
        "--cdp-url",
        cdp_url,
        "--output",
        str(output),
    ] + (["--start", start_date] if start_date else ["--start-from-sheet"])


def build_fubon_atm_refund(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    selected_rows = params.get("selected_rows") or []
    if area not in ("台北", "台中"):
        raise ValueError("富邦 ATM 退款目前只支援台北、台中")
    if not selected_rows:
        raise ValueError("請先勾選要處理的 ATM 退款資料")
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_atm_refund",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
        "--rows",
        ",".join(str(int(row)) for row in selected_rows),
    ]


def build_fubon_payment_request(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    selected_rows = params.get("selected_rows") or []
    if area not in ("台北", "台中"):
        raise ValueError("富邦請款記錄目前只支援台北、台中")
    if not selected_rows:
        raise ValueError("請先勾選要處理的請款記錄資料")
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_payment_request",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
        "--rows",
        ",".join(str(int(row)) for row in selected_rows),
    ] + (["--merge"] if params.get("merge") else [])


def build_fubon_deposit_refund(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    selected_rows = params.get("selected_rows") or []
    if area not in ("台北", "台中"):
        raise ValueError("富邦工具包押金退款目前只支援台北、台中")
    if not selected_rows:
        raise ValueError("請先勾選要處理的工具包押金退款資料")
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_deposit_refund",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
        "--rows",
        ",".join(str(int(row)) for row in selected_rows),
    ]


def build_fubon_supply_purchase(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    month = str(params.get("month") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    selected_rows = params.get("selected_rows") or []
    if area not in ("台北", "台中"):
        raise ValueError("富邦清潔用品採購目前只支援台北、台中")
    if not month:
        raise ValueError("請先輸入採購月份")
    if not selected_rows:
        raise ValueError("請先勾選要處理的清潔用品採購資料")
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_supply_purchase",
        "--area",
        area,
        "--month",
        month,
        "--cdp-url",
        cdp_url,
        "--rows",
        ",".join(str(int(row)) for row in selected_rows),
    ]


def build_fubon_internal_payment(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    payment_rows = params.get("payment_rows") or []
    deposit_rows = params.get("deposit_rows") or []
    supply = {str(month): [int(row) for row in rows] for month, rows in (params.get("supply") or {}).items() if rows}
    if area not in ("台北", "台中"):
        raise ValueError("富邦內部請款目前只支援台北、台中")
    if not (payment_rows or deposit_rows or supply):
        raise ValueError("請先勾選要處理的內部請款資料")
    return [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_internal_payment",
        "--area",
        area,
        "--cdp-url",
        cdp_url,
        "--payment-rows",
        ",".join(str(int(row)) for row in payment_rows),
        "--deposit-rows",
        ",".join(str(int(row)) for row in deposit_rows),
        "--supply",
        json.dumps(supply, ensure_ascii=False),
    ] + (["--merge"] if params.get("merge") else []) + (
        ["--merge-rows", ",".join(str(int(row)) for row in params["merge_rows"])] if params.get("merge_rows") else []
    )


def build_fubon_fixed_expense_transfer(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    month = str(params.get("month") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    if area not in ("台中", "桃園", "新竹", "高雄", "家電"):
        raise ValueError("固定費用富邦匯款請選擇單一地區")
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", month):
        raise ValueError("固定費用富邦匯款缺少正確期別（YYYYMM）")
    command = [
        sys.executable,
        "-m",
        "tools.bank_statement.fubon_fixed_expense_transfer",
        "--area",
        area,
        "--month",
        month,
        "--cdp-url",
        cdp_url,
    ]
    selected_rows = params.get("selected_rows")
    if selected_rows is not None:
        if not selected_rows:
            raise ValueError("請至少勾選一個固定費用科目")
        command.extend(["--rows", ",".join(str(int(row)) for row in selected_rows)])
    return command


def _fubon_mobile_download_args(area: str, params: dict[str, Any], *, required: bool) -> list[str]:
    """手機驗證後接續下載：start_from=sheet 時開始日由 Agent 讀財報最後一列決定。"""
    from_sheet = params.get("start_from") == "sheet"
    start_date = "" if from_sheet else str(params.get("start_date") or "").strip()
    end_date = str(params.get("end_date") or "").strip()
    if required and (not end_date or not (start_date or from_sheet)):
        raise ValueError("富邦明細下載缺少日期")
    output = PROJECT_ROOT / "outputs" / f"fubon_{area}_{start_date or '財報起'}_{end_date}.csv"
    start_args = ["--start-from-sheet"] if from_sheet else ["--start", start_date]
    return [*start_args, "--end", end_date, "--output", str(output)]


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
        command.extend(_fubon_mobile_download_args(area, params, required=True))
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
        command.extend(_fubon_mobile_download_args(area, params, required=False))
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
    from_sheet = params.get("start_from") == "sheet"
    if not end_date or not (start_date or from_sheet):
        raise ValueError("元大明細查詢缺少開始日期或結束日期")
    if from_sheet:
        # 開始日由 Agent 執行時讀財報最後一列決定
        start_date = ""
    start_label = start_date or "財報起"
    output = PROJECT_ROOT / "outputs" / f"yuanta_{area}_{start_label}_{end_date}.csv"
    return [
        sys.executable, "-m", "tools.bank_statement.yuanta_agent", "download",
        "--area", area, "--end", end_date,
        "--output", str(output),
    ] + (["--start", start_date] if start_date else ["--start-from-sheet"])


def build_yuanta_salary_status(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    if not area or area == "全區":
        raise ValueError("元大薪資付款狀態請選擇單一區域")
    return [
        sys.executable, "-m", "tools.bank_statement.yuanta_salary_status",
        "--area", area, "--cdp-url", cdp_url,
    ]


def build_yuanta_salary_upload(params: dict[str, Any]) -> list[str]:
    area = str(params.get("area") or "").strip()
    period = str(params.get("period") or "").strip()
    cdp_url = str(params.get("cdp_url") or "http://127.0.0.1:9222").strip()
    include_contract = bool(params.get("include_contract", True))
    include_internal = bool(params.get("include_internal", False))
    transfer_fubon = bool(params.get("transfer_fubon", True))
    if not area or area == "全區":
        raise ValueError("元大薪資檔上傳請選擇單一區域")
    if not re.fullmatch(r"\d{6}-[12]", period):
        raise ValueError("元大薪資檔上傳期別須為 YYYYMM-1 或 YYYYMM-2")
    if not include_contract and not include_internal:
        raise ValueError("請至少選擇專員承攬或內勤薪資")
    command = [
        sys.executable, "-m", "tools.bank_statement.yuanta_salary_upload",
        "--area", area, "--period", period, "--cdp-url", cdp_url,
    ]
    if not include_contract:
        command.append("--no-contract")
    if include_internal:
        command.append("--internal")
    if transfer_fubon:
        command.append("--fubon-transfer")
    expected_files = params.get("expected_files")
    if isinstance(expected_files, list):
        command.extend(["--expected-plan", json.dumps(expected_files, ensure_ascii=False)])
    return command


# 更新一律拉 origin/main；本機不在 main 時直接失敗並印出分支，避免「Already up to date」卻一直跑舊程式。
# 本機有 main 沒有的 commit 時改用合併（衝突就還原）；更新後用 Agent 自己的 Python 檢查語法，
# 有錯就退回更新前的版本且不重啟，避免 Agent 啟動即 SyntaxError。
GIT_PULL_SCRIPT = (
    'branch="$(git rev-parse --abbrev-ref HEAD)"; '
    'echo "目前分支：$branch（$(git rev-parse --short HEAD)）"; '
    'if [ "$branch" != "main" ]; then '
    'echo "❌ 本機不在 main 分支，不會更新到最新程式。請在 Mac 終端機執行：git -C \"$PWD\" checkout main"; exit 1; fi; '
    'before="$(git rev-parse HEAD)"; '
    'git fetch origin main || exit 1; '
    'if ! git merge --ff-only FETCH_HEAD; then '
    'echo "⚠️ 本機有 main 沒有的 commit，改用合併方式更新"; '
    'if ! git merge --no-edit FETCH_HEAD; then '
    'git merge --abort 2>/dev/null; '
    'echo "❌ 合併失敗，已維持原本版本；請在 Mac 終端機執行 git status 確認"; exit 1; fi; fi; '
    'if ! "$AGENT_PYTHON" -m tools.syntax_check; then '
    'git reset --keep "$before"; '
    'echo "❌ 新版程式在本機 Python 有語法錯誤，已退回 $(git rev-parse --short HEAD)，不會重啟 Agent"; exit 1; fi; '
    'echo "更新後：$(git rev-parse --short HEAD)"'
)


def build_git_pull(_params: dict[str, Any]) -> list[str]:
    """Update this checkout from origin/main without rewriting local commits or modifications."""
    return [
        "/usr/bin/env", f"AGENT_PYTHON={sys.executable}",
        "/bin/sh", "-c", f'cd "{PROJECT_ROOT}" && {GIT_PULL_SCRIPT}',
    ]


def build_agent_status(_params: dict[str, Any]) -> list[str]:
    return [str(PROJECT_ROOT / "scripts" / "local_agent_service.sh"), "status"]


def build_agent_logs(_params: dict[str, Any]) -> list[str]:
    return [str(PROJECT_ROOT / "scripts" / "local_agent_service.sh"), "logs"]


def build_agent_restart(_params: dict[str, Any]) -> list[str]:
    # The running child must not call restart on itself. Mark this task complete,
    # then exit below so the existing user-session supervisor restarts a fresh child.
    return ["/usr/bin/true"]


def build_git_pull_restart(params: dict[str, Any]) -> list[str]:
    return build_git_pull(params)


register_action("cetustek.login", build_cetustek_login)
register_action("cetustek.triplicate", build_cetustek_triplicate)
register_action("cetustek.download", build_cetustek_download)
register_action("cetustek.prize_update", build_cetustek_prize_update)
register_action("cetustek.paper_update", build_cetustek_paper_update)
register_action("cetustek.allowance", build_cetustek_allowance)
register_action("cetustek.paper_invoice_pdf", build_cetustek_paper_invoice_pdf)
register_action("cetustek.serial_import", build_cetustek_serial_import)
register_action("cetustek.serial_section_query", build_cetustek_serial_section_query)
register_action("cetustek.full_pipeline", build_cetustek_full_pipeline)
register_action("cetustek.import_and_allocate", build_cetustek_import_and_allocate)
register_action("mofei.login", build_mofei_login)
register_action("mofei.download", build_mofei_download)
register_action("newebpay.login", build_newebpay_login)
register_action("newebpay.download", build_newebpay_download)
register_action("newebpay.express_payment_check", build_newebpay_express_payment_check)
register_action("newebpay.invoice_amounts", build_newebpay_invoice_amounts)
register_action("newebpay.refund_pending", build_newebpay_refund_pending)
register_action("lemon.stored_value_adjustment", build_lemon_stored_value_adjustment)
register_action("change_order.sync", build_change_order_sync)
register_action("change_order.batch_run", build_change_order_batch_run)
register_action("internal_payment.record", build_internal_payment_record)
register_action("fubon.login", build_fubon_login)
register_action("fubon.download", build_fubon_download)
register_action("fubon.atm_refund", build_fubon_atm_refund)
register_action("fubon.payment_request", build_fubon_payment_request)
register_action("fubon.deposit_refund", build_fubon_deposit_refund)
register_action("fubon.supply_purchase", build_fubon_supply_purchase)
register_action("fubon.internal_payment", build_fubon_internal_payment)
register_action("fubon.fixed_expense_transfer", build_fubon_fixed_expense_transfer)
register_action("fubon.captcha", build_fubon_captcha)
register_action("fubon.verify", build_fubon_verify)
register_action("yuanta.login", build_yuanta_login)
register_action("yuanta.download", build_yuanta_download)
register_action("yuanta.salary_status", build_yuanta_salary_status)
register_action("yuanta.salary_upload", build_yuanta_salary_upload)
register_action("system.git_pull", build_git_pull)
register_action("system.agent_status", build_agent_status)
register_action("system.agent_logs", build_agent_logs)
register_action("system.agent_restart", build_agent_restart)
register_action("system.git_pull_restart", build_git_pull_restart)


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


# 本機 Agent 任務的中文名稱（與網頁選單名稱一致；統一執行 Log 的「功能」欄用，英文代號另記在「來源檔案」欄）
ACTION_LABELS = {
    "cetustek.login": "【鯨躍發票】鯨躍登入／開立發票",
    "cetustek.triplicate": "【鯨躍發票】二聯改三聯",
    "cetustek.download": "【鯨躍發票】鯨躍發票下載",
    "cetustek.prize_update": "【鯨躍發票】中獎發票更新",
    "cetustek.paper_update": "【鯨躍發票】紙本發票更新",
    "cetustek.allowance": "【鯨躍發票】開立折讓單",
    "cetustek.paper_invoice_pdf": "【鯨躍發票】紙本發票PDF建檔",
    "cetustek.serial_import": "【鯨躍發票】電子發票字軌號碼匯入",
    "cetustek.serial_section_query": "【鯨躍發票】電子發票字軌號碼配號",
    "cetustek.full_pipeline": "【鯨躍發票】財政部字軌下載＋匯入＋配號（完整流程）",
    "cetustek.import_and_allocate": "【鯨躍發票】電子發票字軌號碼匯入＋配號",
    "mofei.login": "【財政部電子發票】財政部登入",
    "mofei.download": "【財政部電子發票】字軌取號下載",
    "newebpay.login": "【藍新金流】藍新登入",
    "newebpay.download": "【藍新金流】藍新收退款下載",
    "newebpay.express_payment_check": "【藍新金流】藍新快速收款查帳",
    "newebpay.invoice_amounts": "【藍新金流】藍新手續費發票金額",
    "newebpay.refund_pending": "【藍新金流】藍新信用卡待退款",
    "lemon.stored_value_adjustment": "【檸檬後台】異動儲值金",
    "change_order.sync": "【清潔異動】回寫系統",
    "change_order.batch_run": "【清潔異動】整合執行（逐區依序）",
    "internal_payment.record": "【富邦銀行】內部請款留紀錄",
    "fubon.login": "【富邦銀行】富邦登入",
    "fubon.download": "【富邦銀行】富邦明細下載",
    "fubon.atm_refund": "【富邦銀行】異動 ATM 退款",
    "fubon.payment_request": "【富邦銀行】請款記錄",
    "fubon.deposit_refund": "【富邦銀行】工具包押金退款",
    "fubon.supply_purchase": "【富邦銀行】清潔用品採購",
    "fubon.internal_payment": "【富邦銀行】內部請款整合執行",
    "fubon.fixed_expense_transfer": "【財報】各區固定費用匯款",
    "fubon.captcha": "【富邦銀行】擷取驗證碼",
    "fubon.verify": "【富邦銀行】送出驗證碼",
    "yuanta.login": "【元大銀行】元大登入",
    "yuanta.download": "【元大銀行】元大明細下載",
    "yuanta.salary_status": "【元大銀行】檢查薪資付款狀態",
    "yuanta.salary_upload": "【元大銀行】薪資檔上傳待放行",
    "system.git_pull": "【Agent】更新程式",
    "system.agent_status": "【Agent】查看狀態",
    "system.agent_logs": "【Agent】查看 Log",
    "system.agent_restart": "【Agent】重新啟動",
    "system.git_pull_restart": "【Agent】更新程式並重啟",
}


def _last_error_line(log_text: str) -> str:
    """失敗時抓 log 裡最後一行錯誤（RuntimeError: … 之類），找不到就取最後一行。"""
    lines = [line.strip() for line in str(log_text or "").splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("[") or "FAILED" not in line]
    for line in reversed(lines):
        if "Error" in line or "錯誤" in line or "失敗" in line or line.startswith("❌"):
            return line[:500]
    return lines[-1][:500] if lines else ""


def _unified_task_log(task: dict[str, str], status: str, message: str, log_text: str = "") -> None:
    """本機 Agent 每個任務結束時，另寫一筆到主控表「tool-system執行Log-年度」。"""
    from tools.common import unified_log

    try:
        params = json.loads(task.get("params_json") or "{}")
    except (TypeError, ValueError):
        params = {}
    if status == "failed":
        detail = _last_error_line(log_text)
        message = f"{message}｜{detail}" if detail and detail not in message else message
    period = params.get("start_date") or params.get("month") or params.get("period") or ""
    action = task.get("action", "")
    unified_log.write(system="本機Agent", function=ACTION_LABELS.get(action, action), run_type="本機Agent",
                      area=params.get("area") or "", period=period, target=task.get("task_id", ""),
                      source_file=action,
                      status=status, message=message, operator=task.get("created_by", ""))


def run_task(task: dict[str, str], *, service: Any, spreadsheet_id: str) -> int:
    row_number = int(task["_row"])
    task_id = task["task_id"]
    preview = ""
    pending_log = ""
    next_seq = 1
    result_files: list[str] = []
    special_result: dict[str, Any] = {}
    cancel_requested = threading.Event()
    cancel_monitor_stop = threading.Event()

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
        child_env = os.environ.copy()
        project_root_str = str(PROJECT_ROOT)
        existing_pp = child_env.get("PYTHONPATH", "")
        pp_parts = [p for p in existing_pp.split(os.pathsep) if p]
        if project_root_str not in pp_parts:
            child_env["PYTHONPATH"] = project_root_str + (os.pathsep + existing_pp if existing_pp else "")
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )

        def monitor_cancellation() -> None:
            control_service = get_sheets_service()
            while not cancel_monitor_stop.wait(2):
                try:
                    status = read_task_status(
                        row_number,
                        service=control_service,
                        spreadsheet_id=spreadsheet_id,
                    )
                    if status != "cancel_requested":
                        continue
                    cancel_requested.set()
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        for _ in range(10):
                            if process.poll() is not None:
                                break
                            time.sleep(0.5)
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                    return
                except Exception as exc:
                    print(f"取消狀態檢查失敗：{exc}", file=sys.stderr, flush=True)

        cancel_monitor = threading.Thread(
            target=monitor_cancellation,
            name=f"agent-cancel-{task_id[:8]}",
            daemon=True,
        )
        cancel_monitor.start()
        last_upload = 0.0
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if line.startswith("CAPTCHA_RESULT:"):
                special_result.update(json.loads(line.split(":", 1)[1]))
                if special_result.get("image_base64"):
                    # 驗證碼一擷取到就先寫回，網頁不用等子程式結束（斷開 Chrome、寫 Log）才看到圖片
                    update_task(
                        row_number,
                        {
                            "message": "驗證碼圖片已擷取，請在手機輸入驗證碼",
                            "result_json": json.dumps(
                                {"log_sheet": LOG_SHEET_NAME, **special_result}, ensure_ascii=False
                            ),
                        },
                        service=service,
                        spreadsheet_id=spreadsheet_id,
                    )
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
        cancel_monitor_stop.set()
        cancel_monitor.join(timeout=3)
        if cancel_requested.is_set():
            status = "cancelled"
            message = "已中止目前工作"
        else:
            status = "completed" if return_code == 0 else "failed"
            message = "執行完成" if return_code == 0 else f"執行失敗（exit {return_code}）"
        if not cancel_requested.is_set() and return_code == 0 and special_result.get("image_base64"):
            message = "驗證碼圖片已擷取，請在手機輸入驗證碼"
        elif not cancel_requested.is_set() and return_code == 0 and special_result.get("already_logged_in"):
            message = "富邦已登入；任務已接續執行"
        if not cancel_requested.is_set() and return_code == 0 and result_files:
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
        _unified_task_log(task, status, message, preview)
        return return_code
    except Exception as exc:
        cancel_monitor_stop.set()
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
        _unified_task_log(task, "failed", str(exc)[:500], preview)
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
    try:
        from tools.lemon_backend.config import export_credentials_to_environment

        configured_areas = export_credentials_to_environment()
        print(
            "Lemon backend credentials loaded: "
            + (", ".join(configured_areas) if configured_areas else "none"),
            flush=True,
        )
    except Exception as exc:
        print(f"Lemon backend credentials load failed: {exc}", file=sys.stderr, flush=True)

    args = parse_args()
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    print(
        f"Local Agent ready: {args.agent_id}; actions={','.join(sorted(ACTION_HANDLERS))}",
        flush=True,
    )
    stop_event = threading.Event()
    start_agent_chrome()

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
    chrome_monitor = threading.Thread(
        target=chrome_watchdog, args=(stop_event,), name="agent-chrome-watchdog", daemon=True,
    )
    chrome_monitor.start()
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
            return_code = run_task(task, service=service, spreadsheet_id=spreadsheet_id)
            if (
                return_code == 0
                and task.get("action") in {"system.agent_restart", "system.git_pull_restart"}
            ):
                print(
                    "Agent control task completed; exiting child so the user-session supervisor "
                    "can reload the current code.",
                    flush=True,
                )
                stop_event.set()
                break
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
