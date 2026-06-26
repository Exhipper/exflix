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

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key-change-in-production")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DATABASE_URL = os.getenv("DATABASE_URL")

# ====================== COOKIE PARSER ======================
def extract_netflix_id(cookie_text):
    if not cookie_text:
        return None
    cookie_text = cookie_text.strip()
    match = re.search(r'NetflixId\s*[:=]\s*([^\s;]+)', cookie_text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r'(ct%3D[A-Za-z0-9%._-]+)', cookie_text)
    if match:
        return match.group(1)
    return None

# ====================== RICH METADATA ======================
def fetch_account_metadata(netflix_id, nftoken):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Cookie": f"NetflixId={netflix_id}",
            "x-netflix.nftoken": nftoken,
        }
        r = requests.get("https://www.netflix.com/api/shakti/mdx/account", 
                        headers=headers, timeout=15, verify=False)
        if r.status_code == 200:
            data = r.json()
            return {
                "email": data.get("email") or "N/A",
                "plan": data.get("plan") or "Premium",
                "country": data.get("country") or "Unknown",
                "renewal": data.get("renewalDate") or "N/A"
            }
    except Exception as e:
        logging.warning(f"Metadata failed: {e}")
    return {"email": "N/A", "plan": "Premium", "country": "Unknown", "renewal": "N/A"}

# ====================== NFT TOKEN ======================
def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, None, "No NetflixId found"

    headers = { ... }  # (same as your current working headers)
    params = { ... }   # (same as your current working params)

    try:
        r = requests.get(
            "https://ios.prod.ftl.netflix.com/iosui/user/15.48",
            params=params, headers=headers, timeout=30, verify=False
        )
        
        print(f"🔍 Netflix Token Status: {r.status_code} | Length: {len(r.text)}")
        logging.info(f"Netflix response: {r.text[:300]}")

        if r.status_code != 200:
            return None, None, f"API error {r.status_code}"

        data = r.json()
        token_path = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
        token = token_path.get("token")

        if token:
            metadata = fetch_account_metadata(netflix_id, token)
            return token, metadata, None
        return None, None, "No token in response"
    except Exception as e:
        logging.error(f"Token error: {e}")
        return None, None, str(e)

# ====================== DATABASE (Fixed) ======================
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
            email TEXT,
            plan TEXT,
            country TEXT,
            renewal_date TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP,
            usage_count INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database ready (no drop on redeploy)")

init_db()

# ====================== ROUTES (Updated with Rich Info) ======================
@app.route("/api/add_account", methods=["POST"])
def add_account():
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        cookie_text = (data.get("cookie_text") or "").strip()

        if not cookie_text:
            return jsonify({"success": False, "error": "Cookie empty"}), 400

        token, metadata, error = fetch_nftoken(cookie_text)
        if error or not token:
            return jsonify({"success": False, "error": f"Invalid: {error}"}), 400

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO netflix_accounts (cookie_text, nftoken, email, plan, country, renewal_date, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (cookie_text) DO UPDATE 
            SET nftoken = EXCLUDED.nftoken,
                email = EXCLUDED.email,
                plan = EXCLUDED.plan,
                country = EXCLUDED.country,
                renewal_date = EXCLUDED.renewal_date,
                is_active = TRUE,
                added_at = CURRENT_TIMESTAMP
        """, (cookie_text, token, metadata.get("email"), metadata.get("plan"),
              metadata.get("country"), metadata.get("renewal")))
        conn.commit()
        cur.close()
        conn.close()

        msg = f"""
        ✅ Account validated and added!<br>
        👤 Email: {metadata.get('email', 'N/A')}<br>
        📋 Plan: {metadata.get('plan', 'Premium')}<br>
        🌍 Country: {metadata.get('country', 'Unknown')}
        """
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        logging.error(f"Add error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# (Keep your existing /api/generate and /api/stats routes - they are fine)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
