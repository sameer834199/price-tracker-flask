from bs4 import BeautifulSoup
import requests
import time
import re
import json

def _to_float(num):
    if num is None:
        return None
    s = str(num)
    m = re.search(r'[\d.,]+', s)
    if not m:
        return None
    s = m.group(0).replace(',', '')
    try:
        return float(s)
    except:
        return None

def _price_from_offers(offers):
    # Handles Offer, AggregateOffer, and lists of them
    if not offers:
        return None

    def from_dict(d):
        if not isinstance(d, dict):
            return None
        # Common places where price can live
        for key in ('price', 'lowPrice', 'highPrice'):
            p = _to_float(d.get(key))
            if p:
                return p
        spec = d.get('priceSpecification')
        if isinstance(spec, dict):
            for key in ('price', 'minPrice', 'maxPrice'):
                p = _to_float(spec.get(key))
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

def get_myntra_product_details(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        # Avoid advertising Brotli unless requests has brotli installed
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/"
    }

    try:
        time.sleep(1.5)
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # 1) JSON-LD Product block (most reliable)
        product_data = None
        for script in soup.find_all('script', type='application/ld+json'):
            text = script.string or script.get_text(strip=True)
            if not text:
                continue
            # Some pages contain a list; others a single object
            try:
                data = json.loads(text)
            except Exception:
                # best-effort: extract the first JSON object
                m = re.search(r'\{.*\}', text, re.S)
                if not m:
                    continue
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    continue

            def pick_product(obj):
                if isinstance(obj, dict) and obj.get('@type') == 'Product':
                    return obj
                return None

            if isinstance(data, dict):
                pd = pick_product(data)
                if pd:
                    product_data = pd
                    break
                # sometimes Product is nested in @graph
                if '@graph' in data and isinstance(data['@graph'], list):
                    for item in data['@graph']:
                        pd = pick_product(item)
                        if pd:
                            product_data = pd
                            break
                if product_data:
                    break
            elif isinstance(data, list):
                for item in data:
                    pd = pick_product(item)
                    if pd:
                        product_data = pd
                        break
                if product_data:
                    break

        # Title
        title = None
        if product_data and product_data.get('name'):
            title = product_data['name']

        if not title:
            title_selectors = [
                'h1.pdp-title', 'h1.pdp-name', '.pdp-product-name',
                'h1[data-testid="name"]', '.product-base-title h1',
                '.pdp-e-product-title', '.product-title', 'h1'
            ]
            for sel in title_selectors:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break

        if not title:
            print("Myntra: Could not find product title")
            return None

        # Price
        price = None
        if product_data and product_data.get('offers'):
            price = _price_from_offers(product_data['offers'])

        # Meta fallbacks
        if not price:
            meta = soup.select_one('meta[itemprop="price"]')
            if meta and meta.get('content'):
                price = _to_float(meta.get('content'))

        if not price:
            meta = soup.select_one('meta[property="product:price:amount"]')
            if meta and meta.get('content'):
                price = _to_float(meta.get('content'))

        # DOM selectors fallbacks (Myntra class names change often)
        if not price:
            price_selectors = [
                'span.pdp-price > strong',
                'div.pdp-price > strong',
                'span.pdp-discounted-price',
                'div.pdp-price-info span',
                '.product-discountedPrice',
                '.product-discountPrice',
                '.pdp-offers-price',
                '.price-current',
                '.pdp-price'  # generic last resort
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

        # As an ultimate fallback, try to sniff a JSON blob for price-ish fields
        if not price:
            m = re.search(r'"(offerPrice|discountedPrice|price)"\s*:\s*"?([\d,\.]+)"?', html, re.I)
            if m:
                price = _to_float(m.group(2))

        # Image
        image = None
        if product_data and product_data.get('image'):
            image = product_data['image'][0] if isinstance(product_data['image'], list) else product_data['image']

        if not image:
            image_selectors = [
                '.pdp-product-img img',
                '.image-grid img',
                '.product-image img',
                '.product-sliderImage img',
                '.product-base-imgContainer img'
            ]
            for sel in image_selectors:
                el = soup.select_one(sel)
                if el:
                    image = el.get('src') or el.get('data-src') or el.get('data-original')
                    if image and image.startswith('http'):
                        break

        # Rating
        rating = None
        if product_data and product_data.get('aggregateRating'):
            rating = _to_float(product_data['aggregateRating'].get('ratingValue'))

        if not rating:
            for sel in ['.index-overallRating', '[data-testid="rating"]', '.ratings-rating']:
                el = soup.select_one(sel)
                if el:
                    m = re.search(r'(\d+\.?\d*)', el.get_text())
                    if m:
                        rating = _to_float(m.group(1))
                        if rating:
                            break

        # Rating count
        rating_count = None
        if product_data and product_data.get('aggregateRating'):
            rating_count = _to_float(product_data['aggregateRating'].get('reviewCount'))
            rating_count = int(rating_count) if rating_count else None

        if not rating_count:
            for sel in ['.index-ratingsCount', '.ratings-count', '[data-testid="ratingsCount"]']:
                el = soup.select_one(sel)
                if el:
                    m = re.search(r'([\d,]+)', el.get_text())
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

    except requests.RequestException as e:
        print(f"Myntra request error: {e}")
        return None
    except Exception as e:
        print(f"Myntra scraping error: {e}")
        return None