from __future__ import annotations

import json
import os
import subprocess
import time


YUANTA_HOST = "b2bank.yuantabank.com.tw"
TAB_MARKER = f"yuanta-login-{os.getpid()}"
YUANTA_URL = (
    "https://b2bank.yuantabank.com.tw/B2C/login/LOGIN_Home.faces"
    f"#{TAB_MARKER}"
)


def _compact(source: str) -> str:
    return " ".join(line.strip() for line in source.splitlines() if line.strip())


def execute_yuanta_js(source: str) -> str:
    javascript = _compact(source)
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
