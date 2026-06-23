#!/usr/bin/env bash
# Build/run helper for the Zig backend.
# Handles macOS (stops QuestDB to free RAM during compile) and WSL/Linux
# (routes Zig cache to /tmp to avoid Windows-filesystem permission errors).
#
# Usage: ./safe-build.sh        -> compile only
#        ./safe-build.sh run    -> compile, then run on port 8080
set -euo pipefail

MODE="${1:-build}"

# ── OS detection ────────────────────────────────────────────────────────────
IS_MACOS=0
IS_WSL=0
if [[ "$(uname)" == "Darwin" ]]; then
  IS_MACOS=1
elif grep -qi microsoft /proc/version 2>/dev/null; then
  IS_WSL=1
fi

free_mb() {
  if [[ "$IS_MACOS" == "1" ]]; then
    vm_stat | awk '/Pages free/{f=$3} /Pages speculative/{s=$3} END{gsub("\\.","",f); gsub("\\.","",s); print int((f+s)*4096/1048576)}'
  else
    free -m | awk '/^Mem/{print $4}'
  fi
}

# ── Build flags ──────────────────────────────────────────────────────────────
# On WSL the Zig cache must live on the Linux filesystem; atomic renames across
# the 9P /mnt/c mount fail with AccessDenied / EXDEV.
BUILD_FLAGS="-j1"
BIN_PATH="./zig-out/bin/backend"
if [[ "$IS_WSL" == "1" ]]; then
  BUILD_FLAGS="$BUILD_FLAGS --cache-dir /tmp/zig-cache --global-cache-dir /tmp/zig-global-cache --prefix /tmp/zig-out"
  BIN_PATH="/tmp/zig-out/bin/backend"
fi

# ── QuestDB handling (macOS only) ────────────────────────────────────────────
QDB_WAS_RUNNING=0
if [[ "$IS_MACOS" == "1" ]] && pgrep -f "io.questdb.ServerMain" >/dev/null; then
  QDB_WAS_RUNNING=1
  echo ">> Stopping QuestDB to free RAM for the build..."
  questdb stop >/dev/null 2>&1 || true
  sleep 2
fi

restart_qdb() {
  if [[ "$IS_MACOS" == "1" && "$QDB_WAS_RUNNING" == "1" ]] && ! pgrep -f "io.questdb.ServerMain" >/dev/null; then
    echo ">> Restarting QuestDB..."
    questdb start >/dev/null 2>&1 || true
  fi
}
trap restart_qdb EXIT

# ── Build ────────────────────────────────────────────────────────────────────
echo ">> Free RAM before build: $(free_mb) MB"
echo ">> Building..."
# shellcheck disable=SC2086
zig build $BUILD_FLAGS
echo ">> Build done. Free RAM: $(free_mb) MB"

if [[ "$MODE" != "run" ]]; then
  exit 0
fi

# ── Run ──────────────────────────────────────────────────────────────────────
restart_qdb

if [[ "$IS_MACOS" == "1" ]]; then
  echo ">> Waiting for QuestDB on :9000..."
  for _ in $(seq 1 30); do
    curl -sf 'http://localhost:9000/exec?query=SELECT%201' >/dev/null 2>&1 && break
    sleep 1
  done
fi

if lsof -ti :8080 >/dev/null 2>&1; then
  echo ">> Killing existing process on port 8080..."
  lsof -ti :8080 | xargs kill -9 2>/dev/null || true
fi

echo ">> Starting backend on :8080 (Ctrl-C to stop)..."
trap - EXIT

# In WSL, 127.0.0.1 is the WSL loopback, not the Windows host.
# Route QuestDB traffic to the Windows gateway IP instead.
if [[ "$IS_WSL" == "1" ]]; then
  WIN_HOST=$(ip route show default 2>/dev/null | awk '/default via/{print $3; exit}')
  if [[ -n "$WIN_HOST" ]]; then
    export QUESTDB_HOST="$WIN_HOST"
    echo ">> WSL: using Windows host $WIN_HOST for QuestDB"
  fi
fi

exec "$BIN_PATH"
