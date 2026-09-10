#!/usr/bin/env python3
"""
PyXUI v2 - a self-hosted X-UI style panel for Xray-core.

Supports VLESS, VMess, and Trojan (all over WebSocket+TLS), per-client
traffic limits with live usage from Xray's stats API, expiry dates,
QR codes, and per-client subscription links.

Run on the VPS as root:  sudo python3 app.py
See docs/INSTALL.md for full setup.
"""

import base64
import io
import json
import os
import secrets
import sqlite3
import subprocess
import uuid
from datetime import datetime, date
from functools import wraps

import qrcode
from flask import (Flask, flash, g, redirect, render_template, request,
                    session, url_for, Response)
from werkzeug.security import check_password_hash, generate_password_hash

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "panel.db")

XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"
XRAY_SERVICE_NAME = "xray"
XRAY_BINARY = "xray"

# Local ports each protocol's inbound listens on (nginx proxies to these).
PROTOCOL_PORTS = {"vless": 10001, "vmess": 10002, "trojan": 10003}
API_PORT = 62789  # Xray stats API, loopback only

app = Flask(__name__)
app.secret_key = os.environ.get("PYXUI_SECRET", os.urandom(24).hex())


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            remark TEXT NOT NULL,
            protocol TEXT NOT NULL DEFAULT 'vless',
            client_uuid TEXT UNIQUE NOT NULL,
            sub_token TEXT UNIQUE NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            traffic_limit_gb REAL NOT NULL DEFAULT 0,
            traffic_used_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expiry_date TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    db.commit()

    cur = db.execute("SELECT COUNT(*) AS c FROM admin")
    if cur.fetchone()["c"] == 0:
        db.execute(
            "INSERT INTO admin (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("admin123")),
        )

    defaults = {
        "domain": "vpn.yourdomain.com",
        "public_port": "443",
        "vless_ws_path": "/vless-ws",
        "vmess_ws_path": "/vmess-ws",
        "trojan_ws_path": "/trojan-ws",
    }
    for k, v in defaults.items():
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )
    db.commit()
    db.close()


def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        row = get_db().execute(
            "SELECT * FROM admin WHERE username = ?", (username,)
        ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Traffic stats (Xray API) + limit/expiry enforcement
# --------------------------------------------------------------------------

def query_traffic_stats():
    """
    Queries Xray's stats API for per-user traffic.
    Returns {email: total_bytes} or {} if the API is unreachable
    (e.g. running locally without a real Xray instance).
    """
    try:
        result = subprocess.run(
            [XRAY_BINARY, "api", "statsquery", f"--server=127.0.0.1:{API_PORT}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except Exception:
        return {}

    totals = {}
    for stat in data.get("stat", []):
        name = stat.get("name", "")
        # format: user>>>EMAIL>>>traffic>>>uplink  (or downlink)
        parts = name.split(">>>")
        if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
            email = parts[1]
            totals[email] = totals.get(email, 0) + int(stat.get("value", 0))
    return totals


def refresh_traffic_usage():
    """Pulls latest stats from Xray and updates the DB. No-op if unreachable."""
    stats = query_traffic_stats()
    if not stats:
        return
    db = get_db()
    for email, total_bytes in stats.items():
        db.execute(
            "UPDATE clients SET traffic_used_bytes = ? WHERE remark = ?",
            (total_bytes, email),
        )
    db.commit()


def enforce_limits():
    """Auto-disables clients that are expired or over their traffic limit."""
    db = get_db()
    today = date.today().isoformat()
    clients = db.execute("SELECT * FROM clients WHERE enabled = 1").fetchall()
    for c in clients:
        expired = c["expiry_date"] and c["expiry_date"] < today
        over_limit = (
            c["traffic_limit_gb"] > 0
            and c["traffic_used_bytes"] >= c["traffic_limit_gb"] * 1_000_000_000
        )
        if expired or over_limit:
            db.execute("UPDATE clients SET enabled = 0 WHERE id = ?", (c["id"],))
    db.commit()


def human_bytes(n):
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# --------------------------------------------------------------------------
# Xray config generation + service control
# --------------------------------------------------------------------------

def build_xray_config():
    db = get_db()
    clients = db.execute("SELECT * FROM clients WHERE enabled = 1").fetchall()

    by_protocol = {"vless": [], "vmess": [], "trojan": []}
    for c in clients:
        by_protocol.setdefault(c["protocol"], []).append(c)

    inbounds = [
        {
            "listen": "127.0.0.1",
            "port": API_PORT,
            "protocol": "dokodemo-door",
            "settings": {"address": "127.0.0.1"},
            "tag": "api",
        }
    ]

    # VLESS
    inbounds.append({
        "listen": "127.0.0.1",
        "port": PROTOCOL_PORTS["vless"],
        "protocol": "vless",
        "settings": {
            "clients": [
                {"id": c["client_uuid"], "email": c["remark"]}
                for c in by_protocol["vless"]
            ],
            "decryption": "none",
        },
        "streamSettings": {
            "network": "ws",
            "wsSettings": {"path": get_setting("vless_ws_path", "/vless-ws")},
        },
    })

    # VMess
    inbounds.append({
        "listen": "127.0.0.1",
        "port": PROTOCOL_PORTS["vmess"],
        "protocol": "vmess",
        "settings": {
            "clients": [
                {"id": c["client_uuid"], "email": c["remark"], "alterId": 0}
                for c in by_protocol["vmess"]
            ],
        },
        "streamSettings": {
            "network": "ws",
            "wsSettings": {"path": get_setting("vmess_ws_path", "/vmess-ws")},
        },
    })

    # Trojan (uses client_uuid as the password for simplicity)
    inbounds.append({
        "listen": "127.0.0.1",
        "port": PROTOCOL_PORTS["trojan"],
        "protocol": "trojan",
        "settings": {
            "clients": [
                {"password": c["client_uuid"], "email": c["remark"]}
                for c in by_protocol["trojan"]
            ],
        },
        "streamSettings": {
            "network": "ws",
            "wsSettings": {"path": get_setting("trojan_ws_path", "/trojan-ws")},
        },
    })

    config = {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
            },
        },
        "inbounds": inbounds,
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "freedom", "tag": "api"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"}
            ]
        },
    }
    return config


def write_and_reload_xray():
    config = build_xray_config()
    try:
        os.makedirs(os.path.dirname(XRAY_CONFIG_PATH), exist_ok=True)
        with open(XRAY_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except PermissionError:
        return False, (
            f"Could not write {XRAY_CONFIG_PATH} (permission denied). "
            "Run the panel as root, e.g. `sudo python3 app.py`."
        )
    except Exception as e:
        return False, f"Failed to write config: {e}"

    try:
        subprocess.run(
            ["systemctl", "restart", XRAY_SERVICE_NAME],
            check=True, capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return False, "systemctl not found — is this running on the VPS itself?"
    except subprocess.CalledProcessError as e:
        return False, f"xray restart failed: {e.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "xray restart timed out"

    return True, "Config written and xray restarted successfully."


def xray_service_status():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", XRAY_SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def make_client_link(client):
    domain = get_setting("domain")
    port = get_setting("public_port", "443")
    protocol = client["protocol"]
    remark = client["remark"]
    cid = client["client_uuid"]

    if protocol == "vless":
        path = get_setting("vless_ws_path", "/vless-ws")
        return (f"vless://{cid}@{domain}:{port}"
                f"?type=ws&security=tls&path={path}&host={domain}&sni={domain}#{remark}")

    if protocol == "vmess":
        path = get_setting("vmess_ws_path", "/vmess-ws")
        vmess_obj = {
            "v": "2", "ps": remark, "add": domain, "port": str(port),
            "id": cid, "aid": "0", "net": "ws", "type": "none",
            "host": domain, "path": path, "tls": "tls", "sni": domain,
        }
        b64 = base64.b64encode(json.dumps(vmess_obj).encode()).decode()
        return f"vmess://{b64}"

    if protocol == "trojan":
        path = get_setting("trojan_ws_path", "/trojan-ws")
        return (f"trojan://{cid}@{domain}:{port}"
                f"?type=ws&security=tls&path={path}&host={domain}&sni={domain}#{remark}")

    return ""


def qr_data_uri(text):
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    refresh_traffic_usage()
    enforce_limits()
    db = get_db()
    clients = db.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()

    enriched = []
    for c in clients:
        d = dict(c)
        d["link"] = make_client_link(c)
        d["used_human"] = human_bytes(c["traffic_used_bytes"])
        d["limit_human"] = f"{c['traffic_limit_gb']} GB" if c["traffic_limit_gb"] else "Unlimited"
        if c["traffic_limit_gb"]:
            pct = min(100, round(c["traffic_used_bytes"] / (c["traffic_limit_gb"] * 1_000_000_000) * 100))
        else:
            pct = 0
        d["pct_used"] = pct
        enriched.append(d)

    return render_template(
        "dashboard.html",
        clients=enriched,
        xray_status=xray_service_status(),
        domain=get_setting("domain"),
    )


@app.route("/clients/add", methods=["GET", "POST"])
@login_required
def add_client():
    if request.method == "POST":
        remark = request.form.get("remark", "").strip() or "client"
        protocol = request.form.get("protocol", "vless")
        if protocol not in PROTOCOL_PORTS:
            protocol = "vless"
        expiry = request.form.get("expiry_date", "").strip() or None
        try:
            traffic_limit = float(request.form.get("traffic_limit_gb", "0") or 0)
        except ValueError:
            traffic_limit = 0

        client_uuid = str(uuid.uuid4())
        sub_token = secrets.token_urlsafe(16)

        db = get_db()
        db.execute(
            "INSERT INTO clients "
            "(remark, protocol, client_uuid, sub_token, enabled, traffic_limit_gb, "
            " traffic_used_bytes, created_at, expiry_date) "
            "VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?)",
            (remark, protocol, client_uuid, sub_token, traffic_limit,
             datetime.utcnow().isoformat(), expiry),
        )
        db.commit()

        ok, msg = write_and_reload_xray()
        flash(msg, "success" if ok else "error")
        return redirect(url_for("dashboard"))

    return render_template("add_client.html")


@app.route("/clients/<int:client_id>/qr")
@login_required
def client_qr(client_id):
    db = get_db()
    row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not row:
        return "Not found", 404
    link = make_client_link(row)
    return render_template("qr.html", link=link, qr=qr_data_uri(link), remark=row["remark"])


@app.route("/clients/<int:client_id>/toggle", methods=["POST"])
@login_required
def toggle_client(client_id):
    db = get_db()
    row = db.execute("SELECT enabled FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row:
        db.execute("UPDATE clients SET enabled = ? WHERE id = ?",
                   (0 if row["enabled"] else 1, client_id))
        db.commit()
        ok, msg = write_and_reload_xray()
        flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard"))


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    db = get_db()
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    db.commit()
    ok, msg = write_and_reload_xray()
    flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard"))


@app.route("/sub/<sub_token>")
def subscription(sub_token):
    """Public subscription endpoint: base64 of the client's link. No login —
    the random token is the secret, same convention X-UI uses."""
    db = get_db()
    row = db.execute("SELECT * FROM clients WHERE sub_token = ?", (sub_token,)).fetchone()
    if not row or not row["enabled"]:
        return Response("", status=404)
    link = make_client_link(row)
    body = base64.b64encode(link.encode()).decode()
    return Response(body, mimetype="text/plain")


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        for key in ("domain", "public_port", "vless_ws_path",
                    "vmess_ws_path", "trojan_ws_path"):
            value = request.form.get(key, "").strip()
            if value:
                set_setting(key, value)

        new_password = request.form.get("new_password", "").strip()
        if new_password:
            db = get_db()
            db.execute(
                "UPDATE admin SET password_hash = ? WHERE username = ?",
                (generate_password_hash(new_password), session["username"]),
            )
            db.commit()
            flash("Password updated.", "success")

        ok, msg = write_and_reload_xray()
        flash(msg, "success" if ok else "error")
        return redirect(url_for("settings"))

    keys = ("domain", "public_port", "vless_ws_path", "vmess_ws_path", "trojan_ws_path")
    values = {k: get_setting(k) for k in keys}
    return render_template("settings.html", **values)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=2053, debug=False)
