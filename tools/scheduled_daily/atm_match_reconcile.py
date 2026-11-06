"""ATM 對帳 ② 配對銀行明細＋③ 更新系統對帳（雲端排程，每日 08:10）。

台北／台中各自：
  ② auto_match_bank_rows：用 M 欄末碼（後 5 碼）或 K 欄姓名比對 F 欄備註＋金額，配對後
     G 欄補公式、I／N／O 補下拉選單，N/O 空白時 2000 → 清潔／新訓費、其他 → 清潔／服務費用。
  ③ process_atm_rows：只處理 T 欄「已配對」（含多訂單加總）且 J 欄有訂單編號、M 欄不是取消訂單的列，
     按已付款＋開立發票＋發確認信。需確認／疑似拆單／非訂單收入一律留給人工。
時段由主控表「排程設定」的 atm_match_reconcile 決定（Cloud Schedule Router 派送）。

tool-system 是 public repo：逐筆訊息（客戶、訂單）不印進 Actions 紀錄，只印各區筆數；
明細寫進私人的執行 Log 試算表。
"""
from __future__ import annotations

import contextlib
import io
import sys

TOOL_NAME = "ATM 配對銀行明細＋更新系統對帳"


def summary_lines(result: dict) -> list[str]:
    lines = []
    for region, item in (result or {}).items():
        item = item or {}
        m = item.get("match") or {}
        r = item.get("reconcile") or {}
        line = (
            f"{region}：② 配對成功 {m.get('success', 0)}、需確認 {m.get('confirm_required', 0)}、"
            f"未配對 {m.get('unmatched', 0)}、多筆候選 {m.get('ambiguous', 0)}；"
            f"③ 更新系統 {r.get('success', 0)}／{len(item.get('reconcile_rows') or [])} 列、"
            f"失敗 {r.get('failed', 0)}、略過 {r.get('skipped', 0)}"
        )
        if item.get("error"):
            line += "（發生錯誤）"
        lines.append(line)
    return lines


def _log(status: str, message: str) -> None:
    try:
        from tools.finance_management.execution_log import log_execution

        log_execution(TOOL_NAME, "台北／台中", status, message)
    except Exception:  # 記錄失敗不影響對帳
        pass


def detail_for_log(detail_text: str, limit: int = 80) -> str:
    keep = ("✅", "⚠️", "❌", "⏭", "=====")
    picked = [line.strip() for line in str(detail_text or "").splitlines() if any(k in line for k in keep)]
    if len(picked) > limit:
        picked = picked[:limit] + [f"…另有 {len(picked) - limit} 行"]
    return "\n".join(picked)


def run() -> int:
    from tools.memo_system import atm, memo

    # 一律正式站（比照 atm_unpaid）：Actions 沒有 Streamlit secrets 的 ENV，不指定會連到測試後台。
    memo.set_env("prod")
    detail = io.StringIO()
    error = None
    result = {}
    try:
        with contextlib.redirect_stdout(detail):
            result = atm.run_scheduled_match_and_reconcile()
    except Exception as exc:
        error = exc
        result = getattr(exc, "results", {}) or {}
    lines = summary_lines(result)
    for line in lines:
        print(f"{'✅' if not error else '•'} {line}")
    extra = detail_for_log(detail.getvalue())
    if error:
        print(f"❌ {TOOL_NAME}失敗：{error}")
        _log("失敗", str(error) + (f"\n{extra}" if extra else ""))
        return 1
    failed = any(((item or {}).get("reconcile") or {}).get("failed") for item in result.values())
    _log("有失敗" if failed else "完成", "；".join(lines) + (f"\n{extra}" if extra else ""))
    return 0


if __name__ == "__main__":
    sys.exit(run())
