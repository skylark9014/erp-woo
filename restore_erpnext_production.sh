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
mapfile -t BACKUP_SETS < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*-${SITE_NAME//./_}-site_config_backup.json" | sort)
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
  i=$((i + 1))
done
echo

if [ $i -eq 1 ]; then
  err "No valid backup sets found."
  exit 1
fi

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

# ────────── Maintaining database ──────────
log "✅ Compacting database..."
docker exec "$DB_CONTAINER" mysqlcheck -o -u root -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" >/dev/null 2>&1 || {
  err "Failed to compact database."
  exit 1
}
log "✅ Database compacted successfully."

# ────────── Ensure backend container is up for restore ──────────
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
  docker exec "$BACKEND_CONTAINER" sh -c "mkdir -p '$SITE_PATH/public/files' && tar -xf /tmp/files.tar --strip-components=4 -C '$SITE_PATH/public/files' && rm /tmp/files.tar"
  log "✅ Public files restored."
else
  log "⚠️  No public files tar found. Skipping."
fi

# Restore private files
if [ -f "$BACKUP_DIR/$SELECTED_PREFIX-private-files.tar" ]; then
  log "⬇️  Restoring private files..."
  docker cp "$BACKUP_DIR/$SELECTED_PREFIX-private-files.tar" "$BACKEND_CONTAINER":/tmp/private-files.tar
  docker exec "$BACKEND_CONTAINER" sh -c "mkdir -p '$SITE_PATH/private/files' && tar -xf /tmp/private-files.tar --strip-components=4 -C '$SITE_PATH/private/files' && rm /tmp/private-files.tar"
  log "✅ Private files restored."
else
  log "⚠️  No private files tar found. Skipping."
fi

# Fix file permissions
log "🚀 Setting file permissions..."
docker exec "$BACKEND_CONTAINER" chown -R frappe:frappe "$SITE_PATH"
log "✅ Permissions set."

# Verify extracted files
log "🚀 Verifying extracted files..."
docker exec "$BACKEND_CONTAINER" ls -l "$SITE_PATH/public/files" || log "⚠️  No public files found."
docker exec "$BACKEND_CONTAINER" ls -l "$SITE_PATH/private/files" || log "⚠️  No private files found."
log "✅ File verification complete."

# Verify primary image files
log "🚀 Verifying primary image files..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT name, image FROM tabItem WHERE image IS NOT NULL LIMIT 10;" | tail -n +2 | while IFS=$'\t' read -r name image; do
  if [[ "$image" == /files/* ]]; then
    file_path="$SITE_PATH/public/files/${image#/files/}"
  elif [[ "$image" == /private/files/* ]]; then
    file_path="$SITE_PATH/private/files/${image#/private/files/}"
  else
    log "⚠️  Item $name: Invalid image path $image"
    continue
  fi
  docker exec "$BACKEND_CONTAINER" [ -f "$file_path" ] && log "✅ Item $name: Found $file_path" || log "❌ Item $name: Missing $file_path"
done || {
  err "Failed to verify primary image files."
  exit 1
}
log "✅ Primary image verification complete."

# Check database file paths
log "🚀 Checking file paths in database..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT name, image FROM tabItem WHERE image IS NOT NULL LIMIT 10;"
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT name, file_url, attached_to_doctype, attached_to_name FROM tabFile WHERE attached_to_doctype='Item' LIMIT 10;"
log "✅ Database file paths checked."

# Fix database paths if needed
log "🚀 Fixing database file paths..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "UPDATE tabItem SET image = REGEXP_REPLACE(image, '^.*(public/files/.*)$', '/files/\\1') WHERE image LIKE '%public/files/%';"
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "UPDATE tabItem SET image = REGEXP_REPLACE(image, '^.*(private/files/.*)$', '/private/files/\\1') WHERE image LIKE '%private/files/%';"
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "UPDATE tabFile SET file_url = REGEXP_REPLACE(file_url, '^.*(public/files/.*)$', '/files/\\1') WHERE file_url LIKE '%public/files/%';"
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "UPDATE tabFile SET file_url = REGEXP_REPLACE(file_url, '^.*(private/files/.*)$', '/private/files/\\1') WHERE file_url LIKE '%private/files/%';"
log "✅ Database file paths fixed."

# Log duplicates before removal
log "🚀 Logging duplicate attachments for primary images on same Item..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT f.name, f.file_url, f.attached_to_name FROM tabFile f INNER JOIN tabItem i ON f.file_url = i.image AND f.attached_to_doctype='Item' AND f.attached_to_name = i.name LIMIT 10;"
log "✅ Duplicate attachments logged."

# Remove duplicate attachments for the same Item
log "🚀 Removing duplicate attachments for primary images on same Item..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "DELETE FROM tabFile WHERE attached_to_doctype='Item' AND EXISTS (SELECT 1 FROM tabItem WHERE name = tabFile.attached_to_name AND image = tabFile.file_url); SELECT ROW_COUNT() AS 'Rows Deleted';"
log "✅ Duplicate attachments removed."

# Re-check database file paths
log "🚀 Re-checking file paths in database after cleanup..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT name, image FROM tabItem WHERE image IS NOT NULL LIMIT 10;"
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT name, file_url, attached_to_doctype, attached_to_name FROM tabFile WHERE attached_to_doctype='Item' LIMIT 10;"
log "✅ Database file paths re-checked."

# Log potential overlaps for shared images
log "🚀 Checking for shared images across Items..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT t1.name AS item_name, t1.image, t2.name AS file_name, t2.attached_to_name AS attached_item FROM tabItem t1 LEFT JOIN tabFile t2 ON t1.image = t2.file_url AND t2.attached_to_doctype='Item' WHERE t1.image IS NOT NULL LIMIT 10;"
log "✅ Shared image check complete."

# Check for Items with missing primary images
log "🚀 Checking for Items with missing primary images..."
docker exec "$DB_CONTAINER" mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB_NAME" -e "SELECT name, image FROM tabItem WHERE image IS NOT NULL AND image NOT IN (SELECT file_url FROM tabFile WHERE attached_to_doctype='Item' AND attached_to_name=tabItem.name) LIMIT 10;"
log "✅ Missing primary image check complete."

# Clear cache and rebuild assets
log "🚀 Clearing ERPNext cache and rebuilding assets to ensure images load correctly..."
docker exec "$BACKEND_CONTAINER" bench --site "$SITE_NAME" clear-cache
docker exec "$BACKEND_CONTAINER" bench --site "$SITE_NAME" clear-website-cache
docker exec "$BACKEND_CONTAINER" bench --site "$SITE_NAME" build
log "⚠️  Please clear your browser cache or use incognito mode to ensure images display correctly."
log "✅ Cache cleared and assets rebuilt."

# ────────── 9. Restart full stack ──────────
log "🚀 STEP 9: Restarting ERPNext stack..."
docker compose -f "$COMPOSE_FILE" down
docker compose -f "$COMPOSE_FILE" up -d

# ────────── 10. Site Migration ──────────
log "🚀 STEP 10: Running bench migrate to ensure DB schema is up-to-date..."
docker exec "$BACKEND_CONTAINER" bench --site "$SITE_NAME" migrate
log "✅ bench migrate completed."

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