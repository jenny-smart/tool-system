// ==UserScript==
// @name         Tools App｜富邦銀行明細複製工具
// @namespace    https://github.com/jenny-smart/tool-system
// @version      1.0.0
// @description  複製富邦網銀新增交易，並可獨立調整各筆交易列數。
// @match        https://ebank.taipeifubon.com.tw/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_setClipboard
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const STORE_COPIED = "toolsAppFubonCopiedV1";
  const STORE_LAST = "toolsAppFubonLastCopyV1";
  const PANEL_ID = "tools-app-bank-copy-panel";
  const MAX_STORED_KEYS = 5000;

  const clean = (value) => String(value ?? "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  const money = (value) => clean(value).replace(/,/g, "").replace(/\.00$/, "");
  const cellText = (cell) => clean(cell?.innerText || cell?.textContent || "");

  function hash(text) {
    let value = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      value ^= text.charCodeAt(i);
      value = Math.imul(value, 16777619);
    }
    return (value >>> 0).toString(36);
  }

  function accountScope() {
    const selected = [...document.querySelectorAll("select option:checked")]
      .map((option) => clean(option.textContent))
      .find((text) => /\d{6,}/.test(text));
    return selected || location.pathname;
  }

  function storageKey(record) {
    return `${accountScope()}|${record.date}|${hash(record.raw.join("\u241f"))}`;
  }

  function loadCopied() {
    const stored = GM_getValue(STORE_COPIED, {});
    return stored && typeof stored === "object" ? stored : {};
  }

  function saveCopied(records) {
    const copied = loadCopied();
    const now = Date.now();
    records.forEach((record) => { copied[storageKey(record)] = now; });
    const trimmed = Object.entries(copied)
      .sort((a, b) => b[1] - a[1])
      .slice(0, MAX_STORED_KEYS);
    GM_setValue(STORE_COPIED, Object.fromEntries(trimmed));
  }

  function detectIndexes(headers) {
    const find = (...patterns) => headers.findIndex((header) => patterns.some((pattern) => pattern.test(header)));
    return {
      date: find(/交易日期/, /^日期$/),
      time: find(/交易時間/, /交易日期時間/, /^時間$/),
      type: find(/摘要/, /交易類別/, /交易項目/),
      debit: find(/支出/, /扣款/, /提領/),
      credit: find(/存入/, /收入/, /入帳/),
      balance: find(/餘額/),
      note: find(/備註/, /說明/, /交易明細/),
    };
  }

  function looksLikeStatement(indexes) {
    return indexes.date >= 0 && indexes.type >= 0 && indexes.balance >= 0
      && (indexes.debit >= 0 || indexes.credit >= 0);
  }

  function findStatementTable() {
    const tables = [...document.querySelectorAll("table")];
    for (const table of tables) {
      const rows = [...table.querySelectorAll("tr")];
      for (let rowIndex = 0; rowIndex < Math.min(rows.length, 6); rowIndex += 1) {
        const headers = [...rows[rowIndex].querySelectorAll("th,td")].map(cellText);
        const indexes = detectIndexes(headers);
        if (looksLikeStatement(indexes)) return { table, rows, headerRow: rowIndex, indexes };
      }
    }
    return null;
  }

  function valueAt(cells, index) {
    return index >= 0 && index < cells.length ? cellText(cells[index]) : "";
  }

  function normalizeDate(value) {
    const match = clean(value).match(/(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})/);
    return match ? `${match[1]}/${match[2].padStart(2, "0")}/${match[3].padStart(2, "0")}` : clean(value);
  }

  function extractRecords() {
    const found = findStatementTable();
    if (!found) throw new Error("找不到交易明細表，請先完成查詢並讓明細顯示在頁面上。");

    const records = [];
    found.rows.slice(found.headerRow + 1).forEach((row) => {
      const cells = [...row.querySelectorAll(":scope > th, :scope > td")];
      if (!cells.length) return;
      const raw = cells.map(cellText);
      const date = normalizeDate(valueAt(cells, found.indexes.date));
      const timeValue = valueAt(cells, found.indexes.time);
      if (!/^\d{4}\/\d{2}\/\d{2}$/.test(date)) return;

      records.push({
        date,
        datetime: timeValue.includes(date) ? timeValue : clean(`${date} ${timeValue}`),
        type: valueAt(cells, found.indexes.type),
        debit: money(valueAt(cells, found.indexes.debit)),
        credit: money(valueAt(cells, found.indexes.credit)),
        balance: money(valueAt(cells, found.indexes.balance)),
        note: valueAt(cells, found.indexes.note).replace(/\s+(更多|more)$/i, ""),
        raw,
      });
    });
    return records;
  }

  function newRecords() {
    const copied = loadCopied();
    return extractRecords().filter((record) => !copied[storageKey(record)]);
  }

  function toTsv(records, counts = []) {
    const lines = [];
    records.forEach((record, index) => {
      const count = Math.max(1, Math.min(20, Number(counts[index] || 1)));
      for (let copyIndex = 0; copyIndex < count; copyIndex += 1) {
        lines.push([
          record.date,
          record.datetime,
          record.type,
          copyIndex === 0 ? record.debit : "",
          copyIndex === 0 ? record.credit : "",
          record.balance,
          record.note,
        ].join("\t"));
      }
    });
    return lines.join("\n");
  }

  function copyRecords(records, counts) {
    if (!records.length) {
      alert("目前沒有尚未複製的新交易。");
      return;
    }
    const tsv = toTsv(records, counts);
    GM_setClipboard(tsv, "text");
    GM_setValue(STORE_LAST, tsv);
    saveCopied(records);
    alert(`已複製 ${records.length} 筆交易、共 ${tsv.split("\n").length} 列。請到 Google Sheet 的 B 欄貼上。`);
  }

  function copyNew() {
    try { copyRecords(newRecords()); } catch (error) { alert(error.message); }
  }

  function adjustRows() {
    let records;
    try { records = newRecords(); } catch (error) { alert(error.message); return; }
    if (!records.length) { alert("目前沒有尚未複製的新交易。"); return; }

    const modal = document.createElement("div");
    modal.style.cssText = "position:fixed;inset:0;background:#0008;z-index:2147483647;display:flex;align-items:center;justify-content:center;padding:24px";
    const card = document.createElement("div");
    card.style.cssText = "width:min(900px,96vw);max-height:85vh;overflow:auto;background:white;border-radius:14px;padding:18px;color:#183b4a;font:14px sans-serif";
    card.innerHTML = `<h2 style="margin:0 0 8px">調整交易列數</h2><p>每筆預設 1 列；重複列的收入／支出金額會留白。</p>`;
    const list = document.createElement("div");
    records.forEach((record, index) => {
      const row = document.createElement("div");
      row.style.cssText = "display:grid;grid-template-columns:1fr 80px;gap:12px;align-items:center;border-top:1px solid #ddd;padding:9px 0";
      row.innerHTML = `<div>${record.date}　${record.type}　${record.debit || record.credit || "-"}　${record.note}</div><input data-copy-index="${index}" type="number" min="1" max="20" value="1" style="width:70px;padding:6px">`;
      list.appendChild(row);
    });
    card.appendChild(list);

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:10px;justify-content:flex-end;position:sticky;bottom:0;background:white;padding-top:14px";
    const cancel = document.createElement("button");
    cancel.textContent = "取消";
    const confirm = document.createElement("button");
    confirm.textContent = "依設定複製";
    confirm.style.cssText = "background:#1677a8;color:white;border:0;border-radius:8px;padding:9px 16px";
    cancel.style.cssText = "border:1px solid #aaa;border-radius:8px;padding:9px 16px;background:white";
    cancel.onclick = () => modal.remove();
    confirm.onclick = () => {
      const counts = [...card.querySelectorAll("input[data-copy-index]")].map((input) => input.value);
      modal.remove();
      copyRecords(records, counts);
    };
    actions.append(cancel, confirm);
    card.appendChild(actions);
    modal.appendChild(card);
    document.body.appendChild(modal);
  }

  function repeatLast() {
    const last = GM_getValue(STORE_LAST, "");
    if (!last) { alert("沒有可重新複製的上一批資料。"); return; }
    GM_setClipboard(last, "text");
    alert(`已重新複製上一批，共 ${last.split("\n").length} 列。`);
  }

  function resetToday() {
    if (!confirm("確定要讓今天的交易重新被視為新增資料嗎？")) return;
    const copied = loadCopied();
    let records = [];
    try { records = extractRecords(); } catch (error) { alert(error.message); return; }
    records.forEach((record) => delete copied[storageKey(record)]);
    GM_setValue(STORE_COPIED, copied);
    alert(`已重設目前畫面中的 ${records.length} 筆交易。`);
  }

  function button(label, handler, secondary = false) {
    const element = document.createElement("button");
    element.textContent = label;
    element.onclick = handler;
    element.style.cssText = `border:${secondary ? "1px solid #1777a8" : "0"};border-radius:7px;padding:8px 10px;cursor:pointer;background:${secondary ? "white" : "#1777a8"};color:${secondary ? "#1777a8" : "white"};font-weight:700`;
    return element;
  }

  function mount() {
    if (document.getElementById(PANEL_ID)) return;
    const panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483646;background:#fffffff2;border:1px solid #9fc6d8;border-radius:12px;box-shadow:0 5px 22px #0004;padding:12px;display:flex;gap:7px;flex-wrap:wrap;width:320px;font:13px sans-serif";
    const title = document.createElement("div");
    title.textContent = "🏦 銀行明細工具";
    title.style.cssText = "width:100%;font-weight:800;color:#18495e";
    panel.append(
      title,
      button("複製新增明細", copyNew),
      button("調整交易列數", adjustRows, true),
      button("重複上一批", repeatLast, true),
      button("重設今日紀錄", resetToday, true),
    );
    document.body.appendChild(panel);
  }

  mount();
  new MutationObserver(mount).observe(document.documentElement, { childList: true, subtree: true });
})();
