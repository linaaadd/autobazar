#!/usr/bin/env bash
# Prepare the Oracle Cloud network and grab an Always Free instance.
# Meant to be run inside OCI Cloud Shell, where the CLI is already authenticated.
#
#   bash oci-bootstrap.sh          # chase VM.Standard.A1.Flex (ARM, 1 OCPU / 6 GB)
#   bash oci-bootstrap.sh micro    # take VM.Standard.E2.1.Micro (x86, 1 GB) instead
#
# Idempotent: it reuses the VCN, subnet and rules it created on a previous run,
# and exits immediately if the instance already exists.
set -uo pipefail

MODE="${1:-a1}"
NAME="autobazar"
PUBKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICAKZm1yFaZJFfSRQ8pJHAp+zxP+whwqS+CYeivNCAGT autobazar-oracle'
C="${OCI_TENANCY:?run this inside OCI Cloud Shell}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- swap the ephemeral public IP for a reserved one -----------------------
# An ephemeral address is released when the instance is stopped, which would
# break WEBAPP_URL. Oracle cannot convert one in place, so the address changes
# here: create the reserved IP first, and only then release the ephemeral one,
# so a failure never leaves the instance with no address at all.
if [ "$MODE" = "reserve-ip" ]; then
	ID=$(oci compute instance list -c "$C" --all \
		| jq -r --arg n "$NAME" '[.data[]|select(."display-name"==$n and .["lifecycle-state"]=="RUNNING")][0].id')
	VNIC=$(oci compute instance list-vnics --instance-id "$ID" | jq -r '.data[0].id')
	PRIV=$(oci network private-ip list --vnic-id "$VNIC" | jq -r '.data[0].id')
	OLD_IP=$(oci compute instance list-vnics --instance-id "$ID" | jq -r '.data[0]["public-ip"]')

	RESERVED=$(oci network public-ip list -c "$C" --scope REGION --all \
		| jq -r --arg n "$NAME-ip" '[.data[]|select(."display-name"==$n)][0].id // empty')
	if [ -z "$RESERVED" ]; then
		say "Creating a reserved public IP"
		RESERVED=$(oci network public-ip create -c "$C" --lifetime RESERVED \
			--display-name "$NAME-ip" --wait-for-state AVAILABLE | jq -r '.data.id')
	fi

	if [ -n "$OLD_IP" ] && [ "$OLD_IP" != null ]; then
		EPH=$(oci network public-ip get-by-ip-address -c "$C" --ip-address "$OLD_IP" | jq -r '.data.id')
		say "Releasing the ephemeral address $OLD_IP"
		oci network public-ip delete --public-ip-id "$EPH" --force >/dev/null
	fi

	say "Assigning the reserved address"
	NEW_IP=$(oci network public-ip update --public-ip-id "$RESERVED" --private-ip-id "$PRIV" \
		--wait-for-state ASSIGNED | jq -r '.data["ip-address"]')
	echo "reserved IP: $NEW_IP"
	echo "next: ssh in and re-run ./deploy.sh caddy so the URLs follow the new address"
	exit 0
fi

case "$MODE" in
	a1)    SHAPE="VM.Standard.A1.Flex"; SHAPE_CFG='{"ocpus":1,"memoryInGBs":6}' ;;
	micro) SHAPE="VM.Standard.E2.1.Micro"; SHAPE_CFG="" ;;
	*)     echo "usage: $0 [a1|micro|reserve-ip]" >&2; exit 1 ;;
esac

# --- already done? ---------------------------------------------------------
EXISTING=$(oci compute instance list -c "$C" --all \
	| jq -r --arg n "$NAME" '[.data[]|select(."display-name"==$n and .["lifecycle-state"]!="TERMINATED")][0].id // empty')
if [ -n "$EXISTING" ]; then
	say "Instance $NAME already exists"
	oci compute instance list-vnics --instance-id "$EXISTING" | jq -r '.data[0]["public-ip"]'
	exit 0
fi

AD=$(oci iam availability-domain list -c "$C" | jq -r '.data[0].name')

# --- network ---------------------------------------------------------------
VCN=$(oci network vcn list -c "$C" --all | jq -r --arg n "$NAME-vcn" '[.data[]|select(."display-name"==$n)][0].id // empty')
if [ -z "$VCN" ]; then
	say "Creating VCN"
	VCN=$(oci network vcn create -c "$C" --cidr-blocks '["10.0.0.0/16"]' \
		--display-name "$NAME-vcn" --dns-label autobazar --wait-for-state AVAILABLE \
		| jq -r '.data.id')
fi

IGW=$(oci network internet-gateway list -c "$C" --vcn-id "$VCN" --all | jq -r '.data[0].id // empty')
if [ -z "$IGW" ]; then
	say "Creating internet gateway"
	IGW=$(oci network internet-gateway create -c "$C" --vcn-id "$VCN" --is-enabled true \
		--display-name "$NAME-igw" --wait-for-state AVAILABLE | jq -r '.data.id')
fi

RT=$(oci network vcn get --vcn-id "$VCN" | jq -r '.data["default-route-table-id"]')
say "Routing 0.0.0.0/0 through the gateway"
oci network route-table update --rt-id "$RT" --force --route-rules \
	"[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IGW\"}]" >/dev/null

# The whole rule set is rewritten, so SSH and the ICMP path-MTU rule are
# restated here — an update replaces the list rather than adding to it.
SL=$(oci network vcn get --vcn-id "$VCN" | jq -r '.data["default-security-list-id"]')
say "Opening ports 22, 80, 443"
oci network security-list update --security-list-id "$SL" --force --ingress-security-rules '[
	{"protocol":"6","source":"0.0.0.0/0","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
	{"protocol":"6","source":"0.0.0.0/0","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},
	{"protocol":"6","source":"0.0.0.0/0","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":443,"max":443}}},
	{"protocol":"1","source":"0.0.0.0/0","isStateless":false,"icmpOptions":{"type":3,"code":4}}
]' >/dev/null

SUBNET=$(oci network subnet list -c "$C" --vcn-id "$VCN" --all \
	| jq -r '[.data[]|select(."prohibit-public-ip-on-vnic"==false)][0].id // empty')
if [ -z "$SUBNET" ]; then
	say "Creating public subnet"
	SUBNET=$(oci network subnet create -c "$C" --vcn-id "$VCN" --cidr-block 10.0.0.0/24 \
		--display-name "$NAME-public" --dns-label public \
		--prohibit-public-ip-on-vnic false --wait-for-state AVAILABLE | jq -r '.data.id')
fi

# --- image -----------------------------------------------------------------
IMAGE=$(oci compute image list -c "$C" --operating-system 'Canonical Ubuntu' \
	--operating-system-version '24.04' --shape "$SHAPE" \
	--sort-by TIMECREATED --sort-order DESC | jq -r '.data[0].id // empty')
[ -n "$IMAGE" ] || { echo "no Ubuntu 24.04 image for $SHAPE" >&2; exit 1; }

KEY=$(mktemp); printf '%s\n' "$PUBKEY" > "$KEY"

# --- launch, retrying past capacity errors ---------------------------------
say "Launching $SHAPE — retrying every 60s until capacity frees up"
ARGS=(-c "$C" --availability-domain "$AD" --shape "$SHAPE" --image-id "$IMAGE"
	--subnet-id "$SUBNET" --assign-public-ip true --display-name "$NAME"
	--ssh-authorized-keys-file "$KEY" --wait-for-state RUNNING)
[ -n "$SHAPE_CFG" ] && ARGS+=(--shape-config "$SHAPE_CFG")

n=0
while true; do
	n=$((n+1))
	if oci compute instance launch "${ARGS[@]}" >/tmp/launch.json 2>/tmp/launch.err; then
		ID=$(jq -r '.data.id' /tmp/launch.json)
		IP=$(oci compute instance list-vnics --instance-id "$ID" | jq -r '.data[0]["public-ip"]')
		say "Got one on attempt $n"
		echo "public IP: $IP"
		echo "ssh:       ssh -i ~/.ssh/oracle_autobazar ubuntu@$IP"
		break
	fi
	# Oracle words this several ways ("Out of host capacity.", "Out of capacity
	# for shape ..."), and returns it as a 500 InternalError, so match loosely.
	if ! grep -qi 'capacity' /tmp/launch.err /tmp/launch.json; then
		say "Launch failed for a reason other than capacity"
		cat /tmp/launch.err
		exit 1
	fi
	printf '\r%s  attempt %d — no capacity yet' "$(date +%H:%M:%S)" "$n"
	sleep 60
done
