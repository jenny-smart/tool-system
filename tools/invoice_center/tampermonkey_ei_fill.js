// ==UserScript==
// @name         Lemon EI 發票填入小工具
// @match        https://www.ei.com.tw/InvoiceRent/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(() => {
  const TOOL_ID = "lemon-ei-fill-btn";
  const TOOL_VERSION = "2026-07-15.2";
  if (window.__lemonEiToolVersion === TOOL_VERSION) return;
  window.__lemonEiToolVersion = TOOL_VERSION;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const valueText = (value) => String(value ?? "").trim();

  const fire = (el) => {
    if (!el) return false;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
    return true;
  };

  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (!el) return false;
    el.value = valueText(value);
    fire(el);
    return true;
  };

  const clickId = (id) => {
    const el = document.getElementById(id);
    if (!el) return false;
    el.click();
    fire(el);
    return true;
  };

  const setPay = (payway) => {
    const pay = document.getElementById("pay");
    if (!pay) return false;
    const text = valueText(payway);
    let value = "1";
    if (text.includes("ATM") || text === "2") value = "2";
    else if (text.includes("信用卡") || text === "3") value = "3";
    else if (text.includes("現金") || text === "1") value = "1";
    else if (text.includes("儲值金")) value = "5";
    pay.value = value;
    fire(pay);
    return true;
  };

  const setTax = (d) => {
    clickId("invoicetype07");
    clickId(valueText(d.hastax) === "1" ? "hastax1" : "hastax2");
    const taxMap = { 1: "businesstax1", 2: "businesstax2", 3: "businesstax3", 4: "businesstax4" };
    clickId(taxMap[valueText(d.taxtype)] || "businesstax1");
    clickId(`roundnum${valueText(d.roundnum) || "4"}`);
    setValue("rate", d.rate || "0.05");
  };

  const clearCarrier = () => {
    setValue("carriertype", "");
    setValue("carrierid1", "");
    setValue("carrierid2", "");
    setValue("donatevat", "");
  };

  const setCarrier = async (d) => {
    const buyerId = valueText(d.buyer_identifier);
    const donate = valueText(d.donate);
    const donatevat = valueText(d.donatevat);
    const carrierType = valueText(d.carriertype);
    const carrier1 = valueText(d.carrierid1);
    const carrier2 = valueText(d.carrierid2 || d.carrierid1);

    if (buyerId) {
      clickId("donate2");
      await sleep(80);
      clearCarrier();
      return;
    }

    if (donate === "1" || donatevat) {
      clickId("donate1");
      await sleep(80);
      clearCarrier();
      setValue("donatevat", donatevat);
      return;
    }

    if (!carrierType && !carrier1 && !carrier2) {
      clickId("donate2");
      await sleep(80);
      clearCarrier();
      return;
    }

    clickId("donate0");
    await sleep(100);
    if (carrierType === "3J0002") clickId("barcode3J0002");
    else if (carrierType === "CQ0001") clickId("barcodeCQ0001");
    else clickId("barcodeEJ0011");

    setValue("carriertype", carrierType || "EJ0011");
    setValue("carrierid1", carrier1);
    setValue("carrierid2", carrier2);
  };

  const fillDetailHidden = (d) => {
    setValue("detaildata", d.detaildata || "");
    setValue("saleamount", d.saleamount || "");
    setValue("taxamount", d.taxamount || "");
    setValue("totalamount", d.totalamount || "");
  };

  const fill = async () => {
    const raw = prompt("貼上發票中心 Payload JSON");
    if (!raw) return;

    let d;
    try {
      d = JSON.parse(raw);
    } catch {
      alert("Payload JSON 格式錯誤");
      return;
    }

    setValue("orderid", d.orderid);
    setValue("orderdate", d.orderdate);
    setValue("buyer_name", d.buyer_name);
    setValue("buyer_identifier", d.buyer_identifier);
    setValue("buyer_phone", d.buyer_phone);
    setValue("buyer_address", d.buyer_address);
    setValue("buyer_emailaddress", d.buyer_emailaddress);
    setPay(d.payway);
    setValue("buyer_emailaddress", d.buyer_emailaddress);
    setValue("mainremark", d.mainremark);
    setTax(d);
    await setCarrier(d);
    fillDetailHidden(d);

    alert("已填入。請檢查買受人/統編、Email、付款方式、載具後再按下一步。");
  };

  const addButton = () => {
    document.getElementById(TOOL_ID)?.remove();
    const btn = document.createElement("button");
    btn.id = TOOL_ID;
    btn.type = "button";
    btn.textContent = "貼上發票資料";
    btn.onclick = fill;
    btn.style.cssText = `
      position: fixed;
      right: 18px;
      top: 82px;
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
  setInterval(() => {
    if (!document.getElementById(TOOL_ID)) addButton();
  }, 1000);
})();
