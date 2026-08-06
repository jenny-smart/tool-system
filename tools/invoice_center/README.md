# 發票中心 MVP

`tools/invoice_center` 是 EI 發票系統的初版 scaffold，目標先建立多地區帳密、payload builder、查詢發票號碼與 Streamlit UI 骨架。預設所有開立流程都是 dry-run preview，不會真的送出 EI，除非呼叫端明確傳 `dry_run=False`。

## 環境變數

請在 GitHub Repository Secrets 建立下列 key，並由執行環境注入為同名環境變數；不要把帳密、cookie 或 token 寫進 repo。

| 地區 | 帳號 | 密碼 |
| --- | --- | --- |
| 台北 | `TAIPEI_EI_USERID` | `TAIPEI_EI_PASSWORD` |
| 台中 | `TAICHUNG_EI_USERID` | `TAICHUNG_EI_PASSWORD` |
| 桃園 | `TAOYUAN_EI_USERID` | `TAOYUAN_EI_PASSWORD` |
| 新竹 | `HSINCHU_EI_USERID` | `HSINCHU_EI_PASSWORD` |
| 高雄 | `KAOHSIUNG_EI_USERID` | `KAOHSIUNG_EI_PASSWORD` |

若 EI 需要先從 `admin.jsp?id=...&auth=...` 進入租戶入口，請另外設定：

- `TAIPEI_EI_ENTRY_URL`
- `TAICHUNG_EI_ENTRY_URL`
- `TAOYUAN_EI_ENTRY_URL`
- `HSINCHU_EI_ENTRY_URL`
- `KAOHSIUNG_EI_ENTRY_URL`

入口 URL 內的 `id/auth` 也視為秘密資訊，不要提交到 repo。

Streamlit 環境也可用同名 `st.secrets` key 注入；程式只讀取設定狀態，不會在 UI 顯示秘密內容。

## 主要入口

- `EIInvoiceClient(area)`：使用 `requests.Session` 管理 EI session。
- `auth.login(client, captcha=...)`：保留登入介面，captcha 由外部傳入，不做自動破解。
- `build_detaildata(items)`：用 `goodcode|goodname|unit|quantity|unitprice|amount|fremark` 組成明細，再做 UTF-8 base64 encode。
- `build_add_invoice_payload(payload)`：建立 `addInvoice.action` 表單 payload。
- `query_invoice_by_order_id(order_id, date1, date2, area=...)`：查詢 `common/invoice/invoicelist.jsp` 並嘗試擷取發票號碼。
- `query_invoices_by_period(date1, date2, area=..., paper_only=False)`：查詢某期間全部發票，並可過濾紙本 / 三聯資料。
- `fetch_backend_order_invoice_payload(area, order_no, suffix="-1")`：登入 `https://backend.lemonclean.com.tw` 查訂單，帶入姓名、電話、Email、地址、金額、付款方式與發票資訊。
- `preview_invoice_from_order(area, order_no, suffix="-1")`：用 Lemon 訂單號預覽 EI payload，預設 `LC00212058 -> LC00212058-1`。
- `create_invoice_from_payload(payload, dry_run=True)`：預設只回傳 payload preview；`dry_run=False` 才會 POST 到 EI。
- `lemon_invoice_api.make_invoice(purchase_id, invoice_type=...)`：呼叫 Lemon 發票 API 開立後台訂單發票。
- `render_invoice_center()`：Streamlit UI 骨架，可由 `toolapp.py` 或其他主頁 import。

## Payload 預設

一般發票預設值：

- `invoicetype=07`
- `taxtype=1`
- `donate=0`
- `hastax=2`
- `hasapply=1`
- `rate=0.05`
- `roundnum=4`
- `carriertype=EJ0011`

個人無統編時，`taxamount` 預設為 `0`，`totalamount=saleamount`。如需公司戶或特殊稅別，呼叫端可覆寫 `taxamount`、`totalamount`、`taxtype`、`zerotype`、`zeroreason` 等欄位。

## 目前限制

- EI 登入可能有 captcha，本模組只接受外部傳入 captcha，不處理辨識。
- Lemon 訂單資料已透過 `tools.lemon_backend.BackendClient` 帶入；後台頁面若調整欄位文字，載具/統編解析需再校正。
- Streamlit UI 的正式開立按鈕需勾選確認才會呼叫 Lemon 發票 API。
- 折讓單 API 尚待補齊。
- 發票查詢頁會列出可解析到的下載連結並提供 CSV；若 EI 另有批次 PDF/XML 匯出 endpoint，待實測後補一鍵批次下載。
- EI 實際頁面欄位若與目前假設不同，需用測試帳號確認後調整 login/query payload。

## 本機批次下載 EI 匯出檔

登入鯨躍第一層及指定地區 EI 第二層、不匯出資料：

```bash
python3 -m tools.invoice_center.cetustek_login_only --area 台中
```

帳密可放在本機，不提交到 GitHub：

```text
~/EI account/ei_accounts.json
```

範例：

```json
{
  "common": {
    "entry_url": "https://www.ei.com.tw/InvoiceRent/index.jsp?id=...&auth=..."
  },
  "taipei": {
    "label": "台北",
    "userid": "EI帳號",
    "password": "EI密碼"
  }
}
```

第一層入口帳密可放在同一個檔案的 `common`。程式會自動預填帳密，驗證碼仍由使用者直接在網頁輸入：

```json
{
  "common": {
    "portal_company_id": "第一層統一編號",
    "portal_account": "第一層會員帳號（主帳號可留空）",
    "portal_password": "第一層密碼"
  }
}
```

若未設定第一層帳密，瀏覽器仍會停在第一層登入頁，讓使用者手動輸入。
程式預設連接已開啟的 Chrome，不會每次另開視窗。第一次先開啟可供程式連接、且會保留 Cookie 的 Chrome：

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/EI account/chrome_profile"
```

這個 Chrome 視窗保持開啟即可。第一層成功登入一次後會保留 Cookie；後續程式會直接連接同一視窗。執行全部區域時，也只登入第一層一次，接著逐區完成第二層登入、匯出及登出。

下載單區整月：

```bash
python3 tools/invoice_center/ei_export_all.py 202606 --area 台北
```

未指定區域時，會下載帳密檔內全部已設定區域：

```bash
python3 tools/invoice_center/ei_export_all.py 202606
```

預設會存到：

```text
~/Library/CloudStorage/GoogleDrive-jenny@lemonclean.com.tw/我的雲端硬碟/lemon_人事/03 服務分潤表/2026專員承攬服務費/02.台北專員/202606-2/
```

檔名：

```text
202606-2發票-台北.csv
202606紙本發票-台北.csv
```

全部發票與紙本發票固定存放在相同的 `YYYYMM-2` 資料夾；每區下載完成後都會登出第二層帳號。

## Tool System / Local Agent

Tool System 的「財務管理」可建立以下本機任務：

- `【鯨躍發票】登入`
- `【鯨躍發票】下載`

先保持鯨躍 Chrome 開啟：

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/EI account/chrome_profile"
```

再啟動 Local Agent：

```bash
cd "/Users/jenny/Documents/New project/codex-workspace/apps/tool-system"
python3 -m tools.local_agent
```

任務狀態會寫入「本機Agent任務」，完整 Log 會分段寫入「本機Agent任務Log」。
Agent 採共用 action registry；後續富邦、元大、藍新只需註冊新的 action handler。
