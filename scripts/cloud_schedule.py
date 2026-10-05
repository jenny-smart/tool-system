"""Route externally timed slots in Asia/Taipei; never infer slots from runner start."""
import argparse
import calendar
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')

def scheduled_time(stamp):
    """Allow up to two minutes of clock skew around ten-minute slots."""
    slot_stamp = ((stamp + 300) // 600) * 600
    if abs(stamp - slot_stamp) > 120:
        raise ValueError("雲端時間戳未落在原定時段前後兩分鐘內")
    return dt.datetime.fromtimestamp(slot_stamp, TZ)

def plan(when):
    when = when.astimezone(TZ)
    hm = when.strftime('%H:%M')
    jobs = []
    def add(file, **inputs):
        jobs.append((file + '.yml', inputs))
    daily = {'01:00': 'schedule_report', '01:10': 'staff_schedule',
             '01:20': 'orders_report', '01:30': 'staff_info', '07:00': 'notify'}
    if hm in daily:
        add('scheduled_daily', target=daily[hm])
    last_day = when.day == calendar.monthrange(when.year, when.month)[1]
    if hm == '22:00' and last_day:
        add('scheduled_daily', target='month_end_cleanup')
    if hm == '05:00':
        add('scheduled_field', target='field_all')
    if hm == '05:30':
        add('scheduled_service', target='service_all')
    if hm in {'00:00', '08:00', '12:00', '18:00'}:
        add('performance_report')
    if hm in {'08:00', '10:00', '12:00', '14:00', '16:00'}:
        add('scheduled_fubon_statement')
    if hm == '10:00' and when.weekday() in {1, 4}:
        add('scheduled_fubon_pending_check')
    if hm == '00:00' and when.day == 20:
        add('Scheduled_gmail_401')
    month = when.strftime('%Y%m')
    if when.day == 15 and hm == '17:00':
        add('scheduled_monthly', target='half_month_orders_1', period=month + '-1')
    if last_day and hm in {'17:00', '18:00'}:
        add('scheduled_monthly', target='half_month_orders_2' if hm == '17:00' else 'refund_report', period=month + '-2')
    monthly = {'00:00': 'prepaid_report', '00:30': 'stored_value_settlement', '00:40': 'stored_value_prepaid'}
    if when.day == 1 and hm in monthly:
        previous = (when.replace(day=1) - dt.timedelta(days=1)).strftime('%Y%m')
        add('scheduled_monthly', target=monthly[hm], period=previous + '-2')
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
    for workflow, inputs in jobs:
        title = 'cloud-' + slot
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
            print('Already dispatched:', workflow, slot)
            continue
        api('/actions/workflows/' + workflow + '/dispatches',
            {'ref': 'main', 'inputs': dict(inputs, scheduled_slot=slot)})
        print('Dispatched:', workflow, slot)

if __name__ == '__main__':
    main()
