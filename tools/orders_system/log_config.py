"""訂單系統專用的主控檔／打卡檔設定讀取。

從 config/systems.yaml 的「訂單系統」設定讀取：
  - master_spreadsheet_id：訂單系統主控檔
  - log_spreadsheet_id：訂單系統打卡檔（執行 log 寫入目標）

兩者讀取方式一致，都是模組載入時讀一次、快取成常數，
讓 ordersapp.py 與各功能模組的 _punch_log() 共用同一份設定，
不再各自預設寫入其他系統共用的主控 Log 試算表。
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

ORDERS_MASTER_SPREADSHEET_ID: str = str(_CFG.get("master_spreadsheet_id", "")).strip()
ORDERS_LOG_SPREADSHEET_ID: str = str(_CFG.get("log_spreadsheet_id", "")).strip()
