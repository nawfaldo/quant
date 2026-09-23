# Python MT5 bridge

The bridge connects the installed MetaTrader 5 terminal directly to the Rust
backend. No Expert Advisor, chart attachment, or WebRequest configuration is
needed. The backend still owns strategies, persisted commands, and trade data;
`bridge.py` only executes buy, sell, close, and flat commands and reports
account/position state.

## One-time setup

Create an MT5 account under an environment in the web app, then install the
official MetaTrader package:

```powershell
py -m pip install -r mt5\requirements.txt
```

See which accounts the bridge reads out of `live_trade/app.db`:

```powershell
py mt5\bridge.py --list
```

Verify those accounts and their terminals without polling or trading:

```powershell
py mt5\bridge.py --check
```

Keep the Rust backend running and start live execution from the repository root:

```powershell
py mt5\bridge.py
```

The bridge talks to the backend on `127.0.0.1:4000`, matching the Rust server's
own default. Both read `PORT`, so set it in the same shell as each or pass
`--backend` here. If the backend is not up you get `WinError 10061` on every
poll — that is a missing server or a port mismatch, never an MT5 problem.

The execution bridge and `data_manipulation/exness_stream_ohlcv.py` share a
Windows named mutex around MetaTrader5 extension calls. This permits one
account's execution and read-only candle feed to use the same terminal without
overlapping IPC requests (`-10001: IPC send failed`). Any additional local MT5
consumer must use `mt5/ipc_lock.py` as well or use a separate terminal.

That runs **every** environment account. To run just one:

```powershell
py mt5\bridge.py --account-id 2
```

If automatic terminal discovery selects the wrong MT5 installation:

```powershell
py mt5\bridge.py --account-id 2 `
  --terminal-path "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
```

Running is the default and the process can submit real orders; `--live` is still
accepted so older commands keep working, but it no longer does anything. The
account login, password, and server come from `live_trade/app.db`; they are not
printed.

If the backend uses `MT5_BRIDGE_TOKEN`, set the same value before starting:

```powershell
$env:MT5_BRIDGE_TOKEN="your-secret"
py mt5\bridge.py
```

## Multiple accounts

The official MetaTrader integration controls one terminal session per process,
and a terminal cannot stay logged into two accounts at once. With more than one
account, `bridge.py` forks a worker per account and supervises them — restarting
any worker that dies and prefixing its output with `[account N]`. Each account
past the first needs its own terminal installation:

```powershell
py mt5\bridge.py `
  --terminal 1="C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe" `
  --terminal 2="C:\Program Files\MetaTrader 5 IC\terminal64.exe"
```

Accounts left unmapped share the default terminal; the supervisor warns when
more than one would end up doing so, because they will fight over the login.
`MT5_BRIDGE_TOKEN` is handed to workers through the environment rather than the
command line, where any local process could read it.

## Live strategy safeguards

The Rust runtime supports the strategies listed by the March account UI,
including the seven-sleeve canonical portfolio.
Activating one in the account sidebar does not require a separate process: the
backend warms it from Bookmap history and the existing bridge executes its
orders.

- Every account/strategy pair has its own MT5 magic number and durable position
  key. Closes target the exact MT5 ticket, including partial closes.
- New entries are refused while the bridge is offline, equity is unavailable,
  the symbol is not NQ/USTEC/BTCUSD, state cannot be reconciled after restart,
  the requested size is below the broker minimum `0.01`, or the calibrated
  book's exposure signal has gone stale.
- There is no upper volume ceiling. `MT5_MAX_LIVE_VOLUME` used to impose one and
  was removed: every strategy sizes as a fraction of equity, so a fixed lot cap
  binds only once the account has grown, and when it binds it refuses *entries*
  while still allowing exits -- leaving a book that quietly stops opening and
  keeps closing. Position size belongs to the strategies and the broker.
- The three-sleeve book's exposure is driven by NQ's realised volatility. NQ
  does not print at weekends, so the signal is carried forward; if it goes more
  than four days without a print the feed is treated as dead and the book's
  sleeves stop taking new entries rather than sizing off a frozen multiplier.
  Exits are never held back.
- Every active market/source has an interval-aware stale-feed watchdog. It
  queues closes for only the positions driven by that failed source and retains
  the exit until MT5 acknowledges it.
- If the Rust backend is unreachable for 90 seconds, the Python bridge closes
  only March-owned positions and retains protective-close receipts until the
  backend returns. Configure this with `--backend-failsafe-seconds`; use `0`
  only when another broker-side protection layer owns this responsibility.

Readiness is available at `GET /api/march/live/status`. A strategy should only
be considered able to enter while this reports `phase: "ready"` and the MT5
account reports `status: "ready"`.

Strategy exits are managed by the Rust backend on completed source bars; they
are not broker-hosted stop-loss orders. The bridge protects a backend-only
outage, but a total outage of both backend and bridge can still leave an open
position unmanaged. Keep MT5's own account-level protections in place and
supervise the first live session.
