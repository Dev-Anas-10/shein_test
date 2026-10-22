import html, json, re, sys
from urllib.parse import urlparse, parse_qs, urlencode
import requests
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
API_MARKER = "/bff-api/order/cart/share/landing"
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

def landing_url(group_id, shc, country="SA"):
    q = urlencode({"shc": shc, "group_id": group_id, "local_country": country,
                   "url_from": "", "cart_share": 1})
    return "https://m.shein.com/ar/cart/share/landing?" + q

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
    country = str(info.get("localcountry") or "SA")
    return landing_url(gid, shc, country)

def normalize_input(url):
    if not is_shein(url):
        raise ValueError("لازم رابط SHEIN رسمي")
    q = parse_qs(urlparse(url).query)
    gid = q.get("group_id", [""])[0]
    if gid and re.fullmatch(r"[A-Za-z0-9_-]+", gid) and "/cart/share/landing" in url:
        return url          # رابط مباشر
    return resolve_short(url)  # رابط قصير

def normalize_product(p):
    img = p.get("goods_img") or ""
    if img.startswith("//"):
        img = "https:" + img
    elif img.startswith("/"):
        img = "https://m.shein.com" + img
    retail = p.get("retailPrice") or {}
    sale = p.get("salePrice") or {}
    return {
        "goods_id": str(p.get("goods_id") or ""),
        "goods_sn": str(p.get("goods_sn") or ""),
        "name": p.get("goods_name") or "",
        "image": img,
        "original_price": str(retail.get("amount") or ""),
        "sale_price": str(sale.get("amount") or ""),
        "currency": "SAR",
        "selected_variant": p.get("goodsAttr") or "",
        "sku_code": p.get("sku_code") or "",
        "sold_out": bool(p.get("soldOutStatus", False)),
    }

def capture(url):
    found = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ar", user_agent=UA)
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
        url = normalize_input(extract_url(sys.argv[1]))
        body = capture(url)
        if not isinstance(body, dict):
            sys.exit("ما اتلقطتش استجابة SHEIN (ممكن SHEIN طلبت تحقق إضافي أو الرابط منتهي)")
        if str(body.get("code")) != "0":
            sys.exit(f"SHEIN API error: {body.get('code')} {body.get('msg')}")
        info = body.get("info") or {}
        raw = info.get("normalProducts") or []
        if not raw:
            sys.exit("مفيش منتجات في السلة دي")
        products = [normalize_product(p) for p in raw]
        out = {
            "title": info.get("title") or "العناصر التي شاركها صديقك",
            "product_count": len(products),
            "products": products,
            "out_of_stock_count": len(info.get("outStock") or []),
            "unavailable_count": len(info.get("unavailable") or []),
            "landing_url": url,
        }
        print(json.dumps(out, ensure_ascii=False))
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(f"{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
