# ============================================
# FRESHIPPO PREMIUM v7.0 - CLEAN NO SLEEP
# ============================================

import os
import sys
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
sys.stdout.reconfigure(encoding='utf-8')

app = Flask(__name__)
CORS(app)

# === DATABASE URL FIX FOR RENDER + PSYCOPG3 ===
db_url = os.getenv('DATABASE_URL', '')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
if db_url.startswith("postgresql://") and "+psycopg" not in db_url:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
if not db_url:
    db_url = "sqlite:///freshippo.db"

app.config['SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'freshippo-super-secret-key-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'freshippo-super-secret-key-2026')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=7)
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

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    price = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, default=0)
    category = db.Column(db.String(100), default='General')
    image_url = db.Column(db.String(500), default='')

class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)

class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default='pending')
    requested_at = db.Column(db.DateTime, server_default=db.func.now())
    approved_at = db.Column(db.DateTime, nullable=True)

class StageRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    from_stage = db.Column(db.Integer, nullable=False)
    to_stage = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending')
    requested_at = db.Column(db.DateTime, server_default=db.func.now())

# ============================================
# AUTO MIGRATE
# ============================================
with app.app_context():
    db.create_all()
    try:
        inspector = inspect(db.engine)
        existing_columns = {col['name'] for col in inspector.get_columns('user')}
        missing_columns = {
            'balance': 'NUMERIC(10,2) DEFAULT 0.00', 'total_withdrawn': 'NUMERIC(10,2) DEFAULT 0.00',
            'current_stage': 'INTEGER DEFAULT 1', 'stage_status': "VARCHAR(20) DEFAULT 'pending'",
            'stage_updated_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP', 'language': "VARCHAR(10) DEFAULT 'en'",
        }
        for col_name, col_definition in missing_columns.items():
            if col_name not in existing_columns:
                db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {col_name} {col_definition}'))
                db.session.commit()
    except: db.session.rollback()

# ============================================
# HELPERS
# ============================================
def get_current_user():
    token = request.cookies.get('access_token')
    if not token: return None
    try: return User.query.get(int(decode_token(token)['sub']))
    except: return None

# ============================================
# PUBLIC PAGES
# ============================================
@app.route('/')
def homepage(): return '<h1 style="text-align:center;color:white;background:#0f0c29;padding:50px">🛒 Freshippo API v7.0 LIVE</h1><p style="text-align:center"><a href="/loginpage">Login</a> | <a href="/dashboard">Dashboard</a></p>'

@app.route('/health')
def health(): 
    try: db.session.execute(text('SELECT 1')); return jsonify({"status": "healthy"}), 200
    except Exception as e: return jsonify({"status": "error"}), 500

@app.route('/ping') # ANTI-SLEEP FOR CRON-JOB
def ping(): return "pong", 200

@app.route('/signup', methods=['GET', 'POST'])
def signup_page():
    if request.method == 'POST':
        name, email, password = request.form.get('name'), request.form.get('email'), request.form.get('password')
        if User.query.filter_by(email=email).first(): return "Email exists <a href='/signup'>Back</a>"
        user = User(email=email, name=name); user.password_hash = generate_password_hash(password)
        db.session.add(user); db.session.commit(); return redirect('/loginpage')
    return '''<style>body{background:#0f0c29;color:white;font-family:Poppins}</style><form method='POST' style='max-width:300px;margin:50px auto;padding:30px;background:rgba(255,255,255,0.05);border-radius:20px'><h2>Sign Up</h2><input name='name' placeholder='Name' required style='width:100%;padding:12px;margin:8px 0;border-radius:10px'><input name='email' type='email' required placeholder='Email' style='width:100%;padding:12px;margin:8px 0;border-radius:10px'><input name='password' type='password' required placeholder='Password' style='width:100%;padding:12px;margin:8px 0;border-radius:10px'><button style='width:100%;padding:14px;background:#a855f7;color:white;border:none;border-radius:10px'>Sign Up</button></form>'''

@app.route('/loginpage', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        email, password = request.form.get('email'), request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            token = create_access_token(identity=str(user.id))
            resp = make_response(redirect('/dashboard')); resp.set_cookie('access_token', token, httponly=True); return resp
        return "Wrong credentials <a href='/loginpage'>Back</a>"
    return '''<style>body{background:#0f0c29;color:white;font-family:Poppins}</style><form method='POST' style='max-width:300px;margin:50px auto;padding:30px;background:rgba(255,255,255,0.05);border-radius:20px'><h2>Login</h2><input name='email' type='email' required placeholder='Email' style='width:100%;padding:12px;margin:8px 0;border-radius:10px'><input name='password' type='password' required placeholder='Password' style='width:100%;padding:12px;margin:8px 0;border-radius:10px'><button style='width:100%;padding:14px;background:#a855f7;color:white;border:none;border-radius:10px'>Sign In</button></form>'''

@app.route('/logout')
def logout(): resp = make_response(redirect('/loginpage')); resp.set_cookie('access_token', '', expires=0); return resp

# ============================================
# DASHBOARD + CART
# ============================================
@app.route('/dashboard')
def dashboard():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    products = Product.query.all()
    admin_btn = '<a href="/admin/dashboard">👑 Admin</a>' if user.is_admin else ''
    products_html = "".join([f"<div><h3>{p.name}</h3><p>${p.price}</p><a href=/cart/add/{p.id}>Add +$0.40</a></div>" for p in products])
    return f'<h1>Welcome {user.name}</h1><p>Balance: ${user.balance}</p><a href="/withdraw">Withdraw</a> <a href="/settings">Settings</a> {admin_btn}<hr>{products_html}'

@app.route('/cart/add/<int:product_id>')
def add_to_cart(product_id):
    user = get_current_user()
    if not user: return redirect('/loginpage')
    product = Product.query.get(product_id)
    if product and product.stock > 0:
        db.session.add(CartItem(user_id=user.id, product_id=product.id))
        product.stock -= 1; user.balance += Decimal('0.40'); db.session.commit()
    return redirect('/dashboard')

# ============================================
# WITHDRAW + SETTINGS + HISTORY
# ============================================
@app.route('/withdraw', methods=['GET', 'POST'])
def withdraw():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    if request.method == 'POST':
        amount = Decimal(request.form.get('amount', '0'))
        if amount > 0 and amount <= user.balance:
            db.session.add(Withdrawal(user_id=user.id, amount=amount)); user.balance -= amount; db.session.commit()
            return "Request sent <a href='/dashboard'>Back</a>"
    return f'<form method="POST">Balance: ${user.balance}<br><input name="amount" type="number" step="0.01"><input name="phone" placeholder="Phone"><input name="password" type="password"><button>Request</button></form>'

@app.route('/withdraw/history')
def withdraw_history():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    withdrawals = Withdrawal.query.filter_by(user_id=user.id).all()
    rows = "".join([f"<tr><td>${float(w.amount)}</td><td>{w.status}</td></tr>" for w in withdrawals])
    return f'<h1>History</h1><table>{rows}</table><a href="/dashboard">Back</a>'

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    if request.method == 'POST':
        user.phone = request.form.get('phone', user.phone)
        new_password = request.form.get('new_password', '')
        if new_password: user.password_hash = generate_password_hash(new_password)
        db.session.commit(); return redirect('/settings')
    return f'<form method="POST">Phone: <input name="phone" value="{user.phone}"><br>New Pass: <input name="new_password" type="password"><button>Save</button></form>'

# ============================================
# ADMIN
# ============================================
@app.route('/admin/dashboard')
@jwt_required()
def admin_dashboard():
    user = User.query.get(int(get_jwt_identity()))
    if not user or not user.is_admin: return "Unauthorized", 403
    withdrawals = Withdrawal.query.all()
    w_rows = "".join([f"<tr><td>{w.id}</td><td>${float(w.amount)}</td><td>{w.status}</td><td><a href=/admin/approve-withdrawal/{w.id}>Approve</a></td></tr>" for w in withdrawals])
    return f'<h1>Admin</h1><table>{w_rows}</table>'

@app.route('/admin/approve-withdrawal/<int:withdrawal_id>')
@jwt_required()
def approve_withdrawal(withdrawal_id):
    user = User.query.get(int(get_jwt_identity()))
    if not user or not user.is_admin: return "Unauthorized", 403
    w = Withdrawal.query.get(withdrawal_id); w.status = 'approved'; w.approved_at = datetime.utcnow()
    User.query.get(w.user_id).total_withdrawn += w.amount; db.session.commit()
    return redirect('/admin/dashboard')

@app.route('/admin/add-product', methods=['GET', 'POST'])
def add_product():
    user = get_current_user()
    if not user or not user.is_admin: return redirect('/loginpage')
    if request.method == 'POST':
        db.session.add(Product(name=request.form.get('name'), price=request.form.get('price'), stock=request.form.get('stock')))
        db.session.commit(); return redirect('/dashboard')
    return '<form method="post"><input name="name"><input name="price"><input name="stock"><button>Add</button></form>'

if __name__ == '__main__': app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
