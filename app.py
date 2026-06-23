import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
import anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

# ── Anthropic client ──
client = anthropic.Anthropic()

# ── Google OAuth ──
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

FREE_USES = 3
DB_PATH = "replyze.db"


# ── Database setup ──
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
        db.commit()


init_db()


# ── Auth helpers ──
def login_required(f):
    """Decorator — redirects to login if not authenticated."""
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
        return db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()


# ── Routes ──

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
        email = request.form.get("email", "").strip().lower()
        name  = request.form.get("name", "").strip()
        password = request.form.get("password", "")

        if not email or not password or not name:
            flash("All fields are required.", "error")
            return render_template("signup.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("signup.html")

        try:
            with get_db() as db:
                db.execute(
                    "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                    (email, name, generate_password_hash(password))
                )
                db.commit()
                user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
                session["user_id"] = user["id"]
                return redirect(url_for("editor"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "error")
            return render_template("signup.html")

    return render_template("signup.html")


# ── Login ──
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("editor"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        return redirect(url_for("editor"))

    return render_template("login.html")


# ── Google OAuth ──
@app.route("/login/google")
def login_google():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    token = google.authorize_access_token()
    user_info = token.get("userinfo")

    email     = user_info["email"].lower()
    name      = user_info.get("name", email.split("@")[0])
    google_id = user_info["sub"]

    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user:
            # Update google_id if not set
            if not user["google_id"]:
                db.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, user["id"]))
                db.commit()
        else:
            # Create new user
            db.execute(
                "INSERT INTO users (email, name, google_id) VALUES (?, ?, ?)",
                (email, name, google_id)
            )
            db.commit()
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        session["user_id"] = user["id"]

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


# ── API: generate reply ──
@app.route("/api/reply", methods=["POST"])
@login_required
def generate_reply():
    user = get_current_user()

    # Check free limit
    if user["uses"] >= FREE_USES:
        return jsonify({
            "error": "free_limit_reached",
            "message": f"You've used your {FREE_USES} free replies. Unlock unlimited for $19/month."
        }), 402

    data          = request.get_json()
    message       = data.get("message", "").strip()
    platform      = data.get("platform", "google").strip()
    tone          = data.get("tone", "professional").strip()
    business_name = data.get("business_name", "").strip()

    if not message:
        return jsonify({"error": "Paste the customer message first."}), 400

    if len(message) > 2000:
        return jsonify({"error": "Message too long. Keep it under 2000 characters."}), 400

    platform_context = {
        "google":    "This is a Google Maps review. The reply will be public.",
        "whatsapp":  "This is a WhatsApp message from a customer. Keep it conversational.",
        "instagram": "This is an Instagram comment. Keep it short and warm.",
        "facebook":  "This is a Facebook comment or message. Professional but friendly."
    }.get(platform, "This is a customer message.")

    tone_context = {
        "professional": "Write in a professional, polished tone.",
        "friendly":     "Write in a warm, friendly, personal tone.",
        "apologetic":   "The customer seems unhappy. Be apologetic and empathetic."
    }.get(tone, "Write in a professional tone.")

    business_line = f"The business name is '{business_name}'." if business_name else "Do not mention a specific business name."

    prompt = f"""You are an expert customer communication specialist for small businesses.

Write a perfect reply to the following customer message.

Context:
- {platform_context}
- {tone_context}
- {business_line}

Rules:
- Concise — no fluff
- Sound human, not like a template
- Positive review: thank them specifically
- Negative review: acknowledge, apologize, offer to resolve
- Question: answer helpfully
- Output ONLY the reply text

Customer message:
{message}

Reply:"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )

        reply = response.content[0].text.strip()

        # Increment user's usage count in DB
        with get_db() as db:
            db.execute("UPDATE users SET uses = uses + 1 WHERE id = ?", (user["id"],))
            db.commit()

        return jsonify({"reply": reply})

    except anthropic.APIError as e:
        return jsonify({"error": f"AI error: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)
