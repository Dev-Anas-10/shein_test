#!/usr/bin/env python3
"""أداة تشخيص مستقلة (مش بتستورد أي حاجة من التطبيق ومبتغيّرش أي ملف ولا الداتابيس).
بتفتح رابط سلة SHEIN (landing) وتسجل: طلب الـ API، الـ headers، الكوكيز، وشكل الـ JSON والأسعار.
الاستخدام:
  python3 debug_capture.py 'LINK'            # الرابط كما هو
  python3 debug_capture.py 'LINK' --matrix   # + بدون local_country + SA + EG + OTHER
النتيجة الكاملة في /tmp/shein_capture.json (الـ tokens والكوكيز الحساسة مخفية)."""
import json, re, sys
from urllib.parse import urlparse, parse_qs, urlencode
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
API_MARKER = "/bff-api/order/cart/share/landing"
HIDE = re.compile(r"cookie|auth|token|sign|key|secret|session", re.I)
SHOW_COOKIE = re.compile(r"curr|country|lang|local|site|region|area|locale", re.I)
SYMBOLS = {"ج.م": "EGP", "E£": "EGP", "EGP": "EGP", "LE": "EGP",
           "ر.س": "SAR", "SAR": "SAR", "SR": "SAR", "د.إ": "AED", "AED": "AED",
           "$": "USD", "USD": "USD"}

def set_param(url, key, value):
    p = urlparse(url)
    q = parse_qs(p.query, keep_blank_values=True)
    if value is None:
        q.pop(key, None)
    else:
        q[key] = [value]
    return p._replace(query=urlencode(q, doseq=True)).geturl()

def currency_of(price):
    if not isinstance(price, dict):
        return ""
    for k in ("currency", "currencyCode", "currency_code"):
        v = price.get(k)
        if isinstance(v, str) and v.strip():
            return SYMBOLS.get(v.strip(), v.strip().upper())
    txt = re.sub(r"[\u200e\u200f\u061c\s]", "", str(price.get("amountWithSymbol") or ""))
    for sym in sorted(SYMBOLS, key=len, reverse=True):
        if sym in txt:
            return SYMBOLS[sym]
    return ""

def mask(headers):
    return {k: (f"<hidden:{len(v)}>" if HIDE.search(k) else v) for k, v in headers.items()}

def summarize(body):
    info = (body or {}).get("info") or {}
    prods = info.get("normalProducts") or []
    first = prods[0] if prods else {}
    return {
        "code": (body or {}).get("code"),
        "top_level_keys": sorted((body or {}).keys()),
        "info_keys": sorted(info.keys()),
        "info_scalar_flags": {k: v for k, v in info.items() if not isinstance(v, (dict, list))},
        "product_count": len(prods),
        "first_product_keys": sorted(first.keys()),
        "first_product_prices": {k: first.get(k) for k in first
                                 if re.search(r"price|amount|curr|discount|sale|retail", k, re.I)},
        "first_product_name": (first.get("goods_name") or "")[:50],
        "detected_currency": currency_of(first.get("salePrice") or {}),
    }

def run(url):
    reqs, landing_req, found = [], {}, {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="ar", user_agent=UA)
        page = ctx.new_page()

        def on_req(r):
            if "bff-api" in r.url:
                item = {"method": r.method, "url": r.url, "headers": mask(r.headers),
                        "post_data": (r.post_data or "")[:500]}
                reqs.append(item)
                if API_MARKER in r.url and not landing_req:
                    landing_req.update(item)

        def on_resp(r):
            if API_MARKER in r.url and "body" not in found:
                found["status"] = r.status
                try:
                    found["body"] = r.json()
                except Exception:
                    pass

        page.on("request", on_req)
        page.on("response", on_resp)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(30):
                if "body" in found:
                    break
                page.wait_for_timeout(1000)
        finally:
            cookies = [{"name": c["name"], "domain": c["domain"],
                        "value": c["value"] if SHOW_COOKIE.search(c["name"]) else "<hidden>"}
                       for c in ctx.cookies()]
            final = page.url
            b.close()
    return {"requested_url": url, "final_url": final, "landing_status": found.get("status"),
            "landing_request": landing_req, "summary": summarize(found.get("body")),
            "bff_api_calls": [{"method": r["method"], "url": r["url"]} for r in reqs],
            "cookies": cookies, "raw_body": found.get("body")}

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    m = re.search(r"https?://[^\s]+", args[0])
    url = (m.group(0) if m else args[0]).rstrip(".,;")
    if "/cart/share/landing" not in url:
        sys.exit("لازم رابط landing (m.shein.com/.../cart/share/landing?...).")
    variants = [("as-is", url)]
    if "--matrix" in sys.argv:
        variants += [("no local_country", set_param(url, "local_country", None)),
                     ("local_country=SA", set_param(url, "local_country", "SA")),
                     ("local_country=EG", set_param(url, "local_country", "EG")),
                     ("local_country=OTHER", set_param(url, "local_country", "OTHER"))]
    seen, results = set(), []
    for label, u in variants:
        if u in seen:
            continue
        seen.add(u)
        print(f"\n=== {label}\n{u}")
        try:
            r = run(u)
        except Exception as e:
            r = {"requested_url": u, "error": f"{type(e).__name__}: {e}"}
            print("ERROR:", r["error"])
        else:
            print("final url :", r["final_url"])
            print("status    :", r["landing_status"])
            print(json.dumps(r["summary"], ensure_ascii=False, indent=2))
            print("cookies   :", [c["name"] for c in r["cookies"]][:40])
        r["label"] = label
        results.append(r)
    print("\n--- مقارنة سريعة ---")
    for r in results:
        s = r.get("summary") or {}
        print(f"{r['label']:<22} currency={s.get('detected_currency') or '?':<5} "
              f"prices={s.get('first_product_prices')}")
    with open("/tmp/shein_capture.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nالنتيجة الكاملة: /tmp/shein_capture.json")

if __name__ == "__main__":
    main()
