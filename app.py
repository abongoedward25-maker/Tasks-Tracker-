# ============================================
# FRESHIPPO PREMIUM v5.2 - FINAL STABLE CODE
# ============================================

import os
from datetime import timedelta, datetime
from decimal import Decimal
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, jsonify, make_response, redirect
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, decode_token,
    jwt_required, get_jwt_identity
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, inspect
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
CORS(app)

app.config['SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'change-this-secret')

# === DATABASE URL FIX (Render / PostgreSQL / SQLite fallback) ===
db_url = os.getenv('DATABASE_URL', '')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
if db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
if not db_url:
    db_url = "sqlite:///freshippo.db"

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'change-this-secret')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=7)

# Allow JWT from both headers (API) and cookies (browser pages)
app.config['JWT_TOKEN_LOCATION'] = ['headers', 'cookies']
app.config['JWT_COOKIE_CSRF_PROTECT'] = False

db = SQLAlchemy(app)
jwt = JWTManager(app)

STAGE_TARGETS = {1: 0, 2: 50, 3: 200, 4: 500, 5: 1000}


# ============================================
# MODELS
# ============================================
class User(db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), default='')
    language = db.Column(db.String(10), default='en')
    is_admin = db.Column(db.Boolean, default=False)
    balance = db.Column(db.Numeric(10, 2), default=0.00)
    total_withdrawn = db.Column(db.Numeric(10, 2), default=0.00)
    current_stage = db.Column(db.Integer, default=1)
    stage_status = db.Column(db.String(20), default='pending')
    stage_updated_at = db.Column(db.DateTime, server_default=db.func.now())
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "phone": self.phone,
            "language": self.language,
            "is_admin": self.is_admin,
            "balance": float(self.balance),
            "total_withdrawn": float(self.total_withdrawn),
            "current_stage": self.current_stage,
            "stage_status": self.stage_status,
        }


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    price = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, default=0)
    category = db.Column(db.String(100), default='General')
    image_url = db.Column(db.String(500), default='')

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "price": float(self.price),
            "stock": self.stock,
            "category": self.category,
            "image_url": self.image_url,
        }


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default='pending')
    address = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default='pending')
    requested_at = db.Column(db.DateTime, server_default=db.func.now())
    approved_at = db.Column(db.DateTime, nullable=True)


# ============================================
# AUTO MIGRATE MISSING COLUMNS
# ============================================
def add_missing_columns():
    """Works on both PostgreSQL and SQLite."""
    with app.app_context():
        db.create_all()
        try:
            inspector = inspect(db.engine)
            existing_columns = {col['name'] for col in inspector.get_columns('user')}

            missing_columns = {
                'balance': 'NUMERIC(10,2) DEFAULT 0.00',
                'total_withdrawn': 'NUMERIC(10,2) DEFAULT 0.00',
                'current_stage': 'INTEGER DEFAULT 1',
                'stage_status': "VARCHAR(20) DEFAULT 'pending'",
                'stage_updated_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
                'language': "VARCHAR(10) DEFAULT 'en'",
            }

            for col_name, col_definition in missing_columns.items():
                if col_name in existing_columns:
                    continue
                db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {col_name} {col_definition}'))
                db.session.commit()
                print(f"Added column: {col_name}")
        except Exception as e:
            db.session.rollback()
            print("Migration warning:", e)


# ============================================
# HELPERS
# ============================================
def get_current_user():
    """Return logged-in User from cookie token, or None."""
    token = request.cookies.get('access_token')
    if not token:
        return None
    try:
        decoded = decode_token(token)
        return User.query.get(int(decoded['sub']))
    except Exception:
        return None


def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user_id = get_jwt_identity()
        user = User.query.get(int(user_id))
        if not user or not user.is_admin:
            return jsonify({"msg": "Admin required"}), 403
        return fn(*args, **kwargs)
    return wrapper


# Run migration after models are defined
add_missing_columns()


# ============================================
# PUBLIC PAGES
# ============================================
@app.route('/')
def homepage():
    return '''<html><head><title>Freshippo API</title></head><body style="font-family:Poppins; text-align:center; padding:50px; background:linear-gradient(135deg,#0f0c29,#302b63,#24243e); color:white">
    <h1 style="font-size:48px">🛒 Freshippo API</h1><p>Status: <b style="color:#22c55e">LIVE</b></p>
    <p><a href="/signup" style="color:#a855f7;font-size:18px">Sign Up</a> | <a href="/loginpage" style="color:#a855f7;font-size:18px">Login</a> | <a href="/dashboard" style="color:#a855f7;font-size:18px">Dashboard</a></p>
    </body></html>'''


@app.route('/health')
def health():
    try:
        db.session.execute(db.text('SELECT 1'))
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        return jsonify({"status": "error", "db": str(e)}), 500


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/signup', methods=['GET', 'POST'])
def signup_page():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        if User.query.filter_by(email=email).first():
            return "Error: Email exists <br><a href='/signup'>Try again</a>"
        user = User(email=email, name=name, phone='')
        user.password_hash = generate_password_hash(password)
        db.session.add(user)
        db.session.commit()
        return f"<h1>Account Created</h1><p>Welcome {name}</p><a href='/loginpage'>Sign In</a>"

    return '''<style>body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}</style><h2>Sign Up</h2><form method='POST' style='max-width:300px;margin:auto;padding:30px;background:rgba(255,255,255,0.05);border-radius:20px'><input name='name' placeholder='Name' required style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><input name='email' type='email' required placeholder='Email' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><input name='password' type='password' required placeholder='Password' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><button style='width:100%;padding:14px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold'>Sign Up</button></form>'''


@app.route('/loginpage', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            token = create_access_token(identity=str(user.id))
            resp = make_response(redirect('/dashboard'))
            resp.set_cookie('access_token', token, httponly=True)
            return resp
        return "Error: Wrong credentials <br><a href='/loginpage'>Try again</a>"

    return '''<style>body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}</style><h2>Login</h2><form method='POST' style='max-width:300px;margin:auto;padding:30px;background:rgba(255,255,255,0.05);border-radius:20px'><input name='email' type='email' required placeholder='Email' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><input name='password' type='password' required placeholder='Password' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><button style='width:100%;padding:14px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold'>Sign In</button></form>'''


@app.route('/logout')
def logout():
    resp = make_response(redirect('/loginpage'))
    resp.set_cookie('access_token', '', expires=0)
    return resp


# ============================================
# API ROUTES
# ============================================
@app.route('/auth/register', methods=['POST'])
def register():
    data = request.get_json()
    if not all([data.get('email'), data.get('password'), data.get('name')]):
        return jsonify({"msg": "email, password, name required"}), 400
    if User.query.filter_by(email=data['email']).first():
        return jsonify({"msg": "Email exists"}), 400
    user = User(email=data['email'], name=data['name'], phone=data.get('phone', ''))
    user.password_hash = generate_password_hash(data['password'])
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=str(user.id))
    return jsonify({"access_token": token, "user": user.to_dict()}), 201


@app.route('/api/products')
def api_products():
    products = Product.query.all()
    return jsonify([p.to_dict() for p in products])


# ============================================
# DASHBOARD
# ============================================
@app.route('/dashboard')
def dashboard():
    user = get_current_user()
    if not user:
        return redirect('/loginpage')

    products = Product.query.all()
    cart_items = CartItem.query.filter_by(user_id=user.id).all()
    added_product_ids = {ci.product_id for ci in cart_items}

    # Cooldown check
    last_withdrawal = (
        Withdrawal.query
        .filter_by(user_id=user.id, status='approved')
        .order_by(Withdrawal.approved_at.desc())
        .first()
    )
    can_withdraw = True
    days_left = 0
    if last_withdrawal:
        days_passed = (datetime.utcnow() - last_withdrawal.approved_at).days
        if days_passed < 10:
            can_withdraw = False
            days_left = 10 - days_passed

    # Stage progress
    if user.current_stage < 5:
        next_target = STAGE_TARGETS[user.current_stage + 1]
        progress = min(100, int((float(user.balance) / next_target) * 100)) if next_target else 0
        next_stage_text = f"Stage {user.current_stage} → {user.current_stage + 1}"
    else:
        progress = 100
        next_target = None
        next_stage_text = "Max Stage"

    # Product cards
    products_html = ""
    if not products:
        products_html = "<p style='text-align:center;color:#555'>No products yet. Add some!</p>"
    else:
        for p in products:
            img_url = p.image_url.strip() if p.image_url else ""
            if img_url:
                img_tag = f"<img src='{img_url}' style='width:100%;height:200px;object-fit:cover;border-radius:15px;margin-bottom:12px' onerror=\"this.src='https://via.placeholder.com/400x200/111/a855f7?text=No+Image'\">"
            else:
                img_tag = "<div style='width:100%;height:200px;background:linear-gradient(135deg,#111,#222);border-radius:15px;margin-bottom:12px;display:flex;align-items:center;justify-content:center;color:#555;font-size:18px'>📦 No Image</div>"

            if p.id in added_product_ids:
                add_btn = "<span style='color:#22c55e;font-weight:bold'>✓ Added (+$0.40)</span>"
            else:
                add_btn = f"<a href='/cart/add/{p.id}' class='btn'>🛒 Add +$0.40</a>"

            products_html += f"""
            <div style="border:1px solid rgba(168,85,247,0.2); padding:20px; margin:20px 0; background:rgba(255,255,255,0.03); backdrop-filter:blur(15px); border-radius:20px">
                {img_tag}
                <h3 style="margin:0 0 10px 0;font-size:22px">{p.name}</h3>
                <p style="margin:0 0 15px 0; color:#aaa;font-size:17px">${p.price} | Stock: {p.stock}</p>
                {add_btn}
            </div>"""

    # Admin links
    admin_html = ""
    if user.is_admin:
        admin_html = f"""
        <p><a href="/admin/add-product" class="btn">+ Add New Product</a></p>
        <p><a href="/admin/stages" class="btn">👑 Approve Stages</a></p>
        <p><a href="/admin/withdrawals" class="btn">💰 Approve Withdrawals</a></p>
        """

    # Withdraw button
    if can_withdraw:
        withdraw_btn = f'<a href="/withdraw" class="btn">💰 Request Withdrawal</a>'
    else:
        withdraw_btn = f'<span style="color:#ffaa00;font-size:16px">⏳ Withdrawal available in {days_left} days</span>'

    css = """
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;800&display=swap');
    body{background:linear-gradient(135deg,#0f0c29 0%,#302b63 50%,#24243e 100%);background-attachment:fixed;color:white;font-family:'Poppins',sans-serif;margin:0;min-height:100vh}
    .watermark{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%) rotate(-15deg);font-size:15vw;color:rgba(168,85,247,0.05);z-index:0;pointer-events:none;white-space:nowrap;font-weight:900}
    .content{position:relative;z-index:1;padding:20px;max-width:900px;margin:auto}
    .box{padding:25px;margin:20px 0;background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border:1px solid rgba(168,85,247,0.3);border-radius:20px;box-shadow:0 8px 32px rgba(0,0,0,0.4)}
    .btn{display:inline-block;padding:14px 28px;margin:8px 5px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;text-decoration:none;border-radius:12px;font-weight:600;font-size:15px;box-shadow:0 4px 20px rgba(168,85,247,0.4)}
    .btn.red{background:linear-gradient(135deg,#ef4444,#dc2626)}
    .btn.green{background:linear-gradient(135deg,#22c55e,#16a34a)}
    .stats{display:flex;gap:20px;margin-top:15px}
    .stat{flex:1;background:rgba(0,0,0,0.4);padding:20px;border-radius:15px;text-align:center;border:1px solid rgba(168,85,247,0.2)}
    .menu-btn{background:rgba(255,255,255,0.1);border:2px solid rgba(168,85,247,0.4);color:white;padding:12px 16px;border-radius:12px;cursor:pointer;font-size:20px;font-weight:700}
    .progress{background:rgba(255,255,255,0.1);border-radius:20px;height:18px;overflow:hidden;margin:10px 0 5px}
    .progress-fill{background:linear-gradient(90deg,#22c55e,#a855f7);height:100%;border-radius:20px;transition:width 0.5s}
    table{width:100%;border-collapse:collapse}
    th,td{padding:12px;text-align:left;border-bottom:1px solid rgba(168,85,247,0.2)}
    """

    wrapper = f"""<style>{css}</style>
<div class="watermark">FRESHIPPO</div>
<div class="content">

<div class="box" style="text-align:right;position:relative">
    <button onclick="document.getElementById('menu').style.display=document.getElementById('menu').style.display==='block'?'none':'block'" class="menu-btn">⋮</button>
    <div id="menu" style="display:none;position:absolute;right:0;top:55px;background:rgba(26,26,46,0.98);backdrop-filter:blur(20px);border:2px solid rgba(168,85,247,0.5);border-radius:15px;min-width:200px;z-index:999">
        <a href="/dashboard" style="display:block;padding:15px 20px;color:white;text-decoration:none;border-bottom:1px solid rgba(168,85,247,0.2)">🏠 Home</a>
        <a href="/settings" style="display:block;padding:15px 20px;color:white;text-decoration:none;border-bottom:1px solid rgba(168,85,247,0.2)">⚙️ Settings</a>
        <a href="/logout" style="display:block;padding:15px 20px;color:#ff6666;text-decoration:none">🚪 Logout</a>
    </div>
</div>

<div class="box" style="text-align:center">
    <p style="color:#aaa;font-size:17px">Admin: {'✅ Active' if user.is_admin else '❌ No Access'}</p>
</div>

<div class="box">
    <h3 style="color:#a855f7;font-size:22px">📦 Products: {len(products)} items</h3>
</div>

<div class="box">
    <h3 style="color:#a855f7;font-size:24px">💰 Wallet</h3>
    <div class="stats">
        <div class="stat"><b style="font-size:28px;color:#a855f7">${user.balance}</b><br>Balance</div>
        <div class="stat"><b style="font-size:28px;color:#22c55e">${user.total_withdrawn}</b><br>Withdrawn</div>
    </div>
    {withdraw_btn}
    <p style="font-size:13px;color:#aaa;margin-top:10px;text-align:center">⚡ $0.40 per product | 10-day cooldown | Phone + Password required</p>
</div>

<div class="box">
    <h3 style="color:#a855f7;font-size:24px">🚀 Stage Progress</h3>
    <p>{next_stage_text} · ${float(user.balance)} {f'of ${next_target}' if next_target else ''}</p>
    <div class="progress"><div class="progress-fill" style="width:{progress}%"></div></div>
</div>

{admin_html}
<hr style="margin:40px 0;border:none;height:2px;background:linear-gradient(90deg,transparent,#a855f7,transparent)">

<h1 style='text-align:center;margin-bottom:30px;font-size:42px'>🛒 Welcome {user.name}!</h1>
<p style='text-align:center;color:#aaa;font-size:17px'>Email: {user.email}</p><hr>
<h2 style='margin-top:30px'>Products in Store:</h2>
{products_html}
</div>"""

    return wrapper


# ============================================
# CART / EARNINGS
# ============================================
@app.route('/cart/add/<int:product_id>')
def add_to_cart(product_id):
    user = get_current_user()
    if not user:
        return redirect('/loginpage')

    product = Product.query.get(product_id)
    if not product:
        return '❌ Product not found <br><a href="/dashboard">Back</a>'
    if product.stock <= 0:
        return '❌ Out of stock <br><a href="/dashboard">Back</a>'

    # Prevent adding the same product twice
    existing = CartItem.query.filter_by(user_id=user.id, product_id=product.id).first()
    if existing:
        return '⚠️ Already added this product <br><a href="/dashboard">Back</a>'

    cart_item = CartItem(user_id=user.id, product_id=product.id)
    db.session.add(cart_item)

    product.stock -= 1
    user.balance += Decimal('0.40')
    db.session.commit()

    return redirect('/dashboard')


# ============================================
# WITHDRAWAL
# ============================================
@app.route('/withdraw', methods=['GET', 'POST'])
def withdraw():
    user = get_current_user()
    if not user:
        return redirect('/loginpage')

    # Cooldown check
    last_withdrawal = (
        Withdrawal.query
        .filter_by(user_id=user.id, status='approved')
        .order_by(Withdrawal.approved_at.desc())
        .first()
    )
    if last_withdrawal:
        days_passed = (datetime.utcnow() - last_withdrawal.approved_at).days
        if days_passed < 10:
            return f'''<style>body{{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}}</style>
            <h1>⏳ Cooldown active</h1>
            <p>Next withdrawal in {10 - days_passed} days</p>
            <a href="/dashboard">Back</a>'''

    if request.method == 'POST':
        try:
            amount = Decimal(request.form.get('amount', '0'))
        except Exception:
            return '❌ Invalid amount <br><a href="/withdraw">Try again</a>'

        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')

        if not check_password_hash(user.password_hash, password):
            return '❌ Wrong password <br><a href="/withdraw">Try again</a>'
        if phone != user.phone:
            return '❌ Phone must match account <br><a href="/withdraw">Try again</a>'
        if amount <= 0 or amount > user.balance:
            return '❌ Invalid amount <br><a href="/withdraw">Try again</a>'

        withdrawal = Withdrawal(user_id=user.id, amount=amount)
        db.session.add(withdrawal)
        user.balance -= amount
        db.session.commit()

        return f'''<style>body{{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}}</style>
        <h1>✅ Request sent!</h1>
        <p>Admin will review ${amount}</p>
        <a href="/dashboard">Back</a>'''

    # Show withdrawal form
    css = """
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');
    body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:'Poppins',sans-serif;min-height:100vh}
    .box{max-width:380px;margin:auto;padding:35px;background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border-radius:20px;border:1px solid rgba(168,85,247,0.4)}
    input{width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white}
    button{width:100%;padding:15px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold;margin-top:10px}
    a{color:#a855f7}
    """

    return f"""<style>{css}</style>
    <div style="text-align:right;padding:20px"><a href="/dashboard" style="padding:10px 20px">🏠 Home</a></div>
    <h2 style="text-align:center;margin:30px 0;font-size:32px">💰 Request Withdrawal</h2>
    <div class="box">
        <p style="text-align:center;font-size:18px">Balance: <b style="color:#a855f7;font-size:24px">${user.balance}</b></p>
        <form method="POST">
            <input name="amount" type="number" step="0.01" placeholder="Amount $" required>
            <input name="phone" type="text" placeholder="Confirm phone: {user.phone}" required>
            <input name="password" type="password" placeholder="Confirm Password" required>
            <button type="submit">Request Withdrawal</button>
        </form>
        <p style="margin-top:15px;font-size:13px;color:#aaa">10-day cooldown applies after approval.</p>
    </div>"""


# ============================================
# SETTINGS
# ============================================
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user = get_current_user()
    if not user:
        return redirect('/loginpage')

    if request.method == 'POST':
        user.phone = request.form.get('phone', user.phone).strip()
        user.language = request.form.get('language', user.language)
        new_password = request.form.get('new_password', '')
        if new_password:
            user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        return redirect('/settings?updated=1')

    updated = request.args.get('updated')

    css = """
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');
    body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:'Poppins',sans-serif;min-height:100vh}
    .box{max-width:400px;margin:50px auto;padding:35px;background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border-radius:20px;border:1px solid rgba(168,85,247,0.4)}
    input,select{width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white}
    button{width:100%;padding:15px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold;margin-top:10px}
    a{color:#a855f7}
    """

    return f"""<style>{css}</style>
    <div style="text-align:right;padding:20px"><a href="/dashboard">🏠 Home</a></div>
    <h2 style="text-align:center;margin:30px 0;font-size:32px">⚙️ Settings</h2>
    {'<p style="text-align:center;color:#22c55e">✅ Saved</p>' if updated else ''}
    <div class="box">
        <form method="POST">
            <input name="phone" value="{user.phone}" placeholder="Phone Number">
            <select name="language">
                <option value="en" {'selected' if user.language == 'en' else ''}>English</option>
                <option value="ar" {'selected' if user.language == 'ar' else ''}>العربية</option>
                <option value="fr" {'selected' if user.language == 'fr' else ''}>Français</option>
            </select>
            <input name="new_password" type="password" placeholder="New Password (optional)">
            <button type="submit">Save Changes</button>
        </form>
    </div>"""


# ============================================
# ADMIN ROUTES
# ============================================
def is_admin_user():
    user = get_current_user()
    return user if user and user.is_admin else None


@app.route('/admin/add-product', methods=['GET', 'POST'])
def add_product():
    admin = is_admin_user()
    if not admin:
        return redirect('/loginpage')

    if request.method == 'POST':
        name = request.form.get('name')
        try:
            price = float(request.form.get('price'))
            stock = int(request.form.get('stock'))
        except Exception:
            return '❌ Invalid price/stock <br><a href="/admin/add-product">Try again</a>'

        image_url = request.form.get('image_url', '')
        desc = request.form.get('desc', '')

        new_product = Product(name=name, price=price, stock=stock, description=desc, image_url=image_url)
        db.session.add(new_product)
        db.session.commit()
        return redirect('/dashboard')

    css = """
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');
    body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:'Poppins',sans-serif}
    form{max-width:400px;margin:50px auto;padding:30px;background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border-radius:20px;border:1px solid rgba(168,85,247,0.4)}
    input,textarea{width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white}
    button{width:100%;padding:14px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold}
    a{color:#a855f7}
    """

    return f"""<style>{css}</style>
    <form method="post">
        <h2>Add New Product</h2>
        <input name="name" placeholder="Product Name" required>
        <input name="price" type="number" step="0.01" placeholder="Price $" required>
        <input name="stock" type="number" placeholder="Stock Quantity" required>
        <input name="image_url" placeholder="Image URL">
        <textarea name="desc" placeholder="Description" rows="3"></textarea>
        <button type="submit">Add Product</button>
        <br><br><a href="/dashboard">← Back to Dashboard</a>
    </form>"""


@app.route('/admin/stages')
def admin_stages():
    admin = is_admin_user()
    if not admin:
        return redirect('/loginpage')

    users = User.query.filter_by(is_admin=False).all()
    rows = ""
    for u in users:
        if u.current_stage < 5:
            next_target = STAGE_TARGETS[u.current_stage + 1]
            progress = min(100, int((float(u.balance) / next_target) * 100)) if next_target else 0
            eligible = float(u.balance) >= next_target
            action = f'<a href="/admin/approve-stage/{u.id}" class="btn green">Approve → Stage {u.current_stage + 1}</a>' if eligible else '<span style="color:#888">Not eligible yet</span>'
        else:
            next_target = None
            progress = 100
            action = '<span style="color:#22c55e">✅ Max Stage</span>'

        rows += f"""
        <tr>
            <td>{u.id}</td>
            <td>{u.name}</td>
            <td>{u.email}</td>
            <td>${float(u.balance)}</td>
            <td>Stage {u.current_stage} / 5</td>
            <td>{'Max' if next_target is None else f'${next_target}'} ({progress}%)</td>
            <td>{action}</td>
        </tr>"""

    css = """
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');
    body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:'Poppins',sans-serif;padding:30px}
    table{background:rgba(255,255,255,0.05);border-radius:15px;padding:20px}
    th{color:#a855f7}
    .btn{display:inline-block;padding:8px 14px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;text-decoration:none;border-radius:8px;font-size:14px}
    .btn.green{background:linear-gradient(135deg,#22c55e,#16a34a)}
    a{color:#a855f7}
    """

    return f"""<style>{css}</style>
    <a href="/dashboard">🏠 Home</a>
    <h1>👑 Approve Stages</h1>
    <table>
        <tr><th>ID</th><th>Name</th><th>Email</th><th>Balance</th><th>Stage</th><th>Next Target</th><th>Action</th></tr>
        {rows}
    </table>"""


@app.route('/admin/approve-stage/<int:user_id>')
def approve_stage(user_id):
    admin = is_admin_user()
    if not admin:
        return redirect('/loginpage')

    target_user = User.query.get_or_404(user_id)
    if target_user.current_stage < 5:
        next_target = STAGE_TARGETS[target_user.current_stage + 1]
        if float(target_user.balance) >= next_target:
            target_user.current_stage += 1
            target_user.stage_status = 'approved'
            target_user.stage_updated_at = datetime.utcnow()
            db.session.commit()

    return redirect('/admin/stages')


@app.route('/admin/withdrawals')
def admin_withdrawals():
    admin = is_admin_user()
    if not admin:
        return redirect('/loginpage')

    withdrawals = Withdrawal.query.filter_by(status='pending').order_by(Withdrawal.requested_at.desc()).all()
    rows = ""
    for w in withdrawals:
        user = User.query.get(w.user_id)
        rows += f"""
        <tr>
            <td>{w.id}</td>
            <td>{user.name if user else 'Unknown'}</td>
            <td>{user.email if user else 'Unknown'}</td>
            <td>${float(w.amount)}</td>
            <td>{w.requested_at}</td>
            <td>
                <a href="/admin/approve-withdrawal/{w.id}" class="btn green">✅ Approve</a>
                <a href="/admin/reject-withdrawal/{w.id}" class="btn red">❌ Reject</a>
            </td>
        </tr>"""

    css = """
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');
    body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:'Poppins',sans-serif;padding:30px}
    table{background:rgba(255,255,255,0.05);border-radius:15px;padding:20px}
    th{color:#a855f7}
    .btn{display:inline-block;padding:8px 14px;color:white;text-decoration:none;border-radius:8px;font-size:14px}
    .btn.green{background:linear-gradient(135deg,#22c55e,#16a34a)}
    .btn.red{background:linear-gradient(135deg,#ef4444,#dc2626)}
    a{color:#a855f7}
    """

    return f"""<style>{css}</style>
    <a href="/dashboard">🏠 Home</a>
    <h1>💰 Approve Withdrawals</h1>
    <table>
        <tr><th>ID</th><th>Name</th><th>Email</th><th>Amount</th><th>Requested</th><th>Action</th></tr>
        {rows}
    </table>"""


@app.route('/admin/approve-withdrawal/<int:withdrawal_id>')
def approve_withdrawal(withdrawal_id):
    admin = is_admin_user()
    if not admin:
        return redirect('/loginpage')

    withdrawal = Withdrawal.query.get_or_404(withdrawal_id)
    if withdrawal.status == 'pending':
        user = User.query.get(withdrawal.user_id)
        if user:
            user.total_withdrawn += withdrawal.amount
        withdrawal.status = 'approved'
        withdrawal.approved_at = datetime.utcnow()
        db.session.commit()

    return redirect('/admin/withdrawals')


@app.route('/admin/reject-withdrawal/<int:withdrawal_id>')
def reject_withdrawal(withdrawal_id):
    admin = is_admin_user()
    if not admin:
        return redirect('/loginpage')

    withdrawal = Withdrawal.query.get_or_404(withdrawal_id)
    if withdrawal.status == 'pending':
        # Return the money to the user
        user = User.query.get(withdrawal.user_id)
        if user:
            user.balance += withdrawal.amount
        withdrawal.status = 'rejected'
        db.session.commit()

    return redirect('/admin/withdrawals')


# ============================================
# RUN
# ============================================
if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1', 'yes']
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
