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
/home/jawirgaming66/zig/zig build $BUILD_FLAGS
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

# Reaching QuestDB (which runs on Windows) from WSL depends on the networking
# mode set in .wslconfig:
#   - mirrored mode: localhost reaches Windows services directly, and the
#     default gateway points at the *physical* LAN router (wrong target).
#   - NAT mode (default): localhost is the WSL loopback; Windows is the gateway.
# Auto-detect by probing :9000 on localhost first, then the gateway.
if [[ "$IS_WSL" == "1" ]]; then
  probe() { curl -sf -m 1 "http://$1:9000/exec?query=SELECT%201" >/dev/null 2>&1; }
  GW=$(ip route show default 2>/dev/null | awk '/default via/{print $3; exit}')
  if probe 127.0.0.1; then
    echo ">> WSL: QuestDB reachable on localhost (mirrored networking)"
    # leave QUESTDB_HOST unset → backend defaults to 127.0.0.1
  elif [[ -n "$GW" ]] && probe "$GW"; then
    export QUESTDB_HOST="$GW"
    echo ">> WSL: using Windows host $GW for QuestDB (NAT networking)"
  else
    echo ">> WSL: WARNING — QuestDB not reachable on 127.0.0.1 or gateway $GW."
    echo ">>       Check QuestDB is running and the Windows Firewall allows port 9000."
  fi
fi

exec "$BIN_PATH"
