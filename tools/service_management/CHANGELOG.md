# tool-system Change Log

最後更新：2026-08-12（台北時間）

## 2026-08-12

- `toolapp.py`：拆分客服 CRM 與儲值功能入口；排班、CRM、儲值皆支援全區、台北、台中。
- `tools/service_management/stored_value.py`：建立儲值功能獨立入口。
- `tools/service_management/crm_export.py`：儲值流程改為下載結算 Excel、覆寫固定試算表分頁並更新日期名稱。
- `tools/service_management/crm.py`：建立獨立 CRM 入口；正式規格完成前不執行資料寫入。

## 維護規則

- 每次修改程式檔，必須同步更新檔頭的「最後更新」及 Change Log。
- 舊紀錄保留，最新紀錄置頂。
- 日期一律使用台北時間（`YYYY-MM-DD`）。
