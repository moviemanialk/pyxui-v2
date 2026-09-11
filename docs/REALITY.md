# REALITY (Cloned TLS Handshake)

This is the closest thing to what "SNI cloning" actually means in the
Xray ecosystem — REALITY doesn't just set an SNI field, it makes your
server's TLS handshake **indistinguishable** from a real handshake with
a site you don't control, without needing a certificate or domain of
your own for this protocol.

## How it actually works

Normal TLS (what VLESS/VMess/Trojan+WS use here) needs your own
certificate for your own domain — a network inspector can still tell
it's *a* TLS server, just not necessarily what's behind it.

REALITY goes further: when someone connects, your Xray server
transparently proxies the TLS handshake to a **real, live site**
(the `dest` you configure — e.g. `www.microsoft.com:443`) for anyone
who isn't a valid client. Only connections presenting a valid key
(the private key configured on your server, matched against the
client's public key) get routed to the actual proxy; everyone else
— including automated probes — gets a completely genuine handshake
and response from the real site, because it *is* the real site's
response, relayed live. There's no fake certificate to catch, because
no certificate was faked.

## Settings

| Setting | Meaning |
|---|---|
| Port | Direct public port Xray listens on for this — **not** behind nginx. Default `8443`. |
| Dest | The real site whose handshake gets mimicked, `host:port` form. Must be TLS 1.3, support HTTP/2, and not be on Cloudflare (Cloudflare's edge behaves oddly with REALITY's probing) — see the "picking a dest" section below. |
| Server name | Usually the same hostname as `dest`, without the port. This is what appears in the SNI field. |
| Public key / Short ID | Shown read-only in Settings — generated automatically on first run. Regenerate any time via the button; note this invalidates every existing REALITY client link. |

The private key never leaves the server — only the public key goes
into client links.

## Picking a `dest`

Good candidates: large, well-known sites with their own real
certificates, TLS 1.3 + HTTP/2 support, and not fronted by Cloudflare
(Cloudflare-fronted sites frequently fail REALITY's connection probing
in practice). Commonly used examples: `www.microsoft.com:443`,
`www.bing.com:443`, `itunes.apple.com:443`. The default in this panel
(`www.microsoft.com:443`) is a reasonable starting point, but if
connections seem unreliable, try switching to a different dest — some
sites' edge infrastructure handles the underlying TCP proxying better
than others, and this can vary by region/network.

## Why no nginx, no certificate

REALITY intentionally doesn't sit behind nginx — nginx terminating TLS
first would defeat the entire premise, since the whole trick depends
on Xray itself handling the raw TLS handshake and deciding in real time
whether to relay it to the real site or to the actual proxy logic.
That's also why it needs its own dedicated port rather than sharing 443
with your other protocols (unless you set up SNI-based TCP-level
routing yourself, which is a more advanced multiplexing setup this
panel doesn't currently automate).

## Connecting

The dashboard generates a link like:
```
vless://<uuid>@<domain>:8443?security=reality&encryption=none&pbk=<public_key>&fp=chrome&sni=<server_name>&sid=<short_id>&type=tcp&flow=xtls-rprx-vision#<remark>
```
Import this into any REALITY-capable client (recent v2rayN, v2rayNG,
NekoBox, Xray directly). The `fp=chrome` field asks the client to mimic
Chrome's TLS fingerprint specifically — this matters for REALITY's
credibility and generally shouldn't be changed.

## Verify on your VPS

REALITY support requires a reasonably recent Xray-core version. Check:
```bash
xray version
```
If your installed version predates REALITY support (mid-2023 or
later builds should have it), update via the same installer script
used originally:
```bash
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)"
```

Also confirm the config the panel wrote actually looks right:
```bash
cat /usr/local/etc/xray/config.json | python3 -m json.tool | grep -A10 '"reality"'
```
