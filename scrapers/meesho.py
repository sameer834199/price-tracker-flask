# scrapers/meesho.py
from __future__ import annotations
from bs4 import BeautifulSoup
import requests, time, re, json, html

UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36")

def _ensure_https(u: str) -> str:
    if not u: return u
    if u.startswith("//"): return "https:" + u
    if u.startswith("http://"): return "https://" + u[7:]
    return u

def _price_num(s: str) -> float | None:
    if not s: return None
    m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", s)
    if m:
        try: return float(m.group(1).replace(",", ""))
        except: pass
    t = re.sub(r"[^\d.]", "", s)
    if not t: return None
    try: return float(t)
    except: return None

def _pick_from_srcset(srcset: str) -> str | None:
    try:
        best, val = None, -1
        for part in [p.strip() for p in srcset.split(",") if p.strip()]:
            seg = part.split()
            url = seg[0]
            d = seg[1] if len(seg) > 1 else "1x"
            n = int(re.sub(r"[^\d]", "", d)) if re.search(r"\d", d) else 1
            if n > val: best, val = url, n
        return best
    except:
        return None

def _read_only_mirror(url: str) -> str | None:
    if url.startswith("https://"):
        mirror = "https://r.jina.ai/http://" + url[len("https://"):]
    elif url.startswith("http://"):
        mirror = "https://r.jina.ai/" + url
    else:
        mirror = "https://r.jina.ai/http://" + url
    try:
        r = requests.get(mirror, timeout=20)
        r.raise_for_status()
        return r.text
    except:
        return None

def _fetch(url: str, headers: dict, use_cloudscraper=False) -> str | None:
    try:
        if use_cloudscraper:
            import cloudscraper
            s = cloudscraper.create_scraper(browser={'browser':'chrome','platform':'windows','desktop':True})
            r = s.get(url, headers=headers, timeout=20, allow_redirects=True)
        else:
            r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception:
        return None

def _pick_jsonld(ld, key: str):
    def find(d):
        if not isinstance(d, dict): return None
        if key == "title" and d.get("@type") in ("Product", "WebPage") and d.get("name"):
            return d["name"]
        if key == "image" and d.get("@type") in ("Product", "WebPage"):
            img = d.get("image")
            if isinstance(img, list) and img: return img[0]
            if isinstance(img, str): return img
        if key == "price" and d.get("@type") in ("Product","Offer","AggregateOffer"):
            offers = d.get("offers")
            if isinstance(offers, dict) and offers.get("price"): return offers.get("price")
            if isinstance(offers, list):
                for off in offers:
                    if isinstance(off, dict) and off.get("price"): return off.get("price")
            if d.get("price"): return d.get("price")
            if d.get("lowPrice"): return d.get("lowPrice")
        for v in d.values():
            if isinstance(v, dict):
                r = find(v)
                if r is not None: return r
            elif isinstance(v, list):
                for it in v:
                    r = find(it)
                    if r is not None: return r
        return None
    if isinstance(ld, list):
        for d in ld:
            r = find(d)
            if r is not None: return r
    elif isinstance(ld, dict):
        return find(ld)
    return None

def get_meesho_product_details(url: str) -> dict | None:
    headers = {
        "User-Agent": UA_DESKTOP,
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.meesho.com/",
        "DNT": "1",
    }
    try:
        time.sleep(1.0)
        html_text = (
            _fetch(url, headers, use_cloudscraper=True) or
            _fetch(url, {"User-Agent": UA_MOBILE, "Accept-Language": "en-IN,en;q=0.9", "Referer": "https://www.meesho.com/", "DNT": "1"}, use_cloudscraper=True) or
            _fetch(url, headers, use_cloudscraper=False) or
            _fetch(url, {"User-Agent": UA_MOBILE, "Accept-Language": "en-IN,en;q=0.9", "Referer": "https://www.meesho.com/", "DNT": "1"}, use_cloudscraper=False) or
            _read_only_mirror(url)
        )
        if not html_text:
            print("Meesho: failed to fetch (403/blocked)")
            return None

        soup = BeautifulSoup(html_text, "lxml")
        title = price = image = None

        # JSON-LD
        for s in soup.select('script[type="application/ld+json"]'):
            raw = (s.string or s.get_text() or "").strip()
            if not raw: continue
            try:
                ld = json.loads(raw)
            except:
                try: ld = json.loads(html.unescape(raw))
                except: continue
            if not ld: continue
            title = title or _pick_jsonld(ld, "title")
            if image is None:
                im = _pick_jsonld(ld, "image")
                if im: image = im
            if price is None:
                pr = _pick_jsonld(ld, "price")
                if pr: price = _price_num(str(pr))

        # Meta fallbacks
        if not title:
            el = soup.select_one('meta[property="og:title"], meta[name="twitter:title"]')
            if el and el.get("content"): title = el.get("content").strip()

        if not image:
            el = soup.select_one('meta[property="og:image:secure_url"], meta[property="og:image"], meta[name="twitter:image"]')
            if el and el.get("content"): image = el.get("content").strip()

        if price is None:
            pm = soup.select_one('meta[property="product:price:amount"]')
            if pm and pm.get("content"): price = _price_num(pm.get("content"))
        if price is None:
            el = soup.select_one('[class*="price"], [id*="price"], [data-testid*="price"]')
            if el: price = _price_num(el.get_text(" ", strip=True))
        if price is None:
            m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", soup.get_text(" ", strip=True))
            if m:
                try: price = float(m.group(1).replace(",", ""))
                except: pass

        # Visible image fallbacks
        if not image:
            pl = soup.select_one('link[rel="preload"][as="image"][href]')
            if pl: image = pl.get("href")
        if not image:
            pic = soup.select_one("main picture img")
            if pic:
                image = pic.get("src") or pic.get("data-src") or pic.get("data-original")
                if not image and pic.get("srcset"):
                    image = _pick_from_srcset(pic.get("srcset"))
        if not image:
            img = soup.select_one("img[src*='images.meesho.com'], img[src*='cdn.meesho.com']")
            if img:
                image = img.get("src") or img.get("data-src") or img.get("data-original")

        if image: image = _ensure_https(image)
        if not title:
            return None

        return {
            "title": (title or "Meesho Product")[:200],
            "price": price,
            "image": image,
            "rating": None,
            "rating_count": None,
        }
    except Exception as e:
        print(f"Meesho scraping error: {e}")
        return None