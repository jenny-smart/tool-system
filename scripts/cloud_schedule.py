"""Route externally timed slots in Asia/Taipei; never infer slots from runner start."""
import argparse
import sys
from pathlib import Path
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = ZoneInfo('Asia/Taipei')

def scheduled_time(stamp):
    """Allow up to two minutes of clock skew around ten-minute slots."""
    slot_stamp = ((stamp + 300) // 600) * 600
    if abs(stamp - slot_stamp) > 120:
        raise ValueError("雲端時間戳未落在原定時段前後兩分鐘內")
    return dt.datetime.fromtimestamp(slot_stamp, TZ)

def _is_workday(day):
    from tools.common.tw_holidays import is_workday

    return is_workday(day)


def plan(when, rules=None, is_workday=None):
    """這個時段要派送的 [(workflow 檔, inputs, 功能代號)]。規則來自主控表「排程設定」（讀不到用預設）。"""
    from tools.scheduling import schedule_settings as ss

    when = when.astimezone(TZ)
    if rules is None:
        rules, _from_sheet = ss.load_rules()
    jobs = []
    for rule in ss.due(rules, when, is_workday or _is_workday):
        workflow, inputs = ss.dispatch_for(rule, when)
        jobs.append((workflow, inputs, rule.key))
    return jobs

def api(path, payload=None):
    req = urllib.request.Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else {}

def validate_delivery(when, now):
    now = now.astimezone(TZ)
    age = (now - when).total_seconds()
    if now.date() != when.date():
        raise SystemExit("排程已跨日期，請確認資料日期與期別後補跑；未自動執行。")
    if age < -1800:
        raise SystemExit("排程時間超前超過 30 分鐘，請檢查時間戳。")
    if age > 1800:
        print("::warning::排程延遲超過 30 分鐘，仍執行同日原定任務。")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--timestamp', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    stamp = int(args.timestamp)
    now = dt.datetime.now(dt.timezone.utc)
    when = scheduled_time(stamp)
    if not args.dry_run:
        validate_delivery(when, now)
    jobs = plan(when)
    slot = when.strftime('%Y%m%dT%H%M')
    print('Taipei slot:', when.isoformat(), 'tasks:', jobs)
    if args.dry_run:
        return
    for workflow, inputs, key in jobs:
        # 每個功能各自去重：同時段同一個 workflow 的不同功能都會派送
        job_slot = slot + '-' + key
        title = 'cloud-' + job_slot
        # One router concurrency group serializes retries. Check every returned page;
        # any existing run counts, including failed jobs (operator can rerun them).
        query = urllib.parse.urlencode({'event': 'workflow_dispatch', 'created': '>=' + when.astimezone(dt.timezone.utc).isoformat(), 'per_page': 100})
        found = False
        for page in range(1, 101):
            runs = api('/actions/workflows/' + workflow + '/runs?' + query + '&page=' + str(page))['workflow_runs']
            if any(run['display_title'] == title for run in runs):
                found = True
                break
            if len(runs) < 100:
                break
        else:
            raise RuntimeError('Run listing exceeded safe pagination limit')
        if found:
            print('Already dispatched:', workflow, job_slot)
            continue
        api('/actions/workflows/' + workflow + '/dispatches',
            {'ref': 'main', 'inputs': dict(inputs, scheduled_slot=job_slot)})
        print('Dispatched:', workflow, job_slot)

if __name__ == '__main__':
    main()
