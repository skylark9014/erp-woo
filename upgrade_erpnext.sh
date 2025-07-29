#!/bin/bash
#Save it: nano upgrade_erpnext.sh
#Make it executable: chmod +x upgrade_erpnext.sh
#Run it: ./upgrade_erpnext.sh
#if ssh is reset, change it back with: sudo nano /etc/ssh/sshd_config


set -e

# ───────────────────────────────
#  ERPNext Upgrade Automation
# ───────────────────────────────

TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
BACKUP_DIR="/home/jannie/backups/erpnext-$TIMESTAMP"
COMPOSE_FILE="/home/jannie/frappe-compose.yml"
SITE_DOMAIN="records.techniclad.co.za"

# ────────── Logging helpers ──────────
log() {
  echo -e "[\e[32m$(date +'%H:%M:%S')\e[0m] $1"
}
err() {
  echo -e "[\e[31mERROR\e[0m] $1"
}

# ────────── Check prerequisites ──────────
check_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "❌ '$1' is not installed. Please install it first."
    exit 1
  fi
}

# ────────── Ensure jq ──────────
check_command jq

# ────────── Ensure yq (latest) ──────────
ensure_yq_latest() {
  log "🚀 Checking yq installation..."
  NEED_INSTALL=0

  if ! command -v yq >/dev/null 2>&1; then
    log "⚠️  yq not found. Will install latest."
    NEED_INSTALL=1
  else
    YQ_VERSION=$(yq --version | awk '{print $NF}' | sed 's/v//')
    MAJOR=$(echo "$YQ_VERSION" | cut -d. -f1)
    if [ "$MAJOR" -lt 4 ]; then
      log "⚠️  yq version <4 detected ($YQ_VERSION). Will upgrade."
      NEED_INSTALL=1
    else
      log "✅ yq version $YQ_VERSION is OK."
    fi
  fi

  if [ "$NEED_INSTALL" -eq 1 ]; then
    LATEST_URL=$(curl -s "https://api.github.com/repos/mikefarah/yq/releases/latest" | jq -r '.assets[] | select(.name|test("linux_amd64$")) | .browser_download_url')
    if [ -z "$LATEST_URL" ]; then
      err "❌ Could not find latest yq download URL."
      exit 1
    fi
    log "⬇️  Downloading yq from $LATEST_URL"
    sudo wget -q -O /usr/local/bin/yq "$LATEST_URL"
    sudo chmod +x /usr/local/bin/yq
    log "✅ yq installed/updated to latest."
  fi
}

ensure_yq_latest

log "🚀 STEP 0: Updating Ubuntu packages..."
sudo apt update && sudo apt upgrade -y

log "🚀 Pruning unused Docker images and containers..."
docker system prune -af

check_command docker

# ────────── 1. BACKUP STEP ──────────
log "🚀 STEP 1: Backing up to $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

log "✅ 1.1 Extracting MYSQL_ROOT_PASSWORD from $COMPOSE_FILE..."
MYSQL_ROOT_PASSWORD=$(yq e '.services.db.environment.MYSQL_ROOT_PASSWORD' "$COMPOSE_FILE")
if [ "$MYSQL_ROOT_PASSWORD" == "null" ] || [ -z "$MYSQL_ROOT_PASSWORD" ]; then
  err "❌ Could not extract MYSQL_ROOT_PASSWORD from $COMPOSE_FILE"
  exit 1
fi
log "✅ Found MYSQL_ROOT_PASSWORD."

log "✅ 1.2 Determining DB container..."
DB_CONTAINER=$(docker ps --format '{{.Image}} {{.Names}}' | grep -i 'mariadb' | awk '{print $2}' | head -n1)
if [ -z "$DB_CONTAINER" ]; then
  err "❌ No running MariaDB container found. Is your ERPNext stack up?"
  exit 1
fi
log "✅ Found DB container: $DB_CONTAINER"

log "✅ 1.3 Dumping MariaDB..."
docker exec "$DB_CONTAINER" sh -c "mysqldump -uroot -p$MYSQL_ROOT_PASSWORD --all-databases" > "$BACKUP_DIR/mariadb.sql"
log "✅ MariaDB dump saved to $BACKUP_DIR/mariadb.sql"

log "✅ 1.4 Determining Backend container..."
BACKEND_CONTAINER=$(docker ps --format '{{.Image}} {{.Names}}' | grep -i 'backend' | awk '{print $2}' | head -n1)
if [ -z "$BACKEND_CONTAINER" ]; then
  err "❌ No running backend container found. Is your ERPNext stack up?"
  exit 1
fi
log "✅ Found Backend container: $BACKEND_CONTAINER"

log "✅ 1.5 Backing up sites folder..."
docker exec "$BACKEND_CONTAINER" tar czf - -C /home/frappe/frappe-bench sites > "$BACKUP_DIR/sites.tar.gz"
log "✅ Sites folder backed up to $BACKUP_DIR/sites.tar.gz"


# ────────── 2. FIND & SELECT ERPNext TAG ──────────
log "🚀 STEP 2: Finding available stable ERPNext tags..."

fetch_all_tags() {
  PAGE=1
  while :; do
    RESPONSE=$(curl -s "https://registry.hub.docker.com/v2/repositories/frappe/erpnext/tags?page_size=100&page=$PAGE")
    TAGS=$(echo "$RESPONSE" | jq -r '.results[].name')

    if [ -z "$TAGS" ]; then
      break
    fi

    echo "$TAGS"
    ((PAGE++))
    if ! echo "$RESPONSE" | jq -e '.next' | grep -q 'null'; then
      continue
    else
      break
    fi
  done
}

ALL_TAGS=$(fetch_all_tags)
STABLE_TAGS=$(echo "$ALL_TAGS" | grep -vi '\-dev' | grep -vi '\-beta' | grep -vi '\-rc' | grep -E '^(v?[0-9]+\.[0-9]+\.[0-9]+)$' | sort -V | uniq)

if [ -z "$STABLE_TAGS" ]; then
  err "❌ Could not find any stable ERPNext tags."
  exit 1
fi

LAST_5_TAGS=$(echo "$STABLE_TAGS" | tail -n 5)

log "✅ Found the following latest 5 stable tags:"
echo
i=1
declare -a TAG_ARRAY
for tag in $LAST_5_TAGS; do
  echo "  $i) $tag"
  TAG_ARRAY[$i]=$tag
  i=$((i+1))
done
echo

read -p "👉 Choose a version [1-$((i-1))]: " CHOICE
if ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || [ "$CHOICE" -lt 1 ] || [ "$CHOICE" -ge "$i" ]; then
  err "❌ Invalid choice."
  exit 1
fi

SELECTED_TAG=${TAG_ARRAY[$CHOICE]}
log "✅ You selected: $SELECTED_TAG"

# ────────── 3. UPDATE COMPOSE FILE ──────────
log "🚀 STEP 3: Updating image tags in $COMPOSE_FILE..."

yq -i '(.services[] | select(.image | test("^frappe/erpnext:"))).image = "frappe/erpnext:'"$SELECTED_TAG"'"' "$COMPOSE_FILE"

log "✅ All frappe/erpnext images updated to $SELECTED_TAG in $COMPOSE_FILE."

# ────────── 4. PULL, RESTART, MIGRATE ──────────
log "🚀 STEP 4: Pulling selected ERPNext images..."
docker compose --project-name frappe -f "$COMPOSE_FILE" pull
log "✅ Images pulled."

log "🚀 STEP 5: Restarting ERPNext stack..."
docker compose --project-name frappe -f "$COMPOSE_FILE" down
docker compose --project-name frappe -f "$COMPOSE_FILE" up -d
log "✅ Stack restarted."

log "🚀 STEP 6: Running 'bench migrate'..."
FRAPPE_BACKEND_CONTAINER=$(docker compose --project-name frappe -f "$COMPOSE_FILE" ps --format '{{.Name}}' | grep 'backend' | head -n1)
if [ -z "$FRAPPE_BACKEND_CONTAINER" ]; then
  err "❌ No running frappe backend container found."
  exit 1
fi
docker exec "$FRAPPE_BACKEND_CONTAINER" bench --site all migrate
log "✅ Migration complete."

# ────────── 7. Updating .env file ──────────
log "🚀 STEP 7: Capturing actual running ERPNext and Frappe versions..."

# Get versions from container
VERSIONS_OUTPUT=$(docker exec "$FRAPPE_BACKEND_CONTAINER" bench --site all version)

# Extract with grep and awk
ERPNEXT_VERSION_PARSED=$(echo "$VERSIONS_OUTPUT" | grep -i '^erpnext' | awk '{print $2}')
FRAPPE_VERSION_PARSED=$(echo "$VERSIONS_OUTPUT" | grep -i '^frappe' | awk '{print $2}')

if [ -z "$ERPNEXT_VERSION_PARSED" ] || [ -z "$FRAPPE_VERSION_PARSED" ]; then
  err "❌ Could not parse ERPNext or Frappe versions from 'bench --site all version' output."
  echo "$VERSIONS_OUTPUT"
  exit 1
fi

log "✅ Detected ERPNext version: $ERPNEXT_VERSION_PARSED"
log "✅ Detected Frappe version: $FRAPPE_VERSION_PARSED"

# Write to .env file
ENV_FILE="$HOME/frappe_docker/.env"

echo "ERPNEXT_VERSION=$ERPNEXT_VERSION_PARSED" > "$ENV_FILE"
echo "FRAPPE_VERSION=$FRAPPE_VERSION_PARSED" >> "$ENV_FILE"

log "✅ Updated $ENV_FILE with:"
cat "$ENV_FILE"

# ────────── 8. Startup Verification (HTTP + HTTPS) ──────────
log "🚀 STEP 8: Checking ERPNext site availability..."

RETRIES=20
SLEEP_SECONDS=5
for scheme in http https; do
  for ((i=1;i<=RETRIES;i++)); do
    if curl --silent --fail --max-time 5 "$scheme://$SITE_DOMAIN" >/dev/null 2>&1; then
      log "✅ $scheme://$SITE_DOMAIN is responding!"
      break
    else
      log "⏳ Waiting for $scheme://$SITE_DOMAIN (attempt $i/$RETRIES)..."
      sleep $SLEEP_SECONDS
    fi
  done
done

log "🎉 Restore complete and ERPNext is up on both HTTP and HTTPS!"
log "🎉 Upgrade complete!"

