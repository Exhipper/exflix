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

# ... (keep your extract_netflix_id and fetch_nftoken functions exactly as they are now)

# Improved add_account with better error handling
@app.route("/api/add_account", methods=["POST"])
def add_account():
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        cookie_text = (data.get("cookie_text") or "").strip()

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

        return jsonify({
            "success": True, 
            "message": "✅ Account validated and added successfully!",
            "account_id": "undefined",
            "token_expires": "N/A"
        })
    except Exception as e:
        logging.error(f"Add account error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Keep other routes (generate, stats, admin_page, etc.)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
