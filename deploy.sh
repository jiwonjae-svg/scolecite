#!/usr/bin/env bash
# =============================================================================
# Project Scolecite - Zero-Downtime Deployment Script
# =============================================================================
# Usage:
#   ./deploy.sh              # Full deploy (git pull + build + restart)
#   ./deploy.sh --no-pull    # Skip git pull (rebuild from local code)
#   ./deploy.sh --status     # Show service status only
#   ./deploy.sh --logs       # Tail live logs
#   ./deploy.sh --backup-db  # Backup PostgreSQL database
# =============================================================================

set -euo pipefail

# --- Configuration ---
PROJECT_DIR="/opt/scolecite"
COMPOSE_CMD="docker compose"
BRANCH="main"
LOG_FILE="/var/log/scolecite-deploy.log"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Helpers ---
log() { echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $*" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $*" | tee -a "$LOG_FILE"; }
err() { echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $*" | tee -a "$LOG_FILE" >&2; }

# --- Pre-flight checks ---
preflight() {
    if [ ! -d "$PROJECT_DIR" ]; then
        err "Project directory $PROJECT_DIR does not exist."
        exit 1
    fi
    cd "$PROJECT_DIR"

    if ! command -v docker &>/dev/null; then
        err "Docker is not installed."
        exit 1
    fi

    if ! docker info &>/dev/null; then
        err "Docker daemon is not running or current user lacks permissions."
        exit 1
    fi

    if [ ! -f ".env" ]; then
        err ".env file not found in $PROJECT_DIR"
        exit 1
    fi

    if [ ! -f "docker-compose.yml" ]; then
        err "docker-compose.yml not found in $PROJECT_DIR"
        exit 1
    fi
}

# --- Show status ---
show_status() {
    cd "$PROJECT_DIR"
    echo -e "${BLUE}=== Container Status ===${NC}"
    $COMPOSE_CMD ps
    echo ""
    echo -e "${BLUE}=== Health Checks ===${NC}"
    curl -sf http://localhost:8000/health 2>/dev/null && echo "" || warn "Server health check failed"
    curl -sf http://localhost:8000/ready 2>/dev/null && echo "" || warn "Server readiness check failed"
    echo ""
    echo -e "${BLUE}=== Resource Usage ===${NC}"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" 2>/dev/null || true
    echo ""
    echo -e "${BLUE}=== Disk Usage ===${NC}"
    df -h / | tail -1
    echo "Docker volumes:"
    docker system df 2>/dev/null || true
}

# --- Tail logs ---
tail_logs() {
    cd "$PROJECT_DIR"
    $COMPOSE_CMD logs -f --tail=100
}

# --- Backup PostgreSQL ---
backup_db() {
    cd "$PROJECT_DIR"
    local BACKUP_DIR="$PROJECT_DIR/backups"
    local TIMESTAMP
    TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
    local BACKUP_FILE="$BACKUP_DIR/scolecite_pg_${TIMESTAMP}.sql.gz"

    mkdir -p "$BACKUP_DIR"

    log "Backing up PostgreSQL database..."
    # Load POSTGRES_USER and POSTGRES_DB from .env (match docker-compose)
    set -a
    [ -f "$PROJECT_DIR/.env" ] && . "$PROJECT_DIR/.env"
    set +a
    local PG_USER="${POSTGRES_USER:-scolecite}"
    local PG_DB="${POSTGRES_DB:-scolecite}"
    $COMPOSE_CMD exec -T db pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$BACKUP_FILE"

    if [ -f "$BACKUP_FILE" ]; then
        local SIZE
        SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
        log "Backup complete: $BACKUP_FILE ($SIZE)"

        # Keep only the latest 14 backups
        ls -t "$BACKUP_DIR"/scolecite_pg_*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
        log "Old backups cleaned (keeping latest 14)"
    else
        err "Backup failed!"
        exit 1
    fi
}

# --- Main deploy ---
deploy() {
    local SKIP_PULL=false

    for arg in "$@"; do
        case $arg in
            --no-pull) SKIP_PULL=true ;;
        esac
    done

    preflight

    log "========================================="
    log "  Scolecite Deployment Starting"
    log "========================================="

    # Step 1: Backup database before deploy
    if $COMPOSE_CMD ps db --status running &>/dev/null 2>&1; then
        log "Step 1/5: Backing up database..."
        backup_db || warn "Database backup failed, continuing deploy..."
    else
        log "Step 1/5: No running database found, skipping backup."
    fi

    # Step 2: Git pull
    if [ "$SKIP_PULL" = false ]; then
        log "Step 2/5: Pulling latest code from $BRANCH..."
        git fetch origin "$BRANCH"
        local LOCAL_HASH REMOTE_HASH
        LOCAL_HASH=$(git rev-parse HEAD)
        REMOTE_HASH=$(git rev-parse "origin/$BRANCH")

        if [ "$LOCAL_HASH" = "$REMOTE_HASH" ]; then
            log "Already up to date (${LOCAL_HASH:0:8}). Rebuilding anyway..."
        else
            git pull origin "$BRANCH"
            log "Updated: ${LOCAL_HASH:0:8} → ${REMOTE_HASH:0:8}"
        fi
    else
        log "Step 2/5: Skipping git pull (--no-pull)"
    fi

    # Step 3: Build new images
    log "Step 3/5: Building Docker images..."
    $COMPOSE_CMD build --no-cache

    # Step 4: Rolling restart (zero-downtime)
    log "Step 4/5: Restarting containers..."
    $COMPOSE_CMD up -d --remove-orphans

    # Step 5: Health check
    log "Step 5/5: Waiting for health check..."
    local MAX_WAIT=60
    local WAITED=0
    while [ $WAITED -lt $MAX_WAIT ]; do
        if curl -sf http://localhost:8000/health &>/dev/null; then
            log "Health check passed!"
            break
        fi
        sleep 2
        WAITED=$((WAITED + 2))
        echo -n "."
    done
    echo ""

    if [ $WAITED -ge $MAX_WAIT ]; then
        err "Health check failed after ${MAX_WAIT}s!"
        warn "Rolling back..."
        $COMPOSE_CMD logs --tail=50 server
        exit 1
    fi

    # Cleanup old Docker images
    log "Cleaning up unused Docker images..."
    docker image prune -f &>/dev/null || true

    log "========================================="
    log "  Deployment Complete!"
    log "========================================="
    show_status
}

# --- Parse arguments ---
case "${1:-}" in
    --status)
        preflight
        show_status
        ;;
    --logs)
        preflight
        tail_logs
        ;;
    --backup-db)
        preflight
        backup_db
        ;;
    --help|-h)
        echo "Usage: $0 [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  (none)        Full deploy: git pull + build + restart"
        echo "  --no-pull     Skip git pull, rebuild from local code"
        echo "  --status      Show service status"
        echo "  --logs        Tail live container logs"
        echo "  --backup-db   Backup PostgreSQL database"
        echo "  --help        Show this help"
        ;;
    *)
        deploy "$@"
        ;;
esac
