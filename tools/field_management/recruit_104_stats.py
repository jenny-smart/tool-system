"""104 刊登數據（每週數據）→ 台北名冊「104統計副表」A:L → 「104統計表」每日欄。

2026-11-26 Jenny：每週五 04:40（遇例假日提前）自動做，取代手動「104 匯出 Excel → 貼入副表 → 副表數字貼到統計表」。
- 不下載檔案：在 Mac Agent Chrome（已登入 104 招募管理）的分頁裡直接呼叫 104 頁面自己用的 API
  （auth.vip.104.com.tw/api/statistics/statisticsWeekly），組成跟「匯出Excel」一樣的版面寫進副表 A:L。
- 副表 N:V 是 Jenny 的公式（依列範圍算 台北／桃園／新竹／第 4 區），程式不動，寫完 A:L 後讀 O12:U30 的結果。
- 統計表：12～30 列跟副表 O12:U30 同列號，接在第 12 列最後一個日期後面，一天一欄，寫數值；
  週四那一欄第 6～9 列補 =SUM(前 7 欄)（同 JT6:JT9 的寫法）；第 11 列星期空白才補。
- 104 每次最多查 7 天：從統計表最後一天的隔天開始，每 7 天一段補到昨天
  （提前執行或 Mac 沒開機漏跑時，下一次會自動補齊，不會跳日也不會重複）。
- 檔案：config 的 spreadsheet_ids.roster.台北，換成資料日期所屬年度的檔案（年度切換後自動用新年度檔）。

手動：python -m tools.field_management.recruit_104_stats [--end YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
AREA = "台北"
SYSTEM_NAME = "外場排程系統"  # config/systems.yaml 的系統名稱（同 resume_system／roster_raise）
SUB_SHEET = "104統計副表"
STATS_SHEET = "104統計表"
API_URL = "https://auth.vip.104.com.tw/api/statistics/statisticsWeekly"
PAGE_URL = "https://vip.104.com.tw/rms/dataCenter/ad/weeklyStats"
MAX_DAYS = 7  # 104 每週數據一次最多 7 天
SUB_RESULT_RANGE = "O12:U30"  # 副表四區每日結果（7 天）
FIRST_ROW, LAST_ROW = 12, 30  # 統計表／副表共同列號
BLOCK_STARTS = (12, 17, 22, 27)  # 每區：日期、主動應徵履歷數、職務頁瀏覽數(PV)、應徵率
WEEK_TOTAL_ROWS = {6: 13, 7: 18, 8: 23, 9: 28}  # 第 6～9 列週合計 → 加總的列
WEEKDAY_ROW = 11
WEEKDAYS = "一二三四五六日"
WEEK_END_WEEKDAY = 3  # 週四那欄放週合計（統計週＝週五～週四）
DATE_HEADER_COL = 13  # N 欄（0 起算）是「台北」標題，日期從 O 欄之後


class NeedLogin(RuntimeError):
    """Agent Chrome 沒有登入 104 招募管理。"""


# ── 104 資料 ──────────────────────────────────────

FETCH_JS = """async ([url, start, end]) => {
  const jobs = []; let meta = null;
  for (let page = 1; page <= 50; page++) {
    const qs = new URLSearchParams({queryStartDate: start, queryEndDate: end, page: String(page)});
    const r = await fetch(url + '?' + qs.toString(), {credentials: 'include'});
    if (r.status === 401 || r.status === 403) return {login: true, status: r.status};
    let j; try { j = await r.json(); } catch (e) { return {login: true, status: r.status}; }
    if (j.status !== 'SUCCESS') return {error: j.message || j.status, status: r.status};
    meta = meta || j.result;
    const list = (j.result && j.result.jobList) || [];
    if (!list.length) break;
    jobs.push(...list);
    if (j.result.total && jobs.length >= j.result.total) break;
  }
  return {meta, jobs};
}"""


def fetch_weekly(cdp_url: str, start: date, end: date) -> tuple[dict, list[dict]]:
    """在 Agent Chrome 的 104 分頁裡呼叫每週數據 API（用 Chrome 的登入狀態，不存帳密、不下載檔案）。"""
    from playwright.sync_api import sync_playwright

    from tools.invoice_center.chrome_cdp import connect_existing_chrome

    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = next((p for p in context.pages if "vip.104.com.tw" in p.url), None)
        opened = page is None
        page = page or context.new_page()
        try:
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            if "vip.104.com.tw" not in page.url:
                raise NeedLogin(f"104 招募管理未登入（目前在 {page.url}）")
            result = page.evaluate(FETCH_JS, [API_URL, _ymd(start), _ymd(end)])
        finally:
            if opened:
                page.close()
    if result.get("login"):
        raise NeedLogin(f"104 API 回應 {result.get('status')}，請在 Agent Chrome 重新登入 104 招募管理")
    if result.get("error"):
        raise RuntimeError(f"104 API 失敗：{result['error']}")
    return result["meta"] or {}, result["jobs"]


def _ymd(day: date) -> str:
    return day.strftime("%Y/%m/%d")


def _md(day: date) -> str:
    return f"{day.month}/{day.day}"


def _count(value: Any) -> Any:
    """104 關閉的日子回傳 -1，匯出 Excel 顯示「-」。"""
    return "-" if value is None or value < 0 else value


def export_rows(meta: dict, jobs: list[dict], start: date, end: date) -> list[list[Any]]:
    """組成跟 104「匯出Excel」相同的 A:L 版面（副表公式靠這個版面的列位置）。"""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows: list[list[Any]] = [
        [f"目前刊期：{meta.get('poStartDate', '')}~{meta.get('poEndDate', '')}(刊登天數：{meta.get('poPeriod', '')}天)"],
        [f"查詢日期：{_ymd(start)}~{_ymd(end)}"],
        [],
    ]
    header = [""] + [_md(d) for d in days] + ["總計", "平均", "同業低標", "同業高標"]
    for job in jobs:
        rows.append([job.get("job", "")])
        rows.append([f"職務最近開啟日：{job.get('openDate', '')}(刊期內累計刊登{job.get('poOpenedDays', 0)}天)"])
        rows.append(header)
        rows.append(["主動應徵履歷數"] + [_count(v) for v in job.get("applyCount", [])]
                    + [job.get("applyTotal", 0), job.get("applyAverage", 0),
                       job.get("applyLowValue", ""), job.get("applyHighValue", "")])
        rows.append(["職務頁瀏覽數(PV)"] + [_count(v) for v in job.get("clickCount", [])]
                    + [job.get("clickTotal", 0), job.get("clickAverage", 0),
                       job.get("clickLowValue", ""), job.get("clickHighValue", "")])
        rows.append([])
    return rows


# ── 統計表 ──────────────────────────────────────

def col_letter(index: int) -> str:
    """0 起算 → A1 欄名。"""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def parse_sheet_date(value: Any) -> date | None:
    """日期格顯示成「11/19」（格式只顯示月日），所以讀 SERIAL_NUMBER；也接受 2026/11/19 文字。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return date(1899, 12, 30) + timedelta(days=int(value)) if 30000 < value < 80000 else None
    text = str(value or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def last_date_column(date_row: list[Any]) -> tuple[int, date]:
    """統計表第 12 列最後一個日期的 (欄序號 0 起算, 日期)。"""
    for index in range(len(date_row) - 1, DATE_HEADER_COL, -1):
        day = parse_sheet_date(date_row[index])
        if day:
            return index, day
    raise RuntimeError(f"{STATS_SHEET} 第 {FIRST_ROW} 列找不到日期，無法判斷要從哪一天接著寫")


def windows(last_done: date, until: date) -> list[tuple[date, date]]:
    """從 last_done 隔天補到 until，每段最多 7 天；每段都用滿 7 天（副表公式固定 B～H 七天），
    重疊的日期寫統計表時會略過。"""
    result = []
    cursor = last_done
    while cursor < until:
        end = min(cursor + timedelta(days=MAX_DAYS), until)
        result.append((end - timedelta(days=MAX_DAYS - 1), end))
        cursor = end
    return result


def stats_columns(sub_values: list[list[Any]], days: list[date], new_days: list[date],
                  first_col: int) -> list[tuple[int, date, list[Any]]]:
    """副表 O12:U30 → 統計表要新增的欄 [(欄序號, 日期, 12～30 列的值)]。"""
    height = LAST_ROW - FIRST_ROW + 1
    grid = [list(r) + [""] * (len(days) - len(r)) for r in sub_values] + [[""] * len(days)] * (height - len(sub_values))
    out = []
    for offset, day in enumerate(new_days):
        k = days.index(day)
        column = [grid[r][k] if r < len(grid) else "" for r in range(height)]
        for start in BLOCK_STARTS:
            column[start - FIRST_ROW] = _ymd(day)  # 副表日期是「11/13」文字，統計表放完整日期
        out.append((first_col + offset, day, column))
    return out


def week_total_formulas(col: int) -> dict[int, str]:
    """週四那欄第 6～9 列：=SUM(前 6 欄～本欄)。"""
    left, right = col_letter(col - 6), col_letter(col)
    return {row: f"=SUM({left}{src}:{right}{src})" for row, src in WEEK_TOTAL_ROWS.items()}


# ── Sheets ──────────────────────────────────────

def _execute(request):
    from tools.field_management.roster_raise import execute_with_retry

    return execute_with_retry(request)


def target_spreadsheet(day: date, log: Callable[[str], None]) -> str:
    from tools.field_management.roster_raise import year_file_id
    from tools.field_management.staff_profile import get_spreadsheet_id, load_system_config

    configured = get_spreadsheet_id(load_system_config(SYSTEM_NAME), "roster", AREA)
    try:
        return year_file_id(configured, day.strftime("%Y%m"))
    except FileNotFoundError:
        log(f"⚠️ {day.year} 年度台北名冊還沒建立，先寫 {day.year - 1} 年度檔案")
        return year_file_id(configured, f"{day.year - 1}12")


def sheet_id_of(sheets, spreadsheet_id: str, title: str) -> int:
    meta = _execute(sheets.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                              fields="sheets(properties(sheetId,title))"))
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == title:
            return int(sheet["properties"]["sheetId"])
    raise RuntimeError(f"找不到工作表：{title}")


def write_sub_sheet(sheets, spreadsheet_id: str, rows: list[list[Any]]) -> list[list[Any]]:
    """清空副表 A:L、貼上 104 資料，回傳 N:V 公式算好的 O12:U30。"""
    _execute(sheets.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=f"'{SUB_SHEET}'!A:L", body={}))
    _execute(sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{SUB_SHEET}'!A1", valueInputOption="RAW", body={"values": rows}))
    res = _execute(sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{SUB_SHEET}'!{SUB_RESULT_RANGE}", valueRenderOption="UNFORMATTED_VALUE"))
    return res.get("values", [])


def write_stats_columns(sheets, spreadsheet_id: str, columns: list[tuple[int, date, list[Any]]],
                        weekday_row: list[Any]) -> None:
    if not columns:
        return
    first, last = columns[0][0], columns[-1][0]
    sid = sheet_id_of(sheets, spreadsheet_id, STATS_SHEET)
    # 格式（百分比、框線、粗體）照前面一週的 7 欄
    width = last - first + 1
    src_left = max(DATE_HEADER_COL + 1, first - MAX_DAYS)
    requests = []
    for i in range(width):
        src = src_left + (i % min(MAX_DAYS, first - src_left))
        requests.append({"copyPaste": {
            "source": {"sheetId": sid, "startRowIndex": 5, "endRowIndex": LAST_ROW,
                       "startColumnIndex": src, "endColumnIndex": src + 1},
            "destination": {"sheetId": sid, "startRowIndex": 5, "endRowIndex": LAST_ROW,
                            "startColumnIndex": first + i, "endColumnIndex": first + i + 1},
            "pasteType": "PASTE_FORMAT"}})
    _execute(sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}))

    data = []
    for col, day, values in columns:
        letter = col_letter(col)
        data.append({"range": f"'{STATS_SHEET}'!{letter}{FIRST_ROW}:{letter}{LAST_ROW}",
                     "values": [[v] for v in values]})
        if col >= len(weekday_row) or not str(weekday_row[col]).strip():
            data.append({"range": f"'{STATS_SHEET}'!{letter}{WEEKDAY_ROW}", "values": [[WEEKDAYS[day.weekday()]]]})
        if day.weekday() == WEEK_END_WEEKDAY:
            for row, formula in week_total_formulas(col).items():
                data.append({"range": f"'{STATS_SHEET}'!{letter}{row}", "values": [[formula]]})
    _execute(sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"valueInputOption": "USER_ENTERED", "data": data}))


def week_summary(columns: list[tuple[int, date, list[Any]]]) -> str:
    names = ["台北", "桃園", "新竹", "第4區"]
    parts = []
    for name, start in zip(names, BLOCK_STARTS):
        apply = sum(v for _c, _d, col in columns if isinstance(v := col[start - FIRST_ROW + 1], (int, float)))
        pv = sum(v for _c, _d, col in columns if isinstance(v := col[start - FIRST_ROW + 2], (int, float)))
        parts.append(f"{name} 應徵 {apply:g}／PV {pv:g}")
    return "；".join(parts)


# ── 主流程 ──────────────────────────────────────

def run(until: date, cdp_url: str, dry_run: bool = False, log: Callable[[str], None] = print) -> dict:
    from tools.field_management.staff_profile import get_sheets_service

    sheets = get_sheets_service()
    spreadsheet_id = target_spreadsheet(until, log)
    head = _execute(sheets.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=[f"'{STATS_SHEET}'!{WEEKDAY_ROW}:{WEEKDAY_ROW}", f"'{STATS_SHEET}'!{FIRST_ROW}:{FIRST_ROW}"],
        valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER"))
    ranges = head.get("valueRanges", [])
    weekday_row = (ranges[0].get("values") or [[]])[0]
    date_row = (ranges[1].get("values") or [[]])[0]
    last_col, last_day = last_date_column(date_row)
    log(f"{STATS_SHEET} 最後一天：{_ymd(last_day)}（{col_letter(last_col)} 欄）；補到 {_ymd(until)}")

    plan = windows(last_day, until)
    if not plan:
        log("✅ 已是最新，沒有要補的日期")
        return {"written": 0}

    written_total = []
    next_col, done_until = last_col + 1, last_day
    for start, end in plan:
        meta, jobs = fetch_weekly(cdp_url, start, end)
        days = [start + timedelta(days=i) for i in range(MAX_DAYS)]
        new_days = [d for d in days if d > done_until]
        log(f"104 每週數據 {_ymd(start)}~{_ymd(end)}：{len(jobs)} 個職務；新增 {_ymd(new_days[0])}~{_ymd(new_days[-1])}")
        if dry_run:
            log(f"（dry-run）副表會寫 {len(export_rows(meta, jobs, start, end))} 列，統計表從 {col_letter(next_col)} 欄起")
            done_until = end
            next_col += len(new_days)
            continue
        sub_values = write_sub_sheet(sheets, spreadsheet_id, export_rows(meta, jobs, start, end))
        columns = stats_columns(sub_values, days, new_days, next_col)
        write_stats_columns(sheets, spreadsheet_id, columns, weekday_row)
        log(f"SAVE {STATS_SHEET} {col_letter(columns[0][0])}～{col_letter(columns[-1][0])} 欄：{week_summary(columns)}")
        written_total.extend(columns)
        done_until = end
        next_col += len(new_days)
    if written_total:
        log(f"✅ 完成 104 週報：{_ymd(written_total[0][1])}~{_ymd(written_total[-1][1])}，{week_summary(written_total)}")
    return {"written": len(written_total), "spreadsheet_id": spreadsheet_id}


def notify_login() -> None:
    """Mac 跳提醒視窗：請到 Agent Chrome 登入 104。"""
    import subprocess

    subprocess.run([sys.executable, "-m", "tools.notify_popup", "--title", "104 週報：請登入 104 招募管理",
                    "--message", "排程要抓 104 每週數據，但 Agent Chrome 沒有登入 104 招募管理。\n"
                                 "請在 Agent Chrome 打開 https://vip.104.com.tw 登入後，到 Tools App 重新執行「104 週報」。",
                    "--system", "", "--function", "", "--scan", ""], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="104 每週數據 → 台北名冊 104統計副表／104統計表")
    parser.add_argument("--end", default="", help="補到哪一天（預設昨天）")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    until = (datetime.strptime(args.end, "%Y-%m-%d").date() if args.end
             else datetime.now(TZ).date() - timedelta(days=1))
    try:
        run(until, args.cdp_url, args.dry_run)
    except NeedLogin as exc:
        print(f"❌ {exc}", flush=True)
        notify_login()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
