# Lemon Backend Core

`tools/lemon_backend` 是所有需要登入檸檬後台工具的共用層。之後 `invoice_center`、orders、memo 都應共用這裡的登入、session、訂單搜尋與訂單解析，不再各自維護一份後台登入流程。

## 帳密設定

請在 GitHub Repository Secrets 建立下列 key，並由執行環境注入同名環境變數。不要提交帳密、cookie、session 或 token。

| 地區 | 帳號 | 密碼 |
| --- | --- | --- |
| 台北 | `TAIPEI_EMAIL` | `TAIPEI_PASSWORD` |
| 台中 | `TAICHUNG_EMAIL` | `TAICHUNG_PASSWORD` |
| 桃園 | `TAOYUAN_EMAIL` | `TAOYUAN_PASSWORD` |
| 新竹 | `HSINCHU_EMAIL` | `HSINCHU_PASSWORD` |
| 高雄 | `KAOHSIUNG_EMAIL` | `KAOHSIUNG_PASSWORD` |

可選設定：

- `LEMON_BACKEND_ENV=prod|dev`，預設 `prod`
- `LEMON_BACKEND_BASE_URL`，需要覆寫後台網址時使用

## 主要入口

```python
from tools.lemon_backend import BackendClient

client = BackendClient("台北")
order = client.get_order("LC00212058")
```

`BackendClient` 目前提供：

- `login()`
- `search_order(order_no)`
- `get_order(order_no)`
- `get_purchase_page(params)`
- `get_booking_page(payway)`，信用卡/ATM 使用 `/booking/single`，儲值金使用 `/booking/stored_value_routine`
- `search_orders_by_phone(phone)`
- `update_invoice_no(order_no, invoice_no)`，介面保留，尚未實作
- `update_allowance_no(order_no, allowance_no, amount=None)`，介面保留，尚未實作

## 搬移範圍

- 從 `orders-system` 搬入後台登入、purchase list 查詢、訂單卡片解析、付款/發票/金額/服務日期等解析邏輯。
- 從 `memo-system` 搬入需要登入後台的 memo/address search 架構。
- 評估文字工具不需要登入後台，不放進 `lemon_backend`。

## 後續

- `invoice_center` 之後應改用 `BackendClient.get_order()` 取得 Lemon 訂單資料後再建 EI 發票 payload。
- 需要實測後台 edit form 後，再補 `update_invoice_no()` 與 `update_allowance_no()` 的正式送出流程。
