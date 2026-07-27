#!/usr/bin/env bash
#
# Start Contour's local dependencies: PostGIS, Redis and Valhalla.
#
# These run as native services rather than containers, using Debian's
# pg_ctlcluster and a system Redis. That makes this script specific to a
# Debian/Ubuntu host with root — it is what the reference container uses.
#
# On any other machine, prefer the containers:
#
#   docker compose -f infra/compose/docker-compose.yml up -d
#
# which describes the same three services and additionally builds the routing
# graph for you.
#
# Idempotent: already-running services are left alone.

set -euo pipefail

LOG_DIR="${CONTOUR_LOG_DIR:-/var/log/contour}"
VALHALLA_CONFIG="${CONTOUR_VALHALLA_CONFIG:-/opt/contour/valhalla.json}"
VALHALLA_WORKERS="${CONTOUR_VALHALLA_WORKERS:-1}"

mkdir -p "${LOG_DIR}"
log() { printf '[dev-up] %s\n' "$*"; }

# ---------------------------------------------------------------------------
# PostgreSQL + PostGIS
# ---------------------------------------------------------------------------
if pg_isready -q 2>/dev/null; then
  log "postgres already running"
else
  log "starting postgres"
  pg_ctlcluster 16 main start
fi

if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='contour'\"" | grep -q 1; then
  log "creating role and database"
  su postgres -c "psql -q -c \"CREATE ROLE contour LOGIN PASSWORD 'contour' CREATEDB\""
  su postgres -c "createdb -O contour contour"
  su postgres -c "psql -d contour -q -c 'CREATE EXTENSION IF NOT EXISTS postgis'"
fi

# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
if redis-cli ping >/dev/null 2>&1; then
  log "redis already running"
else
  log "starting redis"
  redis-server --daemonize yes --dir "${LOG_DIR}"
fi

# ---------------------------------------------------------------------------
# Valhalla
# ---------------------------------------------------------------------------
if curl -sf --max-time 5 http://127.0.0.1:8002/status >/dev/null 2>&1; then
  log "valhalla already running"
elif [ ! -f "${VALHALLA_CONFIG}" ]; then
  log "valhalla not configured; run infra/valhalla/make-config.sh (skipping)"
else
  log "starting valhalla"
  # setsid detaches the service from this script's process group, so it keeps
  # running after the shell that started it exits. Without it the service dies
  # with its parent and later health checks mysteriously report it missing.
  setsid nohup valhalla_service "${VALHALLA_CONFIG}" "${VALHALLA_WORKERS}" \
    >> "${LOG_DIR}/valhalla-service.log" 2>&1 < /dev/null &
  disown || true

  for _ in $(seq 1 20); do
    if curl -sf --max-time 2 http://127.0.0.1:8002/status >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
log "--- status ---"
printf '  postgres : %s\n' "$(pg_isready -q && echo up || echo down)"
printf '  redis    : %s\n' "$(redis-cli ping 2>/dev/null || echo down)"
if curl -sf --max-time 5 http://127.0.0.1:8002/status >/dev/null 2>&1; then
  printf '  valhalla : up (%s)\n' \
    "$(curl -s --max-time 5 http://127.0.0.1:8002/status | python3 -c 'import sys,json;print(json.load(sys.stdin).get("version","?"))')"
  tiles=$(find "${CONTOUR_VALHALLA_TILE_DIR:-/opt/contour/valhalla-tiles}" -name '*.gph' 2>/dev/null | wc -l)
  if [ "${tiles}" -eq 0 ]; then
    printf '  tiles    : none — routing is unavailable until infra/valhalla/build-tiles.sh runs\n'
  else
    printf '  tiles    : %s\n' "${tiles}"
  fi
else
  printf '  valhalla : down\n'
fi
