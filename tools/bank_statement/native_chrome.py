from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from datetime import date
from pathlib import Path

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.capture import CapturedTable, copy_to_clipboard
from tools.bank_statement.open_login import parse_date
from tools.bank_statement.sheet_filter import read_and_filter


BANK_HOST = "ebank.taipeifubon.com.tw"
TAB_MARKER = f"bank-statement-{os.getpid()}"
BANK_URL = f"https://ebank.taipeifubon.com.tw/B2C/common/Index.faces#{TAB_MARKER}"
HEADER_WORDS = ("交易日", "帳務日", "交易時間", "交易摘要", "摘要", "支出金額", "存入金額", "餘額", "備註")


def _compact(source: str) -> str:
    return " ".join(line.strip() for line in source.splitlines() if line.strip())


def execute_bank_js(source: str) -> str:
    javascript = _compact(source)
    quoted = json.dumps(javascript, ensure_ascii=False)
    script = f"""
tell application "Google Chrome"
  repeat with wi from 1 to count of windows
    repeat with ti from 1 to count of tabs of window wi
      if (URL of tab ti of window wi contains "{TAB_MARKER}") then
        set active tab index of window wi to ti
        set index of window wi to 1
        activate
        return execute active tab of front window javascript {quoted}
      end if
    end repeat
  end repeat
  if (count of windows) > 0 then
    if (URL of active tab of front window contains "{BANK_HOST}") then
      activate
      return execute active tab of front window javascript {quoted}
    end if
  end if
  repeat with wi from 1 to count of windows
    repeat with ti from 1 to count of tabs of window wi
      if (URL of tab ti of window wi contains "{BANK_HOST}") then
        set active tab index of window wi to ti
        set index of window wi to 1
        activate
        return execute active tab of front window javascript {quoted}
      end if
    end repeat
  end repeat
  return "BANK_TAB_NOT_FOUND"
end tell
"""
    result = subprocess.run(
        ["osascript", "-"], input=script, text=True, capture_output=True, check=False
    )
    if result.returncode:
        message = (result.stderr or result.stdout).strip()
        if "AppleScript" in message and "JavaScript" in message:
            raise RuntimeError(
                "請在 Chrome 開啟：顯示方式 → 開發人員 → 允許來自 Apple 事件的 JavaScript"
            )
        raise RuntimeError(message or "無法控制 Chrome")
    output = result.stdout.strip()
    if output == "BANK_TAB_NOT_FOUND":
        raise RuntimeError("找不到已開啟的富邦 Chrome 分頁")
    return output


FRAME_DOCUMENT = "document.querySelector('#frame1')?.contentDocument?.querySelector('#txnFrame')?.contentDocument"


def claim_bank_tab() -> None:
    marker = json.dumps(TAB_MARKER)
    execute_bank_js(
        f"history.replaceState(null, '', location.pathname + location.search + '#' + {marker}); 'CLAIMED'"
    )


def page_state() -> dict[str, object]:
    output = execute_bank_js(
        f"""
        (() => {{
          const outer = document.querySelector('#frame1')?.contentDocument;
          const outerUrl = outer?.location?.href || '';
          if (outerUrl.includes('NotAuth.jsp') && outerUrl.includes('dupLogin')) {{
            return JSON.stringify({{ready:false, duplicate_login:true, url:outerUrl}});
          }}
          const d = {FRAME_DOCUMENT};
          if (!d) return JSON.stringify({{ready:false, prelogin:outerUrl.includes('PreLogin'), top:location.href, outer:outerUrl}});
          const text = d.body?.innerText || '';
          return JSON.stringify({{
            ready:true,
            title:d.title,
            url:d.location.href,
            deposit:[...d.querySelectorAll('a.menu_CBO03.task_CBOQU003')].some(e => e.getClientRects().length),
            quick:[...d.querySelectorAll('*')].some(e => e.children.length===0 && e.textContent.trim()==='快速功能' && e.getClientRects().length),
            statement:[...d.querySelectorAll('*')].some(e => e.children.length===0 && e.textContent.trim()==='交易明細查詢' && e.getClientRects().length),
            query:text.includes('查詢期間') || text.includes('自訂查詢'),
            result:text.includes('支出金額') && text.includes('存入金額') && (text.includes('帳面餘額') || text.includes('餘額')),
            prelogin:d.location.href.includes('PreLogin')
          }});
        }})()
        """
    )
    return json.loads(output)


def fill_fubon_login(account: object, timeout: float = 30.0) -> None:
    values = json.dumps(
        [account.login_id, account.user_code, account.password], ensure_ascii=False
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = execute_bank_js(
            f"""
            (() => {{
              const docs=[];
              const walk=d => {{
                docs.push(d);
                [...d.querySelectorAll('iframe,frame')].forEach(f => {{
                  try {{ if (f.contentDocument) walk(f.contentDocument); }} catch(_) {{}}
                }});
              }};
              walk(document);
              for (const d of docs) {{
                const text=d.body?.innerText || '';
                if (!text.includes('身分證字號') || !text.includes('使用者代碼')) continue;
                const inputs=[...d.querySelectorAll('input')].filter(x =>
                  x.type!=='hidden' && x.type!=='button' && x.type!=='submit' && x.getClientRects().length
                );
                if (inputs.length < 4) continue;
                const vals={values};
                for (let i=0;i<3;i++) {{
                  const input=inputs[i];
                  const setter=Object.getOwnPropertyDescriptor(
                    input.ownerDocument.defaultView.HTMLInputElement.prototype,'value'
                  ).set;
                  setter.call(input, vals[i]);
                  input.dispatchEvent(new Event('input',{{bubbles:true}}));
                  input.dispatchEvent(new Event('change',{{bubbles:true}}));
                  input.dispatchEvent(new Event('blur',{{bubbles:true}}));
                }}
                inputs[3].focus();
                return 'FILLED';
              }}
              const login=docs.flatMap(d => [...d.querySelectorAll('[id="header_form:header_login"]')])
                .find(x => x.getClientRects().length);
              if (login) {{ login.click(); return 'OPENED'; }}
              return 'WAIT';
            }})()
            """
        )
        if result == "FILLED":
            print("富邦帳密已由本機 JSON 預填；請輸入驗證碼並按『登入』。")
            return
        time.sleep(0.5)
    raise RuntimeError("找不到富邦登入欄位，無法預填帳密")


def wait_for_manual_login(account: object, timeout: float = 30.0) -> dict[str, object]:
    try:
        state = page_state()
    except RuntimeError as exc:
        if "找不到已開啟" not in str(exc):
            raise
        subprocess.run(["open", "-a", "Google Chrome", BANK_URL], check=False)
        print("已開啟正常 Chrome，請手動輸入驗證碼並登入富邦。")
        state = {}
        tab_deadline = time.monotonic() + 20
        while time.monotonic() < tab_deadline:
            try:
                state = page_state()
                break
            except RuntimeError as tab_exc:
                if "找不到已開啟" not in str(tab_exc):
                    raise
                time.sleep(0.5)
        if not state:
            raise RuntimeError("Chrome 已開啟，但富邦分頁尚未載入，請再執行一次")
    claim_bank_tab()
    state = page_state()
    if not state.get("ready") or state.get("prelogin"):
        fill_fubon_login(account)
    deadline = time.monotonic() + timeout
    announced = False
    while time.monotonic() < deadline:
        try:
            state = page_state()
        except RuntimeError:
            time.sleep(1)
            continue
        if state.get("duplicate_login"):
            raise RuntimeError("富邦偵測到重複登入；請完全關閉其他富邦分頁，稍後重新登入")
        if state.get("ready") and not state.get("prelogin") and (
            state.get("deposit") or state.get("quick") or state.get("query")
        ):
            print("已偵測到富邦登入成功，開始擷取明細。")
            return state
        if not announced:
            print("等待你在 Chrome 完成富邦登入（最長 30 秒）……")
            announced = True
        time.sleep(1)
    raise RuntimeError("等待富邦登入逾時，請重新執行指令")


def wait_state(key: str, timeout: float = 30.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        last = page_state()
        if last.get("duplicate_login"):
            raise RuntimeError("富邦偵測到重複登入；請先關閉自動測試 Chrome，再用正常 Chrome 重新登入一次")
        if last.get(key):
            return last
        if last.get("prelogin"):
            raise RuntimeError("富邦工作階段已登出，請重新手動登入")
        time.sleep(0.5)
    raise RuntimeError(f"等待富邦頁面狀態逾時：{key}；目前：{last}")


def click_deposit() -> None:
    result = execute_bank_js(
        f"""
        (() => {{
          const d = {FRAME_DOCUMENT};
          if (!d) return 'NO_FRAME';
          const items = [...d.querySelectorAll('a.menu_CBO03.task_CBOQU003')];
          const e = items.find(x => x.getClientRects().length);
          if (!e) return 'NOT_FOUND';
          e.click();
          return 'CLICKED';
        }})()
        """
    )
    if result != "CLICKED":
        raise RuntimeError(f"找不到可見的『我的存款』：{result}")


def click_quick(account_suffix: str) -> None:
    suffix = json.dumps(account_suffix)
    result = execute_bank_js(
        f"""
        (() => {{
          const d = {FRAME_DOCUMENT};
          if (!d) return 'NO_FRAME';
          const row=[...d.querySelectorAll('tr')].find(r => r.textContent.replace(/\D/g,'').includes({suffix}));
          if (!row) return 'ACCOUNT_NOT_FOUND';
          const e=[...row.querySelectorAll('*')].find(x => x.children.length===0 && x.textContent.trim()==='快速功能');
          if (!e) return 'QUICK_NOT_FOUND';
          const target=e.closest('a,button,[onclick]') || e;
          target.dispatchEvent(new MouseEvent('mouseover',{{bubbles:true,view:d.defaultView}}));
          target.click();
          return 'CLICKED';
        }})()
        """
    )
    if result != "CLICKED":
        raise RuntimeError(f"無法開啟帳號快速功能：{result}")


def click_statement() -> None:
    result = execute_bank_js(
        f"""
        (() => {{
          const d = {FRAME_DOCUMENT};
          if (!d) return 'NO_FRAME';
          const e=[...d.querySelectorAll('*')].find(x => x.children.length===0 && x.textContent.trim()==='交易明細查詢' && x.getClientRects().length);
          if (!e) return 'NOT_FOUND';
          (e.closest('a,button,[onclick]') || e).click();
          return 'CLICKED';
        }})()
        """
    )
    if result != "CLICKED":
        raise RuntimeError(f"無法點擊交易明細查詢：{result}")


def set_dates_and_query(start: date, end: date) -> None:
    start_text = json.dumps(start.strftime("%Y/%m/%d"))
    end_text = json.dumps(end.strftime("%Y/%m/%d"))
    result = execute_bank_js(
        f"""
        (() => {{
          const d = {FRAME_DOCUMENT};
          if (!d) return 'NO_FRAME';
          const inputs=[d.querySelector('[id="form1:startDate"]'), d.querySelector('[id="form1:endDate"]')];
          if (!inputs[0] || !inputs[1]) return 'DATE_INPUTS_NOT_FOUND';
          const custom=d.querySelector('[id="form1:rdoCustom"]');
          if (custom) {{
            custom.checked=true;
            custom.dispatchEvent(new Event('change',{{bubbles:true}}));
          }}
          const setValue=(el,value) => {{
            el.focus(); el.removeAttribute('readonly'); el.value=value;
            el.dispatchEvent(new Event('input',{{bubbles:true}}));
            el.dispatchEvent(new Event('change',{{bubbles:true}}));
            el.blur();
          }};
          setValue(inputs[0],{start_text}); setValue(inputs[1],{end_text});
          if (inputs[0].value!=={start_text} || inputs[1].value!=={end_text}) return 'DATE_SET_FAILED';
          const query=[...d.querySelectorAll('*')].find(x => x.children.length===0 && x.textContent.trim()==='開始查詢' && x.getClientRects().length);
          if (!query) return 'QUERY_NOT_FOUND';
          (query.closest('a,button,[onclick]') || query).click();
          return 'CLICKED';
        }})()
        """
    )
    if result != "CLICKED":
        raise RuntimeError(f"無法設定日期或開始查詢：{result}")


def extract_matrices() -> list[list[list[str]]]:
    output = execute_bank_js(
        f"""
        (() => {{
          const root = {FRAME_DOCUMENT};
          if (!root) return '[]';
          const docs=[];
          const walk=d => {{
            docs.push(d);
            [...d.querySelectorAll('iframe,frame')].forEach(f => {{ try {{ if(f.contentDocument) walk(f.contentDocument); }} catch(_) {{}} }});
          }};
          walk(root);
          const tables=[];
          docs.forEach(d => [...d.querySelectorAll('table')].forEach(t => {{
            if (!t.getClientRects().length) return;
            tables.push([...t.rows].map(r => [...r.cells].map(c => (c.innerText || '').trim())));
          }}));
          return JSON.stringify(tables);
        }})()
        """
    )
    return json.loads(output)


def captured_table(matrices: list[list[list[str]]]) -> CapturedTable:
    best: tuple[int, list[list[str]]] | None = None
    for matrix in matrices:
        if len(matrix) < 2:
            continue
        scores = [sum(word in "".join(row).replace(" ", "") for word in HEADER_WORDS) for row in matrix[:6]]
        score = max(scores, default=0)
        if score >= 4 and (best is None or score > best[0]):
            best = score, matrix
    if best is None:
        raise RuntimeError("查詢頁找不到交易明細表")
    matrix = best[1]
    header_index = max(
        range(min(6, len(matrix))),
        key=lambda index: sum(word in "".join(matrix[index]).replace(" ", "") for word in HEADER_WORDS),
    )
    headers = [value.strip() for value in matrix[header_index]]
    rows = []
    for raw in matrix[header_index + 1 :]:
        row = [value.strip() for value in raw]
        if len(row) == len(headers) and "總計" not in "".join(row) and any(row):
            rows.append(row)
    if not rows:
        raise RuntimeError("交易明細表沒有資料")
    return CapturedTable(headers=headers, rows=rows)


def save_csv(table: CapturedTable, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(table.headers)
        writer.writerows(table.rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用正常 Chrome 擷取富邦明細")
    parser.add_argument("--area", required=True, choices=("台北", "台中", "桃園", "新竹", "高雄"))
    parser.add_argument("--start", type=parse_date, default=date.today())
    parser.add_argument("--end", type=parse_date, default=date.today())
    parser.add_argument("--accounts-file", default=str(DEFAULT_ACCOUNTS_FILE))
    parser.add_argument("--output", type=Path, help="明細 CSV 輸出路徑")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account = load_account("fubon", args.area, Path(args.accounts_file).expanduser())
    state = wait_for_manual_login(account)
    if not state.get("quick") and not state.get("query"):
        click_deposit()
        wait_state("quick")
    if not page_state().get("query"):
        click_quick(account.bank_account[-5:])
        wait_state("statement", timeout=10)
        click_statement()
        wait_state("query")
    set_dates_and_query(args.start, args.end)
    wait_state("result", timeout=60)
    table = captured_table(extract_matrices())
    if args.output:
        save_csv(table, args.output.expanduser())
        print(f"RESULT_FILE:{args.output.expanduser().resolve()}")
    new_table = read_and_filter(table, args.area, "fubon")
    if not new_table.rows:
        print(f"共擷取 {len(table.rows)} 筆；Google Sheet 已全部存在，無新增資料。")
        return 0
    copy_to_clipboard(new_table)
    print(
        f"共擷取 {len(table.rows)} 筆；排除 C 欄已存在時間後，"
        f"{len(new_table.rows)} 筆新增明細已複製。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
