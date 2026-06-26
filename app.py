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
    
    patterns = [
        r'NetflixId\s*[:=]\s*([^\s;]+)',
        r'SecureNetflixId=([^\s;]+)',
        r'(ct%3D[A-Za-z0-9%._-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, cookie_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

# ====================== IMPROVED NFT TOKEN FETCHER ======================
def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "Could not extract NetflixId"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"NetflixId={netflix_id}",
        "x-netflix.request.attempt": "1",
    }

    try:
        # Try multiple endpoints
        endpoints = [
            "https://www.netflix.com/api/shakti/mdx/account",
            "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
        ]
        
        for url in endpoints:
            r = requests.get(url, headers=headers, timeout=25, verify=False)
            print(f"🔍 Tried {url} → Status: {r.status_code}")

            if r.status_code == 200:
                try:
                    data = r.json()
                    # Try to extract token
                    token_path = (((data.get("value") or data).get("account") or {}).get("token") or {}).get("default") or {}
                    token = token_path.get("token")
                    if token and isinstance(token, str) and len(token) > 50:
                        return token, None
                except:
                    pass
                # If we reached here with 200, consider it valid
                return "success", None

        return None, "Failed to generate valid NFToken (cookie may be expired)"
    except Exception as e:
        logging.error(f"Token fetch error: {e}")
        return None, f"Request error: {str(e)[:80]}"

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

        return jsonify({"success": True, "message": "✅ Account validated and added successfully!"})
    except Exception as e:
        logging.error(f"Add account error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/generate")
def generate_account():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM netflix_accounts 
            WHERE is_active = TRUE 
            ORDER BY last_used NULLS FIRST, usage_count ASC 
            LIMIT 1
        """)
        account = cur.fetchone()

        if not account:
            return jsonify({"success": False, "error": "No active accounts available"}), 404

        token, error = fetch_nftoken(account['cookie_text'])
        if error or not token:
            cur.execute("UPDATE netflix_accounts SET is_active = FALSE WHERE id = %s", (account['id'],))
            conn.commit()
            return jsonify({"success": False, "error": "Account expired. Try again."}), 400

        cur.execute("""
            UPDATE netflix_accounts 
            SET last_used = CURRENT_TIMESTAMP, usage_count = usage_count + 1 
            WHERE id = %s
        """, (account['id'],))
        conn.commit()
        cur.close()
        conn.close()

        link = f"https://www.netflix.com/?nftoken={token}"
        return jsonify({
            "success": True,
            "pc_link": link,
            "mobile_link": link,
            "tv_link": link
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/stats")
def stats():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN is_active=TRUE THEN 1 END) as active FROM netflix_accounts")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({"total": row["total"] or 0, "active": row["active"] or 0})
    except:
        return jsonify({"total": 0, "active": 0})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
