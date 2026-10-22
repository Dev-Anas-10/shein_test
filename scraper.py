
import html
import json
import re
import sys
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)

API_MARKER = "/bff-api/order/cart/share/landing"
HEADERS = {"User-Agent": UA}


def is_shein(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in ("http", "https")
        and (host == "shein.com" or host.endswith(".shein.com"))
    )


URL_RE = re.compile(r"https?://[^\s<>\"']+")


def extract_url(text):
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?)")
        if is_shein(url):
            return url
    raise ValueError("ما لقيتش رابط SHEIN في النص")


def landing_url(group_id, shc, market="EG"):
    """
    بناء رابط المشاركة حسب السوق المطلوب.
    EG: مسار مصر من غير /ar/ مع local_country=OTHER.
    SA: المسار العربي مع local_country=SA.
    """
    if market == "EG":
        path = "/cart/share/landing"
        country = "OTHER"
    elif market == "SA":
        path = "/ar/cart/share/landing"
        country = "SA"
    else:
        raise ValueError("السوق المدعوم هنا EG أو SA فقط")

    query = urlencode({
        "shc": shc,
        "group_id": group_id,
        "local_country": country,
        "url_from": "",
        "cart_share": "1",
    })

    return f"https://m.shein.com{path}?{query}"


def share_data_from_url(url):
    """
    استخراج بيانات المشاركة من رابط يحتوي بالفعل على group_id وshc.
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    group_id = query.get("group_id", [""])[0]
    shc = query.get("shc", [""])[0]

    if group_id and shc:
        return group_id, shc

    return None, shc


def resolve_short(short_url, market="EG"):
    """
    يتبع رابط onelink ويحاول استخراج رابط مشاركة كامل.
    بعد استخراج group_id وshc يبني رابط السوق المطلوب.
    """
    if not is_shein(short_url):
        raise ValueError("لازم رابط SHEIN رسمي")

    session = requests.Session()
    response = session.get(
        short_url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()

    final_url = response.url
    group_id, shc = share_data_from_url(final_url)

    if not shc:
        shc = parse_qs(urlparse(short_url).query).get("shc", [""])[0]

    # محاولة استخراج appjump وshareInfo من صفحة الرابط القصير.
    appjump = None
    source = html.unescape(response.text)

    for tag in re.findall(r"<input\b[^>]*>", source, re.I):
        if re.search(r'\bid=["\']url["\']', tag, re.I):
            match = re.search(r'\bvalue=["\']([^"\']+)["\']', tag, re.I)
            if match:
                appjump = html.unescape(match.group(1))
                break

    if appjump and is_shein(appjump):
        jump_response = session.get(
            appjump,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True,
        )
        jump_response.raise_for_status()

        jump_url = jump_response.url
        jump_group, jump_shc = share_data_from_url(jump_url)

        if jump_group:
            group_id = jump_group
        if jump_shc:
            shc = jump_shc

        jump_source = html.unescape(jump_response.text)
        match = re.search(
            r"\bvar\s+shareInfo\s*=\s*(\{.*?\})\s*;",
            jump_source,
            re.S,
        )

        if match:
            try:
                info = json.loads(match.group(1))
                extracted_group = str(
                    info.get("id") or info.get("shareId") or ""
                )
                if extracted_group.isdigit():
                    group_id = extracted_group

                if not shc:
                    shc = (
                        parse_qs(urlparse(short_url).query).get("shc", [""])[0]
                        or parse_qs(urlparse(appjump).query).get("shc", [""])[0]
                    )
            except json.JSONDecodeError:
                pass

    if not group_id or not str(group_id).isdigit():
        raise ValueError(
            "لم أستطع استخراج group_id من onelink. "
            "لا يمكن تخمين معرّف المجموعة المصرية."
        )

    if not shc:
        raise ValueError("لم أستطع استخراج shc من الرابط")

    print("Resolved group_id:", group_id, file=sys.stderr)
    print("Resolved shc:", shc, file=sys.stderr)
    print("Requested market:", market, file=sys.stderr)

    return landing_url(str(group_id), shc, market=market)


def normalize_input(url, market="EG"):
    if not is_shein(url):
        raise ValueError("لازم رابط SHEIN رسمي")

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    group_id = query.get("group_id", [""])[0]
    shc = query.get("shc", [""])[0]

    # حتى لو الرابط مباشر، نعيد بناء مسار السوق المطلوب
    # مع الحفاظ على group_id وshc الموجودين في الرابط.
    if (
        group_id
        and re.fullmatch(r"[A-Za-z0-9_-]+", group_id)
        and shc
        and "/cart/share/landing" in parsed.path
    ):
        return landing_url(group_id, shc, market=market)

    # رابط onelink أو رابط قصير.
    return resolve_short(url, market=market)


def normalize_product(product):
    image = product.get("goods_img") or ""

    if image.startswith("//"):
        image = "https:" + image
    elif image.startswith("/"):
        image = "https://m.shein.com" + image

    retail = product.get("retailPrice") or {}
    sale = product.get("salePrice") or {}

    display_price = sale.get("amountWithSymbol") or ""
    currency = (
        "EGP" if "EGP" in display_price
        else "SAR" if "SAR" in display_price
        else "USD" if "USD" in display_price or "$" in display_price
        else ""
    )

    return {
        "goods_id": str(product.get("goods_id") or ""),
        "goods_sn": str(product.get("goods_sn") or ""),
        "name": product.get("goods_name") or "",
        "image": image,
        "original_price": str(retail.get("amount") or ""),
        "original_price_display": retail.get("amountWithSymbol") or "",
        "sale_price": str(sale.get("amount") or ""),
        "sale_price_display": display_price,
        "currency": currency,
        "selected_variant": product.get("goodsAttr") or "",
        "sku_code": product.get("sku_code") or "",
        "sold_out": bool(product.get("soldOutStatus", False)),
    }


def capture(url):
    responses = []
    final_url = url

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            locale="en",
            user_agent=UA,
        )
        page = context.new_page()

        def on_response(response):
            if API_MARKER not in response.url:
                return

            try:
                responses.append({
                    "api_url": response.url,
                    "status": response.status,
                    "body": response.json(),
                })
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            for _ in range(30):
                if responses:
                    break
                page.wait_for_timeout(1000)

            final_url = page.url
        finally:
            browser.close()

    return final_url, responses


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python scraper.py URL")

    try:
        input_url = extract_url(sys.argv[1])

        # السوق الافتراضي المطلوب هو مصر.
        url = normalize_input(input_url, market="EG")

        print("Requested landing URL:", url, file=sys.stderr)

        final_url, responses = capture(url)

        print("Final page URL:", final_url, file=sys.stderr)

        if not responses:
            sys.exit(
                "لم نلتقط استجابة API. قد تكون الصفحة لم تطلب البيانات "
                "أو ظهر تحقق إضافي."
            )

        result = responses[-1]
        body = result["body"]

        print("API URL:", result["api_url"], file=sys.stderr)
        print("HTTP status:", result["status"], file=sys.stderr)

        if not isinstance(body, dict):
            sys.exit("استجابة API غير متوقعة")

        if str(body.get("code")) != "0":
            sys.exit(
                f"SHEIN API error: {body.get('code')} {body.get('msg')}"
            )

        info = body.get("info") or {}
        raw_products = info.get("normalProducts") or []

        if not raw_products:
            sys.exit(
                "الـ API رجّع استجابة ناجحة من غير منتجات. "
                "راجع Requested landing URL وFinal page URL وAPI URL. "
                "إجبار مسار مصر لا يحوّل group_id سعوديًا إلى مجموعة مصرية."
            )

        products = [normalize_product(product) for product in raw_products]

        output = {
            "title": info.get("title") or "Items shared by your friend",
            "product_count": len(products),
            "products": products,
            "out_of_stock_count": len(info.get("outStock") or []),
            "unavailable_count": len(info.get("unavailable") or []),
            "landing_url": url,
            "final_page_url": final_url,
            "api_url": result["api_url"],
        }

        print(json.dumps(output, ensure_ascii=False))

    except SystemExit:
        raise
    except Exception as error:
        sys.exit(f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
