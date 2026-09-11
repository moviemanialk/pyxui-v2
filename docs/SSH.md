# OpenSSH Tunnel Accounts

How the SSH protocol option works, and what to check if it isn't behaving.

## What it actually does

Unlike VLESS/VMess/Trojan, an SSH client isn't an Xray inbound at all —
it's a real Linux user account on the VPS, created with:

```bash
useradd -M -s /usr/sbin/nologin -e <expiry> <username>
chpasswd  # sets the generated password
```

- `-M` — no home directory (not needed for tunneling)
- `-s /usr/sbin/nologin` — blocks interactive shell login. The account
  can still authenticate over SSH and use port forwarding
  (`ssh -D`, `-L`, `-R`) — `nologin` only blocks getting an actual shell
  prompt, which is exactly the access level a tunnel-only account should
  have.
- `-e <expiry>` — the account is auto-locked by the OS itself on that
  date, independent of the panel's own `enforce_limits()` check.

This is the classic "SSH tunnel account" model the free SSH sites from
your original question use — same idea, run on your own server.

## Requirement: AllowTcpForwarding

sshd must have `AllowTcpForwarding yes` (this is actually the default on
most distros, but some hardened images disable it). `install.sh` adds it
explicitly to `/etc/ssh/sshd_config` and restarts `sshd`. If you're
retrofitting onto an existing box, check manually:

```bash
grep AllowTcpForwarding /etc/ssh/sshd_config
```

If it's set to `no`, change it to `yes` and `systemctl restart sshd`.

## Connecting

The dashboard shows a ready-to-use command for each SSH client:

```bash
ssh -D 1080 -N <username>@<domain> -p 22
```

- `-D 1080` opens a local SOCKS5 proxy on port 1080
- `-N` means "don't run a remote command" (we don't want a shell — and
  couldn't get one anyway, since the account's shell is `nologin`)
- Point apps/browser at `127.0.0.1:1080` as a SOCKS5 proxy afterward

For whole-system tunneling rather than per-app SOCKS5, use `-D` with a
SOCKS-aware OS-level proxy tool, or `-L <local>:<dest>:<remote>` for
forwarding a specific port.

## Password handling — read this

The generated password is stored in the panel's SQLite database in
**plaintext**, not hashed, because — unlike the admin login — you need to
be able to retrieve and hand it to the person using that SSH account.
This is a deliberate trade-off for a single-admin personal panel, not an
oversight, but it does mean:

- Anyone with read access to `panel.db` can read every SSH password
- This is one more reason to lock down the panel itself (see
  docs/CONFIG.md's hardening section) and to keep VPS root access tight
- If this bothers you for a specific account, you can always set that
  account's password manually and just not store the panel-generated one

## Managing accounts

- **Disable** in the dashboard → `usermod -L <username>` (locks the
  account; password stays set but login is refused)
- **Enable** → `usermod -U <username>` (unlocks)
- **Delete** → `userdel <username>` (removes the account entirely — no
  `-r`, since there's no home directory to clean up)
- **Expiry** — set at creation via `useradd -e`; the OS itself enforces
  this independent of the panel

## Traffic limits don't apply to SSH clients

SSH tunneling doesn't go through Xray, so the panel has no visibility
into an SSH account's bandwidth usage — the traffic-limit field is
hidden for this protocol in the Add Client form. If you need bandwidth
caps on SSH accounts specifically, that would need to be enforced at the
OS/network level (e.g. `tc` traffic shaping or per-user `iptables`
counters) — outside what this panel manages.

## SSH-over-TLS ("sshtls") — the disguised version

The plain `ssh` protocol above works fine on port 22 for most networks,
but some clients specifically expect "SSL + SSH" — TLS-wrapped SSH on a
port like 443/444, for networks that block or throttle raw SSH. The
`sshtls` protocol type creates the exact same kind of Linux account as
plain SSH, but the panel also configures **stunnel** to wrap it in TLS.

### How it's wired

```
Client (HTTP Injector, etc.)
   │  TLS handshake
   ▼
stunnel :444  (uses your existing Let's Encrypt cert)
   │  plaintext SSH after TLS is stripped off
   ▼
sshd :22  (same restricted accounts as plain SSH)
```

The panel writes `/etc/stunnel/stunnel.conf` and restarts the
`stunnel4` service automatically whenever you create an `sshtls`
client, save Settings, or change the domain — using whatever
certificate is currently at
`/etc/letsencrypt/live/<domain>/`.

### Requirements

- `stunnel4` installed (`install.sh` does this)
- A valid cert already issued for your domain (the same one used for
  nginx) — if certbot hasn't run yet, `sshtls` client creation will
  report a stunnel setup failure; get the cert first, then re-save
  Settings to retry
- The stunnel port (default `444`) open in your VPS firewall — this is
  **separate** from port 443, since nginx already owns that; see
  docs/INSTALL.md's firewall step for adding it

### Certificate renewal

Certbot renewing your main cert won't automatically update stunnel's
copy of it. Add a renewal hook:
```bash
sudo nano /etc/letsencrypt/renewal-hooks/deploy/stunnel.sh
```
```bash
#!/bin/bash
DOMAIN="vpn.yourdomain.com"   # match your actual domain
cat /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/letsencrypt/live/$DOMAIN/privkey.pem > /etc/stunnel/stunnel.pem
chmod 600 /etc/stunnel/stunnel.pem
systemctl restart stunnel4
```
```bash
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/stunnel.sh
```
(The panel itself also rewrites this file whenever you save Settings,
so this hook is a safety net for automatic renewals specifically,
which happen outside of any panel interaction.)

### Connecting (HTTP Injector and similar apps)

- **Tunnel type**: SSL + SSH (or "SSH over SSL/TLS")
- **Server / SNI**: your domain
- **Port**: whatever's set in Settings → SSH-over-TLS (default 444)
- **Username / Password**: from the client card on the dashboard
- The app's own "SNI hostname" field (some apps default this to
  something unrelated, like `aka.ms`) doesn't need to match your
  domain — stunnel doesn't inspect or route on SNI, it just presents
  whichever certificate is configured. What has to match is the
  **port** and the actual SSH credentials.

## Why not put plain SSH behind nginx/WebSocket like the others?

You could (this is exactly what the `sshtls` protocol above does, via
stunnel), but plain SSH mostly doesn't need that added complexity —
SSH's own protocol already works fine on port 22 in most networks. Use
`sshtls` specifically when a client needs the disguised version.
