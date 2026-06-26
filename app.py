from flask import Flask, render_template, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import os
import re
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
DATABASE_URL = os.getenv("DATABASE_URL")

# ====================== NFTOKEN LOGIC ======================
def extract_netflix_id(cookie_text):
    match = re.search(r'NetflixId=([^;]+)', cookie_text)
    return match.group(1) if match else None

def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "Missing NetflixId"

    headers = {
        "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
        "Cookie": f"NetflixId={netflix_id}",
    }

    params = {"path": '["account","token","default"]', "responseFormat": "json"}

    try:
        r = requests.get("https://ios.prod.ftl.netflix.com/iosui/user/15.48", 
                        params=params, headers=headers, timeout=20, verify=False)
        data = r.json()
        token = data.get("value", {}).get("account", {}).get("token", {}).get("default", {}).get("token")
        if token:
            return token, None
        return None, "Failed to generate token"
    except Exception as e:
        return None, str(e)

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
        else:
            return render_template("admin_login.html", error="Invalid credentials")
    return render_template("admin_login.html")

@app.route("/api/add_account", methods=["POST"])
def add_account():
    try:
        data = request.get_json()
        cookie_text = data.get("cookie_text", "").strip()

        if not cookie_text:
            return jsonify({"success": False, "error": "Cookie is empty"}), 400

        # Validate with Netflix
        token, error = fetch_nftoken(cookie_text)
        if error or not token:
            return jsonify({"success": False, "error": f"Invalid/Expired cookie: {error}"}), 400

        # Save only active accounts
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

        return jsonify({"success": True, "message": "Account validated and added successfully!"})
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
            # Auto deactivate bad account
            cur.execute("UPDATE netflix_accounts SET is_active = FALSE WHERE id = %s", (account['id'],))
            conn.commit()
            return jsonify({"success": False, "error": "Account expired, trying another..."}), 400

        # Update usage
        cur.execute("""
            UPDATE netflix_accounts 
            SET last_used = CURRENT_TIMESTAMP, usage_count = usage_count + 1 
            WHERE id = %s
        """, (account['id'],))
        conn.commit()

        pc, mobile, tv = (
            f"https://www.netflix.com/?nftoken={token}",
            f"https://www.netflix.com/unsupported?nftoken={token}",
            f"https://www.netflix.com/?nftoken={token}"
        )

        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "pc_link": pc,
            "mobile_link": mobile,
            "tv_link": tv
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/stats")
def stats():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN is_active=TRUE THEN 1 END) as active FROM netflix_accounts")
        stats = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify(stats)
    except:
        return jsonify({"total": 0, "active": 0})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
