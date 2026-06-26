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

    match = re.search(r'([A-Za-z0-9%]{150,})', cookie_text)
    if match and 'ct%3D' in match.group(1):
        return match.group(1)
    return None


# ====================== NFT TOKEN FETCHER ======================
def fetch_nftoken(cookie_text):
    netflix_id = extract_netflix_id(cookie_text)
    if not netflix_id:
        return None, f"Could not find NetflixId. Length: {len(cookie_text)}"

    headers = {
        "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
        "x-netflix.request.attempt": "1",
        "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
        "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
        "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
        "x-netflix.context.app-version": "15.48.1",
        "x-netflix.argo.translated": "true",
        "x-netflix.context.form-factor": "phone",
        "x-netflix.context.sdk-version": "2012.4",
        "x-netflix.client.appversion": "15.48.1",
        "x-netflix.context.max-device-width": "375",
        "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
        "x-netflix.client.type": "argo",
        "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
        "accept-language": "en-US;q=1",
        "x-netflix.request.client.context": '{"appState":"foreground"}',
        "Cookie": f"NetflixId={netflix_id}",
    }

    params = {
        "appVersion": "15.48.1",
        "device_type": "NFAPPL-02-",
        "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
        "idiom": "phone",
        "iosVersion": "15.8.5",
        "isTablet": "false",
        "languages": "en-US",
        "locale": "en-US",
        "maxDeviceWidth": "375",
        "model": "saget",
        "modelType": "IPHONE8-1",
        "odpAware": "true",
        "path": '["account","token","default"]',
        "pathFormat": "graph",
        "pixelDensity": "2.0",
        "progressive": "false",
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
        
        print(f"Netflix Status: {r.status_code} | Response: {r.text[:300]}")
        
        if r.status_code != 200:
            return None, f"Netflix API error {r.status_code}: {r.text[:300]}"

        data = r.json()
        token_path = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
        token = token_path.get("token")

        if token:
            return token, None
        return None, "Failed to generate NFToken (cookie may be expired/invalid)"

    except Exception as e:
        return None, f"Request error: {str(e)[:150]}"


# ====================== DATABASE ======================
def get_db():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS netflix_accounts;")
        cur.execute("""
            CREATE TABLE netflix_accounts (
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
        print("✅ Database table recreated successfully.")
    except Exception as e:
        print(f"⚠️ DB init error: {e}")

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
        data = request.get_json(silent=True) or request.form.to_dict()
        cookie_text = (data.get("cookie_text") or data.get("netflix_cookie") or "").strip()

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
            ON CONFLICT (cookie_text) 
            DO UPDATE SET is_active = TRUE, added_at = CURRENT_TIMESTAMP
        """, (cookie_text,))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "message": "✅ Account validated and added!"})
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

        cur.execute("""
            UPDATE netflix_accounts 
            SET last_used = CURRENT_TIMESTAMP, usage_count = usage_count + 1 
            WHERE id = %s
        """, (account['id'],))
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
