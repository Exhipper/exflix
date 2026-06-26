from flask import Flask, render_template, request, jsonify, redirect, url_for
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import json
import os
from datetime import datetime
import random

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'super-secret-key-change-me')

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

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

def setup():
    init_db()

# Call setup on app startup
setup()

# ====================== ROUTES ======================

@app.route("/")
def dashboard():
    return render_template("index.html")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    # ... (rest of your admin logic remains)
    # For brevity I'm showing only the fix part
    pass  # Your full admin code is already here

# Add other routes as they exist in your file...
