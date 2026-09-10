#!/usr/bin/env python3
"""
PyXUI v3 - self-hosted X-UI style panel for Xray-core + native OpenSSH
tunnel accounts.

New in v3:
  - SSH protocol: creates a real, shell-restricted Linux system account
    for classic `ssh -D`/`ssh -L` tunneling (no Xray involved)
  - System stats widget (CPU / RAM / disk / uptime) via psutil
  - Search + protocol/status filtering on the dashboard
  - Copy-to-clipboard, toast notifications, protocol icons (frontend)

Run on the VPS as root:  sudo python3 app.py
See docs/INSTALL.md and docs/SSH.md for full setup.
"""

import base64
import io
import json
import os
import re
import secrets
import sqlite3
import string
import subprocess
import uuid
from datetime import datetime, date
from functools import wraps

import psutil
import qrcode
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

PROTOCOL_PORTS = {"vless": 10001, "vmess": 10002, "trojan": 10003}
API_PORT = 62789

# Shell assigned to SSH-tunnel-only accounts: blocks interactive login,
# but sshd still permits -L/-D/-R port forwarding for these users as
# long as AllowTcpForwarding is enabled in sshd_config (see docs/SSH.md).
SSH_TUNNEL_SHELL = "/usr/sbin/nologin"
SSH_PORT = "22"

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
            expiry_date TEXT,
            ssh_username TEXT,
            ssh_password TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    db.commit()

    existing_cols = {row["name"] for row in db.execute("PRAGMA table_info(clients)")}
    if "ssh_username" not in existing_cols:
        db.execute("ALTER TABLE clients ADD COLUMN ssh_username TEXT")
    if "ssh_password" not in existing_cols:
        db.execute("ALTER TABLE clients ADD COLUMN ssh_password TEXT")
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
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
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
        row = get_db().execute("SELECT * FROM admin WHERE username = ?", (username,)).fetchone()
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
# OpenSSH tunnel account management (real Linux system users)
# --------------------------------------------------------------------------

def sanitize_username(remark):
    """Turns a remark into a valid, unique-ish Linux username."""
    name = re.sub(r"[^a-z0-9]", "", remark.lower())[:20] or "sshuser"
    if not name[0].isalpha():
        name = "u" + name
    return f"vpn_{name}"


def generate_ssh_password(length=14):
    # Easy-to-read charset (no ambiguous chars) since it's often typed on mobile.
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def run_root(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10, **kwargs)


def create_ssh_account(username, password, expiry_date=None):
    """Creates a real shell-restricted Linux user for SSH tunneling."""
    result = run_root([
        "useradd", "-M",
        "-s", SSH_TUNNEL_SHELL,
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


def set_ssh_expiry(username, expiry_date):
    run_root(["chage", "-E", expiry_date or "-1", username])


def lock_ssh_account(username):
    run_root(["usermod", "-L", username])


def unlock_ssh_account(username):
    run_root(["usermod", "-U", username])


def delete_ssh_account(username):
    run_root(["userdel", username])


def ssh_account_exists(username):
    return run_root(["id", username]).returncode == 0


# --------------------------------------------------------------------------
# Traffic stats (Xray API) + limit/expiry enforcement
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
    for email, total_bytes in stats.items():
        db.execute("UPDATE clients SET traffic_used_bytes = ? WHERE remark = ?", (total_bytes, email))
    db.commit()


def enforce_limits():
    db = get_db()
    today = date.today().isoformat()
    clients = db.execute("SELECT * FROM clients WHERE enabled = 1").fetchall()
    changed = False
    for c in clients:
        expired = c["expiry_date"] and c["expiry_date"] < today
        over_limit = (
            c["protocol"] != "ssh"
            and c["traffic_limit_gb"] > 0
            and c["traffic_used_bytes"] >= c["traffic_limit_gb"] * 1_000_000_000
        )
        if expired or over_limit:
            db.execute("UPDATE clients SET enabled = 0 WHERE id = ?", (c["id"],))
            if c["protocol"] == "ssh" and c["ssh_username"]:
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


# --------------------------------------------------------------------------
# Xray config generation + service control
# --------------------------------------------------------------------------

def build_xray_config():
    db = get_db()
    clients = db.execute(
        "SELECT * FROM clients WHERE enabled = 1 AND protocol != 'ssh'"
    ).fetchall()

    by_protocol = {"vless": [], "vmess": [], "trojan": []}
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
    if protocol == "ssh":
        return f"ssh -D 1080 -N {client['ssh_username']}@{domain} -p {SSH_PORT}"
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
            "cpu_pct": round(cpu, 1),
            "mem_pct": round(mem.percent, 1),
            "mem_used": human_bytes(mem.used),
            "mem_total": human_bytes(mem.total),
            "disk_pct": round(disk.percent, 1),
            "disk_used": human_bytes(disk.used),
            "disk_total": human_bytes(disk.total),
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
        enriched.append(d)

    all_clients = db.execute("SELECT protocol, enabled FROM clients").fetchall()
    stats = {
        "total": len(all_clients),
        "active": sum(1 for c in all_clients if c["enabled"]),
        "by_protocol": {
            p: sum(1 for c in all_clients if c["protocol"] == p)
            for p in ("vless", "vmess", "trojan", "ssh")
        },
    }

    return render_template(
        "dashboard.html",
        clients=enriched,
        xray_status=xray_service_status(),
        domain=get_setting("domain"),
        stats=stats,
        system=get_system_stats(),
        q=q, proto_filter=proto_filter, status_filter=status_filter,
    )


@app.route("/clients/add", methods=["GET", "POST"])
@login_required
def add_client():
    if request.method == "POST":
        remark = request.form.get("remark", "").strip() or "client"
        protocol = request.form.get("protocol", "vless")
        if protocol not in ("vless", "vmess", "trojan", "ssh"):
            protocol = "vless"
        expiry = request.form.get("expiry_date", "").strip() or None
        try:
            traffic_limit = float(request.form.get("traffic_limit_gb", "0") or 0)
        except ValueError:
            traffic_limit = 0

        client_uuid = str(uuid.uuid4())
        sub_token = secrets.token_urlsafe(16)
        ssh_username = ssh_password = None

        if protocol == "ssh":
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

        db = get_db()
        db.execute(
            "INSERT INTO clients "
            "(remark, protocol, client_uuid, sub_token, enabled, traffic_limit_gb, "
            " traffic_used_bytes, created_at, expiry_date, ssh_username, ssh_password) "
            "VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?, ?, ?)",
            (remark, protocol, client_uuid, sub_token, traffic_limit,
             datetime.utcnow().isoformat(), expiry, ssh_username, ssh_password),
        )
        db.commit()

        if protocol == "ssh":
            flash(f"SSH account '{ssh_username}' created.", "success")
        else:
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
    row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row:
        new_state = 0 if row["enabled"] else 1
        db.execute("UPDATE clients SET enabled = ? WHERE id = ?", (new_state, client_id))
        db.commit()
        if row["protocol"] == "ssh" and row["ssh_username"]:
            (unlock_ssh_account if new_state else lock_ssh_account)(row["ssh_username"])
            flash(f"SSH account {'unlocked' if new_state else 'locked'}.", "success")
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
    if row and row["protocol"] == "ssh" and row["ssh_username"]:
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
        for key in ("domain", "public_port", "vless_ws_path", "vmess_ws_path", "trojan_ws_path"):
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

    keys = ("domain", "public_port", "vless_ws_path", "vmess_ws_path", "trojan_ws_path")
    values = {k: get_setting(k) for k in keys}
    return render_template("settings.html", **values)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=2053, debug=False)
