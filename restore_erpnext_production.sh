#!/usr/bin/env bash

# ───────────────────────────────
# ERPNext Restore Automation (Production)
# Save as: nano restore_erpnext_production.sh
# Make executable: chmod +x restore_erpnext_production.sh
# Run with: ./restore_erpnext_production.sh
# ───────────────────────────────

set -euo pipefail

# ────────── Bash-only guard ──────────
if [ -z "${BASH_VERSION:-}" ]; then
  echo "❌ This script requires Bash. Please run it with Bash."
  exit 1
fi

# ────────── Config ──────────
BACKUP_DIR="/home/jannie/ERPNext_backups"
COMPOSE_FILE="/home/jannie/frappe-compose.yml"
SITE_NAME="records.techniclad.co.za"
SITE_PATH="/home/frappe/frappe-bench/sites/$SITE_NAME"

# ────────── Logging helpers ──────────
log() {
  echo -e "[\e[32m$(date +'%H:%M:%S')\e[0m] $1"
}

err() {
  echo -e "[\e[31m$(date +'%H:%M:%S') 🔴 ❌ $1\e[0m]"
}

# ────────── Check prerequisites ──────────
check_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "'$1' is not installed. Please install it first."
    exit 1
  fi
}

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
      err "Could not find latest yq download URL."
      exit 1
    fi
    log "⬇️  Downloading yq from $LATEST_URL"
    sudo wget -q -O /usr/local/bin/yq "$LATEST_URL"
    sudo chmod +x /usr/local/bin/yq
    log "✅ yq installed/updated to latest."
  fi
}

check_command jq
check_command docker
ensure_yq_latest

# ────────── 1. Choose Backup Set ──────────
log "🚀 STEP 1: Listing available backup sets..."

BACKUP_SETS=($(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*-${SITE_NAME//./_}-site_config_backup.json" | sort))
if [ ${#BACKUP_SETS[@]} -eq 0 ]; then
  err "No backup sets found in $BACKUP_DIR."
  exit 1
fi

i=1
declare -a PREFIXES
echo
for f in "${BACKUP_SETS[@]}"; do
  prefix=$(basename "$f" | sed 's/-site_config_backup\.json$//')
  echo "  $i) $prefix"
  PREFIXES[$i]="$prefix"
  i=$((i+1))
done
echo

read -p "👉 Choose a backup to restore [1-$((i-1))]: " CHOICE
if ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || [ "$CHOICE" -lt 1 ] || [ "$CHOICE" -ge "$i" ]; then
  err "Invalid choice."
  exit 1
fi

SELECTED_PREFIX="${PREFIXES[$CHOICE]}"
log "✅ Selected backup set: $SELECTED_PREFIX"

# ────────── 2. Extract DB password ──────────
log "🚀 STEP 2: Extracting MYSQL_ROOT_PASSWORD from $COMPOSE_FILE..."
MYSQL_ROOT_PASSWORD=$(yq e '.services.db.environment.MYSQL_ROOT_PASSWORD' "$COMPOSE_FILE")
if [ "$MYSQL_ROOT_PASSWORD" == "null" ] || [ -z "$MYSQL_ROOT_PASSWORD" ]; then
  err "Could not extract MYSQL_ROOT_PASSWORD from $COMPOSE_FILE"
  exit 1
fi
log "✅ MYSQL_ROOT_PASSWORD detected."

# ────────── 3. Stop ERPNext stack ──────────
log "🚀 STEP 3: Stopping ERPNext stack..."
docker compose -f "$COMPOSE_FILE" down || true

# ────────── 4. Start DB container only ──────────
log "🚀 STEP 4: Starting DB container for restore..."
docker compose -f "$COMPOSE_FILE" up -d db

log "✅ Waiting for DB to be ready..."
sleep 15

DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep 'db' | head -n1)
if [ -z "$DB_CONTAINER" ]; then
  err "Could not find running DB container!"
  exit 1
fi
log "✅ Using DB container: $DB_CONTAINER"

# ────────── 5. Get db_name from site_config_backup.json ──────────
log "🚀 STEP 5: Reading db_name from site_config_backup.json..."
DB_NAME=$(jq -r '.db_name' "$BACKUP_DIR/$SELECTED_PREFIX-site_config_backup.json")
if [ -z "$DB_NAME" ] || [ "$DB_NAME" == "null" ]; then
  err "Could not read db_name from site_config_backup.json!"
  exit 1
fi
log "✅ db_name: $DB_NAME"

# ────────── 6. Create database if needed ──────────
log "🚀 STEP 6: Creating database if missing..."
docker exec "$DB_CONTAINER" sh -c 'mysql -uroot -p"'"$MYSQL_ROOT_PASSWORD"'" -e "CREATE DATABASE IF NOT EXISTS \`'"$DB_NAME"'\`;"'
log "✅ Database ensured."

# ────────── 7. Restore MariaDB dump ──────────
log "🚀 STEP 7: Restoring MariaDB dump..."
gunzip -c "$BACKUP_DIR/$SELECTED_PREFIX-database.sql.gz" | docker exec -i "$DB_CONTAINER" sh -c 'mysql -uroot -p"'"$MYSQL_ROOT_PASSWORD"'" "'"$DB_NAME"'"'
log "✅ MariaDB restored."

# ────────── NEW: Ensure backend container is up for restore ──────────
log "✅ Starting backend service to copy site files..."
docker compose -f "$COMPOSE_FILE" up -d backend
sleep 5

# ────────── 8. Restore site files to backend ──────────
log "🚀 STEP 8: Restoring site files to backend container..."

BACKEND_CONTAINER=$(docker ps --format '{{.Names}}' | grep 'backend' | head -n1)
if [ -z "$BACKEND_CONTAINER" ]; then
  err "Could not find running backend container!"
  exit 1
fi
log "✅ Using backend container: $BACKEND_CONTAINER"

# Restore public files
if [ -f "$BACKUP_DIR/$SELECTED_PREFIX-files.tar" ]; then
  log "⬇️  Restoring public files..."
  docker cp "$BACKUP_DIR/$SELECTED_PREFIX-files.tar" "$BACKEND_CONTAINER":/tmp/files.tar
  docker exec "$BACKEND_CONTAINER" sh -c "mkdir -p '$SITE_PATH/public' && tar -xf /tmp/files.tar -C '$SITE_PATH/public'"
  log "✅ Public files restored."
else
  log "⚠️  No public files tar found. Skipping."
fi

# Restore private files
if [ -f "$BACKUP_DIR/$SELECTED_PREFIX-private-files.tar" ]; then
  log "⬇️  Restoring private files..."
  docker cp "$BACKUP_DIR/$SELECTED_PREFIX-private-files.tar" "$BACKEND_CONTAINER":/tmp/private-files.tar
  docker exec "$BACKEND_CONTAINER" sh -c "mkdir -p '$SITE_PATH/private' && tar -xf /tmp/private-files.tar -C '$SITE_PATH/private'"
  log "✅ Private files restored."
else
  log "⚠️  No private files tar found. Skipping."
fi

# ────────── 9. Restart full stack ──────────
log "🚀 STEP 9: Restarting ERPNext stack..."
docker compose -f "$COMPOSE_FILE" down
docker compose -f "$COMPOSE_FILE" up -d

# ────────── 10. Site Migration ──────────
log "🚀 STEP 10: Running bench migrate to ensure DB schema is up-to-date..."

read -p "👉 Run 'bench migrate' to upgrade database schema? [Y/n]: " RUN_MIGRATE
if [[ ! "$RUN_MIGRATE" =~ ^[Nn]$ ]]; then
  docker exec "$BACKEND_CONTAINER" bench --site "$SITE_NAME" migrate
  log "✅ bench migrate completed."
else
  log "⚠️  Skipped bench migrate by user choice."
fi

# ────────── 11. Startup Verification ──────────
log "🚀 STEP 11: Checking ERPNext site availability..."

RETRIES=20
SLEEP_SECONDS=5
SUCCESS=false

for scheme in http https; do
  for ((i=1; i<=RETRIES; i++)); do
    if curl --silent --fail --max-time 5 "$scheme://$SITE_NAME" >/dev/null 2>&1; then
      log "✅ $scheme://$SITE_NAME is responding!"
      SUCCESS=true
      break
    else
      log "⏳ Waiting for $scheme://$SITE_NAME (attempt $i/$RETRIES)..."
      sleep "$SLEEP_SECONDS"
    fi
  done
done

if [ "$SUCCESS" = true ]; then
  log "🎉 Restore complete and ERPNext is up!"
else
  err "ERPNext did not respond on HTTP or HTTPS after $((RETRIES * SLEEP_SECONDS)) seconds."
  exit 1
fi


