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

def parse_cookie(cookie_text):
    """Improved parser for multi-line Netscape/JSON/raw cookies"""
    if not cookie_text:
        return None
    lines = cookie_text.strip().split('\n')
    netflix_id = None
    secure_id = None
    for line in lines:
        line = line.strip()
        if 'NetflixId' in line:
            match = re.search(r'NetflixId\s*[:=]\s*([^\s;]+)', line, re.IGNORECASE)
            if match:
                netflix_id = match.group(1)
        if 'SecureNetflixId' in line:
            match = re.search(r'SecureNetflixId\s*[:=]\s*([^\s;]+)', line, re.IGNORECASE)
            if match:
                secure_id = match.group(1)
    return netflix_id, secure_id

def fetch_nftoken(cookie_text):
    netflix_id, secure_id = parse_cookie(cookie_text)
    if not netflix_id:
        return None, "No NetflixId found in cookie"
    
    cookie_header = f"NetflixId={netflix_id}"
    if secure_id:
        cookie_header += f"; SecureNetflixId={secure_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie_header,
    }
    
    try:
        r = requests.get("https://www.netflix.com/api/shakti/mdx/account", headers=headers, timeout=15, verify=False)
        if r.status_code in [200, 204]:
            # Simulate token (in real implementation call GraphQL createAutoLoginToken)
            token = "generated-nftoken-" + netflix_id[:8]
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

# Routes (same as before + enhanced generate)
@app.route("/")
def dashboard():
    return render_template("index.html")

# ... (keep all other routes: /admin, /api/add_account, /api/stats, /api/accounts, /api/deactivate)

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
            return jsonify({"success": False, "error": "No active accounts"}), 404

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
            "email": account.get('email', 'unknown@email.com'),
            "plan": account.get('plan', 'Premium'),
            "country": account.get('country', 'US')
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
