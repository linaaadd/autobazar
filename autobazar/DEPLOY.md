# Deploying AutoBazar on an Oracle Cloud Always Free VM

Replaces the previous Railway deployment. The bot needs three things a free
PaaS usually will not give at once: an always-on process (long polling), a
persistent disk (SQLite at `/data`), and a public HTTPS URL with a valid
certificate (Telegram Mini Apps refuse anything else).

## 1. Create the VM

Oracle Cloud → Compute → Instances → Create instance.

- Image: **Ubuntu 24.04 (aarch64)**
- Shape: **VM.Standard.A1.Flex**, 2 OCPU / 12 GB
  (the Always Free ceiling since 15 June 2026 — 1 OCPU / 6 GB is plenty here)
- Boot volume: 50 GB
- Save the SSH public key, and note the public IPv4 address

If the console answers `Out of host capacity`, the region has no free A1 stock
at that moment. Retry later, or pick another availability domain.

Reserve the IP so it survives a stop/start: Networking → Reserved public IPs →
attach to the instance VNIC. A changing IP breaks `WEBAPP_URL`.

## 2. Open the ports (Caddy profile only)

Two separate firewalls have to agree. Missing the second one is the classic
"the security list is open but nothing connects" trap.

VCN → Security Lists → default → add ingress rules:
`0.0.0.0/0` → TCP `80` and TCP `443`.

Then on the instance itself:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

The Cloudflare Tunnel profile needs neither step — the tunnel dials outbound.

## 3. Fast path — one script

`deploy.sh` does everything the rest of this document describes by hand:
installs Docker, opens the local firewall, derives the sslip.io hostname from
the VM's public IP, writes it into `.env`, builds, starts, and waits for the
health check. Re-running it is safe.

```bash
git clone https://github.com/linaaadd/autobazar.git
cd autobazar/autobazar
```

Copy your secrets over from your own machine (nothing secret lives in the
repo):

```bash
scp autobazar/.env ubuntu@<vm-ip>:~/autobazar/autobazar/.env
```

Then, on the VM:

```bash
chmod +x deploy.sh && ./deploy.sh caddy     # or: ./deploy.sh tunnel
```

It stops with a clear message if a required variable is missing. Sections 4–5
below are the manual equivalent, kept for when something needs unpicking.

## 4. Get the code and configure

```bash
git clone https://github.com/linaaadd/autobazar.git
cd autobazar/autobazar
cp .env.example .env
nano .env
```

Fill in `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, `CHANNEL_ID`, `ADMIN_ID`, and
the HTTPS variables for whichever option you pick below.

## 5. Pick an HTTPS option

### A — Caddy + sslip.io (no domain required)

`sslip.io` resolves any dashed IP to that IP, so Caddy can get a real Let's
Encrypt certificate for it. For public IP `130.61.42.7`:

```
SITE_ADDRESS=130-61-42-7.sslip.io
WEBAPP_URL=https://130-61-42-7.sslip.io
```

```bash
docker compose --profile caddy up -d --build
```

### B — Cloudflare Tunnel (needs a domain on Cloudflare)

Zero Trust dashboard → Networks → Tunnels → create a tunnel → add a public
hostname (e.g. `autobazar.example.com`) pointing at `http://bot:8080`. Copy the
connector token.

```
TUNNEL_TOKEN=eyJhIjoi...
WEBAPP_URL=https://autobazar.example.com
```

```bash
docker compose --profile tunnel up -d --build
```

Nothing is exposed to the internet directly, and the certificate is
Cloudflare's.

## 6. Verify

```bash
docker compose logs -f bot
curl -s https://<your-host>/health
```

Expect `✅ AutoBazar Bot запущен!` in the logs and a healthy response from
`/health`. Then open the bot in Telegram and press **🚗 Подать объявление** —
if the Mini App opens, `WEBAPP_URL` is correct.

## Migrating the existing database

The Railway volume held `/data/autobazar.db`. To carry it over:

```bash
docker compose cp autobazar.db bot:/data/autobazar.db
docker compose restart bot
```

## Notes

- `TZ=UTC` is set explicitly in the image and in compose. The daily jobs are
  scheduled at fixed UTC times (09:00 repost, 08:00 cleanup, 10:00 expiry
  warnings); a host in Amsterdam time would silently shift all three.
- SQLite lives in the named volume `bot-data`, so `docker compose down` does
  not lose listings. `docker compose down -v` does.
- Updating: `git pull && docker compose --profile <caddy|tunnel> up -d --build`.
- `railway.toml` is kept only for reference; it is unused here.
