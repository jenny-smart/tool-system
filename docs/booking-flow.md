# 後台成立訂單流程（依系統操作）

以批次成單的邏輯為基準，所有成單入口只照後台表單操作，不查 Google Map、不猜區域。

## 兩個入口（差在付款方式）

| 付款方式 | 後台網址 | 班表 | 發票 |
|---|---|---|---|
| ATM／信用卡 | `/booking/single` | 只能勾 1 個時段（`datePeriod`） | 必填 |
| 儲值金 | `/booking/stored_value_routine` | 可勾多個時段（`date_list[]`） | 不填 |

程式：`tools/orders_system/booking_flow.py`（`route_for`、`build_submit_data`、`build_submit_pairs`）。

## 共用步驟

| 步驟 | 後台操作 | 程式 |
|---|---|---|
| 1 | 選購買項目 | `clean_type_id` |
| 2 | 輸入手機 → 查詢會員。有姓名/mail＝舊客；查無會員＝新客，需拆解訂單資訊填姓名/mail | `get_member`；新客 `parse_new_customer_text` → `/ajax/create_member` |
| 3 | 選縣市／鄉鎮市區，其餘地址填空白欄 → 查詢地區 | `_split_booking_address` → `resolve_backend_booking_address`（舊客下拉已有地址只選 `addressId`；新地址才按後台「查詢地區」`query_native_address`） |
| 4 | 輸入坪數、區域數量／需求 → 計算時數 | `calculate_hour` |
| 5 | 看時數/人數，需要再調整 → 查詢班表 | `hour`、`person` → `get_all_sections_raw` |
| 6 | 勾選時段。找不到＝該時段無法服務；仍要該時段 → 專員管理/搜尋檸檬人，勾該日期時段班表後儲存，再重查 | `slot_exists_in_section_response`；補班 `ensure_lemon_cleaner_shifts`（`allow_auto_lemon_shift=True` 才會做） |
| 7 | 選付款方式 ATM/信用卡（儲值金不選） | `route.payway_code` |
| 8 | 發票：捐贈／個人二聯式／三聯式 | `invoice_type` 1/2/3 |
| 9 | 二聯式選載具：會員載具（預設 mail）／手機載具（填 `/XXXXXXX`）／紙本 | `carrier_type_id`、`carrier_info` |
| 10 | 優惠代碼（有才填） | `discount_code` |
| 送出 | 跳到訂單管理頁並出現訂單編號；儲值金不足則沒有訂單編號 | `_find_matching_order_after_submit`；不足時 `stored_value_failed_count` |

新客拆解範例：

```
訂購人姓名：陳佳吟          → name
訂購人電話：0910828425      → phone（查無會員才算新客）
訂購人Email：chiayin24@gmail.com → email
服務地址：108臺北市萬華區…   → 去掉郵遞區號 → 台北市／萬華區／華江里環河南路二段250巷42弄5號3
室內坪數 :28                → ping 代碼 3（21–30 坪）
付款方式： 信用卡           → /booking/single，payway=1
發票載具：手機載具 /1P06KYP  → invoice_type=2，carrier_type_id=2，carrier_info=/1P06KYP
```

## 各功能是否共用同一流程

| 功能 | 入口函式 | 共用核心 |
|---|---|---|
| 舊客成單 | `quick_create_order` | ✅ 核心本身 |
| 新客成單 | `quick_create_new_customer_order` | ✅ 建會員後呼叫 `quick_create_order` |
| 保留單 | `reserve_optimizer` | ✅ `quick_create_order`（不自動補班） |
| 訂單轉換 | `convert_order`、`convert_order_stage2_create_new_orders` | ✅ `quick_create_order` |
| 儲值金補差價 | `stored_value_makeup_create_stored_order`／`_paid_order` | ✅ 兩張單都走 `quick_create_order` |
| VIP 行事曆 | `vip_calendar_sync` | ✅ `quick_create_order` |
| 批次成單（儲值金） | `orders.process_one_group` | ⚠️ 共用步驟 2–6（`get_member`、`resolve_backend_booking_address`、`calculate_hour`、`get_all_sections_raw`），但送出自組 `date_list[]`，沒有走 `booking_flow.build_submit_pairs` |

## 已知差異與處理狀態

1. **已修正**：地址前面帶郵遞區號（`108臺北市…`）時，縣市會被判成「108台北市」，對不到後台下拉。現在 `_split_booking_address` 會先去掉開頭郵遞區號。
2. **已修正**：查舊客歷史訂單時，遇到購物金、現金等其他付款方式會丟出「不支援的付款方式」讓畫面崩潰。讀舊單改用 `_read_payway`，不認得的付款方式只略過；成立訂單時仍嚴格檢查。
3. **已支援**：新客可填優惠代碼（畫面欄位，或貼上資料中的「優惠代碼：XXX」）。
4. **已支援**：新客／舊客發票都可選捐贈（含捐贈碼，預設 8585）與紙本。貼上資料寫「發票：捐贈 919」或「發票：紙本」也會自動辨識。
5. **已支援**：新客／舊客都可用「建立筆數」選多個日期或時段，每筆各自走一次共用流程送出。新客從第 2 筆起不再誤判成舊客；查到其實是舊客、改用舊客身份送出時，也會送出全部筆數。
6. 批次成單的送出資料沒有走 `booking_flow`（保留 `date_s`，也不移除發票欄位）。這條路目前可正常運作，所以沒有改動。
