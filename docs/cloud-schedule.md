# 雲端定時觸發

## 2026-11 起：時間一律在主控表「排程設定」分頁設定

- cron-job.org **只留一個 job**：每 10 分鐘（台北時間 `*/10 * * * *`）POST 到 `cloud_schedule.yml/dispatches`，
  body 不變：`{"ref":"main","inputs":{"timestamp":"%cjo:unixtime%"}}`。其他逐時段的 job 全部停用。
- Router（`scripts/cloud_schedule.py`）每次讀主控表「排程設定」分頁（`tools/scheduling/schedule_settings.py`），
  判斷這個 10 分鐘時段有哪些功能要跑，逐一派送。讀不到 Sheet 時改用程式內建的預設值（Actions log 會有 warning）。
- 分頁第一次執行時自動建立並寫入預設值；之後程式新增功能時，只會補上 Sheet 缺少的列，不會改你改過的列。
- 每個功能一列，各自設定：
  | 欄 | 說明 |
  |---|---|
  | 啟用 | 勾選才執行 |
  | 功能代號 | 固定代號（不要改） |
  | 功能名稱 | 顯示用 |
  | 時間 | `HH:MM`，多個用逗號；區間每小時寫 `04:00~23:00 每小時`。要是 10 分鐘的倍數 |
  | 頻率 | 每日／每週／每月／單月／雙月 |
  | 日期／星期 | 每週：`二,五`；每月：`1`、`5,8,10`、`18-25`、`月底` |
  | 遇例假日 | 照跑／略過／提前（每月的日期遇週末或國定假日改到前一個工作日，依政府辦公日曆） |
  | 執行方式 | 直接執行／盤點後自動排入（排給 Mac Agent）／盤點後等確認（只盤點＋行事曆提醒） |
  | 系統名稱 | 功能屬於哪個系統（tool-system／order-system／investment-system），放在最後一欄（J），程式自動填，空白才填、不改你填過的 |
- 去重以「時段＋功能代號」為單位（GitHub run 標題 `cloud-YYYYMMDDTHHMM-功能代號`），
  同一時段、同一個 workflow 的不同功能都會派送；同一功能重試不會重複派送。
- 欄位填錯（時間不是 10 分鐘倍數、代號不認得…）的那一列不會執行，Router log 會列出原因。

### 預設排程（2026-11-04 與 Jenny 確認的部分）

| 功能代號 | 預設時間 | 說明 |
|---|---|---|
| deposit_report | 每月 1 日 04:00 | 工具包押金（台北、台中）：前月覆蓋＋當月統計 → 依統計月份打勾 → N 欄異常 → 比對當月最後一列 Y 欄＝B1；有問題建提醒 |
| review_monthly | 每月 1 日 04:30 | 年度review：各區 AA 欄／年度review AB 欄預估公式改到上個月預估；期別以前只看預估、之後及年度實際合計收起 |
| fixed_expense | 每月 1 日 06:00 | 台北當月租金／管理費、台中單月當月＋下月租金、台灣連線（付當月費用，記當月） |
| cash_gap_check | 每月 1 日 08:30 | 全區現金缺口 → 預收款金額 → review「現金缺口」區塊；列出還沒有數字的項目 |
| cash_gap_followup | 每月 2～10 日 09:00 | 同上重算（上游晚做的數字自動補進 review），還缺就建提醒 |
| fixed_expense_mail | 每月 5、8、10 日 09:00 | 台北查信（AWS／震旦／眾點／台灣連線），10 日還沒查到建提醒 |
| fixed_expense_remit | 每月 10 日 04:00（遇例假日提前） | 【整合執行】各區固定費用匯款：台中／桃園／新竹看行銷總表、高雄 40000 固定，排入 Mac 富邦匯款給台北（只記台北分錄，高雄不跑富邦更新）；學院 47911 自己轉帳，只提醒寫台北分錄（內勤薪資／勞保費／健保費／退休金）；台中單月另外檢查台中固定費用請款（租金、電費） |
| field_roster | 每月 1、15 日 06:00（遇例假日提前） | 【月排程】新增專員名冊（台北／台中），期別＝當月；已存在就略過 |
| field_raise | 每月 15 日 06:00（遇例假日提前） | 【月排程】新增專員調薪（台北／台中），期別＝當月；已存在就略過 |
| field_claim_review | 每月 15 日 06:00（遇例假日提前） | 【專員請款】請款資料審核（台北／台中）：AJ 給付期別空白的列，填當月-1 |
| field_trainee_fee | 每週五 06:00 | 【新訓退款】新訓退訓實境費用（台北／台中） |
| change_order_scan | 每週二、五 09:30 | 【整合執行】清潔異動盤點（預設等確認：建提醒）。車馬費發票改自動開立（同待收款） |
| fubon_pending_check | 每週二、五 10:30 | 【整合執行】富邦內部請款盤點（預設等確認：建提醒） |
| mof_serial | 雙月 20 日 10:00，遇假日提前（做下一期） | 下一期字軌：沒配號的地區排入「財政部登入＋完整流程」，提醒登入 |
| monthly_checklist | 每日 17:00 | 月度盤點：更新主控表「月度盤點」分頁，過期沒做完的合併成一則提醒 |
| mof_serial_check | 雙月 21～25 日 10:00 | 字軌還沒完成就提醒 |

其餘（日排程、外場、客服、業績、富邦／元大明細、月排程、儲值金、⑥–⑩、月初藍新鯨躍、月初檢查…）沿用原本時間，見分頁。

### investment-system（2026-11-06 與 Jenny 確認）

原本在 cron-job.org 的 investment-system 排程全部併到這裡；台股／美股／基金由每 30 分鐘改成原時段內每 10 分鐘。
派送到 `jenny-smart/investment-system`，需要 Secret `INVESTMENT_SYSTEM_DISPATCH_TOKEN`
（fine-grained token，只給 investment-system 的 Actions: Read and write）。沒設定時這些功能會略過並讓 Router 失敗，
其他功能照常派送。各 workflow 的執行結果由 investment-system 自己寫進它的更新記錄試算表，說明見該 repo 的 `SCHEDULES.md`。

| 功能代號 | 預設時間 | Workflow |
|---|---|---|
| invest_tw_prices | 週一至五 08:30～14:30 每 10 分鐘 | tw-stock-prices.yml（auto；休市日程式略過） |
| invest_us_prices_night | 週一至五 21:00～23:50 每 10 分鐘 | us-stock-prices.yml（auto） |
| invest_us_prices_morning | 週二至六 00:00～06:00 每 10 分鐘 | us-stock-prices.yml（auto） |
| invest_fund_nav | 每日 00:00～23:50 每 10 分鐘 | fund-nav.yml（基金淨值＋Richart 匯率，只寫有變的格子） |
| invest_month_end_ledger | 每月 1–10、28–31 日 00:00～23:50 每 10 分鐘 | month-end-ledger.yml（auto：月底固定、1 日切換總覽） |
| invest_keepalive | 每日每 10 分鐘 | keepalive.yml（也觸發依總覽自動確認基金配息） |
| invest_stock_dividends | 每日每 10 分鐘 | stock-dividends-from-ledger.yml（台股股利：應入帳→細帳備註確認入帳） |
| invest_snapshot | 每日 08:00、20:00 | snapshot.yml |
| invest_stock_radar | 週一至五 14:30 | stock-radar.yml |

這幾列**預設不啟用**。等 investment-system 的 workflow 能接受 `scheduled_slot`（investment-system PR #99）、
tool-system 設好 Secret 之後，在分頁勾選「啟用」；確認派送成功（investment-system 的 Actions 出現 `cloud-…` 的執行）
後，再停用 cron-job.org 上 investment-system 的對應工作。

先前版本的 `invest_month_end`、`invest_fund_freeze`、`invest_month_roll` 已停用（月底固定改由
`invest_month_end_ledger` 派送 month-end-ledger.yml），分頁裡這三列請刪除；Router 會在 log 提醒，這三列不會執行。

---|---|---|
| invest_month_end | 每月月底 23:50 | Jennyall 本月欄：4～36 列的股票區塊（台股、渣打-美股…）＋64～67 列，公式改成值 |
| invest_fund_freeze | 每月 1～7 日 00:30～23:30 每小時 | 4～36 列的基金區塊：總覽 G 欄每檔基金淨值日期都到上月最後一個工作天才轉值；7 日 23:00 後仍沒到齊就照當下值轉並提醒 |
| invest_month_roll | 每月 1～7 日 00:10 | 總覽參照 Jennyall／YYYY細帳 的上月欄換到本月欄；已換好就不動 |

---

## 舊說明（2026-11 以前，逐時段 job）

cron-job.org 以 Asia/Taipei 固定時段 POST 至 `cloud_schedule.yml/dispatches`。
請求 body：`{"ref":"main","inputs":{"timestamp":"%cjo:unixtime%"}}`。
Authorization 使用限定 tool-system 的 GitHub Actions read/write token，僅存於 cron-job.org。

四個雲端定時規則（原本的 00:40 規則已不需要）：

| 規則 | 台灣時間 cron |
|---|---|
| 整點 | `0 0,1,2,5,7,8,10,11,12,18,22 * * *` |
| 10 分 | `10 0,1 * * *` |
| 20 分 | `20 0,1 * * *` |
| 半點 | `30 0,1,5,16 * * *` |

`scripts/cloud_schedule.py` 按請求的時間戳決定任務，非 runner 實際啟動時間。
外場每日 05:00、客服每日 05:30；日排程 01:00/10/20/30、通知 07:00；月底清理 22:00。
業績每日 00:00/08:00/12:00/18:00；富邦明細每日 08:00、工作日另加 11:00/16:30（例假日與國定假日由程式略過）；富邦待處理週二、五 10:00；元大明細每月 25 日 11:00（遇例假日提前到前一個工作日，18～25 日 11:00 由程式判斷）。
月任務為 15 日 12:00 上半月訂單、月底 12:00 下半月訂單／18:00 已退款、1 日 00:00 預收／00:10 儲值金結算／00:20 儲值金預收，明確傳遞期別。
每月 1 日財務（`scheduled_finance.yml`）：00:30 固定費用、01:00 儲值金完整流程、02:00 現金缺口＋預收款金額＋年度review 整理／現金缺口區塊、05:00 排入月初藍新／鯨躍／現金缺口、02:00～23:00 每小時月初檢查（沒完成就建行事曆提醒）；單月 10 日 10:00 會計事務所費用確認提醒。
401 歸檔修正為每月 20 日 00:00（原 UTC 日期換算錯了一天）。

啟用：雲端五個 job 就緒後設定 GitHub repository variable `EXTERNAL_SCHEDULER_ENABLED=true`。
此時 GitHub 原生 schedule 的 job 會略過，手動執行仍保留。Router 在該變數未啟用時只接受 dry_run。
回復：先停用五個外部 job，再將變數設為 false；原 cron 隨即恢復，但仍會有 GitHub 原生觸發延遲。

客服／外場大掃除排班月份可設定 GitHub repository variable
`SERVICE_SCHEDULE_DEEP_CLEAN_START=YYYY-MM-DD`。指定日之前維持本月＋次月；
自指定日（含）起，日排程下載、客服匯入與外場排班統計會處理當月至隔年 2 月，
並逐月縮短（例如 10–2 月、11–2 月、12–2 月）；最低仍保留本月＋次月，
因此 2 月會處理 2 月與 3 月。季節結束後自動維持本月＋次月。
月份資料逐月遞補到固定欄位：`台北台中排班統計表`的台北當月固定從 G 欄、
台中當月固定從 CD 欄開始；`【空班】居家清潔(每日回報)`的台北當月固定從
K 欄、台中當月固定從 CW 欄開始。
外場排班年度檔依「執行日期的年份」切換：2026 年執行期間即使包含 2027 年
1、2 月預排資料，仍寫入 2026 年檔及 `2026排班統計表`；到 2027 年 1 月執行時，
才切換到 2027 年檔及 `2027排班統計表`。系統會優先讀取
`spreadsheet_ids.roster_by_year.<年份>.<地區>`；未設定時，會在目前年度檔的同一
Drive 資料夾尋找檔名年份已替換的下一年度 Google 試算表。

Router 以 concurrency 序列化，使用 scheduled_slot 的執行標題確認是否已派送，避免重試時重複派送。
既有失敗的工作不自動重跑，應檢查原因後使用 GitHub rerun。
執行時間在原時段正負 30 分鐘內正常處理；同日延遲超過 30 分鐘仍派送並記錄 warning，不因超時漏跑。跨日期或超前超過 30 分鐘則回報失敗，需確認資料日期後補跑。外部時鐘前後兩分鐘偏差會正規化到最近的十分鐘時段；這與 runner 等待時間是不同的檢查。
cron-job.org HTTP 成功只代表 GitHub 接受觸發，工作是否成功仍需看 GitHub Actions。
外部排程消除 GitHub schedule 事件延遲，仍不能保證 runner 秒級啟動或服務不中斷。

## 月初財務時段調整（00:10／00:20／00:30／01:00／02:00）

合併前請先改 cron-job.org：
- 整點 job 的 Hours 加 2（02:00 現金缺口）。
- 「01:10」job 改成 `10 0,1 * * *`（Hours 加 0），「01:20」job 改成 `20 0,1 * * *`（Hours 加 0）。
- 00:40 job 停用（儲值金預收改到 00:20）。
- 半點 job 已有 0 點，00:30 不用改。
- 新增「1日 02:00～23:00 每小時｜月初檢查」job：`0 2-23 1 * *`（跟其他整點 job 重疊的時段，Router 會判斷已派送而略過）。
沒改的話 00:10、00:20、02:00 不會觸發。

## 本次時段切換

合併本次改動前，需先在 cron-job.org 的整點 job 將 Hours 的 6 改為 5，半點 job 的 Hours 增加 5（保留 0、1）。未同步雲端設定就只合併程式，會漏掉外場與客服觸發。這份 PR 不會自動修改 cron-job.org。

## 富邦明細時段調整（08:00／11:00／16:30）

合併前請先改 cron-job.org：整點 job 的 Hours 加 11、拿掉 14、16（只剩富邦明細用，已不需要）；
半點 job 的 Hours 加 16。沒改的話 11:00、16:30 不會觸發。
同一地區、同一天的富邦明細下載若累積多筆（Mac 沒開機），本機 Agent 只做最新一筆，其餘標成「已略過」。
