"""訂單系統（orders-system，同步自 tools/orders_system/）專用的打卡檔設定讀取。

從 config/systems.yaml 的「訂單系統」設定讀取 log_spreadsheet_id（打卡檔，
執行 log 寫入目標），供 ordersapp.py 與各功能模組的 _punch_log() 共用，
不再落到其他系統共用的主控 Log 試算表。

注意：order-system（jenny-smart/order-system，架構跟這裡的 orders-system
不同的另一個獨立 repo）有自己的主控檔與執行 log，不在這份設定裡，
兩邊的 log 各自獨立，不互相同步。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
SYSTEM_NAME = "訂單系統"


def _load_system_config() -> dict[str, Any]:
    try:
        config_path = BASE_DIR / "config" / "systems.yaml"
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

        for sys_cfg in data.get("systems", []):
            if sys_cfg.get("name") == SYSTEM_NAME:
                return sys_cfg

        print(f"⚠️ systems.yaml 找不到系統：{SYSTEM_NAME}", flush=True)
    except Exception as e:
        print(f"⚠️ 讀取 systems.yaml 失敗：{e}", flush=True)

    return {}


_CFG = _load_system_config()

ORDERS_LOG_SPREADSHEET_ID: str = str(_CFG.get("log_spreadsheet_id", "")).strip()
