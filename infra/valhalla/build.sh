#!/usr/bin/env bash
#
# Build Valhalla from source and install it system-wide.
#
# Contour uses Valhalla as its primary deterministic bicycle routing, elevation
# and map-matching engine (see docs/architecture.md). The upstream project
# publishes container images, but this build path exists for environments where
# no Docker daemon is available.
#
# Procedure follows the upstream instructions in
#   https://github.com/valhalla/valhalla/blob/master/docs/docs/start/building.md
# ("Building from Source - Linux"): install dependencies with the vendored
# scripts/install-linux-deps.sh, then configure with CMake and build.
#
# Usage:
#   sudo ./infra/valhalla/build.sh
#
# Environment:
#   VALHALLA_VERSION   git tag to build (default: 3.8.3)
#   VALHALLA_SRC_DIR   checkout location (default: /opt/contour/valhalla-src)
#   VALHALLA_JOBS      parallel compile jobs (default: nproc)

set -euo pipefail

VALHALLA_VERSION="${VALHALLA_VERSION:-3.8.3}"
VALHALLA_SRC_DIR="${VALHALLA_SRC_DIR:-/opt/contour/valhalla-src}"
VALHALLA_JOBS="${VALHALLA_JOBS:-$(nproc)}"

log() { printf '[valhalla-build] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "target version=${VALHALLA_VERSION} src=${VALHALLA_SRC_DIR} jobs=${VALHALLA_JOBS}"

# ---------------------------------------------------------------------------
# 1. Source
# ---------------------------------------------------------------------------
if [ -d "${VALHALLA_SRC_DIR}/.git" ]; then
  log "reusing existing checkout"
else
  log "cloning valhalla ${VALHALLA_VERSION}"
  mkdir -p "$(dirname "${VALHALLA_SRC_DIR}")"
  git clone --recurse-submodules --shallow-submodules --depth 1 \
    --branch "${VALHALLA_VERSION}" \
    https://github.com/valhalla/valhalla "${VALHALLA_SRC_DIR}"
fi

cd "${VALHALLA_SRC_DIR}"
log "HEAD=$(git rev-parse HEAD)"

# ---------------------------------------------------------------------------
# 2. Dependencies (upstream-provided script)
# ---------------------------------------------------------------------------
# Upstream's install-linux-deps.sh runs `apt-get update` under `set -o errexit`,
# so a single unreachable third-party repository aborts the whole build. On
# hosts behind a restrictive egress policy that is a common failure: repositories
# unrelated to Valhalla (language PPAs, vendor repos) answer 403 and take the
# build down with them. Disable only the sources that are actually unreachable,
# leaving a record of what was moved and where.
preflight_apt_sources() {
  local disabled_dir=/etc/apt/sources.list.d/disabled-by-contour
  local file uri reachable
  mkdir -p "${disabled_dir}"

  for file in /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    [ -e "${file}" ] || continue
    reachable=yes
    while read -r uri; do
      [ -n "${uri}" ] || continue
      if ! curl -fsS --max-time 20 -o /dev/null "${uri}" 2>/dev/null; then
        reachable=no
        log "apt source unreachable: $(basename "${file}") -> ${uri}"
        break
      fi
    done < <(grep -hoE '^(URIs:[[:space:]]*|deb[^ ]*[[:space:]]+)https?://[^ ]+' "${file}" 2>/dev/null |
      grep -oE 'https?://[^ ]+' | sort -u)

    if [ "${reachable}" = "no" ]; then
      log "disabling $(basename "${file}") (moved to ${disabled_dir})"
      mv "${file}" "${disabled_dir}/"
    fi
  done
}

log "checking apt source reachability"
preflight_apt_sources

log "installing linux dependencies via upstream scripts/install-linux-deps.sh"
export DEBIAN_FRONTEND=noninteractive
./scripts/install-linux-deps.sh

# ---------------------------------------------------------------------------
# 3. Configure
# ---------------------------------------------------------------------------
# Tests and benchmarks are disabled: Contour validates routing behaviour through
# its own integration suite (apps/api/tests/integration/test_valhalla_provider.py)
# and the upstream unit tests roughly double the compile time.
#
# Python bindings are disabled deliberately. Contour reaches Valhalla over HTTP
# through the RoutingProvider adapter, never in-process, so the bindings add no
# capability. They also make the build depend on the host's numpy being
# importable: nanobind's stub generator imports every module the extension
# references, and a broken or shadowed system numpy fails the whole build at the
# very last step. Turning them off removes that failure mode entirely.
log "configuring with cmake"
cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_TESTS=OFF \
  -DENABLE_BENCHMARKS=OFF \
  -DENABLE_SINGLE_FILES_WERROR=OFF \
  -DENABLE_PYTHON_BINDINGS=OFF \
  -DENABLE_SERVICES=ON \
  -DENABLE_DATA_TOOLS=ON \
  -DENABLE_HTTP=ON

# ---------------------------------------------------------------------------
# 4. Build and install
# ---------------------------------------------------------------------------
log "building with ${VALHALLA_JOBS} jobs (expect 45-90 minutes on 4 cores)"
make -C build -j"${VALHALLA_JOBS}"

log "installing"
make -C build install
ldconfig

log "installed binaries:"
command -v valhalla_service valhalla_build_tiles valhalla_build_config || true
valhalla_service --version 2>&1 | head -2 || true

log "done"
