# Shadowsocks (2022, multi-user)

## Why 2022-blake3-aes-256-gcm specifically

Classic Shadowsocks AEAD ciphers (like `chacha20-ietf-poly1305`)
traditionally support only **one password per inbound** in most
Xray-core versions — fine for a single user, awkward for a panel meant
to manage many. The "2022" Shadowsocks methods (blake3-based) were
specifically designed to support proper **multi-user setups**: one
server-wide key plus a separate per-user key, combined at connection
time. That's what lets each client in this panel get its own distinct
password while sharing one inbound/port.

## Settings

| Setting | Meaning |
|---|---|
| Port | Direct public port — not behind nginx, same reasoning as REALITY (raw Shadowsocks isn't an HTTP/WebSocket protocol nginx can proxy). Default `8388`. |
| Method | `2022-blake3-aes-256-gcm` by default. Only change this if you specifically need a different cipher and know your client apps support it. |
| Server PSK | Generated automatically, shown read-only in Settings. This is the server-wide half of every client's key. |

Each client gets its own `ss_password` (their half of the key),
generated at creation and shown in their connection link.

## Connecting

The dashboard's link looks like:
```
ss://<base64(method:server_psk:client_psk)>@<domain>:<port>#<remark>
```

**Important — verify this against your actual client app.** Shadowsocks
2022 link conventions have some variation between implementations
(some clients expect the `method:password` portion base64-encoded
separately from the host, others expect the whole thing encoded
differently). If a client fails to import the link directly:

1. Decode the current link's base64 portion to confirm it reads
   `method:server_psk:client_psk` as plain text.
2. Manually enter server address, port, method, and the combined
   `server_psk:client_psk` string as the password field if your
   specific client app has separate fields instead of accepting a
   single `ss://` URI.

## Verify on your VPS

Confirm your Xray-core version actually supports the 2022 methods
(this has been supported since Xray-core 1.8.0):
```bash
xray version
```

Check the generated config looks right:
```bash
cat /usr/local/etc/xray/config.json | python3 -m json.tool | grep -A15 '"shadowsocks"'
```

You should see a `method`, top-level `password` (the server PSK), and
a `clients` array with each client's own `password` + `email`.
