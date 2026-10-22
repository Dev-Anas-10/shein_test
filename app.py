import os, json, subprocess, sys, uuid
from decimal import Decimal, InvalidOperation
from functools import wraps
import psycopg2, psycopg2.extras
from psycopg2 import errors
from flask import (Flask, request, session, redirect, url_for,
                   render_template, flash, abort, Response, g)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
SCRAPER = os.path.join(BASE, "scraper.py")
DB_URL = os.environ["DATABASE_URL"]

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)

CUR = {"SAR": "ر.س"}

@app.template_filter("money")
def money(v):
    return "{:,.2f}".format(Decimal(v))

@app.template_filter("cur")
def cur_name(c):
    return CUR.get(c, c)

def db():
    if "db" not in g:
        g.db = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()

def q(sql, args=(), fetch="all"):
    conn = db()
    with conn.cursor() as cur:
        cur.execute(sql, args)
        res = (cur.fetchall() if fetch == "all"
               else cur.fetchone() if fetch == "one" else None)
    conn.commit()
    return res

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return w

@app.context_processor
def inject():
    n = 0
    if session.get("uid"):
        n = q("SELECT COALESCE(SUM(qty),0) AS n FROM cart_items WHERE user_id=%s",
              (session["uid"],), "one")["n"]
    return {"cart_n": n}

def dec(x):
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return Decimal("0")

def enrich(p):
    o, s = dec(p.get("original_price")), dec(p.get("sale_price"))
    if o < s or o == 0:
        o = s
    pct = int((o - s) / o * 100) if o > s else 0
    return {"p": p, "orig": o, "sale": s, "pct": pct}

# ---------- auth ----------
@app.route("/")
def index():
    return redirect(url_for("order" if session.get("uid") else "login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        if "@" not in email or len(pw) < 6:
            flash("اكتب إيميل صحيح وباسورد 6 حروف على الأقل")
            return redirect(url_for("register"))
        try:
            row = q("INSERT INTO users(email,password_hash) VALUES(%s,%s) RETURNING id",
                    (email, generate_password_hash(pw)), "one")
        except errors.UniqueViolation:
            db().rollback()
            flash("الإيميل ده مسجل قبل كده")
            return redirect(url_for("register"))
        session.clear(); session.permanent = True
        session["uid"] = row["id"]; session["email"] = email
        return redirect(url_for("order"))
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        u = q("SELECT * FROM users WHERE email=%s", (email,), "one")
        if not u or not check_password_hash(u["password_hash"], pw):
            flash("الإيميل أو الباسورد غلط")
            return redirect(url_for("login"))
        session.clear(); session.permanent = True
        session["uid"] = u["id"]; session["email"] = u["email"]
        return redirect(url_for("order"))
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- order ----------
def run_scraper(url):
    p = subprocess.run([sys.executable, SCRAPER, url],
                       capture_output=True, text=True, timeout=150)
    if p.returncode != 0:
        lines = p.stderr.strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "فشل الاستخراج")
    return json.loads(p.stdout)

@app.route("/order", methods=["GET", "POST"])
@login_required
def order():
    if request.method == "POST":
        url = request.form.get("url", "").strip()[:5000]
        try:
            data = run_scraper(url)
        except Exception as e:
            flash(str(e)[:300])
            return redirect(url_for("order"))
        sid = str(uuid.uuid4())
        q("INSERT INTO scrapes(id,user_id,data) VALUES(%s,%s,%s)",
          (sid, session["uid"], psycopg2.extras.Json(data)), None)
        return redirect(url_for("result", sid=sid))
    return render_template("order.html")

def get_scrape(sid):
    try:
        uuid.UUID(sid)
    except ValueError:
        abort(404)
    row = q("SELECT data FROM scrapes WHERE id=%s AND user_id=%s",
            (sid, session["uid"]), "one")
    if not row:
        abort(404)
    return row["data"]

@app.route("/order/<sid>")
@login_required
def result(sid):
    data = get_scrape(sid)
    items = [enrich(p) for p in data["products"]]
    ok = [i for i in items if not i["p"].get("sold_out")]
    total = sum((i["sale"] for i in ok), Decimal(0))
    total_orig = sum((i["orig"] for i in ok), Decimal(0))
    currency = items[0]["p"].get("currency", "SAR") if items else "SAR"
    return render_template("result.html", sid=sid, data=data, items=items,
                           total=total, total_orig=total_orig, currency=currency,
                           raw=json.dumps(data, ensure_ascii=False, indent=2))

@app.route("/order/<sid>/json")
@login_required
def result_json(sid):
    return Response(json.dumps(get_scrape(sid), ensure_ascii=False, indent=2),
                    mimetype="application/json; charset=utf-8")

# ---------- cart ----------
@app.post("/cart/add")
@login_required
def cart_add():
    sid = request.form.get("sid", "")
    idx = request.form.get("idx", "all")
    prods = get_scrape(sid)["products"]
    try:
        sel = prods if idx == "all" else [prods[int(idx)]]
    except (ValueError, IndexError):
        abort(400)
    n = 0
    for p in sel:
        if p.get("sold_out"):
            continue
        o, s = dec(p["original_price"]), dec(p["sale_price"])
        q("""INSERT INTO cart_items(user_id,goods_id,goods_sn,name,image,original_price,
                sale_price,currency,selected_variant,sku_code)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
             ON CONFLICT (user_id,goods_id,sku_code) DO UPDATE SET
               qty=cart_items.qty+1, name=EXCLUDED.name, image=EXCLUDED.image,
               original_price=EXCLUDED.original_price, sale_price=EXCLUDED.sale_price""",
          (session["uid"], p["goods_id"], p["goods_sn"], p["name"], p["image"], o, s,
           p["currency"], p["selected_variant"], p["sku_code"]), None)
        n += 1
    flash(f"تمت إضافة {n} منتج للعربة")
    return redirect(url_for("cart"))

@app.route("/cart")
@login_required
def cart():
    rows = q("SELECT * FROM cart_items WHERE user_id=%s ORDER BY added_at DESC",
             (session["uid"],))
    total = sum((r["sale_price"] * r["qty"] for r in rows), Decimal(0))
    total_orig = sum((max(r["original_price"], r["sale_price"]) * r["qty"] for r in rows), Decimal(0))
    currency = rows[0]["currency"] if rows else "SAR"
    for r in rows:
        o, s = max(r["original_price"], r["sale_price"]), r["sale_price"]
        r["pct"] = int((o - s) / o * 100) if o > s else 0
        r["orig"] = o
    return render_template("cart.html", rows=rows, total=total,
                           total_orig=total_orig, currency=currency)

@app.post("/cart/qty/<int:item_id>")
@login_required
def cart_qty(item_id):
    delta = 1 if request.form.get("d") == "+" else -1
    q("UPDATE cart_items SET qty=qty+%s WHERE id=%s AND user_id=%s",
      (delta, item_id, session["uid"]), None)
    q("DELETE FROM cart_items WHERE id=%s AND user_id=%s AND qty<=0",
      (item_id, session["uid"]), None)
    return redirect(url_for("cart"))

@app.post("/cart/remove/<int:item_id>")
@login_required
def cart_remove(item_id):
    q("DELETE FROM cart_items WHERE id=%s AND user_id=%s", (item_id, session["uid"]), None)
    return redirect(url_for("cart"))

@app.post("/cart/clear")
@login_required
def cart_clear():
    q("DELETE FROM cart_items WHERE user_id=%s", (session["uid"],), None)
    return redirect(url_for("cart"))
