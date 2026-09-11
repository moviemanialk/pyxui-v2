#!/usr/bin/env python3
"""
PyXUI v4 - advanced self-hosted panel for Xray-core + native SSH tunnels.

New in v4:
  - VLESS+TCP+REALITY  — clones a real site's TLS handshake (e.g. a big
    CDN-fronted domain) so the connection is indistinguishable from a
    normal visit to that site, with no certificate/domain of your own
    needed for this protocol specifically.
  - Shadowsocks (2022-blake3-aes-256-gcm, multi-user)
  - SSH-over-TLS ("sshtls") — the SSH account type wrapped in stunnel,
    for clients like HTTP Injector that expect "SSL + SSH"
  - Traffic history chart (global, sampled over time)
  - Per-client online/last-active indicator
  - Client editing (remark / traffic limit / expiry) without delete+recreate
  - Login lockout after repeated failed attempts

Run on the VPS as root:  sudo python3 app.py
See docs/ for full setup — several new pieces (REALITY, Shadowsocks link
formats, stunnel) are noted where they need on-VPS verification against
your installed Xray/stunnel version.
"""

import base64
import io
import json
import os
import re
import secrets
import sqlite3
import subprocess
import uuid
from datetime import datetime, date, timedelta
from functools import wraps

import psutil
import qrcode
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization
from flask import (Flask, flash, g, redirect, render_template, request,
                    session, url_for, Response, jsonify)
from werkzeug.security import check_password_hash, generate_password_hash

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "panel.db")

XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"
XRAY_SERVICE_NAME = "xray"
XRAY_BINARY = "xray"
STUNNEL_CONFIG_PATH = "/etc/stunnel/stunnel.conf"
STUNNEL_SERVICE_NAME = "stunnel4"

# Local ports for nginx-fronted (WS+TLS) protocols
PROTOCOL_PORTS = {"vless": 10001, "vmess": 10002, "trojan": 10003}
API_PORT = 62789

# Direct-listen protocols (no nginx in front — see docs/REALITY.md / docs/SHADOWSOCKS.md)
DEFAULT_REALITY_PORT = 8443
DEFAULT_SS_PORT = 8388
DEFAULT_SSHTLS_PORT = 444

SSH_TUNNEL_SHELL = "/usr/sbin/nologin"
SSH_PORT = "22"

PROTOCOLS = ("vless", "vmess", "trojan", "reality", "ss", "ssh", "sshtls")
XRAY_MANAGED_PROTOCOLS = ("vless", "vmess", "trojan", "reality", "ss")
SYSTEM_SSH_PROTOCOLS = ("ssh", "sshtls")

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15

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
            last_seen_bytes INTEGER NOT NULL DEFAULT 0,
            last_active_at TEXT,
            created_at TEXT NOT NULL,
            expiry_date TEXT,
            ssh_username TEXT,
            ssh_password TEXT,
            ss_password TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS traffic_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            total_bytes INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT PRIMARY KEY,
            fail_count INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT
        );
        """
    )
    db.commit()

    # Migrations for DBs created by earlier versions
    existing_cols = {row["name"] for row in db.execute("PRAGMA table_info(clients)")}
    for col, coltype in [
        ("ssh_username", "TEXT"), ("ssh_password", "TEXT"),
        ("ss_password", "TEXT"), ("last_seen_bytes", "INTEGER NOT NULL DEFAULT 0"),
        ("last_active_at", "TEXT"),
    ]:
        if col not in existing_cols:
            db.execute(f"ALTER TABLE clients ADD COLUMN {col} {coltype}")
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
        "reality_port": str(DEFAULT_REALITY_PORT),
        "reality_dest": "www.microsoft.com:443",
        "reality_server_name": "www.microsoft.com",
        "reality_private_key": "",
        "reality_public_key": "",
        "reality_short_id": secrets.token_hex(4),
        "ss_port": str(DEFAULT_SS_PORT),
        "ss_method": "2022-blake3-aes-256-gcm",
        "ss_server_psk": base64.b64encode(os.urandom(32)).decode(),
        "sshtls_port": str(DEFAULT_SSHTLS_PORT),
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    db.commit()

    # Auto-generate REALITY keypair on first run if empty
    row = db.execute("SELECT value FROM settings WHERE key='reality_private_key'").fetchone()
    if row and not row["value"]:
        priv, pub = generate_reality_keypair()
        db.execute("UPDATE settings SET value=? WHERE key='reality_private_key'", (priv,))
        db.execute("UPDATE settings SET value=? WHERE key='reality_public_key'", (pub,))
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
# Auth (with lockout)
# --------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def is_locked_out(username):
    db = get_db()
    row = db.execute("SELECT * FROM login_attempts WHERE username = ?", (username,)).fetchone()
    if row and row["locked_until"]:
        if datetime.utcnow().isoformat() < row["locked_until"]:
            return True
    return False


def record_login_failure(username):
    db = get_db()
    row = db.execute("SELECT * FROM login_attempts WHERE username = ?", (username,)).fetchone()
    fail_count = (row["fail_count"] if row else 0) + 1
    locked_until = None
    if fail_count >= LOGIN_MAX_ATTEMPTS:
        locked_until = (datetime.utcnow() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat()
        fail_count = 0  # reset counter once locked
    db.execute(
        "INSERT INTO login_attempts (username, fail_count, locked_until) VALUES (?, ?, ?) "
        "ON CONFLICT(username) DO UPDATE SET fail_count = excluded.fail_count, "
        "locked_until = excluded.locked_until",
        (username, fail_count, locked_until),
    )
    db.commit()
    return locked_until is not None


def clear_login_failures(username):
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
    db.commit()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if is_locked_out(username):
            flash(f"Too many failed attempts. Try again in a few minutes.", "error")
            return render_template("login.html")

        row = get_db().execute("SELECT * FROM admin WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            clear_login_failures(username)
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("dashboard"))

        just_locked = record_login_failure(username)
        if just_locked:
            flash(f"Too many failed attempts. Locked for {LOGIN_LOCKOUT_MINUTES} minutes.", "error")
        else:
            flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# REALITY key generation
# --------------------------------------------------------------------------

def generate_reality_keypair():
    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    priv_b64 = base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode()
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    return priv_b64, pub_b64


# --------------------------------------------------------------------------
# OpenSSH / SSH-over-TLS account management (real Linux system users)
# --------------------------------------------------------------------------

def sanitize_username(remark):
    name = re.sub(r"[^a-z0-9]", "", remark.lower())[:20] or "sshuser"
    if not name[0].isalpha():
        name = "u" + name
    return f"vpn_{name}"


def generate_ssh_password(length=14):
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run_root(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10, **kwargs)


def create_ssh_account(username, password, expiry_date=None):
    result = run_root([
        "useradd", "-M", "-s", SSH_TUNNEL_SHELL,
        *(["-e", expiry_date] if expiry_date else []),
        username,
    ])
    if result.returncode != 0:
        return False, result.stderr.strip()
    result = run_root(["chpasswd"], input=f"{username}:{password}\n")
    if result.returncode != 0:
        run_root(["userdel", username])
        return False, result.stderr.strip()
    return True, "OK"


def lock_ssh_account(username):
    run_root(["usermod", "-L", username])


def unlock_ssh_account(username):
    run_root(["usermod", "-U", username])


def delete_ssh_account(username):
    run_root(["userdel", username])


def ssh_account_exists(username):
    return run_root(["id", username]).returncode == 0


def is_ssh_user_online(username):
    """Best-effort: checks active login sessions for this system user."""
    result = run_root(["who"])
    return username in result.stdout if result.returncode == 0 else False


# --------------------------------------------------------------------------
# stunnel (SSH-over-TLS) config generation
# --------------------------------------------------------------------------

def write_and_reload_stunnel():
    domain = get_setting("domain")
    port = get_setting("sshtls_port", str(DEFAULT_SSHTLS_PORT))
    cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    key_path = f"/etc/letsencrypt/live/{domain}/privkey.pem"
    combined_pem = "/etc/stunnel/stunnel.pem"

    try:
        if os.path.exists(cert_path) and os.path.exists(key_path):
            with open(cert_path) as f:
                cert_data = f.read()
            with open(key_path) as f:
                key_data = f.read()
            os.makedirs(os.path.dirname(combined_pem), exist_ok=True)
            with open(combined_pem, "w") as f:
                f.write(cert_data + key_data)
            os.chmod(combined_pem, 0o600)
    except PermissionError:
        return False, "Permission denied reading cert/writing stunnel.pem. Run as root."
    except Exception as e:
        return False, f"Failed to prepare stunnel cert: {e}"

    config = (
        "pid = /var/run/stunnel4.pid\n"
        f"cert = {combined_pem}\n\n"
        "[ssh]\n"
        f"accept = {port}\n"
        "connect = 127.0.0.1:22\n"
    )
    try:
        os.makedirs(os.path.dirname(STUNNEL_CONFIG_PATH), exist_ok=True)
        with open(STUNNEL_CONFIG_PATH, "w") as f:
            f.write(config)
    except PermissionError:
        return False, "Permission denied writing stunnel.conf. Run as root."
    except Exception as e:
        return False, f"Failed to write stunnel config: {e}"

    try:
        subprocess.run(["systemctl", "restart", STUNNEL_SERVICE_NAME],
                        check=True, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return False, "systemctl not found."
    except subprocess.CalledProcessError as e:
        return False, f"stunnel restart failed: {e.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "stunnel restart timed out"

    return True, "stunnel configured and restarted."


# --------------------------------------------------------------------------
# Traffic stats (Xray API) + history + limit/expiry enforcement
# --------------------------------------------------------------------------

def query_traffic_stats():
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
        parts = stat.get("name", "").split(">>>")
        if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
            totals[parts[1]] = totals.get(parts[1], 0) + int(stat.get("value", 0))
    return totals


def refresh_traffic_usage():
    stats = query_traffic_stats()
    if not stats:
        return
    db = get_db()
    now = datetime.utcnow().isoformat()
    for email, total_bytes in stats.items():
        row = db.execute("SELECT id, last_seen_bytes FROM clients WHERE remark = ?", (email,)).fetchone()
        if row:
            update = {"traffic_used_bytes": total_bytes}
            if total_bytes > (row["last_seen_bytes"] or 0):
                db.execute(
                    "UPDATE clients SET traffic_used_bytes=?, last_seen_bytes=?, last_active_at=? WHERE id=?",
                    (total_bytes, total_bytes, now, row["id"]),
                )
            else:
                db.execute("UPDATE clients SET traffic_used_bytes=? WHERE id=?", (total_bytes, row["id"]))
    db.commit()


def record_traffic_history():
    """Samples total traffic across all Xray-managed clients, at most every 5 minutes."""
    db = get_db()
    last = db.execute("SELECT ts FROM traffic_history ORDER BY id DESC LIMIT 1").fetchone()
    now = datetime.utcnow()
    if last:
        last_ts = datetime.fromisoformat(last["ts"])
        if now - last_ts < timedelta(minutes=5):
            return
    total = db.execute(
        "SELECT COALESCE(SUM(traffic_used_bytes),0) AS t FROM clients WHERE protocol NOT IN ('ssh','sshtls')"
    ).fetchone()["t"]
    db.execute("INSERT INTO traffic_history (ts, total_bytes) VALUES (?, ?)", (now.isoformat(), total))
    # keep only the most recent 100 samples
    db.execute(
        "DELETE FROM traffic_history WHERE id NOT IN "
        "(SELECT id FROM traffic_history ORDER BY id DESC LIMIT 100)"
    )
    db.commit()


def enforce_limits():
    db = get_db()
    today = date.today().isoformat()
    clients = db.execute("SELECT * FROM clients WHERE enabled = 1").fetchall()
    changed = False
    for c in clients:
        expired = c["expiry_date"] and c["expiry_date"] < today
        over_limit = (
            c["protocol"] not in SYSTEM_SSH_PROTOCOLS
            and c["traffic_limit_gb"] > 0
            and c["traffic_used_bytes"] >= c["traffic_limit_gb"] * 1_000_000_000
        )
        if expired or over_limit:
            db.execute("UPDATE clients SET enabled = 0 WHERE id = ?", (c["id"],))
            if c["protocol"] in SYSTEM_SSH_PROTOCOLS and c["ssh_username"]:
                lock_ssh_account(c["ssh_username"])
            changed = True
    if changed:
        db.commit()


def human_bytes(n):
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def client_activity(c):
    """Returns a short human status string for the dashboard."""
    if c["protocol"] in SYSTEM_SSH_PROTOCOLS:
        if not c["ssh_username"]:
            return "unknown"
        return "online now" if is_ssh_user_online(c["ssh_username"]) else "offline"
    if c["last_active_at"]:
        last = datetime.fromisoformat(c["last_active_at"])
        delta = datetime.utcnow() - last
        if delta < timedelta(minutes=3):
            return "active now"
        if delta < timedelta(hours=1):
            return f"active {int(delta.total_seconds() // 60)}m ago"
        if delta < timedelta(days=1):
            return f"active {int(delta.total_seconds() // 3600)}h ago"
        return f"active {delta.days}d ago"
    return "no activity yet"


# --------------------------------------------------------------------------
# Xray config generation + service control
# --------------------------------------------------------------------------

def build_xray_config():
    db = get_db()
    clients = db.execute(
        "SELECT * FROM clients WHERE enabled = 1 AND protocol IN "
        "('vless','vmess','trojan','reality','ss')"
    ).fetchall()

    by_protocol = {"vless": [], "vmess": [], "trojan": [], "reality": [], "ss": []}
    for c in clients:
        by_protocol.setdefault(c["protocol"], []).append(c)

    inbounds = [{
        "listen": "127.0.0.1", "port": API_PORT, "protocol": "dokodemo-door",
        "settings": {"address": "127.0.0.1"}, "tag": "api",
    }]

    inbounds.append({
        "listen": "127.0.0.1", "port": PROTOCOL_PORTS["vless"], "protocol": "vless",
        "settings": {
            "clients": [{"id": c["client_uuid"], "email": c["remark"]} for c in by_protocol["vless"]],
            "decryption": "none",
        },
        "streamSettings": {"network": "ws", "wsSettings": {"path": get_setting("vless_ws_path", "/vless-ws")}},
    })

    inbounds.append({
        "listen": "127.0.0.1", "port": PROTOCOL_PORTS["vmess"], "protocol": "vmess",
        "settings": {
            "clients": [{"id": c["client_uuid"], "email": c["remark"], "alterId": 0} for c in by_protocol["vmess"]],
        },
        "streamSettings": {"network": "ws", "wsSettings": {"path": get_setting("vmess_ws_path", "/vmess-ws")}},
    })

    inbounds.append({
        "listen": "127.0.0.1", "port": PROTOCOL_PORTS["trojan"], "protocol": "trojan",
        "settings": {
            "clients": [{"password": c["client_uuid"], "email": c["remark"]} for c in by_protocol["trojan"]],
        },
        "streamSettings": {"network": "ws", "wsSettings": {"path": get_setting("trojan_ws_path", "/trojan-ws")}},
    })

    # REALITY — listens directly on its own public port, no nginx/cert needed
    reality_port = int(get_setting("reality_port", DEFAULT_REALITY_PORT))
    inbounds.append({
        "listen": "0.0.0.0", "port": reality_port, "protocol": "vless",
        "settings": {
            "clients": [
                {"id": c["client_uuid"], "email": c["remark"], "flow": "xtls-rprx-vision"}
                for c in by_protocol["reality"]
            ],
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": get_setting("reality_dest", "www.microsoft.com:443"),
                "xver": 0,
                "serverNames": [get_setting("reality_server_name", "www.microsoft.com")],
                "privateKey": get_setting("reality_private_key", ""),
                "shortIds": [get_setting("reality_short_id", "")],
            },
        },
    })

    # Shadowsocks 2022, multi-user — also listens directly on its own port
    ss_port = int(get_setting("ss_port", DEFAULT_SS_PORT))
    inbounds.append({
        "listen": "0.0.0.0", "port": ss_port, "protocol": "shadowsocks",
        "settings": {
            "method": get_setting("ss_method", "2022-blake3-aes-256-gcm"),
            "password": get_setting("ss_server_psk", ""),
            "clients": [
                {"password": c["ss_password"], "email": c["remark"]} for c in by_protocol["ss"]
            ],
        },
    })

    return {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {"statsInboundUplink": True, "statsInboundDownlink": True},
        },
        "inbounds": inbounds,
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "freedom", "tag": "api"},
        ],
        "routing": {"rules": [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]},
    }


def write_and_reload_xray():
    config = build_xray_config()
    try:
        os.makedirs(os.path.dirname(XRAY_CONFIG_PATH), exist_ok=True)
        with open(XRAY_CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except PermissionError:
        return False, f"Could not write {XRAY_CONFIG_PATH} (permission denied). Run the panel as root."
    except Exception as e:
        return False, f"Failed to write config: {e}"

    try:
        subprocess.run(["systemctl", "restart", XRAY_SERVICE_NAME],
                        check=True, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return False, "systemctl not found — is this running on the VPS itself?"
    except subprocess.CalledProcessError as e:
        return False, f"xray restart failed: {e.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "xray restart timed out"

    return True, "Config written and xray restarted successfully."


def xray_service_status():
    try:
        result = subprocess.run(["systemctl", "is-active", XRAY_SERVICE_NAME],
                                 capture_output=True, text=True, timeout=5)
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
        obj = {"v": "2", "ps": remark, "add": domain, "port": str(port), "id": cid,
               "aid": "0", "net": "ws", "type": "none", "host": domain, "path": path,
               "tls": "tls", "sni": domain}
        return f"vmess://{base64.b64encode(json.dumps(obj).encode()).decode()}"
    if protocol == "trojan":
        path = get_setting("trojan_ws_path", "/trojan-ws")
        return (f"trojan://{cid}@{domain}:{port}"
                f"?type=ws&security=tls&path={path}&host={domain}&sni={domain}#{remark}")
    if protocol == "reality":
        rport = get_setting("reality_port", str(DEFAULT_REALITY_PORT))
        pbk = get_setting("reality_public_key", "")
        sni = get_setting("reality_server_name", "")
        sid = get_setting("reality_short_id", "")
        return (f"vless://{cid}@{domain}:{rport}"
                f"?security=reality&encryption=none&pbk={pbk}&fp=chrome&sni={sni}"
                f"&sid={sid}&type=tcp&flow=xtls-rprx-vision#{remark}")
    if protocol == "ss":
        sport = get_setting("ss_port", str(DEFAULT_SS_PORT))
        method = get_setting("ss_method", "2022-blake3-aes-256-gcm")
        server_psk = get_setting("ss_server_psk", "")
        userinfo = f"{method}:{server_psk}:{client['ss_password']}"
        b64 = base64.urlsafe_b64encode(userinfo.encode()).rstrip(b"=").decode()
        return f"ss://{b64}@{domain}:{sport}#{remark}"
    if protocol == "ssh":
        return f"ssh -D 1080 -N {client['ssh_username']}@{domain} -p {SSH_PORT}"
    if protocol == "sshtls":
        sport = get_setting("sshtls_port", str(DEFAULT_SSHTLS_PORT))
        return (f"SSH-over-TLS — host: {domain}  port: {sport}  "
                f"user: {client['ssh_username']}  (use an SSL+SSH capable client, e.g. HTTP Injector)")
    return ""


def qr_data_uri(text):
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"


def get_system_stats():
    try:
        cpu = psutil.cpu_percent(interval=0.2)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime_seconds = int(datetime.now().timestamp() - psutil.boot_time())
        days, rem = divmod(uptime_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        return {
            "cpu_pct": round(cpu, 1), "mem_pct": round(mem.percent, 1),
            "mem_used": human_bytes(mem.used), "mem_total": human_bytes(mem.total),
            "disk_pct": round(disk.percent, 1),
            "disk_used": human_bytes(disk.used), "disk_total": human_bytes(disk.total),
            "uptime": f"{days}d {hours}h {minutes}m",
        }
    except Exception:
        return None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    refresh_traffic_usage()
    record_traffic_history()
    enforce_limits()
    db = get_db()

    q = request.args.get("q", "").strip().lower()
    proto_filter = request.args.get("protocol", "")
    status_filter = request.args.get("status", "")

    clients = db.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()

    enriched = []
    for c in clients:
        if q and q not in c["remark"].lower():
            continue
        if proto_filter and c["protocol"] != proto_filter:
            continue
        if status_filter == "enabled" and not c["enabled"]:
            continue
        if status_filter == "disabled" and c["enabled"]:
            continue

        d = dict(c)
        d["link"] = make_client_link(c)
        d["used_human"] = human_bytes(c["traffic_used_bytes"])
        d["limit_human"] = f"{c['traffic_limit_gb']} GB" if c["traffic_limit_gb"] else "Unlimited"
        d["pct_used"] = (
            min(100, round(c["traffic_used_bytes"] / (c["traffic_limit_gb"] * 1_000_000_000) * 100))
            if c["traffic_limit_gb"] else 0
        )
        d["activity"] = client_activity(c)
        enriched.append(d)

    all_clients = db.execute("SELECT protocol, enabled FROM clients").fetchall()
    stats = {
        "total": len(all_clients),
        "active": sum(1 for c in all_clients if c["enabled"]),
        "by_protocol": {p: sum(1 for c in all_clients if c["protocol"] == p) for p in PROTOCOLS},
    }

    history_rows = db.execute("SELECT ts, total_bytes FROM traffic_history ORDER BY id ASC").fetchall()
    traffic_chart = {
        "labels": [r["ts"][11:16] for r in history_rows],
        "values": [round(r["total_bytes"] / 1_000_000_000, 3) for r in history_rows],
    }

    return render_template(
        "dashboard.html",
        clients=enriched, xray_status=xray_service_status(), domain=get_setting("domain"),
        stats=stats, system=get_system_stats(), traffic_chart=traffic_chart,
        q=q, proto_filter=proto_filter, status_filter=status_filter,
    )


@app.route("/clients/add", methods=["GET", "POST"])
@login_required
def add_client():
    if request.method == "POST":
        remark = request.form.get("remark", "").strip() or "client"
        protocol = request.form.get("protocol", "vless")
        if protocol not in PROTOCOLS:
            protocol = "vless"
        expiry = request.form.get("expiry_date", "").strip() or None
        try:
            traffic_limit = float(request.form.get("traffic_limit_gb", "0") or 0)
        except ValueError:
            traffic_limit = 0

        client_uuid = str(uuid.uuid4())
        sub_token = secrets.token_urlsafe(16)
        ssh_username = ssh_password = ss_password = None

        if protocol in SYSTEM_SSH_PROTOCOLS:
            db = get_db()
            base_username = sanitize_username(remark)
            ssh_username = base_username
            suffix = 0
            while ssh_account_exists(ssh_username) or \
                  db.execute("SELECT 1 FROM clients WHERE ssh_username = ?", (ssh_username,)).fetchone():
                suffix += 1
                ssh_username = f"{base_username}{suffix}"
            ssh_password = generate_ssh_password()
            ok, err = create_ssh_account(ssh_username, ssh_password, expiry)
            if not ok:
                flash(f"Failed to create SSH account: {err}", "error")
                return redirect(url_for("add_client"))
            if protocol == "sshtls":
                ok, msg = write_and_reload_stunnel()
                if not ok:
                    flash(f"SSH account created, but stunnel setup failed: {msg}", "error")

        if protocol == "ss":
            ss_password = base64.b64encode(os.urandom(32)).decode()

        db = get_db()
        db.execute(
            "INSERT INTO clients "
            "(remark, protocol, client_uuid, sub_token, enabled, traffic_limit_gb, "
            " traffic_used_bytes, created_at, expiry_date, ssh_username, ssh_password, ss_password) "
            "VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?, ?, ?, ?)",
            (remark, protocol, client_uuid, sub_token, traffic_limit,
             datetime.utcnow().isoformat(), expiry, ssh_username, ssh_password, ss_password),
        )
        db.commit()

        if protocol in SYSTEM_SSH_PROTOCOLS:
            flash(f"{'SSH-over-TLS' if protocol == 'sshtls' else 'SSH'} account '{ssh_username}' created.", "success")
        else:
            ok, msg = write_and_reload_xray()
            flash(msg, "success" if ok else "error")
        return redirect(url_for("dashboard"))

    reality_ready = bool(get_setting("reality_private_key"))
    return render_template("add_client.html", reality_ready=reality_ready)


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def edit_client(client_id):
    db = get_db()
    row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not row:
        return "Not found", 404

    if request.method == "POST":
        remark = request.form.get("remark", "").strip() or row["remark"]
        expiry = request.form.get("expiry_date", "").strip() or None
        try:
            traffic_limit = float(request.form.get("traffic_limit_gb", "0") or 0)
        except ValueError:
            traffic_limit = row["traffic_limit_gb"]

        db.execute(
            "UPDATE clients SET remark=?, traffic_limit_gb=?, expiry_date=? WHERE id=?",
            (remark, traffic_limit, expiry, client_id),
        )
        db.commit()

        if row["protocol"] in SYSTEM_SSH_PROTOCOLS and row["ssh_username"]:
            run_root(["chage", "-E", expiry or "-1", row["ssh_username"]])
            flash("Client updated.", "success")
        else:
            ok, msg = write_and_reload_xray()
            flash(msg, "success" if ok else "error")
        return redirect(url_for("dashboard"))

    return render_template("edit_client.html", c=row)


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
    row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row:
        new_state = 0 if row["enabled"] else 1
        db.execute("UPDATE clients SET enabled = ? WHERE id = ?", (new_state, client_id))
        db.commit()
        if row["protocol"] in SYSTEM_SSH_PROTOCOLS and row["ssh_username"]:
            (unlock_ssh_account if new_state else lock_ssh_account)(row["ssh_username"])
            flash(f"Account {'unlocked' if new_state else 'locked'}.", "success")
        else:
            ok, msg = write_and_reload_xray()
            flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard"))


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
@login_required
def delete_client(client_id):
    db = get_db()
    row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    db.commit()
    if row and row["protocol"] in SYSTEM_SSH_PROTOCOLS and row["ssh_username"]:
        delete_ssh_account(row["ssh_username"])
        flash("SSH account deleted.", "success")
    else:
        ok, msg = write_and_reload_xray()
        flash(msg, "success" if ok else "error")
    return redirect(url_for("dashboard"))


@app.route("/sub/<sub_token>")
def subscription(sub_token):
    db = get_db()
    row = db.execute("SELECT * FROM clients WHERE sub_token = ?", (sub_token,)).fetchone()
    if not row or not row["enabled"]:
        return Response("", status=404)
    link = make_client_link(row)
    return Response(base64.b64encode(link.encode()).decode(), mimetype="text/plain")


@app.route("/api/system")
@login_required
def api_system():
    return jsonify(get_system_stats() or {})


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        for key in ("domain", "public_port", "vless_ws_path", "vmess_ws_path", "trojan_ws_path",
                    "reality_port", "reality_dest", "reality_server_name",
                    "ss_port", "ss_method", "sshtls_port"):
            value = request.form.get(key, "").strip()
            if value:
                set_setting(key, value)

        new_password = request.form.get("new_password", "").strip()
        if new_password:
            db = get_db()
            db.execute("UPDATE admin SET password_hash = ? WHERE username = ?",
                       (generate_password_hash(new_password), session["username"]))
            db.commit()
            flash("Password updated.", "success")

        ok, msg = write_and_reload_xray()
        flash(msg, "success" if ok else "error")
        return redirect(url_for("settings"))

    keys = ("domain", "public_port", "vless_ws_path", "vmess_ws_path", "trojan_ws_path",
            "reality_port", "reality_dest", "reality_server_name", "reality_public_key",
            "reality_short_id", "ss_port", "ss_method", "ss_server_psk", "sshtls_port")
    values = {k: get_setting(k) for k in keys}
    return render_template("settings.html", **values)


@app.route("/settings/regenerate-reality-keys", methods=["POST"])
@login_required
def regenerate_reality_keys():
    priv, pub = generate_reality_keypair()
    set_setting("reality_private_key", priv)
    set_setting("reality_public_key", pub)
    ok, msg = write_and_reload_xray()
    flash("REALITY keys regenerated. " + msg, "success" if ok else "error")
    return redirect(url_for("settings"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=2053, debug=False)
