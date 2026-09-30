
import os, json, math, sqlite3, time, datetime as dt, urllib.parse, urllib.request
from flask import Flask, g, request, session, redirect, url_for, render_template, jsonify

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(APP_DIR, "delivery.db"))
GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS restaurants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, pin TEXT NOT NULL,
  address TEXT NOT NULL, phone TEXT, lat REAL, lng REAL,
  hours TEXT NOT NULL, closed_override INTEGER NOT NULL DEFAULT 0,
  prep_default INTEGER NOT NULL DEFAULT 15);

CREATE TABLE IF NOT EXISTS menu_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL, name TEXT NOT NULL,
  description TEXT, price_cents INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS drivers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, pin TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'offline',
  pending_request TEXT,
  max_stack INTEGER NOT NULL DEFAULT 3,
  last_seen TEXT);

CREATE TABLE IF NOT EXISTS dispatchers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  restaurant_id INTEGER NOT NULL,
  customer_name TEXT NOT NULL, customer_phone TEXT NOT NULL,
  address TEXT NOT NULL, address_note TEXT, lat REAL, lng REAL,
  items TEXT NOT NULL,
  subtotal_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL,
  tax_cents INTEGER NOT NULL DEFAULT 0, tip_cents INTEGER NOT NULL DEFAULT 0,
  total_cents INTEGER NOT NULL,
  miles REAL NOT NULL DEFAULT 0,
  kitchen_status TEXT NOT NULL DEFAULT 'pending',   -- pending|preparing|ready
  dispatch_status TEXT NOT NULL DEFAULT 'held',     -- held|queued|assigned|picked_up|delivered|cancelled
  hold_reason TEXT,
  driver_id INTEGER, stack_seq INTEGER,
  prep_minutes INTEGER, prep_started TEXT, ready_at TEXT,
  placed_by TEXT NOT NULL DEFAULT 'customer',
  delivered_at TEXT,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  driver_id INTEGER NOT NULL, sender TEXT NOT NULL,
  body TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS geocache (
  q TEXT PRIMARY KEY, formatted TEXT, lat REAL, lng REAL, ok INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, detail TEXT, created_at TEXT);
"""

DEFAULT_HOURS = {str(i): ["10:00", "21:00"] for i in range(7)}

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()

def now():
    return dt.datetime.now().isoformat(timespec="seconds")

def log(kind, detail):
    db().execute("INSERT INTO events(kind,detail,created_at) VALUES(?,?,?)", (kind, detail, now()))

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cur = con.execute("SELECT COUNT(*) c FROM restaurants")
    if cur.fetchone()["c"] == 0:
        seed(con)
    for k, v in [("base_fee_cents", "399"), ("base_miles", "3"), ("per_mile_cents", "100"),
                 ("tax_rate_bp", "900"), ("auto_assign", "1"), ("max_stack_default", "3")]:
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    con.commit()
    con.close()

def seed(con):
    hours = json.dumps(DEFAULT_HOURS)
    rows = [
        ("Tiger Town Grill", "tigertown", "1111", "2300 Interstate Dr, Opelika, AL 36801",
         "334-555-0140", 32.6423, -85.3487, hours, 15),
        ("Auburn Noodle House", "noodle", "1111", "160 N College St, Auburn, AL 36830",
         "334-555-0172", 32.6099, -85.4808, hours, 20),
        ("Valley Smokehouse", "valleybbq", "1111", "3000 20th Ave, Valley, AL 36854",
         "334-555-0190", 32.8104, -85.1805, hours, 25),
    ]
    for r in rows:
        con.execute("""INSERT INTO restaurants(name,slug,pin,address,phone,lat,lng,hours,prep_default)
                       VALUES(?,?,?,?,?,?,?,?,?)""", r)
    menus = {
        1: [("Tiger Burger", "Double patty, pimento cheese, fries", 1099),
            ("Fried Catfish Plate", "Two filets, slaw, hushpuppies", 1399),
            ("Chicken Tenders", "Four tenders, honey mustard", 949),
            ("House Salad", "Greens, tomato, cucumber", 749),
            ("Sweet Tea (32oz)", "", 299)],
        2: [("Beef Pho", "Rice noodle, brisket, herbs", 1249),
            ("Pad Thai", "Shrimp or chicken", 1199),
            ("Pork Dumplings", "Eight, steamed or fried", 799),
            ("Spring Rolls", "Two, peanut sauce", 599),
            ("Thai Iced Tea", "", 449)],
        3: [("Pulled Pork Plate", "Two sides, white bread", 1349),
            ("Half Rack Ribs", "Dry rub, sauce on the side", 1899),
            ("Smoked Wings", "Eight, alabama white", 1199),
            ("Brunswick Stew", "Pint", 699),
            ("Banana Pudding", "", 499)],
    }
    for rid, items in menus.items():
        for name, desc, price in items:
            con.execute("""INSERT INTO menu_items(restaurant_id,name,description,price_cents)
                           VALUES(?,?,?,?)""", (rid, name, desc, price))
    con.execute("INSERT INTO dispatchers(name,username,password) VALUES(?,?,?)",
                ("Dispatch Desk", "admin", "dispatch123"))
    for name, phone in [("Marcus Hill", "3345550111"), ("Dana Reed", "3345550122"),
                        ("Chris Boyd", "3345550133")]:
        con.execute("INSERT INTO drivers(name,phone,pin) VALUES(?,?,?)", (name, phone, "1234"))
    demo = [
        ("2302 waverly parkway, opelika, al 36801", "2302 Waverly Pkwy, Opelika, AL 36801", 32.6514, -85.3968),
        ("600 s college st, auburn, al 36832", "600 S College St, Auburn, AL 36832", 32.5932, -85.4855),
        ("1700 fob james dr, valley, al 36854", "1700 Fob James Dr, Valley, AL 36854", 32.8172, -85.1839),
    ]
    for q, f, la, ln in demo:
        con.execute("INSERT OR REPLACE INTO geocache(q,formatted,lat,lng,ok) VALUES(?,?,?,?,1)",
                    (q, f, la, ln))

def setting(key, cast=int):
    row = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return cast(row["value"]) if row else None

# ---------------------------------------------------------------- geo + fees

def haversine_miles(a_lat, a_lng, b_lat, b_lng):
    R = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(h))

ROAD_FACTOR = 1.3   # straight-line -> driving estimate when no routing key is set

def geocode(raw):
    """Validate + normalise a customer address. Returns dict(ok, formatted, lat, lng, source)."""
    q = " ".join(raw.lower().split())
    row = db().execute("SELECT * FROM geocache WHERE q=?", (q,)).fetchone()
    if row:
        return {"ok": bool(row["ok"]), "formatted": row["formatted"],
                "lat": row["lat"], "lng": row["lng"], "source": "cache"}
    res = None
    try:
        if GOOGLE_KEY:
            url = ("https://maps.googleapis.com/maps/api/geocode/json?address="
                   + urllib.parse.quote(raw) + "&key=" + GOOGLE_KEY)
            data = json.loads(urllib.request.urlopen(url, timeout=8).read())
            if data.get("status") == "OK":
                top = data["results"][0]
                loc = top["geometry"]["location"]
                res = {"ok": True, "formatted": top["formatted_address"],
                       "lat": loc["lat"], "lng": loc["lng"], "source": "google"}
        else:
            url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1&q="
                   + urllib.parse.quote(raw))
            req = urllib.request.Request(url, headers={"User-Agent": "fleetfoot-delivery/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read())
            if data:
                top = data[0]
                res = {"ok": True, "formatted": top["display_name"],
                       "lat": float(top["lat"]), "lng": float(top["lon"]), "source": "osm"}
    except Exception:
        res = None
    if res is None:
        return {"ok": False, "formatted": None, "lat": None, "lng": None, "source": "none"}
    db().execute("INSERT OR REPLACE INTO geocache(q,formatted,lat,lng,ok) VALUES(?,?,?,?,1)",
                 (q, res["formatted"], res["lat"], res["lng"]))
    db().commit()
    return res

def fee_for_miles(miles):
    base_fee = setting("base_fee_cents")
    base_miles = setting("base_miles")
    per_mile = setting("per_mile_cents")
    if miles <= base_miles:
        return base_fee
    return base_fee + int(math.ceil(miles - base_miles)) * per_mile

def quote(restaurant, lat, lng):
    miles = round(haversine_miles(restaurant["lat"], restaurant["lng"], lat, lng) * ROAD_FACTOR, 2)
    return miles, fee_for_miles(miles)

def money(cents):
    return "${:,.2f}".format((cents or 0) / 100.0)

app.jinja_env.filters["money"] = money

# ---------------------------------------------------------------- hours

WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def is_open(restaurant, when=None):
    if restaurant["closed_override"]:
        return False
    when = when or dt.datetime.now()
    hours = json.loads(restaurant["hours"])
    span = hours.get(str(when.weekday()))
    if not span or span[0] == "" or span[1] == "":
        return False
    o = dt.datetime.strptime(span[0], "%H:%M").time()
    c = dt.datetime.strptime(span[1], "%H:%M").time()
    t = when.time()
    return o <= t <= c if o <= c else (t >= o or t <= c)

def hours_label(restaurant):
    hours = json.loads(restaurant["hours"])
    span = hours.get(str(dt.datetime.now().weekday()))
    if not span or not span[0]:
        return "Closed today"
    return "Today " + span[0] + " - " + span[1]

# ---------------------------------------------------------------- queue / dispatch

def available_drivers():
    rows = db().execute("""
        SELECT d.*, (SELECT COUNT(*) FROM orders o
                     WHERE o.driver_id=d.id AND o.dispatch_status IN ('assigned','received','at_restaurant','enroute')) AS load
        FROM drivers d WHERE d.status='online' ORDER BY load ASC, d.id ASC""").fetchall()
    return [r for r in rows if r["load"] < r["max_stack"]]

def recompute_queue():
    """Queue = orders with no driver yet, oldest first. Held when no driver has capacity."""
    con = db()
    waiting = con.execute("""SELECT * FROM orders
                             WHERE dispatch_status IN ('queued','held')
                             ORDER BY created_at ASC, id ASC""").fetchall()
    free = available_drivers()
    capacity = sum(d["max_stack"] - d["load"] for d in free)
    for i, o in enumerate(waiting):
        status = "queued" if i < capacity else "held"
        reason = None if status == "queued" else "no driver available"
        con.execute("UPDATE orders SET dispatch_status=?, hold_reason=? WHERE id=?",
                    (status, reason, o["id"]))
    con.commit()

def queue_position(order_id):
    rows = db().execute("""SELECT id FROM orders WHERE dispatch_status IN ('queued','held')
                           ORDER BY created_at ASC, id ASC""").fetchall()
    for i, r in enumerate(rows):
        if r["id"] == order_id:
            return i + 1
    return None

def auto_assign():
    if not setting("auto_assign"):
        recompute_queue()
        return
    con = db()
    while True:
        free = available_drivers()
        if not free:
            break
        o = con.execute("""SELECT * FROM orders
                           WHERE driver_id IS NULL AND dispatch_status IN ('queued','held')
                             AND kitchen_status IN ('preparing','ready')
                           ORDER BY created_at ASC LIMIT 1""").fetchone()
        if not o:
            break
        d = free[0]
        seq = con.execute("""SELECT COALESCE(MAX(stack_seq),0)+1 s FROM orders
                             WHERE driver_id=? AND dispatch_status IN ('assigned','received','at_restaurant','enroute')""",
                          (d["id"],)).fetchone()["s"]
        con.execute("""UPDATE orders SET driver_id=?, dispatch_status='assigned',
                       hold_reason=NULL, stack_seq=? WHERE id=?""", (d["id"], seq, o["id"]))
        con.execute("""INSERT INTO messages(driver_id,sender,body,created_at) VALUES(?,?,?,?)""",
                    (d["id"], "system",
                     "Order " + o["code"] + " assigned to you (stop #" + str(seq) + ").", now()))
        log("assign", o["code"] + " -> " + d["name"])
        con.commit()
    recompute_queue()

def nav_url(dest, lat=None, lng=None):
    target = (str(lat) + "," + str(lng)) if lat and lng else dest
    return "https://www.google.com/maps/dir/?api=1&travelmode=driving&destination=" + urllib.parse.quote(target)

app.jinja_env.globals["nav_url"] = nav_url

def order_dict(o):
    r = db().execute("SELECT * FROM restaurants WHERE id=?", (o["restaurant_id"],)).fetchone()
    d = db().execute("SELECT * FROM drivers WHERE id=?", (o["driver_id"],)).fetchone() if o["driver_id"] else None
    eta = None
    if o["prep_started"] and o["prep_minutes"]:
        end = dt.datetime.fromisoformat(o["prep_started"]) + dt.timedelta(minutes=o["prep_minutes"])
        eta = int((end - dt.datetime.now()).total_seconds())
    return {
        "id": o["id"], "code": o["code"], "restaurant": r["name"], "restaurant_address": r["address"],
        "restaurant_nav": nav_url(r["address"], r["lat"], r["lng"]),
        "customer": o["customer_name"], "phone": o["customer_phone"],
        "address": o["address"], "note": o["address_note"],
        "customer_nav": nav_url(o["address"], o["lat"], o["lng"]),
        "items": json.loads(o["items"]),
        "subtotal": money(o["subtotal_cents"]), "fee": money(o["fee_cents"]),
        "tax": money(o["tax_cents"]), "tip": money(o["tip_cents"]), "total": money(o["total_cents"]),
        "miles": o["miles"], "kitchen_status": o["kitchen_status"],
        "dispatch_status": o["dispatch_status"], "hold_reason": o["hold_reason"],
        "driver": d["name"] if d else None, "driver_id": o["driver_id"], "stack_seq": o["stack_seq"],
        "prep_minutes": o["prep_minutes"], "timer_seconds": eta,
        "placed_by": o["placed_by"], "created_at": o["created_at"],
        "delivered_at": o["delivered_at"],
        "queue_position": queue_position(o["id"]),
    }

# ---------------------------------------------------------------- customer site

@app.route("/")
def home():
    rs = db().execute("SELECT * FROM restaurants ORDER BY name").fetchall()
    cards = [{"r": r, "open": is_open(r), "hours": hours_label(r)} for r in rs]
    return render_template("index.html", cards=cards)

@app.route("/r/<slug>")
def menu(slug):
    r = db().execute("SELECT * FROM restaurants WHERE slug=?", (slug,)).fetchone()
    if not r:
        return redirect(url_for("home"))
    items = db().execute("SELECT * FROM menu_items WHERE restaurant_id=? AND active=1", (r["id"],)).fetchall()
    return render_template("menu.html", r=r, items=items, open=is_open(r), hours=hours_label(r),
                           base_fee=money(setting("base_fee_cents")),
                           base_miles=setting("base_miles"),
                           per_mile=money(setting("per_mile_cents")))

@app.post("/api/quote")
def api_quote():
    data = request.get_json(force=True)
    r = db().execute("SELECT * FROM restaurants WHERE id=?", (data.get("restaurant_id"),)).fetchone()
    if not r:
        return jsonify({"ok": False, "error": "Unknown restaurant"}), 400
    g1 = geocode(data.get("address", ""))
    if not g1["ok"]:
        return jsonify({"ok": False, "error": "We could not verify that address. Add the city, state and ZIP."})
    miles, fee = quote(r, g1["lat"], g1["lng"])
    return jsonify({"ok": True, "formatted": g1["formatted"], "lat": g1["lat"], "lng": g1["lng"],
                    "miles": miles, "fee_cents": fee, "fee": money(fee)})

@app.post("/checkout")
def checkout():
    payload = request.get_json(force=True)
    r = db().execute("SELECT * FROM restaurants WHERE id=?", (payload["restaurant_id"],)).fetchone()
    if not r:
        return jsonify({"ok": False, "error": "Unknown restaurant"}), 400
    if not is_open(r) and payload.get("placed_by", "customer") == "customer":
        return jsonify({"ok": False, "error": r["name"] + " is closed right now."}), 400
    g1 = geocode(payload["address"])
    if not g1["ok"]:
        return jsonify({"ok": False, "error": "Address could not be validated."}), 400
    items = payload["items"]
    subtotal = sum(i["price_cents"] * i["qty"] for i in items)
    if subtotal <= 0:
        return jsonify({"ok": False, "error": "Your cart is empty."}), 400
    miles, fee = quote(r, g1["lat"], g1["lng"])
    tax = int(round(subtotal * setting("tax_rate_bp") / 10000.0))
    tip = int(payload.get("tip_cents", 0))
    total = subtotal + fee + tax + tip
    code = "FF" + dt.datetime.now().strftime("%H%M%S") + str(int(time.time() * 1000) % 97)
    cur = db().execute("""INSERT INTO orders(code,restaurant_id,customer_name,customer_phone,address,
        address_note,lat,lng,items,subtotal_cents,fee_cents,tax_cents,tip_cents,total_cents,miles,
        kitchen_status,dispatch_status,hold_reason,placed_by,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending','held','waiting on kitchen',?,?)""",
        (code, r["id"], payload["customer_name"], payload["customer_phone"], g1["formatted"],
         payload.get("note", ""), g1["lat"], g1["lng"], json.dumps(items), subtotal, fee, tax, tip,
         total, miles, payload.get("placed_by", "customer"), now()))
    db().commit()
    log("order", code + " placed for " + r["name"])
    recompute_queue()
    return jsonify({"ok": True, "code": code, "order_id": cur.lastrowid, "total": money(total)})

@app.route("/track/<code>")
def track(code):
    o = db().execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
    if not o:
        return render_template("track.html", order=None, code=code)
    return render_template("track.html", order=order_dict(o), code=code)

@app.get("/api/track/<code>")
def api_track(code):
    o = db().execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
    if not o:
        return jsonify({"ok": False}), 404
    return jsonify({"ok": True, "order": order_dict(o)})

# ---------------------------------------------------------------- dispatcher

def dispatcher_required():
    return session.get("dispatcher_id") is not None

@app.route("/dispatch/login", methods=["GET", "POST"])
def dispatch_login():
    err = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        row = db().execute("SELECT * FROM dispatchers WHERE username=? AND password=?", (u, p)).fetchone()
        if row:
            session["dispatcher_id"] = row["id"]
            session["dispatcher_name"] = row["name"]
            return redirect(url_for("dispatch"))
        err = "Wrong username or password."
    return render_template("dispatch_login.html", err=err)

@app.route("/dispatch/logout")
def dispatch_logout():
    session.pop("dispatcher_id", None)
    return redirect(url_for("dispatch_login"))

@app.route("/dispatch")
def dispatch():
    if not dispatcher_required():
        return redirect(url_for("dispatch_login"))
    return render_template("dispatch.html")

@app.get("/api/dispatch/board")
def api_board():
    if not dispatcher_required():
        return jsonify({"ok": False}), 403
    recompute_queue()
    live = db().execute("""SELECT * FROM orders WHERE dispatch_status!='delivered'
                           AND dispatch_status!='cancelled' ORDER BY created_at ASC""").fetchall()
    done = db().execute("""SELECT * FROM orders WHERE dispatch_status IN ('delivered','cancelled')
                           ORDER BY COALESCE(delivered_at, created_at) DESC LIMIT 30""").fetchall()
    drivers = db().execute("""SELECT d.*, (SELECT COUNT(*) FROM orders o WHERE o.driver_id=d.id
                              AND o.dispatch_status IN ('assigned','received','at_restaurant','enroute')) load
                              FROM drivers d ORDER BY d.name""").fetchall()
    return jsonify({
        "ok": True,
        "orders": [order_dict(o) for o in live],
        "completed": [order_dict(o) for o in done],
        "drivers": [{"id": d["id"], "name": d["name"], "phone": d["phone"], "status": d["status"],
                     "pending_request": d["pending_request"], "load": d["load"],
                     "max_stack": d["max_stack"]} for d in drivers],
    })

@app.post("/api/dispatch/driver-status")
def api_driver_status():
    if not dispatcher_required():
        return jsonify({"ok": False}), 403
    data = request.get_json(force=True)
    status = data["status"]
    if status not in ("online", "break", "offline"):
        return jsonify({"ok": False, "error": "bad status"}), 400
    set_driver_status(data["driver_id"], status, "Dispatch set you " + status + ".")
    return jsonify({"ok": True})

@app.post("/api/dispatch/assign")
def api_assign():
    if not dispatcher_required():
        return jsonify({"ok": False}), 403
    data = request.get_json(force=True)
    oid, did = data["order_id"], data.get("driver_id")
    if did in (None, "", 0, "0"):
        db().execute("""UPDATE orders SET driver_id=NULL, stack_seq=NULL, dispatch_status='queued'
                        WHERE id=?""", (oid,))
    else:
        seq = db().execute("""SELECT COALESCE(MAX(stack_seq),0)+1 s FROM orders WHERE driver_id=?
                              AND dispatch_status IN ('assigned','picked_up')""", (did,)).fetchone()["s"]
        db().execute("""UPDATE orders SET driver_id=?, dispatch_status='assigned', hold_reason=NULL,
                        stack_seq=? WHERE id=?""", (did, seq, oid))
        o = db().execute("SELECT code FROM orders WHERE id=?", (oid,)).fetchone()
        db().execute("INSERT INTO messages(driver_id,sender,body,created_at) VALUES(?,?,?,?)",
                     (did, "dispatch", "You have order " + o["code"] + " (stop #" + str(seq) + ").", now()))
    db().commit()
    recompute_queue()
    return jsonify({"ok": True})

@app.post("/api/order/status")
def api_order_status():
    data = request.get_json(force=True)
    oid = data["order_id"]
    o = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        return jsonify({"ok": False}), 404
    if session.get("driver_id") and not dispatcher_required() and not session.get("restaurant_id"):
        if o["driver_id"] != session["driver_id"]:
            return jsonify({"ok": False, "error": "not your order"}), 403
    k = data.get("kitchen_status")
    d = data.get("dispatch_status")
    if k in ("pending", "preparing", "ready"):
        if k == "preparing":
            mins = int(data.get("prep_minutes") or 15)
            db().execute("UPDATE orders SET kitchen_status=?, prep_minutes=?, prep_started=? WHERE id=?",
                         (k, mins, now(), oid))
        elif k == "ready":
            db().execute("UPDATE orders SET kitchen_status=?, ready_at=? WHERE id=?", (k, now(), oid))
        else:
            db().execute("UPDATE orders SET kitchen_status=? WHERE id=?", (k, oid))
    if d in ("held", "queued", "assigned", "received", "at_restaurant", "enroute",
             "delivered", "cancelled"):
        db().execute("UPDATE orders SET dispatch_status=? WHERE id=?", (d, oid))
        if d in ("delivered", "cancelled"):
            db().execute("UPDATE orders SET stack_seq=NULL, delivered_at=? WHERE id=?", (now(), oid))
            if o["driver_id"]:
                db().execute("""INSERT INTO messages(driver_id,sender,body,created_at)
                                VALUES(?,?,?,?)""",
                             (o["driver_id"], "system",
                              "Order " + o["code"] + " marked " + d + ".", now()))
    db().commit()
    auto_assign()
    return jsonify({"ok": True})

@app.post("/api/order/hold")
def api_hold():
    data = request.get_json(force=True)
    db().execute("""UPDATE orders SET dispatch_status='held', hold_reason=?, driver_id=NULL,
                    stack_seq=NULL WHERE id=?""",
                 (data.get("reason", "held by dispatch"), data["order_id"]))
    db().commit()
    return jsonify({"ok": True})

@app.route("/dispatch/restaurants", methods=["GET", "POST"])
def dispatch_restaurants():
    if not dispatcher_required():
        return redirect(url_for("dispatch_login"))
    saved = False
    if request.method == "POST":
        rid = request.form["restaurant_id"]
        hours = {}
        for i in range(7):
            o = request.form.get("open_" + str(i), "")
            c = request.form.get("close_" + str(i), "")
            hours[str(i)] = [o, c] if o and c else ["", ""]
        db().execute("""UPDATE restaurants SET hours=?, closed_override=?, prep_default=?, phone=?
                        WHERE id=?""",
                     (json.dumps(hours), 1 if request.form.get("closed_override") else 0,
                      int(request.form.get("prep_default") or 15), request.form.get("phone", ""), rid))
        db().commit()
        saved = True
    rs = db().execute("SELECT * FROM restaurants ORDER BY name").fetchall()
    data = [{"r": r, "hours": json.loads(r["hours"]), "open": is_open(r)} for r in rs]
    return render_template("dispatch_restaurants.html", data=data, week=WEEK, saved=saved)

@app.route("/dispatch/settings", methods=["GET", "POST"])
def dispatch_settings():
    if not dispatcher_required():
        return redirect(url_for("dispatch_login"))
    saved = False
    if request.method == "POST":
        for key in ("base_fee_cents", "base_miles", "per_mile_cents", "tax_rate_bp", "auto_assign"):
            if key in request.form:
                db().execute("UPDATE settings SET value=? WHERE key=?", (request.form[key], key))
        db().commit()
        saved = True
    rows = db().execute("SELECT * FROM settings").fetchall()
    return render_template("dispatch_settings.html", s={r["key"]: r["value"] for r in rows}, saved=saved)

# ---------------------------------------------------------------- chat

@app.get("/api/chat/<int:driver_id>")
def api_chat(driver_id):
    rows = db().execute("""SELECT * FROM messages WHERE driver_id=? ORDER BY id DESC LIMIT 60""",
                        (driver_id,)).fetchall()
    msgs = [{"sender": r["sender"], "body": r["body"], "at": r["created_at"][11:16]}
            for r in reversed(rows)]
    return jsonify({"ok": True, "messages": msgs})

@app.post("/api/chat/<int:driver_id>")
def api_chat_send(driver_id):
    data = request.get_json(force=True)
    sender = data.get("sender", "dispatch")
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"ok": False}), 400
    db().execute("INSERT INTO messages(driver_id,sender,body,created_at) VALUES(?,?,?,?)",
                 (driver_id, sender, body, now()))
    db().commit()
    low = body.lower()
    if sender == "driver":
        want = None
        if "online" in low or "clock in" in low or "ready to roll" in low:
            want = "online"
        elif "break" in low or "lunch" in low:
            want = "break"
        elif "offline" in low or "clock out" in low or "done for" in low:
            want = "offline"
        if want:
            request_status(driver_id, want)
    return jsonify({"ok": True})


def request_status(driver_id, want):
    """A driver can only ASK. Dispatch is the one who flips the switch."""
    db().execute("UPDATE drivers SET pending_request=? WHERE id=?", (want, driver_id))
    db().execute("INSERT INTO messages(driver_id,sender,body,created_at) VALUES(?,?,?,?)",
                 (driver_id, "system",
                  "Request sent to dispatch: " + want + ". Waiting on dispatch to approve.", now()))
    db().commit()


def set_driver_status(driver_id, status, reply):
    """Dispatch-only. Nothing in the driver app calls this directly."""
    db().execute("UPDATE drivers SET status=?, pending_request=NULL, last_seen=? WHERE id=?",
                 (status, now(), driver_id))
    db().execute("INSERT INTO messages(driver_id,sender,body,created_at) VALUES(?,?,?,?)",
                 (driver_id, "dispatch", reply, now()))
    db().commit()
    if status != "online":
        db().execute("""UPDATE orders SET driver_id=NULL, stack_seq=NULL, dispatch_status='queued'
                        WHERE driver_id=? AND dispatch_status='assigned'""", (driver_id,))
        db().commit()
    auto_assign()

# ---------------------------------------------------------------- driver app

@app.route("/driver/login", methods=["GET", "POST"])
def driver_login():
    err = None
    if request.method == "POST":
        phone = "".join(ch for ch in request.form.get("phone", "") if ch.isdigit())
        pin = request.form.get("pin", "")
        row = db().execute("SELECT * FROM drivers WHERE phone=? AND pin=?", (phone, pin)).fetchone()
        if row:
            session["driver_id"] = row["id"]
            session["driver_name"] = row["name"]
            return redirect(url_for("driver"))
        err = "No driver with that phone and PIN."
    return render_template("driver_login.html", err=err)

@app.route("/driver/logout")
def driver_logout():
    session.pop("driver_id", None)
    return redirect(url_for("driver_login"))

@app.route("/driver")
def driver():
    if not session.get("driver_id"):
        return redirect(url_for("driver_login"))
    return render_template("driver.html", driver_id=session["driver_id"], driver_name=session["driver_name"])

@app.get("/api/driver/state")
def api_driver_state():
    did = session.get("driver_id")
    if not did:
        return jsonify({"ok": False}), 403
    d = db().execute("SELECT * FROM drivers WHERE id=?", (did,)).fetchone()
    recompute_queue()
    mine = db().execute("""SELECT * FROM orders WHERE driver_id=? AND dispatch_status IN
                           ('assigned','received','at_restaurant','enroute')
                           ORDER BY stack_seq ASC""", (did,)).fetchall()
    waiting = db().execute("""SELECT * FROM orders WHERE dispatch_status IN ('queued','held')
                              ORDER BY created_at ASC""").fetchall()
    return jsonify({"ok": True,
                    "driver": {"name": d["name"], "status": d["status"],
                               "pending_request": d["pending_request"], "max_stack": d["max_stack"]},
                    "stack": [order_dict(o) for o in mine],
                    "queue": [order_dict(o) for o in waiting]})

@app.post("/api/driver/request")
def api_driver_request():
    """Drivers request a status change. Only dispatch can grant it."""
    did = session.get("driver_id")
    if not did:
        return jsonify({"ok": False}), 403
    want = request.get_json(force=True).get("status")
    if want not in ("online", "break", "offline"):
        return jsonify({"ok": False}), 400
    db().execute("INSERT INTO messages(driver_id,sender,body,created_at) VALUES(?,?,?,?)",
                 (did, "driver", "Requesting " + want + ".", now()))
    db().commit()
    request_status(did, want)
    return jsonify({"ok": True})

@app.route("/driver/new-order")
def driver_new_order():
    if not session.get("driver_id"):
        return redirect(url_for("driver_login"))
    rs = db().execute("SELECT * FROM restaurants ORDER BY name").fetchall()
    items = db().execute("SELECT * FROM menu_items WHERE active=1").fetchall()
    menu_map = {}
    for it in items:
        menu_map.setdefault(it["restaurant_id"], []).append(
            {"id": it["id"], "name": it["name"], "price_cents": it["price_cents"]})
    return render_template("driver_new_order.html", restaurants=rs, menu_map=menu_map)

# ---------------------------------------------------------------- restaurant app

@app.route("/restaurant/login", methods=["GET", "POST"])
def rest_login():
    err = None
    if request.method == "POST":
        row = db().execute("SELECT * FROM restaurants WHERE slug=? AND pin=?",
                           (request.form.get("slug", "").strip().lower(),
                            request.form.get("pin", ""))).fetchone()
        if row:
            session["restaurant_id"] = row["id"]
            session["restaurant_name"] = row["name"]
            return redirect(url_for("rest_home"))
        err = "Wrong store code or PIN."
    return render_template("rest_login.html", err=err)

@app.route("/restaurant/logout")
def rest_logout():
    session.pop("restaurant_id", None)
    return redirect(url_for("rest_login"))

@app.route("/restaurant")
def rest_home():
    if not session.get("restaurant_id"):
        return redirect(url_for("rest_login"))
    r = db().execute("SELECT * FROM restaurants WHERE id=?", (session["restaurant_id"],)).fetchone()
    return render_template("rest.html", r=r, open=is_open(r), hours=hours_label(r))

@app.get("/api/restaurant/orders")
def api_rest_orders():
    rid = session.get("restaurant_id")
    if not rid:
        return jsonify({"ok": False}), 403
    rows = db().execute("""SELECT * FROM orders WHERE restaurant_id=? AND dispatch_status NOT IN
                           ('delivered','cancelled') ORDER BY created_at ASC""", (rid,)).fetchall()
    r = db().execute("SELECT * FROM restaurants WHERE id=?", (rid,)).fetchone()
    return jsonify({"ok": True, "orders": [order_dict(o) for o in rows],
                    "open": is_open(r), "prep_default": r["prep_default"]})

@app.post("/api/restaurant/toggle")
def api_rest_toggle():
    rid = session.get("restaurant_id")
    if not rid:
        return jsonify({"ok": False}), 403
    r = db().execute("SELECT closed_override FROM restaurants WHERE id=?", (rid,)).fetchone()
    db().execute("UPDATE restaurants SET closed_override=? WHERE id=?",
                 (0 if r["closed_override"] else 1, rid))
    db().commit()
    return jsonify({"ok": True})

@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "time": now()})

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
