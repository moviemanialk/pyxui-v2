# PyXUI v4

A self-hosted, X-UI style web panel for managing an Xray-core server —
**VLESS, VMess, Trojan, VLESS+REALITY, Shadowsocks (2022 multi-user),
plain OpenSSH tunnels, and SSH-over-TLS** — with per-client traffic
limits, expiry dates, QR codes, subscription links, live server stats,
traffic history charts, activity indicators, and client editing.

Built as a lighter, easier-to-modify Python/Flask alternative to the
Go-based X-UI/3X-UI panels, for running your **own** private tunnel to
your **own** devices.

## What's new in v4

- **REALITY** — clones a real site's TLS handshake so your server is
  indistinguishable from a normal visit to that site, no certificate of
  your own needed for this protocol specifically ([docs/REALITY.md](docs/REALITY.md))
- **Shadowsocks** (2022-blake3-aes-256-gcm, multi-user) ([docs/SHADOWSOCKS.md](docs/SHADOWSOCKS.md))
- **SSH-over-TLS** — the SSH tunnel account type wrapped in stunnel, for
  clients like HTTP Injector expecting "SSL + SSH" ([docs/SSH.md](docs/SSH.md))
- **Traffic history chart** — samples total usage over time, rendered on the dashboard
- **Per-client activity indicator** — "active now" / "active 5m ago" for Xray protocols, real online/offline for SSH accounts
- **Client editing** — change remark, traffic limit, or expiry without deleting and recreating
- **Login lockout** — 5 failed attempts locks the account for 15 minutes
- **Runs under gunicorn**, bound to localhost only — reached via a dedicated panel subdomain or SSH tunnel, never exposed directly

## What's inside

```
pyxui-v4/
├── app.py                  # Flask backend — all panel logic
├── requirements.txt
├── install.sh               # one-shot VPS installer
├── nginx.conf.example       # TLS + WebSocket routing for VLESS/VMess/Trojan
├── templates/                # dashboard, add/edit client, settings, QR pages
└── docs/
    ├── INSTALL.md            # step-by-step VPS setup
    ├── CONFIG.md              # every setting explained
    ├── USAGE.md               # day-to-day panel usage
    ├── SSH.md                  # OpenSSH + SSH-over-TLS accounts
    ├── REALITY.md              # how the cloned-handshake protocol works
    ├── SHADOWSOCKS.md          # multi-user Shadowsocks specifics
    ├── ARCHITECTURE.md        # how the pieces fit together
    └── TROUBLESHOOTING.md
```

## Quick start

1. **[docs/INSTALL.md](docs/INSTALL.md)** — full VPS setup, start to finish.
2. **[docs/USAGE.md](docs/USAGE.md)** — day-to-day client management.
3. **[docs/REALITY.md](docs/REALITY.md)** / **[docs/SHADOWSOCKS.md](docs/SHADOWSOCKS.md)** / **[docs/SSH.md](docs/SSH.md)** — protocol specifics.
4. **[docs/CONFIG.md](docs/CONFIG.md)** — change defaults, harden the panel.
5. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how traffic actually flows.
6. Stuck? **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**.

## Feature summary

| Feature | Status |
|---|---|
| VLESS / VMess / Trojan (WS + TLS, behind nginx) | ✅ |
| VLESS + REALITY (cloned TLS handshake, direct port) | ✅ |
| Shadowsocks 2022 (multi-user, direct port) | ✅ |
| Plain OpenSSH tunnel accounts | ✅ |
| SSH-over-TLS (stunnel-wrapped, for SSL+SSH clients) | ✅ |
| Add / enable / disable / delete / **edit** clients | ✅ |
| Per-client traffic limit (GB) — Xray protocols only | ✅ |
| Live traffic usage + history chart | ✅ |
| Per-client activity/online indicator | ✅ |
| Auto-disable / auto-lock on expiry or over-limit | ✅ |
| QR code + subscription link (Xray protocols) | ✅ |
| Live CPU / RAM / disk / uptime widget | ✅ |
| Search + protocol/status filters | ✅ |
| Click-to-copy links, usernames, passwords | ✅ |
| Login lockout after repeated failures | ✅ |
| Runs under gunicorn, localhost-only by default | ✅ |
| Multi-user admin accounts | ❌ (single admin only) |
| SNI-multiplexing multiple protocols on one port 443 | ❌ (REALITY/SS/sshtls each need their own port) |
| Reverse-proxy reseller/multi-node support | ❌ (single server only) |

## Security note

This manages **your own** server for **your own** devices. It does not
include reseller/multi-tenant billing features. SSH account passwords
are stored in plaintext in the panel's database (necessary so they can
be shown to you) — see [docs/SSH.md](docs/SSH.md) for the trade-off.
The panel itself runs behind gunicorn on localhost only — see
[docs/INSTALL.md](docs/INSTALL.md) for exposing it safely via a
dedicated subdomain (with basic auth) or an SSH tunnel, never directly.

## A note on REALITY/Shadowsocks link formats

I generated these from Xray's documented config schema and standard
key encodings (verified the REALITY key format matches Xray's expected
32-byte base64url encoding), but I don't have a real Xray instance to
test end-to-end against actual client apps. If a generated link doesn't
import cleanly into your client of choice, check docs/REALITY.md /
docs/SHADOWSOCKS.md's verification steps first — the underlying config
Xray receives is more likely correct than the exact client-side URI
formatting, which has more variation between app implementations.
