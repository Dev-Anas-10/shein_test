
import os
import json
import subprocess
import sys
import uuid
from decimal import Decimal, InvalidOperation
from functools import wraps

import psycopg2
import psycopg2.extras
from psycopg2 import errors
from flask import (
    Flask, request, session, redirect, url_for,
    render_template, flash, abort, Response, g
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
SCRAPER = os.path.join(BASE, "scraper.py")
DB_URL = os.environ["DATABASE_URL"]

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)

# أسماء العملات المدعومة
CUR = {
    "SAR": "ر.س",
    "EGP": "ج.م",
    "USD": "$",
}


@app.template_filter("money")
def money(value):
    return "{:,.2f}".format(Decimal(str(value)))


@app.template_filter("cur")
def cur_name(currency):
    return CUR.get(currency, currency)


def db():
    if "db" not in g:
        g.db = psycopg2.connect(
            DB_URL,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return g.db


@app.teardown_appcontext
def close_db(_):
    connection = g.pop("db", None)
    if connection:
        connection.close()


def q(sql, args=(), fetch="all"):
    connection = db()
    with connection.cursor() as cursor:
        cursor.execute(sql, args)
        if fetch == "all":
            result = cursor.fetchall()
        elif fetch == "one":
            result = cursor.fetchone()
        else:
            result = None
    connection.commit()
    return result


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


@app.context_processor
def inject():
    count = 0
    if session.get("uid"):
        row = q(
            "SELECT COALESCE(SUM(qty), 0) AS n "
            "FROM cart_items WHERE user_id=%s",
            (session["uid"],),
            "one",
        )
        count = row["n"]
    return {"cart_n": count}


def dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def enrich(product):
    original = dec(product.get("original_price"))
    sale = dec(product.get("sale_price"))

    if original < sale or original == 0:
        original = sale

    percentage = int((original - sale) / original * 100) if original > sale else 0

    return {
        "p": product,
        "orig": original,
        "sale": sale,
        "pct": percentage,
    }


# ---------- Authentication ----------

@app.route("/")
def index():
    return redirect(url_for("order" if session.get("uid") else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if "@" not in email or len(password) < 6:
            flash("اكتب إيميل صحيح وباسورد 6 حروف على الأقل")
            return redirect(url_for("register"))

        try:
            row = q(
                "INSERT INTO users(email, password_hash) "
                "VALUES(%s, %s) RETURNING id",
                (email, generate_password_hash(password)),
                "one",
            )
        except errors.UniqueViolation:
            db().rollback()
            flash("الإيميل ده مسجل قبل كده")
            return redirect(url_for("register"))

        session.clear()
        session.permanent = True
        session["uid"] = row["id"]
        session["email"] = email
        return redirect(url_for("order"))

    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = q(
            "SELECT * FROM users WHERE email=%s",
            (email,),
            "one",
        )

        if not user or not check_password_hash(user["password_hash"], password):
            flash("الإيميل أو الباسورد غلط")
            return redirect(url_for("login"))

        session.clear()
        session.permanent = True
        session["uid"] = user["id"]
        session["email"] = user["email"]
        return redirect(url_for("order"))

    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Order scraping ----------

def run_scraper(url):
    process = subprocess.run(
        [sys.executable, SCRAPER, url],
        capture_output=True,
        text=True,
        timeout=150,
    )

    if process.returncode != 0:
        lines = process.stderr.strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "فشل الاستخراج")

    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("الـ scraper لم يُرجع JSON صالحًا")


@app.route("/order", methods=["GET", "POST"])
@login_required
def order():
    if request.method == "POST":
        url = request.form.get("url", "").strip()[:5000]

        try:
            data = run_scraper(url)
        except Exception as error:
            flash(str(error)[:300])
            return redirect(url_for("order"))

        scrape_id = str(uuid.uuid4())
        q(
            "INSERT INTO scrapes(id, user_id, data) VALUES(%s, %s, %s)",
            (
                scrape_id,
                session["uid"],
                psycopg2.extras.Json(data),
            ),
            None,
        )

        return redirect(url_for("result", sid=scrape_id))

    return render_template("order.html")


def get_scrape(sid):
    try:
        uuid.UUID(sid)
    except (ValueError, TypeError):
        abort(404)

    row = q(
        "SELECT data FROM scrapes WHERE id=%s AND user_id=%s",
        (sid, session["uid"]),
        "one",
    )

    if not row:
        abort(404)

    return row["data"]


@app.route("/order/<sid>")
@login_required
def result(sid):
    data = get_scrape(sid)
    items = [enrich(product) for product in data["products"]]
    available = [item for item in items if not item["p"].get("sold_out")]

    total = sum((item["sale"] for item in available), Decimal("0"))
    total_original = sum((item["orig"] for item in available), Decimal("0"))

    currency = items[0]["p"].get("currency", "SAR") if items else "SAR"

    return render_template(
        "result.html",
        sid=sid,
        data=data,
        items=items,
        total=total,
        total_orig=total_original,
        currency=currency,
        raw=json.dumps(data, ensure_ascii=False, indent=2),
    )


@app.route("/order/<sid>/json")
@login_required
def result_json(sid):
    return Response(
        json.dumps(get_scrape(sid), ensure_ascii=False, indent=2),
        mimetype="application/json; charset=utf-8",
    )


# ---------- Shopping cart ----------

@app.post("/cart/add")
@login_required
def cart_add():
    sid = request.form.get("sid", "")
    index = request.form.get("idx", "all")
    products = get_scrape(sid)["products"]

    try:
        selected = products if index == "all" else [products[int(index)]]
    except (ValueError, IndexError):
        abort(400)

    added = 0

    for product in selected:
        if product.get("sold_out"):
            continue

        original = dec(product["original_price"])
        sale = dec(product["sale_price"])

        q(
            """
            INSERT INTO cart_items(
                user_id, goods_id, goods_sn, name, image,
                original_price, sale_price, currency,
                selected_variant, sku_code
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id, goods_id, sku_code)
            DO UPDATE SET
                qty = cart_items.qty + 1,
                name = EXCLUDED.name,
                image = EXCLUDED.image,
                original_price = EXCLUDED.original_price,
                sale_price = EXCLUDED.sale_price,
                currency = EXCLUDED.currency
            """,
            (
                session["uid"],
                product["goods_id"],
                product["goods_sn"],
                product["name"],
                product["image"],
                original,
                sale,
                product["currency"],
                product["selected_variant"],
                product["sku_code"],
            ),
            None,
        )
        added += 1

    flash(f"تمت إضافة {added} منتج للعربة")
    return redirect(url_for("cart"))


@app.route("/cart")
@login_required
def cart():
    rows = q(
        "SELECT * FROM cart_items WHERE user_id=%s ORDER BY added_at DESC",
        (session["uid"],),
    )

    total = sum((row["sale_price"] * row["qty"] for row in rows), Decimal("0"))
    total_original = sum(
        (max(row["original_price"], row["sale_price"]) * row["qty"] for row in rows),
        Decimal("0"),
    )

    currency = rows[0]["currency"] if rows else "SAR"

    for row in rows:
        original = max(row["original_price"], row["sale_price"])
        sale = row["sale_price"]
        row["pct"] = int((original - sale) / original * 100) if original > sale else 0
        row["orig"] = original

    return render_template(
        "cart.html",
        rows=rows,
        total=total,
        total_orig=total_original,
        currency=currency,
    )


@app.post("/cart/qty/<int:item_id>")
@login_required
def cart_qty(item_id):
    delta = 1 if request.form.get("d") == "+" else -1

    q(
        "UPDATE cart_items SET qty=qty+%s WHERE id=%s AND user_id=%s",
        (delta, item_id, session["uid"]),
        None,
    )
    q(
        "DELETE FROM cart_items WHERE id=%s AND user_id=%s AND qty<=0",
        (item_id, session["uid"]),
        None,
    )

    return redirect(url_for("cart"))


@app.post("/cart/remove/<int:item_id>")
@login_required
def cart_remove(item_id):
    q(
        "DELETE FROM cart_items WHERE id=%s AND user_id=%s",
        (item_id, session["uid"]),
        None,
    )
    return redirect(url_for("cart"))


@app.post("/cart/clear")
@login_required
def cart_clear():
    q(
        "DELETE FROM cart_items WHERE user_id=%s",
        (session["uid"],),
        None,
    )
    return redirect(url_for("cart"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
