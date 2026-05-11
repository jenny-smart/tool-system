from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Taipei")


def log(message: str) -> None:
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)
