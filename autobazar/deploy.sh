#!/usr/bin/env bash
# Bootstrap AutoBazar on a fresh Oracle Cloud Always Free VM (Ubuntu, ARM).
#
#   ./deploy.sh caddy    — HTTPS via Caddy + sslip.io (no domain needed)
#   ./deploy.sh tunnel   — HTTPS via Cloudflare Tunnel (domain on Cloudflare)
#
# Safe to re-run: it installs what is missing, leaves the rest alone.
set -euo pipefail

PROFILE="${1:-caddy}"
cd "$(dirname "$0")"

case "$PROFILE" in
	caddy|tunnel) ;;
	*) echo "usage: $0 [caddy|tunnel]" >&2; exit 1 ;;
esac

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- .env ------------------------------------------------------------------
if [ ! -f .env ]; then
	cp .env.example .env
	die ".env was missing — a template has been created.
Fill in TELEGRAM_TOKEN, ANTHROPIC_API_KEY, CHANNEL_ID, ADMIN_ID, then re-run.
Or copy the one from your machine:  scp autobazar/.env ubuntu@<ip>:~/autobazar/autobazar/.env"
fi

for var in TELEGRAM_TOKEN ANTHROPIC_API_KEY CHANNEL_ID; do
	grep -qE "^${var}=.+" .env || die "$var is empty in .env"
done

# --- Docker ----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
	say "Installing Docker"
	curl -fsSL https://get.docker.com | sudo sh
	sudo usermod -aG docker "$USER"
	NEED_RELOGIN=1
fi

DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

# --- HTTPS setup -----------------------------------------------------------
if [ "$PROFILE" = "caddy" ]; then
	say "Opening ports 80/443 in the local firewall"
	# The VCN security list must allow them too — that part is console-only.
	for port in 80 443; do
		sudo iptables -C INPUT -m state --state NEW -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
			|| sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$port" -j ACCEPT
	done
	sudo netfilter-persistent save >/dev/null

	IP="$(curl -fsS --max-time 10 https://api.ipify.org)" || die "could not determine the public IP"
	HOST="${IP//./-}.sslip.io"

	# Both must track the VM's current public IP, so they are rewritten rather
	# than filled in only when blank — a .env carried over from Railway still
	# holds the dead *.up.railway.app URL.
	for kv in "SITE_ADDRESS=$HOST" "WEBAPP_URL=https://$HOST"; do
		key="${kv%%=*}"
		grep -qF "$kv" .env && continue
		say "$key -> ${kv#*=}"
		grep -qE "^$key=" .env \
			&& sed -i "s|^$key=.*|$kv|" .env \
			|| printf '%s\n' "$kv" >> .env
	done
else
	grep -qE '^TUNNEL_TOKEN=.+' .env || die "TUNNEL_TOKEN is empty in .env"
	grep -qE '^WEBAPP_URL=https://.+' .env || die "WEBAPP_URL is empty in .env"
	grep -qE '^WEBAPP_URL=https://your-' .env && die "WEBAPP_URL still holds the placeholder"
fi

# --- Run -------------------------------------------------------------------
say "Building and starting (profile: $PROFILE)"
$DOCKER compose --profile "$PROFILE" up -d --build

say "Waiting for the health check"
for _ in $(seq 1 30); do
	state="$($DOCKER compose ps --format '{{.Health}}' bot 2>/dev/null | head -1)"
	[ "$state" = "healthy" ] && break
	sleep 5
done

URL="$(grep -E '^WEBAPP_URL=' .env | cut -d= -f2-)"
say "Done. Bot health: ${state:-unknown}"
echo "Public URL: $URL"
echo "Logs:       $DOCKER compose logs -f bot"
[ -n "${NEED_RELOGIN:-}" ] && echo "Log out and back in to use docker without sudo."
exit 0
