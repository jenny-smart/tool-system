"""排程完成記行事曆（2026-11-16 Jenny）：主控表「排程設定」的功能跑完後，在 jenny@hers.com.tw 行事曆留一則紀錄。

Cloud Schedule Router 每 10 分鐘跑一次，派送完這個時段的功能後接著跑本程式：
  1. 讀「排程設定」，K 欄「完成記行事曆」是 TRUE 的功能（空白＝預設，高頻功能預設不記）。
  2. 到各 repo 查最近 LOOKBACK_HOURS 小時內由 Router 派送（run 標題 cloud-<時段>-<功能代號>）且已結束的 run。
  3. 還沒記過的（用事件的 extendedProperties.private.schedule_run 去重）就建一則行事曆事件：
       標題  【排程】功能名稱 ✅／❌ 失敗／⚠️ 取消
       內容  功能代號／系統、排程時段、開始～完成、執行內容（各 job 的步驟，失敗的標 ❌）、備註、GitHub 連結
     時間＝run 完成的時間，30 分鐘，不跳提醒（只留紀錄；要處理的事各功能本來就會另建提醒）。

一個功能記失敗不影響其他功能；讀不到某 repo（沒設 token）就略過那個 repo。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.scheduling import schedule_settings as ss  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
RECORD_CALENDAR_ID = "jenny@hers.com.tw"
TITLE_PREFIX = "【排程】"
LOOKBACK_HOURS = 6
EVENT_MINUTES = 30
PROPERTY = "schedule_run"
# 準備環境的步驟不列進「執行內容」
_SETUP_STEPS = ("Set up job", "Complete job", "Checkout", "Set up Python", "Setup Python", "Install",
                "Verify", "Post ", "Commit", "actions/", "Run actions/")
CONCLUSION_MARK = {"success": "✅", "failure": "❌ 失敗", "cancelled": "⚠️ 取消", "timed_out": "❌ 逾時",
                   "skipped": "⏭ 略過", "neutral": "✅", "action_required": "⚠️ 待處理", "startup_failure": "❌ 啟動失敗"}


def github_api(path: str, repo: str = "", token: str = "") -> dict:
    req = urllib.request.Request(
        "https://api.github.com/repos/" + (repo or os.environ["GITHUB_REPOSITORY"]) + path,
        headers={"Authorization": "Bearer " + (token or os.environ["GH_TOKEN"]),
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else {}


def parse_title(title: str) -> Optional[tuple[str, str]]:
    """「cloud-20261116T0700-notify」→ ("20261116T0700", "notify")；不是 Router 派送的回傳 None。"""
    if not str(title or "").startswith("cloud-") or str(title).startswith("cloud-router-"):
        return None
    rest = title[len("cloud-"):]
    slot, sep, key = rest.partition("-")
    if not sep or len(slot) != 13 or "T" not in slot or not key:
        return None
    return slot, key


def _local(text: str) -> Optional[dt.datetime]:
    if not text:
        return None
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(TZ)


def _slot_text(slot: str) -> str:
    try:
        return dt.datetime.strptime(slot, "%Y%m%dT%H%M").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return slot


def step_lines(jobs: Iterable[dict]) -> list[str]:
    """各 job 的實際執行步驟（略過 checkout／安裝等準備步驟），失敗的標 ❌。"""
    lines = []
    for job in jobs:
        steps = [s for s in job.get("steps") or []
                 if not any(str(s.get("name", "")).startswith(p) for p in _SETUP_STEPS)
                 and s.get("conclusion") not in ("skipped", None)]
        for step in steps:
            mark = "✅" if step.get("conclusion") == "success" else CONCLUSION_MARK.get(step.get("conclusion"), "❔")
            lines.append(f"{mark.split()[0]} {step.get('name', '')}")
        if job.get("conclusion") not in ("success", "skipped") and not steps:
            lines.append(f"{CONCLUSION_MARK.get(job.get('conclusion'), '❔').split()[0]} {job.get('name', '')}")
    return lines


def build_event(rule: ss.Rule, run: dict, slot: str, repo: str, jobs: list[dict]) -> dict:
    conclusion = run.get("conclusion") or "neutral"
    mark = CONCLUSION_MARK.get(conclusion, conclusion)
    started, finished = _local(run.get("run_started_at") or run.get("created_at")), _local(run.get("updated_at"))
    finished = finished or dt.datetime.now(TZ)
    lines = [f"功能：{rule.name}（{rule.key}）",
             f"系統：{ss.system_name(rule.key) or repo}",
             f"結果：{mark}",
             f"排程時段：{_slot_text(slot)}（{rule.freq}{(' ' + rule.days) if rule.days else ''}，{rule.mode}）"]
    if started:
        minutes = max(0, round((finished - started).total_seconds() / 60))
        lines.append(f"執行：{started:%H:%M}～{finished:%H:%M}（約 {minutes} 分鐘）")
    steps = step_lines(jobs)
    if steps:
        lines += ["", "執行內容："] + steps
    if rule.note:
        lines += ["", f"備註：{rule.note}"]
    if run.get("html_url"):
        lines += ["", f"GitHub：{run['html_url']}"]
    start = finished
    return {
        "summary": f"{TITLE_PREFIX}{rule.name} {mark}",
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Taipei"},
        "end": {"dateTime": (start + dt.timedelta(minutes=EVENT_MINUTES)).isoformat(), "timeZone": "Asia/Taipei"},
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {"private": {PROPERTY: run_id(repo, run)}},
    }


def run_id(repo: str, run: dict) -> str:
    return f"{repo or os.environ.get('GITHUB_REPOSITORY', '')}#{run.get('id')}"


def recorded_ids(service, calendar_id: str, since: dt.datetime) -> set[str]:
    ids, page = set(), None
    while True:
        resp = service.events().list(calendarId=calendar_id, timeMin=since.isoformat(), singleEvents=True,
                                     q=TITLE_PREFIX, maxResults=250, pageToken=page).execute()
        for event in resp.get("items", []):
            value = ((event.get("extendedProperties") or {}).get("private") or {}).get(PROPERTY)
            if value:
                ids.add(value)
        page = resp.get("nextPageToken")
        if not page:
            return ids


def _repo_token(repo: str) -> str:
    if not repo:
        return os.environ.get("GH_TOKEN", "")
    secret = ss.EXTERNAL_DISPATCH_TOKENS.get(repo, "")
    return os.environ.get(secret, "") if secret else ""


def completed_runs(rules: list[ss.Rule], since: dt.datetime, api: Callable = github_api,
                   log: Callable[[str], None] = print) -> list[tuple[ss.Rule, dict, str, str]]:
    """[(rule, run, slot, repo)]：since 之後由 Router 派送、已結束、K 欄要記的 run。"""
    by_key = {r.key: r for r in rules if r.record_calendar and r.key in ss.FUNCTIONS}
    workflows: dict[tuple[str, str], None] = {}
    for key in by_key:
        repo, name = ss.split_workflow(ss.FUNCTIONS[key][0])
        workflows[(repo, name)] = None
    found = []
    query = urllib.parse.urlencode({"event": "workflow_dispatch", "status": "completed", "per_page": 100,
                                    "created": ">=" + since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    for repo, name in workflows:
        token = _repo_token(repo)
        if not token:
            log(f"::warning::完成記行事曆：{repo} 沒有 token，略過 {name}")
            continue
        try:
            runs = api(f"/actions/workflows/{name}/runs?{query}", repo=repo, token=token).get("workflow_runs", [])
        except Exception as exc:  # 單一 workflow 查不到不影響其他
            log(f"::warning::完成記行事曆：查 {repo or '本 repo'} {name} 失敗：{exc}")
            continue
        for run in runs:
            parsed = parse_title(run.get("display_title", ""))
            if not parsed or parsed[1] not in by_key:
                continue
            rule = by_key[parsed[1]]
            if ss.split_workflow(ss.FUNCTIONS[rule.key][0]) != (repo, name):
                continue
            found.append((rule, run, parsed[0], repo))
    return found


def record(now: Optional[dt.datetime] = None, rules: Optional[list[ss.Rule]] = None, api: Callable = github_api,
           service=None, calendar_id: str = "", log: Callable[[str], None] = print) -> int:
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    since = now - dt.timedelta(hours=LOOKBACK_HOURS)
    if rules is None:
        rules, _ok = ss.load_rules(log=log)
    runs = completed_runs(rules, since, api=api, log=log)
    if not runs:
        log("完成記行事曆：沒有新完成的排程")
        return 0
    if service is None:
        from tools.orders_system import calendar_notify

        service, calendar_id = calendar_notify.calendar_for(RECORD_CALENDAR_ID)
    calendar_id = calendar_id or RECORD_CALENDAR_ID
    done = recorded_ids(service, calendar_id, since - dt.timedelta(days=1))
    count = 0
    for rule, run, slot, repo in runs:
        rid = run_id(repo, run)
        if rid in done:
            continue
        try:
            jobs = api(f"/actions/runs/{run['id']}/jobs?per_page=100", repo=repo,
                       token=_repo_token(repo)).get("jobs", [])
        except Exception as exc:
            log(f"::warning::完成記行事曆：讀不到 {rule.key} 的步驟：{exc}")
            jobs = []
        try:
            body = build_event(rule, run, slot, repo, jobs)
            service.events().insert(calendarId=calendar_id, body=body, sendUpdates="none").execute()
        except Exception as exc:
            log(f"::warning::完成記行事曆：{rule.key} 建立事件失敗：{exc}")
            continue
        done.add(rid)
        count += 1
        log(f"已在 {calendar_id} 行事曆記錄：{body['summary']}")
    return count


def main() -> None:
    record()


if __name__ == "__main__":
    main()
