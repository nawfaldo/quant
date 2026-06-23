#!/usr/bin/env bash
# Low-memory backend build/run for an 8GB Mac.
# Build happens with QuestDB stopped (frees RAM for the compile spike).
# For "run", QuestDB is brought back up BEFORE launching the server,
# because main.zig reads QuestDB at startup to build its caches.
# Usage: ./safe-build.sh        -> compile only
#        ./safe-build.sh run    -> compile, then run on port 8080
set -euo pipefail

MODE="${1:-build}"
free_mb() { vm_stat | awk '/Pages free/{f=$3} /Pages speculative/{s=$3} END{gsub("\\.","",f); gsub("\\.","",s); print int((f+s)*4096/1048576)}'; }

QDB_WAS_RUNNING=0
if pgrep -f "io.questdb.ServerMain" >/dev/null; then
  QDB_WAS_RUNNING=1
  echo ">> Stopping QuestDB to free RAM for the build..."
  questdb stop >/dev/null 2>&1 || true
  sleep 2
fi

restart_qdb() {
  if [ "$QDB_WAS_RUNNING" = "1" ] && ! pgrep -f "io.questdb.ServerMain" >/dev/null; then
    echo ">> Restarting QuestDB..."
    questdb start >/dev/null 2>&1 || true
  fi
}
# If anything fails, make sure QuestDB comes back.
trap restart_qdb EXIT

echo ">> Free RAM before build: $(free_mb) MB"
echo ">> Building (single-threaded, low memory)..."
zig build -j1            # compile only; no parallel jobs => capped peak memory
echo ">> Build done. Free RAM: $(free_mb) MB"

if [ "$MODE" != "run" ]; then
  exit 0
fi

# --- run path ---
# Bring QuestDB back BEFORE the server starts (startup caches read it).
restart_qdb
echo ">> Waiting for QuestDB on :9000..."
for _ in $(seq 1 30); do
  curl -sf 'http://localhost:9000/exec?query=SELECT%201' >/dev/null 2>&1 && break
  sleep 1
done

# Free port 8080 (required per CLAUDE.md).
if lsof -ti :8080 >/dev/null 2>&1; then
  echo ">> Killing existing process on port 8080..."
  lsof -ti :8080 | xargs kill -9 2>/dev/null || true
fi

# Launch the already-built binary directly (no rebuild => no second memory spike).
echo ">> Starting backend on :8080 (Ctrl-C to stop)..."
trap - EXIT   # leave QuestDB running after we exit
exec ./zig-out/bin/backend
