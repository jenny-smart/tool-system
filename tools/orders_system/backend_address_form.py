"""Run the backend's own address button; never infer service areas locally."""
import shutil
from urllib.parse import parse_qs, urlparse


def _address_result(response):
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError('後台查詢地區回覆格式異常')
    if payload.get('return_code') != '0000':
        raise RuntimeError('後台查詢地區回覆：' + str(payload.get('description') or payload.get('message') or payload.get('return_code')))
    area = dict(payload.get('area') or {})
    if not area.get('area_id') or not area.get('company_id'):
        raise RuntimeError('後台查詢地區未回傳服務區域，尚未查班表')
    # Coordinates are those sent by the native button, not an independently selected location.
    sent = parse_qs(response.request.post_data or '')
    for field in ('lat', 'lng'):
        if sent.get(field):
            area[field] = sent[field][0]
    return {**payload, 'area': area}


def _native_form_result(page):
    """Read the result exactly as the backend form would use it for later steps."""
    area = page.evaluate(
        """() => {
            const value = selector => (document.querySelector(selector) || {}).value || '';
            return {
                area_id: value('#area_id'),
                company_id: value('#company_id'),
                lat: value('#lat'),
                lng: value('#lng'),
            };
        }"""
    )
    area = dict(area or {})
    if not area.get('area_id') or not area.get('company_id'):
        raise RuntimeError('後台查詢地區未帶入服務區域，尚未查班表')
    return {'return_code': '0000', 'area': area}


def query_native_address(
    session, base_url, member_id, address, clean_type_id, city="", country_id="",
    address_id="", phone="",
):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    except ImportError as exc:
        raise RuntimeError('後台地址查詢所需瀏覽器尚未安裝，請更新部署套件') from exc
    origin = base_url.rstrip('/')
    hostname = urlparse(origin).hostname
    cookies = []
    for cookie in session.cookies:
        domain = cookie.domain.lstrip('.')
        if domain and not (hostname == domain or hostname.endswith('.' + domain)):
            continue
        cookies.append({'name': cookie.name, 'value': cookie.value,
                        'domain': cookie.domain or hostname, 'path': cookie.path or '/',
                        'secure': cookie.secure})
    with sync_playwright() as runtime:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        options = {'headless': True}
        if executable:
            options['executable_path'] = executable
        try:
            browser = runtime.chromium.launch(**options)
        except Exception as exc:
            raise RuntimeError('後台地址查詢瀏覽器無法啟動，請確認部署已安裝 Chromium') from exc
        try:
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()
            # Only the native address lookup may write an HTTP request; never submit an order.
            def restrict(route):
                request = route.request
                target = urlparse(request.url)
                if request.method not in ('GET', 'HEAD', 'OPTIONS') and not (
                    target.hostname == hostname and target.path == '/ajax/check_contain'
                ):
                    route.abort()
                else:
                    route.continue_()
            context.route('**/*', restrict)
            page.goto(origin + '/booking/single', wait_until='domcontentloaded')
            if '/login' in page.url:
                raise RuntimeError('後台地址查詢登入已失效')
            for selector, value in (('#member_id', member_id), ('#clean_type_id', clean_type_id)):
                page.locator(selector).evaluate('(el, value) => { el.value = value; }', str(value))
            try:
                if address_id:
                    if not str(phone or '').strip():
                        raise RuntimeError('既有地址必須先用手機查詢會員，不能改走地址輸入欄位')
                    phone_input = page.locator('input[placeholder="手機"]')
                    phone_input.evaluate(
                        '(el, value) => { el.value = value; el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }',
                        str(phone),
                    )
                    page.locator('input.get_member').click()
                    page.wait_for_function(
                        "addressId => Array.from(document.querySelectorAll('select[name=\"addressId\"] option')).some(o => o.value === String(addressId))",
                        str(address_id),
                    )
                    # 命中舊客下拉地址時只選 addressId；由後台帶入地址、區域與客服備註。
                    page.locator('select[name="addressId"]').evaluate(
                        '(el, value) => { el.value = String(value); el.dispatchEvent(new Event("change", {bubbles:true})); }',
                        str(address_id),
                    )
                else:
                    # 下拉選單沒有該地址，才使用新地址欄位。
                    page.locator('#address').evaluate('(el, value) => { el.value = value; }', str(address))
                    if city:
                        page.locator('#city').evaluate(
                            '(el, value) => { el.value = value; el.dispatchEvent(new Event("change", {bubbles:true})); }',
                            str(city),
                        )
                    if country_id:
                        page.locator('#area').evaluate(
                            '(el, value) => { el.value = value; el.dispatchEvent(new Event("change", {bubbles:true})); }',
                            str(country_id),
                        )
                    page.locator('.check_contain').click()
                # Follow the backend form state instead of requiring a particular network
                # response. Once these fields exist, the UI itself is ready for 計算時數.
                page.wait_for_function(
                    """() => ['#area_id', '#company_id'].every(selector => {
                        const el = document.querySelector(selector);
                        return el && String(el.value || '').trim();
                    })""",
                    timeout=30000,
                )
                result = _native_form_result(page)
            except BrowserTimeout as exc:
                message = page.locator('.sweet-alert.visible h2').all_text_contents()
                raise RuntimeError('後台查詢地區未完成：' + ('；'.join(message) or '服務區域欄位未帶入，尚未查班表')) from exc
            # Preserve the backend session's rotated cookies for subsequent requests.
            for cookie in context.cookies([origin]):
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
            return result
        finally:
            browser.close()
