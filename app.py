import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, flash, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
import anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "rellipse-secret-2025")
app.permanent_session_lifetime = timedelta(days=30)

client = anthropic.Anthropic()

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

FREE_USES = 3
ADMIN_EMAILS = ["samratdgod@gmail.com", "ncvasu@gmail.com", "nesechayas30@gsiscommunity.kr", "titanium10235@gmail.com"]
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rellipse.db")


# ── Database ──

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT,
                password_hash TEXT,
                google_id TEXT,
                uses INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS reply_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                message TEXT,
                reply TEXT,
                platform TEXT DEFAULT 'google',
                language TEXT DEFAULT 'English',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


init_db()


# ── Auth helpers ──

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    if "user_id" not in session:
        return None
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if row:
            user = dict(row)
            # Validate session email matches to prevent session hijacking
            session_email = session.get("_email")
            if session_email and session_email != user["email"]:
                session.clear()
                return None
            # Store email in session for validation
            if not session_email:
                session["_email"] = user["email"]
                session.permanent = True
            return user
        else:
            # User doesn't exist, clear session
            session.clear()
            return None


# ── PWA routes ──

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")


@app.route("/service-worker.js")
def service_worker():
    resp = send_from_directory("static", "service-worker.js")
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ── Page routes ──

@app.route("/")
def index():
    user = get_current_user()
    return render_template("index.html", user=user)


@app.route("/app")
@login_required
def editor():
    user = get_current_user()
    return render_template("editor.html", user=user)


# ── Signup ──

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("editor"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        name     = request.form.get("name", "").strip()
        password = request.form.get("password", "")

        if not email or not password or not name:
            flash("All fields are required.", "error")
            return render_template("signup.html", user=None)

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("signup.html", user=None)

        try:
            with get_db() as db:
                cur = db.execute(
                    "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                    (email, name, generate_password_hash(password))
                )
                user_id = cur.lastrowid
            session["user_id"] = user_id
            session["_email"] = email
            session.permanent = True
            return redirect(url_for("editor"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "error")
            return render_template("signup.html", user=None)

    return render_template("signup.html", user=None)


# ── Login ──

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("editor"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            user = dict(row) if row else None

        if not user or not user["password_hash"] or \
           not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
            return render_template("login.html", user=None)

        session["user_id"] = user["id"]
        session["_email"] = user["email"]
        session.permanent = True
        return redirect(url_for("editor"))

    return render_template("login.html", user=None)


# ── Google OAuth ──

@app.route("/login/google")
def login_google():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    token     = google.authorize_access_token()
    user_info = token.get("userinfo")
    email     = user_info["email"].lower()
    name      = user_info.get("name", email.split("@")[0])
    google_id = user_info["sub"]

    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            if not row["google_id"]:
                db.execute("UPDATE users SET google_id = ? WHERE id = ?",
                           (google_id, row["id"]))
            user_id = row["id"]
        else:
            cur = db.execute(
                "INSERT INTO users (email, name, google_id) VALUES (?, ?, ?)",
                (email, name, google_id)
            )
            user_id = cur.lastrowid

    session["user_id"] = user_id
    session["_email"] = email
    session.permanent = True
    return redirect(url_for("editor"))


# ── Logout ──

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── API: history ──

@app.route("/api/history")
@login_required
def get_history():
    with get_db() as db:
        rows = db.execute("""
            SELECT id, message, reply, platform, language, created_at
            FROM reply_history WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 10
        """, (session["user_id"],)).fetchall()
        rows = [dict(r) for r in rows]

    def fmt(s):
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%b %d, %I:%M %p")
        except Exception:
            return s or ""

    return jsonify({"history": [{
        "id":              r["id"],
        "message_preview": (r["message"] or "")[:80],
        "full_reply":      r["reply"] or "",
        "platform":        r["platform"] or "google",
        "language":        r["language"] or "English",
        "created_at":      fmt(r["created_at"]),
    } for r in rows]})


# ── Helpers ──

def detect_email(text):
    return bool(re.search(
        r'(?i)^(from|to|subject|date|cc|bcc)\s*:', text, re.MULTILINE
    ))


def tone_label(v):
    if v <= 20:  return "extremely formal and corporate"
    if v <= 40:  return "professional and polished"
    if v <= 60:  return "balanced and natural"
    if v <= 80:  return "warm and friendly"
    return "casual and relaxed, like texting a friend"


# ── API: generate reply ──

@app.route("/api/reply", methods=["POST"])
@login_required
def generate_reply():
    user = get_current_user()
    is_admin = user["email"] in ADMIN_EMAILS

    if not is_admin and user["uses"] >= FREE_USES:
        return jsonify({"error": "free_limit_reached"}), 402

    data          = request.get_json()
    message       = (data.get("message") or "").strip()
    platform      = (data.get("platform") or "google").strip()
    language      = (data.get("language") or "English").strip()
    context       = (data.get("context") or "").strip()
    tone_value    = int(data.get("tone_value") or 50)
    length        = (data.get("length") or "medium").strip()
    business_name = (data.get("business_name") or "").strip()

    if not message:
        return jsonify({"error": "Paste the customer message first."}), 400
    if len(message) > 2000:
        return jsonify({"error": "Message too long (max 2000 chars)."}), 400

    is_email = detect_email(message) or platform == "email"

    platform_ctx = {
        "google":    "This is a Google Maps review. Reply will be public.",
        "whatsapp":  "This is a WhatsApp message. Keep it conversational.",
        "instagram": "This is an Instagram comment. Short and warm.",
        "facebook":  "This is a Facebook comment. Professional but friendly.",
        "twitter":   "This is a Twitter/X mention. Concise, under 280 characters.",
        "email":     "This is a customer email. Format as professional email reply.",
    }.get(platform, "This is a customer message.")

    length_map = {
        "short":  ("under 60 words",   150),
        "medium": ("60 to 150 words",  400),
        "long":   ("150 to 300 words", 650),
    }
    length_instr, max_tokens = length_map.get(length, length_map["medium"])

    biz_line  = f"Business name: '{business_name}'." if business_name else "Do not mention a business name."
    ctx_line  = f"Extra context: {context}" if context else ""
    lang_line = f"Write the reply in {language}." if language.lower() != "english" else ""
    email_fmt = "Format: 'Subject: [line]' on line 1, blank line, then body." if is_email else ""

    prompt = f"""You are {business_name or 'responding'} to this message.

Write a reply that sounds natural and genuine, like how you would actually respond. Not a template—just real and direct.

Guidelines:
- Platform: {platform_ctx}
- Tone: {tone_label(tone_value)}
- Length: {length_instr}
{lang_line}
{ctx_line}
{email_fmt}

Rules:
- Sound like a real person or organization, not a bot
- Be direct and honest
- Match the vibe of the message
- If positive → show genuine gratitude
- If negative → acknowledge, take responsibility, offer to fix it
- If a question → answer clearly
- Keep personality intact—no templates

Output ONLY the reply text.

Message:
{message}

Reply:"""

    try:
        resp  = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        reply = resp.content[0].text.strip()

        with get_db() as db:
            if not is_admin:
                db.execute("UPDATE users SET uses = uses + 1 WHERE id = ?", (user["id"],))
            db.execute("""
                INSERT INTO reply_history (user_id, message, reply, platform, language)
                VALUES (?, ?, ?, ?, ?)
            """, (user["id"], message[:500], reply, platform, language))
            db.execute("""
                DELETE FROM reply_history WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM reply_history WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT 10
                )
            """, (user["id"], user["id"]))

        return jsonify({"reply": reply, "is_email": is_email})

    except Exception as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500


@app.route("/api/pricing")
def get_pricing():
    """Return pricing based on user's country."""
    country = request.headers.get('CF-IPCountry') or request.headers.get('X-Forwarded-Country') or 'US'
    
    prices = {
        'IN': '₹299', 'KR': '₩9,900', 'GB': '£15', 'AU': 'A$28', 'SG': 'S$25',
        'PH': '₱299', 'BR': 'R$49', 'DE': '€17', 'FR': '€17', 'CA': 'C$25',
        'JP': '¥2,200', 'NG': '₦4,999', 'US': '$19', 'NZ': 'NZ$26', 'ZA': 'R$320',
        'MX': 'MXN$380', 'AE': 'د.إ90', 'SE': 'kr190', 'CH': 'CHF21', 'NL': '€17',
        'BE': '€17', 'AT': '€17', 'IT': '€17', 'ES': '€17', 'PL': 'zł79', 'CZ': 'Kč480',
    }
    
    price = prices.get(country, '$19')
    return jsonify({'price': price, 'country': country})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
