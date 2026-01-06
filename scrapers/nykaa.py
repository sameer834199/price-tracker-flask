from bs4 import BeautifulSoup
import requests
import time
import re

def get_nykaa_product_details(url):
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
        
        # Title selectors for Nykaa
        title = None
        title_selectors = [
            'h1.product-title',
            '.product-name h1',
            '.pdp-product-name',
            'h1[data-testid="pdpProductName"]'
        ]
        
        for selector in title_selectors:
            title_elem = soup.select_one(selector)
            if title_elem:
                title = title_elem.get_text().strip()
                break
        
        if not title:
            return None
        
        # Price selectors for Nykaa
        price = None
        price_selectors = [
            '.final-price',
            '.product-price-final',
            '.price-final .amount',
            '[data-testid="pdpPrice"]'
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
        
        # Image selectors for Nykaa
        image = None
        image_selectors = [
            '.product-image-main img',
            '.product-gallery img',
            '.pdp-image img',
            '[data-testid="pdpImage"]'
        ]
        
        for selector in image_selectors:
            image_elem = soup.select_one(selector)
            if image_elem:
                image = image_elem.get('src') or image_elem.get('data-src')
                if image and image.startswith('http'):
                    break
        
        # Rating selectors for Nykaa
        rating = None
        rating_selectors = [
            '.rating-value',
            '.product-rating .rating',
            '[data-testid="pdpRating"]'
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
        
        # Rating count for Nykaa
        rating_count = None
        count_selectors = [
            '.rating-count',
            '.reviews-count',
            '[data-testid="pdpReviewCount"]'
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
        print(f"Nykaa scraping error: {e}")
        return None
