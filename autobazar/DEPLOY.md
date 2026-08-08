# Deploying AutoBazar on Oracle Cloud Always Free

Replaces the previous Railway deployment. The bot needs three things at once,
and no free PaaS offers all three: an always-on process (long polling), a
persistent disk (SQLite at `/data`), and a public HTTPS URL with a valid
certificate — Telegram Mini Apps refuse anything else.

## 1. Create the tenancy

Sign up at [cloud.oracle.com](https://cloud.oracle.com). A card is required for
identity verification; Always Free resources are never charged. The **home
region is permanent and cannot be changed later**, and Always Free compute only
exists in it — pick the one you actually want.

## 2. Network and instance

Everything below runs in **Cloud Shell** (the `>_` icon in the console), where
the OCI CLI is already authenticated — no API keys to set up.

```bash
git clone https://github.com/linaaadd/autobazar.git ~/ab
bash ~/ab/autobazar/oci-bootstrap.sh a1
```

This creates the VCN, internet gateway, route, public subnet and security
rules for 22/80/443, then launches `VM.Standard.A1.Flex` (1 OCPU / 6 GB),
retrying once a minute for as long as it takes. It is idempotent — re-running
reuses whatever already exists.

Clone rather than `curl` the raw URL: raw.githubusercontent caches for several
minutes, so a freshly pushed fix is silently not the one you run.

### When A1 has no capacity

`Out of host capacity` for Ampere is routine in busy regions, and it is a stock
problem, not an account problem. Amsterdam has a single availability domain, so
there is no second AD to fall back to, and the region cannot be changed.

The other Always Free shape is x86 and almost always available:

```bash
bash ~/ab/autobazar/oci-bootstrap.sh micro     # VM.Standard.E2.1.Micro, 1 GB
```

1 GB is enough — the bot idles around 250 MB — but add swap for the Pillow
spikes during watermarking. `deploy.sh` does not do this; run it once:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 3. Reserve the public IP

A launched instance gets an **ephemeral** address, which is released when the
instance stops — and `WEBAPP_URL` dies with it. Oracle cannot convert one in
place, so reserving deliberately changes the address:

```bash
bash ~/ab/autobazar/oci-bootstrap.sh reserve-ip
```

Do this before the first deploy, or re-run `deploy.sh` afterwards so the URLs
follow the new address.

## 4. Deploy

Copy the secrets from your own machine — nothing secret lives in the repo:

```bash
scp -i ~/.ssh/oracle_autobazar autobazar/.env ubuntu@<ip>:~/.env
```

Then on the VM:

```bash
git clone https://github.com/linaaadd/autobazar.git
mv ~/.env ~/autobazar/autobazar/.env
cd ~/autobazar/autobazar && chmod +x deploy.sh && ./deploy.sh caddy
```

`deploy.sh` installs Docker, opens the firewall, points `SITE_ADDRESS` and
`WEBAPP_URL` at `<ip-with-dashes>.sslip.io`, builds, starts, and waits for the
health check. It stops with a clear message if a variable is missing, and is
safe to re-run.

### The two HTTPS options

- **`caddy`** — no domain needed. `sslip.io` resolves a dashed IP to that IP, so
  Caddy obtains a real Let's Encrypt certificate for it.
- **`tunnel`** — needs a domain on Cloudflare. Create a tunnel in the Zero Trust
  dashboard pointing at `http://bot:8080`, put its token in `TUNNEL_TOKEN`.
  Nothing is exposed inbound, so step 5 below does not apply.

## 5. Two firewalls, not one

This is the usual reason a correctly configured server answers nothing. Ports
must be open in **both** the VCN security list (handled by `oci-bootstrap.sh`)
and `iptables` on the instance (handled by `deploy.sh`).

Oracle's Ubuntu image ends its INPUT chain with a blanket REJECT, so an ACCEPT
rule appended after it never matches. `deploy.sh` inserts ahead of the REJECT —
worth knowing if you ever add a port by hand.

## 6. Verify

```bash
docker compose logs -f bot
curl -s https://<your-host>/health
```

Expect `✅ AutoBazar Bot запущен!` and `OK`. Then open the bot in Telegram and
press **🚗 Подать объявление** — if the Mini App opens, `WEBAPP_URL` is right.

`telegram.error.Conflict: terminated by other getUpdates request` means a second
copy of the bot is still polling with the same token somewhere. Only one may
run.

## Notes

- `TZ=UTC` is pinned in the image and in compose. The daily jobs run at fixed
  UTC times (09:00 repost, 08:00 cleanup, 10:00 expiry warnings); a host on
  Amsterdam time would silently shift all three.
- SQLite lives in the named volume `bot-data`, so `docker compose down` keeps
  the listings. `docker compose down -v` does not.
- A `.env` copied from Windows usually has no trailing newline; `deploy.sh`
  adds one before appending, or the appended variable would fuse onto the last
  line.
- Updating: `git pull && ./deploy.sh caddy`.
