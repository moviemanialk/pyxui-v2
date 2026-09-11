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
| REALITY port | `8443` | Direct public port — no nginx involved. See docs/REALITY.md. |
| REALITY dest | `www.microsoft.com:443` | The real site whose TLS handshake gets mimicked. |
| REALITY server name | `www.microsoft.com` | SNI presented to clients — usually matches dest's hostname. |
| Shadowsocks port | `8388` | Direct public port. See docs/SHADOWSOCKS.md. |
| Shadowsocks method | `2022-blake3-aes-256-gcm` | Only change if you know your clients support a different one. |
| SSH-over-TLS port | `444` | stunnel listens here, forwards to local sshd:22. See docs/SSH.md. |

Change a WebSocket path here **and** in `nginx.conf` (or the file you
copied it to) — they have to match, or nginx will 404 instead of
proxying to Xray. REALITY/Shadowsocks/SSH-over-TLS ports need to match
your VPS firewall rules, not nginx (they don't go through nginx at all).

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
Editable any time via the pencil icon on the dashboard — this updates
the remark, traffic limit, and expiry date without recreating the
client (protocol and credentials stay fixed; delete and re-add if you
need to change those).

| Field | Meaning |
|---|---|
| Remark | Label shown in the dashboard and used as the Xray "email" tag for stats/traffic tracking. Keep it unique. |
| Protocol | `vless`, `vmess`, `trojan`, `reality`, `ss`, `ssh`, or `sshtls`. Fixed after creation. |
| Traffic limit (GB) | `0` = unlimited. Otherwise the client is auto-disabled once usage reaches this. Not applicable to `ssh`/`sshtls`. |
| Expiry date | Optional. Client is auto-disabled the day after this date (and the underlying Linux account is separately expired by the OS itself for SSH protocols). |

## Login lockout

Built in, not configurable via the UI: 5 failed login attempts locks
that username for 15 minutes (`LOGIN_MAX_ATTEMPTS` /
`LOGIN_LOCKOUT_MINUTES` constants in `app.py` if you want to change the
thresholds). This resets on any successful login.

## Hardening the panel itself

Recommended for any public-facing deployment:

1. **Never expose port 2053 directly.** The installer now binds
   gunicorn to `127.0.0.1:2053` by default — not reachable from the
   internet at all. Reach it via a dedicated panel subdomain behind
   nginx (see INSTALL.md step 6) or an SSH tunnel.
2. **Add HTTP basic auth** as a second layer in front of the panel
   login, on whichever nginx server block proxies to it:
   ```nginx
   location / {
       auth_basic "Restricted";
       auth_basic_user_file /etc/nginx/.htpasswd;
       proxy_pass http://127.0.0.1:2053/;
       ...
   }
   ```
   Create the password file with `htpasswd -c /etc/nginx/.htpasswd youruser`.
3. **Use a strong admin password** — the default `admin123` must be
   changed before you expose anything.
4. **Keep the VPS firewall tight** — only open what you actually use:
   22, 80, 443 always; 8443/8388/444 only if you're using
   REALITY/Shadowsocks/SSH-over-TLS respectively.
