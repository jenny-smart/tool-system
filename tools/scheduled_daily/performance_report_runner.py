from __future__ import annotations

import argparse
import os
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.scheduled_daily import performance_report

try:
    from tools.common.log_to_sheet import log_to_sheet
except Exception:
    log_to_sheet = None

TZ_TAIPEI = timezone(timedelta(hours=8))


def now_dt() -> datetime:
    return datetime.now(TZ_TAIPEI)


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in ["1", "true", "yes", "y", "寄送", "send"]


def apply_email_fallback_env() -> None:
    if not os.getenv("REPORT_EMAIL_SENDER") and os.getenv("NOTIFY_EMAIL"):
        os.environ["REPORT_EMAIL_SENDER"] = os.getenv("NOTIFY_EMAIL", "")
    if not os.getenv("REPORT_EMAIL_APP_PASSWORD") and os.getenv("NOTIFY_PASSWORD"):
        os.environ["REPORT_EMAIL_APP_PASSWORD"] = os.getenv("NOTIFY_PASSWORD", "")
    if not os.getenv("REPORT_EMAIL_RECIPIENT") and os.getenv("NOTIFY_TO"):
        os.environ["REPORT_EMAIL_RECIPIENT"] = os.getenv("NOTIFY_TO", "")


def write_log(status: str, message: str) -> None:
    if log_to_sheet is None:
        print(f"⚠️ log_to_sheet 不可用，略過打卡：{status} / {message}", flush=True)
        return

    try:
        log_to_sheet(
            system="日排程系統",
            function="業績報表",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            area="全區",
            period=now_dt().strftime("%Y%m%d"),
            target="dashboard_data/latest",
            source_file="performance_report.py",
            status=status,
            message=message,
        )
        print(f"📝 業績報表打卡：{status}", flush=True)
    except Exception as exc:
        print(f"⚠️ 業績報表打卡失敗：{exc}", flush=True)


def main(mode: str = "dashboard", auto_send: bool = False) -> dict[str, Any]:
    apply_email_fallback_env()
    write_log("執行中", f"開始執行業績報表：mode={mode}, auto_send={auto_send}")

    try:
        result = performance_report.main(mode=mode, auto_send=auto_send)
        write_log("成功", f"業績報表完成；raw_rows={result.get('raw_rows')}; summary_rows={result.get('summary_rows')}")
        return result
    except Exception as exc:
        tb = traceback.format_exc()
        print(tb, flush=True)
        write_log("失敗", f"{exc}\n{tb[-3000:]}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="dashboard")
    parser.add_argument("auto_send", nargs="?", default="false")
    args = parser.parse_args()
    main(mode=args.mode, auto_send=parse_bool(args.auto_send))
