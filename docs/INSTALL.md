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

This installs Xray-core, Python, nginx, and certbot; sets up a Python
virtualenv; and registers the panel as a systemd service (`pyxui`)
listening on `127.0.0.1:2053` internally (reachable on your VPS's public
IP on port 2053 until you lock it down — see step 6).

Check it's running:

```bash
systemctl status pyxui
systemctl status xray
```

## 4. Get a TLS certificate

```bash
sudo certbot certonly --standalone -d vpn.yourdomain.com
```

If nginx is already bound to port 80/443, stop it first:
`systemctl stop nginx`, run certbot, then `systemctl start nginx`.

## 5. Configure nginx

```bash
cp nginx.conf.example /etc/nginx/sites-available/pyxui
nano /etc/nginx/sites-available/pyxui   # replace vpn.yourdomain.com everywhere
ln -s /etc/nginx/sites-available/pyxui /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

## 6. Log in and configure the panel

Open `http://<vps-ip>:2053` in your browser.

- Username: `admin`
- Password: `admin123`

**Immediately** go to Settings and:
1. Set your real domain (`vpn.yourdomain.com`)
2. Set a new admin password
3. Save (this also writes the first Xray config and restarts Xray)

## 7. Lock down the panel port

Once everything works over nginx on 443, close the panel's raw port
(2053) to the outside world and access it only through nginx or an SSH
tunnel:

```bash
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw deny 2053/tcp
ufw enable
```

To still reach the panel from your PC, use an SSH tunnel instead of
opening the port publicly:

```bash
ssh -L 2053:127.0.0.1:2053 root@<vps-ip>
# then open http://127.0.0.1:2053 locally
```

(Or use the `/panel/` path already wired up in `nginx.conf.example`, and
add HTTP basic auth to that location block — see docs/CONFIG.md.)

## 8. Add your first client

From the dashboard, click **+ Add Client**, choose a protocol (VLESS is
the simplest default), optionally set a traffic limit and expiry date,
and save. The client's `vless://` / `vmess://` / `trojan://` link and QR
code are shown immediately — see docs/USAGE.md for connecting a device.

## 9. Verify it's actually working

```bash
systemctl status xray        # should say "active (running)"
ss -tlnp | grep -E '10001|10002|10003|62789'   # inbounds listening locally
curl -I https://vpn.yourdomain.com/vless-ws    # should get an HTTP response, not a timeout
```

You're done. Continue to [USAGE.md](USAGE.md) for day-to-day client
management, or [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if something
above didn't work.
