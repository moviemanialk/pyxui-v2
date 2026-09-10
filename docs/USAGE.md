# Usage Guide

Day-to-day operation once the panel is installed and running.

## Adding a client

1. Dashboard → **+ Add Client**
2. Fill in:
   - **Remark** — a label for you, e.g. `sithum-phone`, `laptop-work`.
     Keep these unique; the panel uses this as the identity Xray reports
     traffic stats against.
   - **Protocol** — VLESS is the simplest and most widely supported;
     Trojan looks more like plain HTTPS traffic to network inspection;
     VMess is the older/more compatible option if a client app doesn't
     support VLESS; **OpenSSH** creates a real Linux tunnel account
     instead of an Xray client — see [SSH.md](SSH.md) for how it differs
     (no QR/subscription link, no traffic limit, password shown once on
     the dashboard).
   - **Traffic limit** — `0` for unlimited, or a GB cap (e.g. `50`).
   - **Expiry date** — optional; leave blank for no expiry.
3. Save. You're taken back to the dashboard where the new client's
   connection link, QR code link, and subscription link are all
   immediately available.

## Connecting a device

### Option A — paste the link
Copy the `vless://…`, `vmess://…`, or `trojan://…` link shown on the
dashboard into a compatible client app:
- **Windows** — v2rayN
- **Android** — v2rayNG
- **iOS** — Shadowrocket, Streisand, or FoXray
- **macOS** — V2rayU, or Xray directly

Most of these apps have an "Import from clipboard" or "Import from URL"
option that reads the link directly.

### Option B — scan the QR code
Click **QR code** next to any client on the dashboard. Most mobile
client apps (v2rayNG, Shadowrocket, etc.) can scan this directly from
their "add server" screen.

### Option C — subscription link
Click **Sub link** next to a client. This opens a small page of base64
text at a URL like `https://vpn.yourdomain.com/sub/<random-token>`. Some
client apps let you add a "subscription" by URL instead of pasting a
single link — paste this URL there. It updates automatically if you ever
regenerate that client (note: currently one client per sub link, not a
multi-server subscription).

## Managing existing clients

- **Disable** — temporarily cuts off a client without deleting it (their
  entry is dropped from `config.json` on the next save, so re-enabling
  restores the same UUID/link).
- **Delete** — permanently removes the client and its link stops working
  immediately after the next config reload.
- **Editing** a client (change its traffic limit, expiry, etc.) isn't
  exposed in the UI yet — delete and re-add it with new settings. This is
  a straightforward addition if you want to extend `app.py` — see the
  `/clients/add` route as a template for an `/clients/<id>/edit` route.

## Traffic usage

The dashboard's Traffic column reflects the last time it queried Xray's
stats API — this happens automatically every time you load the
dashboard. If a client shows `0 B` right after creation, that's normal;
it updates once real traffic has flowed. If it's always `0 B` even after
you've used the connection, see docs/TROUBLESHOOTING.md.

Clients that hit their traffic limit or pass their expiry date are
auto-disabled the next time the dashboard loads or a config write
happens — there's no separate background job, so the panel needs to be
opened (or hit via a cron `curl` to `/`) periodically if you want prompt
enforcement while you're not actively using it.

## Changing server-wide settings

Settings → domain, public port, or any of the three WebSocket paths.
Remember: if you change a WebSocket path here, you must also update the
matching `location` block in your nginx config and reload nginx
(`nginx -t && systemctl reload nginx`) — the panel only controls the
Xray side, not nginx.
