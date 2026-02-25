"""
FUT SPIN — Flask Backend v3
============================
Novità:
  - Daily Streak: bonus spin per giorni consecutivi
  - Sistema Referral: codice univoco per ogni utente
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, session
import os, sqlite3, random, hashlib, secrets
from datetime import datetime, date, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "futspin-secret-cambia-in-produzione")
DB_PATH = "futspin.db"

try:
    import stripe
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_AVAILABLE = bool(stripe.api_key)
except ImportError:
    STRIPE_AVAILABLE = False

PRIZES = [
    {"label": "200 FC",  "coins": 200,   "weight": 30},
    {"label": "500 FC",  "coins": 500,   "weight": 25},
    {"label": "1K FC",   "coins": 1000,  "weight": 20},
    {"label": "2K FC",   "coins": 2000,  "weight": 12},
    {"label": "5K FC",   "coins": 5000,  "weight": 8},
    {"label": "10K FC",  "coins": 10000, "weight": 4},
    {"label": "50K FC",  "coins": 50000, "weight": 1},
    {"label": "MISS!",   "coins": 0,     "weight": 0},
]

SPIN_PACKAGES = {
    "3":  {"spins": 3,  "price": 99,  "name": "3 Spin Pack"},
    "10": {"spins": 10, "price": 249, "name": "10 Spin Pack"},
    "25": {"spins": 25, "price": 499, "name": "25 Spin Pack"},
}

FREE_SPINS_PER_DAY   = 3
MAX_REWARDED_PER_DAY = 2
STREAK_REWARDS = {2:1, 3:1, 4:2, 5:2, 6:3, 7:5}
STREAK_MAX_BONUS = 5
REFERRAL_REWARD_INVITATO  = 2
REFERRAL_REWARD_INVITANTE = 3

# ── DATABASE ──────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                username         TEXT    UNIQUE NOT NULL,
                password         TEXT    NOT NULL,
                coins            INTEGER NOT NULL DEFAULT 0,
                spins            INTEGER NOT NULL DEFAULT 3,
                spins_extra      INTEGER NOT NULL DEFAULT 0,
                rewarded_today   INTEGER NOT NULL DEFAULT 0,
                streak           INTEGER NOT NULL DEFAULT 1,
                last_spin_date   TEXT,
                referral_code    TEXT    UNIQUE,
                referred_by      INTEGER,
                referral_rewarded INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (referred_by) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS spin_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                coins_won  INTEGER NOT NULL,
                label      TEXT    NOT NULL,
                spin_type  TEXT    NOT NULL DEFAULT 'free',
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS referrals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id   INTEGER NOT NULL,
                invitee_id   INTEGER NOT NULL,
                rewarded     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (inviter_id) REFERENCES users(id),
                FOREIGN KEY (invitee_id) REFERENCES users(id)
            );
        """)

# ── HELPERS ───────────────────────────────────
def hash_password(pw): return hashlib.sha256(f"futspin2025{pw}".encode()).hexdigest()
def generate_referral_code(): return secrets.token_urlsafe(6).upper()

def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if "user_id" not in session: return redirect(url_for("login"))
        return f(*a, **kw)
    return dec

def get_current_user():
    if "user_id" not in session: return None
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def get_streak_bonus(streak):
    if streak in STREAK_REWARDS: return STREAK_REWARDS[streak]
    return STREAK_MAX_BONUS if streak > 7 else 0

def check_daily_reset(user_id):
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with get_db() as conn:
        user = conn.execute("SELECT last_spin_date, streak FROM users WHERE id=?", (user_id,)).fetchone()
        if user["last_spin_date"] == today: return
        last = user["last_spin_date"]
        new_streak = (user["streak"] or 1) + 1 if last == yesterday else 1
        bonus = get_streak_bonus(new_streak)
        conn.execute("""
            UPDATE users SET spins=MAX(spins,?)+?, rewarded_today=0, streak=?, last_spin_date=?
            WHERE id=?
        """, (FREE_SPINS_PER_DAY, bonus, new_streak, today, user_id))

def check_referral_reward(user_id):
    with get_db() as conn:
        user = conn.execute("SELECT referred_by, referral_rewarded FROM users WHERE id=?", (user_id,)).fetchone()
        if user["referred_by"] and not user["referral_rewarded"]:
            conn.execute("UPDATE users SET spins_extra=spins_extra+? WHERE id=?", (REFERRAL_REWARD_INVITANTE, user["referred_by"]))
            conn.execute("UPDATE users SET referral_rewarded=1 WHERE id=?", (user_id,))
            conn.execute("UPDATE referrals SET rewarded=1 WHERE inviter_id=? AND invitee_id=?", (user["referred_by"], user_id))

# ── PAGES ─────────────────────────────────────
@app.route("/")
@login_required
def index():
    user = get_current_user()
    check_daily_reset(user["id"])
    user = get_current_user()
    with get_db() as conn:
        history = conn.execute("SELECT label, coins_won FROM spin_log WHERE user_id=? ORDER BY id DESC LIMIT 15", (user["id"],)).fetchall()
        ref_count    = conn.execute("SELECT COUNT(*) as n FROM referrals WHERE inviter_id=?", (user["id"],)).fetchone()["n"]
        ref_rewarded = conn.execute("SELECT COUNT(*) as n FROM referrals WHERE inviter_id=? AND rewarded=1", (user["id"],)).fetchone()["n"]
    return render_template("index.html",
        user=user, history=history,
        streak_bonus=get_streak_bonus(user["streak"]),
        referral_url=request.host_url + "register?ref=" + (user["referral_code"] or ""),
        referral_count=ref_count,
        referral_rewarded=ref_rewarded,
    )

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username","").strip().lower()
        p = request.form.get("password","")
        if not u or not p:
            error = "Inserisci username e password."
        else:
            with get_db() as conn:
                user = conn.execute("SELECT * FROM users WHERE username=? AND password=?", (u, hash_password(p))).fetchone()
            if user:
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                return redirect(url_for("index"))
            error = "Username o password errati."
    return render_template("login.html", error=error)

@app.route("/register", methods=["GET","POST"])
def register():
    error = None
    ref_code = request.args.get("ref","").strip().upper()
    if request.method == "POST":
        username = request.form.get("username","").strip().lower()
        password = request.form.get("password","")
        confirm  = request.form.get("confirm","")
        ref_code = request.form.get("ref_code","").strip().upper()
        if not username or not password:      error = "Tutti i campi sono obbligatori."
        elif len(username) < 3:               error = "Username: minimo 3 caratteri."
        elif len(username) > 20:              error = "Username: massimo 20 caratteri."
        elif not username.isalnum():          error = "Username: solo lettere e numeri."
        elif len(password) < 6:              error = "Password: minimo 6 caratteri."
        elif password != confirm:             error = "Le password non coincidono."
        else:
            inviter_id  = None
            bonus_spins = FREE_SPINS_PER_DAY
            if ref_code:
                with get_db() as conn:
                    inv = conn.execute("SELECT id FROM users WHERE referral_code=?", (ref_code,)).fetchone()
                if inv:
                    inviter_id  = inv["id"]
                    bonus_spins = FREE_SPINS_PER_DAY + REFERRAL_REWARD_INVITATO
                else:
                    error = "Codice referral non valido."
            if not error:
                try:
                    today = date.today().isoformat()
                    code  = generate_referral_code()
                    with get_db() as conn:
                        conn.execute("""
                            INSERT INTO users (username,password,coins,spins,last_spin_date,referral_code,referred_by)
                            VALUES (?,?,0,?,?,?,?)
                        """, (username, hash_password(password), bonus_spins, today, code, inviter_id))
                        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                        if inviter_id:
                            conn.execute("INSERT INTO referrals (inviter_id,invitee_id) VALUES (?,?)", (inviter_id, user["id"]))
                    session["user_id"]  = user["id"]
                    session["username"] = user["username"]
                    return redirect(url_for("index"))
                except sqlite3.IntegrityError:
                    error = "Username già in uso."
    return render_template("register.html", error=error, ref_code=ref_code)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── API ───────────────────────────────────────
@app.route("/api/spin", methods=["POST"])
@login_required
def api_spin():
    user_id = session["user_id"]
    check_daily_reset(user_id)
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user["spins"] + user["spins_extra"] <= 0:
            return jsonify({"error": "Nessuno spin disponibile"}), 400
        segs = []
        for i, p in enumerate(PRIZES): segs.extend([i]*p["weight"])
        prize = PRIZES[random.choice(segs)]
        if user["spins_extra"] > 0:
            conn.execute("UPDATE users SET spins_extra=spins_extra-1, coins=coins+? WHERE id=?", (prize["coins"], user_id))
            stype = "paid"
        else:
            conn.execute("UPDATE users SET spins=spins-1, coins=coins+? WHERE id=?", (prize["coins"], user_id))
            stype = "free"
        conn.execute("INSERT INTO spin_log (user_id,coins_won,label,spin_type) VALUES (?,?,?,?)", (user_id, prize["coins"], prize["label"], stype))
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    check_referral_reward(user_id)
    return jsonify({"success":True, "prize":prize, "coins":user["coins"], "spins":user["spins"], "spins_extra":user["spins_extra"]})

@app.route("/api/rewarded-spin", methods=["POST"])
@login_required
def rewarded_spin():
    user_id = session["user_id"]
    check_daily_reset(user_id)
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if user["rewarded_today"] >= MAX_REWARDED_PER_DAY:
            return jsonify({"error": "Limite giornaliero raggiunto"}), 400
        conn.execute("UPDATE users SET spins=spins+1, rewarded_today=rewarded_today+1 WHERE id=?", (user_id,))
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return jsonify({"success":True, "spins":user["spins"], "rewarded_today":user["rewarded_today"]})

@app.route("/api/leaderboard")
def api_leaderboard():
    with get_db() as conn:
        rows = conn.execute("SELECT username,coins,streak FROM users ORDER BY coins DESC LIMIT 10").fetchall()
    return jsonify([{"username":r["username"],"coins":r["coins"],"streak":r["streak"]} for r in rows])

@app.route("/api/create-checkout", methods=["POST"])
@login_required
def create_checkout():
    body = request.get_json(silent=True) or {}
    pkg  = SPIN_PACKAGES.get(str(body.get("spins","3")))
    if not pkg: return jsonify({"error":"Pacchetto non valido"}), 400
    if not STRIPE_AVAILABLE:
        with get_db() as conn:
            conn.execute("UPDATE users SET spins_extra=spins_extra+? WHERE id=?", (pkg["spins"], session["user_id"]))
        return jsonify({"url":None,"demo":True,"spins":pkg["spins"]})
    try:
        cs = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price_data":{"currency":"eur","unit_amount":pkg["price"],"product_data":{"name":pkg["name"]}},"quantity":1}],
            mode="payment",
            success_url=url_for("payment_success", spins=pkg["spins"], _external=True),
            cancel_url=url_for("index", _external=True),
        )
        return jsonify({"url":cs.url})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/success")
@login_required
def payment_success():
    spins = request.args.get("spins",0,type=int)
    if spins > 0:
        with get_db() as conn:
            conn.execute("UPDATE users SET spins_extra=spins_extra+? WHERE id=?", (spins, session["user_id"]))
    return redirect(url_for("index"))

@app.route("/cancel")
def payment_cancel(): return redirect(url_for("index"))

@app.route("/admin/stats")
def admin_stats():
    with get_db() as conn:
        return jsonify({
            "users":     conn.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"],
            "spins":     conn.execute("SELECT COUNT(*) as n FROM spin_log").fetchone()["n"],
            "coins_won": conn.execute("SELECT COALESCE(SUM(coins_won),0) as n FROM spin_log").fetchone()["n"],
            "referrals": conn.execute("SELECT COUNT(*) as n FROM referrals").fetchone()["n"],
            "top":       [dict(r) for r in conn.execute("SELECT username,coins,streak FROM users ORDER BY coins DESC LIMIT 5").fetchall()],
        })

@app.route("/privacy")
def privacy(): return render_template("privacy.html")

@app.route("/terms")
def terms(): return render_template("terms.html")

@app.route("/come-funziona")
def how_it_works(): return render_template("how_it_works.html")

if __name__ == "__main__":
    init_db()
    port  = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_DEBUG","true").lower() == "true"
    print(f"\n🚀 FUT SPIN v3 — http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
