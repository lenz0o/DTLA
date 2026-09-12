#!/usr/bin/env python3
"""
Farmer Direct Inventory System - Production ready version
"""

from flask import Flask, render_template, request, redirect, url_for, flash, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import sqlite3
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "farmer-direct-change-this-in-production-2026")

# Use /tmp on cloud platforms so the DB is writable
if os.environ.get("RENDER") or os.environ.get("DYNO"):
    DATABASE = "/tmp/inventory.db"
else:
    DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    """Create tables and seed data if needed."""
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA foreign_keys = ON")

    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        role TEXT DEFAULT 'user',
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        product_type TEXT,
        unit_size REAL,
        unit TEXT DEFAULT 'g',
        supplier TEXT,
        reorder_level REAL DEFAULT 0,
        unit_cost REAL DEFAULT 0,
        retail_price REAL DEFAULT 0,
        active INTEGER DEFAULT 1,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        area TEXT,
        storage_type TEXT,
        responsible TEXT,
        capacity_notes TEXT,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        batch_lot TEXT NOT NULL DEFAULT '',
        location_id INTEGER NOT NULL,
        qty_on_hand REAL NOT NULL DEFAULT 0,
        unit_cost REAL DEFAULT 0,
        expiration_date TEXT,
        status TEXT DEFAULT 'IN STOCK',
        last_count_date TEXT,
        notes TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(product_id, batch_lot, location_id)
    );
    CREATE TABLE IF NOT EXISTS receiving (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_id TEXT,
        date TEXT NOT NULL,
        supplier TEXT,
        product_id INTEGER,
        batch_lot TEXT,
        location_id INTEGER,
        qty_received REAL NOT NULL,
        unit_cost REAL,
        total_cost REAL,
        received_by TEXT,
        reference_number TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS transfers_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT,
        date TEXT NOT NULL,
        type TEXT NOT NULL,
        from_location_id INTEGER,
        to_location_or_customer TEXT,
        product_id INTEGER,
        batch_lot TEXT,
        qty_out REAL NOT NULL,
        unit_cost REAL,
        value_moved REAL,
        handled_by TEXT,
        reference_number TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS waste_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        adjustment_id TEXT,
        date TEXT NOT NULL,
        type TEXT,
        product_id INTEGER,
        batch_lot TEXT,
        location_id INTEGER,
        qty REAL NOT NULL,
        reason TEXT,
        authorized_by TEXT,
        value REAL,
        reference_number TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db.commit()

    # Seed only if empty
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        users = [
            ("admin", generate_password_hash("admin123"), "Administrator", "admin"),
            ("warehouse", generate_password_hash("warehouse1"), "Warehouse Staff", "user"),
            ("retail", generate_password_hash("retail1"), "Retail Staff", "user"),
            ("production", generate_password_hash("prod1"), "Production Staff", "user"),
            ("manager", generate_password_hash("manager1"), "Operations Manager", "admin"),
        ]
        for u in users:
            db.execute("INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)", u)

        for loc in [
            ("Vault A", "Main Storage", "Secured Storage"),
            ("Vault B", "Finished Goods", "Secured Storage"),
            ("Production Room", "Processing", "Production"),
            ("Retail", "Sales Floor", "Retail Display"),
        ]:
            db.execute("INSERT INTO locations (name, area, storage_type, active) VALUES (?,?,?,1)", loc)

        products = [
            ("FLW-001", "Blue Dream", "Flower", "Indoor", 3.5, "g", 20, 25.0, 45.0),
            ("FLW-002", "Ice Cream", "Flower", "Outdoor", 3.5, "g", 20, 22.0, 40.0),
            ("IPR-001", "SEA", "Pre-Roll", "Infused", 1.0, "g", 50, 8.0, 15.0),
            ("IPR-002", "Ganja", "Pre-Roll", "Infused", 1.0, "g", 50, 5.0, 15.0),
            ("CON-001", "Lazy Thrax", "Concentrate", "Live Resin", 1.0, "g", 15, 20.0, 35.0),
            ("CON-002", "Elite", "Concentrate", "Live Resin", 1.0, "g", 15, 15.0, 40.0),
            ("ED-001", "Gummies 10-Pack", "Edible", "Live Resin Edible", 10.0, "ea", 25, 12.0, 25.0),
            ("VAP-001", "Green Crack", "Vape", "Distillate Cart", 1.0, "g", 20, 18.0, 35.0),
            ("PKG-001", "Exit Bag", "Packaging", "Packaging", 1.0, "ea", 100, 0.35, 0.75),
        ]
        for p in products:
            db.execute("""INSERT INTO products 
                (sku, name, category, product_type, unit_size, unit, reorder_level, unit_cost, retail_price, active)
                VALUES (?,?,?,?,?,?,?,?,?,1)""", p)
        db.commit()
        print("Database seeded with default users and products.")
    db.close()

# Initialize DB as soon as the app starts
with app.app_context():
    try:
        init_db()
    except Exception as e:
        print("DB init warning:", e)

class User(UserMixin):
    def __init__(self, id, username, full_name, role):
        self.id = id
        self.username = username
        self.full_name = full_name
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
    if row:
        return User(row["id"], row["username"], row["full_name"], row["role"])
    return None

def get_or_create_inventory(db, product_id, batch_lot, location_id, unit_cost=0):
    batch_lot = batch_lot or ""
    row = db.execute(
        "SELECT id FROM inventory WHERE product_id=? AND batch_lot=? AND location_id=?",
        (product_id, batch_lot, location_id)
    ).fetchone()
    if row:
        return row["id"]
    cur = db.execute(
        "INSERT INTO inventory (product_id, batch_lot, location_id, qty_on_hand, unit_cost, status) VALUES (?,?,?,0,?, 'IN STOCK')",
        (product_id, batch_lot, location_id, unit_cost or 0)
    )
    db.commit()
    return cur.lastrowid

def update_stock(db, product_id, batch_lot, location_id, qty_delta, unit_cost=None):
    inv_id = get_or_create_inventory(db, product_id, batch_lot, location_id, unit_cost)
    db.execute(
        "UPDATE inventory SET qty_on_hand = qty_on_hand + ?, updated_at = ? WHERE id = ?",
        (qty_delta, datetime.now().isoformat(), inv_id)
    )
    if unit_cost is not None:
        db.execute("UPDATE inventory SET unit_cost = ? WHERE id = ?", (unit_cost, inv_id))
    db.execute("""
        UPDATE inventory SET status = CASE
            WHEN qty_on_hand <= 0 THEN 'OUT OF STOCK'
            WHEN qty_on_hand <= (SELECT reorder_level FROM products WHERE id = inventory.product_id) THEN 'LOW STOCK'
            ELSE 'IN STOCK' END
        WHERE id = ?""", (inv_id,))
    db.commit()

# ---------- Routes ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            user = User(row["id"], row["username"], row["full_name"], row["role"])
            login_user(user)
            flash(f"Welcome, {user.full_name or user.username}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    db = get_db()
    total_value = db.execute("SELECT COALESCE(SUM(qty_on_hand * unit_cost), 0) FROM inventory WHERE qty_on_hand > 0").fetchone()[0]
    total_units = db.execute("SELECT COALESCE(SUM(qty_on_hand), 0) FROM inventory WHERE qty_on_hand > 0").fetchone()[0]
    low_stock = db.execute("SELECT COUNT(*) FROM inventory WHERE status = 'LOW STOCK'").fetchone()[0]
    out_of_stock = db.execute("SELECT COUNT(*) FROM inventory WHERE status = 'OUT OF STOCK' OR qty_on_hand <= 0").fetchone()[0]

    by_category = db.execute("""
        SELECT p.category, COALESCE(SUM(i.qty_on_hand * i.unit_cost), 0) as value,
               COALESCE(SUM(i.qty_on_hand), 0) as units
        FROM inventory i JOIN products p ON p.id = i.product_id
        WHERE i.qty_on_hand > 0 GROUP BY p.category ORDER BY value DESC
    """).fetchall()

    recent_receiving = db.execute("""
        SELECT r.*, p.name as product_name, p.sku, l.name as location_name
        FROM receiving r
        LEFT JOIN products p ON p.id = r.product_id
        LEFT JOIN locations l ON l.id = r.location_id
        ORDER BY r.date DESC, r.id DESC LIMIT 5
    """).fetchall()

    low_items = db.execute("""
        SELECT i.*, p.sku, p.name, p.reorder_level, l.name as location_name
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        JOIN locations l ON l.id = i.location_id
        WHERE i.status IN ('LOW STOCK', 'OUT OF STOCK')
        ORDER BY i.qty_on_hand ASC LIMIT 10
    """).fetchall()

    return render_template("dashboard.html", total_value=total_value, total_units=total_units,
                           low_stock=low_stock, out_of_stock=out_of_stock, by_category=by_category,
                           recent_receiving=recent_receiving, low_items=low_items)

@app.route("/products")
@login_required
def products():
    db = get_db()
    rows = db.execute("SELECT * FROM products ORDER BY category, name").fetchall()
    return render_template("products.html", products=rows)

@app.route("/products/add", methods=["GET", "POST"])
@login_required
def product_add():
    if request.method == "POST":
        db = get_db()
        try:
            db.execute("""INSERT INTO products (sku, name, category, product_type, unit_size, unit,
                supplier, reorder_level, unit_cost, retail_price, notes, active)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (request.form["sku"].strip().upper(), request.form["name"].strip(),
                 request.form["category"], request.form.get("product_type", ""),
                 float(request.form.get("unit_size") or 0), request.form.get("unit", "g"),
                 request.form.get("supplier", ""), float(request.form.get("reorder_level") or 0),
                 float(request.form.get("unit_cost") or 0), float(request.form.get("retail_price") or 0),
                 request.form.get("notes", ""), 1 if request.form.get("active") else 0))
            db.commit()
            flash("Product added.", "success")
            return redirect(url_for("products"))
        except sqlite3.IntegrityError:
            flash("SKU already exists.", "danger")
    categories = ["Flower", "Pre-Roll", "Concentrate", "Edible", "Vape", "Packaging"]
    return render_template("product_form.html", product=None, categories=categories)

@app.route("/products/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def product_edit(pid):
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("products"))
    if request.method == "POST":
        db.execute("""UPDATE products SET sku=?, name=?, category=?, product_type=?, unit_size=?, unit=?,
            supplier=?, reorder_level=?, unit_cost=?, retail_price=?, notes=?, active=? WHERE id=?""",
            (request.form["sku"].strip().upper(), request.form["name"].strip(),
             request.form["category"], request.form.get("product_type", ""),
             float(request.form.get("unit_size") or 0), request.form.get("unit", "g"),
             request.form.get("supplier", ""), float(request.form.get("reorder_level") or 0),
             float(request.form.get("unit_cost") or 0), float(request.form.get("retail_price") or 0),
             request.form.get("notes", ""), 1 if request.form.get("active") else 0, pid))
        db.commit()
        flash("Product updated.", "success")
        return redirect(url_for("products"))
    categories = ["Flower", "Pre-Roll", "Concentrate", "Edible", "Vape", "Packaging"]
    return render_template("product_form.html", product=product, categories=categories)

@app.route("/inventory")
@login_required
def inventory():
    db = get_db()
    rows = db.execute("""
        SELECT i.*, p.sku, p.name, p.category, p.unit, p.reorder_level, l.name as location_name
        FROM inventory i
        JOIN products p ON p.id = i.product_id
        JOIN locations l ON l.id = i.location_id
        ORDER BY p.category, p.name, i.batch_lot, l.name
    """).fetchall()
    return render_template("inventory.html", items=rows)

@app.route("/receiving")
@login_required
def receiving_list():
    db = get_db()
    rows = db.execute("""
        SELECT r.*, p.sku, p.name as product_name, l.name as location_name
        FROM receiving r
        LEFT JOIN products p ON p.id = r.product_id
        LEFT JOIN locations l ON l.id = r.location_id
        ORDER BY r.date DESC, r.id DESC
    """).fetchall()
    return render_template("receiving_list.html", records=rows)

@app.route("/receiving/add", methods=["GET", "POST"])
@login_required
def receiving_add():
    db = get_db()
    products = db.execute("SELECT id, sku, name, unit_cost FROM products WHERE active=1 ORDER BY name").fetchall()
    locations = db.execute("SELECT id, name FROM locations WHERE active=1 ORDER BY name").fetchall()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        batch = request.form.get("batch_lot", "").strip()
        location_id = int(request.form["location_id"])
        qty = float(request.form["qty_received"])
        unit_cost = float(request.form.get("unit_cost") or 0)
        total_cost = qty * unit_cost
        db.execute("""INSERT INTO receiving (receipt_id, date, supplier, product_id, batch_lot, location_id,
            qty_received, unit_cost, total_cost, received_by, reference_number, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (request.form.get("receipt_id") or f"REC-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
             request.form["date"], request.form.get("supplier", ""), product_id, batch, location_id,
             qty, unit_cost, total_cost, current_user.full_name or current_user.username,
             request.form.get("reference_number", ""), request.form.get("notes", "")))
        update_stock(db, product_id, batch, location_id, qty, unit_cost)
        db.commit()
        flash("Receiving recorded and stock updated.", "success")
        return redirect(url_for("receiving_list"))
    return render_template("receiving_form.html", products=products, locations=locations, today=date.today().isoformat())

@app.route("/transfers")
@login_required
def transfers_list():
    db = get_db()
    rows = db.execute("""
        SELECT t.*, p.sku, p.name as product_name, l.name as from_location
        FROM transfers_sales t
        LEFT JOIN products p ON p.id = t.product_id
        LEFT JOIN locations l ON l.id = t.from_location_id
        ORDER BY t.date DESC, t.id DESC
    """).fetchall()
    return render_template("transfers_list.html", records=rows)

@app.route("/transfers/add", methods=["GET", "POST"])
@login_required
def transfers_add():
    db = get_db()
    products = db.execute("SELECT id, sku, name FROM products WHERE active=1 ORDER BY name").fetchall()
    locations = db.execute("SELECT id, name FROM locations WHERE active=1 ORDER BY name").fetchall()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        batch = request.form.get("batch_lot", "").strip()
        from_loc = int(request.form["from_location_id"])
        qty = float(request.form["qty_out"])
        unit_cost = float(request.form.get("unit_cost") or 0)
        value = qty * unit_cost
        tx_type = request.form["type"]

        inv = db.execute("SELECT qty_on_hand FROM inventory WHERE product_id=? AND batch_lot=? AND location_id=?",
                         (product_id, batch, from_loc)).fetchone()
        if not inv or inv["qty_on_hand"] < qty:
            flash("Not enough stock in that location/batch.", "danger")
            return redirect(url_for("transfers_add"))

        db.execute("""INSERT INTO transfers_sales (transaction_id, date, type, from_location_id,
            to_location_or_customer, product_id, batch_lot, qty_out, unit_cost, value_moved,
            handled_by, reference_number, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (request.form.get("transaction_id") or f"TXN-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
             request.form["date"], tx_type, from_loc, request.form.get("to_location_or_customer", ""),
             product_id, batch, qty, unit_cost, value, current_user.full_name or current_user.username,
             request.form.get("reference_number", ""), request.form.get("notes", "")))
        update_stock(db, product_id, batch, from_loc, -qty)
        if tx_type == "Transfer":
            to_name = request.form.get("to_location_or_customer", "").strip()
            to_loc = db.execute("SELECT id FROM locations WHERE name = ?", (to_name,)).fetchone()
            if to_loc:
                update_stock(db, product_id, batch, to_loc["id"], qty, unit_cost)
        db.commit()
        flash(f"{tx_type} recorded and stock updated.", "success")
        return redirect(url_for("transfers_list"))
    return render_template("transfers_form.html", products=products, locations=locations, today=date.today().isoformat())

@app.route("/waste")
@login_required
def waste_list():
    db = get_db()
    rows = db.execute("""
        SELECT w.*, p.sku, p.name as product_name, l.name as location_name
        FROM waste_adjustments w
        LEFT JOIN products p ON p.id = w.product_id
        LEFT JOIN locations l ON l.id = w.location_id
        ORDER BY w.date DESC, w.id DESC
    """).fetchall()
    return render_template("waste_list.html", records=rows)

@app.route("/waste/add", methods=["GET", "POST"])
@login_required
def waste_add():
    db = get_db()
    products = db.execute("SELECT id, sku, name FROM products WHERE active=1 ORDER BY name").fetchall()
    locations = db.execute("SELECT id, name FROM locations WHERE active=1 ORDER BY name").fetchall()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        batch = request.form.get("batch_lot", "").strip()
        location_id = int(request.form["location_id"])
        qty = float(request.form["qty"])
        unit_cost = float(request.form.get("unit_cost") or 0)
        db.execute("""INSERT INTO waste_adjustments (adjustment_id, date, type, product_id, batch_lot,
            location_id, qty, reason, authorized_by, value, reference_number, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (request.form.get("adjustment_id") or f"ADJ-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
             request.form["date"], request.form.get("type", "Waste"), product_id, batch, location_id,
             qty, request.form.get("reason", ""), current_user.full_name or current_user.username,
             qty * unit_cost, request.form.get("reference_number", ""), request.form.get("notes", "")))
        update_stock(db, product_id, batch, location_id, -qty)
        db.commit()
        flash("Waste/Adjustment recorded.", "success")
        return redirect(url_for("waste_list"))
    return render_template("waste_form.html", products=products, locations=locations, today=date.today().isoformat())

@app.route("/locations")
@login_required
def locations():
    db = get_db()
    rows = db.execute("SELECT * FROM locations ORDER BY name").fetchall()
    return render_template("locations.html", locations=rows)

@app.route("/locations/add", methods=["POST"])
@login_required
def location_add():
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        try:
            db.execute("INSERT INTO locations (name, area, storage_type, active) VALUES (?,?,?,1)",
                       (name, request.form.get("area", ""), request.form.get("storage_type", "")))
            db.commit()
            flash("Location added.", "success")
        except sqlite3.IntegrityError:
            flash("Location already exists.", "danger")
    return redirect(url_for("locations"))

@app.route("/users")
@login_required
def users_page():
    if current_user.role != "admin":
        flash("Admin access required.", "warning")
        return redirect(url_for("dashboard"))
    db = get_db()
    rows = db.execute("SELECT id, username, full_name, role, active FROM users ORDER BY username").fetchall()
    return render_template("users.html", users=rows)

# Health check for Render
@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
from flask import Flask, render_template, request, redirect, url_for, flash, g, Response
from datetime import datetime, date, timedelta
import csv
import io
