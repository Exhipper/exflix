from flask import Flask, render_template, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import os
import re
import random
import time
from urllib3.exceptions import InsecureRequestWarning
import logging

logging.basicConfig(level=logging.INFO)
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key-change-in-production")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DATABASE_URL = os.getenv("DATABASE_URL")

# Proxy lists (lightweight)
PROXY_URLS = [
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt",
]

def load_and_validate_proxies():
    proxies = []
    for url in PROXY_URLS:
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                proxies.extend([p.strip() for p in r.text.splitlines() if p.strip()])
        except:
            pass
    proxies = list(set(proxies))
    working = []
    test_url = "https://www.netflix.com"
    for p in random.sample(proxies, min(15, len(proxies))):
        try:
            r = requests.get(test_url, proxies={"http": p, "https": p}, timeout=6)
            if r.status_code in [200, 403, 429]:
                working.append(p)
                if len(working) >= 6:
                    break
        except:
            pass
    return working or [""]

WORKING_PROXIES = load_and_validate_proxies()

def get_proxy():
    return random.choice(WORKING_PROXIES)

def extract_netflix_id(cookie_text):
    if not cookie_text:
        return None
    text = cookie_text.strip()
    match = re.search(r'NetflixId\s*[:=]\s*([^\s;]+)', text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r'ct%3D([A-Za-z0-9%._-]+)', text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def fetch_nftoken(cookie_text):
    """
    Netflix nfToken generator.
    CRITICAL: Update the sha256Hash when it stops working.
    How: Login to Netflix → DevTools → Network → search "createAutoLoginToken" or "mdx" → copy real sha256Hash.
    """
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "No NetflixId found in cookie"

    proxy = get_proxy()
    proxies_dict = {"http": proxy, "https": proxy} if proxy else None

    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        ]),
        "Cookie": f"NetflixId={netflix_id}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.netflix.com",
        "Referer": "https://www.netflix.com/",
    }

    try:
        time.sleep(random.uniform(0.6, 1.8))
        payload = {
            "operationName": "createAutoLoginToken",
            "variables": {},
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "b8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8"  # ← UPDATE THIS
                }
            }
        }
        r = requests.post(
            "https://www.netflix.com/api/shakti/mdx",
            json=payload,
            headers=headers,
            proxies=proxies_dict,
            timeout=25,
            verify=False
        )

        token = None
        if r.status_code == 200:
            try:
                data = r.json()
                token = data.get('data', {}).get('createAutoLoginToken', {}).get('token')
            except:
                pass

        if token:
            return token, proxy
        else:
            return None, f"Netflix rejected (status {r.status_code})"
    except Exception as e:
        logging.error(f"fetch_nftoken error: {e}")
        return None, "Connection/proxy error"

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")
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
            return jsonify({"success": False, "error": "Cookie is empty"}), 400

        token, err = fetch_nftoken(cookie_text)
        if err:
            return jsonify({"success": False, "error": err}), 400

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
        return jsonify({"success": True, "message": "Account validated and injected into vault!"})
    except Exception as e:
        logging.error(f"add_account error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/generate")
def generate_account():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM netflix_accounts 
            WHERE is_active = TRUE 
            ORDER BY last_used ASC NULLS FIRST, usage_count ASC 
            LIMIT 12
        """)
        accounts = cur.fetchall()
        cur.close()
        conn.close()

        if not accounts:
            return jsonify({
                "success": False, 
                "error": "No active accounts in vault. Go to /admin and add fresh cookies."
            }), 404

        for account in accounts:
            time.sleep(random.uniform(0.7, 2.0))
            token, proxy_used = fetch_nftoken(account['cookie_text'])
            
            if token:  # SUCCESS
                conn = get_db()
                cur = conn.cursor()
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
                    "mobile_link": f"{base}/?nftoken={token}",
                    "tv_link": f"{base}/?nftoken={token}"
                })

        # All failed
        return jsonify({
            "success": False, 
            "error": "All active cookies failed to generate a token. They are probably expired — re-validate or add new ones."
        }), 500

    except Exception as e:
        logging.error(f"generate_account error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/stats")
def get_stats():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as total, 
                   COUNT(CASE WHEN is_active = TRUE THEN 1 END) as active 
            FROM netflix_accounts
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({"total": row[0], "active": row[1] or 0})
    except Exception as e:
        logging.error(f"Stats error: {e}")
        return jsonify({"total": 0, "active": 0})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
