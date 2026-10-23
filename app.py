import os, re, math, secrets, sqlite3, threading, webbrowser
from datetime import date, timedelta
from collections import Counter
from flask import Flask, g, render_template, request, redirect, url_for, flash, abort, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
UPLOADS = os.path.join(os.path.dirname(__file__), "static", "uploads"); os.makedirs(UPLOADS, exist_ok=True)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DB = os.path.join(os.path.dirname(__file__), "lostfound.db")
CATEGORIES = ["Electronics", "Books & Notes", "ID & Cards", "Keys", "Bags", "Clothing", "Bottles", "Glasses", "Other"]
PLACES = ["Library", "Canteen", "Main Block", "Sports Ground", "Hostel", "Auditorium", "Parking", "Labs", "Bus Stop"]

# ---------- database ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, kind TEXT, title TEXT, description TEXT, category TEXT,
 place TEXT, date TEXT, secret_detail TEXT, contact_hint TEXT, owner_token TEXT UNIQUE, status TEXT DEFAULT 'pending',
 reject_reason TEXT, photo TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS claims(id INTEGER PRIMARY KEY, item_id INTEGER, claimant TEXT, proof TEXT, reply_hint TEXT,
 token TEXT UNIQUE, status TEXT DEFAULT 'new', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS matches(lost_id INTEGER, found_id INTEGER, lost_ok INT DEFAULT 0, found_ok INT DEFAULT 0,
 dismissed INT DEFAULT 0, PRIMARY KEY(lost_id, found_id));
CREATE TABLE IF NOT EXISTS flags(item_id INTEGER, created TEXT DEFAULT CURRENT_TIMESTAMP);
"""
SAMPLES = [
 ("lost", "Black backpack with red zipper", "Black Wildcraft backpack, red zipper pulls, a keychain shaped like a cricket bat. Left near the study tables.", "Bags", "Library", 4, "What is written inside the front pocket?"),
 ("found", "Rucksack, black with red zips", "Black rucksack with red zip pullers found on a bench. Has a small bat-shaped keyring.", "Bags", "Library", 4, "Name the book inside the bag."),
 ("lost", "iPhone with blue cover", "Smartphone in a dark blue silicone case, cracked screen protector. Lock screen shows a dog photo.", "Electronics", "Canteen", 3, "What is the lock screen photo?"),
 ("found", "Mobile phone, navy case", "Cell phone with navy blue cover found on canteen table 4 at lunch.", "Electronics", "Canteen", 3, "Describe the wallpaper."),
 ("found", "Student ID card", "College ID card found near the bus stop shelter. Name is hidden; handed to security desk.", "ID & Cards", "Bus Stop", 2, "Which department is on the card?"),
 ("lost", "White earbuds case", "White wireless earbuds charging case with a green sticker. Lost after lab session.", "Electronics", "Labs", 5, "What sticker is on the case?"),
 ("found", "Steel water bottle, green", "Green insulated steel flask with dent at the bottom, found at the ground bleachers.", "Bottles", "Sports Ground", 1, "What is stuck on the lid?"),
 ("lost", "Round glasses, gold frame", "Spectacles with thin gold round frame in a brown hard case.", "Glasses", "Auditorium", 6, "What colour is the case lining?"),
]

def init_db():
    with sqlite3.connect(DB) as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM items").fetchone():
            for k, t, d, cat, p, dt, sec in SAMPLES:
                c.execute("INSERT INTO items(kind,title,description,category,place,date,secret_detail,contact_hint,owner_token,status)"
                          " VALUES(?,?,?,?,?,?,?,?,?,'open')", (k, t, d, cat, p, (date.today() - timedelta(days=dt)).isoformat(), sec, "Meet at the security desk, Main Block", secrets.token_urlsafe(12)))
            for t in ("Maths textbook", "Grey hoodie", "Bike keys"):
                c.execute("INSERT INTO items(kind,title,description,category,place,date,secret_detail,owner_token,status) VALUES('found',?,'Returned to owner','Other','Library',date('now','-9 day'),'-',?,'returned')", (t, secrets.token_urlsafe(12)))

# ---------- semantic matching (offline TF-IDF + concept map; no external model needed) ----------
CONCEPTS = {"phone": "mobile", "smartphone": "mobile", "iphone": "mobile", "cell": "mobile", "cellphone": "mobile", "android": "mobile",
 "earbuds": "earphones", "airpods": "earphones", "headphones": "earphones", "earphone": "earphones",
 "backpack": "bag", "rucksack": "bag", "bag": "bag", "satchel": "bag", "purse": "wallet", "wallet": "wallet",
 "flask": "bottle", "bottle": "bottle", "tumbler": "bottle", "spectacles": "glasses", "specs": "glasses", "eyeglasses": "glasses",
 "id": "card", "identity": "card", "cards": "card", "keys": "key", "keyring": "key", "keychain": "key", "zips": "zip", "zipper": "zip",
 "zippers": "zip", "zip": "zip", "pullers": "zip", "pulls": "zip", "cover": "case", "casing": "case", "navy": "blue", "dark": "dark",
 "spec": "glasses", "laptop": "laptop", "notebook": "book", "textbook": "book", "books": "book", "steel": "metal", "charger": "charger", "adapter": "charger"}
STOP = set("a an the and or of in on at with to is was for it its near by from has have had left found lost my i small big this that".split())

def tokens(text):
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in STOP or len(w) < 2: continue
        out.append(CONCEPTS.get(w, w[:-1] if w.endswith("s") and len(w) > 3 else w))
    return out

def doc_text(i):
    return f"{i['title']} {i['title']} {i['description']} {i['category']} {i['category']}"

def suggestions(item, limit=3):
    other = "found" if item["kind"] == "lost" else "lost"
    pool = db().execute("SELECT * FROM items WHERE kind=? AND status='open'", (other,)).fetchall()
    if not pool: return []
    docs = {p["id"]: Counter(tokens(doc_text(p))) for p in pool}
    q = Counter(tokens(doc_text(item)))
    n = len(pool) + 1
    df = Counter(t for c in list(docs.values()) + [q] for t in c)
    idf = lambda t: math.log((n + 1) / (df[t] + 0.5)) + 1
    def vec(c): return {t: (1 + math.log(v)) * idf(t) for t, v in c.items()}
    qv = vec(q); qn = math.sqrt(sum(x * x for x in qv.values())) or 1
    res = []
    for p in pool:
        pv = vec(docs[p["id"]]); pn = math.sqrt(sum(x * x for x in pv.values())) or 1
        s = sum(qv[t] * pv.get(t, 0) for t in qv) / (qn * pn)
        if p["category"] == item["category"]: s += 0.1
        if p["place"] == item["place"]: s += 0.07
        if s >= 0.3: res.append((p, min(int(s * 100), 99)))
    res.sort(key=lambda x: -x[1])
    dismissed = {(r["lost_id"], r["found_id"]) for r in db().execute("SELECT * FROM matches WHERE dismissed=1")}
    keep = [(p, s) for p, s in res if ((item["id"], p["id"]) if item["kind"] == "lost" else (p["id"], item["id"])) not in dismissed]
    return keep[:limit]

# ---------- moderation ----------
PII = [(r"\b\d{10}\b|\b\d{3}[\s-]\d{3,4}[\s-]\d{3,4}\b", "a phone number"), (r"[\w.+-]+@[\w-]+\.[\w.]+", "an email address"),
       (r"https?://|www\.", "a link")]
BANNED = ["sell", "buy", "free offer", "whatsapp", "telegram", "instagram"]

def check_text(*texts):
    blob = " ".join(texts)
    for rx, name in PII:
        if re.search(rx, blob, re.I): return f"Please remove {name}. Contact details stay private; the claim workflow shares them safely."
    for w in BANNED:
        if w in blob.lower(): return f'Posts can\'t include "{w}". See the rules page.'
    return None

def by_token(token):
    row = db().execute("SELECT * FROM items WHERE owner_token=?", (token,)).fetchone()
    if not row: abort(404)
    return row

@app.template_filter("ago")
def ago(d):
    n = (date.today() - date.fromisoformat(d)).days
    return "today" if n <= 0 else "yesterday" if n == 1 else f"{n} days ago"

def save_photo(file):
    if not file or not file.filename: return None
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "webp"): return "bad"
    name = secrets.token_hex(8) + "." + ext
    file.save(os.path.join(UPLOADS, name)); return name

# ---------- public routes ----------
@app.route("/")
def home():
    q = request.args.get("q", "").strip(); kind = request.args.get("kind", ""); cat = request.args.get("category", ""); place = request.args.get("place", "")
    days = request.args.get("days", "0"); sort = request.args.get("sort", "new")
    sql, args = "SELECT * FROM items WHERE status='open' AND date >= date('now','-60 day')", []
    for col, val in (("kind", kind), ("category", cat), ("place", place)):
        if val: sql += f" AND {col}=?"; args.append(val)
    if days.isdigit() and int(days) > 0: sql += " AND date >= date('now', ?)"; args.append(f"-{int(days)} day")
    rows = db().execute(sql + (" ORDER BY date ASC, id ASC" if sort == "old" else " ORDER BY date DESC, id DESC"), args).fetchall()
    if q:
        qt = set(tokens(q))
        rows = [r for r in rows if qt & set(tokens(doc_text(r)))]
    counts = {k: db().execute("SELECT COUNT(*) FROM items WHERE kind=? AND status='open'", (k,)).fetchone()[0] for k in ("lost", "found")}
    counts["returned"] = db().execute("SELECT COUNT(*) FROM items WHERE status='returned'").fetchone()[0]
    alerts = []
    for wq, since in session.get("watches", []):
        qt = set(tokens(wq))
        new = [r for r in db().execute("SELECT * FROM items WHERE status='open' AND id>?", (since,)) if qt & set(tokens(doc_text(r)))]
        if new: alerts.append((wq, len(new)))
    return render_template("index.html", items=rows, q=q, kind=kind, cat=cat, place=place, days=days, sort=sort, cats=CATEGORIES, places=PLACES,
                           counts=counts, alerts=alerts, watching=bool(session.get("watches")))

@app.route("/report/<kind>", methods=["GET", "POST"])
def report(kind):
    if kind not in ("lost", "found"): abort(404)
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("title", "description", "category", "place", "date", "secret_detail", "contact_hint")}
        if not all([f["title"], f["description"], f["category"], f["place"], f["date"], f["secret_detail"]]):
            flash("Please fill in every required field.", "error")
        elif (err := check_text(f["title"], f["description"], f["contact_hint"])):
            flash(err, "error")
        elif (photo := save_photo(request.files.get("photo"))) == "bad":
            flash("Photos must be PNG, JPG or WEBP and under 4 MB.", "error")
        else:
            token = secrets.token_urlsafe(16)
            db().execute("INSERT INTO items(kind,title,description,category,place,date,secret_detail,contact_hint,owner_token,photo) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (kind, f["title"], f["description"], f["category"], f["place"], f["date"], f["secret_detail"], f["contact_hint"], token, photo))
            db().commit()
            return redirect(url_for("manage", token=token, new=1))
        return render_template("report.html", kind=kind, f=f, cats=CATEGORIES, places=PLACES)
    return render_template("report.html", kind=kind, f={}, cats=CATEGORIES, places=PLACES)

@app.route("/item/<int:iid>")
def item(iid):
    it = db().execute("SELECT * FROM items WHERE id=? AND status='open'", (iid,)).fetchone()
    if not it: abort(404)
    return render_template("item.html", it=it)

@app.route("/item/<int:iid>/claim", methods=["POST"])
def claim(iid):
    it = db().execute("SELECT * FROM items WHERE id=? AND status='open'", (iid,)).fetchone()
    if not it: abort(404)
    name, proof, hint = (request.form.get(k, "").strip() for k in ("claimant", "proof", "reply_hint"))
    err = check_text(proof, hint) if (proof and hint) else None
    if not name or not proof: flash("Add your name and your answer to the check question.", "error")
    elif err: flash(err.replace("Contact details stay private; the claim workflow shares them safely.", "Use the reply hint box for where to meet instead."), "error")
    else:
        t = secrets.token_urlsafe(16)
        db().execute("INSERT INTO claims(item_id,claimant,proof,reply_hint,token) VALUES(?,?,?,?,?)", (iid, name, proof, hint, t)); db().commit()
        return redirect(url_for("claim_status", token=t))
    return redirect(url_for("item", iid=iid))

@app.route("/claim/<token>")
def claim_status(token):
    c = db().execute("SELECT * FROM claims WHERE token=?", (token,)).fetchone() or abort(404)
    it = db().execute("SELECT * FROM items WHERE id=?", (c["item_id"],)).fetchone()
    return render_template("claim.html", c=c, it=it)

# ---------- owner (private link) ----------
@app.route("/manage/<token>")
def manage(token):
    it = by_token(token)
    claims = db().execute("SELECT * FROM claims WHERE item_id=? ORDER BY id DESC", (it["id"],)).fetchall()
    sugg = suggestions(it) if it["status"] == "open" else []
    side = "lost_ok" if it["kind"] == "lost" else "found_ok"
    ok = {}
    for r in db().execute("SELECT * FROM matches WHERE lost_id=? OR found_id=?", (it["id"], it["id"])):
        other = r["found_id"] if it["kind"] == "lost" else r["lost_id"]
        ok[other] = r
    return render_template("manage.html", it=it, claims=claims, sugg=sugg, ok=ok, side=side, new=request.args.get("new"))

@app.route("/manage/<token>/claim/<int:cid>/<action>", methods=["POST"])
def decide(token, cid, action):
    it = by_token(token)
    if action not in ("approve", "reject"): abort(400)
    db().execute("UPDATE claims SET status=? WHERE id=? AND item_id=?", ("approved" if action == "approve" else "rejected", cid, it["id"]))
    db().commit(); flash("Claimant approved. They can now see your meeting hint." if action == "approve" else "Claim declined.", "ok")
    return redirect(url_for("manage", token=token))

@app.route("/manage/<token>/match/<int:other>/<action>", methods=["POST"])
def match(token, other, action):
    it = by_token(token)
    lost, found = (it["id"], other) if it["kind"] == "lost" else (other, it["id"])
    db().execute("INSERT OR IGNORE INTO matches(lost_id,found_id) VALUES(?,?)", (lost, found))
    if action == "dismiss": db().execute("UPDATE matches SET dismissed=1 WHERE lost_id=? AND found_id=?", (lost, found))
    else: db().execute(f"UPDATE matches SET {'lost_ok' if it['kind']=='lost' else 'found_ok'}=1 WHERE lost_id=? AND found_id=?", (lost, found))
    db().commit(); flash("Thanks. The other person will see your confirmation." if action == "confirm" else "Suggestion hidden.", "ok")
    return redirect(url_for("manage", token=token))

@app.route("/manage/<token>/returned", methods=["POST"])
def returned(token):
    it = by_token(token)
    db().execute("UPDATE items SET status='returned' WHERE id=?", (it["id"],)); db().commit()
    flash("Marked as returned. The post is now hidden. Glad it worked out.", "ok")
    return redirect(url_for("manage", token=token))

@app.route("/item/<int:iid>/flag", methods=["POST"])
def flag(iid):
    db().execute("INSERT INTO flags(item_id) VALUES(?)", (iid,)); db().commit()
    flash("Thanks. A moderator will review this post.", "ok"); return redirect(url_for("item", iid=iid))

@app.route("/watch", methods=["POST"])
def watch():
    q = request.form.get("q", "").strip()
    if not q: flash("Type what you want to find first, then set an alert.", "error")
    else:
        top = db().execute("SELECT COALESCE(MAX(id),0) FROM items").fetchone()[0]
        session["watches"] = (session.get("watches", []) + [[q, top]])[-5:]
        flash(f'Alert set for "{q}". A banner appears here when a matching post goes live.', "ok")
    return redirect(url_for("home", q=q))

@app.route("/watch/clear", methods=["POST"])
def watch_clear():
    session.pop("watches", None); return redirect(url_for("home"))

@app.route("/rules")
def rules():
    return render_template("rules.html")

# ---------- admin moderation ----------
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST" and "password" in request.form:
        if request.form["password"] == ADMIN_PASSWORD: session["admin"] = True
        else: flash("Wrong password.", "error")
        return redirect(url_for("admin"))
    if not session.get("admin"): return render_template("admin.html", queue=None)
    queue = db().execute("SELECT * FROM items WHERE status='pending' ORDER BY id").fetchall()
    flagged = db().execute("SELECT i.*, COUNT(f.item_id) n FROM items i JOIN flags f ON f.item_id=i.id WHERE i.status='open' GROUP BY i.id").fetchall()
    return render_template("admin.html", queue=queue, flagged=flagged)

@app.route("/admin/<int:iid>/<action>", methods=["POST"])
def moderate(iid, action):
    if not session.get("admin"): abort(403)
    if action == "approve": db().execute("UPDATE items SET status='open' WHERE id=?", (iid,))
    else: db().execute("UPDATE items SET status='rejected', reject_reason=? WHERE id=?", (request.form.get("reason", "Breaks the posting rules."), iid))
    db().commit(); return redirect(url_for("admin"))

@app.route("/admin/flag/<int:iid>/<action>", methods=["POST"])
def moderate_flag(iid, action):
    if not session.get("admin"): abort(403)
    if action == "remove": db().execute("UPDATE items SET status='rejected', reject_reason='Removed after a report.' WHERE id=?", (iid,))
    db().execute("DELETE FROM flags WHERE item_id=?", (iid,)); db().commit(); return redirect(url_for("admin"))

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

init_db()
if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 5055))
    print(f"\nFindr is running. Open http://127.0.0.1:{PORT} in your browser. Keep this window open.\n")
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)
