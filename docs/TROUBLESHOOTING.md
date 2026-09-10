# Troubleshooting

## Panel shows "Could not write config (permission denied)"

The panel process isn't running as root, so it can't write
`/usr/local/etc/xray/config.json` or restart the `xray` service. Confirm
the systemd unit runs as root (`User=root` in
`/etc/systemd/system/pyxui.service`) and that you didn't start it
manually as a non-root user.

## Flash message says "xray restart failed: ..."

Check the actual Xray error:
```bash
journalctl -u xray -n 50 --no-pager
```
Common causes:
- Invalid JSON somehow got into the config (shouldn't happen normally —
  file a note in your own repo if you see this, it'd indicate a bug in
  `build_xray_config()`).
- A port in `PROTOCOL_PORTS` or `API_PORT` is already in use by
  something else on the VPS. Check with `ss -tlnp`.

## Client can't connect at all

1. **Confirm DNS resolves correctly:**
   ```bash
   dig +short vpn.yourdomain.com
   ```
   Must match your VPS's IP.
2. **Confirm nginx is listening on 443 and the cert is valid:**
   ```bash
   curl -vI https://vpn.yourdomain.com/vless-ws
   ```
   A TLS handshake should succeed even if the HTTP response itself is an
   error (Xray expects a WebSocket upgrade, not a plain GET).
3. **Confirm Xray is actually running:**
   ```bash
   systemctl status xray
   ```
4. **Confirm the client's config matches exactly:**
   - Same domain
   - Same port (443)
   - Same WebSocket path as set in Settings *and* in nginx
   - TLS/SNI both set to the domain (not left blank, not set to the IP)
   - Correct UUID/password for that specific client

## Traffic always shows 0 B even after using the connection

The stats feature depends on Xray's API being reachable and the `xray`
binary being on the panel's `$PATH` (it calls `xray api statsquery`
under the hood). Check:

```bash
which xray
xray api statsquery --server=127.0.0.1:62789
```

If that command errors out, the config's `api`/`stats`/`policy` blocks
aren't taking effect — confirm your installed Xray-core version supports
the stats API (recent versions do) and that the config the panel wrote
actually includes those sections:
```bash
cat /usr/local/etc/xray/config.json | python3 -m json.tool | grep -A3 '"stats"'
```
If the binary path differs on your system, update `XRAY_BINARY` in
`app.py`.

## Panel login page won't load

```bash
systemctl status pyxui
journalctl -u pyxui -n 50 --no-pager
```
Usually either the Python venv is missing a dependency (re-run
`./venv/bin/pip install -r requirements.txt`) or port 2053 is already
taken by something else.

## I forgot the admin password

SSH into the VPS and reset it directly in the database:
```bash
cd /opt/pyxui
./venv/bin/python3 -c "
import sqlite3
from werkzeug.security import generate_password_hash
db = sqlite3.connect('panel.db')
db.execute('UPDATE admin SET password_hash = ? WHERE username = ?',
           (generate_password_hash('newpassword123'), 'admin'))
db.commit()
print('Password reset.')
"
```

## Clients keep getting auto-disabled unexpectedly

Check their expiry date and traffic limit on the dashboard — the
enforcement logic (`enforce_limits()` in `app.py`) disables anyone whose
`expiry_date` has passed or whose `traffic_used_bytes` has reached their
`traffic_limit_gb`. If a client shouldn't be limited, re-add them with
`0` for traffic limit and no expiry date (there's no edit form yet — see
docs/USAGE.md).
