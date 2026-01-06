from bs4 import BeautifulSoup
import time
import re
import json
from urllib.parse import urljoin

try:
    import cloudscraper
    _HAS_CLOUDSCRAPER = True
except Exception:
    import requests
    _HAS_CLOUDSCRAPER = False

def _abs_url(base, u):
    if not u:
        return None
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin(base, u)

def _to_float(val):
    if val is None:
        return None
    s = str(val)
    m = re.search(r'[\d.,]+', s)
    if not m:
        return None
    s = m.group(0).replace(',', '')
    try:
        return float(s)
    except:
        return None

def _price_from_offers(offers):
    if not offers:
        return None

    def from_dict(d):
        if not isinstance(d, dict):
            return None
        for k in ('price', 'lowPrice', 'highPrice'):
            p = _to_float(d.get(k))
            if p:
                return p
        spec = d.get('priceSpecification')
        if isinstance(spec, dict):
            for k in ('price', 'minPrice', 'maxPrice'):
                p = _to_float(spec.get(k))
                if p:
                    return p
        return None

    if isinstance(offers, dict):
        return from_dict(offers)
    if isinstance(offers, list):
        for o in offers:
            p = _price_from_offers(o)
            if p:
                return p
    return None

def _json_loads_loose(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None

def _bfs_find_first_numeric(obj, keys_priority):
    # Breadth-first search through nested dict/list to find first numeric under any key in keys_priority
    if obj is None:
        return None
    q = [obj]
    seen = 0
    while q and seen < 150000:
        seen += 1
        cur = q.pop(0)
        if isinstance(cur, dict):
            # direct matches
            for k in keys_priority:
                if k in cur:
                    p = _to_float(cur[k])
                    if p:
                        return p
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    q.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    q.append(v)
    return None

def _bfs_find_first_image(obj):
    # Look for a usable image URL anywhere in nested JSON
    if obj is None:
        return None
    q = [obj]
    seen = 0
    while q and seen < 150000:
        seen += 1
        cur = q.pop(0)
        if isinstance(cur, dict):
            # Try common image keys first
            for k in ('image', 'imageUrl', 'thumbnail', 'primaryImage', 'url'):
                v = cur.get(k)
                if isinstance(v, str) and re.search(r'\.(?:jpg|jpeg|png|webp)(?:\?|$)', v, re.I):
                    return v
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and re.search(r'\.(?:jpg|jpeg|png|webp)(?:\?|$)', item, re.I):
                            return item
                        if isinstance(item, dict):
                            u = item.get('url') or item.get('image') or item.get('imageUrl')
                            if isinstance(u, str) and re.search(r'\.(?:jpg|jpeg|png|webp)(?:\?|$)', u, re.I):
                                return u
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    q.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    q.append(v)
    return None

def _extract_any_json_blobs(html, soup):
    blobs = []

    # 1) Explicit JSON scripts
    for sc in soup.find_all('script', {'type': ['application/json', 'application/ld+json']}):
        text = sc.string or sc.get_text(strip=True)
        if not text:
            continue
        data = _json_loads_loose(text)
        if data is not None:
            blobs.append(data)

    # 2) __NEXT_DATA__
    sc = soup.find('script', id='__NEXT_DATA__') or soup.select_one('script#__NEXT_DATA__')
    if sc and (sc.string or sc.get_text()):
        data = _json_loads_loose(sc.string or sc.get_text())
        if data is not None:
            blobs.append(data)

    # 3) __APOLLO_STATE__ or dataLayer in inline JS
    #   a) Apollo
    m = re.search(r'__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>', html, re.S)
    if m:
        try:
            blobs.append(json.loads(m.group(1)))
        except Exception:
            pass
    #   b) dataLayer
    for m in re.finditer(r'dataLayer\s*=\s*(```math[\s\S]*?```)\s*;', html):
        try:
            blobs.append(json.loads(m.group(1)))
        except Exception:
            pass
    for m in re.finditer(r'dataLayer\.pushKATEX_INLINE_OPEN\s*(\{[\s\S]*?\})\s*KATEX_INLINE_CLOSE\s*;', html):
        try:
            blobs.append(json.loads(m.group(1)))
        except Exception:
            pass

    # 4) Generic inline JSON that obviously contains price keys
    for m in re.finditer(r'(\{[\s\S]*?(?:finalPrice|youPay|offerPrice|sellingPrice|currentPrice|price)[\s\S]*?\})', html):
        data = _json_loads_loose(m.group(1))
        if data:
            blobs.append(data)

    return blobs

def get_croma_product_details(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        # keep gzip/deflate to avoid br unless brotli is installed automatically by cloudscraper
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/"
    }

    try:
        # Choose session
        if _HAS_CLOUDSCRAPER:
            session = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
            )
            # cloudscraper handles brotli automatically
            session.headers.update(headers)
        else:
            import requests
            session = requests.Session()
            session.headers.update(headers)

        time.sleep(1.0)
        resp = session.get(url, timeout=25)
        if resp.status_code >= 400:
            print(f"Croma: HTTP {resp.status_code}")
            return None

        html = resp.text
        lower = html.lower()
        if any(x in lower for x in ["captcha", "access denied", "just a moment", "enable javascript"]):
            print("Croma: anti-bot or JS wall encountered. Install/use cloudscraper or proxy.")
            return None

        soup = BeautifulSoup(html, "lxml")

        # 1) Product JSON-LD (title, price, image, rating)
        product_data = None
        for sc in soup.find_all('script', type='application/ld+json'):
            text = sc.string or sc.get_text(strip=True)
            if not text:
                continue
            data = _json_loads_loose(text)
            if not data:
                continue

            def pick_product(obj):
                return obj if isinstance(obj, dict) and obj.get('@type') == 'Product' else None

            pd = None
            if isinstance(data, dict):
                pd = pick_product(data)
                if not pd and isinstance(data.get('@graph'), list):
                    for it in data['@graph']:
                        pd = pick_product(it)
                        if pd:
                            break
            elif isinstance(data, list):
                for it in data:
                    pd = pick_product(it)
                    if pd:
                        break
            if pd:
                product_data = pd
                break

        # Title
        title = None
        if product_data and product_data.get('name'):
            title = product_data['name']

        if not title:
            for sel in ['meta[property="og:title"]', 'meta[name="twitter:title"]']:
                mt = soup.select_one(sel)
                if mt and mt.get('content'):
                    title = mt['content'].strip()
                    break

        if not title:
            for sel in [
                'h1.pdp-product-name', 'h1.pdp-title', '.product-title h1',
                'h1[data-testid="productName"]', 'h1.product-name', 'h1'
            ]:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break

        if not title:
            print("Croma: Title not found (possible bot wall).")
            return None

        # Price
        price = None
        if product_data and product_data.get('offers'):
            price = _price_from_offers(product_data['offers'])

        if not price:
            # Meta fallbacks
            for sel in [
                'meta[itemprop="price"]',
                'meta[property="product:price:amount"]',
                'meta[property="og:price:amount"]',
                'meta[name="twitter:data1"]'
            ]:
                mt = soup.select_one(sel)
                if mt and mt.get('content'):
                    p = _to_float(mt['content'])
                    if p:
                        price = p
                        break

        # Parse all JSON blobs (__NEXT_DATA__, dataLayer, etc.)
        if not price:
            blobs = _extract_any_json_blobs(html, soup)
            if blobs:
                priority = ['finalPrice', 'youPay', 'offerPrice', 'sellingPrice', 'currentPrice', 'price', 'displayPrice', 'totalPayable', 'amount']
                for b in blobs:
                    p = _bfs_find_first_numeric(b, priority)
                    if p:
                        price = p
                        break

        # DOM fallbacks (last resort)
        if not price:
            price_selectors = [
                '.pdp-price .amount',
                '.product-price .final-price',
                '.product-price .amount',
                '.price-final',
                '.new-price',
                '.selling-price',
                '.current-price',
                '.cp-price__current',
                '.pdp__price',
                '.pdp-price'
            ]
            for sel in price_selectors:
                el = soup.select_one(sel)
                if not el:
                    continue
                txt = el.get('content') if el.name == 'meta' else el.get_text(" ", strip=True)
                p = _to_float(txt)
                if p:
                    price = p
                    break

        # Regex last chance
        if not price:
            m = re.search(
                r'"(finalPrice|youPay|offerPrice|sellingPrice|currentPrice|price|displayPrice|totalPayable|amount)"\s*:\s*"?([\d,\.]+)"?',
                html, re.I
            )
            if m:
                price = _to_float(m.group(2))

        # Image
        image = None
        if product_data and product_data.get('image'):
            img = product_data['image']
            if isinstance(img, list):
                image = img[0] if img else None
            else:
                image = img
            image = _abs_url(url, image)

        if not image:
            for sel in ['meta[property="og:image"]', 'meta[name="twitter:image"]', 'link[rel="image_src"]']:
                mt = soup.select_one(sel)
                if mt and mt.get('content'):
                    image = _abs_url(url, mt['content'])
                    if image:
                        break

        if not image:
            img_selectors = [
                'img[itemprop="image"]',
                '.pdp-image img',
                '.product-image img',
                '.main-image img',
                '.gallery-image img',
                '.product-gallery img',
                '.pdp__image img',
                '.swiper-slide img',
                'picture source'  # for <picture><source srcset=...>
            ]
            for sel in img_selectors:
                el = soup.select_one(sel)
                if not el:
                    continue
                src = el.get('src') or el.get('data-src') or el.get('data-original') or el.get('data-lazy')
                if not src:
                    srcset = el.get('srcset') or el.get('data-srcset')
                    if srcset:
                        # take highest-res from srcset
                        parts = [p.strip().split(' ') for p in srcset.split(',')]
                        if parts:
                            src = parts[-1][0]
                if src:
                    image = _abs_url(url, src)
                    if image:
                        break

        # If still no image, try from JSON blobs
        if not image:
            blobs = _extract_any_json_blobs(html, soup)
            for b in blobs:
                u = _bfs_find_first_image(b)
                if u:
                    image = _abs_url(url, u)
                    break

        # Rating and count
        rating = None
        rating_count = None
        if product_data and product_data.get('aggregateRating'):
            agg = product_data['aggregateRating']
            rating = _to_float(agg.get('ratingValue'))
            rc = _to_float(agg.get('reviewCount') or agg.get('ratingCount'))
            rating_count = int(rc) if rc is not None else None

        if rating is None:
            for sel in ['.rating-value', '.star-rating .rating', '[itemprop="ratingValue"]', '.reviews-rating']:
                el = soup.select_one(sel)
                if el:
                    val = el.get('content') if el.name == 'meta' else el.get_text()
                    r = _to_float(val)
                    if r is not None:
                        rating = r
                        break

        if rating_count is None:
            for sel in ['.rating-count', '.reviews-count', '.total-reviews', '[itemprop="reviewCount"]']:
                el = soup.select_one(sel)
                if el:
                    val = el.get('content') if el.name == 'meta' else el.get_text()
                    m = re.search(r'([\d,]+)', val)
                    if m:
                        try:
                            rating_count = int(m.group(1).replace(',', ''))
                            break
                        except:
                            pass

        return {
            'title': title[:200],
            'price': price,
            'image': image,
            'rating': rating,
            'rating_count': rating_count
        }

    except Exception as e:
        print(f"Croma scraping error: {e}")
        return None