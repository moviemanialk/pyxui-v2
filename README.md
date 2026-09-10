# PyXUI v2

A self-hosted, X-UI style web panel for managing an Xray-core server —
VLESS, VMess, and Trojan over WebSocket+TLS, with per-client traffic
limits, expiry dates, QR codes, and subscription links.

Built as a lighter, easier-to-modify Python/Flask alternative to the
Go-based X-UI/3X-UI panels, for running your **own** private tunnel to
your **own** devices.

## What's inside

```
pyxui-v2/
├── app.py                  # Flask backend — all panel logic
├── requirements.txt
├── install.sh               # one-shot VPS installer
├── nginx.conf.example       # TLS + WebSocket routing for all 3 protocols
├── templates/                # dashboard, add-client, settings, QR pages
└── docs/
    ├── INSTALL.md            # step-by-step VPS setup
    ├── CONFIG.md              # every setting explained
    ├── USAGE.md               # day-to-day panel usage
    ├── ARCHITECTURE.md        # how the pieces fit together
    └── TROUBLESHOOTING.md
```

## Quick start

1. Read **[docs/INSTALL.md](docs/INSTALL.md)** — full VPS setup, start to finish.
2. Then **[docs/USAGE.md](docs/USAGE.md)** for day-to-day client management.
3. **[docs/CONFIG.md](docs/CONFIG.md)** if you want to change defaults (ports, paths, etc).
4. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for how traffic actually flows.
5. Stuck? **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**.

## Feature summary

| Feature | Status |
|---|---|
| VLESS / VMess / Trojan (WS + TLS) | ✅ |
| Add / enable / disable / delete clients | ✅ |
| Per-client traffic limit (GB) | ✅ |
| Live traffic usage (via Xray stats API) | ✅ |
| Auto-disable on expiry or over-limit | ✅ |
| QR code per client | ✅ |
| Subscription link per client | ✅ |
| Auto config.json generation + xray restart | ✅ |
| Multi-user admin accounts | ❌ (single admin only) |
| Reverse-proxy reseller/multi-node support | ❌ (single server only) |

## Security note

This manages **your own** server for **your own** devices. It does not
include reseller/multi-tenant billing features, and running it as an open
relay for strangers carries real abuse and liability risk — see
[docs/CONFIG.md](docs/CONFIG.md) for hardening the panel itself before
exposing it publicly.
