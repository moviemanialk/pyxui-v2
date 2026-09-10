# Configuration Reference

Everything you can change, and where.

## Settings page (in the panel UI)

These live in the `settings` table in `panel.db` and are re-applied to
Xray's config every time you save or add/remove a client.

| Setting | Default | Meaning |
|---|---|---|
| Domain | `vpn.yourdomain.com` | The hostname clients connect to. Must match your DNS A record and TLS cert. |
| Public port | `443` | The port clients connect to from the outside (nginx listens here). |
| VLESS WebSocket path | `/vless-ws` | Must match the `location` block in your nginx config. |
| VMess WebSocket path | `/vmess-ws` | Same idea, for VMess clients. |
| Trojan WebSocket path | `/trojan-ws` | Same idea, for Trojan clients. |

Change a WebSocket path here **and** in `nginx.conf` (or the file you
copied it to) — they have to match, or nginx will 404 instead of
proxying to Xray.

## Constants in `app.py`

These aren't exposed in the UI because changing them requires updating
nginx too — edit `app.py` directly if you need to change them, then
re-run through the corresponding nginx location blocks.

| Constant | Default | Meaning |
|---|---|---|
| `PROTOCOL_PORTS` | `vless=10001, vmess=10002, trojan=10003` | Local ports each Xray inbound listens on (loopback only). |
| `API_PORT` | `62789` | Xray's stats API port (loopback only, not exposed publicly). |
| `XRAY_CONFIG_PATH` | `/usr/local/etc/xray/config.json` | Where the generated config is written. Adjust if your Xray install uses a different path. |
| `XRAY_SERVICE_NAME` | `xray` | systemd service name that gets restarted after changes. |

## Environment variables

| Variable | Purpose |
|---|---|
| `PYXUI_SECRET` | Flask session secret key. If unset, a random one is generated on each restart (which logs everyone out on restart) — set this to a fixed random string in production, e.g. in the systemd unit's `Environment=` line. |

Example, in `/etc/systemd/system/pyxui.service`:

```ini
[Service]
Environment=PYXUI_SECRET=your-long-random-string-here
```

Generate one with: `python3 -c "import secrets; print(secrets.token_hex(32))"`

## Per-client fields

Set when you add a client, editable only by deleting and re-adding
(there's no edit form in this version — see docs/USAGE.md for the
delete/re-add workflow, or extend `app.py`'s `/clients/add` route into an
edit route if you want that).

| Field | Meaning |
|---|---|
| Remark | Label shown in the dashboard and used as the Xray "email" tag for stats/traffic tracking. Keep it unique. |
| Protocol | `vless`, `vmess`, or `trojan`. |
| Traffic limit (GB) | `0` = unlimited. Otherwise the client is auto-disabled once usage reaches this. |
| Expiry date | Optional. Client is auto-disabled the day after this date. |

## Hardening the panel itself

The panel's login has no rate limiting or 2FA built in. Recommended for
any public-facing deployment:

1. **Don't expose port 2053 directly.** Either tunnel over SSH (see
   INSTALL.md step 7), or put it behind the `/panel/` nginx location and
   add HTTP basic auth:
   ```nginx
   location /panel/ {
       auth_basic "Restricted";
       auth_basic_user_file /etc/nginx/.htpasswd;
       proxy_pass http://127.0.0.1:2053/;
       ...
   }
   ```
   Create the password file with `htpasswd -c /etc/nginx/.htpasswd youruser`.
2. **Use a strong admin password** — the default `admin123` must be
   changed before you expose anything.
3. **Keep the VPS firewall tight** — only 22, 80, 443 open externally.
