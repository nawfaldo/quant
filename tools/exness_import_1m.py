#!/usr/bin/env python3
"""Import a broker's MT5 one-minute bars into the store as `<broker>_<symbol>_1m`.

`--broker exness` (the default) lands `exness_<symbol>_1m`; `--broker
fundednext` lands `fundednext_<symbol>_1m` from the FundedNext terminal. The
table is named for the BROKER's symbol, so FundedNext's NDX100 is
`fundednext_ndx100_1m`. Everything below was written for Exness and holds for
both, except the clock -- see FUNDEDNEXT CLOCK.

FUNDEDNEXT CLOCK. FundedNext's MT5 epoch is SERVER time, not UTC: the server
sits at New York + 7h all year (UTC+3 in the US summer, UTC+2 in winter), so the
stored New York wall clock is the raw stamp minus seven hours, with no DST
arithmetic -- EXCEPT before 2024-07-08 12:37 server, when the server kept
Central European time: New York + 6, and + 5 in the weeks each spring and
autumn when the US and EU clocks disagree (`FUNDEDNEXT_OFFSET_EXCEPTIONS`).
Never calibrate against an `exness_*` table: Exness FX is itself an hour early
in every EU winter before 2024-04. Use Dukascopy FX, Databento indices, or a
clock-anchored event such as the 08:30 New York data spike.

WHY THIS EXISTS. Every canon sleeve signals off a vendor table (`gbpjpy_1m`
from Dukascopy, `ethusd_1m` from Binance, `nq_1m` from Databento) but the book
is executed at Exness. Those two price series are not the same series: a
different venue, a different aggregation, a different set of gaps. This
importer lands the broker's OWN bars beside the vendor's so a sleeve can be
signalled on one and filled on the other, and the difference measured rather
than assumed.

TIMEZONE. Bar tables in this repository hold NEW YORK WALL CLOCK relabelled as
UTC (see AGENT.md). The MetaTrader5 Python API returns a genuine UTC epoch --
verified against `gbpjpy_1m` by scanning the hourly offset that minimises the
mean absolute close difference, which bottoms sharply at +4h in August, exactly
New York's DST offset (0.005 there against 0.10 at either neighbour). So the
stored timestamp is the UTC instant read in New York and then relabelled UTC,
which is what `actual_to_stored_ns` does and what `exness_stream_ohlcv.py`
already writes.

MAX BARS, AND WHY THIS WALKS IN MONTHS. MetaTrader caps every history call at
the terminal's "Max bars in chart" setting. At the default 100,000 a YEAR of M1
is over the cap, and an over-cap request does not come back truncated -- it
returns "Invalid params" and NOTHING. Asked for a year at that setting, US500
returned 0 rows on 2026-09-20 while the same symbol served 29,974 bars for a
month-sized window.

That is why the request is a MONTH and never a year. A month of M1 is about
30,000 bars, which is inside the default cap, so this importer runs against a
terminal nobody has reconfigured -- and, on a machine where that terminal is
carrying live orders, without a restart to change a setting.

Raising `[Charts] MaxBars` to 2147483647 still helps: it is one request per
month either way, but the terminal caches more and the settle loop converges
sooner. It is no longer a prerequisite.

This importer reads market data only. It never sends, changes, or closes an
MT5 order.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import MetaTrader5 as _mt5

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mt5"))
from ipc_lock import synchronized_mt5  # noqa: E402

mt5 = synchronized_mt5(_mt5)

from parquet_writer import VENDOR_DIR, Sender, TimestampNanos, compact, table_span


UTC = timezone.utc
DEFAULT_TERMINAL = Path(r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")
FUNDEDNEXT_TERMINAL = Path(r"C:\Program Files\FundedNext MT5 Terminal\terminal64.exe")

#: Hours the FundedNext server clock runs ahead of New York wall clock.
FUNDEDNEXT_SERVER_AHEAD_OF_NY_HOURS = 7

#: FundedNext's currency pairs. Kept because the class matters for commission
#: in `cfd_families`; the clock no longer depends on it.
FUNDEDNEXT_FX = {"AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD",
                 "USDCAD", "USDJPY"}

#: `(first, end, hours, scope)` in SERVER time where the lead was not seven.
#: ONE SWITCH: every symbol was New York + 6 until 2024-07-08 12:37 server and
#: + 7 since. Before it the server kept Central European time, so in the US/EU
#: DST gap weeks of 2020-2022 the lead was five -- found on AUDUSD and AUDJPY,
#: the only FundedNext M1 that old, at day resolution against Dukascopy. The
#: 2022-10 and 2023 gap weeks read six. Later rows override earlier ones. That edge is a mid-week forward jump -- stamps go 12:37 then
#: 13:37 -- so both bars land on 06:37 New York and the writer keeps the later.
#:
#: Checked 2026-09-24 against clock-correct references only: Dukascopy FX
#: (day-level, 2020-2026, AUDUSD/AUDJPY/GBPJPY/USDJPY/EURUSD), Databento
#: `es_1m`/`nq_1m` for the indices, Exness indices and oils from 2022-07, and
#: the 17:00 New York daily break for the metals. An earlier version of this
#: table held six scoped windows because it was calibrated against `exness_*`
#: FX, which is itself an hour early in every EU winter before 2024-04
#: ([[exness-history-is-an-hour-early-in-eu-winter]]).
FUNDEDNEXT_OFFSET_EXCEPTIONS = (
    (datetime(2000, 1, 1, tzinfo=timezone.utc),
     datetime(2024, 7, 8, 13, tzinfo=timezone.utc), 6, "all"),
    (datetime(2020, 3, 7, tzinfo=timezone.utc),
     datetime(2020, 3, 28, tzinfo=timezone.utc), 5, "all"),
    (datetime(2020, 10, 24, tzinfo=timezone.utc),
     datetime(2020, 10, 31, tzinfo=timezone.utc), 5, "all"),
    (datetime(2021, 3, 13, tzinfo=timezone.utc),
     datetime(2021, 3, 27, tzinfo=timezone.utc), 5, "all"),
    (datetime(2021, 10, 30, tzinfo=timezone.utc),
     datetime(2021, 11, 6, tzinfo=timezone.utc), 5, "all"),
    (datetime(2022, 3, 12, tzinfo=timezone.utc),
     datetime(2022, 3, 26, tzinfo=timezone.utc), 5, "all"),
)

#: The eleven instruments the canon book trades, as Exness names them. The keys
#: are the repository's symbols, so `nq` -> `USTEC` matches `BROKER_ALIAS` in
#: `sandbox/research/cfd_families.py` and the table lands as
#: `exness_ustec_1m`.
CANON = {
    # RENAMED FROM `nq` 2026-09-03. The table this lands is `exness_ustec_1m`
    # either way -- it is named for the BROKER symbol -- but keying it `nq` was
    # wrong twice over: `nq` is now barred as a repository symbol
    # ([[nq-is-barred-ustec-is-the-replacement]]), and `nq_1m` is the
    # back-adjusted futures continuum, which is NOT the series this table holds.
    "ustec": "USTEC",
    "usdjpy": "USDJPY",
    "audusd": "AUDUSD",
    "jp225": "JP225",
    "ethusd": "ETHUSD",
    "gbpjpy": "GBPJPY",
    "gbpusd": "GBPUSD",
    "ukoil": "UKOIL",
    "eurjpy": "EURJPY",
    "xalusd": "XALUSD",
    "uk100": "UK100",
    # ADDED 2026-08-31, and the reason is worth recording: this map had drifted
    # from the book. `btc` and `xniusd` are canon sleeves that were missing
    # here, so a default run imported ten of the twelve symbols the book needs
    # and said nothing about the two it skipped. Checked against
    # `cfd_families.broker_symbol`, which is the authority.
    "btc": "BTCUSD",
    "xniusd": "XNIUSD",
    # The rest of the research pool, added 2026-08-31 so a candidate can be
    # screened on live fills rather than skipped for want of a table. `es` and
    # `de40` are the two whose broker name does NOT follow the repository name
    # -- US500 and DE30 -- which is exactly why this map is resolved through
    # `cfd_families.broker_symbol` and never guessed.
    "es": "US500",
    "de40": "DE30",
    "hk50": "HK50",
    "xauaud": "XAUAUD",
    "xaueur": "XAUEUR",
    "xaugbp": "XAUGBP",
    # ADDED 2026-09-03. `usoil` was the ONLY symbol in the research pool with no
    # broker table, so its sleeves were the only ones a live-execution study
    # could not correct: they kept the constant spread and the vendor bar open
    # while every other sleeve paid a measured per-minute spread and a late
    # fill. Two of them were candidates for canon at the time.
    "usoil": "USOIL",
    # ADDED 2026-09-22. ETHBTC is the one symbol in the widened search with no
    # broker table at all, so it was the only candidate a live-execution study
    # could not price. Its contract size is 100, not 1
    # ([[ethbtc-live-size-is-100x-the-contract-size]]).
    "ethbtc": "ETHBTC",
    # ADDED 2026-09-23 for the `fastbar` 5m sweep. Each has a 1m vendor table
    # and was refused only for want of a broker table to price its fills.
    "fr40": "FR40",
    "stoxx50": "STOXX50",
    "aus200": "AUS200",
    "eurusd": "EURUSD",
    "usdcad": "USDCAD",
    "audjpy": "AUDJPY",
}

#: The research symbols FundedNext quotes, as FundedNext names them. Probed
#: 2026-09-24 from `symbols_get` (67 symbols): no crypto, no XAL/XNI, no gold
#: crosses. The indices and oils do not follow the Exness names.
FUNDEDNEXT = {
    "ustec": "NDX100",
    "es": "SPX500",
    "de40": "GER30",
    "fr40": "FRA40",
    "stoxx50": "EUSTX50",
    "uk100": "UK100",
    "jp225": "JP225",
    "hk50": "HK50",
    "aus200": "AUS200",
    "ukoil": "UKOUSD",
    "usoil": "USOUSD",
    "usdjpy": "USDJPY",
    "audusd": "AUDUSD",
    "gbpjpy": "GBPJPY",
    "gbpusd": "GBPUSD",
    "eurjpy": "EURJPY",
    "eurusd": "EURUSD",
    "usdcad": "USDCAD",
    "audjpy": "AUDJPY",
    "xagusd": "XAGUSD",
    "xptusd": "XPTUSD",
    "xauusd": "XAUUSD",
    "audcad": "AUDCAD",
    "audchf": "AUDCHF",
    "audnzd": "AUDNZD",
    "audsgd": "AUDSGD",
    "cadchf": "CADCHF",
    "cadjpy": "CADJPY",
    "chfjpy": "CHFJPY",
    "euraud": "EURAUD",
    "eurcad": "EURCAD",
    "eurchf": "EURCHF",
    "eurgbp": "EURGBP",
    "eurhkd": "EURHKD",
    "eurhuf": "EURHUF",
    "eurnok": "EURNOK",
    "eurnzd": "EURNZD",
    "eursgd": "EURSGD",
    "gbpaud": "GBPAUD",
    "gbpcad": "GBPCAD",
    "gbpchf": "GBPCHF",
    "gbpnzd": "GBPNZD",
    "gbpsgd": "GBPSGD",
    "mxnjpy": "MXNJPY",
    "nokjpy": "NOKJPY",
    "nzdcad": "NZDCAD",
    "nzdchf": "NZDCHF",
    "nzdjpy": "NZDJPY",
    "nzdsgd": "NZDSGD",
    "nzdusd": "NZDUSD",
    "sgdjpy": "SGDJPY",
    "usdchf": "USDCHF",
    "usdcnh": "USDCNH",
    "usddkk": "USDDKK",
    "usdhkd": "USDHKD",
    "usdhuf": "USDHUF",
    "usdmxn": "USDMXN",
    "usdnok": "USDNOK",
    "usdsek": "USDSEK",
    "usdsgd": "USDSGD",
    "usdzar": "USDZAR",
    "zarjpy": "ZARJPY",
    "nth25": "NTH25",
    "swi20": "SWI20",
    "us2000": "US2000",
    "us30": "US30",
    "vixcfd": "VIX",
    "us500": "SPX500",
}

THE5ERS_TERMINAL = Path(r"C:\Program Files\Five Percent Online MetaTrader 5\terminal64.exe")

#: The research symbols The5ers (FivePercentOnline-Real) quotes, as it names
#: them. Probed 2026-11-07 from `symbols_get` (64 symbols) on High Stakes
#: 26823080: no NTH25 / SWI20 / US2000 / VIX, and its index names follow
#: neither Exness nor FundedNext (NAS100, SP500, DAX40, F40EUR, ESXEUR,
#: ASXAUD, HSIHKD). `jp225` is left out on purpose ([[no-jp225-anywhere]]).
THE5ERS = {
    "ustec": "NAS100", "us500": "SP500", "us30": "US30", "de40": "DAX40",
    "fr40": "F40EUR", "stoxx50": "ESXEUR", "uk100": "UK100",
    "aus200": "ASXAUD", "hk50": "HSIHKD",
    "ukoil": "XBRUSD", "usoil": "XTIUSD",
    "xauusd": "XAUUSD", "xagusd": "XAGUSD", "xptusd": "XPTUSD",
    "xpdusd": "XPDUSD", "xaueur": "XAUEUR",
    "audcad": "AUDCAD", "audchf": "AUDCHF", "audjpy": "AUDJPY",
    "audnzd": "AUDNZD", "audusd": "AUDUSD", "cadchf": "CADCHF",
    "cadjpy": "CADJPY", "chfjpy": "CHFJPY", "euraud": "EURAUD",
    "eurcad": "EURCAD", "eurchf": "EURCHF", "eurgbp": "EURGBP",
    "eurjpy": "EURJPY", "eurnzd": "EURNZD", "eurusd": "EURUSD",
    "gbpaud": "GBPAUD", "gbpcad": "GBPCAD", "gbpchf": "GBPCHF",
    "gbpjpy": "GBPJPY", "gbpnzd": "GBPNZD", "gbpusd": "GBPUSD",
    "nzdcad": "NZDCAD", "nzdchf": "NZDCHF", "nzdjpy": "NZDJPY",
    "nzdusd": "NZDUSD", "usdcad": "USDCAD", "usdchf": "USDCHF",
    "usdjpy": "USDJPY",
    #: Crypto, all `trade_mode` FULL (2026-11-07), quoted 7 days a week.
    "btc": "BTCUSD", "ethusd": "ETHUSD", "ltcusd": "LTCUSD", "solusd": "SOLUSD",
    "xrpusd": "XRPUSD", "dogeusd": "DOGUSD", "btceur": "BTCEUR", "etheur": "ETHEUR",
}

#: The5ers' server clock: New York + 7 (UTC+2 winter / UTC+3 summer), read
#: 2026-11-07 off its own M1: FX quotes 00:05-23:54 server = 17:05-16:54 NY,
#: metals / indices / oil 01:05-23:49 = 18:05-16:49 NY, the 17:00 NY break.
#: `(first, end, hours, scope)` rows where the lead was NOT seven, in server
#: time, like `FUNDEDNEXT_OFFSET_EXCEPTIONS`. CALIBRATED 2026-11-07 on
#: EURUSD 2020-01..2026-11 against Dukascopy `eurusd_1m` (15m closes, best
#: hour shift per ISO week): + 0 every week EXCEPT the US/EU daylight-saving
#: gap weeks of 2020-2023, where the server kept EU time and sat at NY + 6
#: (best shift +1h, mean |diff| 0.1-0.7 bp against 4-19 bp at 0). From 2024
#: on every gap week reads 0 -- the server follows the US clock.
#: SCOPED, re-checked per ISO week on SP500 / DAX40 / BTCUSD against
#: Dukascopy us500 / de40 and Binance btc: through the 2022 MARCH gap only FX
#: moved to NY + 6 -- indices and crypto stayed at NY + 7 (a blanket "all"
#: row put them an hour off in 2020-W11..13, 2020-W44, 2021-W11, 2021-W44,
#: 2022-W11). From the 2022 OCTOBER gap on, every symbol moved together.
_T5_GAPS = ((2020, 3, 8, 3, 29, "fx"), (2020, 10, 25, 11, 1, "fx"),
            (2021, 3, 14, 3, 28, "fx"), (2021, 10, 31, 11, 7, "fx"),
            (2022, 3, 13, 3, 27, "fx"), (2022, 10, 30, 11, 6, "all"),
            (2023, 3, 12, 3, 26, "all"), (2023, 10, 29, 11, 5, "all"))
THE5ERS_OFFSET_EXCEPTIONS = tuple(
    (datetime(y, m1, d1, tzinfo=timezone.utc), datetime(y, m2, d2, tzinfo=timezone.utc), 6, scope)
    for y, m1, d1, m2, d2, scope in _T5_GAPS)

#: Non-FX names among The5ers' six-letter symbols (metals, crypto, oil).
_T5_NOT_FX = {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "XAUEUR", "XAGEUR", "CUCUSD",
              "BTCUSD", "ETHUSD", "LTCUSD", "SOLUSD", "XRPUSD", "DOGUSD", "BTCEUR",
              "ETHEUR", "XBRUSD", "XTIUSD", "NGCUSD"}


def is_fx_symbol(broker, clock):
    """Whether the server clock's `fx`-scoped rows apply to `broker`. FundedNext
    keeps its original 8-pair set (its rows are all `all` anyway); on The5ers
    every six-letter currency pair is FX."""
    if clock == "the5ers_ny_plus_7":
        return len(broker) == 6 and broker.isalpha() and broker.upper() not in _T5_NOT_FX
    return broker in FUNDEDNEXT_FX

#: Broker symbols whose feed is stamped at a FIXED UTC offset, not the
#: server's NY + 7. SOLUSD, found 2026-11-07: against Binance SOLUSDT (checked
#: raw: 2025-01-15 19:30 UTC open 201.03 = `solusd_1m` 14:30 NY, but
#: The5ers' 15:30 bar) it is right in every US-summer month and one hour late
#: in every US-winter month 2024-2026 -- UTC + 3 all year. BTCUSD and ETHUSD
#: follow the server clock. Checked per symbol by the search driver's clock
#: gate before a symbol is used.
#: Mapped per ISO week 2020-2026 (2026-11-07, broker vs Dukascopy / Binance,
#: best 15m-close shift): F40EUR, ESXEUR, ASXAUD and BTCEUR are -1h in EVERY
#: US-winter stretch and right every summer -- the same fixed UTC + 3 as
#: SOLUSD. ETHEUR has BTCEUR's shape. Value: `[(start, until, hours), ...]`
#: -- a server stamp in `[start, until)` (None = open) reads as UTC + `hours`;
#: outside every range the symbol is on the server clock.
#:
#: EVERY fixed feed is back on the server clock in 2026-W45, the first week
#: after the 2026-11-01 US switch (+1h under a fixed rule there, on all
#: eleven), so The5ers normalised them then: the ranges end 2026-11-01 and
#: live trading today sees the server clock. NAS100 was UTC + 3 only from
#: 2020-W49 to the 2022 autumn switch (server clock before and after);
#: XBRUSD to the 2023 autumn switch; XPTUSD's vendor table starts 2021-W44.
_T5_NORMALISED = datetime(2026, 11, 1, tzinfo=timezone.utc)
THE5ERS_FIXED_UTC_HOURS = {
    **{name: [(None, _T5_NORMALISED, 3)] for name in (
        "SOLUSD", "BTCEUR", "ETHEUR", "F40EUR", "ESXEUR", "ASXAUD", "HSIHKD",
        # every crypto but BTCUSD / ETHUSD, and platinum (mapped 2026-11-07)
        "LTCUSD", "XRPUSD", "DOGUSD", "XPTUSD")},
    "NAS100": [(datetime(2020, 11, 30, tzinfo=timezone.utc),
                datetime(2022, 10, 30, tzinfo=timezone.utc), 3)],
    "XBRUSD": [(None, datetime(2023, 10, 29, tzinfo=timezone.utc), 3)],
}

#: Per-symbol server-clock rows, applied AFTER the general ones (the last
#: matching row wins). UK100 moved to NY + 6 in the 2020-10, 2021-03 and
#: 2021-10 gaps (where indices did not) and stayed at NY + 7 in the 2022-10
#: gap (where everything else moved).
THE5ERS_SYMBOL_EXCEPTIONS = (
    (datetime(2020, 10, 25, tzinfo=timezone.utc), datetime(2020, 11, 1, tzinfo=timezone.utc), 6, "UK100"),
    (datetime(2021, 3, 14, tzinfo=timezone.utc), datetime(2021, 3, 28, tzinfo=timezone.utc), 6, "UK100"),
    (datetime(2021, 10, 31, tzinfo=timezone.utc), datetime(2021, 11, 7, tzinfo=timezone.utc), 6, "UK100"),
    (datetime(2022, 10, 30, tzinfo=timezone.utc), datetime(2022, 11, 6, tzinfo=timezone.utc), 7, "UK100"),
    # XAGUSD moved with FX (NY + 6) in the 2020-2022 gaps, as UK100 did
    *((datetime(y, m1, d1, tzinfo=timezone.utc), datetime(y, m2, d2, tzinfo=timezone.utc), 6, "XAGUSD")
      for y, m1, d1, m2, d2, scope in _T5_GAPS if scope == "fx"),
    # XTIUSD stayed at NY + 7 in the 2023 March gap
    (datetime(2023, 3, 12, tzinfo=timezone.utc), datetime(2023, 3, 26, tzinfo=timezone.utc), 7, "XTIUSD"),
)


def fixed_hours_at(rules, server_seconds):
    """The fixed UTC offset in force at `server_seconds`, or None."""
    for start, until, hours in rules or ():
        if ((start is None or server_seconds >= start.timestamp())
                and (until is None or server_seconds < until.timestamp())):
            return hours
    return None

#: Everything that differs between brokers. `clock` says what the terminal's
#: epoch is: `utc` is a true UTC instant (Exness), `ny_plus_7` is server time
#: seven hours ahead of New York (FundedNext), `the5ers_ny_plus_7` the same
#: with The5ers' own exception table. Each broker gets its own IPC mutex: the
#: lock exists for processes sharing ONE terminal, and a prop-firm import must
#: never queue behind another terminal's bridge.
BROKERS = {
    "exness": {"terminal": DEFAULT_TERMINAL, "symbols": CANON,
               "prefix": "exness_", "clock": "utc", "mutex": None},
    "fundednext": {"terminal": FUNDEDNEXT_TERMINAL, "symbols": FUNDEDNEXT,
                   "prefix": "fundednext_", "clock": "ny_plus_7",
                   "mutex": r"Local\QuantMetaTrader5IPC_fundednext"},
    "the5ers": {"terminal": THE5ERS_TERMINAL, "symbols": THE5ERS,
                "prefix": "the5ers_", "clock": "the5ers_ny_plus_7",
                "mutex": r"Local\QuantMetaTrader5IPC_the5ers"},
}

#: Clock name -> the server-offset exception table it applies (general rows,
#: then symbol-scoped rows, so a symbol row overrides).
SERVER_CLOCKS = {"ny_plus_7": FUNDEDNEXT_OFFSET_EXCEPTIONS,
                 "the5ers_ny_plus_7": THE5ERS_OFFSET_EXCEPTIONS + THE5ERS_SYMBOL_EXCEPTIONS}

#: A year chunk is requested until two consecutive calls agree, because the
#: first call only ASKS the server for that history and returns whatever is
#: cached at the moment it is asked. Without the settle loop a deep year
#: silently imports a fraction of itself and looks like a thin market.
SETTLE_ATTEMPTS = 12
SETTLE_SECONDS = 4.0

#: How many empty years in a row end the walk backwards. One is not enough: a
#: CFD can be quoted, dropped for a year, and quoted again.
EMPTY_YEAR_LIMIT = 2

#: How far back the walk goes by default. Exness serves FX M1 back to 1999 and
#: the walk will happily take all of it, but the canon comparison only ever
#: reads the study window, so the deep years are download time spent on rows
#: nothing queries. Pass `--first-year` to go further back.
EARLIEST_YEAR = 2020


def log(message):
    print(f"[{datetime.now(tz=UTC):%H:%M:%S}] {message}", flush=True)


def safe_table(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe table name: {value!r}")
    return value


def actual_to_stored_ns(actual_seconds, wall_clock):
    """Real UTC instant -> New York wall clock reinterpreted as UTC."""
    local = datetime.fromtimestamp(actual_seconds, tz=UTC).astimezone(wall_clock)
    return int(local.replace(tzinfo=UTC).timestamp()) * 1_000_000_000


def _row_applies(scope, fx, symbol):
    """`all`; `fx` (FX pairs only); `nonfx`; or one broker symbol's name."""
    if scope == "all":
        return True
    if scope == "fx":
        return fx
    if scope == "nonfx":
        return not fx
    return symbol is not None and scope == symbol.upper()


def server_to_stored_ns(server_seconds, fx=True, exceptions=FUNDEDNEXT_OFFSET_EXCEPTIONS,
                        symbol=None):
    """Prop-firm server stamp (NY + 7 unless `exceptions` say otherwise) ->
    New York wall clock reinterpreted as UTC. FundedNext's table by default.
    The last matching row wins, so symbol rows listed last override."""
    hours = FUNDEDNEXT_SERVER_AHEAD_OF_NY_HOURS
    for first, end, lead, scope in exceptions:
        if not _row_applies(scope, fx, symbol):
            continue
        if first.timestamp() <= server_seconds < end.timestamp():
            hours = lead
    return (server_seconds - hours * 3600) * 1_000_000_000


def to_stored_ns(seconds, wall_clock, clock, fx=True, fixed_rules=None, symbol=None):
    fixed = fixed_hours_at(fixed_rules, seconds)
    if fixed is not None:      # a feed on a fixed UTC offset (`THE5ERS_FIXED_UTC_HOURS`)
        return actual_to_stored_ns(seconds - fixed * 3600, wall_clock)
    if clock in SERVER_CLOCKS:
        return server_to_stored_ns(seconds, fx, SERVER_CLOCKS[clock], symbol)
    return actual_to_stored_ns(seconds, wall_clock)


def to_actual_seconds(seconds, clock, fx=True, fixed_rules=None, symbol=None):
    """A terminal stamp as a true UTC instant, for comparing with the cutoff."""
    fixed = fixed_hours_at(fixed_rules, seconds)
    if fixed is not None:
        return seconds - fixed * 3600
    if clock not in SERVER_CLOCKS:
        return seconds
    stored = datetime.fromtimestamp(
        server_to_stored_ns(seconds, fx, SERVER_CLOCKS[clock], symbol) // 10**9, tz=UTC)
    return int(stored.replace(tzinfo=ZoneInfo("America/New_York")).timestamp())


def ensure_table(table):
    """Nothing to create. A shard appears when the first row is written.

    KEPT AS A NO-OP because what it used to declare still holds: a re-run over
    the same year must not double every bar. QuestDB needed an explicit
    `DEDUP UPSERT KEYS(timestamp)` for that, since ILP has no upsert without
    one; the Parquet writer replaces a row at a timestamp it already holds, so
    the guarantee is now a property of the writer rather than of the schema.
    """


def month_windows(start, stop):
    """`[start, stop)` cut into calendar months, oldest first.

    THE UNIT THE TERMINAL WILL ACTUALLY SERVE. See the MAX BARS note: a year of
    M1 is over the default cap and comes back as nothing at all, so the window
    has to be small enough to answer. A calendar month rather than a fixed 28
    days so the chunk boundaries are stable across re-runs and a re-import asks
    for the same windows it asked for last time.
    """
    edge = datetime(start.year, start.month, 1, tzinfo=UTC)
    while edge < stop:
        following = (datetime(edge.year + 1, 1, 1, tzinfo=UTC)
                     if edge.month == 12
                     else datetime(edge.year, edge.month + 1, 1, tzinfo=UTC))
        lo, hi = max(edge, start), min(following, stop)
        if lo < hi:
            yield lo, hi
        edge = following


def settled_rates(symbol, start, stop):
    """M1 bars in `[start, stop)`, waited on until the terminal stops growing them.

    THE RANGE IS RE-CHECKED IN PYTHON BECAUSE MT5 DOES NOT HONOUR IT. Asked for
    a year the server has no history for, `copy_rates_range` does not return an
    empty array -- it returns ONE bar, the same stale bar every time, dated
    outside the window entirely (asking for 2016-06-01..2016-06-08 returned a
    single 2026-05-11 bar). Left alone that is two bugs: the walk backwards
    never sees an empty year so it grinds through to 1999, and a fabricated row
    is written over a real one, since DEDUP upserts on the timestamp. So every
    row is filtered against the window that was actually asked for.
    """
    lo, hi = int(start.timestamp()), int(stop.timestamp())
    previous = -1
    rows = []
    answered = False
    for _ in range(SETTLE_ATTEMPTS):
        fetched = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start, stop)
        # `None` IS AN ERROR AND WAS READ AS AN EMPTY MARKET. A refused call and
        # a year the broker never quoted both used to arrive here as zero rows,
        # so an over-cap request tripped `EMPTY_YEAR_LIMIT` and the run reported
        # "no bars" and exited 0 over a table it had not touched. Invalid params
        # is the cap (see MAX BARS) and it is raised rather than absorbed;
        # anything else is logged and left to the settle loop.
        if fetched is None:
            code, text = mt5.last_error()
            if code == -2:
                raise RuntimeError(
                    f"{symbol}: the terminal refused {start:%Y-%m-%d}.."
                    f"{stop:%Y-%m-%d} as too large ({text}). That window is "
                    f"over `[Charts] MaxBars`; this importer asks a month at a "
                    f"time, so a refusal here means the cap is set below one "
                    f"month of M1.")
            log(f"  {symbol} {start:%Y-%m}: {text} -- retrying")
            time.sleep(SETTLE_SECONDS)
            continue
        answered = True
        rows = [row for row in fetched if lo <= int(row["time"]) < hi]
        if len(rows) == previous:
            return rows
        previous = len(rows)
        time.sleep(SETTLE_SECONDS)
    # NEVER A SILENT HOLE. If the terminal did not answer once (IPC lost, the
    # server refusing logins -- 2026-11-07 15:24, "Service is not available"),
    # this month is unknown, not empty: stop the import rather than store a
    # gap that reads as a quiet market.
    if not answered:
        raise RuntimeError(f"{symbol} {start:%Y-%m}: the terminal never answered "
                           f"({SETTLE_ATTEMPTS} tries) -- import stopped, no hole written")
    return rows


class Writer:
    """One reconnecting QuestDB ILP connection, shared by every symbol."""

    def __init__(self, wall_clock, clock="utc"):
        self.wall_clock = wall_clock
        self.clock = clock
        self.sender = None
        self.written = 0
        #: set per symbol by `import_symbol`: its fixed-offset rules
        #: (`THE5ERS_FIXED_UTC_HOURS`) and its broker name (symbol-scoped rows)
        self.fixed_rules = None
        self.symbol = None

    def connect(self):
        if self.sender is None:
            self.sender = Sender()
            self.sender.establish()
        return self.sender

    def write(self, table, rows, cutoff_seconds, fx=True):
        sender = self.connect()
        written = 0
        try:
            for row in rows:
                seconds = int(row["time"])
                if to_actual_seconds(seconds, self.clock, fx, self.fixed_rules,
                                     self.symbol) >= cutoff_seconds:
                    continue
                sender.row(
                    table,
                    symbols=None,
                    columns={
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["tick_volume"]),
                        "spread": int(row["spread"]),
                        "real_volume": int(row["real_volume"]),
                    },
                    at=TimestampNanos(to_stored_ns(seconds, self.wall_clock,
                                                   self.clock, fx, self.fixed_rules,
                                                   self.symbol)),
                )
                written += 1
            sender.flush()
        except Exception:
            self.close()
            raise
        self.written += written
        return written

    def close(self):
        if self.sender is None:
            return
        try:
            self.sender.flush()
            self.sender.close()
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass
        self.sender = None


def settled_span(table):
    """`(min, max, count)` for `table`.

    NO LONGER POLLED, and the reason it was is worth keeping. A QuestDB
    `count()` taken the instant after a flush UNDERSTATED the table -- GBPJPY
    reported 2,407,400 rows against 2,480,404 written, and the two agreed
    exactly a minute later ([[questdb-update-read-lag]]) -- so this re-read
    until the count stopped moving. A Parquet shard is renamed into place
    complete, so the first read is already the settled one.
    """
    return table_span(table) or (None, None, 0)


def import_symbol(key, broker, writer, cutoff, first_year, table_prefix, start=None):
    table = safe_table(f"{table_prefix}{broker.lower()}_1m")
    writer.fixed_rules = (THE5ERS_FIXED_UTC_HOURS.get(broker)
                          if writer.clock == "the5ers_ny_plus_7" else None)
    writer.symbol = broker
    if not mt5.symbol_select(broker, True):
        raise RuntimeError(f"cannot select {broker}: {mt5.last_error()}")
    ensure_table(table)

    cutoff_seconds = int(cutoff.timestamp())
    total, empty_run = 0, 0
    for year in range(cutoff.year, first_year - 1, -1):
        year_start = datetime(year, 1, 1, tzinfo=UTC)
        if start is not None:
            year_start = max(year_start, start)
        stop = min(datetime(year + 1, 1, 1, tzinfo=UTC), cutoff)
        if stop <= year_start:
            continue
        # ONE MONTH PER REQUEST, WRITTEN AS IT ARRIVES. The year is still the
        # unit the walk backwards counts in -- a year with no month in it is
        # what ends it -- but nothing asks the terminal for a year any more.
        # Writing per month also means an interrupted deep import keeps what it
        # already fetched instead of losing the year it was in the middle of.
        written = 0
        for edge, following in month_windows(year_start, stop):
            rows = settled_rates(broker, edge, following)
            if rows:
                written += writer.write(table, rows, cutoff_seconds,
                                        is_fx_symbol(broker, writer.clock))
        if written == 0:
            empty_run += 1
            log(f"  {broker} {year}: no bars")
            if empty_run >= EMPTY_YEAR_LIMIT:
                break
            continue
        empty_run = 0
        total += written
        log(f"  {broker} {year}: {written:,} bars -> {table} (running {total:,})")

    # COMPACTED BEFORE THE SPAN IS READ, so the reported row count is the one a
    # reader will actually see. The writer lands a shard per New York day, which
    # is right for a collector that must survive a crash and wrong for a bulk
    # import: 2025-2026 is 513 shards a symbol, and every consumer then opens
    # 513 files to answer one question. `flush` first -- the shards have to be
    # on disk before they can be folded.
    writer.close()
    rows = compact(table)
    if rows:
        log(f"  {table}: compacted to one file, {rows:,} rows")

    span = settled_span(table)
    return {"symbol": key, "broker": broker, "table": table,
            "written": total, "first_row": str(span[0])[:19],
            "last_row": str(span[1])[:19], "rows": span[2]}


def parse_args():
    two_days_ago = date.today() - timedelta(days=2)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broker", choices=sorted(BROKERS), default="exness")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="repository symbols; default is every symbol in "
                             "the broker's map")
    parser.add_argument("--to-date", default=two_days_ago.isoformat(),
                        help="last INCLUSIVE New York date to import "
                             "(default: two days ago)")
    parser.add_argument("--first-year", type=int, default=EARLIEST_YEAR)
    parser.add_argument("--from-date", default=None,
                        help="first INCLUSIVE New York date to import; with it "
                             "only the months from that date on are fetched, "
                             "which is what a same-day comparison needs")
    parser.add_argument("--terminal", type=Path, default=None,
                        help="default: the broker's own terminal")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--table-prefix", default=None, help="default: <broker>_")
    parser.add_argument("--report", type=Path, default=None,
                        help="default: <vendor dir>/<broker>/import_1m_report.json")
    args = parser.parse_args()
    profile = BROKERS[args.broker]
    args.symbol_map = profile["symbols"]
    args.clock = profile["clock"]
    args.symbols = args.symbols or sorted(args.symbol_map)
    args.terminal = args.terminal or profile["terminal"]
    args.table_prefix = args.table_prefix or profile["prefix"]
    args.report = args.report or VENDOR_DIR / args.broker / "import_1m_report.json"
    if profile["mutex"]:
        from ipc_lock import _NamedMutex  # noqa: PLC0415

        mt5._mutex = _NamedMutex(profile["mutex"])
    return args


def main():
    args = parse_args()
    wall_clock = ZoneInfo(args.timezone)
    symbol_map = args.symbol_map
    unknown = [s for s in args.symbols if s.lower() not in symbol_map]
    if unknown:
        print(f"unknown symbols for {args.broker}: {unknown}; "
              f"known: {sorted(symbol_map)}", file=sys.stderr)
        return 2

    # The cutoff is a NEW YORK date, so it is converted back to the real UTC
    # instant MT5 timestamps are expressed in before it filters anything.
    cutoff_ny = datetime.fromisoformat(args.to_date) + timedelta(days=1)
    cutoff = cutoff_ny.replace(tzinfo=wall_clock).astimezone(UTC)
    start = None
    if args.from_date:
        start = (datetime.fromisoformat(args.from_date)
                 .replace(tzinfo=wall_clock).astimezone(UTC))
        args.first_year = start.year

    if not mt5.initialize(path=str(args.terminal)):
        print(f"MT5 initialization failed: {mt5.last_error()}", file=sys.stderr)
        return 1

    writer = Writer(wall_clock, args.clock)
    report = []
    try:
        terminal, account = mt5.terminal_info(), mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account is unavailable: {mt5.last_error()}")
        log(f"account {account.login} on {account.server}, "
            f"maxbars {terminal.maxbars:,} (read-only feed)")
        # NOT A WARNING ANY MORE, because the request is a month and a month of
        # M1 fits inside even the 100,000 default. It used to say deep history
        # "will be truncated", which was wrong in the worse direction: an
        # over-cap request returns nothing at all rather than a truncated
        # answer. Raising the cap still helps the terminal cache more, so it is
        # reported and no longer asked for -- changing it means restarting a
        # terminal that may be carrying live orders.
        if terminal.maxbars <= 1_000_000:
            log(f"maxbars {terminal.maxbars:,} is the default; requests are "
                f"one month each and fit inside it. Raising [Charts] MaxBars "
                f"only makes the terminal cache more.")
        log(f"cutoff {cutoff.isoformat()} UTC "
            f"(New York bars through {args.to_date} 23:59)")

        for key in args.symbols:
            key = key.lower()
            log(f"{key} -> {symbol_map[key]}")
            try:
                summary = import_symbol(key, symbol_map[key], writer, cutoff,
                                        args.first_year, args.table_prefix,
                                        start)
            except Exception as error:  # noqa: BLE001 - one symbol must not
                # cost the other ten; an unquoted CFD is a normal outcome.
                log(f"{key}: {type(error).__name__}: {error}")
                summary = {"symbol": key, "broker": symbol_map[key],
                           "error": f"{type(error).__name__}: {error}"}
            report.append(summary)
            log(f"{key}: {summary}")
    finally:
        writer.close()
        mt5.shutdown()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    log(f"wrote {writer.written:,} bars; report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
