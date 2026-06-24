import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, flash, make_response
)
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
import anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
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
# Absolute path so the DB is always next to app.py, regardless of CWD
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replyze.db")


# ── Database ──

@contextmanager
def get_db():
    """Open a SQLite connection, commit on success, rollback + close on error."""
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
                language TEXT DEFAULT 'english',
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
        return dict(row) if row else None


# ── PWA static routes ──

@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


@app.route("/service-worker.js")
def service_worker():
    resp = make_response(app.send_static_file("service-worker.js"))
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
    uses_left = max(0, FREE_USES - user["uses"])
    return render_template("editor.html", user=user, uses_left=uses_left)


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

        if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
            return render_template("login.html", user=None)

        session["user_id"] = user["id"]
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
                db.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, row["id"]))
            user_id = row["id"]
        else:
            cur = db.execute(
                "INSERT INTO users (email, name, google_id) VALUES (?, ?, ?)",
                (email, name, google_id)
            )
            user_id = cur.lastrowid

    session["user_id"] = user_id
    session.permanent = True
    return redirect(url_for("editor"))


# ── Logout ──

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── API: uses left ──

@app.route("/api/uses-left")
@login_required
def uses_left():
    user = get_current_user()
    left = max(0, FREE_USES - user["uses"])
    return jsonify({"uses_left": left, "free_total": FREE_USES})


# ── API: reply history ──

@app.route("/api/history")
@login_required
def get_history():
    with get_db() as db:
        rows = db.execute("""
            SELECT id, message, reply, platform, language, created_at
            FROM reply_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 10
        """, (session["user_id"],)).fetchall()
        rows = [dict(r) for r in rows]

    def fmt_date(s):
        if not s:
            return ""
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%b %d, %I:%M %p")
        except Exception:
            return s

    history = [
        {
            "id":              r["id"],
            "message_preview": (r["message"] or "")[:70],
            "reply_preview":   (r["reply"] or "")[:70],
            "full_reply":      r["reply"] or "",
            "platform":        r["platform"] or "google",
            "language":        r["language"] or "english",
            "created_at":      fmt_date(r["created_at"]),
        }
        for r in rows
    ]
    return jsonify({"history": history})


# ── Helpers ──

def detect_email(text):
    """True if the pasted text looks like an email (contains common email headers)."""
    return bool(re.search(r'(?i)^(from|to|subject|date|cc|bcc)\s*:', text, re.MULTILINE))


def tone_description(value):
    """Convert 0-100 slider value to a tone instruction for the prompt."""
    if value <= 20:
        return "extremely formal and corporate, using proper business language and titles"
    if value <= 40:
        return "professional and polished, maintaining business decorum"
    if value <= 60:
        return "balanced and natural, neither too formal nor too casual"
    if value <= 80:
        return "warm and friendly, conversational but respectful"
    return "casual and relaxed, like texting a friend"


# ── API: generate reply ──

@app.route("/api/reply", methods=["POST"])
@login_required
def generate_reply():
    user = get_current_user()

    if user["uses"] >= FREE_USES:
        return jsonify({
            "error": "free_limit_reached",
            "message": f"You've used your {FREE_USES} free replies. Unlock unlimited for $19/month."
        }), 402

    data          = request.get_json()
    message       = data.get("message", "").strip()
    platform      = data.get("platform", "google").strip()
    language      = data.get("language", "english").strip()
    context       = data.get("context", "").strip()
    tone_value    = int(data.get("tone_value", 50))
    length        = data.get("length", "medium").strip()
    business_name = data.get("business_name", "").strip()

    if not message:
        return jsonify({"error": "Paste the customer message first."}), 400
    if len(message) > 2000:
        return jsonify({"error": "Message too long. Keep it under 2000 characters."}), 400

    is_email_mode = detect_email(message) or platform == "email"

    platform_ctx = {
        "google":    "This is a Google Maps review. The reply will be public.",
        "whatsapp":  "This is a WhatsApp message from a customer. Keep it conversational.",
        "instagram": "This is an Instagram comment. Keep it short and warm.",
        "facebook":  "This is a Facebook comment or message. Professional but friendly.",
        "twitter":   "This is a Twitter/X mention or DM. Keep it concise, under 280 characters.",
        "email":     "This is a customer email. Format as a professional email reply.",
    }.get(platform, "This is a customer message.")

    length_map = {
        "short":  ("under 60 words",   150),
        "medium": ("60 to 150 words",  350),
        "long":   ("150 to 300 words", 600),
    }
    length_instruction, max_tokens = length_map.get(length, length_map["medium"])

    business_line = (
        f"The business name is '{business_name}'." if business_name
        else "Do not mention a specific business name."
    )
    context_line = f"Extra context: {context}" if context else ""
    lang_line    = f"Write the reply in {language.capitalize()}." if language != "english" else "Write in English."
    email_line   = (
        "Format your reply with 'Subject: [relevant subject line]' on line 1, blank line, then body."
        if is_email_mode else ""
    )

    prompt = f"""You are an expert customer communication specialist for small businesses.

Write a perfect reply to the following customer message.

Context:
- {platform_ctx}
- Tone: {tone_description(tone_value)}
- Length: {length_instruction}
- {business_line}
- {lang_line}
{context_line}
{email_line}

Rules:
- Sound human, not like a template
- Positive review: thank them specifically
- Negative review: acknowledge, apologise, offer to resolve
- Question: answer helpfully
- Output ONLY the reply text

Customer message:
{message}

Reply:"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        reply = response.content[0].text.strip()

        with get_db() as db:
            db.execute("UPDATE users SET uses = uses + 1 WHERE id = ?", (user["id"],))
            db.execute("""
                INSERT INTO reply_history (user_id, message, reply, platform, language)
                VALUES (?, ?, ?, ?, ?)
            """, (user["id"], message[:500], reply, platform, language))
            db.execute("""
                DELETE FROM reply_history
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM reply_history
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT 10
                )
            """, (user["id"], user["id"]))

        return jsonify({"reply": reply, "is_email": is_email_mode})

    except Exception as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)
