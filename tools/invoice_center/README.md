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
