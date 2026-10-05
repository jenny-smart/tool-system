# 雲端定時觸發

cron-job.org 以 Asia/Taipei 固定時段 POST 至 `cloud_schedule.yml/dispatches`。
請求 body：`{"ref":"main","inputs":{"timestamp":"%cjo:unixtime%"}}`。
Authorization 使用限定 tool-system 的 GitHub Actions read/write token，僅存於 cron-job.org。

五個雲端定時規則：

| 規則 | 台灣時間 cron |
|---|---|
| 整點 | `0 0,1,5,7,8,10,12,14,16,17,18,22 * * *` |
| 01:10 | `10 1 * * *` |
| 01:20 | `20 1 * * *` |
| 半點 | `30 0,1,5 * * *` |
| 00:40 | `40 0 * * *` |

`scripts/cloud_schedule.py` 按請求的時間戳決定任務，非 runner 實際啟動時間。
外場每日 05:00、客服每日 05:30；日排程 01:00/10/20/30、通知 07:00；月底清理 22:00。
業績每日 00:00/08:00/12:00/18:00；富邦明細 08:00/10:00/12:00/14:00/16:00；富邦待處理週二、五 10:00。
月任務為 15 日 17:00、月底 17:00/18:00、1 日 00:00/00:30/00:40，明確傳遞期別。
401 歸檔修正為每月 20 日 00:00（原 UTC 日期換算錯了一天）。

啟用：雲端五個 job 就緒後設定 GitHub repository variable `EXTERNAL_SCHEDULER_ENABLED=true`。
此時 GitHub 原生 schedule 的 job 會略過，手動執行仍保留。Router 在該變數未啟用時只接受 dry_run。
回復：先停用五個外部 job，再將變數設為 false；原 cron 隨即恢復，但仍會有 GitHub 原生觸發延遲。

Router 以 concurrency 序列化，使用 scheduled_slot 的執行標題確認是否已派送，避免重試時重複派送。
既有失敗的工作不自動重跑，應檢查原因後使用 GitHub rerun。
執行時間在原時段正負 30 分鐘內正常處理；同日延遲超過 30 分鐘仍派送並記錄 warning，不因超時漏跑。跨日期或超前超過 30 分鐘則回報失敗，需確認資料日期後補跑。外部時鐘前後兩分鐘偏差會正規化到最近的十分鐘時段；這與 runner 等待時間是不同的檢查。
cron-job.org HTTP 成功只代表 GitHub 接受觸發，工作是否成功仍需看 GitHub Actions。
外部排程消除 GitHub schedule 事件延遲，仍不能保證 runner 秒級啟動或服務不中斷。

## 本次時段切換

合併本次改動前，需先在 cron-job.org 的整點 job 將 Hours 的 6 改為 5，半點 job 的 Hours 增加 5（保留 0、1）。未同步雲端設定就只合併程式，會漏掉外場與客服觸發。這份 PR 不會自動修改 cron-job.org。
