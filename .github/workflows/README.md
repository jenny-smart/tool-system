# GitHub Actions 排程說明

時間一律為**台灣時間**（workflow 檔裡的 cron 是 UTC，台灣＝UTC＋8）。
所有排程都可以到 GitHub → Actions → 選左邊的排程 → **Run workflow** 手動執行。

## 觸發方式

| 方式 | 說明 |
|---|---|
| GitHub 內建 cron | 各 workflow 檔的 `schedule:`。GitHub 常延遲幾分鐘到一小時。 |
| 外部準時觸發（Cloud Schedule Router） | cron-job.org 在固定時段呼叫 `cloud_schedule.yml`，由 `scripts/cloud_schedule.py` 依時段派送對應的 workflow。repository variable `EXTERNAL_SCHEDULER_ENABLED=true` 時啟用，此時各 workflow 自己的 cron 會略過，改由 Router 觸發。詳見 `docs/cloud-schedule.md`。 |

> 改排程時間要同時改三個地方：workflow 檔的 cron、`scripts/cloud_schedule.py`、cron-job.org 的時段（以及 `docs/cloud-schedule.md`）。

## 排程一覽

| Workflow（檔案） | 時間 | 在哪裡執行 | 內容 |
|---|---|---|---|
| **Cloud Schedule Router**（`cloud_schedule.yml`） | cron-job.org 每個固定時段 | GitHub | 排程分派器：依時間戳決定要啟動哪些排程，避免 GitHub cron 延遲。 |
| **Scheduled Daily Jobs**（`scheduled_daily.yml`） | 每日 01:00 日排程、01:10 工作人員排班、01:20 訂單資料、01:30 工作人員資料、07:00 寄送結果通知；月底 22:00 清空 @日排程來源資料夾 | GitHub | 日排程系列。手動可選 target：`all`／`schedule_report`／`staff_schedule`／`orders_report`／`staff_info`／`notify`／`month_end_cleanup`。 |
| **Scheduled Field Jobs**（`scheduled_field.yml`） | 每日 05:00 | GitHub | 外場排程：排班統計表、專員班表、訂單、專員個資。手動可選 `field_all` 或單項。 |
| **Scheduled Service Jobs**（`scheduled_service.yml`） | 每日 05:30 | GitHub | 客服排程：排班統計、每日回報、營收。手動可選 `service_all` 或單項。 |
| **Performance Report**（`performance_report.yml`） | 每日 00:00、08:00、12:00、18:00 | GitHub | 業績報表。 |
| **Scheduled Change Order Daily Check**（`scheduled_change_order_check.yml`） | 每日 10:00 | GitHub | 清潔異動每日檢查：有未備妥的列時在 jenny@lemonclean.com.tw 行事曆建提醒。 |
| **Scheduled Fubon Pending Check**（`scheduled_fubon_pending_check.yml`） | 每週二、五 10:00 | GitHub | 富邦待辦通知：盤點台北／台中待付款的請款記錄、工具包押金退款、清潔用品採購，在行事曆提醒。 |
| **Scheduled Fubon Statement Download**（`scheduled_fubon_statement.yml`） | 每日 08:00；工作日另加 11:00、16:30（週六日與國定假日只跑 08:00） | **本機 Agent（Mac）** | 見下方說明。 |
| **Scheduled Yuanta Statement Download**（`scheduled_yuanta_statement.yml`） | 每月 25 日 11:00；遇例假日提前到前一個工作日 | **本機 Agent（Mac）** | 見下方說明。 |
| **Scheduled Monthly Jobs**（`scheduled_monthly.yml`） | 15 日 17:00 上半月訂單；月底 17:00 下半月訂單、18:00 已退款；1 日 00:00 預收、00:30 儲值金結算、00:40 儲值金預收 | GitHub | 月排程。手動可選 target 與期別。 |
| **Scheduled Gmail 401 Archive**（`Scheduled_gmail_401.yml`） | 每月 20 日 00:00 | GitHub | 把 Gmail 主旨含「401」信件的 401 PDF 附件，依檔名關鍵字存到各區雲端資料夾的西元年子資料夾（設定在 `systems.yaml` 的 `gmail_401`），處理過的信加上「401已歸檔」標籤。 |
| **New Year Generate**（`new_year_generate.yml`） | 只能手動 | GitHub | 年度切換：依主控表「生成新年度」產生新年度資料夾／檔案。參數：年度、列、`dry_run`（預設只預覽）、`fresh`。 |

## 富邦明細下載（唯一經過 Mac 的排程）

- GitHub **不會登入富邦**，只在主控表「本機Agent任務」新增兩筆 `fubon.download`（台北、台中；查詢開始日＝財報「富邦更新」最後一列的日期，Agent 執行時才讀，結束日＝今天）。
- 由 Mac 上的本機 Agent 執行：接續或登入富邦網銀 → 下載「財報最後一列日期～今天」的明細 → 登出。已登記過的明細會比對濾掉，只新增還沒登記的，所以重疊的日期不會重複寫入。需要驗證碼／手機核准時會停在那一步等人工。
- 11:00、16:30 這兩次在例假日（週六日、國定假日，依 `tools/common/tw_holidays.py` 的政府行事曆）不排任務。
- **Mac 沒開機時**：任務會排隊，開機後 Agent 依序執行；同一地區、同一天累積多筆時只做最新一筆，較舊的標成「已略過（有較新的同日任務）」。不同日期的任務仍會補跑。

## 元大明細下載

- 每月 25 日 11:00，遇例假日（週六日、國定假日）提前到前一個工作日（`scheduled_yuanta_statement.yml`，每月 18～25 日 11:00 叫起來，由 `tools/scheduled_daily/yuanta_statement_queue.py` 判斷是不是本月執行日）。
- 一樣只在主控表新增 `yuanta.download`（台北、台中），由 Mac 上的本機 Agent 執行；查詢開始日＝財報「元大更新」最後一列的日期，結束日＝今天。已登記的明細會比對濾掉。

## 共通

- 各排程的執行結果會寫入主控表的執行 Log（`TOOLS_APP_LOG_SPREADSHEET_ID`）；詳細 log 在 GitHub Actions 該次執行裡。
- 需要的帳密、憑證都放在 GitHub repository secrets（例如 `GOOGLE_SERVICE_ACCOUNT`、`GOOGLE_OAUTH_*`、`GOOGLE_CALENDAR_*`、各區後台帳密）。
