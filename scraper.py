import html, json, os, re, sys
from urllib.parse import urlparse, parse_qs, urlencode
import requests
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
API_MARKER = "/bff-api/order/cart/share/landing"

# قيمة local_country المطلوبة للأسعار. الموقع المصري بيستخدم OTHER (من رابط السلة المصري).
# تتغير من SHEIN_COUNTRY في /etc/shein-site.env
TARGET_COUNTRY = (os.environ.get("SHEIN_COUNTRY") or "OTHER").strip().upper()
COUNTRY_CURRENCY = {"OTHER": "EGP", "EG": "EGP", "SA": "SAR", "AE": "AED", "KW": "KWD",
                    "QA": "QAR", "BH": "BHD", "OM": "OMR", "JO": "JOD"}
SYMBOLS = {"ج.م": "EGP", "E£": "EGP", "EGP": "EGP", "LE": "EGP",
           "ر.س": "SAR", "SAR": "SAR", "SR": "SAR",
           "د.إ": "AED", "AED": "AED", "$": "USD", "USD": "USD"}
HEADERS = {"User-Agent": UA}

def is_shein(url):
    p = urlparse(url)
    h = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and (h == "shein.com" or h.endswith(".shein.com"))

URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")

def extract_url(text):
    """يلقط أول رابط SHEIN من أي نص (حتى لو فيه كلام زيادة حواليه)."""
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?)'\"")
        if is_shein(u):
            return u
    raise ValueError("ما لقيتش رابط SHEIN في النص")

def landing_url(group_id, shc, country=None):
    q = urlencode({"shc": shc, "group_id": group_id,
                   "local_country": country or TARGET_COUNTRY,
                   "url_from": "", "cart_share": 1})
    return "https://m.shein.com/cart/share/landing?" + q

def force_country(url, country):
    """يغيّر local_country في رابط landing للدولة المطلوبة."""
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    q["local_country"] = [country]
    return p._replace(query=urlencode(q, doseq=True)).geturl()

def landing_variants(url):
    """نفس الرابط بدون /ar الأول (شكل رابط الموقع المصري)، وبعدين مع /ar."""
    p = urlparse(url)
    plain = re.sub(r"^/ar(?=/)", "", p.path)
    out = []
    for path in (plain, "/ar" + plain):
        u = p._replace(path=path).geturl()
        if u not in out:
            out.append(u)
    return out

def body_products(body):
    return ((body or {}).get("info") or {}).get("normalProducts") or []

def body_currencies(body):
    return {currency_of(x.get("salePrice") or {}) or currency_of(x.get("retailPrice") or {})
            for x in body_products(body)}

def currency_of(price):
    """يقرأ عملة السعر من استجابة SHEIN (حقل currency أو رمز العملة)."""
    if not isinstance(price, dict):
        return ""
    for k in ("currency", "currencyCode", "currency_code"):
        v = price.get(k)
        if isinstance(v, str) and v.strip():
            v = v.strip()
            return SYMBOLS.get(v, v.upper())
    txt = re.sub(r"[\u200e\u200f\u061c\s]", "", str(price.get("amountWithSymbol") or ""))
    for sym in sorted(SYMBOLS, key=len, reverse=True):
        if sym in txt:
            return SYMBOLS[sym]
    return ""

def resolve_short(short_url):
    s = requests.Session()
    first = s.get(short_url, headers=HEADERS, timeout=30)
    first.raise_for_status()
    appjump = None
    for tag in re.findall(r"<input\b[^>]*>", first.text, re.I):
        if re.search(r'\bid=["\']url["\']', tag, re.I):
            m = re.search(r'\bvalue=["\']([^"\']+)["\']', tag, re.I)
            if m:
                appjump = html.unescape(m.group(1))
                break
    if not appjump or not is_shein(appjump):
        raise ValueError("ما لقيتش رابط appjump في الرابط القصير")
    second = s.get(appjump, headers=HEADERS, timeout=30)
    second.raise_for_status()
    src = html.unescape(second.text)
    m = re.search(r"\bvar\s+shareInfo\s*=\s*(\{.*?\})\s*;", src, re.S)
    if not m:
        raise ValueError("ما لقيتش shareInfo في الصفحة")
    info = json.loads(m.group(1))
    gid = str(info.get("id") or info.get("shareId") or "")
    if not gid.isdigit():
        raise ValueError("group_id غير صالح")
    shc = (parse_qs(urlparse(short_url).query).get("shc", [""])[0]
           or parse_qs(urlparse(appjump).query).get("shc", [""])[0])
    if not shc:
        raise ValueError("ما لقيتش shc في الرابط")
    return landing_url(gid, shc, TARGET_COUNTRY)

def normalize_input(url):
    if not is_shein(url):
        raise ValueError("لازم رابط SHEIN رسمي")
    q = parse_qs(urlparse(url).query)
    gid = q.get("group_id", [""])[0]
    if gid and re.fullmatch(r"[A-Za-z0-9_-]+", gid) and "/cart/share/landing" in url:
        return force_country(url, TARGET_COUNTRY)  # رابط مباشر
    return resolve_short(url)  # رابط قصير

def normalize_product(p):
    img = p.get("goods_img") or ""
    if img.startswith("//"):
        img = "https:" + img
    elif img.startswith("/"):
        img = "https://m.shein.com" + img
    retail = p.get("retailPrice") or {}
    sale = p.get("salePrice") or {}
    cur = currency_of(sale) or currency_of(retail)
    return {
        "goods_id": str(p.get("goods_id") or ""),
        "goods_sn": str(p.get("goods_sn") or ""),
        "name": p.get("goods_name") or "",
        "image": img,
        "original_price": str(retail.get("amount") or ""),
        "sale_price": str(sale.get("amount") or ""),
        "currency": cur or COUNTRY_CURRENCY.get(TARGET_COUNTRY, ""),
        "currency_detected": bool(cur),
        "selected_variant": p.get("goodsAttr") or "",
        "sku_code": p.get("sku_code") or "",
        "sold_out": bool(p.get("soldOutStatus", False)),
    }

def capture(url):
    found = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ar-EG", timezone_id="Africa/Cairo", user_agent=UA)
        page = ctx.new_page()

        def on_response(r):
            if API_MARKER in r.url and "body" not in found:
                try:
                    found["body"] = r.json()
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(30):
                if "body" in found:
                    break
                page.wait_for_timeout(1000)
        finally:
            browser.close()
    return found.get("body")

def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: scraper.py URL")
    try:
        base = normalize_input(extract_url(sys.argv[1]))
        expected = COUNTRY_CURRENCY.get(TARGET_COUNTRY, "")
        body = url = fallback = None
        last_err = "ما اتلقطتش استجابة SHEIN (ممكن SHEIN طلبت تحقق إضافي أو الرابط منتهي)"
        for cand in landing_variants(base):
            b = capture(cand)
            if not isinstance(b, dict):
                continue
            if str(b.get("code")) != "0":
                last_err = f"SHEIN API error: {b.get('code')} {b.get('msg')}"
                continue
            if not body_products(b):
                last_err = "مفيش منتجات في السلة دي"
                continue
            if not expected or body_currencies(b) == {expected}:
                body, url = b, cand      # العملة المتوقعة (جنيه) — نوقف هنا
                break
            if fallback is None:
                fallback = (b, cand)     # استجابة سليمة بعملة تانية — احتياطي
        if body is None and fallback:
            body, url = fallback
        if body is None:
            sys.exit(last_err)
        info = body.get("info") or {}
        raw = body_products(body)
        if os.environ.get("SHEIN_DEBUG") == "1":
            with open("/tmp/shein_last_body.json", "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
        products = [normalize_product(p) for p in raw]
        expected = COUNTRY_CURRENCY.get(TARGET_COUNTRY, "")
        got = {x["currency"] for x in products}
        warning = ""
        if not all(x["currency_detected"] for x in products):
            warning = "ما قدرتش أتأكد من عملة الأسعار اللي رجعتها SHEIN."
        elif expected and got != {expected}:
            warning = ("SHEIN رجّعت الأسعار بعملة %s بدل %s — الأسعار دي مش بتاعة الموقع المطلوب."
                       % ("/".join(sorted(got)), expected))
        out = {
            "title": info.get("title") or "العناصر التي شاركها صديقك",
            "product_count": len(products),
            "products": products,
            "out_of_stock_count": len(info.get("outStock") or []),
            "unavailable_count": len(info.get("unavailable") or []),
            "landing_url": url,
            "country": TARGET_COUNTRY,
            "currency_warning": warning,
        }
        print(json.dumps(out, ensure_ascii=False))
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
