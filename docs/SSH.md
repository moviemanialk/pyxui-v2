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

## Why not put SSH behind nginx/WebSocket like the others?

You could (this is what "SSH over TLS" via stunnel/Dropbear-on-443
services do), but it adds real complexity for a use case that mostly
doesn't need SNI-based disguising — SSH's own protocol already works
fine on port 22 in most networks. If your use case specifically needs
SSH disguised as HTTPS traffic on 443, that's a good next feature to
add (stunnel wrapping port 22, fronted by nginx's `stream {}` module for
SNI-based routing) — it's not built into this version.
