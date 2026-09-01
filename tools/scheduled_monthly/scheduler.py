from __future__ import annotations

"""月排程 scheduler。

- MONTHLY_ROOT_FOLDER_ID 指向「03 服務分潤表」總根目錄。
- 依 period/start/目前年份自動進入「YYYY專員承攬服務費」。
- 月排程 Drive 操作強制使用 Jenny OAuth。
- 執行 Log 保持由主程序原本 Service Account 寫入。
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from tools.common.log_to_sheet import write_job_log

TZ = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parents[2]

AREA_ALIASES = {
    "01.台北專員": "台北",
    "02.台中專員": "台中",
    "03.新北專員": "新北",
    "04.桃園專員": "桃園",
    "05.新竹專員": "新竹",
    "06.高雄專員": "高雄",
}

JOBS = {
    "half_month_orders_1": {"label": "上半月訂單", "script": "tools/scheduled_monthly/half_month_orders.py", "extra": ["--half", "1"], "needs_folder_id": True},
    "half_month_orders_2": {"label": "下半月訂單", "script": "tools/scheduled_monthly/half_month_orders.py", "extra": ["--half", "2"], "needs_folder_id": True},
    "refund_report": {"label": "已退款", "script": "tools/scheduled_monthly/refund_report.py", "extra": [], "needs_folder_id": True},
    "prepaid_report": {"label": "預收", "script": "tools/scheduled_monthly/prepaid_report.py", "extra": [], "needs_folder_id": True},
    "stored_value_settlement": {"label": "儲值金結算", "script": "tools/scheduled_monthly/stored_value_settlement.py", "extra": [], "needs_folder_id": True, "root_env": "STORED_VALUE_ROOT_FOLDER_ID", "root_default": "15GQ7eUqUrxS95JKOaO6W_qV0g6OBXRPv"},
    "stored_value_prepaid": {"label": "儲值金預收", "script": "tools/scheduled_monthly/stored_value_prepaid.py", "extra": [], "needs_folder_id": True, "root_env": "STORED_VALUE_ROOT_FOLDER_ID", "root_default": "15GQ7eUqUrxS95JKOaO6W_qV0g6OBXRPv"},
}


def now_tw() -> datetime:
    return datetime.now(TZ)


def normalize_area_arg(area: str = "all") -> str:
    value = str(area or "all").strip()
    if value in ["", "全區", "全部", "ALL", "All", "all"]:
        return "all"
    return AREA_ALIASES.get(value, value)


def _plain_secret(value):
    try:
        if hasattr(value, "to_dict"):
            return value.to_dict()
    except Exception:
        pass
    try:
        if hasattr(value, "items") and not isinstance(value, str):
            return {str(k): _plain_secret(v) for k, v in value.items()}
    except Exception:
        pass
    return value


def build_child_env() -> dict[str, str]:
    """把 Streamlit Secrets 完整傳給月排程 subprocess。"""
    env = os.environ.copy()

    base_str = str(BASE_DIR)
    existing_pp = env.get("PYTHONPATH", "")
    pp_parts = [p for p in existing_pp.split(os.pathsep) if p]
    if base_str not in pp_parts:
        env["PYTHONPATH"] = base_str + (os.pathsep + existing_pp if existing_pp else "")

    try:
        import streamlit as st
        try:
            import toml
            secrets_dict = {str(k): _plain_secret(v) for k, v in st.secrets.items()}
            env["STREAMLIT_SECRETS_TOML"] = toml.dumps(secrets_dict)
        except Exception as exc:
            print(f"⚠️ 月排程 secrets TOML 注入失敗：{exc}", flush=True)

        try:
            for key in st.secrets:
                try:
                    plain = _plain_secret(st.secrets[key])
                    if isinstance(plain, str):
                        env[str(key)] = plain
                    elif isinstance(plain, dict):
                        env[str(key)] = json.dumps(plain, ensure_ascii=False)
                except Exception:
                    pass
        except Exception as exc:
            print(f"⚠️ 月排程 secrets env 注入失敗：{exc}", flush=True)
    except Exception:
        pass

    env["MONTHLY_SCHEDULER_MANAGED"] = "1"

    oauth_keys = (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    )
    missing = [key for key in oauth_keys if not env.get(key, "").strip()]
    if missing:
        raise RuntimeError("一鍵月排程子程序缺少 OAuth Secret：" + ", ".join(missing))

    return env


def get_drive_service():
    """年度資料夾解析本身也強制 Jenny OAuth。"""
    child_env = build_child_env()
    credentials = Credentials(
        token=None,
        refresh_token=child_env["GOOGLE_OAUTH_REFRESH_TOKEN"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=child_env["GOOGLE_OAUTH_CLIENT_ID"].strip(),
        client_secret=child_env["GOOGLE_OAUTH_CLIENT_SECRET"].strip(),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def q_escape(value: str) -> str:
    return value.replace("'", "\\'")


def resolve_year(period: str = "", start: str = "") -> int:
    period = str(period or "").strip()
    start = str(start or "").strip()
    if len(period) >= 4 and period[:4].isdigit():
        return int(period[:4])
    if len(start) >= 4 and start[:4].isdigit():
        return int(start[:4])
    return now_tw().year


def find_child_folder(service, parent_id: str, folder_name: str) -> str:
    query = (
        f"'{q_escape(parent_id)}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and "
        f"name='{q_escape(folder_name)}' and trashed=false"
    )
    result = service.files().list(
        q=query,
        fields="files(id,name)",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files", [])
    return files[0]["id"] if files else ""


def get_or_create_year_folder(root_folder_id: str, year: int) -> str:
    if not root_folder_id:
        raise RuntimeError("缺少 MONTHLY_ROOT_FOLDER_ID")

    service = get_drive_service()
    folder_name = f"{year}專員承攬服務費"
    folder_id = find_child_folder(service, root_folder_id, folder_name)
    if folder_id:
        print(f"📁 月排程年度資料夾：{folder_name} ({folder_id})", flush=True)
        return folder_id

    created = service.files().create(
        body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [root_folder_id]},
        fields="id,name",
        supportsAllDrives=True,
    ).execute()
    print(f"📁 已建立月排程年度資料夾：{created['name']} ({created['id']})", flush=True)
    return created["id"]


def punch(label, status, started_at, finished_at=None, area="", period="", target="", message="", traceback_text=""):
    try:
        write_job_log(
            system_name="月排程系統",
            job_name=label,
            status=status,
            started_at=started_at,
            finished_at=finished_at or "",
            message=message,
            area=area,
            period=period,
            date="",
            target=target,
            source_file="",
            run_type="排程" if os.getenv("GITHUB_ACTIONS") else "手動",
            traceback_text=traceback_text,
        )
        print(f"📝 月排程打卡：{label} / {status}", flush=True)
    except Exception as exc:
        print(f"⚠️ 月排程打卡失敗：{exc}", flush=True)


def build_child_command(script: Path, extra: list[str], folder_id: str, area: str, period: str, start: str, end: str) -> list[str]:
    """與日排程相同，強制 OAuth bootstrap 後才執行月排程 script。"""
    bootstrap = (
        "import importlib, runpy, sys; "
        "import sitecustomize; "
        "importlib.reload(sitecustomize); "
        "script = sys.argv[1]; "
        "sys.argv = sys.argv[1:]; "
        "runpy.run_path(script, run_name='__main__')"
    )
    cmd = [sys.executable, "-u", "-c", bootstrap, str(script), *extra, "--folder-id", folder_id]
    if area:
        cmd.extend(["--area", area])
    if period:
        cmd.extend(["--period", period])
    if start and end:
        cmd.extend(["--start", start, "--end", end])
    return cmd


def run_job(job_name: str, folder_id: str = "", area: str = "all", period: str = "", start: str = "", end: str = "") -> dict:
    if job_name not in JOBS:
        raise RuntimeError(f"未知月排程 job：{job_name}")

    area = normalize_area_arg(area)
    job = JOBS[job_name]
    label = job["label"]
    script = BASE_DIR / job["script"]
    if not script.exists():
        raise RuntimeError(f"{label} 找不到執行檔：{script}")
    if job.get("needs_folder_id") and not folder_id:
        raise RuntimeError(f"{label} 缺少 folder_id")

    period_text = period or f"{start}~{end}".strip("~")
    started_at = now_tw()
    punch(label, "running", started_at, area=area, period=period_text, target=folder_id, message="開始執行")

    try:
        child_env = build_child_env()
        cmd = build_child_command(script, job.get("extra", []), folder_id, area, period, start, end)
        print("OAuth env ready = True", flush=True)
        print("OAuth bootstrap forced = True", flush=True)
        print(f"Command: Python bootstrap -> {script}", flush=True)
        completed = subprocess.run(cmd, cwd=BASE_DIR, text=True, capture_output=True, env=child_env)
    except Exception as exc:
        finished_at = now_tw()
        message = f"{label} 啟動前失敗：{exc}"
        punch(label, "failed", started_at, finished_at, area=area, period=period_text, target=folder_id, message=message, traceback_text=message)
        raise RuntimeError(message) from exc

    finished_at = now_tw()
    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""
    if stdout_text:
        print(stdout_text, flush=True)
    if stderr_text:
        print(stderr_text, flush=True)

    if completed.returncode != 0:
        message = (
            f"{label} 執行失敗，exit={completed.returncode}\n"
            f"STDOUT:\n{stdout_text[-3000:]}\n"
            f"STDERR:\n{stderr_text[-5000:]}"
        )
        punch(label, "failed", started_at, finished_at, area=area, period=period_text, target=folder_id, message=message, traceback_text=stderr_text or message)
        raise RuntimeError(message)

    punch(label, "success", started_at, finished_at, area=area, period=period_text, target=folder_id, message="完成")
    return {"job": job_name, "label": label, "status": "success"}


def main(target: str = "all", folder_id: str = "", area: str = "all", period: str = "", start: str = "", end: str = "") -> list[dict]:
    year = resolve_year(period=period, start=start)
    year_folder_id = get_or_create_year_folder(folder_id, year)
    print(f"📂 月排程總根目錄：{folder_id}", flush=True)
    print(f"📂 本次使用年度：{year}", flush=True)
    print(f"📂 本次年度 folder_id：{year_folder_id}", flush=True)

    targets = list(JOBS.keys()) if target == "all" else [target]
    results = []
    failed = []
    for job_name in targets:
        job = JOBS[job_name]
        root_env = job.get("root_env")
        if root_env:
            # 這些任務有自己獨立的雲端硬碟根目錄，不套用「{年}專員承攬服務費」這層包裝。
            # 環境變數優先；若當下執行環境沒設（例如 toolapp.py 直接 import 呼叫，
            # 不是透過 GitHub Actions），退回寫死的預設根目錄。
            job_folder_id = os.getenv(root_env, "").strip() or job.get("root_default", "")
            if not job_folder_id:
                failed.append({"job": job_name, "status": "failed", "message": f"缺少 {root_env}"})
                if target != "all":
                    raise RuntimeError(f"{job['label']} 缺少 {root_env}")
                continue
        else:
            job_folder_id = year_folder_id
        try:
            results.append(run_job(job_name, folder_id=job_folder_id, area=area, period=period, start=start, end=end))
        except Exception as exc:
            failed.append({"job": job_name, "status": "failed", "message": str(exc)})
            if target != "all":
                raise

    if failed:
        raise RuntimeError(f"月排程有失敗項目：{failed}")

    print("scheduled_monthly scheduler 全部完成", flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="all", choices=["all", *JOBS.keys()])
    parser.add_argument("--folder-id", default=os.getenv("MONTHLY_ROOT_FOLDER_ID", ""))
    parser.add_argument("--area", default="all")
    parser.add_argument("--period", default="")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    args = parser.parse_args()
    main(target=args.target, folder_id=args.folder_id, area=args.area, period=args.period, start=args.start, end=args.end)
