"""月初檢查：每月 1 日 02:00 起每小時（02:00～23:00）檢查 1 日該完成的工作，沒完成就在 Jenny 行事曆建會跳通知的提醒。

檢查項目：
  - 雲端月排程：預收、儲值金結算、儲值金預收（scheduled_monthly）、儲值金完整流程（scheduled_finance）
    → 看 repo 裡當天 logs/YYYYMMDD/*.json 的執行結果（沒有 json＝沒跑）
  - 固定費用：當月台北租金／管理費、單月台中租金是不是已新增到請款記錄
  - 月初藍新收退款、藍新發票金額、鯨躍發票、現金缺口：同【整合執行】月初藍新／鯨躍／現金缺口的盤點
    （排隊等 Mac、失敗、沒做都算未完成）
02:00 那次跟現金缺口同一個排程，先算完現金缺口再檢查（同一時段同一個 workflow 只能派送一次）。
全部完成就不建提醒。只提醒、不自動重跑：要人工驗證碼的步驟，等有空時再到整合執行按「盤點→執行」。
  - 富邦／元大明細：各區財報「富邦更新」「元大更新」最後一筆帳務日要到上個月最後一個工作天（現金缺口要用月底餘額）。
    台北／台中由本機 Agent 下載，會提醒、可補做；桃園／新竹／高雄由各區處理，只在檢查頁顯示，排程不提醒。
提醒只留最新一則：每次檢查先刪掉當天之前建的「月初工作未完成」提醒，全部完成就全部刪掉。
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
        entries.append({"label": label, "status": status, "fix": fix, "ok": ok,
                        "kind": "cloud", "key": name, "runnable": not ok})
    return entries


def month_start_entries(items: list[dict]) -> list[dict]:
    """月初整合執行的盤點結果：預設打勾（沒做／失敗）或不能勾（排隊等 Mac、執行中）都是未完成。"""
    return [{"label": i["label"], "status": i["status"], "fix": MONTH_START_FIX,
             "ok": not (i["default"] or not i["selectable"]),
             "kind": "month", "key": i["step"], "runnable": bool(i["default"] and i["selectable"])} for i in items]


def fixed_expense_entries(items: list[dict]) -> list[dict]:
    return [{"label": f"固定費用 {i['area']} {i['memo']}", "status": i["status"], "fix": FIXED_EXPENSE_FIX,
             "ok": not (i["default"] or i["status"] == "請款記錄讀取失敗"),
             "kind": "fixed", "item": i, "runnable": bool(i["default"])}
            for i in items if not i.get("info")]


# 富邦／元大明細：台北、台中由本機 Agent 下載（可提醒、可補做）；其他地區由各區處理，只顯示
STATEMENT_REPORTS = {"富邦更新": "fubon.download", "元大更新": "yuanta.download"}
AGENT_STATEMENT_AREAS = ("台北", "台中")
STATEMENT_FIX = "Tools App【富邦銀行】富邦明細下載／【元大銀行】元大明細下載，或這裡勾選後執行（排入本機 Agent）"
OTHER_STATEMENT_FIX = "由各區財務更新財報（Tools App 無法代為下載），排程不提醒"


def last_workday_before(day: date) -> date:
    """day（某月 1 日）的前一個工作天＝上個月最後一個工作天。"""
    from tools.common.tw_holidays import is_workday

    current = day - timedelta(days=1)
    while not is_workday(current):
        current -= timedelta(days=1)
    return current


def last_statement_date(values: list[list]) -> Optional[date]:
    from tools.finance_management.cash_gap import COL_B, _cell, _parse_date

    dates = [d for row in values[1:] if (d := _parse_date(_cell(row, COL_B)))]
    return max(dates) if dates else None


def statement_entries(day: date, read_values=None, target: Optional[date] = None) -> list[dict]:
    from tools.finance_management.cash_gap import CASH_GAP_AREAS, _report_values

    read_values = read_values or _report_values
    target = target or last_workday_before(day)
    entries = []
    for area in CASH_GAP_AREAS:
        mine = area in AGENT_STATEMENT_AREAS
        for report, action in STATEMENT_REPORTS.items():
            label = f"{area} {report[:2]}明細"
            try:
                last = last_statement_date(read_values(area, report))
                ok = bool(last and last >= target)
                status = (f"最後一筆 {last:%Y/%m/%d}" if last else "沒有資料") + ("" if ok else f"（要到 {target:%Y/%m/%d}）")
            except Exception as exc:
                ok, status = False, f"讀取失敗：{exc}"
            entries.append({"label": label, "status": status, "ok": ok, "kind": "statement",
                            "key": (area, action), "remind": mine, "runnable": mine and not ok,
                            "fix": STATEMENT_FIX if mine else OTHER_STATEMENT_FIX})
    return entries


def _problems(entries: list[dict]) -> list[dict]:
    """沒完成、而且排程要提醒的（桃園／新竹／高雄的富邦元大明細不提醒）。"""
    return [{k: e[k] for k in ("label", "status", "fix")} for e in entries
            if not e["ok"] and e.get("remind", True)]


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
    lines += ["", "1 日每小時會再檢查一次（到 23:00），行事曆只留最新一則；全部完成後提醒會自動刪除。"]
    return f"{REMINDER_PREFIX} {len(problems)} 項（{day:%m/%d}）", "\n".join(lines)


# 月初藍新／鯨躍 08:00 才排入（藍新約 08:00 寄手續費發票），之前不提醒這幾項
MONTH_START_QUEUE_HOUR = 8


def collect_all(day: date, log: Callable[[str], None] = print, include_month_start: bool = True) -> list[dict]:
    """day＝那個月的 1 日。回傳所有項目（含已完成，ok=True）。"""
    from tools.finance_management import fixed_expense_batch, month_start_batch

    entries = cloud_entries(day)
    try:
        entries += statement_entries(day)
    except Exception as exc:
        entries.append({"label": "富邦／元大明細", "status": f"檢查失敗：{exc}", "fix": STATEMENT_FIX, "ok": False, "kind": "error", "runnable": False})
    try:
        entries += fixed_expense_entries(fixed_expense_batch.scan(day.strftime("%Y%m"), log=log))
    except Exception as exc:
        entries.append({"label": "固定費用", "status": f"檢查失敗：{exc}", "fix": FIXED_EXPENSE_FIX, "ok": False, "kind": "error", "runnable": False})
    if not include_month_start:
        return entries
    try:
        items = month_start_batch.scan(month_start_batch.previous_period(day), log=log)
        entries += month_start_entries(items)
    except Exception as exc:
        entries.append({"label": "月初藍新／鯨躍／現金缺口", "status": f"檢查失敗：{exc}", "fix": MONTH_START_FIX,
                        "ok": False, "kind": "error", "runnable": False})
    return entries


def collect(day: date, log: Callable[[str], None] = print, hour: int = 23) -> list[dict]:
    """排程用：只回傳沒完成的；08:00 前不看月初藍新／鯨躍／現金缺口（還沒排入）。"""
    return _problems(collect_all(day, log=log, include_month_start=hour >= MONTH_START_QUEUE_HOUR))


REMINDER_PREFIX = "月初工作未完成"


def clear_reminders(day: date, log: Callable[[str], None] = print) -> int:
    """刪掉 day 當天之前建的「月初工作未完成」提醒（只留最新一則／全部完成就全刪）。"""
    from tools.orders_system import calendar_notify

    service = calendar_notify._calendar_service()
    start = datetime.combine(day, datetime.min.time(), tzinfo=TW_TZ)
    events = service.events().list(calendarId=REMIND_CALENDAR_ID, timeMin=start.isoformat(),
                                   timeMax=(start + timedelta(days=2)).isoformat(), q=REMINDER_PREFIX,
                                   singleEvents=True, maxResults=100).execute().get("items", [])
    removed = 0
    for event in events:
        if str(event.get("summary", "")).startswith(REMINDER_PREFIX):
            service.events().delete(calendarId=REMIND_CALENDAR_ID, eventId=event["id"]).execute()
            removed += 1
    if removed:
        log(f"已刪除 {removed} 則之前的月初提醒")
    return removed


def check(now: Optional[datetime] = None, log: Callable[[str], None] = print) -> list[dict]:
    now = now or datetime.now(TW_TZ)
    problems = collect(now.date(), log=log, hour=now.hour)
    try:
        clear_reminders(now.date(), log=log)
    except Exception as exc:
        log(f"⚠️ 刪除之前的月初提醒失敗：{exc}")
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


# ── 檢查完直接補做 ───────────────────────────────────
# 雲端排程 → 用 GitHub API 觸發 workflow（App secrets [github] 的 token 要有 Actions 寫入權限）
CLOUD_DISPATCH = {
    "monthly_prepaid_report": ("scheduled_monthly.yml", "prepaid_report"),
    "monthly_stored_value_settlement": ("scheduled_monthly.yml", "stored_value_settlement"),
    "monthly_stored_value_prepaid": ("scheduled_monthly.yml", "stored_value_prepaid"),
    "finance_vip_full_pipeline": ("scheduled_finance.yml", "vip_full_pipeline"),
}
VIP_SOURCES = {"monthly_stored_value_settlement", "monthly_stored_value_prepaid"}


def dispatch_inputs(key: str, day: date) -> tuple[str, dict]:
    from tools.finance_management.month_start_batch import previous_period

    workflow, target = CLOUD_DISPATCH[key]
    if workflow == "scheduled_monthly.yml":
        return workflow, {"target": target, "period": previous_period(day) + "-2"}
    return workflow, {"target": target, "date": day.isoformat()}


def dispatch_workflow(workflow: str, inputs: dict) -> None:
    import requests

    owner, repo, token, branch = _github_config()
    if not (owner and repo and token):
        raise RuntimeError("App 沒有設定 GitHub token（secrets [github] owner／repo／token），請到 GitHub Actions 手動執行")
    response = requests.post(
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches",
        json={"ref": branch, "inputs": inputs}, timeout=30,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    if response.status_code != 204:
        raise RuntimeError(f"GitHub 觸發失敗（HTTP {response.status_code}）：{response.text[:200]}")


def plan_run(entries: list[dict]) -> tuple[list[dict], list[str]]:
    """勾選的項目 → (實際要做的, 提醒)。儲值金完整流程要等結算／預收的檔案，同一次勾了就先不做。"""
    selected = [e for e in entries if e.get("runnable")]
    notes = []
    cloud_keys = {e["key"] for e in selected if e["kind"] == "cloud"}
    if "finance_vip_full_pipeline" in cloud_keys and cloud_keys & VIP_SOURCES:
        selected = [e for e in selected if e.get("key") != "finance_vip_full_pipeline"]
        notes.append("儲值金完整流程要等儲值金結算／預收跑完（約 2 分鐘）再回來檢查、執行")
    return selected, notes


def run_selected(entries: list[dict], day: date, create_task, log: Callable[[str], None] = print,
                 dispatch=dispatch_workflow, created_by: str = "月初檢查") -> list[str]:
    """補做勾選的未完成項目：雲端排程觸發 GitHub workflow、固定費用直接新增、
    藍新／鯨躍排入本機 Agent；只有現金缺口（不用等藍新）時直接在這裡算。回傳結果訊息。"""
    from tools.finance_management import month_start_batch

    selected, messages = plan_run(entries)
    for entry in (e for e in selected if e["kind"] == "cloud"):
        workflow, inputs = dispatch_inputs(entry["key"], day)
        try:
            dispatch(workflow, inputs)
            messages.append(f"✅ {entry['label']}：已觸發 GitHub {workflow}（target {inputs['target']}），約幾分鐘後完成")
        except Exception as exc:
            messages.append(f"❌ {entry['label']}：{exc}")
    for entry in (e for e in selected if e["kind"] == "statement"):
        area, action = entry["key"]
        params = {"start_from": "sheet", "end_date": datetime.now(TW_TZ).strftime("%Y-%m-%d"), "area": area,
                  "cdp_url": "http://127.0.0.1:9222"}
        if action == "fubon.download":
            params["resume"] = "download"
        try:
            task = create_task(action, params, created_by=created_by)
            messages.append(f"✅ {entry['label']}：已排入本機 Agent（{task.get('task_id', '')}），富邦需手機核准")
        except Exception as exc:
            messages.append(f"❌ {entry['label']}：{exc}")
    fixed = [e["item"] for e in selected if e["kind"] == "fixed"]
    if fixed:
        from tools.finance_management.fixed_expense_batch import submit

        try:
            added = submit(fixed, run_type="月初檢查補做", log=log)
            messages.append("✅ 固定費用：" + "、".join(f"{a} 新增 {n} 筆" for a, n in added.items()))
        except Exception as exc:
            messages.append(f"❌ 固定費用：{exc}")
    steps = [e["key"] for e in selected if e["kind"] == "month"]
    period = month_start_batch.previous_period(day)
    if steps == ["cash_gap"]:
        code = month_start_batch._run_step("cash_gap", period, "")
        messages.append("✅ 現金缺口：已重新試算" if code == 0 else "❌ 現金缺口：試算失敗")
    elif steps:
        task = month_start_batch.queue_steps(period, steps, create_task, created_by=created_by, log=log)
        labels = "→".join(month_start_batch.STEPS[s][0] for s in month_start_batch.STEP_ORDER if s in steps)
        messages.append(f"✅ {labels}：已排入本機 Agent（{(task or {}).get('task_id', '')}），藍新／鯨躍要到 Mac 輸入驗證碼")
    for message in messages:
        log(message)
    return messages
