// ==UserScript==
// @name         Lemon EI 發票填入小工具
// @match        https://www.ei.com.tw/InvoiceRent/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(() => {
  if (window.__lemonEiTool) return;
  window.__lemonEiTool = true;

  const clean = (value) => String(value || "").replace(/\s+/g, "");
  const fire = (el) => {
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };
  const rows = () => [...document.querySelectorAll("tr")];

  const setByName = (names, value) => {
    for (const name of names) {
      const el = document.querySelector(`[name="${name}"], #${name}`);
      if (!el) continue;
      el.value = value || "";
      fire(el);
      return true;
    }
    return false;
  };

  const setInputByRow = (label, value) => {
    for (const row of rows()) {
      if (!clean(row.textContent).includes(clean(label))) continue;
      const input = row.querySelector("input[type='text'], input:not([type]), textarea");
      if (!input) continue;
      input.value = value || "";
      fire(input);
      return true;
    }
    return false;
  };

  const setSelectByRow = (label, value) => {
    const target = clean(value);
    for (const row of rows()) {
      if (!clean(row.textContent).includes(clean(label))) continue;
      const select = row.querySelector("select");
      if (!select) continue;
      const option = [...select.options].find((item) => {
        return clean(item.text).includes(target) || clean(item.value).includes(target);
      });
      if (option) select.value = option.value;
      fire(select);
      return true;
    }
    return false;
  };

  const textAfter = (input) => {
    let text = "";
    let node = input.nextSibling;
    while (node) {
      if (node.nodeType === 1 && node.matches?.("input[type='radio']")) break;
      text += node.textContent || "";
      node = node.nextSibling;
    }
    return clean(text || input.closest("label")?.textContent || input.parentElement?.textContent);
  };

  const clickRadio = (rowLabel, optionLabel) => {
    const target = clean(optionLabel);
    const scopeRows = rowLabel ? rows().filter((row) => clean(row.textContent).includes(clean(rowLabel))) : rows();
    for (const row of scopeRows) {
      for (const radio of row.querySelectorAll("input[type='radio']")) {
        if (textAfter(radio).includes(target)) {
          radio.click();
          fire(radio);
          return true;
        }
      }
    }
    return false;
  };

  const setPayment = (payway) => {
    const value = String(payway || "");
    if (value.includes("ATM") || value === "2") return setSelectByRow("付款方式", "ATM");
    if (value.includes("信用卡") || value === "3") return setSelectByRow("付款方式", "信用卡");
    if (value.includes("現金")) return setSelectByRow("付款方式", "現金");
    return false;
  };

  const fill = () => {
    const raw = prompt("貼上發票中心 Payload JSON");
    if (!raw) return;

    let d;
    try {
      d = JSON.parse(raw);
    } catch {
      alert("Payload JSON 格式錯誤");
      return;
    }

    setByName(["orderid", "orderId"], d.orderid) || setInputByRow("訂單編號", d.orderid);
    setByName(["orderdate", "orderDate"], d.orderdate) || setInputByRow("訂單日期", d.orderdate);
    setByName(["buyer_name", "buyerName"], d.buyer_name) || setInputByRow("買方名稱", d.buyer_name);
    setByName(["buyer_identifier", "buyerIdentifier"], d.buyer_identifier) || setInputByRow("買方統編", d.buyer_identifier);
    setByName(["buyer_address", "buyerAddress"], d.buyer_address) || setInputByRow("買方地址", d.buyer_address);
    setByName(["buyer_emailaddress", "buyerEmailAddress"], d.buyer_emailaddress) || setInputByRow("買方Email", d.buyer_emailaddress);
    setByName(["buyer_phone", "buyerPhone"], d.buyer_phone) || setInputByRow("買方電話", d.buyer_phone);
    setByName(["mainremark", "remark"], d.mainremark) || setInputByRow("備註", d.mainremark);
    setPayment(d.payway);

    clickRadio("單價是否含稅", "含稅");
    clickRadio("營業稅", "應稅");
    clickRadio("發票計算位數", "4");
    setByName(["rate"], d.rate || "0.05") || setInputByRow("稅率", d.rate || "0.05");

    if (d.buyer_identifier) {
      clickRadio("發票方式", "紙本");
    } else if (d.donate === "1" || d.donatevat) {
      clickRadio("發票方式", "捐贈");
      setByName(["donatevat"], d.donatevat);
    } else {
      clickRadio("發票方式", "載具");
      if (d.carriertype === "3J0002") clickRadio("", "手機條碼");
      else if (d.carriertype === "CQ0001") clickRadio("", "自然人憑證");
      else clickRadio("", "會員");
      setByName(["carriertype", "carrierType"], d.carriertype) || setInputByRow("載具類別編號", d.carriertype);
      setByName(["carrierid1", "carrierId1"], d.carrierid1) || setInputByRow("顯碼", d.carrierid1);
      setByName(["carrierid2", "carrierId2"], d.carrierid2) || setInputByRow("隱碼", d.carrierid2);
    }

    setByName(["detaildata"], d.detaildata);
    alert("已填入。請檢查買方名稱/統編、Email、付款方式、載具與商品明細後再按下一步。");
  };

  const addButton = () => {
    if (document.getElementById("lemon-ei-fill-btn")) return;
    const btn = document.createElement("button");
    btn.id = "lemon-ei-fill-btn";
    btn.textContent = "貼上發票資料";
    btn.onclick = fill;
    btn.style.cssText = `
      position: fixed;
      right: 18px;
      top: 80px;
      z-index: 999999;
      padding: 12px 18px;
      background: #e53935;
      color: #fff;
      border: 0;
      border-radius: 8px;
      font-size: 16px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(0,0,0,.22);
    `;
    document.body.appendChild(btn);
  };

  addButton();
  setInterval(addButton, 1000);
})();
