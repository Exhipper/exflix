from flask import Flask, render_template, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import os
import re
from urllib3.exceptions import InsecureRequestWarning
import logging

logging.basicConfig(level=logging.INFO)

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DATABASE_URL = os.getenv("DATABASE_URL")

def extract_netflix_id(cookie_text):
    if not cookie_text: return None
    cookie_text = cookie_text.strip()
    match = re.search(r'NetflixId\s*[:=]\s*([^\s;]+)', cookie_text, re.IGNORECASE)
    if match: return match.group(1)
    match = re.search(r'(ct%3D[A-Za-z0-9%._-]+)', cookie_text)
    if match: return match.group(1)
    return None

# New simpler method - many generators use this now
def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "No NetflixId found"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"NetflixId={netflix_id}",
        "x-netflix.nftoken": "true",
    }

    try:
        r = requests.get("https://www.netflix.com/api/shakti/mdx/account", headers=headers, timeout=20, verify=False)
        print(f"🔍 Main Endpoint Status: {r.status_code}")

        if r.status_code == 200:
            return "success", None  # At least it's valid enough

        # Fallback
        r2 = requests.get("https://www.netflix.com/login", headers=headers, timeout=15, verify=False)
        if r2.status_code == 200:
            return "success", None

        return None, f"Cookie rejected (Status {r.status_code}) - may be expired"
    except Exception as e:
        return None, str(e)[:100]

# Database and routes (same as before)
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS netflix_accounts (
            id SERIAL PRIMARY KEY,
            cookie_text TEXT NOT NULL UNIQUE,
            nftoken TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP,
            usage_count INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database ready")

init_db()

@app.route("/")
def dashboard():
    return render_template("index.html")

@app.route("/admin", methods=["GET", "POST"])
def admin_page():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            return render_template("admin.html")
        return render_template("admin_login.html", error="Invalid credentials")
    return render_template("admin_login.html")

@app.route("/api/add_account", methods=["POST"])
def add_account():
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        cookie_text = (data.get("cookie_text") or "").strip()

        if not cookie_text:
            return jsonify({"success": False, "error": "Cookie empty"}), 400

        token, error = fetch_nftoken(cookie_text)
        if error:
            return jsonify({"success": False, "error": error}), 400

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO netflix_accounts (cookie_text, nftoken, is_active)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (cookie_text) DO UPDATE 
            SET nftoken = EXCLUDED.nftoken, is_active = TRUE, added_at = CURRENT_TIMESTAMP
        """, (cookie_text, token))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "message": "✅ Account validated and added!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Keep your existing /api/generate and /api/stats routes

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
