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

# Your proxy lists
PROXY_URLS = [
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks4/data.txt",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt"
]

def load_and_validate_proxies():
    proxies = []
    for url in PROXY_URLS:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                proxies.extend([p.strip() for p in r.text.splitlines() if p.strip()])
        except:
            pass
    proxies = list(set(proxies))  # unique
    working_proxies = []
    test_url = "https://www.netflix.com"
    for p in random.sample(proxies, min(20, len(proxies))):  # test 20 random
        try:
            proxies_dict = {"http": p, "https": p}
            r = requests.get(test_url, proxies=proxies_dict, timeout=8)
            if r.status_code in [200, 403, 429]:
                working_proxies.append(p)
                if len(working_proxies) >= 8:  # keep up to 8 working
                    break
        except:
            pass
    return working_proxies or [""]  # fallback no proxy

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
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, "No NetflixId found"

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
    }

    try:
        time.sleep(random.uniform(0.8, 2.5))
        payload = {
            "operationName": "createAutoLoginToken",
            "variables": {},
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "b8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8"
                }
            }
        }
        r = requests.post("https://www.netflix.com/api/shakti/mdx", json=payload, headers=headers, proxies=proxies_dict, timeout=20, verify=False)
        
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
            return None, f"Failed (Status {r.status_code})"
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
        cur.execute("SELECT * FROM netflix_accounts WHERE is_active = TRUE")
        accounts = cur.fetchall()
        cur.close()
        conn.close()

        if not accounts:
            return jsonify({"success": False, "error": "No active accounts. Add fresh cookies in Admin Panel."}), 404

        for account in accounts:
            time.sleep(random.uniform(0.8, 2.5))
            token, proxy_used = fetch_nftoken(account['cookie_text'])
            if not error and token:  # fixed typo in previous
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
                    "mobile_link": f"{base}/unsupported?nftoken={token}",
                    "tv_link": f"{base}/tv2?nftoken={token}",
                    "proxy_used": proxy_used or "Direct"
                })
        
        return jsonify({"success": False, "error": "All accounts temporarily unavailable. Try again later."}), 400
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
