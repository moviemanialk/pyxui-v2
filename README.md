# PyXUI v3

A self-hosted, X-UI style web panel for managing an Xray-core server —
**VLESS, VMess, Trojan (WebSocket+TLS), and native OpenSSH tunnel
accounts** — with per-client traffic limits, expiry dates, QR codes,
subscription links, live server stats, and a redesigned dashboard.

Built as a lighter, easier-to-modify Python/Flask alternative to the
Go-based X-UI/3X-UI panels, for running your **own** private tunnel to
your **own** devices.

## What's new in v3

- **OpenSSH tunnel accounts** — a 4th client type alongside VLESS/VMess/
  Trojan. Creates a real, shell-restricted Linux user for classic
  `ssh -D`/`-L` tunneling (see [docs/SSH.md](docs/SSH.md))
- **Redesigned UI** — sidebar navigation, stat cards, live CPU/RAM/disk/
  uptime widget, protocol badges, toast notifications, click-to-copy on
  every link/password/username, search + protocol/status filters
- **Client cards** instead of a dense table — clearer on both desktop
  and mobile

## What's inside

```
pyxui-v3/
├── app.py                  # Flask backend — all panel logic
├── requirements.txt
├── install.sh               # one-shot VPS installer
├── nginx.conf.example       # TLS + WebSocket routing for the 3 Xray protocols
├── templates/                # dashboard, add-client, settings, QR pages
└── docs/
    ├── INSTALL.md            # step-by-step VPS setup
    ├── CONFIG.md              # every setting explained
    ├── USAGE.md               # day-to-day panel usage
    ├── SSH.md                  # how OpenSSH tunnel accounts work
    ├── ARCHITECTURE.md        # how the pieces fit together
    └── TROUBLESHOOTING.md
```

## Quick start

1. **[docs/INSTALL.md](docs/INSTALL.md)** — full VPS setup, start to finish.
2. **[docs/USAGE.md](docs/USAGE.md)** — day-to-day client management.
3. **[docs/SSH.md](docs/SSH.md)** — specifics of the OpenSSH account type.
4. **[docs/CONFIG.md](docs/CONFIG.md)** — change defaults (ports, paths, etc).
5. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how traffic actually flows.
6. Stuck? **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**.

## Feature summary

| Feature | Status |
|---|---|
| VLESS / VMess / Trojan (WS + TLS) | ✅ |
| OpenSSH tunnel accounts (real Linux users) | ✅ |
| Add / enable / disable / delete clients | ✅ |
| Per-client traffic limit (GB) — Xray protocols only | ✅ |
| Live traffic usage (via Xray stats API) | ✅ |
| Auto-disable / auto-lock on expiry or over-limit | ✅ |
| QR code per client (Xray protocols) | ✅ |
| Subscription link per client (Xray protocols) | ✅ |
| Live CPU / RAM / disk / uptime widget | ✅ |
| Search + protocol/status filters | ✅ |
| Click-to-copy links, usernames, passwords | ✅ |
| Auto config.json generation + xray restart | ✅ |
| Multi-user admin accounts | ❌ (single admin only) |
| SSH-over-TLS on port 443 (disguised as HTTPS) | ❌ (SSH runs on plain port 22 — see docs/SSH.md) |
| Reverse-proxy reseller/multi-node support | ❌ (single server only) |

## Security note

This manages **your own** server for **your own** devices. It does not
include reseller/multi-tenant billing features. SSH account passwords
are stored in plaintext in the panel's database (necessary so they can
be shown to you) — see [docs/SSH.md](docs/SSH.md) for the trade-off, and
[docs/CONFIG.md](docs/CONFIG.md) for locking the panel itself down
before exposing it publicly.
