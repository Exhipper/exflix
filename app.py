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

# Cookie parser
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

# Metadata
def fetch_account_metadata(netflix_id, nftoken):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Cookie": f"NetflixId={netflix_id}",
            "x-netflix.nftoken": nftoken,
        }
        r = requests.get("https://www.netflix.com/api/shakti/mdx/account", headers=headers, timeout=15, verify=False)
        if r.status_code == 200:
            data = r.json()
            return {
                "email": data.get("email") or "N/A",
                "plan": data.get("plan") or "Premium",
                "country": data.get("country") or "Unknown",
                "renewal": data.get("renewalDate") or "N/A"
            }
    except:
        pass
    return {"email": "N/A", "plan": "Premium", "country": "Unknown", "renewal": "N/A"}

# NFT Token
def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, None, "Could not find NetflixId"

    headers = {
        "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
        "x-netflix.request.attempt": "1",
        "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
        "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
        "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
        "x-netflix.context.app-version": "15.48.1",
        "x-netflix.argo.translated": "true",
        "x-netflix.context.form-factor": "phone",
        "Cookie": f"NetflixId={netflix_id}",
    }

    params = {
        "appVersion": "15.48.1",
        "device_type": "NFAPPL-02-",
        "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
        "idiom": "phone",
        "iosVersion": "15.8.5",
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
        print(f"🔍 Netflix Token Status: {r.status_code}")
        logging.info(f"Netflix response length: {len(r.text)}")

        if r.status_code != 200:
            return None, None, f"Netflix API error {r.status_code}"

        data = r.json()
        token_path = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
        token = token_path.get("token")

        if token:
            metadata = fetch_account_metadata(netflix_id, token)
            return token, metadata, None
        return None, None, "Failed to generate NFToken"
    except Exception as e:
        logging.error(f"Token error: {e}")
        return None, None, str(e)[:100]

# Database
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
    print("✅ Database ready")

init_db()

# Routes
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

# ... (keep the rest of your add_account, generate_account, stats routes as they were in the previous full code)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
