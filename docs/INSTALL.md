# Install Guide

Full setup from a bare VPS to a working, connectable server.

## Prerequisites

- A VPS (any provider — DigitalOcean, Vultr, Contabo, etc.), Ubuntu 22.04
  or Debian 12 recommended
- A domain name you control, with the ability to add an A record
- Root SSH access to the VPS

## 1. Point your domain at the VPS

In your domain registrar / DNS provider, add an **A record**:

```
Type: A
Name: vpn  (or whatever subdomain you want, e.g. vpn.example.com)
Value: <your VPS's public IP>
TTL: default
```

Wait a few minutes and confirm it resolves:

```bash
dig +short vpn.yourdomain.com
```

## 2. Copy the project to the VPS

From your local machine:

```bash
scp -r pyxui-v2 root@<vps-ip>:/root/
ssh root@<vps-ip>
cd pyxui-v2
```

## 3. Run the installer

```bash
sudo bash install.sh
```

This installs Xray-core, stunnel, OpenSSH, Python, nginx, and certbot;
sets up a Python virtualenv; and registers the panel as a systemd
service (`pyxui`) running under **gunicorn**, bound to
`127.0.0.1:2053` — **not** reachable from the internet directly. This
is deliberate: the panel is only ever meant to be reached through
nginx (step 6) or an SSH tunnel, never by hitting the VPS's public IP
on port 2053.

Check it's running:

```bash
systemctl status pyxui
systemctl status xray
```

## 4. Get TLS certificates

You need one for your main tunnel domain, and — recommended — a
separate one for a dedicated panel subdomain (see step 6 for why):

```bash
sudo certbot certonly --standalone -d vpn.yourdomain.com
sudo certbot certonly --standalone -d panel.yourdomain.com
```

Add an A record for `panel.yourdomain.com` pointing at the same VPS IP
before running the second command.

If nginx is already bound to port 80/443, stop it first:
`systemctl stop nginx`, run certbot, then `systemctl start nginx`.

## 5. Configure nginx for the tunnel protocols

```bash
cp nginx.conf.example /etc/nginx/sites-available/pyxui
nano /etc/nginx/sites-available/pyxui   # replace vpn.yourdomain.com everywhere
```

**Remove the `location /panel/` block** if you see one in the example
file — path-prefixing Flask behind nginx is fragile (login redirects
break out of the prefix) and isn't used in this version. Use a
dedicated subdomain instead (next step).

```bash
ln -s /etc/nginx/sites-available/pyxui /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

## 6. Give the panel its own subdomain

This avoids all the subpath-redirect problems entirely — Flask just
serves normally from `/`, which is what it already expects.

```bash
nano /etc/nginx/sites-available/pyxui-panel
```
```nginx
server {
    listen 443 ssl;
    server_name panel.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/panel.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:2053/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 80;
    server_name panel.yourdomain.com;
    return 301 https://$host$request_uri;
}
```
```bash
ln -s /etc/nginx/sites-available/pyxui-panel /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Strongly recommended before exposing this: add HTTP basic auth as a
second layer in front of the panel login — see docs/CONFIG.md's
hardening section.

## 7. Log in and configure the panel

Open `https://panel.yourdomain.com` in your browser.

- Username: `admin`
- Password: `admin123`

Five failed attempts locks the account for 15 minutes — this is
automatic, not something to disable.

**Immediately** go to Settings and:
1. Set your real domain (`vpn.yourdomain.com` — the tunnel domain, not the panel one)
2. Set a new admin password
3. Save (this also writes the first Xray config and restarts Xray)

If you'd rather skip standing up a panel subdomain entirely, an SSH
tunnel works too and needs nothing else configured:
```bash
ssh -L 2053:127.0.0.1:2053 root@<vps-ip>
# then open http://127.0.0.1:2053 locally, for as long as that SSH session stays open
```

## 8. Add your first client

From the dashboard, click **+ Add Client**, choose a protocol, optionally
set a traffic limit and expiry date, and save. Xray-managed protocols
(VLESS/VMess/Trojan/REALITY/Shadowsocks) get a connection link and QR
code immediately; SSH/SSH-over-TLS get a username/password. See
docs/USAGE.md, docs/REALITY.md, docs/SHADOWSOCKS.md, and docs/SSH.md
for protocol-specific details.

**If you plan to use REALITY, Shadowsocks, or SSH-over-TLS**, open
their (non-443) ports in your firewall too — these listen directly,
not through nginx:
```bash
gcloud compute firewall-rules create allow-pyxui-extra \
  --allow=tcp:8443,tcp:8388,tcp:444 \
  --direction=INGRESS --target-tags=vpn-server
```
(Adjust port numbers if you changed the defaults in Settings.)

## 9. Verify it's actually working

```bash
systemctl status xray         # should say "active (running)"
systemctl status stunnel4     # if using SSH-over-TLS
ss -tlnp | grep -E '10001|10002|10003|62789|8443|8388'
curl -I https://vpn.yourdomain.com/vless-ws    # should get an HTTP response, not a timeout
```

You're done. Continue to [USAGE.md](USAGE.md) for day-to-day client
management, or [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if something
above didn't work.
