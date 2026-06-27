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

def extract_netflix_id(cookie_text):
    """Robust parser for multi-line Netscape format"""
    if not cookie_text:
        return None
    for line in cookie_text.splitlines():
        line = line.strip()
        if 'NetflixId' in line:
            match = re.search(r'NetflixId\s*[:=]\s*([^\s;]+)', line, re.IGNORECASE)
            if match:
                return match.group(1)
    return None

def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "No NetflixId found in cookie"
    
    cookie_header = f"NetflixId={netflix_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie_header,
        "Accept": "application/json",
    }
    
    try:
        # Validation + token simulation (align with NFToken-Generator)
        r = requests.get("https://www.netflix.com/api/shakti/mdx/account", headers=headers, timeout=15, verify=False)
        if r.status_code in [200, 204]:
            token = f"nftoken-{netflix_id[:12]}"
            return token, None
        return None, f"Validation failed (Status {r.status_code})"
    except Exception as e:
        logging.error(f"Error: {e}")
        return None, "Connection error"

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
            is_active BOOLEAN DEFAULT TRUE,
            email TEXT,
            plan TEXT,
            country TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("Database ready")

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
        return jsonify({"success": True, "message": "Account validated and added!"})
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
            return jsonify({"success": False, "error": "No active accounts. Add cookies in Admin Panel first."}), 404

        token, error = fetch_nftoken(account['cookie_text'])
        if error or not token:
            cur.execute("UPDATE netflix_accounts SET is_active = FALSE WHERE id = %s", (account['id'],))
            conn.commit()
            return jsonify({"success": False, "error": "Account expired"}), 400

        cur.execute("""
            UPDATE netflix_accounts 
            SET last_used = CURRENT_TIMESTAMP, usage_count = usage_count + 1 
            WHERE id = %s
        """, (account['id'],))
        conn.commit()
        cur.close()
        conn.close()

        base = "https://www.netflix.com"
        return jsonify({
            "success": True,
            "pc_link": f"{base}/?nftoken={token}",
            "mobile_link": f"{base}/unsupported?nftoken={token}",
            "tv_link": f"{base}/?nftoken={token}",
            "email": account.get('email', 'Premium@Account.com'),
            "plan": account.get('plan', 'Premium'),
            "country": account.get('country', 'US')
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

@app.route("/api/accounts")
def list_accounts():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, added_at, last_used, usage_count, is_active FROM netflix_accounts ORDER BY added_at DESC LIMIT 100")
        accounts = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "accounts": accounts})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/deactivate/<int:account_id>", methods=["POST"])
def deactivate_account(account_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE netflix_accounts SET is_active = FALSE WHERE id = %s", (account_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
