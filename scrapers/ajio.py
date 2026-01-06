from bs4 import BeautifulSoup
import requests
import time
import re
import json

def get_ajio_product_details(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive"
    }
    
    try:
        time.sleep(2)
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        
        # Title selectors for AJIO
        title = None
        title_selectors = [
            '.product-title',
            '.item-title h1',
            '.ajio-product-name',
            'h1[data-automation-id="productTitle"]',
            '.prod-title h1'
        ]
        
        for selector in title_selectors:
            title_elem = soup.select_one(selector)
            if title_elem:
                title = title_elem.get_text().strip()
                break
        
        if not title:
            return None
        
        # Price selectors for AJIO
        price = None
        price_selectors = [
            '.price-current',
            '.final-price .amount',
            '.price-display',
            '[data-automation-id="productPrice"]',
            '.prod-sp'
        ]
        
        for selector in price_selectors:
            price_elem = soup.select_one(selector)
            if price_elem:
                price_text = price_elem.get_text().strip()
                price_text = re.sub(r'[^\d]', '', price_text.replace('₹', '').replace(',', ''))
                try:
                    price = float(price_text)
                    break
                except ValueError:
                    continue
        
        # Image selectors for AJIO
        image = None
        image_selectors = [
            '.rilrtl-lazy-img',
            '.product-image img',
            '.img-responsive',
            '[data-automation-id="productImage"]',
            '.prod-img img'
        ]
        
        for selector in image_selectors:
            image_elem = soup.select_one(selector)
            if image_elem:
                image = (image_elem.get('src') or 
                        image_elem.get('data-src') or 
                        image_elem.get('data-original'))
                if image and 'http' in image:
                    break
        
        # Rating selectors for AJIO
        rating = None
        rating_selectors = [
            '.rating-value',
            '.prod-rating .rating',
            '[data-automation-id="rating"]'
        ]
        
        for selector in rating_selectors:
            rating_elem = soup.select_one(selector)
            if rating_elem:
                rating_text = rating_elem.get_text()
                rating_match = re.search(r'(\d+\.?\d*)', rating_text)
                if rating_match:
                    try:
                        rating = float(rating_match.group(1))
                        break
                    except ValueError:
                        continue
        
        # Rating count for AJIO
        rating_count = None
        count_selectors = [
            '.rating-count',
            '.prod-rating .count',
            '[data-automation-id="ratingCount"]'
        ]
        
        for selector in count_selectors:
            count_elem = soup.select_one(selector)
            if count_elem:
                count_text = count_elem.get_text()
                count_match = re.search(r'([\d,]+)', count_text.replace(',', ''))
                if count_match:
                    try:
                        rating_count = int(count_match.group(1).replace(',', ''))
                        break
                    except ValueError:
                        continue
        
        return {
            'title': title[:200],
            'price': price,
            'image': image,
            'rating': rating,
            'rating_count': rating_count
        }
        
    except Exception as e:
        print(f"AJIO scraping error: {e}")
        return None
