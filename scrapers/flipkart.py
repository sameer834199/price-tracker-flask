# scrapers/flipkart.py
from __future__ import annotations
from bs4 import BeautifulSoup
import requests, time, re

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

def _num(s: str) -> float | None:
    if not s:
        return None
    m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", s)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            return None
    t = re.sub(r"[^\d.]", "", s)
    if not t:
        return None
    try:
        return float(t)
    except Exception:
        return None

def _ensure_https(u: str) -> str:
    if not u: return u
    if u.startswith("//"): return "https:" + u
    if u.startswith("http://"): return "https://" + u[7:]
    return u

def _pick_from_srcset(srcset: str) -> str | None:
    try:
        best, val = None, -1
        for part in [p.strip() for p in srcset.split(",") if p.strip()]:
            seg = part.split()
            url = seg[0]
            desc = seg[1] if len(seg) > 1 else "1x"
            n = int(re.sub(r"[^\d]", "", desc)) if re.search(r"\d", desc) else 1
            if n > val:
                best, val = url, n
        return best
    except Exception:
        return None

def _jsonld_price(soup: BeautifulSoup) -> float | None:
    for s in soup.select('script[type="application/ld+json"]'):
        raw = (s.string or s.get_text() or "").strip()
        if not raw:
            continue
        # Try strict first
        try:
            import json
            ld = json.loads(raw)
        except Exception:
            # Be forgiving about minor escapes
            try:
                import html as _html, json
                ld = json.loads(_html.unescape(raw))
            except Exception:
                continue
        if not ld:
            continue

        def find_price(d):
            if not isinstance(d, dict):
                return None
            # Product -> offers -> price
            if d.get("@type") in ("Product", "Offer", "AggregateOffer"):
                offers = d.get("offers")
                if isinstance(offers, dict) and offers.get("price"):
                    return _num(str(offers.get("price")))
                if isinstance(offers, list):
                    for off in offers:
                        if isinstance(off, dict) and off.get("price"):
                            v = _num(str(off.get("price")))
                            if v is not None:
                                return v
                for k in ("price", "lowPrice", "highPrice"):
                    if d.get(k) is not None:
                        v = _num(str(d.get(k)))
                        if v is not None:
                            return v
            # nested
            for v in d.values():
                if isinstance(v, dict):
                    r = find_price(v)
                    if r is not None:
                        return r
                elif isinstance(v, list):
                    for it in v:
                        r = find_price(it)
                        if r is not None:
                            return r
            return None

        if isinstance(ld, list):
            for d in ld:
                p = find_price(d)
                if p is not None:
                    return p
        elif isinstance(ld, dict):
            p = find_price(ld)
            if p is not None:
                return p
    return None

def get_flipkart_product_details(url: str) -> dict | None:
    headers = {
        "User-Agent": UA,
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.flipkart.com/",
        "DNT": "1",
    }
    try:
        time.sleep(1.0)
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        # Title (robust)
        title = None
        for sel in ['span.B_NuCI', 'h1.YoV1Gd', 'meta[property="og:title"]', 'meta[name="twitter:title"]']:
            el = soup.select_one(sel)
            if el:
                title = el.get("content").strip() if el.name == "meta" else el.get_text(strip=True)
                if title:
                    break
        if not title:
            return None

        # Price (JSON-LD -> meta -> new classes -> fallback regex)
        price = _jsonld_price(soup)

        if price is None:
            # meta tags sometimes carry the price
            for sel in [
                'meta[property="product:price:amount"]',
                'meta[itemprop="price"]',
                'meta[name="twitter:data1"]',
            ]:
                el = soup.select_one(sel)
                if el and el.get("content"):
                    price = _num(el.get("content"))
                    if price is not None:
                        break

        if price is None:
            # New Flipkart price classes observed recently
            price_selectors = [
                'div._30jeq3._16Jk6d',  # classic
                'div._30jeq3',
                'span._30jeq3._16Jk6d',
                'span._30jeq3',
                # Newer variants
                'div.Nx9bqj', 'span.Nx9bqj',
                'div.CxhGGd', 'span.CxhGGd',
                'div.CEmiEU .Nx9bqj', 'div.CEmiEU .CxhGGd',
                'div._25b18c ._30jeq3',
            ]
            for sel in price_selectors:
                el = soup.select_one(sel)
                if el:
                    price = _num(el.get_text(" ", strip=True))
                    if price is not None:
                        break

        if price is None:
            # Last resort: first ₹number on page
            txt = soup.get_text(" ", strip=True)
            price = _num(txt)

        # Image (your images already fetch; keep robust extraction)
        image = None
        for sel in [
            'img._2r_T1I', 'img._396cs4', 'img.CXW8mj',
            'meta[property="og:image"]', 'meta[name="twitter:image"]'
        ]:
            el = soup.select_one(sel)
            if el:
                if el.name == "meta" and el.get("content"):
                    image = el.get("content").strip()
                else:
                    image = el.get("src") or el.get("data-src")
                    if not image and el.get("srcset"):
                        image = _pick_from_srcset(el.get("srcset"))
                if image:
                    break
        if image:
            image = _ensure_https(image)

        return {
            "title": title[:200],
            "price": price,
            "image": image,
            "rating": None,
            "rating_count": None,
        }
    except requests.RequestException as e:
        print(f"Flipkart request error: {e}")
        return None
    except Exception as e:
        print(f"Flipkart scraping error: {e}")
        return None