"""排程完成記行事曆（2026-11-16 Jenny）：主控表「排程設定」的功能跑完後，在排程日曆留一則紀錄
（2026-11-23 起：SCHEDULE_NOTICE_CALENDAR_ID，讀不到時用 calendar_notify.DEFAULT_NOTICE_CALENDAR_ID，不再寫 jenny@hers.com.tw）。

Cloud Schedule Router 每 10 分鐘跑一次，派送完這個時段的功能後接著跑本程式：
  1. 讀「排程設定」，K 欄「完成記行事曆」是 TRUE 的功能（空白＝預設，高頻功能預設不記）。
  2. 到各 repo 查最近 LOOKBACK_HOURS 小時內由 Router 派送（run 標題 cloud-<時段>-<功能代號>）且已結束的 run。
  3. 還沒記過的（用事件的 extendedProperties.private.schedule_run 去重）就建一則行事曆事件：
       標題  【排程】功能名稱 ✅／❌ 失敗／⚠️ 取消
       內容  執行結果（從 GitHub 執行日誌挑出程式印的結果行：新增幾筆、略過、⚠️／❌ 訊息…，濾掉安裝套件、
           警告、Traceback 等雜訊）；最後附排程時段、執行時間、失敗的步驟、GitHub 連結
     時間＝run 完成的時間，30 分鐘，不跳提醒（只留紀錄；要處理的事各功能本來就會另建提醒）。

一個功能記失敗不影響其他功能；讀不到某 repo（沒設 token）就略過那個 repo。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
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
# 2026-11-24 Jenny：程式自己會把結果記行事曆的功能，成功時不再另記【排程】（避免重複）；失敗仍記。
# 富邦明細排程只排入 Mac，結果由 Agent 下載完記「【富邦明細】…」。
SELF_RECORDED_KEYS = {
    "stored_value_notice_daily",   # 【儲值金客服備註空白】
    "customer_memo_daily",         # 【客人備註】
    "rating_complaint_daily",      # 【評價客訴】
    "rating_booking_daily",        # 【評價預約】
    "lemon_ren_count",             # 【檸檬人待換專員】
    "unassigned_staff_record",     # 【未配班剩1人】
    "unassigned_reserve_create",   # 【未配班保留單】
    "fubon_statement",             # 【富邦明細】（Mac Agent 下載完記）
}
# 2026-11-24 Jenny：這些功能的【排程】紀錄仍記 jenny@hers.com.tw（不轉到排程行事曆）
HERS_KEYS = {"invest_snapshot"}


def skip_record(rule_key: str, run: dict) -> bool:
    return rule_key in SELF_RECORDED_KEYS and run.get("conclusion") in ("success", "neutral", "skipped")


def github_api(path: str, repo: str = "", token: str = "") -> dict:
    req = urllib.request.Request(
        "https://api.github.com/repos/" + (repo or os.environ["GITHUB_REPOSITORY"]) + path,
        headers={"Authorization": "Bearer " + (token or os.environ["GH_TOKEN"]),
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else {}


MAX_RESULT_LINES = 15
# 程式自己印的時間戳「[2026-11-17 08:01:21] 」
_INLINE_TS_RE = re.compile(r"^\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?\]?\s*")
_URL_RE = re.compile(r"\s*(\S+=)?https?://\S+")
_HTTP_ERROR_RE = re.compile(r"<HttpError (\d+).*")
# 過程細節（抓取、解析、除錯輸出、段落標題）不算結果（2026-11-17 Jenny：只顯示結果）
_PROCESS_RE = re.compile(
    r"抓取|parse_html|columns|前\s*\d+\s*筆\s*=|開始|^=+.*=+$|^(QUERY|WINDOW|DEBUG|INFO)\b|讀取中|查詢中|下載中|登入中"
    r"|=\s*[\[{]|\*\*\*'")
_REGION_LINE_RE = re.compile(r"^[^\s／：]{1,6}／[^：]+：")
# 結果行：有狀態符號或「完成／新增／失敗…」等字
_OUTCOME_RE = re.compile(r"✅|❌|⚠️|完成|新增|成功|失敗|錯誤|略過|已|寄出|貼入|補齊|未備妥|FINISH|SAVE|筆數|沒有|找到")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z ?")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# 有中日文、全形符號或狀態符號的行才當成「結果」（程式給人看的訊息）
_RESULT_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef✅❌⚠️⏭🔁📌]")
# 準備環境的步驟（##[group]Run 後面的指令）整段略過
_SETUP_RUN_RE = re.compile(r"^(actions/|pip |python -m pip|git |apt|sudo |npm |playwright install|echo |mkdir )")
_NOISE_RE = re.compile(
    r"missing ScriptRunContext|Session state does not function|DeprecationWarning|^\s*(File \"|\^|Traceback)"
    r"|^\s{2,}|^\[command\]|Requirement already|^Using cached|^Collecting|^Downloading|^Successfully installed"
    r"|^[=\-─━*#~]{3,}\s*$|版本：|^(GitHub 事件|GitHub cron|Log 目錄|Drive 根目錄|台北時間|執行任務|開始時間|結束時間|執行秒數)："
    r"|Log 已提交|沒有新的 Log 需要提交|沒有 logs 目錄|^\[登入\] 已登入|檸檬後台已登出$"
    r"|已設定：|^(✅\s*)?已設定")


def fetch_job_log(job_id, repo: str = "", token: str = "") -> str:
    """下載一個 job 的純文字日誌（GitHub 會轉址到暫存網址，Authorization 不能跟著轉過去）。"""
    req = urllib.request.Request(
        "https://api.github.com/repos/" + (repo or os.environ["GITHUB_REPOSITORY"]) + f"/actions/jobs/{job_id}/logs",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    req.add_unredirected_header("Authorization", "Bearer " + (token or os.environ["GH_TOKEN"]))
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def result_lines(log_text: str, limit: int = MAX_RESULT_LINES) -> list[str]:
    """從 job 日誌挑出程式印的結果行：只看主要步驟（略過 checkout、安裝套件、git commit、Post 清理），
    略過指令回顯／env 區塊與雜訊，保留有中文或 ✅❌⚠️ 的行與 ##[error]。太長時留前 5 行＋最後幾行。"""
    lines, skip_step, in_group = [], False, False
    for raw in str(log_text or "").splitlines():
        line = _ANSI_RE.sub("", _TS_RE.sub("", raw)).rstrip()
        if line.startswith("Post job cleanup") or line.startswith("##[group]Post "):
            break
        if line.startswith("##[group]"):
            in_group = True
            cmd = line[len("##[group]"):]
            if cmd.startswith("Run "):
                skip_step = bool(_SETUP_RUN_RE.match(cmd[4:].strip()))
            continue
        if line.startswith("##[endgroup]"):
            in_group = False
            continue
        if in_group or skip_step:
            continue
        if line.startswith("##[error]"):
            line = "❌ " + line[len("##[error]"):]
        elif line.startswith("##["):
            continue
        text = line.strip()
        if not text or _NOISE_RE.search(line) or not _RESULT_RE.search(text):
            continue
        text = _INLINE_TS_RE.sub("", text)
        if not text or _NOISE_RE.search(text) or _PROCESS_RE.search(text):
            continue
        text = _HTTP_ERROR_RE.sub(r"HttpError \1", text)
        text = _URL_RE.sub("", text).strip()[:200]
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    # 程式有印「台北／客人備註：…」這種每區每種一行的結果時，SAVE／FINISH 摘要行就不重複列
    # 2026-11-24 Jenny：有這種結果行時只留結果行與 ❌／⚠️（過程訊息如「某週找到 0 筆」不列，行事曆只看結果）
    if any(_REGION_LINE_RE.match(t) for t in lines):
        lines = [t for t in lines if _REGION_LINE_RE.match(t) or t.startswith(("❌", "⚠️"))]
    else:
        outcomes = [t for t in lines if _OUTCOME_RE.search(t)]
        lines = outcomes or lines[-3:]
    if len(lines) > limit:
        # 太多時：❌／⚠️ 全留（最多 limit 行），其餘留最後幾行，順序不變
        keep = {i for i, t in enumerate(lines) if t.startswith(("❌", "⚠️"))}
        keep = set(sorted(keep)[:limit])
        for i in range(len(lines) - 1, -1, -1):
            if len(keep) >= limit - 1:
                break
            keep.add(i)
        skipped = len(lines) - len(keep)
        lines = [f"…（另有 {skipped} 行，見 GitHub）"] + [t for i, t in enumerate(lines) if i in keep]
    return lines


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


def failed_steps(jobs: Iterable[dict]) -> list[str]:
    return [line for line in step_lines(jobs) if not line.startswith("✅")]


def build_event(rule: ss.Rule, run: dict, slot: str, repo: str, jobs: list[dict],
                results: Optional[list[str]] = None) -> dict:
    conclusion = run.get("conclusion") or "neutral"
    mark = CONCLUSION_MARK.get(conclusion, conclusion)
    started, finished = _local(run.get("run_started_at") or run.get("created_at")), _local(run.get("updated_at"))
    finished = finished or dt.datetime.now(TZ)
    lines = ["執行結果："] + (results or ["（程式沒有印出結果訊息，詳見 GitHub 執行紀錄）"])
    failed = failed_steps(jobs)
    if failed:
        lines += ["", "失敗的步驟："] + failed
    lines += ["", f"{mark}　{rule.key}（{ss.system_name(rule.key) or repo}）",
              f"排程時段：{_slot_text(slot)}"]
    if started:
        minutes = max(0, round((finished - started).total_seconds() / 60))
        lines.append(f"執行：{started:%H:%M}～{finished:%H:%M}（約 {minutes} 分鐘）")
    if run.get("html_url"):
        lines.append(f"GitHub：{run['html_url']}")
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


def run_results(jobs: list[dict], repo: str, fetch_log: Callable = fetch_job_log,
                log: Callable[[str], None] = print) -> list[str]:
    results = []
    for job in jobs:
        try:
            text = fetch_log(job["id"], repo=repo, token=_repo_token(repo))
        except Exception as exc:  # 讀不到日誌就只記步驟
            log(f"::warning::完成記行事曆：讀不到 job {job.get('id')} 日誌：{exc}")
            continue
        found = result_lines(text)
        if found and len(jobs) > 1:
            found = [f"【{job.get('name', '')}】"] + found
        results += found
    return results[:MAX_RESULT_LINES + 10]


def record(now: Optional[dt.datetime] = None, rules: Optional[list[ss.Rule]] = None, api: Callable = github_api,
           service=None, calendar_id: str = "", log: Callable[[str], None] = print,
           fetch_log: Callable = fetch_job_log) -> int:
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    since = now - dt.timedelta(hours=LOOKBACK_HOURS)
    if rules is None:
        rules, _ok = ss.load_rules(log=log)
    runs = completed_runs(rules, since, api=api, log=log)
    if not runs:
        log("完成記行事曆：沒有新完成的排程")
        return 0
    targets: dict = {}

    def target(keep: bool):
        """(service, 行事曆 ID, 已記過的 run)；keep＝記在 jenny@hers.com.tw、不轉排程行事曆。"""
        if keep not in targets:
            if service is not None:
                svc, cid = service, calendar_id or RECORD_CALENDAR_ID
            else:
                from tools.orders_system import calendar_notify

                svc, cid = calendar_notify.calendar_for(RECORD_CALENDAR_ID, keep=keep)
            targets[keep] = (svc, cid, recorded_ids(svc, cid, since - dt.timedelta(days=1)))
        return targets[keep]

    count = 0
    for rule, run, slot, repo in runs:
        if skip_record(rule.key, run):
            continue
        svc, cal_id, done = target(rule.key in HERS_KEYS)
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
            results = run_results(jobs, repo, fetch_log=fetch_log, log=log)
            body = build_event(rule, run, slot, repo, jobs, results)
            svc.events().insert(calendarId=cal_id, body=body, sendUpdates="none").execute()
        except Exception as exc:
            log(f"::warning::完成記行事曆：{rule.key} 建立事件失敗：{exc}")
            continue
        done.add(rid)
        count += 1
        log(f"已在 {cal_id} 行事曆記錄：{body['summary']}")
    return count


def main() -> None:
    record()


if __name__ == "__main__":
    main()
