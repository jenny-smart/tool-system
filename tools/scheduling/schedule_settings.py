"""排程設定（主控表「排程設定」分頁）：每個功能一列，各自設定時間、頻率、日期、遇例假日、執行方式。

cron-job.org 每 10 分鐘叫一次 Cloud Schedule Router（scripts/cloud_schedule.py），Router 讀這張表，
判斷這個時段有哪些功能要跑，派送對應的 GitHub workflow。改時間只要改 Sheet，不用改程式或 cron-job.org。
讀不到 Sheet（權限、網路）時退回本檔的 DEFAULT_RULES，並在 Router log 裡警告。

欄位（第 1 列標題，第 2 列起每列一個功能）：
  A 啟用        TRUE／FALSE（勾選框）
  B 功能代號    固定代號，對應 FUNCTIONS（不要改）
  C 功能名稱    顯示用
  D 時間        HH:MM，多個用逗號（08:00,11:00,16:30）；區間＋每小時：04:00~23:00 每小時
                時間要是 10 分鐘的倍數（Router 每 10 分鐘一次）
  E 頻率        每日／每週／每月／單月／雙月
  F 日期／星期  每週：一,二,…,日；每月：1,5,10、18-25、月底
  G 遇例假日    照跑／略過（例假日不跑）／提前（每月的日期遇例假日改到前一個工作日）
  H 執行方式    直接執行／盤點後自動排入／盤點後等確認（只盤點＋行事曆提醒，你確認後到 Tools App 執行）
  I 備註
  J 系統名稱    功能屬於哪個系統（tool-system／order-system／investment-system），程式自動填，顯示用

功能也可以派送到其他 repo 的 workflow（例如 order-system 的週末提醒、investment-system 的
月底轉值），見 EXTERNAL_DISPATCH_TOKENS。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Optional

SHEET_NAME = "排程設定"
HEADER = ["啟用", "功能代號", "功能名稱", "時間", "頻率", "日期／星期", "遇例假日", "執行方式", "備註", "系統名稱"]
SYSTEM_COL = "J"  # 系統名稱欄（放最後，前面各欄位置不變）
THIS_SYSTEM = "tool-system"

MODE_DIRECT = "直接執行"
MODE_QUEUE = "盤點後自動排入"
MODE_CONFIRM = "盤點後等確認"
MODES = (MODE_DIRECT, MODE_QUEUE, MODE_CONFIRM)

HOLIDAY_RUN, HOLIDAY_SKIP, HOLIDAY_EARLIER = "照跑", "略過", "提前"
WEEKDAYS = "一二三四五六日"


def _prev_period(when: datetime) -> str:
    return (when.replace(day=1) - timedelta(days=1)).strftime("%Y%m")


# 功能代號 → (workflow 檔, 參數)。參數是 dict 或 when→dict 的函式（期別依執行日算）。
FUNCTIONS: dict[str, tuple[str, object]] = {
    # 日排程
    "schedule_report": ("scheduled_daily.yml", {"target": "schedule_report"}),
    "staff_schedule": ("scheduled_daily.yml", {"target": "staff_schedule"}),
    "orders_report": ("scheduled_daily.yml", {"target": "orders_report"}),
    "staff_info": ("scheduled_daily.yml", {"target": "staff_info"}),
    "notify": ("scheduled_daily.yml", {"target": "notify"}),
    "month_end_cleanup": ("scheduled_daily.yml", {"target": "month_end_cleanup"}),
    "field_all": ("scheduled_field.yml", {"target": "field_all"}),
    "field_roster": ("scheduled_field.yml", {"target": "field_roster"}),
    "field_raise": ("scheduled_field.yml", {"target": "field_raise"}),
    "field_claim_review": ("scheduled_field.yml", {"target": "field_claim_review"}),
    "field_trainee_fee": ("scheduled_field.yml", {"target": "field_trainee_fee"}),
    "service_all": ("scheduled_service.yml", {"target": "service_all"}),
    "performance_report": ("performance_report.yml", {}),
    "fubon_statement": ("scheduled_fubon_statement.yml", {}),
    "yuanta_statement": ("scheduled_yuanta_statement.yml", {}),
    "gmail_401": ("Scheduled_gmail_401.yml", {}),
    "atm_unpaid": ("scheduled_atm_unpaid.yml", {}),
    "atm_match_reconcile": ("jenny-smart/order-system:atm-match-reconcile-schedule.yml", {}),
    # 清潔異動／富邦請款
    "change_order_check": ("scheduled_change_order_check.yml", {"target": "check"}),
    "change_order_scan": ("scheduled_change_order_check.yml", {"target": "batch_scan"}),
    "fubon_pending_check": ("scheduled_fubon_pending_check.yml", {}),
    # 其他 repo 的 workflow：寫成「owner/repo:檔名」，Router 用 EXTERNAL_DISPATCH_TOKENS 對應的 token 派送
    "weekend_reminder": ("jenny-smart/order-system:weekend-reminder-schedule.yml", {}),
    "customer_memo_daily": ("jenny-smart/order-system:customer-memo-schedule.yml", {}),
    # investment-system（個人投資總帳／總覽）
    "invest_tw_prices": ("jenny-smart/investment-system:tw-stock-prices.yml", {"mode": "auto"}),
    "invest_us_prices_night": ("jenny-smart/investment-system:us-stock-prices.yml", {"mode": "auto"}),
    "invest_us_prices_morning": ("jenny-smart/investment-system:us-stock-prices.yml", {"mode": "auto"}),
    "invest_fund_nav": ("jenny-smart/investment-system:fund-nav.yml", {}),
    "invest_month_end_ledger": ("jenny-smart/investment-system:month-end-ledger.yml", {"mode": "auto"}),
    "invest_keepalive": ("jenny-smart/investment-system:keepalive.yml", {}),
    "invest_snapshot": ("jenny-smart/investment-system:snapshot.yml", {}),
    "invest_stock_radar": ("jenny-smart/investment-system:stock-radar.yml", {}),
    "invest_stock_dividends": ("jenny-smart/investment-system:stock-dividends-from-ledger.yml", {}),
    "invest_night_futures": ("jenny-smart/investment-system:night-futures.yml", {}),
    # 月排程
    "half_month_orders_1": ("scheduled_monthly.yml",
                            lambda w: {"target": "half_month_orders_1", "period": w.strftime("%Y%m") + "-1"}),
    "half_month_orders_2": ("scheduled_monthly.yml",
                            lambda w: {"target": "half_month_orders_2", "period": w.strftime("%Y%m") + "-2"}),
    "refund_report": ("scheduled_monthly.yml",
                      lambda w: {"target": "refund_report", "period": w.strftime("%Y%m") + "-2"}),
    "prepaid_report": ("scheduled_monthly.yml",
                       lambda w: {"target": "prepaid_report", "period": _prev_period(w) + "-2"}),
    "stored_value_settlement": ("scheduled_monthly.yml",
                                lambda w: {"target": "stored_value_settlement", "period": _prev_period(w) + "-2"}),
    "stored_value_prepaid": ("scheduled_monthly.yml",
                             lambda w: {"target": "stored_value_prepaid", "period": _prev_period(w) + "-2"}),
    # 財務
    "fixed_expense": ("scheduled_finance.yml", {"target": "fixed_expense"}),
    "fixed_expense_mail": ("scheduled_finance.yml", {"target": "fixed_expense_mail"}),
    "fixed_expense_remit": ("scheduled_finance.yml", {"target": "fixed_expense_remit"}),
    "vip_full_pipeline": ("scheduled_finance.yml", {"target": "vip_full_pipeline"}),
    "revenue_reconcile": ("scheduled_finance.yml", {"target": "revenue_reconcile"}),
    "deposit_report": ("scheduled_finance.yml", {"target": "deposit_report"}),
    "review_monthly": ("scheduled_finance.yml", {"target": "review_monthly"}),
    "month_start_queue": ("scheduled_finance.yml", {"target": "month_start_queue"}),
    "cash_gap_check": ("scheduled_finance.yml", {"target": "cash_gap_check"}),
    "cash_gap_followup": ("scheduled_finance.yml", {"target": "cash_gap_followup"}),
    "month_start_check": ("scheduled_finance.yml", {"target": "month_start_check"}),
    "accounting_reminder": ("scheduled_finance.yml", {"target": "accounting_reminder"}),
    "mof_serial": ("scheduled_finance.yml", {"target": "mof_serial"}),
    "mof_serial_check": ("scheduled_finance.yml", {"target": "mof_serial_check"}),
    "monthly_checklist": ("scheduled_finance.yml", {"target": "monthly_checklist"}),
}

# 已停用的功能代號 → 說明（Sheet 裡還有這列時提醒刪除）
RETIRED_KEYS = {
    "invest_month_end": "investment-system 月底固定改用 invest_month_end_ledger（month-end-ledger.yml）",
    "invest_fund_freeze": "investment-system 月底固定改用 invest_month_end_ledger（month-end-ledger.yml）",
    "invest_month_roll": "investment-system 月初切換總覽改用 invest_month_end_ledger（month-end-ledger.yml）",
    "field_roster_raise": "拆成 field_roster（名冊，每月 1、15 日）與 field_raise（調薪，每月 15 日）",
    "fixed_expense_transfer": "改為 fixed_expense_remit【財報】各區固定費用匯款，每月 10 日 04:00",
}

# 預設值：第一次建立「排程設定」分頁時寫進去，讀不到 Sheet 時也用這份。
# (啟用, 代號, 名稱, 時間, 頻率, 日期, 遇例假日, 執行方式, 備註)
DEFAULT_ROWS: list[list] = [
    [True, "performance_report", "業績報表", "00:00,08:00,12:00,18:00", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "schedule_report", "日排程", "01:00", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "staff_schedule", "工作人員排班", "01:10", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "orders_report", "訂單資料", "01:20", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "staff_info", "工作人員資料", "01:30", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "field_all", "外場排程", "05:00", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "field_roster", "【月排程】新增專員名冊（台北／台中）", "06:00", "每月", "1,15", "提前",
     MODE_DIRECT, "期別＝當月；已存在就略過"],
    [True, "field_raise", "【月排程】新增專員調薪（台北／台中）", "06:00", "每月", "15", "提前",
     MODE_DIRECT, "期別＝當月（例 11/15 建 202611）；已存在就略過"],
    [True, "field_claim_review", "【專員請款】請款資料審核（台北／台中）", "06:00", "每月", "15", "提前",
     MODE_DIRECT, "處理 AJ 給付期別空白的列，填當月-1"],
    [True, "field_trainee_fee", "【新訓退款】新訓退訓實境費用（台北／台中）", "06:00", "每週", "五", "照跑",
     MODE_DIRECT, ""],
    [True, "service_all", "客服排程", "05:30", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "notify", "寄送日排程結果通知", "07:00", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "fubon_statement", "富邦明細下載（排入 Mac）", "08:00,11:00,16:30", "每日", "", "照跑", MODE_QUEUE,
     "11:00、16:30 例假日由程式略過"],
    [False, "atm_unpaid", "ATM 待付款清單同步（台北／台中）", "09:00~18:00 每30分鐘", "每日", "", "略過", MODE_DIRECT,
     "取代 Mac 的 ATM 本機排程：先移除 Mac launchd 再勾選啟用，避免兩邊同時貼上"],
    # 2026-11-06 Jenny：每日 08:10 跑 ATM 對帳 ② 配對銀行明細＋③ 更新系統對帳
    [True, "atm_match_reconcile", "ATM 對帳 ② 配對銀行明細＋③ 更新系統對帳（台北／台中）", "08:10", "每日", "", "照跑",
     MODE_DIRECT, "程式在 order-system（atm-match-reconcile-schedule.yml）；③ 只處理 T 欄「已配對」且 11/6 後入帳的列"],
    [True, "change_order_check", "清潔異動每日檢查（未備妥建 11:00 提醒）", "10:00", "每日", "", "照跑", MODE_DIRECT, ""],
    [True, "weekend_reminder", "週末／例假日服務提醒（休假前 2 天產生名單）", "11:00", "每日", "", "照跑", MODE_DIRECT,
     "每天檢查；程式依辦公日曆判斷是否為休假前 2 天（一般週四，遇國定假日提前），不是就略過。只寫追蹤表，不發 LINE"],
    # 2026-11-10 Jenny：上班日 10:00 貼客人備註非空白訂單到台北／台中內勤工作表單＋記行事曆
    [True, "customer_memo_daily", "客人備註非空白（台北／台中內勤工作表單）", "10:00", "每日", "", "略過", MODE_DIRECT,
     "程式在 order-system（customer-memo-schedule.yml）；區間＝前一上班日 10:00～今天 10:00（訂購時間），"
     "貼到「客人備註」分頁後記 jenny@hers.com.tw 行事曆"],
    [True, "change_order_scan", "【整合執行】清潔異動盤點", "09:30", "每週", "二,五", "照跑", MODE_CONFIRM,
     "盤點後等確認：只建提醒；改成盤點後自動排入會直接排給 Mac Agent"],
    [True, "fubon_pending_check", "【整合執行】富邦內部請款盤點", "10:30", "每週", "二,五", "照跑", MODE_CONFIRM,
     "請款記錄／工具包押金退款／清潔用品採購"],
    [True, "month_end_cleanup", "月底清空日排程來源資料夾", "22:00", "每月", "月底", "照跑", MODE_DIRECT, ""],
    [True, "half_month_orders_1", "上半月訂單", "12:00", "每月", "15", "照跑", MODE_DIRECT, ""],
    [True, "half_month_orders_2", "下半月訂單", "12:00", "每月", "月底", "照跑", MODE_DIRECT, ""],
    [True, "refund_report", "已退款", "18:00", "每月", "月底", "照跑", MODE_DIRECT, ""],
    [True, "prepaid_report", "預收", "00:00", "每月", "1", "照跑", MODE_DIRECT, "期別＝上個月"],
    [True, "stored_value_settlement", "儲值金結算", "00:10", "每月", "1", "照跑", MODE_DIRECT, "期別＝上個月"],
    [True, "stored_value_prepaid", "儲值金預收", "00:20", "每月", "1", "照跑", MODE_DIRECT, "期別＝上個月"],
    [True, "vip_full_pipeline", "【儲值金】完整流程", "01:00", "每月", "1", "照跑", MODE_DIRECT,
     "00:10、00:20 要先產出檔案"],
    [True, "revenue_reconcile", "財報營收 ⑥–⑩ 金流對帳", "03:00", "每月", "1", "照跑", MODE_DIRECT, ""],
    [True, "deposit_report", "【財報】工具包押金（彙整＋打勾＋比對）", "04:00", "每月", "1", "照跑", MODE_DIRECT,
     "統計月份＝前月（覆蓋）＋當月"],
    [True, "review_monthly", "【財報】年度review（公式＋欄位顯示）", "04:30", "每月", "1", "照跑", MODE_DIRECT,
     "期別＝上個月"],
    [True, "month_start_queue", "【整合執行】月初藍新／鯨躍／現金缺口（排入 Mac）", "05:00", "每月", "1", "照跑",
     MODE_QUEUE, "藍新約 08:00 才寄手續費發票"],
    [True, "month_start_check", "月初檢查（沒完成建提醒）", "04:00~23:00 每小時", "每月", "1", "照跑", MODE_DIRECT, ""],
    [True, "fixed_expense", "固定費用：台北租金／台中單月租金／台灣連線（當月）", "06:00", "每月", "1", "照跑",
     MODE_DIRECT, ""],
    [True, "cash_gap_check", "全區現金缺口＋預收款金額＋review 現金缺口區塊", "08:30", "每月", "1", "照跑",
     MODE_DIRECT, ""],
    [True, "cash_gap_followup", "現金缺口補算（上游有新數字就重算）＋未完成提醒", "09:00", "每月", "2-10", "照跑",
     MODE_DIRECT, "缺的項目每天提醒到 10 日"],
    [True, "fixed_expense_mail", "台北固定費用查信（AWS／震旦／眾點／台灣連線）", "09:00", "每月", "5,8,10", "提前",
     MODE_DIRECT, ""],
    [True, "fixed_expense_remit", "【整合執行】各區固定費用匯款（台中／桃園／新竹／學院＋台中請款，不含高雄）", "04:00", "每月", "10", "提前",
     MODE_QUEUE, "排程不跑高雄（2026-11-09）；排入 Mac，在富邦核對後按確認。學院 47911 自己轉帳，只提醒寫台北分錄"],
    [True, "accounting_reminder", "會計事務所費用確認提醒", "10:00", "單月", "10", "提前", MODE_DIRECT, ""],
    [True, "mof_serial", "【鯨躍發票】財政部字軌下載＋匯入＋配號（下一期）", "10:00", "雙月", "20", "提前", MODE_QUEUE,
     "雙月 20 日做下一期（例如 12 月做隔年 01-02）；需先登入財政部（圖形驗證碼）"],
    [True, "mof_serial_check", "字軌未完成提醒", "10:00", "雙月", "21-25", "照跑", MODE_DIRECT, ""],
    [True, "monthly_checklist", "【整合執行】月度盤點（更新主控表「月度盤點」分頁＋過期提醒）", "17:00", "每日", "", "照跑",
     MODE_DIRECT, "過了應完成日還沒做完的項目合併成一則提醒"],
    [True, "yuanta_statement", "元大明細下載（排入 Mac）", "11:00", "每月", "25", "提前", MODE_QUEUE, ""],
    [True, "gmail_401", "Gmail 401 歸檔", "00:00", "每月", "20", "照跑", MODE_DIRECT, ""],
    # investment-system：2026-11-06 Jenny 確認（台股／美股／基金由每 30 分鐘改成每 10 分鐘）
    # 預設不啟用：investment-system 的 workflow 要先能接受 scheduled_slot、tool-system 要有
    # INVESTMENT_SYSTEM_DISPATCH_TOKEN，在「排程設定」勾選啟用，再停用 cron-job.org 的對應工作。
    [False, "invest_tw_prices", "【投資】台股價格", "08:30~14:30 每10分鐘", "每週", "一,二,三,四,五", "照跑",
     MODE_DIRECT, "台股休市日程式自己略過"],
    [False, "invest_us_prices_night", "【投資】美股價格（晚上）", "21:00~23:50 每10分鐘", "每週", "一,二,三,四,五", "照跑",
     MODE_DIRECT, "美股休市時寫入的是前一交易日收盤"],
    [False, "invest_us_prices_morning", "【投資】美股價格（凌晨）", "00:00~06:00 每10分鐘", "每週", "二,三,四,五,六", "照跑",
     MODE_DIRECT, ""],
    [False, "invest_fund_nav", "【投資】基金淨值＋Richart 匯率", "00:00~23:50 每10分鐘", "每日", "", "照跑", MODE_DIRECT,
     "只寫有變的格子"],
    [False, "invest_month_end_ledger", "【投資】總帳月底固定／月初切換總覽", "00:00~23:50 每10分鐘", "每月", "1-10,28-31",
     "照跑", MODE_DIRECT, "程式依日期判斷：月底後等淨值到了就固定，1 日切換總覽"],
    [False, "invest_keepalive", "【投資】App 喚醒", "00:00~23:50 每10分鐘", "每日", "", "照跑", MODE_DIRECT,
     "也觸發依總覽自動確認基金配息"],
    [False, "invest_snapshot", "【投資】資產快照", "08:00,20:00", "每日", "", "照跑", MODE_DIRECT, ""],
    [False, "invest_stock_radar", "【投資】台股雷達", "14:30", "每週", "一,二,三,四,五", "照跑", MODE_DIRECT, ""],
    [False, "invest_stock_dividends", "【投資】台股股利入帳（比對細帳備註）", "00:00~23:50 每10分鐘", "每日", "", "照跑",
     MODE_DIRECT, "掃描除權息公告建立應入帳，再用 YYYY細帳 股利-台股備註確認入帳"],
    [False, "invest_night_futures", "【投資】台指期夜盤", "07:00", "每日", "", "照跑", MODE_DIRECT,
     "夜盤漲跌／量佔比／外資夜盤淨額／四種劇本 → investment-system「台指期夜盤」頁籤；週二至六未公布最多等 20 分鐘"],
]


@dataclass
class Rule:
    enabled: bool
    key: str
    name: str
    times: list[str] = field(default_factory=list)  # HH:MM
    freq: str = "每日"
    days: str = ""
    holiday: str = HOLIDAY_RUN
    mode: str = MODE_DIRECT
    errors: list[str] = field(default_factory=list)


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})\s*[~～\-－]\s*(\d{1,2}:\d{2})\s*每\s*(\d*)\s*(小時|分鐘|分)$")


def _hm(text: str) -> Optional[str]:
    m = _TIME_RE.match(text.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_times(text: str) -> tuple[list[str], list[str]]:
    times, errors = [], []
    for part in re.split(r"[,，、]", str(text or "")):
        part = part.strip()
        if not part:
            continue
        rng = _RANGE_RE.match(part)
        if rng:
            start, end = _hm(rng.group(1)), _hm(rng.group(2))
            step = int(rng.group(3) or 1) * (60 if rng.group(4) == "小時" else 1)
            if not start or not end or step <= 0:
                errors.append(f"時間格式不對：{part}")
                continue
            cur = int(start[:2]) * 60 + int(start[3:])
            stop = int(end[:2]) * 60 + int(end[3:])
            while cur <= stop:
                times.append(f"{cur // 60:02d}:{cur % 60:02d}")
                cur += step
            continue
        hm = _hm(part)
        if hm:
            times.append(hm)
        else:
            errors.append(f"時間格式不對：{part}")
    for t in times:
        if int(t[3:]) % 10:
            errors.append(f"時間要是 10 分鐘的倍數：{t}")
    return sorted(set(times)), errors


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in ("TRUE", "1", "Y", "YES", "V", "是", "啟用", "✓", "✅")


def parse_row(row: list) -> Optional[Rule]:
    cells = [row[i] if i < len(row) else "" for i in range(len(HEADER))]
    key = str(cells[1] or "").strip()
    if not key:
        return None
    times, errors = parse_times(cells[3])
    rule = Rule(enabled=_truthy(cells[0]), key=key, name=str(cells[2] or "").strip() or key, times=times,
                freq=str(cells[4] or "").strip() or "每日", days=str(cells[5] or "").strip(),
                holiday=str(cells[6] or "").strip() or HOLIDAY_RUN, mode=str(cells[7] or "").strip() or MODE_DIRECT,
                errors=errors)
    if key in RETIRED_KEYS:
        rule.errors.append(f"這個功能已停用（{RETIRED_KEYS[key]}），請刪除這列")
    elif key not in FUNCTIONS:
        rule.errors.append(f"不認得的功能代號：{key}")
    if rule.freq not in ("每日", "每週", "每月", "單月", "雙月"):
        rule.errors.append(f"頻率要是 每日／每週／每月／單月／雙月：{rule.freq}")
    if rule.holiday not in (HOLIDAY_RUN, HOLIDAY_SKIP, HOLIDAY_EARLIER):
        rule.errors.append(f"遇例假日要是 照跑／略過／提前：{rule.holiday}")
    if rule.mode not in MODES:
        rule.errors.append(f"執行方式要是 {'／'.join(MODES)}：{rule.mode}")
    if rule.freq != "每日" and not rule.days:
        rule.errors.append("每週／每月要填日期或星期")
    return rule


def default_rules() -> list[Rule]:
    return [r for r in (parse_row(row) for row in DEFAULT_ROWS) if r]


def _last_day(day: date) -> int:
    nxt = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (nxt - timedelta(days=1)).day


def month_days(text: str, day: date) -> list[int]:
    last = _last_day(day)
    result = []
    for part in re.split(r"[,，、]", text or ""):
        part = part.strip()
        if not part:
            continue
        if part in ("月底", "最後一天", "L"):
            result.append(last)
            continue
        m = re.match(r"^(\d{1,2})\s*[-~～－]\s*(\d{1,2})$", part)
        if m:
            result.extend(d for d in range(int(m.group(1)), int(m.group(2)) + 1) if d <= last)
        elif part.isdigit() and int(part) <= last:
            result.append(int(part))
    return sorted(set(result))


def _weekdays(text: str) -> set[int]:
    result = set()
    for ch in str(text or ""):
        if ch in WEEKDAYS:
            result.add(WEEKDAYS.index(ch))
        elif ch == "天":
            result.add(6)
    return result


def previous_workday(day: date, is_workday: Callable[[date], bool]) -> date:
    while not is_workday(day):
        day -= timedelta(days=1)
    return day


def _month_ok(rule: Rule, month: int) -> bool:
    if rule.freq == "單月" and month % 2 == 0:
        return False
    if rule.freq == "雙月" and month % 2 == 1:
        return False
    return True


def target_date(rule: Rule, day: date, is_workday: Callable[[date], bool]) -> Optional[date]:
    """day 要執行時，對應的原訂日期（遇例假日提前時可能是下個月初，例如 11/1 週日 → 10/30 執行、原訂 11/1）。"""
    if rule.freq in ("每日", "每週"):
        hit = rule.freq == "每日" or day.weekday() in _weekdays(rule.days)
        return day if hit and (rule.holiday == HOLIDAY_RUN or is_workday(day)) else None
    if rule.holiday == HOLIDAY_EARLIER:
        months = [day.replace(day=1)]
        nxt = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
        months.append(nxt)  # 下個月月初的日期遇假日會提前到本月底
        for first in months:
            if not _month_ok(rule, first.month):
                continue
            for d in month_days(rule.days, first):
                target = first.replace(day=d)
                if target >= day and previous_workday(target, is_workday) == day:
                    return target
        return None
    if not _month_ok(rule, day.month):
        return None
    if day.day in month_days(rule.days, day) and (rule.holiday == HOLIDAY_RUN or is_workday(day)):
        return day
    return None


def matches_day(rule: Rule, day: date, is_workday: Callable[[date], bool]) -> bool:
    return target_date(rule, day, is_workday) is not None


def due(rules: Iterable[Rule], when: datetime, is_workday: Callable[[date], bool]) -> list[Rule]:
    hm = when.strftime("%H:%M")
    return [r for r in rules if r.enabled and not r.errors and hm in r.times
            and matches_day(r, when.date(), is_workday)]


# 派送到其他 repo 時用的 token（GitHub Secret 名稱）；token 需要該 repo 的 Actions: Read and write。
EXTERNAL_DISPATCH_TOKENS = {
    "jenny-smart/order-system": "ORDER_SYSTEM_DISPATCH_TOKEN",
    "jenny-smart/investment-system": "INVESTMENT_SYSTEM_DISPATCH_TOKEN",
}


def split_workflow(workflow: str) -> tuple[str, str]:
    """「owner/repo:檔名」→ (owner/repo, 檔名)；本 repo 的 workflow 回傳 ("", 檔名)。"""
    if ":" in workflow:
        repo, name = workflow.split(":", 1)
        return repo.strip(), name.strip()
    return "", workflow


def system_name(key: str) -> str:
    """功能代號 → 系統名稱（派送到哪個 repo），寫進「排程設定」J 欄。"""
    if key not in FUNCTIONS:
        return ""
    repo, _name = split_workflow(FUNCTIONS[key][0])
    return repo.split("/")[-1] if repo else THIS_SYSTEM


def with_system(row: list) -> list:
    """DEFAULT_ROWS 的一列（A～I）補上 J 系統名稱。"""
    return list(row) + [""] * (9 - len(row)) + [system_name(str(row[1]))]


def dispatch_for(rule: Rule, when: datetime) -> tuple[str, dict]:
    workflow, params = FUNCTIONS[rule.key]
    inputs = params(when) if callable(params) else dict(params)
    return workflow, inputs


# ── 讀寫 Sheet ──────────────────────────────────────

def _service_and_id():
    from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

    return get_sheets_service(), get_master_spreadsheet_id()


# 程式改了預設、Sheet 裡還是舊預設（F 日期、G 假日都沒被改過）的列 → 自動換成新預設（可換功能代號）。
# 功能代號: (舊 F, 舊 G, 新功能代號)
OUTDATED_DEFAULTS = {
    # 2026-11-05 Jenny：名冊改 1、15 日，調薪 15 日，請款只有 15 日，遇例假日提前
    "field_roster_raise": ("15", "照跑", "field_roster"),
    "field_claim_review": ("15,月底", "照跑", "field_claim_review"),
    # 2026-11-05 Jenny：各區固定費用匯款（10 日）、台北固定費用查信、會計事務所費用確認遇例假日提前
    "fixed_expense_remit": ("10", "照跑", "fixed_expense_remit"),
    "fixed_expense_mail": ("5,8,10", "照跑", "fixed_expense_mail"),
    "accounting_reminder": ("10", "照跑", "accounting_reminder"),
}


def _upgrade_outdated_rows(service, spreadsheet_id: str, rows: list[list], log: Callable[[str], None]) -> None:
    """rows＝B2:G 的值（會就地改成新功能代號，讓後面補列時不重複）。"""
    defaults = {row[1]: row for row in DEFAULT_ROWS}
    data = []
    for offset, row in enumerate(rows):
        cells = [str(c).strip() for c in row] + [""] * (6 - len(row))
        key = cells[0]
        if key not in OUTDATED_DEFAULTS:
            continue
        old_days, old_holiday, new_key = OUTDATED_DEFAULTS[key]
        if (cells[4], cells[5]) != (old_days, old_holiday) or new_key not in defaults:
            continue
        new = defaults[new_key]
        row_no = offset + 2
        if new_key == key:  # 同一功能：只改 F 日期、G 遇例假日，名稱／時間等其他欄位保留
            data.append({"range": f"'{SHEET_NAME}'!F{row_no}:G{row_no}", "values": [new[5:7]]})
        else:  # 換功能代號：整列換成新預設
            data.append({"range": f"'{SHEET_NAME}'!B{row_no}:I{row_no}", "values": [new[1:9]]})
            rows[offset] = [new_key]
        log(f"「{SHEET_NAME}」第 {row_no} 列 {key} 改成新預設：{new_key} {new[4]} {new[5]} 遇例假日{new[6]}")
    if data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"valueInputOption": "RAW", "data": data}).execute()


def ensure_sheet(service=None, spreadsheet_id: str = "", log: Callable[[str], None] = print) -> bool:
    """沒有「排程設定」分頁就建立並寫入預設值；回傳是否新建。已存在時補上 Sheet 缺少的功能列（不改既有列）。"""
    if service is None:
        service, spreadsheet_id = _service_and_id()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if SHEET_NAME not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME,
                                                            "gridProperties": {"frozenRowCount": 1}}}}]},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A1", valueInputOption="RAW",
            body={"values": [HEADER] + [with_system(r) for r in DEFAULT_ROWS]},
        ).execute()
        log(f"已建立「{SHEET_NAME}」分頁（{len(DEFAULT_ROWS)} 個功能）")
        return True
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!B2:G").execute().get("values", [])
    _upgrade_outdated_rows(service, spreadsheet_id, rows, log)
    have = {str(r[0]).strip() for r in rows if r}
    missing = [row for row in DEFAULT_ROWS if row[1] not in have]
    if missing:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A:{SYSTEM_COL}", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": [with_system(r) for r in missing]},
        ).execute()
        log(f"「{SHEET_NAME}」補上新功能：{'、'.join(r[1] for r in missing)}")
    _fill_system_column(service, spreadsheet_id, rows, log)
    return False


def _fill_system_column(service, spreadsheet_id: str, rows: list[list], log: Callable[[str], None]) -> None:
    """J 欄沒有標題就補「系統名稱」；既有列 J 欄空白、功能代號認得的就填上（不改有填的）。

    rows＝B2:G 的值（_upgrade_outdated_rows 處理過）；新補上的列 append 時已經帶 J。"""
    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!{SYSTEM_COL}1:{SYSTEM_COL}{len(rows) + 1}",
    ).execute().get("values", [])
    current += [[]] * (len(rows) + 1 - len(current))
    data = []
    if not (current[0] and str(current[0][0]).strip()):
        data.append({"range": f"'{SHEET_NAME}'!{SYSTEM_COL}1", "values": [[HEADER[-1]]]})
    for offset, row in enumerate(rows):
        key = str(row[0]).strip() if row else ""
        cell = current[offset + 1]
        name = system_name(key)
        if name and not (cell and str(cell[0]).strip()):
            data.append({"range": f"'{SHEET_NAME}'!{SYSTEM_COL}{offset + 2}", "values": [[name]]})
    if data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"valueInputOption": "RAW", "data": data}).execute()
        log(f"「{SHEET_NAME}」系統名稱欄補上 {len(data)} 格")


def load_rules(log: Callable[[str], None] = print) -> tuple[list[Rule], bool]:
    """回傳 (規則, 是否從 Sheet 讀到)。讀不到時用預設值。"""
    try:
        service, spreadsheet_id = _service_and_id()
        ensure_sheet(service, spreadsheet_id, log=log)
        values = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{SHEET_NAME}'!A2:I",
            valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    except Exception as exc:
        log(f"::warning::讀不到「{SHEET_NAME}」，改用程式內建的預設排程：{exc}")
        return default_rules(), False
    rules = [r for r in (parse_row(row) for row in values) if r]
    for r in rules:
        for err in r.errors:
            log(f"::warning::「{SHEET_NAME}」{r.key}：{err}（這列不會執行）")
    return rules, True


def get_mode(key: str, default: str = MODE_DIRECT, log: Callable[[str], None] = print) -> str:
    """執行中的功能查自己的執行方式（盤點後自動排入／盤點後等確認）。讀不到用預設。"""
    import os

    override = os.getenv("SCHEDULE_MODE", "").strip()
    if override in MODES:
        return override
    rules, _ok = load_rules(log=lambda _t: None)
    for r in rules:
        if r.key == key and r.mode in MODES:
            return r.mode
    return default
