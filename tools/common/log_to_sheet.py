from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from tools.common.log_to_sheet import log_to_sheet
except Exception:
    from tools.common.log_to_sheet import log_to_sheet


def format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")


def write_job_log(
    *,
    system_name: str,
    job_name: str,
    status: str,
    started_at: Any = "",
    finished_at: Any = "",
    message: str = "",
    area: str = "",
    period: str = "",
    date: str = "",
    target: str = "",
    source_file: str = "",
    run_type: str = "手動",
    traceback_text: str = "",
) -> None:
    status_text = "成功" if status in ["success", "成功", "✅ 成功"] else "失敗"

    msg = message or ""
    if started_at or finished_at:
        msg = (
            f"開始時間：{format_dt(started_at)}｜"
            f"完成時間：{format_dt(finished_at)}｜"
            f"{msg}"
        )

    if "日排程" in system_name:
        system_key = "daily"
    elif "月排程" in system_name:
        system_key = "monthly"
    elif "外場" in system_name:
        system_key = "field"
    elif "通知" in system_name:
        system_key = "notify"
    else:
        system_key = "daily"

    log_to_sheet(
        system=system_key,
        function=job_name,
        run_type=run_type,
        area=area,
        period=period,
        date=date,
        target=target,
        source_file=source_file,
        status=status_text,
        message=msg,
        traceback_text=traceback_text or message,
    )
