from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, Response, jsonify, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user, login_required,
    current_user, UserMixin
)
from flask_bcrypt import Bcrypt
from xhtml2pdf import pisa
import csv
from io import BytesIO, StringIO
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import os
from datetime import datetime
from urllib.parse import urlparse
from requests.utils import requote_uri
from itsdangerous import URLSafeTimedSerializer
import google.generativeai as genai

# =====================================================================
# App and DB Configuration
# =====================================================================
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "users.db")

app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_PATH}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# Gmail SMTP Configuration
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASS = os.environ.get("GMAIL_PASS")

# Token serializer for password reset
serializer = URLSafeTimedSerializer(app.secret_key)

# Configure Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Login Manager Setup
login_manager = LoginManager()
login_manager.login_view = 'index'
login_manager.login_message = 'Please log in to access this page.'
login_manager.init_app(app)

# Placeholder Image
PLACEHOLDER_IMG = "https://via.placeholder.com/240x240?text=No+Image"

# =====================================================================
# Database Models
# =====================================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    products = db.relationship('Product', backref='user', lazy=True, cascade='all, delete-orphan')


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    url = db.Column(db.String(300), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    target_price = db.Column(db.Float)
    image_url = db.Column(db.String(600))
    platform = db.Column(db.String(50))
    last_checked = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =====================================================================
# User Loader
# =====================================================================
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    return redirect(url_for('index') + '?login=required')


# =====================================================================
# Image Proxy Configuration
# =====================================================================
ALLOWED_IMG_SUFFIXES = (
    "m.media-amazon.com",
    "images-na.ssl-images-amazon.com",
    "images.amazon.com",
    "media-amazon.com",
    "meesho.com",
    "flixcart.com",
    "myntassets.com",
)


def _referer_for_host(host: str) -> str:
    host = host.lower()
    if "amazon" in host:
        return "https://www.amazon.in/"
    if "meesho.com" in host:
        return "https://www.meesho.com/"
    if "flipkart" in host or "flixcart" in host:
        return "https://www.flipkart.com/"
    if "myntassets.com" in host or "myntra.com" in host:
        return "https://www.myntra.com/"
    return ""


def _html_escape(text):
    """Escape HTML special characters"""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


IMG_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


@app.get("/proxy/img")
def proxy_img():
    src = request.args.get("u") or ""
    if not src:
        abort(400)
    p = urlparse(src)
    if p.scheme not in ("http", "https"):
        abort(400, "bad scheme")
    host = p.netloc.lower()
    host_ok = any(host == suf or host.endswith("." + suf) for suf in ALLOWED_IMG_SUFFIXES)
    if not host_ok:
        abort(400, f"host not allowed: {host}")

    headers = IMG_HEADERS.copy()
    ref = _referer_for_host(host)
    if ref:
        headers["Referer"] = ref

    try:
        r = requests.get(src, timeout=10, headers=headers, stream=True)
    except requests.RequestException:
        abort(502)

    ctype = r.headers.get("Content-Type", "")
    if r.status_code != 200 or not ctype.startswith("image/"):
        abort(502)

    return Response(r.content, headers={
        "Content-Type": ctype,
        "Cache-Control": "public, max-age=86400",
    })


def proxied(url: str | None) -> str | None:
    return f"/proxy/img?u={requote_uri(url)}" if url else None


@app.get("/debug/allow")
def debug_allow():
    src = request.args.get("u", "")
    p = urlparse(src)
    host = p.netloc.lower()
    ok = any(host == suf or host.endswith("." + suf) for suf in ALLOWED_IMG_SUFFIXES)
    return jsonify({"host": host, "allowed": ok, "suffixes": ALLOWED_IMG_SUFFIXES})


# =====================================================================
# Email Functions
# =====================================================================
def send_price_alert(to_email, title, product_url, image_url, current_price, target_price):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"PriceGenius Alerts <{GMAIL_USER}>"
        msg["To"] = to_email
        msg["Subject"] = "🎉 Price Drop Alert - Great Deal Found!"

        html = f"""
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;">
    <div style="max-width:600px;margin:20px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,0.1);">
        
        <!-- Header -->
        <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:30px 20px;text-align:center;color:#ffffff;">
            <h1 style="margin:0;font-size:28px;font-weight:700;">🎊 Price Drop Alert!</h1>
            <p style="margin:10px 0 0 0;font-size:14px;opacity:0.9;">Great news! A product you're tracking just got cheaper!</p>
        </div>

        <!-- Product Section -->
        <div style="padding:30px 20px;">
            <!-- Product Image -->
            <div style="text-align:center;margin-bottom:20px;">
                <img src="{_html_escape(image_url)}" alt="Product" style="max-width:100%;height:auto;max-height:300px;border-radius:8px;box-shadow:0 4px 15px rgba(0,0,0,0.1);">
            </div>

            <!-- Product Details -->
            <div style="background:#f8f9fa;padding:20px;border-radius:8px;margin-bottom:20px;border-left:4px solid #667eea;">
                <h2 style="margin:0 0 15px 0;font-size:20px;color:#1a1a1a;font-weight:600;">{_html_escape(title)}</h2>
                
                <table style="width:100%;margin-bottom:15px;">
                    <tr>
                        <td style="padding:10px 0;border-bottom:1px solid #e0e0e0;">
                            <span style="color:#666;font-size:14px;">Current Price:</span>
                        </td>
                        <td style="padding:10px 0;border-bottom:1px solid #e0e0e0;text-align:right;">
                            <span style="font-size:24px;font-weight:700;color:#10b981;">₹{current_price:.2f}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:10px 0;">
                            <span style="color:#666;font-size:14px;">Your Target Price:</span>
                        </td>
                        <td style="padding:10px 0;text-align:right;">
                            <span style="font-size:16px;color:#764ba2;font-weight:600;">₹{target_price:.2f}</span>
                        </td>
                    </tr>
                </table>

                <!-- Savings Badge -->
                <div style="background:#10b981;color:#ffffff;padding:12px;border-radius:6px;text-align:center;font-weight:600;">
                    💰 You Save: ₹{(target_price - current_price):.2f} ({((target_price - current_price)/target_price*100):.1f}% off)
                </div>
            </div>

            <!-- CTA Button -->
            <div style="text-align:center;margin-bottom:20px;">
                <a href="{_html_escape(product_url)}" style="display:inline-block;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;box-shadow:0 4px 15px rgba(102,126,234,0.3);">
                    🛒 Buy Now Before Price Goes Up!
                </a>
            </div>

            <!-- Info Box -->
            <div style="background:#e3f2fd;border-left:4px solid #2196f3;padding:15px;border-radius:6px;margin-bottom:20px;">
                <p style="margin:0;color:#1565c0;font-size:13px;line-height:1.6;">
                    <strong>✓ Limited Time Offer:</strong> This price is temporary and may change at any time. We recommend completing your purchase soon to secure this deal!
                </p>
            </div>
        </div>

        <!-- Footer -->
        <div style="background:#f8f9fa;padding:20px;border-top:1px solid #e0e0e0;text-align:center;font-size:12px;color:#666;">
            <p style="margin:0 0 10px 0;">
                <strong>PriceGenius</strong> - Your Smart Price Tracking Assistant
            </p>
            <p style="margin:0 0 10px 0;">
                Never miss a deal again! Track prices across Amazon, Flipkart, Myntra & more.
            </p>
            <p style="margin:0;color:#999;font-size:11px;">
                This is an automated alert. Please do not reply to this email.<br>
                © 2026 PriceGenius. All rights reserved.
            </p>
        </div>
    </div>
</body>
</html>
"""

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)

    except Exception as e:
        print("Email error:", e)


def send_password_reset_email(to_email: str, reset_url: str):
    """Send password reset email with reset link"""
    if not (GMAIL_USER and GMAIL_PASS and to_email):
        print("Email not sent: Gmail creds or recipient missing")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"PriceGenius <{GMAIL_USER}>"
        msg['To'] = to_email
        msg['Subject'] = "🔐 PriceGenius - Password Reset Request"

        plain = f"""Hello,

You requested to reset your password for PriceGenius.

Click the link below to reset your password:
{reset_url}

This link will expire in 1 hour.

If you didn't request this, please ignore this email.

- PriceGenius Team"""

        html = f"""
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;">
    <div style="max-width:600px;margin:20px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,0.1);">
        
        <!-- Header -->
        <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:30px 20px;text-align:center;color:#ffffff;">
            <h1 style="margin:0;font-size:28px;font-weight:700;">🔐 Password Reset</h1>
            <p style="margin:10px 0 0 0;font-size:14px;opacity:0.9;">Secure Your Account Access</p>
        </div>

        <!-- Content -->
        <div style="padding:30px 20px;">
            <p style="color:#333;font-size:15px;line-height:1.6;margin:0 0 20px 0;">Hello,</p>
            
            <p style="color:#333;font-size:15px;line-height:1.6;margin:0 0 20px 0;">You requested to reset your password for <strong>PriceGenius</strong>. Click the button below to set a new password:</p>

            <!-- CTA Button -->
            <div style="text-align:center;margin:30px 0;">
                <a href="{_html_escape(reset_url)}" style="display:inline-block;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;box-shadow:0 4px 15px rgba(102,126,234,0.3);">
                    🔗 Reset Your Password
                </a>
            </div>

            <!-- Fallback Link -->
            <p style="color:#666;font-size:13px;margin:20px 0;padding:15px;background:#f5f5f5;border-radius:6px;border-left:4px solid #667eea;">
                <strong>Or copy this link:</strong><br>
                <code style="word-break:break-all;color:#1565c0;font-family:monospace;">{_html_escape(reset_url)}</code>
            </p>

            <!-- Important Info -->
            <div style="background:#fff3cd;border-left:4px solid #ffc107;padding:15px;border-radius:6px;margin:20px 0;">
                <p style="margin:0;color:#856404;font-size:13px;">
                    <strong>⏰ This link expires in 1 hour.</strong> If you didn't request this, please ignore this email and your password will remain unchanged.
                </p>
            </div>

            <!-- Security Note -->
            <p style="color:#999;font-size:12px;margin:20px 0 0 0;line-height:1.6;">
                ✓ Never share this link with anyone<br>
                ✓ PriceGenius will never ask for your password via email<br>
                ✓ This is an automated security message
            </p>
        </div>

        <!-- Footer -->
        <div style="background:#f8f9fa;padding:20px;border-top:1px solid #e0e0e0;text-align:center;font-size:12px;color:#666;">
            <p style="margin:0 0 10px 0;">
                <strong>PriceGenius</strong> - Smart Price Tracking
            </p>
            <p style="margin:0;color:#999;font-size:11px;">
                © 2026 PriceGenius. All rights reserved.
            </p>
        </div>
    </div>
</body>
</html>
"""

        msg.attach(MIMEText(plain, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)

        print(f"Password reset email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


# =====================================================================
# Password Reset Token Functions
# =====================================================================
def generate_reset_token(email):
    """Generate a secure password reset token"""
    return serializer.dumps(email, salt='password-reset-salt')


def verify_reset_token(token, expiration=3600):
    """Verify token and return email if valid (default: 1 hour expiration)"""
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=expiration)
        return email
    except Exception:
        return None


# =====================================================================
# Scraping Helper
# =====================================================================
def scrape_product_details(url):
    """Detect platform and scrape product details"""
    try:
        u = url.lower()

        if 'amazon' in u:
            try:
                from scrapers.amazon import get_amazon_product_details
                res = get_amazon_product_details(url)
            except Exception as e:
                print("Amazon scraper import/run error:", e)
                res = None
            if res:
                return {
                    "title": res.get('title', 'Amazon Product'),
                    "price": res.get('price', 0.0) or 0.0,
                    "image_url": proxied(res.get('image')) if res.get('image') else PLACEHOLDER_IMG,
                    "platform": "Amazon"
                }

        elif 'flipkart' in u:
            try:
                from scrapers.flipkart import get_flipkart_product_details
                res = get_flipkart_product_details(url)
            except Exception as e:
                print("Flipkart scraper import/run error:", e)
                res = None
            if res:
                return {
                    "title": res.get('title', 'Flipkart Product'),
                    "price": res.get('price', 0.0) or 0.0,
                    "image_url": proxied(res.get('image')) if res.get('image') else PLACEHOLDER_IMG,
                    "platform": "Flipkart"
                }

        elif 'myntra' in u:
            try:
                from scrapers.myntra import get_myntra_product_details
                res = get_myntra_product_details(url)
            except Exception as e:
                print("Myntra scraper import/run error:", e)
                res = None
            if res:
                return {
                    "title": res.get('title', 'Myntra Product'),
                    "price": res.get('price', 0.0) or 0.0,
                    "image_url": proxied(res.get('image')) if res.get('image') else PLACEHOLDER_IMG,
                    "platform": "Myntra"
                }

        elif 'meesho.com' in u:
            try:
                from scrapers.meesho import get_meesho_product_details
                res = get_meesho_product_details(url)
            except Exception as e:
                print("Meesho scraper import/run error:", e)
                res = None
            if res:
                return {
                    "title": res.get('title', 'Meesho Product'),
                    "price": res.get('price', 0.0) or 0.0,
                    "image_url": proxied(res.get('image')) if res.get('image') else PLACEHOLDER_IMG,
                    "platform": "Meesho"
                }

        return {"title": "Manual Entry", "price": 0.0, "image_url": PLACEHOLDER_IMG, "platform": "Unknown"}
    except Exception as e:
        print(f"Scraping error for {url}: {e}")
        return {"title": "Product (Failed to fetch details)", "price": 0.0, "image_url": PLACEHOLDER_IMG, "platform": "Unknown"}


# =====================================================================
# Routes
# =====================================================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['POST'])
def login():
    try:
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not email or not password:
            return jsonify({'success': False, 'message': 'Please provide both email and password.'}), 400

        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return jsonify({'success': True, 'redirect': next_page if next_page else url_for('dashboard')})
        else:
            return jsonify({'success': False, 'message': 'Invalid email or password.'}), 401
    except Exception as e:
        app.logger.error(f'Login error: {str(e)}')
        return jsonify({'success': False, 'message': 'An internal error occurred. Please try again.'}), 500


@app.route('/register', methods=['POST'])
def register():
    try:
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not email or not password:
            return jsonify({'success': False, 'message': 'Please provide both email and password.'}), 400
        if password != confirm_password:
            return jsonify({'success': False, 'message': 'Passwords do not match.'}), 400
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters long.'}), 400

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return jsonify({'success': False, 'message': 'Email already registered. Please use a different email.'}), 409

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(email=email, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()

        login_user(new_user, remember=True)
        return jsonify({'success': True, 'message': 'Account created successfully!', 'redirect': url_for('dashboard')})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Registration error: {str(e)}')
        return jsonify({'success': False, 'message': 'An error occurred while creating your account. Please try again.'}), 500


@app.post('/forgot-password')
def forgot_password():
    """Handle forgot password request"""
    email = request.form.get('email', '').strip()

    if not email:
        return jsonify({'success': False, 'message': 'Email is required'})

    user = User.query.filter_by(email=email).first()

    # Always return success to prevent email enumeration
    if user:
        token = generate_reset_token(email)
        reset_url = url_for('reset_password', token=token, _external=True)
        send_password_reset_email(email, reset_url)

    return jsonify({
        'success': True,
        'message': 'If an account exists with that email, a password reset link has been sent.'
    })


@app.get('/reset-password/<token>')
def reset_password(token):
    """Display password reset form"""
    email = verify_reset_token(token)
    if not email:
        flash('Invalid or expired reset link. Please request a new one.', 'danger')
        return redirect(url_for('index'))

    return render_template('reset_password.html', token=token, email=email)


@app.post('/reset-password/<token>')
def reset_password_post(token):
    """Process password reset"""
    email = verify_reset_token(token)

    if not email:
        return jsonify({'success': False, 'message': 'Invalid or expired reset link'})

    new_password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()

    if not new_password or not confirm_password:
        return jsonify({'success': False, 'message': 'All fields are required'})

    if new_password != confirm_password:
        return jsonify({'success': False, 'message': 'Passwords do not match'})

    if len(new_password) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters'})

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'success': False, 'message': 'User not found'})

    # Update password
    user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Password successfully reset! You can now login with your new password.',
        'redirect': url_for('index')
    })


@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    if request.method == 'POST':
        url = request.form.get('url', '').strip()
        try:
            target_price = float(request.form.get('target_price', 0))
        except Exception:
            target_price = 0.0

        if not url or target_price <= 0:
            flash('Please provide a valid URL and target price.', 'error')
            return redirect(url_for('dashboard'))

        existing_product = Product.query.filter_by(user_id=current_user.id, url=url).first()
        if existing_product:
            flash("This product is already being tracked.", 'warning')
            return redirect(url_for('dashboard'))

        product_info = scrape_product_details(url)
        new_product = Product(
            user_id=current_user.id,
            url=url,
            title=product_info['title'],
            current_price=product_info['price'],
            target_price=target_price,
            image_url=product_info['image_url'],
            platform=product_info['platform']
        )
        db.session.add(new_product)
        db.session.commit()

        if new_product.current_price and new_product.target_price and new_product.current_price <= new_product.target_price:
            try:
                send_price_alert(
                    to_email=current_user.email,
                    title=new_product.title,
                    product_url=new_product.url,
                    image_url=new_product.image_url or "",
                    current_price=new_product.current_price,
                    target_price=new_product.target_price
                )
            except Exception as e:
                app.logger.warning(f"Alert send failed on add: {e}")

        flash(f"Product '{product_info['title']}' added successfully!", 'success')
        return redirect(url_for('dashboard'))

    products = Product.query.filter_by(user_id=current_user.id).order_by(Product.created_at.desc()).all()
    stats = {
        'total_products': len(products),
        'targets_reached': sum(1 for p in products if p.current_price and p.target_price and p.current_price <= p.target_price),
        'potential_savings': sum((p.target_price - p.current_price) for p in products if p.current_price and p.target_price and p.current_price < p.target_price),
        'avg_savings_rate': 0
    }
    return render_template("dashboard.html", user=current_user, products=products, stats=stats)


@app.route('/search')
@login_required
def search():
    query = request.args.get('query', '').strip()
    if not query:
        flash("Please enter a search term.", 'warning')
        return redirect(url_for('dashboard'))
    results = []
    return render_template("search_results.html", query=query, results=results)


@app.route("/delete/<int:product_id>")
@login_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    if product.user_id != current_user.id:
        flash("You are not authorized to delete this product.", 'error')
        return redirect(url_for("dashboard"))
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted successfully.", 'success')
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", 'success')
    return redirect(url_for("index"))


@app.route('/api/check-auth')
def check_auth():
    return jsonify({
        'authenticated': current_user.is_authenticated,
        'user_id': current_user.id if current_user.is_authenticated else None
    })


@app.route('/export')
@login_required
def export_products():
    return "Export feature coming soon!"


@app.route('/export_pdf')
@login_required
def export_pdf():
    return "PDF export feature coming soon!"


@app.route('/update_price/<int:product_id>')
@login_required
def update_price(product_id):
    product = Product.query.get_or_404(product_id)
    if product.user_id != current_user.id:
        flash("Unauthorized access.", 'error')
        return redirect(url_for('dashboard'))

    try:
        u = product.url.lower()
        if 'amazon' in u:
            from scrapers.amazon import get_amazon_product_details
            info = get_amazon_product_details(product.url)
        elif 'flipkart' in u:
            from scrapers.flipkart import get_flipkart_product_details
            info = get_flipkart_product_details(product.url)
        elif 'myntra' in u:
            from scrapers.myntra import get_myntra_product_details
            info = get_myntra_product_details(product.url)
        elif 'meesho.com' in u:
            from scrapers.meesho import get_meesho_product_details
            info = get_meesho_product_details(product.url)
        else:
            flash('Unsupported platform.', 'error')
            return redirect(url_for('dashboard'))

        if info and (info.get('price') is not None):
            product.current_price = float(info['price'])
            product.last_checked = datetime.utcnow()
            if info.get('image'):
                product.image_url = proxied(info['image'])
            db.session.commit()

            if product.target_price and product.current_price and product.current_price <= product.target_price:
                try:
                    send_price_alert(
                        to_email=current_user.email,
                        title=product.title,
                        product_url=product.url,
                        image_url=product.image_url or "",
                        current_price=product.current_price,
                        target_price=product.target_price
                    )
                except Exception as e:
                    app.logger.warning(f"Alert send failed on update: {e}")

            flash(f'Price updated: ₹{product.current_price:,.0f}', 'success')
        else:
            flash('Unable to fetch current price.', 'warning')

    except ImportError as e:
        flash(f'Scraper import error: {e}', 'error')
    except Exception as e:
        flash(f'Error updating price: {e}', 'error')

    return redirect(url_for('dashboard'))


@app.route('/about')
def about():
    return render_template('about.html')


@app.post('/api/chat')
def chat():
    """Handle chatbot messages using Gemini API"""
    try:
        user_message = request.json.get('message', '').strip()

        if not user_message:
            return jsonify({'success': False, 'message': 'Message is required'})

        if not GEMINI_API_KEY:
            return jsonify({
                'success': False,
                'message': 'Gemini API key not configured. Please contact support.'
            })

        # Create Gemini model
        model = genai.GenerativeModel('gemini-2.5-flash')

        # System prompt for PriceGenius chatbot
        system_prompt = """You are a helpful AI assistant for PriceGenius, a smart price tracking platform.

        Key features of PriceGenius:
        - Track prices across Amazon, Flipkart, and Myntra
        - Real-time price monitoring and alerts
        - Email notifications when prices drop
        - Price history and analytics
        - Support for multiple products

        Answer user questions about:
        - How to use the platform
        - Pricing plans (Free, Pro, Enterprise)
        - Features and capabilities
        - General shopping advice
        - Price tracking tips

        Be helpful, friendly, and concise. If asked about specific product prices,
        explain that users need to add products to track them."""

        # Generate response
        full_prompt = f"{system_prompt}\n\nUser: {user_message}\n\nAssistant:"
        response = model.generate_content(full_prompt)

        return jsonify({
            'success': True,
            'message': response.text
        })

    except Exception as e:
        print(f"Chatbot error: {e}")
        return jsonify({
            'success': False,
            'message': 'Sorry, I encountered an error. Please try again.'
        })


# =====================================================================
# Main
# =====================================================================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    print("DB:", app.config['SQLALCHEMY_DATABASE_URI'])
    if GMAIL_USER and GMAIL_PASS:
        print("Gmail SMTP ready")
    else:
        print("Gmail not configured (set GMAIL_USER and GMAIL_PASS App Password)")

    app.run(host="127.0.0.1", port=5000, debug=True)
