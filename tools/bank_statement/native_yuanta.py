from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import date
from typing import Any

from tools.bank_statement.capture import CapturedTable


YUANTA_HOST = "b2bank.yuantabank.com.tw"
TAB_MARKER = f"yuanta-login-{os.getpid()}"
YUANTA_URL = (
    "https://b2bank.yuantabank.com.tw/B2C/login/LOGIN_Home.faces"
    f"#{TAB_MARKER}"
)
_YUANTA_PAGE: Any | None = None
_YUANTA_CONTEXT: Any | None = None


def bind_yuanta_page(page: Any) -> None:
    """將元大控制綁定到 Agent Chrome 的 Playwright 分頁。"""
    global _YUANTA_PAGE, _YUANTA_CONTEXT
    _YUANTA_PAGE = page
    _YUANTA_CONTEXT = page.context


def _compact(source: str) -> str:
    return " ".join(line.strip() for line in source.splitlines() if line.strip())


def execute_yuanta_js(source: str) -> str:
    global _YUANTA_PAGE
    javascript = _compact(source)
    if _YUANTA_CONTEXT is not None:
        pages = [
            page for page in _YUANTA_CONTEXT.pages
            if not page.is_closed() and YUANTA_HOST in page.url
        ]
        if pages:
            _YUANTA_PAGE = pages[-1]
        if _YUANTA_PAGE is None or _YUANTA_PAGE.is_closed():
            raise RuntimeError("Agent Chrome 找不到元大分頁")
        result = _YUANTA_PAGE.evaluate(javascript)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
    quoted = json.dumps(javascript, ensure_ascii=False)
    script = f'''
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
    if (URL of active tab of front window contains "{YUANTA_HOST}") then
      activate
      return execute active tab of front window javascript {quoted}
    end if
  end if
  repeat with wi from 1 to count of windows
    repeat with ti from 1 to count of tabs of window wi
      if (URL of tab ti of window wi contains "{YUANTA_HOST}") then
        set active tab index of window wi to ti
        set index of window wi to 1
        activate
        return execute active tab of front window javascript {quoted}
      end if
    end repeat
  end repeat
  return "YUANTA_TAB_NOT_FOUND"
end tell
'''
    result = subprocess.run(
        ["osascript", "-"], input=script, text=True, capture_output=True, check=False
    )
    if result.returncode:
        message = (result.stderr or result.stdout).strip()
        if "AppleScript" in message and "JavaScript" in message:
            raise RuntimeError(
                "請在 Chrome 開啟：顯示方式 → 開發人員 → "
                "允許來自 Apple 事件的 JavaScript"
            )
        raise RuntimeError(message or "無法控制 Chrome")
    output = result.stdout.strip()
    if output == "YUANTA_TAB_NOT_FOUND":
        raise RuntimeError("找不到已開啟的元大 Chrome 分頁")
    return output


def page_state() -> dict[str, object]:
    output = execute_yuanta_js(
        r'''
        (() => {
          const docs=[];
          const walk=d => {
            docs.push(d);
            [...d.querySelectorAll('iframe,frame')].forEach(f => {
              try { if (f.contentDocument) walk(f.contentDocument); } catch(_) {}
            });
          };
          walk(document);
          const loginDoc=docs.find(d => {
            const company=d.querySelector('[id="login:viewCompanyUid"]');
            return company && company.getClientRects().length;
          });
          const text=docs.map(d => d.body?.innerText || '').join('\n');
          return JSON.stringify({
            ready:true,
            prelogin:Boolean(loginDoc),
            logged_in:text.includes('登出') ||
              (text.includes('儀表板') && text.includes('帳戶查詢')),
            url:location.href
          });
        })()
        '''
    )
    return json.loads(output)


def fill_yuanta_login(account: object, timeout: float = 30.0) -> None:
    values = json.dumps(
        [account.login_id, account.user_code, account.password], ensure_ascii=False
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = execute_yuanta_js(
            f'''
            (() => {{
              const docs=[];
              const walk=d => {{
                docs.push(d);
                [...d.querySelectorAll('iframe,frame')].forEach(f => {{
                  try {{ if (f.contentDocument) walk(f.contentDocument); }} catch(_) {{}}
                }});
              }};
              walk(document);
              const d=docs.find(item =>
                item.querySelector('[id="login:viewCompanyUid"]') &&
                item.querySelector('[id="login:userUuid"]') &&
                item.querySelector('[id="login:password"]') &&
                item.querySelector('[id="login:pictCode"]')
              );
              if (!d) return 'WAIT';
              const ids=['login:viewCompanyUid','login:userUuid','login:password'];
              const vals={values};
              for (let i=0;i<ids.length;i++) {{
                const input=d.getElementById(ids[i]);
                input.focus();
                const setter=Object.getOwnPropertyDescriptor(
                  input.ownerDocument.defaultView.HTMLInputElement.prototype,'value'
                )?.set;
                if (setter) setter.call(input, vals[i]); else input.value=vals[i];
                input.dispatchEvent(new Event('input',{{bubbles:true}}));
                input.dispatchEvent(new Event('change',{{bubbles:true}}));
                input.blur();
              }}
              d.getElementById('login:pictCode').focus();
              return 'FILLED';
            }})()
            '''
        )
        if result == "FILLED":
            print("元大帳密已由本機 JSON 預填；請輸入圖形驗證碼並按『登入』。")
            return
        time.sleep(0.5)
    raise RuntimeError("找不到元大登入欄位，無法預填帳密")


def wait_for_yuanta_login(account: object, timeout: float = 300.0) -> dict[str, object]:
    try:
        state = page_state()
    except RuntimeError as exc:
        if "找不到已開啟" not in str(exc):
            raise
        subprocess.run(["open", "-a", "Google Chrome", YUANTA_URL], check=False)
        state = {}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                state = page_state()
                break
            except RuntimeError as tab_exc:
                if "找不到已開啟" not in str(tab_exc):
                    raise
                time.sleep(0.5)
        if not state:
            raise RuntimeError("Chrome 已開啟，但元大登入頁尚未載入，請再執行一次")

    if state.get("logged_in"):
        print("元大目前已登入，沿用現有工作階段。")
        return state
    fill_yuanta_login(account)
    print("等待你在 Chrome 輸入驗證碼並登入元大（最長 5 分鐘）……")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = page_state()
        except RuntimeError:
            time.sleep(1)
            continue
        if state.get("logged_in") or not state.get("prelogin"):
            # 登入後頁面可能先轉址，再載入導覽列；稍候確認不會跳回登入頁。
            time.sleep(2)
            stable = page_state()
            if stable.get("logged_in") or not stable.get("prelogin"):
                return stable
        time.sleep(1)
    raise RuntimeError("等待元大登入逾時，請重新執行指令")


def _click_text(text: str, timeout: float = 20.0) -> None:
    target = json.dumps(text, ensure_ascii=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = execute_yuanta_js(
            f'''
            (() => {{
              const docs=[];
              const walk=d=>{{docs.push(d);[...d.querySelectorAll('iframe,frame')].forEach(f=>{{try{{if(f.contentDocument)walk(f.contentDocument)}}catch(_){{}}}})}};
              walk(document);
              const wanted={target};
              const matches=[];
              for(const d of docs) for(const el of d.querySelectorAll('a,button,input,span,div,li')){{
                const label=((el.value||el.innerText||'')+'').replace(/\\s+/g,' ').trim();
                if(el.getClientRects().length && (label===wanted || label.includes(wanted))) matches.push([label===wanted?0:1,label.length,el]);
              }}
              if(!matches.length)return 'WAIT';
              matches.sort((a,b)=>a[0]-b[0]||a[1]-b[1]);
              const el=matches[0][2];
              const control=el.closest('a,button,[onclick],[role="button"],li')||el;
              control.dispatchEvent(new MouseEvent('mouseover',{{bubbles:true}}));
              control.click();
              return 'CLICKED';
            }})()
            '''
        )
        if result == "CLICKED":
            return
        time.sleep(0.5)
    raise RuntimeError(f"元大找不到可點選項目：{text}")


def open_yuanta_statement_menu() -> None:
    def query_form_ready() -> bool:
        if _YUANTA_PAGE is None or _YUANTA_PAGE.is_closed():
            return False
        for frame in _YUANTA_PAGE.frames:
            account = frame.locator('[id="cacdp003:accountCombo"]')
            if account.count() and account.first.is_visible():
                return True
        return False

    if query_form_ready():
        print("步驟 1～3/4：已在『帳戶查詢 → 存款查詢 → 交易明細查詢』。")
        return

    if _YUANTA_PAGE is not None and not _YUANTA_PAGE.is_closed():
        target = None
        for frame in _YUANTA_PAGE.frames:
            text = frame.get_by_text("交易明細查詢", exact=True)
            for index in range(text.count()):
                item = text.nth(index)
                if not item.is_visible():
                    continue
                link = item.locator("xpath=ancestor::a[1]")
                if link.count() and link.first.is_visible():
                    target = link.first
                    break
            if target is not None:
                break
        if target is None:
            # 已在結果頁時，先回到相同功能的查詢表單。
            for frame in _YUANTA_PAGE.frames:
                again = frame.get_by_text("重新查詢", exact=True)
                if again.count() and again.first.is_visible():
                    target = again.first
                    break
        if target is None:
            raise RuntimeError("元大找不到『交易明細查詢／重新查詢』連結")
        # 使用 Playwright 真實滑鼠事件；元大不接受部分 JavaScript 合成點擊。
        target.click(timeout=5_000)
    else:
        _click_text("交易明細查詢")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if query_form_ready():
            print("步驟 1～3/4：已進入『帳戶查詢 → 存款查詢 → 交易明細查詢』。")
            return
        time.sleep(0.5)
    raise RuntimeError("元大點擊交易明細查詢後，表單未載入")


def configure_yuanta_query(account_number: str, start: date, end: date) -> None:
    if _YUANTA_PAGE is not None and not _YUANTA_PAGE.is_closed():
        form = None
        for frame in _YUANTA_PAGE.frames:
            account_select = frame.locator('[id="cacdp003:accountCombo"]')
            if account_select.count() and account_select.first.is_visible():
                form = frame
                break
        if form is None:
            raise RuntimeError("元大交易明細查詢表單尚未載入")
        account_select = form.locator('[id="cacdp003:accountCombo"]').first
        wanted = re.sub(r"\D", "", account_number)
        option_value = None
        options = account_select.locator("option")
        for index in range(options.count()):
            option = options.nth(index)
            digits = re.sub(r"\D", "", option.inner_text())
            if wanted in digits or digits.endswith(wanted[-8:]):
                option_value = option.get_attribute("value")
                break
        if option_value is None:
            raise RuntimeError(f"元大找不到帳號 {account_number}")
        account_select.select_option(option_value)
        form.locator('[id="cacdp003:rdoRange5"]').first.evaluate("el => el.click()")
        for selector, value in (
            ('[id="cacdp003:startDate"]', start.strftime("%Y/%m/%d")),
            ('[id="cacdp003:endDate"]', end.strftime("%Y/%m/%d")),
        ):
            form.locator(selector).first.evaluate(
                "(el, value) => { el.removeAttribute('readonly'); el.value=value; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); }",
                value,
            )
        form.locator('[id="cacdp003:startHourCombo"]').first.select_option("00")
        form.locator('[id="cacdp003:startMinCombo"]').first.select_option("00")
        form.locator('[id="cacdp003:endHourCombo"]').first.select_option("23")
        form.locator('[id="cacdp003:endMinCombo"]').first.select_option("59")
        form.locator('[id="cacdp003:linkCommand"]').first.click(timeout=5_000)
        print(f"步驟 4/4：帳號末碼 {account_number[-5:]}，查詢 {start:%Y/%m/%d}～{end:%Y/%m/%d}。")
        return

    values = json.dumps(
        {
            "account": re.sub(r"\D", "", account_number),
            "start": start.strftime("%Y/%m/%d"),
            "end": end.strftime("%Y/%m/%d"),
        },
        ensure_ascii=False,
    )
    result = execute_yuanta_js(
        f'''
        (() => {{
          const values={values};
          const docs=[];
          const walk=d=>{{docs.push(d);[...d.querySelectorAll('iframe,frame')].forEach(f=>{{try{{if(f.contentDocument)walk(f.contentDocument)}}catch(_){{}}}})}};
          walk(document);
          let accountFound=false;
          for(const d of docs){{
            for(const select of d.querySelectorAll('select')){{
              if(!select.getClientRects().length)continue;
              const option=[...select.options].find(o=>{{
                const digits=(o.textContent||'').replace(/\\D/g,'');
                return digits.includes(values.account)||digits.endsWith(values.account.slice(-8));
              }});
              if(option){{select.value=option.value;select.dispatchEvent(new Event('change',{{bubbles:true}}));accountFound=true;}}
            }}
          }}
          if(!accountFound)return 'ACCOUNT_NOT_FOUND';
          const setValue=(input,value)=>{{
            input.removeAttribute('readonly');input.value=value;
            input.dispatchEvent(new Event('input',{{bubbles:true}}));
            input.dispatchEvent(new Event('change',{{bubbles:true}}));
          }};
          let startInput=null,endInput=null;
          for(const d of docs){{
            const inputs=[...d.querySelectorAll('input:not([type="hidden"]):not([type="button"]):not([type="submit"])')].filter(x=>x.getClientRects().length);
            startInput ||= inputs.find(x=>/start|begin|from|sdate|date_s/i.test((x.id||'')+' '+(x.name||'')));
            endInput ||= inputs.find(x=>/end|to|edate|date_e/i.test((x.id||'')+' '+(x.name||'')));
            if(!startInput||!endInput){{
              const dateInputs=inputs.filter(x=>/date|日期/i.test((x.id||'')+' '+(x.name||'')+' '+(x.placeholder||'')));
              startInput ||= dateInputs[0];endInput ||= dateInputs[1];
            }}
          }}
          if(!startInput||!endInput)return 'DATE_NOT_FOUND';
          setValue(startInput,values.start);setValue(endInput,values.end);
          for(const d of docs){{
            const controls=[...d.querySelectorAll('button,input[type="button"],input[type="submit"],a')].filter(x=>x.getClientRects().length);
            const query=controls.find(x=>/^(開始)?查詢$/.test(((x.value||x.innerText||'')+'').replace(/\\s+/g,'')));
            if(query){{query.click();return 'SUBMITTED';}}
          }}
          return 'QUERY_NOT_FOUND';
        }})()
        '''
    )
    messages = {
        "ACCOUNT_NOT_FOUND": f"元大找不到帳號 {account_number}",
        "DATE_NOT_FOUND": "元大找不到開始／結束日期欄位",
        "QUERY_NOT_FOUND": "元大找不到查詢按鈕",
    }
    if result != "SUBMITTED":
        raise RuntimeError(messages.get(result, f"元大查詢設定失敗：{result}"))
    print(f"步驟 4/4：帳號末碼 {account_number[-5:]}，查詢 {start:%Y/%m/%d}～{end:%Y/%m/%d}。")


def _statement_matrix() -> list[list[str]] | None:
    output = execute_yuanta_js(
        r'''
        (() => {
          const docs=[];
          const walk=d=>{docs.push(d);[...d.querySelectorAll('iframe,frame')].forEach(f=>{try{if(f.contentDocument)walk(f.contentDocument)}catch(_){}})};
          walk(document);
          const words=['交易日','帳務日','交易時間','摘要','支出','存入','餘額','備註'];
          let best=null;
          for(const d of docs) for(const table of d.querySelectorAll('table')){
            if(!table.getClientRects().length)continue;
            const matrix=[...table.rows].map(r=>[...r.cells].map(c=>(c.innerText||'').trim()));
            if(matrix.length<2)continue;
            const score=Math.max(...matrix.slice(0,6).map(r=>words.filter(w=>r.join('').replace(/\s/g,'').includes(w)).length));
            if(score>=4 && (!best || score>best.score || (score===best.score && matrix.length>best.matrix.length))) best={score,matrix};
          }
          return JSON.stringify(best?best.matrix:null);
        })()
        '''
    )
    return json.loads(output)


def _find_header(headers: list[str], words: tuple[str, ...], *, exclude: tuple[str, ...] = ()) -> int | None:
    for index, header in enumerate(headers):
        compact=re.sub(r"\s+", "", header)
        if any(word in compact for word in words) and not any(word in compact for word in exclude):
            return index
    return None


def normalize_yuanta_matrix(matrix: list[list[str]]) -> CapturedTable:
    header_index = max(
        range(min(6, len(matrix))),
        key=lambda i: sum(word in "".join(matrix[i]).replace(" ", "") for word in ("交易日", "帳務日", "交易時間", "摘要", "支出", "存入", "餘額", "備註")),
    )
    source_headers = matrix[header_index]
    date_index = _find_header(source_headers, ("帳務日期", "帳務日", "交易日期", "日期"), exclude=("時間",))
    time_index = _find_header(source_headers, ("交易時間", "日期時間", "時間"))
    if time_index is None:
        time_index = _find_header(source_headers, ("交易日",))
    summary_index = _find_header(source_headers, ("摘要", "交易說明", "說明"))
    debit_index = _find_header(source_headers, ("支出", "提款", "借方"))
    credit_index = _find_header(source_headers, ("存入", "存款", "貸方"))
    balance_index = _find_header(source_headers, ("餘額",))
    note_index = _find_header(source_headers, ("備註", "註記", "附言"))
    required = (date_index, summary_index, debit_index, credit_index, balance_index)
    if any(index is None for index in required):
        raise RuntimeError(f"元大交易表欄位無法辨識：{source_headers}")

    def cell(row: list[str], index: int | None) -> str:
        return row[index].strip() if index is not None and index < len(row) else ""

    rows: list[list[str]] = []
    for source in matrix[header_index + 1 :]:
        date_text = cell(source, date_index)
        time_text = cell(source, time_index)
        if not date_text or "總計" in "".join(source):
            continue
        if time_text and not re.search(r"\d{4}[/.-]\d{1,2}[/.-]\d{1,2}", time_text):
            transaction_time = f"{date_text.split()[0]} {time_text}"
        else:
            transaction_time = time_text or date_text
        accounting_date = date_text.split()[0]
        rows.append([
            accounting_date,
            transaction_time,
            cell(source, summary_index),
            cell(source, debit_index),
            cell(source, credit_index),
            cell(source, balance_index),
            cell(source, note_index),
        ])
    if not rows:
        raise RuntimeError("元大查詢結果沒有可擷取的交易資料")
    return CapturedTable(
        headers=["帳務日期", "交易時間", "摘要", "支出金額", "存入金額", "即時餘額", "附註"],
        rows=rows,
    )


def wait_for_yuanta_statement(timeout: float = 60.0) -> CapturedTable:
    deadline = time.monotonic() + timeout
    candidate: CapturedTable | None = None
    stable_since = 0.0
    time.sleep(2)
    while time.monotonic() < deadline:
        matrix = _statement_matrix()
        if matrix:
            table = normalize_yuanta_matrix(matrix)
            now = time.monotonic()
            if candidate is None or candidate.fingerprint != table.fingerprint:
                candidate, stable_since = table, now
            elif now - stable_since >= 1:
                return table
        time.sleep(0.5)
    raise RuntimeError("等待元大交易明細表逾時")
