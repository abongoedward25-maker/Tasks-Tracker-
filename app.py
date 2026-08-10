# ============================================
# FRESHIPPO PREMIUM v6.5 - FINAL COMPLETE CODE
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

# === DATABASE URL FIX ===
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
        return {"id": self.id, "email": self.email, "name": self.name, "phone": self.phone, 
                "language": self.language, "is_admin": self.is_admin, "balance": float(self.balance), 
                "total_withdrawn": float(self.total_withdrawn), "current_stage": self.current_stage, 
                "stage_status": self.stage_status}

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
def add_missing_columns():
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
        except Exception as e:
            db.session.rollback()

add_missing_columns()

# ============================================
# HELPERS
# ============================================
def get_current_user():
    token = request.cookies.get('access_token')
    if not token: return None
    try:
        decoded = decode_token(token)
        return User.query.get(int(decoded['sub']))
    except Exception: return None

def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user_id = get_jwt_identity()
        user = User.query.get(int(user_id))
        if not user or not user.is_admin: return jsonify({"msg": "Admin required"}), 403
        return fn(*args, **kwargs)
    return wrapper

# ============================================
# PUBLIC PAGES
# ============================================
@app.route('/')
def homepage():
    return '''<html><head><title>Freshippo API</title></head><body style="font-family:Poppins; text-align:center; padding:50px; background:linear-gradient(135deg,#0f0c29,#302b63,#24243e); color:white">
    <h1 style="font-size:48px">🛒 Freshippo API v6.5</h1><p>Status: <b style="color:#22c55e">LIVE</b></p>
    <p><a href="/signup" style="color:#a855f7;font-size:18px">Sign Up</a> | <a href="/loginpage" style="color:#a855f7;font-size:18px">Login</a> | <a href="/dashboard" style="color:#a855f7;font-size:18px">Dashboard</a></p>
    </body></html>'''

@app.route('/health')
def health():
    try: db.session.execute(db.text('SELECT 1')); return jsonify({"status": "healthy"}), 200
    except Exception as e: return jsonify({"status": "error", "db": str(e)}), 500

@app.route('/ping')
def ping(): return "pong", 200

@app.route('/signup', methods=['GET', 'POST'])
def signup_page():
    if request.method == 'POST':
        name, email, password = request.form.get('name'), request.form.get('email'), request.form.get('password')
        if User.query.filter_by(email=email).first(): return "Error: Email exists <br><a href='/signup'>Try again</a>"
        user = User(email=email, name=name, phone='')
        user.password_hash = generate_password_hash(password)
        db.session.add(user); db.session.commit()
        return f"<h1>Account Created</h1><p>Welcome {name}</p><a href='/loginpage'>Sign In</a>"
    return '''<style>body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}</style><h2>Sign Up</h2><form method='POST' style='max-width:300px;margin:auto;padding:30px;background:rgba(255,255,255,0.05);border-radius:20px'><input name='name' placeholder='Name' required style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><input name='email' type='email' required placeholder='Email' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><input name='password' type='password' required placeholder='Password' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><button style='width:100%;padding:14px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold'>Sign Up</button></form>'''

@app.route('/loginpage', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        email, password = request.form.get('email'), request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            token = create_access_token(identity=str(user.id))
            resp = make_response(redirect('/dashboard')); resp.set_cookie('access_token', token, httponly=True); return resp
        return "Error: Wrong credentials <br><a href='/loginpage'>Try again</a>"
    return '''<style>body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}</style><h2>Login</h2><form method='POST' style='max-width:300px;margin:auto;padding:30px;background:rgba(255,255,255,0.05);border-radius:20px'><input name='email' type='email' required placeholder='Email' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><input name='password' type='password' required placeholder='Password' style='width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white'><br><button style='width:100%;padding:14px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold'>Sign In</button></form>'''

@app.route('/logout')
def logout():
    resp = make_response(redirect('/loginpage')); resp.set_cookie('access_token', '', expires=0); return resp

# ============================================
# DASHBOARD
# ============================================
@app.route('/dashboard')
def dashboard():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    products = Product.query.all()
    cart_items = CartItem.query.filter_by(user_id=user.id).all()
    added_product_ids = {ci.product_id for ci in cart_items}

    last_withdrawal = Withdrawal.query.filter_by(user_id=user.id, status='approved').order_by(Withdrawal.approved_at.desc()).first()
    can_withdraw, days_left = True, 0
    if last_withdrawal:
        days_passed = (datetime.utcnow() - last_withdrawal.approved_at).days
        if days_passed < 10: can_withdraw, days_left = False, 10 - days_passed

    if user.current_stage < 5:
        next_target = STAGE_TARGETS[user.current_stage + 1]
        progress = min(100, int((float(user.balance) / next_target) * 100)) if next_target else 0
        next_stage_text = f"Stage {user.current_stage} → {user.current_stage + 1}"
    else: progress, next_target, next_stage_text = 100, None, "Max Stage"

    products_html = "".join([f"<div style='border:1px solid rgba(168,85,247,0.2); padding:20px; margin:20px 0; background:rgba(255,255,255,0.03); border-radius:20px'><img src='{p.image_url or 'https://via.placeholder.com/400x200/111/a855f7?text=No+Image'}' style='width:100%;height:200px;object-fit:cover;border-radius:15px;margin-bottom:12px'><h3>{p.name}</h3><p>${p.price} | Stock: {p.stock}</p>{'<span style=color:#22c55e>✓ Added</span>' if p.id in added_product_ids else f'<a href=/cart/add/{p.id} class=btn>🛒 Add +$0.40</a>'}</div>" for p in products]) if products else "<p>No products yet.</p>"

    admin_html = f"<p><a href='/admin/add-product' class='btn'>+ Add Product</a></p><p><a href='/admin/dashboard' class='btn'>👑 Admin Dashboard</a></p>" if user.is_admin else ""
    withdraw_btn = f'<a href="/withdraw" class="btn">💰 Withdraw</a> <a href="/withdraw/history" class="btn">📜 History</a>' if can_withdraw else f'<span style="color:#ffaa00">⏳ Next in {days_left} days</span>'

    css = "@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;800&display=swap');body{background:linear-gradient(135deg,#0f0c29 0%,#302b63 50%,#24243e 100%);background-attachment:fixed;color:white;font-family:'Poppins',sans-serif;margin:0;min-height:100vh}.watermark{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%) rotate(-15deg);font-size:15vw;color:rgba(168,85,247,0.05);z-index:0;pointer-events:none;white-space:nowrap;font-weight:900}.content{position:relative;z-index:1;padding:20px;max-width:900px;margin:auto}.box{padding:25px;margin:20px 0;background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);border:1px solid rgba(168,85,247,0.3);border-radius:20px}.btn{display:inline-block;padding:14px 28px;margin:8px 5px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;text-decoration:none;border-radius:12px;font-weight:600}.stats{display:flex;gap:20px}.stat{flex:1;background:rgba(0,0,0,0.4);padding:20px;border-radius:15px;text-align:center}.menu-btn{background:rgba(255,255,255,0.1);border:2px solid rgba(168,85,247,0.4);color:white;padding:12px 16px;border-radius:12px;cursor:pointer;font-size:20px}.progress{background:rgba(255,255,255,0.1);border-radius:20px;height:18px;overflow:hidden}.progress-fill{background:linear-gradient(90deg,#22c55e,#a855f7);height:100%}"

    return f"""<style>{css}</style><div class="watermark">FRESHIPPO</div><div class="content">
    <div class="box" style="text-align:right"><button onclick="document.getElementById('menu').style.display=document.getElementById('menu').style.display==='block'?'none':'block'" class="menu-btn">⋮</button>
    <div id="menu" style="display:none;position:absolute;right:20px;background:rgba(26,26,46,0.98);border-radius:15px;min-width:200px;z-index:999">
    <a href="/dashboard" style="display:block;padding:15px 20px;color:white;text-decoration:none">🏠 Home</a><a href="/settings" style="display:block;padding:15px 20px;color:white;text-decoration:none">⚙️ Settings</a><a href="/logout" style="display:block;padding:15px 20px;color:#ff6666;text-decoration:none">🚪 Logout</a></div></div>
    <div class="box"><p>Admin: {'✅ Active' if user.is_admin else '❌ No Access'}</p></div>
    <div class="box"><h3>📦 Products: {len(products)} items</h3></div>
    <div class="box"><h3>💰 Wallet</h3><div class="stats"><div class="stat"><b style="font-size:28px;color:#a855f7">${user.balance}</b><br>Balance</div><div class="stat"><b style="font-size:28px;color:#22c55e">${user.total_withdrawn}</b><br>Withdrawn</div></div>{withdraw_btn}</div>
    <div class="box"><h3>🚀 Stage Progress</h3><p>{next_stage_text} · ${float(user.balance)} {f'of ${next_target}' if next_target else ''}</p><div class="progress"><div class="progress-fill" style="width:{progress}%"></div></div></div>
    {admin_html}<hr><h1 style='text-align:center'>🛒 Welcome {user.name}!</h1><p style='text-align:center'>Email: {user.email}</p><hr><h2>Products:</h2>{products_html}</div>"""

# ============================================
# CART / WITHDRAW / SETTINGS / HISTORY
# ============================================
@app.route('/cart/add/<int:product_id>')
def add_to_cart(product_id):
    user = get_current_user()
    if not user: return redirect('/loginpage')
    product = Product.query.get(product_id)
    if not product or product.stock <= 0: return '❌ Error <br><a href="/dashboard">Back</a>'
    if CartItem.query.filter_by(user_id=user.id, product_id=product.id).first(): return '⚠️ Already added <br><a href="/dashboard">Back</a>'
    db.session.add(CartItem(user_id=user.id, product_id=product.id))
    product.stock -= 1; user.balance += Decimal('0.40'); db.session.commit()
    return redirect('/dashboard')

@app.route('/withdraw', methods=['GET', 'POST'])
def withdraw():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    last_withdrawal = Withdrawal.query.filter_by(user_id=user.id, status='approved').order_by(Withdrawal.approved_at.desc()).first()
    if last_withdrawal and (datetime.utcnow() - last_withdrawal.approved_at).days < 10: return f'<h1>⏳ Cooldown</h1><p>Next in {10 - (datetime.utcnow() - last_withdrawal.approved_at).days} days</p><a href="/dashboard">Back</a>'
    if request.method == 'POST':
        try: amount = Decimal(request.form.get('amount', '0'))
        except: return '❌ Invalid amount'
        phone, password = request.form.get('phone', '').strip(), request.form.get('password', '')
        if not check_password_hash(user.password_hash, password): return '❌ Wrong password'
        if phone != user.phone: return '❌ Phone must match'
        if amount <= 0 or amount > user.balance: return '❌ Invalid amount'
        db.session.add(Withdrawal(user_id=user.id, amount=amount)); user.balance -= amount; db.session.commit()
        return f'<h1>✅ Request sent!</h1><p>Admin will review ${amount}</p><a href="/dashboard">Back</a>'
    return f'''<style>body{{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}}</style><div style="text-align:right;padding:20px"><a href="/dashboard">🏠 Home</a></div><h2 style="text-align:center">💰 Request Withdrawal</h2><div style="max-width:380px;margin:auto;padding:35px;background:rgba(255,255,255,0.05);border-radius:20px"><p>Balance: <b>${user.balance}</b></p><form method="POST"><input name="amount" type="number" step="0.01" placeholder="Amount $" required style="width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><input name="phone" type="text" placeholder="Confirm phone: {user.phone}" required style="width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><input name="password" type="password" placeholder="Confirm Password" required style="width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><button type="submit" style="width:100%;padding:15px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold">Request</button></form></div>'''

@app.route('/withdraw/history')
def withdraw_history():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    withdrawals = Withdrawal.query.filter_by(user_id=user.id).order_by(Withdrawal.requested_at.desc()).all()
    rows = "".join([f"<tr><td>${float(w.amount)}</td><td>{w.requested_at.strftime('%Y-%m-%d %H:%M')}</td><td style='color:{'#22c55e' if w.status == 'approved' else '#ffaa00' if w.status == 'pending' else '#ef4444'}'>{w.status}</td></tr>" for w in withdrawals])
    return f'''<style>body{{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins;padding:30px}}table{{width:100%;background:rgba(255,255,255,0.05);border-radius:15px}}th,td{{padding:12px}}</style><a href="/dashboard">🏠 Home</a><h1>📜 Withdrawal History</h1><table><tr><th>Amount</th><th>Date</th><th>Status</th></tr>{rows}</table>'''

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user = get_current_user()
    if not user: return redirect('/loginpage')
    if request.method == 'POST':
        user.phone = request.form.get('phone', user.phone).strip()
        user.language = request.form.get('language', user.language)
        new_password = request.form.get('new_password', '')
        if new_password: user.password_hash = generate_password_hash(new_password)
        db.session.commit(); return redirect('/settings?updated=1')
    updated = request.args.get('updated')
    return f'''<style>body{{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}}</style><div style="text-align:right;padding:20px"><a href="/dashboard">🏠 Home</a></div><h2 style="text-align:center">⚙️ Settings</h2>{'<p style="text-align:center;color:#22c55e">✅ Saved</p>' if updated else ''}<div style="max-width:400px;margin:50px auto;padding:35px;background:rgba(255,255,255,0.05);border-radius:20px"><form method="POST"><input name="phone" value="{user.phone}" placeholder="Phone Number" style="width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><select name="language" style="width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><option value="en" {'selected' if user.language == 'en' else ''}>English</option><option value="ar" {'selected' if user.language == 'ar' else ''}>العربية</option></select><input name="new_password" type="password" placeholder="New Password (optional)" style="width:100%;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><button type="submit" style="width:100%;padding:15px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold">Save Changes</button></form></div>'''

# ============================================
# ADMIN
# ============================================
@app.route('/admin/dashboard')
@jwt_required()
def admin_dashboard():
    current_user_id = get_jwt_identity()
    current_user = User.query.get(int(current_user_id))
    if not current_user or not current_user.is_admin: return "Unauthorized", 403
    
    withdrawals = Withdrawal.query.order_by(Withdrawal.requested_at.desc()).all()
    stage_requests = StageRequest.query.order_by(StageRequest.requested_at.desc()).all()
    
    w_rows = "".join([f"<tr><td>{User.query.get(w.user_id).name if User.query.get(w.user_id) else 'N/A'}</td><td>${float(w.amount)}</td><td>{User.query.get(w.user_id).phone if User.query.get(w.user_id) else 'N/A'}</td><td>{w.requested_at.strftime('%Y-%m-%d %H:%M')}</td><td>{w.status}</td><td>{'<a href=/admin/approve-withdrawal/'+str(w.id)+' style=color:#22c55e>✅</a> <a href=/admin/reject-withdrawal/'+str(w.id)+' style=color:#ef4444>❌</a>' if w.status=='pending' else '-'}</td></tr>" for w in withdrawals])
    s_rows = "".join([f"<tr><td>{User.query.get(s.user_id).name if User.query.get(s.user_id) else 'N/A'}</td><td>Stage {s.from_stage}</td><td>Stage {s.to_stage}</td><td>{s.requested_at.strftime('%Y-%m-%d %H:%M')}</td><td>{s.status}</td><td>{'<a href=/admin/approve-stage/'+str(s.user_id)+' style=color:#22c55e>Approve</a>' if s.status=='pending' else '-'}</td></tr>" for s in stage_requests])
    
    return f'''<style>body{{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins;padding:30px}}table{{background:rgba(255,255,255,0.05);border-radius:15px;width:100%}}th,td{{padding:12px}}th{{color:#a855f7}}a{{color:#a855f7}}</style><h1>👑 Admin Dashboard</h1><h2>💰 Withdrawals</h2><table><tr><th>User</th><th>Amount</th><th>Phone</th><th>Date</th><th>Status</th><th>Action</th></tr>{w_rows}</table><h2>🚀 Stage Requests</h2><table><tr><th>User</th><th>From</th><th>To</th><th>Date</th><th>Status</th><th>Action</th></tr>{s_rows}</table><br><a href="/dashboard">← Back</a>'''

@app.route('/admin/approve-withdrawal/<int:withdrawal_id>')
@jwt_required()
def approve_withdrawal(withdrawal_id):
    current_user_id = get_jwt_identity()
    admin = User.query.get(int(current_user_id))
    if not admin or not admin.is_admin: return "Unauthorized", 403
    withdrawal = Withdrawal.query.get_or_404(withdrawal_id)
    if withdrawal.status == 'pending':
        user = User.query.get(withdrawal.user_id)
        if user: user.total_withdrawn += withdrawal.amount
        withdrawal.status = 'approved'
        withdrawal.approved_at = datetime.utcnow()
        db.session.commit()
    return redirect('/admin/dashboard')

@app.route('/admin/reject-withdrawal/<int:withdrawal_id>')
@jwt_required()
def reject_withdrawal(withdrawal_id):
    current_user_id = get_jwt_identity()
    admin = User.query.get(int(current_user_id))
    if not admin or not admin.is_admin: return "Unauthorized", 403
    withdrawal = Withdrawal.query.get_or_404(withdrawal_id)
    if withdrawal.status == 'pending':
        user = User.query.get(withdrawal.user_id)
        if user: user.balance += withdrawal.amount
        withdrawal.status = 'rejected'
        db.session.commit()
    return redirect('/admin/dashboard')

@app.route('/admin/approve-stage/<int:user_id>')
@jwt_required()
def approve_stage(user_id):
    current_user_id = get_jwt_identity()
    admin = User.query.get(int(current_user_id))
    if not admin or not admin.is_admin: return "Unauthorized", 403
    target_user = User.query.get_or_404(user_id)
    if target_user.current_stage < 5:
        next_target = STAGE_TARGETS[target_user.current_stage + 1]
        if float(target_user.balance) >= next_target:
            target_user.current_stage += 1
            target_user.stage_status = 'approved'
            target_user.stage_updated_at = datetime.utcnow()
            db.session.commit()
    return redirect('/admin/dashboard')

@app.route('/admin/add-product', methods=['GET', 'POST'])
def add_product():
    user = get_current_user()
    if not user or not user.is_admin: return redirect('/loginpage')
    if request.method == 'POST':
        name, price, stock = request.form.get('name'), float(request.form.get('price')), int(request.form.get('stock'))
        db.session.add(Product(name=name, price=price, stock=stock, description=request.form.get('desc', ''), image_url=request.form.get('image_url', '')))
        db.session.commit(); return redirect('/dashboard')
    return '''<style>body{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:white;font-family:Poppins}</style><form method="post" style="max-width:400px;margin:50px auto;padding:30px;background:rgba(255,255,255,0.05);border-radius:20px"><h2>Add Product</h2><input name="name" placeholder="Product Name" required style="width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><input name="price" type="number" step="0.01" placeholder="Price $" required style="width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><input name="stock" type="number" placeholder="Stock" required style="width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><input name="image_url" placeholder="Image URL" style="width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #333;background:#0a0a0a;color:white"><button type="submit" style="width:100%;padding:14px;background:linear-gradient(135deg,#a855f7,#7c3aed);color:white;border:none;border-radius:10px;font-weight:bold">Add</button><br><br><a href="/dashboard">← Back</a></form>'''

# ============================================
# RUN
# ============================================
if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1', 'yes']
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
