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
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key-change-in-production")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DATABASE_URL = os.getenv("DATABASE_URL")

# ====================== COOKIE PARSER ======================
def extract_netflix_id(cookie_text):
    if not cookie_text:
        return None
    cookie_text = cookie_text.strip()

    # Multiple patterns for different cookie formats
    patterns = [
        r'NetflixId\s*[:=]\s*([^\s;]+)',
        r'(ct%3D[A-Za-z0-9%._-]+)',
        r'SecureNetflixId=([^\s;]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, cookie_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

# ====================== NFT TOKEN FETCHER (Improved) ======================
def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "Could not extract NetflixId from cookie"

    headers = {
        "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
        "x-netflix.request.attempt": "1",
        "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
        "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
        "Cookie": f"NetflixId={netflix_id}",
    }

    params = {
        "appVersion": "15.48.1",
        "path": '["account","token","default"]',
        "responseFormat": "json",
    }

    try:
        r = requests.get(
            "https://ios.prod.ftl.netflix.com/iosui/user/15.48",
            params=params,
            headers=headers,
            timeout=30,
            verify=False
        )
        
        print(f"🔍 Netflix Status: {r.status_code}")
        logging.info(f"Response preview: {r.text[:300]}")

        if r.status_code != 200:
            return None, f"API Error {r.status_code}"

        data = r.json()
        
        # More robust token path
        token = None
        if isinstance(data, dict):
            value = data.get("value") or data
            account = value.get("account") or {}
            token_obj = account.get("token") or {}
            default = token_obj.get("default") or token_obj
            token = default.get("token") if isinstance(default, dict) else None

        if token and isinstance(token, str) and len(token) > 50:
            return token, None
        return None, "Failed to extract NFToken (cookie likely expired)"

    except Exception as e:
        logging.error(f"Error: {e}")
        return None, str(e)[:100]

# ====================== DATABASE ======================
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

# ====================== ROUTES ======================
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
            return jsonify({"success": False, "error": "Cookie is empty"}), 400

        token, error = fetch_nftoken(cookie_text)
        if error or not token:
            return jsonify({"success": False, "error": f"Invalid/Expired: {error}"}), 400

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

        return jsonify({"success": True, "message": "✅ Account validated and added successfully!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# (keep the rest of your routes: /api/generate, /api/stats)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
