"""Put the live account on the 23-sleeve canon (the 2026-09-30 book). Idempotent.

    py -3 tools/go_live_canon_23.py            # switch if needed
    py -3 tools/go_live_canon_23.py --check    # exit 1 if not on it, change nothing

`start_live_trade.bat` runs this before anything starts, so the account can
never trade a different membership from the one the parity gate just proved.

WHAT "ON IT" MEANS. The live account's rows in `mt5_account_strategies` are
exactly the registry's (`exness_live_book`): the same strategy ids, each on the
broker symbol `required_symbol` gives it, every one active, and nothing else.
When that already holds this prints so and touches nothing -- so a restart
keeps `live_started_at`, which a re-activation would reset.

WHEN IT DOES NOT, the switch is the one `go_live_canon_22.py` made:

1. Backs up live_trade/app.db.
2. Runs `activate_canonical_live --apply` -- rows straight from the Rust
   registry, `live_started_at` reset. It REFUSES while anything is open or a
   command is unsettled, and then nothing after the backup is changed.

The 23 (`exness_combined_strategies.BOOK`): usdjpy volume_thrust, pullback,
half_life, rvol, qp_ma_cross@30m, lux_htf_manipulation@60m; audusd zscore;
ethusd confluence, volatility_breakout, obv_break, break_retest,
lux_body_momentum@15m; gbpjpy trap, luxalgo_manipulation@120m,
lux_supply_demand@30m; ukoil level_confluence; ustec lux_body_momentum@5m;
eurjpy qp_ma_cross@60m, lux_ny_vwap_pullback@15m; de40 lux_sweep_reclaim@60m;
uk100 xma_cross; usoil xma_cross; btc jump.
"""
import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "live_trade" / "app.db"
EXPECTED = 23


def registry():
    """`(book_id, {strategy: symbol})` from the built `exness_live_book`."""
    for name in ("exness_live_book.exe", "exness_live_book"):
        binary = ROOT / "live_trade" / "target" / "release" / name
        if binary.is_file():
            break
    else:
        sys.exit("no live_trade/target/release/exness_live_book -- build it first "
                 "(cargo build --release --bins)")
    done = subprocess.run([str(binary)], capture_output=True, text=True,
                          check=True, cwd=ROOT)
    payload = json.loads(done.stdout)
    return payload["book_id"], {row["strategy"]: row["symbol"]
                                for row in payload["strategies"]}


def live_rows():
    """`{strategy: (symbol, active)}` for the live account's rows.

    The live account is every account holding an active strategy -- the retired
    trial account keeps its rows, all inactive, and is not it.
    """
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    rows = db.execute(
        "SELECT s.strategy, s.symbol, s.active FROM mt5_account_strategies s "
        "WHERE s.account_id IN (SELECT account_id FROM mt5_account_strategies "
        "WHERE active=1)").fetchall()
    db.close()
    return {strategy: (symbol, bool(active)) for strategy, symbol, active in rows}


def differences(wanted, rows):
    out = []
    for strategy, symbol in sorted(wanted.items()):
        have = rows.get(strategy)
        if have is None:
            out.append(f"missing  {strategy} ({symbol})")
        elif have[0] != symbol or not have[1]:
            out.append(f"wrong    {strategy}: {have[0]} active={have[1]}, wants {symbol}")
    out.extend(f"extra    {strategy}" for strategy in sorted(set(rows) - set(wanted)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report only; exit 1 if the account is not on the canon")
    args = parser.parse_args()

    book_id, wanted = registry()
    if len(wanted) != EXPECTED:
        sys.exit(f"the registry holds {len(wanted)} sleeves, this script is for "
                 f"{EXPECTED} -- the book changed; write its own go-live script")
    diff = differences(wanted, live_rows())
    if not diff:
        print(f"live account already on {book_id}, {EXPECTED} sleeves -- nothing to do")
        return
    print(f"live account is NOT on the canon ({len(diff)} differences):")
    for line in diff:
        print(f"   {line}")
    if args.check:
        sys.exit(1)

    backup = DB.with_name(f"app.db.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(DB, backup)
    print(f"1. backup      {backup.name}")

    result = subprocess.run(
        [sys.executable, "-m", "sandbox.research.activate_canonical_live", "--apply"],
        cwd=ROOT)
    if result.returncode != 0:
        sys.exit("2. activation REFUSED -- nothing else was changed after the backup")
    print("2. activated")

    diff = differences(wanted, live_rows())
    if diff:
        for line in diff:
            print(f"   {line}")
        sys.exit("\nTHE ACCOUNT STILL DIFFERS FROM THE CANON -- do not start the stack")
    print(f"\nlive account on {book_id}: {EXPECTED} sleeves active")


if __name__ == "__main__":
    main()
