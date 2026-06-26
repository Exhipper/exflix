from flask import Flask, render_template, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import os
import re

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key-change-in-production")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
DATABASE_URL = os.getenv("DATABASE_URL")

# ====================== BETTER COOKIE PARSER ======================
def extract_netflix_id(cookie_text):
    """Strong extractor for harshitkamboj checker output"""
    if not cookie_text:
        return None
    
    # 1. Look for NetflixId line in Netscape format
    match = re.search(r'NetflixId\s+([^\s]+)', cookie_text)
    if match:
        return match.group(1)
    
    # 2. Direct = format
    match = re.search(r'NetflixId=([^;,\s]+)', cookie_text)
    if match:
        return match.group(1)
    
    # 3. Last resort - long base64-like string after NetflixId
    match = re.search(r'NetflixId[\t\s]+([a-zA-Z0-9%._-]+)', cookie_text)
    if match:
        return match.group(1)
    
    return None

def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "NetflixId not found in the provided cookie"

    headers = {
        "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
        "Cookie": f"NetflixId={netflix_id}",
    }
    params = {"path": '["account","token","default"]', "responseFormat": "json"}

    try:
        r = requests.get(
            "https://ios.prod.ftl.netflix.com/iosui/user/15.48",
            params=params,
            headers=headers,
            timeout=25,
            verify=False
        )
        data = r.json()
        token = data.get("value", {}).get("account", {}).get("token", {}).get("default", {}).get("token")
        
        if token:
            return token, None
        return None, "Failed to generate NFToken (cookie may be expired)"
    except Exception as e:
        return None, f"Network error: {str(e)[:80]}"

# ====================== DATABASE & ROUTES ======================
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS netflix_accounts (
            id SERIAL PRIMARY KEY,
            cookie_text TEXT NOT NULL UNIQUE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP,
            usage_count INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database initialized.")

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
        else:
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
            INSERT INTO netflix_accounts (cookie_text, is_active)
            VALUES (%s, TRUE)
            ON CONFLICT (cookie_text) DO UPDATE 
            SET is_active = TRUE, added_at = CURRENT_TIMESTAMP
        """, (cookie_text,))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "message": "✅ Account validated and added successfully!"})
    except Exception as e:
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

        cur.execute("UPDATE netflix_accounts SET last_used = CURRENT_TIMESTAMP, usage_count = usage_count + 1 WHERE id = %s", (account['id'],))
        conn.commit()

        link = f"https://www.netflix.com/?nftoken={token}"

        cur.close()
        conn.close()

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
