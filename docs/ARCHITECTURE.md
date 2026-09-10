# Architecture

## Traffic flow

```
                         Internet
                             │
                             │  TLS ClientHello with SNI = vpn.yourdomain.com
                             ▼
                    ┌─────────────────┐
                    │  nginx :443/:80 │  terminates TLS, reads Host/path,
                    │                 │  routes by WebSocket path
                    └────────┬────────┘
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
      Xray VLESS        Xray VMess       Xray Trojan
      127.0.0.1:10001   127.0.0.1:10002  127.0.0.1:10003
             │                │                │
             └────────────────┴────────────────┘
                             ▼
                        Xray "freedom"
                        outbound → internet
```

Separately:

```
      PyXUI panel (Flask, :2053)
             │
             ├── writes ──▶ /usr/local/etc/xray/config.json
             ├── runs ────▶ systemctl restart xray
             └── queries ─▶ Xray stats API (127.0.0.1:62789, loopback only)
                             for per-client traffic totals
```

## Why nginx sits in front of Xray

Xray's inbounds listen only on `127.0.0.1` — they're never exposed
directly. nginx is the only thing bound to the public IP on 443/80. This
means:

- **TLS is terminated once**, at nginx, using a normal Let's Encrypt
  cert — no self-signed cert weirdness on the client side.
- **SNI-based routing** happens at the TLS layer: the domain name in the
  handshake is what identifies this is a request "for" your VPN
  endpoint, before any WebSocket/HTTP logic even runs.
- **Traffic looks like ordinary HTTPS** to anything doing shallow
  inspection — same TLS handshake shape as visiting any HTTPS website.
  The specific WebSocket path (`/vless-ws` etc.) then routes to the
  right Xray inbound; anything else returns a plain 404, so the domain
  looks like a dead/empty site to casual scanning.

## Why the panel and Xray are separate processes

The Flask panel never proxies live VPN traffic — it only:
1. Manages the client list in SQLite (`panel.db`)
2. Regenerates `config.json` from that list
3. Restarts the `xray` systemd service to apply it
4. Polls Xray's separate stats API for traffic numbers to display

This keeps the panel simple and means a panel bug/restart can't take
down active VPN connections mid-session (Xray keeps running
independently once configured).

## Data model

```
clients
├── id                  primary key
├── remark              label + Xray "email" tag for stats
├── protocol            vless | vmess | trojan
├── client_uuid         UUID (used as VLESS/VMess id, or Trojan password)
├── sub_token            random token for the public /sub/<token> link
├── enabled              0/1
├── traffic_limit_gb     0 = unlimited
├── traffic_used_bytes   cached from last stats query
├── created_at
└── expiry_date          nullable

settings
├── domain
├── public_port
├── vless_ws_path
├── vmess_ws_path
└── trojan_ws_path

admin
├── username
└── password_hash
```

## Why WebSocket rather than raw TCP

Xray supports several transports; WebSocket is used here because it lets
nginx (an ordinary, well-understood HTTP-and-TLS-terminating reverse
proxy) sit in front of everything without any Xray-specific network
plumbing — nginx just sees "an HTTPS request that wants to upgrade to a
WebSocket at this path" and proxies it like it would any other
WebSocket backend (e.g. a chat app). This is also what makes multiplexing
three separate protocols behind one IP/port straightforward: it's just
three different paths.
