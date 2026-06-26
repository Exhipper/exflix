#!/usr/bin/env python3
"""
Premium Account Gen - Flask app
Mimics the beautiful dark UI from your screenshot.
Netflix NFToken generation using adapted logic from harshitkamboj/Netflix-NFToken-Generator
"""

import os
import re
import json
import urllib.parse
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-to-a-long-random-string-in-production")
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"  # Change via env var in production

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("WARNING: DATABASE_URL not set. Set it in environment for production.")

# ====================== COOKIE & NFTOKEN LOGIC (adapted from the generator) ======================

COOKIE_KEYS = ("NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent")
REQUIRED_COOKIE = "NetflixId"

API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"

QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
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

BASE_HEADERS = {
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
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

def parse_netscape_cookie_line(line):
    parts = line.strip().split("\t")
    if len(parts) >= 7:
        return {parts[5]: parts[6]}
    return {}

def _decode_cookie_value(value):
    if isinstance(value, str) and "%" in value:
        try:
            return urllib.parse.unquote(value)
        except Exception:
            return value
    return value

def extract_cookie_dict(text):
    """Parse cookie from raw string, Netscape format, or JSON (supports the checker output formats)."""
    cookie_dict = {}

    # Netscape / TSV lines
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cookie_dict.update(parse_netscape_cookie_line(line))

    # JSON
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, list):
        for cookie in data:
            name = cookie.get("name")
            value = cookie.get("value")
            if name in COOKIE_KEYS and isinstance(value, str):
                cookie_dict[name] = _decode_cookie_value(value)
    elif isinstance(data, dict):
        if any(key in data for key in COOKIE_KEYS):
            for key in COOKIE_KEYS:
                value = data.get(key)
                if isinstance(value, str):
                    cookie_dict[key] = _decode_cookie_value(value)
        elif isinstance(data.get("cookies"), list):
            for cookie in data["cookies"]:
                name = cookie.get("name")
                value = cookie.get("value")
                if name in COOKIE_KEYS and isinstance(value, str):
                    cookie_dict[name] = _decode_cookie_value(value)

    # Regex fallback for raw "Key=Value; Key2=Value2"
    for key in COOKIE_KEYS:
        if key in cookie_dict:
            continue
        match = re.search(rf"(?<!\w){re.escape(key)}=([^;,\s]+)", text)
        if match:
            cookie_dict[key] = _decode_cookie_value(match.group(1))

    return cookie_dict

def fetch_nftoken(cookie_dict):
    """Generate NFToken using Netflix's internal endpoint (adapted from the public generator)."""
    netflix_id = cookie_dict.get(REQUIRED_COOKIE)
    if not netflix_id:
        raise ValueError("Missing required cookie: NetflixId. Make sure the cookie is valid and contains NetflixId.")

    headers = dict(BASE_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"

    response = requests.get(
        API_URL,
        params=QUERY_PARAMS,
        headers=headers,
        timeout=25,
        verify=False,  # as in original generator
    )
    response.raise_for_status()

    data = response.json()
    token_data = (
        (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default")
        or {}
    )
    token = token_data.get("token")
    expires = token_data.get("expires")

    if not token:
        raise ValueError("No token returned by Netflix. Cookie may be expired, invalid, or account has issues (on-hold, etc.).")

    if isinstance(expires, int) and len(str(expires)) == 13:
        expires = expires // 1000

    return token, expires

def build_watch_links(token):
    """Return PC, Mobile, TV variants. Customize TV if you have a better URL."""
    pc_link = f"https://www.netflix.com/?nftoken={token}"
    mobile_link = f"https://www.netflix.com/unsupported?nftoken={token}"
    # TV: Many people just use the PC link on TV browser, or you can experiment with https://www.netflix.com/tv
    tv_link = f"https://www.netflix.com/?nftoken={token}"
    return pc_link, mobile_link, tv_link

# ====================== DATABASE ======================

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set. Please configure it on Render.")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Create table if it doesn't exist."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS netflix_accounts (
                id SERIAL PRIMARY KEY,
                cookie_text TEXT NOT NULL,
                plan VARCHAR(100) DEFAULT 'Premium',
                country VARCHAR(10) DEFAULT 'US',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP,
                usage_count INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE
            );
            CREATE INDEX IF NOT EXISTS idx_netflix_active_last_used ON netflix_accounts (is_active, last_used NULLS FIRST, usage_count);
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database table initialized successfully.")
    except Exception as e:
        print(f"❌ Database initialization error: {e}")

# ====================== FLASK ROUTES ======================

def setup():
    init_db()

# Call setup on app startup
setup()

@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    """Return simple stats for dashboard (total accounts, active, etc.)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN is_active = TRUE THEN 1 END) as active,
                COUNT(CASE WHEN last_used IS NOT NULL THEN 1 END) as used
            FROM netflix_accounts
        """)
        stats = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({
            "success": True,
            "total_accounts": stats["total"] or 0,
            "active_accounts": stats["active"] or 0,
            "used_accounts": stats["used"] or 0
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/analytics")
def api_analytics():
    """Detailed analytics for the analytics page"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Overall stats
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN is_active = TRUE THEN 1 END) as active,
                COALESCE(SUM(usage_count), 0) as total_uses
            FROM netflix_accounts
        """)
        stats = cur.fetchone()

        # Recent accounts
        cur.execute("""
            SELECT id, plan, country, 
                   TO_CHAR(last_used, 'YYYY-MM-DD HH24:MI') as last_used,
                   usage_count
            FROM netflix_accounts 
            ORDER BY last_used DESC NULLS LAST, usage_count DESC 
            LIMIT 15
        """)
        recent = cur.fetchall()
        
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "total": stats["total"] or 0,
            "active": stats["active"] or 0,
            "total_uses": stats["total_uses"] or 0,
            "recent": [dict(row) for row in recent]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/admin", methods=["GET", "POST"])
def admin_page():
    """Simple form-based login for admin"""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            return render_template("admin.html", admin_pass=ADMIN_PASSWORD)
        else:
            return render_template("admin_login.html", error="Invalid credentials"), 401

    # GET - show login form
    return render_template("admin_login.html")

@app.route("/analytics")
def analytics_page():
    if request.args.get("pass") != ADMIN_PASSWORD:
        return redirect(url_for('admin_page'))
    return render_template("analytics.html")

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Main endpoint called by the frontend Generate button."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Smart selection: least recently used + lowest usage + random tiebreaker
        cur.execute("""
            SELECT id, cookie_text, plan, country, usage_count 
            FROM netflix_accounts 
            WHERE is_active = TRUE 
            ORDER BY last_used NULLS FIRST, usage_count ASC, RANDOM() 
            LIMIT 1
        """)
        account = cur.fetchone()

        if not account:
            cur.close()
            conn.close()
            return jsonify({
                "success": False,
                "error": "No active Netflix accounts in the database. Please add some using the /admin page."
            }), 404

        cookie_text = account["cookie_text"]

        # Generate fresh NFToken
        cookie_dict = extract_cookie_dict(cookie_text)
        
        try:
            token, expires = fetch_nftoken(cookie_dict)
        except Exception as e:
            # Auto-mark as inactive if token generation fails
            cur.execute("UPDATE netflix_accounts SET is_active = FALSE WHERE id = %s", (account["id"],))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({
                "success": False, 
                "error": f"Account {account['id']} failed validation. It has been deactivated. Try Generate Again."
            }), 400

        # Update stats
        cur.execute("""
            UPDATE netflix_accounts 
            SET last_used = NOW(), usage_count = usage_count + 1 
            WHERE id = %s
        """, (account["id"],))
        conn.commit()

        cur.close()
        conn.close()

        pc_link, mobile_link, tv_link = build_watch_links(token)

        return jsonify({
            "success": True,
            "message": "Fresh premium account generated successfully!",
            "account_info": {
                "plan": account.get("plan", "Premium"),
                "country": account.get("country", "Unknown"),
                "usage_count": account["usage_count"] + 1,
                "account_id": account["id"]
            },
            "token": token,
            "expires": datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S") if isinstance(expires, (int, float)) else str(expires),
            "pc_link": pc_link,
            "mobile_link": mobile_link,
            "tv_link": tv_link
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Netflix request failed: {str(e)}"}), 502
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {str(e)}"}), 500

@app.route("/api/add_account", methods=["POST"])
def api_add_account():
    """Admin endpoint to add + validate a new cookie. Simplified - no plan/country."""
    data = request.get_json(silent=True) or request.form.to_dict()

    if data.get("admin_pass") != ADMIN_PASSWORD:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    cookie_text = (data.get("cookie_text") or "").strip()

    if not cookie_text or len(cookie_text) < 20:
        return jsonify({"success": False, "error": "Cookie text is too short or empty"}), 400

    try:
        # Validate immediately by generating NFToken (only active subscriptions saved)
        cookie_dict = extract_cookie_dict(cookie_text)
        token, expires = fetch_nftoken(cookie_dict)

        # Insert into DB - simplified
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO netflix_accounts (cookie_text, plan, country)
            VALUES (%s, 'Premium', 'US')
            RETURNING id
        """, (cookie_text,))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Account validated & added successfully! (Premium - US)",
            "account_id": new_id,
            "token_expires": datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S") if isinstance(expires, (int, float)) else str(expires)
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/health")
def health():
    return {"status": "ok", "service": "premium-account-gen"}


@app.route("/api/cleanup", methods=["POST"])
def api_cleanup():
    """Auto cleanup inactive accounts (older than 30 days or failed)"""
    try:
        if request.args.get("pass") != ADMIN_PASSWORD:
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        conn = get_db_connection()
        cur = conn.cursor()
        
        # Mark accounts inactive if not used in 30+ days
        cur.execute("""
            UPDATE netflix_accounts 
            SET is_active = FALSE 
            WHERE is_active = TRUE 
            AND last_used < NOW() - INTERVAL '30 days'
        """)
        
        # Delete very old inactive accounts (optional)
        cur.execute("""
            DELETE FROM netflix_accounts 
            WHERE is_active = FALSE 
            AND added_at < NOW() - INTERVAL '90 days'
        """)
        
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True, 
            "message": f"Cleanup completed. {deleted} old inactive accounts removed."
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/export_accounts")
def export_accounts():
    """View all accounts as a simple HTML table (no file download)"""
    if request.args.get("pass") != ADMIN_PASSWORD:
        return redirect(url_for('admin_page'))
    
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, plan, country, 
                   TO_CHAR(added_at, 'YYYY-MM-DD HH24:MI') as added_at,
                   TO_CHAR(last_used, 'YYYY-MM-DD HH24:MI') as last_used,
                   usage_count, is_active
            FROM netflix_accounts 
            ORDER BY added_at DESC
        """)
        accounts = cur.fetchall()
        cur.close()
        conn.close()

        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Accounts Export</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-[#020617] text-slate-200 p-8">
            <h1 class="text-3xl font-bold mb-8">All Netflix Accounts</h1>
            <table class="w-full border-collapse">
                <thead>
                    <tr class="bg-slate-800">
                        <th class="p-3 text-left">ID</th>
                        <th class="p-3 text-left">Plan</th>
                        <th class="p-3 text-left">Country</th>
                        <th class="p-3 text-left">Added</th>
                        <th class="p-3 text-left">Last Used</th>
                        <th class="p-3 text-center">Uses</th>
                        <th class="p-3 text-center">Active</th>
                    </tr>
                </thead>
                <tbody>
        """
        for acc in accounts:
            html += f"""
                <tr class="border-b border-slate-700 hover:bg-slate-900">
                    <td class="p-3 font-mono">{acc['id']}</td>
                    <td class="p-3">{acc['plan']}</td>
                    <td class="p-3">{acc['country']}</td>
                    <td class="p-3 text-slate-400">{acc['added_at']}</td>
                    <td class="p-3 text-slate-400">{acc['last_used'] or 'Never'}</td>
                    <td class="p-3 text-center font-semibold">{acc['usage_count']}</td>
                    <td class="p-3 text-center">
                        <span class="inline-block px-3 py-1 rounded-full text-xs {'bg-emerald-900 text-emerald-400' if acc['is_active'] else 'bg-red-900 text-red-400'}">
                            {'YES' if acc['is_active'] else 'NO'}
                        </span>
                    </td>
                </tr>
            """
        html += """
                </tbody>
            </table>
            <div class="mt-8 text-sm text-slate-500">Total: """ + str(len(accounts)) + """ accounts • Auto-refreshes on reload</div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1>"

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
