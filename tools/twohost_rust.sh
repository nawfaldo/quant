#!/bin/bash
# Run one live_trade Rust binary over a list of units on the PC AND the Mac,
# sharing the units the way `sandbox/research/twohost.py` does: a unit is
# claimed with an atomic `mkdir` in ~/quant/.twohost/<run>/claims on the Mac
# (the PC claims over ssh), so no unit runs twice and the faster host takes more.
# The Mac walks the list from the end, so the two meet in the middle.
#
#   bash tools/twohost_rust.sh <run id> <bin> <units file> [pc jobs] [mac jobs]
#
# <units file>: one unit per line, each line the binary's arguments, e.g.
#     2024-01-02 2024-03-31
#     2024-04-01 2024-06-30
# PC jobs 0 = the Mac does everything (use it while the PC is gaming); PC jobs
# run at idle priority. Before starting it pushes live_trade's source to the Mac
# (~/quant/live_trade), builds the binary there, and pushes every table listed
# in $TWOHOST_TABLES (space-separated names under data/parquet) that differs.
# Every unit's stdout lands in .twohost/<run>/results/<unit>.txt on the host
# that ran it; at the end the PC pulls the Mac's and prints them all in order.
# Reusing a run id resumes it (done units are skipped).
set -e
cd "$(dirname "$0")/.."
RUN=$1 BIN=$2 UNITS=$3 PJ=${4:-1} MJ=${5:-4}
[ -f "$UNITS" ] || { sed -n 2,20p "$0"; exit 1; }
MAC=${TWOHOST_MAC:-mac}
LOCAL=.twohost/$RUN
mkdir -p "$LOCAL/results"
cp "$UNITS" "$LOCAL/units.txt"

echo "twohost-rust $RUN: pushing source to the Mac"
tar cf - --exclude=target --exclude=fixtures --exclude='app.db*' live_trade/src live_trade/Cargo.toml live_trade/Cargo.lock \
    tools/twohost_rust_worker.py "$LOCAL/units.txt" | ssh "$MAC" 'cd ~/quant && tar xf -'
if [ -n "$TWOHOST_TABLES" ]; then
  ${PY:-py} tools/twohost_sync_tables.py $TWOHOST_TABLES
fi
echo "twohost-rust $RUN: building $BIN on the Mac"
ssh "$MAC" "cd ~/quant/live_trade && export PATH=\$HOME/.cargo/bin:\$PATH && cargo build --release --bin $BIN 2>&1 | tail -2"

ssh "$MAC" "cd ~/quant && (nohup caffeinate -is .venv/bin/python tools/twohost_rust_worker.py $RUN $BIN mac $MJ \
  > $LOCAL/mac.log 2>&1) > /dev/null 2>&1 < /dev/null &"
echo "twohost-rust $RUN: Mac started ($MJ jobs), PC $PJ jobs"
if [ "$PJ" -gt 0 ]; then
  ${PY:-py} tools/twohost_rust_worker.py "$RUN" "$BIN" pc "$PJ"
fi

# Collect: wait until every unit is done somewhere, pull the Mac's results.
while true; do
  done_count=$(ssh "$MAC" "find ~/quant/$LOCAL/claims -maxdepth 2 -name done 2>/dev/null | wc -l" | tr -d ' ')
  total=$(grep -c . "$LOCAL/units.txt")
  [ "$done_count" -ge "$total" ] && break
  echo "twohost-rust $RUN: $done_count/$total units done"
  sleep 60
done
ssh "$MAC" "cd ~/quant/$LOCAL/results && tar cf - ." | (cd "$LOCAL/results" && tar xf -)
while IFS= read -r line; do
  [ -z "$line" ] && continue
  id=$(${PY:-py} -c "import zlib,sys;print(f'{zlib.crc32(sys.argv[1].encode()):08x}')" "$line")
  echo "== $line"
  cat "$LOCAL/results/$id.txt" 2>/dev/null || echo "(missing)"
done < "$LOCAL/units.txt"
