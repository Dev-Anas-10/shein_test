
import html
import json
import re
import sys
from urllib.parse import urlparse, parse_qs, urlencode

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
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return (
        p.scheme in ("http", "https")
        and (host == "shein.com" or host.endswith(".shein.com"))
    )


URL_RE = re.compile(r"https?://[^\s<>\"']+")


def extract_url(text):
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?)")
        if is_shein(url):
            return url
    raise ValueError("ما لقيتش رابط SHEIN في النص")


def landing_url(group_id, shc, country="OTHER"):
    query = urlencode({
        "shc": shc,
        "group_id": group_id,
        "local_country": country,
        "url_from": "",
        "cart_share": "1",
    })
    return "https://m.shein.com/cart/share/landing?" + query


def resolve_short(short_url):
    session = requests.Session()

    response = session.get(
        short_url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()

    # لو الرابط اتحول بالفعل لرابط مشاركة كامل، احتفظ به.
    final_url = response.url
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query)

    if query.get("group_id") and "/cart/share/landing" in parsed.path:
        return final_url

    # محاولة استخراج رابط appjump من صفحة الرابط القصير.
    appjump = None
    for tag in re.findall(r"<input\b[^>]*>", response.text, re.I):
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

        source = html.unescape(jump_response.text)
        match = re.search(
            r"\bvar\s+shareInfo\s*=\s*(\{.*?\})\s*;",
            source,
            re.S,
        )

        if match:
            info = json.loads(match.group(1))
            group_id = str(info.get("id") or info.get("shareId") or "")
            shc = (
                parse_qs(urlparse(short_url).query).get("shc", [""])[0]
                or parse_qs(urlparse(appjump).query).get("shc", [""])[0]
            )

            if group_id.isdigit() and shc:
                country = str(info.get("localcountry") or "OTHER")
                return landing_url(group_id, shc, country)

    raise ValueError(
        "لم أستطع استخراج رابط مشاركة كامل من الرابط القصير. "
        "جرّب رابط cart/share/landing الأصلي."
    )


def normalize_input(url):
    if not is_shein(url):
        raise ValueError("لازم رابط SHEIN رسمي")

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    if query.get("group_id") and "/cart/share/landing" in parsed.path:
        # لا نعيد بناء الرابط المباشر؛ نحافظ على معطياته الأصلية.
        return url

    return resolve_short(url)


def normalize_product(product):
    image = product.get("goods_img") or ""

    if image.startswith("//"):
        image = "https:" + image
    elif image.startswith("/"):
        image = "https://m.shein.com" + image

    retail = product.get("retailPrice") or {}
    sale = product.get("salePrice") or {}

    # استخدم العملة من السعر إن توفرت، بدل افتراض SAR.
    amount_symbol = sale.get("amountWithSymbol") or ""
    currency = (
        "EGP" if "EGP" in amount_symbol
        else "SAR" if "SAR" in amount_symbol
        else "USD" if "USD" in amount_symbol or "$" in amount_symbol
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
        "sale_price_display": amount_symbol,
        "currency": currency,
        "selected_variant": product.get("goodsAttr") or "",
        "sku_code": product.get("sku_code") or "",
        "sold_out": bool(product.get("soldOutStatus", False)),
    }


def capture(url):
    captured = []

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
                body = response.json()
                captured.append({
                    "api_url": response.url,
                    "status": response.status,
                    "body": body,
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
                if captured:
                    break
                page.wait_for_timeout(1000)

            final_url = page.url
        finally:
            browser.close()

    return final_url, captured


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python scraper.py URL")

    try:
        url = normalize_input(extract_url(sys.argv[1]))
        final_url, responses = capture(url)

        print("Final page URL:", final_url, file=sys.stderr)

        if not responses:
            sys.exit(
                "لم نلتقط استجابة API. قد تكون الصفحة لم تطلب البيانات "
                "أو ظهر تحقق إضافي."
            )

        # نختار آخر استجابة ملتقطة، ونوضح نتيجتها بدل تجاهلها.
        result = responses[-1]
        body = result["body"]

        print("API URL:", result["api_url"], file=sys.stderr)
        print("HTTP status:", result["status"], file=sys.stderr)

        if not isinstance(body, dict):
            sys.exit("استجابة API غير متوقعة.")

        if str(body.get("code")) != "0":
            sys.exit(
                f"SHEIN API error: {body.get('code')} {body.get('msg')}"
            )

        info = body.get("info") or {}
        raw_products = info.get("normalProducts") or []

        if not raw_products:
            sys.exit(
                "الـ API رجّع استجابة ناجحة لكن من غير منتجات. "
                "راجع Final page URL وAPI URL لتحديد السوق الذي تم الوصول إليه."
            )

        products = [normalize_product(p) for p in raw_products]

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
