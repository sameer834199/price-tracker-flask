# scrapers/amazon.py (or wherever you keep it)
from bs4 import BeautifulSoup
import requests, time, re, json, html
from urllib.parse import urlparse

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

def _pick_largest_from_dynamic_json(attr_value: str) -> str | None:
    """
    Amazon puts image candidates in data-a-dynamic-image as JSON:
    {"https://m.media-amazon.com/...jpg":[500,500], ...}
    Pick the largest (width*height).
    """
    try:
        data = json.loads(html.unescape(attr_value))
        if not isinstance(data, dict):
            return None
        # sort by area desc, return URL
        best = sorted(data.items(), key=lambda kv: (kv[1][0] * kv[1][1]), reverse=True)
        return best[0][0] if best else None
    except Exception:
        return None

def _pick_from_srcset(srcset: str) -> str | None:
    """
    srcset: "url1 1x, url2 2x" or "url 320w, url 640w"
    Pick the largest descriptor.
    """
    try:
        parts = [p.strip() for p in srcset.split(",") if p.strip()]
        best_url, best_val = None, -1
        for p in parts:
            pieces = p.split()
            if not pieces:
                continue
            url = pieces[0]
            if len(pieces) > 1:
                d = pieces[1]
                if d.endswith("w"):
                    val = int(re.sub(r"[^\d]", "", d))
                elif d.endswith("x"):
                    val = int(re.sub(r"[^\d]", "", d))
                else:
                    val = 1
            else:
                val = 1
            if val > best_val:
                best_val, best_url = val, url
        return best_url
    except Exception:
        return None

def _ensure_https(url: str) -> str:
    if not url:
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url

def _clean_amazon_img(url: str) -> str:
    """
    Optional: normalize Amazon image variants to a decent size.
    Eg. turn ...._SY75_.jpg into ...._SL1000_.jpg when possible.
    """
    if not url:
        return url
    # If it has a size token (._SX..., _SY..., _UX..., etc.), you can swap to SL1000
    url = re.sub(r"\._[A-Z]{2}\d+.*?_\.", "._SL1000_.", url)
    return url

def get_amazon_product_details(url: str) -> dict | None:
    headers = {
        "User-Agent": UA,
        "Accept-Language": "en-IN,en;q=0.9",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "Referer": "https://www.amazon.in/" if ".in" in url else "https://www.amazon.com/",
        "DNT": "1",
    }

    try:
        time.sleep(2)  # be nice
        s = requests.Session()
        r = s.get(url, headers=headers, timeout=20, allow_redirects=True)
        r.raise_for_status()

        # Detect robot check/captcha page
        low = r.text.lower()
        if ("captcha" in low) or ("robot check" in low) or ("/errors/validatecaptcha" in r.url.lower()):
            print("Amazon: Blocked by captcha/Robot Check")
            return None

        soup = BeautifulSoup(r.text, "lxml")  # lxml is more robust than html.parser

        # Title (with og:title fallback)
        title = None
        for sel in ['#productTitle', 'h1#title', '.product-title', 'h1.a-size-large', '[data-automation-id="product-title"]',
                    'meta[property="og:title"]']:
            el = soup.select_one(sel)
            if el:
                title = el.get("content").strip() if el.name == "meta" else el.get_text(strip=True)
                if title:
                    break
        if not title:
            print("Amazon: title not found")
            return None

        # Price
        price = None
        for sel in [
            '.a-price .a-offscreen',
            '#priceblock_dealprice', '#priceblock_ourprice', '#corePrice_feature_div .a-offscreen',
            '.a-price .a-price-range .a-price-whole', '.a-price-current .a-offscreen',
            'meta[property="og:price:amount"]'
        ]:
            el = soup.select_one(sel)
            if el:
                txt = el.get("content") if el.name == "meta" else el.get_text()
                txt = (txt or "").strip()
                txt = re.sub(r"[^\d.,]", "", txt.replace("₹", "").replace("$", ""))
                txt = txt.replace(",", "")
                try:
                    price = float(txt)
                    break
                except Exception:
                    pass

        # Image (robust)
        image = None

        # 1) data-a-dynamic-image on landingImage (best)
        landing = soup.select_one("#landingImage, #imgTagWrapperId img, .a-dynamic-image")
        if landing:
            dyn = landing.get("data-a-dynamic-image")
            if dyn:
                image = _pick_largest_from_dynamic_json(dyn)

        # 2) srcset on main image
        if not image and landing and landing.get("srcset"):
            image = _pick_from_srcset(landing.get("srcset"))

        # 3) direct attributes
        if not image and landing:
            image = landing.get("data-old-hires") or landing.get("data-src") or landing.get("src")

        # 4) og:image meta fallback
        if not image:
            og = soup.select_one('meta[property="og:image"]')
            if og and og.get("content"):
                image = og.get("content").strip()

        # 5) last resort: any img in block containers
        if not image:
            for sel in ['#imageBlock_feature_div img', '#ebooksImageBlockContainer img', '#imageBlock img']:
                el = soup.select_one(sel)
                if el:
                    image = el.get("data-old-hires") or el.get("data-src") or el.get("src")
                    if image:
                        break

        if image:
            image = _ensure_https(image)
            image = _clean_amazon_img(image)
            # basic sanity: only allow amazon media hosts
            host = urlparse(image).netloc
            if not host.endswith("amazon.com") and "m.media-amazon.com" not in host and "ssl-images-amazon.com" not in host:
                # still allow m.media-amazon.com, images-na.ssl-images-amazon.com
                pass

        # Rating
        rating = None
        for sel in [
            'i[data-hook="average-star-rating"] .a-icon-alt',
            '.a-icon-star .a-icon-alt',
            '#acrPopover .a-icon-alt',
            '.cr-widget-AverageCustomerReviews .a-icon-alt'
        ]:
            el = soup.select_one(sel)
            if el:
                m = re.search(r"(\d+(\.\d+)?)", el.get_text())
                if m:
                    try:
                        rating = float(m.group(1))
                        break
                    except Exception:
                        pass

        # Rating count
        rating_count = None
        for sel in ['#acrCustomerReviewText', '[data-hook="total-review-count"]', '.a-link-normal .a-size-base']:
            el = soup.select_one(sel)
            if el:
                m = re.search(r"([\d,]+)", el.get_text())
                if m:
                    try:
                        rating_count = int(m.group(1).replace(",", ""))
                        break
                    except Exception:
                        pass

        return {
            "title": title[:200],
            "price": price,
            "image": image,     # direct URL (may 403 when embedded)
            "rating": rating,
            "rating_count": rating_count,
        }
    except requests.RequestException as e:
        print(f"Amazon request error: {e}")
        return None
    except Exception as e:
        print(f"Amazon scraping error: {e}")
        return None