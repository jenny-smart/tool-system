"""月初檢查：每月 1 日 02:00 起每小時（02:00～23:00）檢查 1 日該完成的工作，沒完成就在 Jenny 行事曆建會跳通知的提醒。

檢查項目：
  - 雲端月排程：預收、儲值金結算、儲值金預收（scheduled_monthly）、儲值金完整流程（scheduled_finance）
    → 看 repo 裡當天 logs/YYYYMMDD/*.json 的執行結果（沒有 json＝沒跑）
  - 固定費用：當月台北租金／管理費、單月台中租金是不是已新增到請款記錄
  - 月初藍新收退款、藍新發票金額、鯨躍發票、現金缺口：同【整合執行】月初藍新／鯨躍／現金缺口的盤點
    （排隊等 Mac、失敗、沒做都算未完成）
02:00 那次跟現金缺口同一個排程，先算完現金缺口再檢查（同一時段同一個 workflow 只能派送一次）。
全部完成就不建提醒。只提醒、不自動重跑：要人工驗證碼的步驟，等有空時再到整合執行按「盤點→執行」。
Tools App【整合執行】月初檢查（1日所有排程）也用這裡的 collect_all()，按一下就列出所有項目的狀態。
雲端排程結果優先從 GitHub 讀 main 上的 logs（App 裡的 repo 不一定是最新），讀不到才看本機 logs。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")
REMIND_CALENDAR_ID = "jenny@lemonclean.com.tw"
LOG_ROOT = Path(__file__).resolve().parents[2] / "logs"

# (顯示名稱, logs 裡的 json 名稱, 補跑方式)
CLOUD_JOBS = [
    ("預收", "monthly_prepaid_report", "GitHub Actions → Scheduled Monthly Jobs → target prepaid_report"),
    ("儲值金結算", "monthly_stored_value_settlement",
     "GitHub Actions → Scheduled Monthly Jobs → target stored_value_settlement"),
    ("儲值金預收", "monthly_stored_value_prepaid",
     "GitHub Actions → Scheduled Monthly Jobs → target stored_value_prepaid"),
    ("儲值金完整流程", "finance_vip_full_pipeline",
     "Tools App【儲值金】完整流程，或 GitHub Actions → Scheduled Finance Jobs → target vip_full_pipeline"),
]
MONTH_START_FIX = "Tools App【整合執行】月初藍新／鯨躍／現金缺口 → 盤點 → 執行"
FIXED_EXPENSE_FIX = "Tools App【整合執行】固定費用（辦公室租金）→ 盤點 → 新增"


def _github_config() -> tuple[str, str, str, str]:
    """App 的 secrets [github] owner／repo／token／branch（沒設定就用環境變數）。"""
    import os

    values = {"owner": os.getenv("GITHUB_OWNER", ""), "repo": os.getenv("GITHUB_REPO", ""),
              "token": os.getenv("GITHUB_TOKEN", ""), "branch": os.getenv("GITHUB_BRANCH", "") or "main"}
    try:
        import streamlit as st

        section = st.secrets.get("github", {}) or {}
        for key in values:
            values[key] = str(section.get(key, "") or values[key])
    except Exception:
        pass
    return values["owner"], values["repo"], values["token"], values["branch"]


def _read_github_log(day: date, name: str) -> Optional[dict]:
    """從 GitHub 讀 main 上的 logs；沒設定 GitHub token 或讀不到回傳 None。"""
    owner, repo, token, branch = _github_config()
    if not (owner and repo and token):
        return None
    import base64

    import requests

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/logs/{day:%Y%m%d}/{name}.json"
    try:
        response = requests.get(url, params={"ref": branch}, timeout=20, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        if response.status_code != 200:
            return None
        return json.loads(base64.b64decode(response.json()["content"]).decode("utf-8"))
    except Exception:
        return None


def read_job_log(day: date, name: str, root: Path = LOG_ROOT) -> Optional[dict]:
    result = _read_github_log(day, name)
    if result is not None:
        return result
    path = root / day.strftime("%Y%m%d") / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cloud_entries(day: date, read=read_job_log) -> list[dict]:
    entries = []
    for label, name, fix in CLOUD_JOBS:
        result = read(day, name)
        if result is None:
            status, ok = "沒有執行紀錄", False
        elif result.get("status") != "success":
            status, ok = f"失敗（exit {result.get('exit_code', '?')}）", False
        else:
            status, ok = f"成功（{result.get('finished_at', '')}）", True
        entries.append({"label": label, "status": status, "fix": fix, "ok": ok})
    return entries


def month_start_entries(items: list[dict]) -> list[dict]:
    """月初整合執行的盤點結果：預設打勾（沒做／失敗）或不能勾（排隊等 Mac、執行中）都是未完成。"""
    return [{"label": i["label"], "status": i["status"], "fix": MONTH_START_FIX,
             "ok": not (i["default"] or not i["selectable"])} for i in items]


def fixed_expense_entries(items: list[dict]) -> list[dict]:
    return [{"label": f"固定費用 {i['area']} {i['memo']}", "status": i["status"], "fix": FIXED_EXPENSE_FIX,
             "ok": not (i["default"] or i["status"] == "請款記錄讀取失敗")}
            for i in items if not i.get("info")]


def _problems(entries: list[dict]) -> list[dict]:
    return [{k: e[k] for k in ("label", "status", "fix")} for e in entries if not e["ok"]]


def cloud_problems(day: date, read=read_job_log) -> list[dict]:
    return _problems(cloud_entries(day, read))


def month_start_problems(items: list[dict]) -> list[dict]:
    return _problems(month_start_entries(items))


def fixed_expense_problems(items: list[dict]) -> list[dict]:
    return _problems(fixed_expense_entries(items))


def build_reminder(day: date, now: datetime, problems: list[dict]) -> tuple[str, str]:
    lines = [f"{now:%Y/%m/%d %H:%M} 檢查，{len(problems)} 項還沒完成：", ""]
    for p in problems:
        lines.append(f"・{p['label']}：{p['status']}")
    lines += ["", "處理方式："]
    for fix in dict.fromkeys(p["fix"] for p in problems):
        lines.append(f"・{fix}")
    lines += ["", "1 日每小時會再檢查一次（到 23:00），全部完成就不再提醒。"]
    return f"月初工作未完成 {len(problems)} 項（{day:%m/%d}）", "\n".join(lines)


# 月初藍新／鯨躍 09:00 才排入（藍新 08:00 才寄手續費發票），之前不提醒這幾項
MONTH_START_QUEUE_HOUR = 9


def collect_all(day: date, log: Callable[[str], None] = print, include_month_start: bool = True) -> list[dict]:
    """day＝那個月的 1 日。回傳所有項目（含已完成，ok=True）。"""
    from tools.finance_management import fixed_expense_batch, month_start_batch

    entries = cloud_entries(day)
    try:
        entries += fixed_expense_entries(fixed_expense_batch.scan(day.strftime("%Y%m"), log=log))
    except Exception as exc:
        entries.append({"label": "固定費用", "status": f"檢查失敗：{exc}", "fix": FIXED_EXPENSE_FIX, "ok": False})
    if not include_month_start:
        return entries
    try:
        items = month_start_batch.scan(month_start_batch.previous_period(day), log=log)
        entries += month_start_entries(items)
    except Exception as exc:
        entries.append({"label": "月初藍新／鯨躍／現金缺口", "status": f"檢查失敗：{exc}", "fix": MONTH_START_FIX,
                        "ok": False})
    return entries


def collect(day: date, log: Callable[[str], None] = print, hour: int = 23) -> list[dict]:
    """排程用：只回傳沒完成的；09:00 前不看月初藍新／鯨躍／現金缺口（還沒排入）。"""
    return _problems(collect_all(day, log=log, include_month_start=hour >= MONTH_START_QUEUE_HOUR))


def check(now: Optional[datetime] = None, log: Callable[[str], None] = print) -> list[dict]:
    now = now or datetime.now(TW_TZ)
    problems = collect(now.date(), log=log, hour=now.hour)
    if not problems:
        log("月初工作全部完成，不建提醒 🎉")
        return []
    title, body = build_reminder(now.date(), now, problems)
    log(title + "\n" + body)
    from tools.orders_system import calendar_notify

    # 開始時間留 30 分鐘前導，Google 的提醒推播才排得進去（20、10 分鐘前各跳一次）
    calendar_notify.create_notify_event(summary=title, description=body, start=now + timedelta(minutes=30),
                                        calendar_id=REMIND_CALENDAR_ID, attendee="", reminder_minutes=[20, 10])
    log(f"已在 {REMIND_CALENDAR_ID} 行事曆建提醒")
    return problems
