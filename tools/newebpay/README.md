# 藍新各區收款／退款下載

輸入 `YYYYMM` 後，程式逐區開啟企業登入。每區需人工查看並輸入一次驗證碼，之後自動下載：

```text
YYYYMM-2藍新收款-區域.csv
YYYYMM-2藍新退款-區域.csv
```

預設期間為下半月（16 日～月底）。可用 `--half 1` 改成 1～15 日，或用 `--half full` 下載整月。

## 1. 安裝

在 `tool-system` 目錄執行：

```bash
python3 -m pip install -r requirements.txt
```

程式使用電腦已安裝的 Google Chrome，不需另存 Chrome 登入資料。
Tool System 任務會透過共用 Local Agent 與 `http://127.0.0.1:9222`，沿用「鯨躍藍新」Chrome。

## 2. 各區帳密另存

建議執行設定程式，一次輸入台北、台中、新北、桃園、新竹、高雄帳密：

```bash
python3 tools/newebpay/setup_accounts.py
```

只修改部分區域（其他區域設定不變）：

```bash
python3 tools/newebpay/setup_accounts.py --area 台中 新竹 高雄
```

程式會建立單一帳密檔：

```text
~/NewebPay account/newebpay_accounts.json
```

所有區域放在同一個 JSON：

```json
{
  "areas": [
    {
      "area": "台北",
      "company_id": "公司統編",
      "account": "管理者帳號",
      "password": "管理者密碼",
      "merchant_label": ""
    },
    {
      "area": "台中",
      "company_id": "公司統編",
      "account": "管理者帳號",
      "password": "管理者密碼",
      "merchant_label": ""
    }
  ]
}
```

設定程式會依序加入台北、台中、新北、桃園、新竹、高雄。帳密放在專案外，不會提交到 Git。

若同一登入帳號管理多個藍新商店，可在 `merchant_label` 填商店選單中可辨識的文字，例如 `檸檬專業清潔`；留空表示使用「全部」。

## 3. 執行

只登入藍新、不查詢或下載：

```bash
python3 -m tools.newebpay.login_only --area 台中
```

Tool System / Local Agent：

```bash
python3 -m tools.local_agent
```

支援 action：`newebpay.login`、`newebpay.download`。下載 action 使用 UI 的開始、結束日期，結果與檔案位置回寫 Queue Log。

下載全部已設定區域的下半月資料：

```bash
python3 tools/newebpay/download_reports.py 202606
```

只下載台北：

```bash
python3 tools/newebpay/download_reports.py 202606 --area 台北
```

下載整月：

```bash
python3 tools/newebpay/download_reports.py 202606 --half full
```

預設輸出到 Google Drive 人事目錄，例如台北 202606 下半月：

```text
~/Library/CloudStorage/GoogleDrive-jenny@lemonclean.com.tw/我的雲端硬碟/lemon_人事/03 服務分潤表/2026專員承攬服務費/01.台北專員/202606-2/
```

各區資料夾：

```text
01.台北專員
02.台中專員
03.新北專員
04.桃園專員
05.新竹專員
06.高雄專員
```

藍新若改版導致欄位找不到，程式會把當時畫面及 HTML 存到輸出資料夾的 `debug/`。

## 4. 只查發票金額（新增流程）

這是獨立流程，不影響上面的收款／退款 CSV 下載。

```bash
# 202606 歸屬月份：查藍新畫面 2026-07-01 的發票
python3 tools/newebpay/invoice_amounts.py 202606 --area 台北

# 202605～202606：查 2026-06-01、2026-07-01
python3 tools/newebpay/invoice_amounts.py 202605-202606 --area 台北

# 多區一次執行
python3 tools/newebpay/invoice_amounts.py 202605-202606 --area 台北 台中 桃園
```

同一地區會把所有發票日合併成一次查詢，因此每區只需登入一次、輸入一次驗證碼。結果直接顯示於終端機，預設不產生檔案；需要 CSV 時才加上 `--output 檔名.csv`。
