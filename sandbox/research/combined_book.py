"""NQ + BTC + crypto + commodities + index CFDs on ONE shared account.

`INITIAL` is 400.0, not the 1,000 this docstring used to claim.

THE BOOK.

    nq:ofi                 sandbox replica of NQ Deep OFI Momentum
    nq:hdr                 sandbox replica of NQ Hourly Delta Reversal
    nq:drift_vwap          Matteo Conti Drift VWAP Pullback, 1% equity risk
    btcusd:maroy_ladder    maroy_intraday_momentum "Ladder #1", the config
                           compiled as btc_maroy_ladder.rs
    btcusd:pdr             crypto_families_research sealed cell
    btcusd:donchian        the cell compiled in btc_donchian.rs, NOT the
                           study's same-named cell -- see OVERRIDES
    ethusd:vwap            crypto_families_research sealed cell
    ethbtc:orb             crypto_families_research sealed cell
    ethbtc:gap             crypto_families_research sealed cell
    xalusd:ma_cross        commodity_families_research frozen pass
    xngusd:donchian        commodity_families_research frozen pass
    xalusd:pdr             commodity_families_research frozen pass
    xalusd:overnight       commodity_families_research frozen pass
    xngusd:gap             commodity_families_research frozen pass
    xngusd:zscore          commodity_families_research frozen pass
    aus200:momentum        index_families_research sealed cell
    fr40:gap               index_families_research sealed cell
    hk50:gap               index_families_research sealed cell
    aus200:pdr             index_families_research sealed cell

Sleeves are labelled `market:rule`. BTC is keyed `btc` in QuestDB and in
`cf.SYMBOLS`, but the tradeable instrument is BTCUSD, so `MARKET_LABEL` renames
it for display only.

WHY THE TWO NEW BTC SLEEVES. `pdr` and `donchian` are the only BTC cells that
beat their own coin-flip null by a margin outside the three-seed error bar:
+64.4 and +40.4, against `momentum` +13.3 and `zscore` +9.4. BTC's null baseline
runs +18% to +40% out of sample, so a raw holdout return on this symbol means
little and the margin is the whole test. They are also structurally different --
a prior-session range break against a channel breakout -- rather than two
spellings of one trend rule.

ON THE LADDER'S SIZE. It contributes least in dollars, and that is sizing, not
edge: run alone it returns +15.8% at a 4.56% drawdown and PF 1.31 on **1.15x**
notional, where `pdr` and `donchian` run ~2x. Per trade it earns more than
`donchian` ($0.59 against $0.38) at half the leverage, and its return-per-unit-
of-drawdown is the best of the three. Its compiled policy targets 30% annualised
position volatility capped at 2x; the crypto-engine sleeves use a 1.5% risk
fraction over a 1.5x ATR stop, which lands near 2x. Different sizing
philosophies, not different quality.

Its extraction is still ~2x high against the engine (+35.0% / 10.1% standalone
versus the published +18.2% / 8.6%), so read its contribution as approximate.

WHY THIS IS IN PYTHON. `/api/combine` cannot run it: `prepare.rs` accepts only
`nq | es | btc | ethusd`, and ethbtc has no loader and no compiled strategy --
its P&L is denominated in BTC and the engine's `point_value` is a constant 1.0,
so it would be wrong by the BTC price. The NQ figures here are *replica*
figures and will not match `/api/combine` to the decimal.

HOW DIFFERENT SIZING MODELS ARE PUT ON ONE BALANCE. Every sleeve here sizes
linearly in equity -- a risk fraction over a stop, throttled by volatility -- so
each trade reduces to two equity-independent numbers:

    points_per_unit    USD profit per unit held, after that sleeve's own costs
    units_per_dollar   units the sleeve wanted per dollar of equity it had

`units_per_dollar` is the strategy's own sizing decision expressed independently
of the account it was measured on. Re-multiplying it by the *shared* balance
reproduces that decision here, which is what lets each sleeve keep its own risk
model while all of them compound against one another.

DRAWDOWN IS REPORTED TWICE. `closed` books a trade only when it closes, which is
what this module always did and what understates the number: a replay of closed
trades put an earlier version of this same book at 21% where the Rust engine's
mark-to-market said 37% -- [[engine-drawdown-is-mark-to-market]]. `mtm` re-marks
every open position against its market's 1-minute closes, so intraday open risk
is visible. Read `mtm`. `closed` is kept only so the two can be compared and so
the older reports in `results/` remain intelligible.

The MTM path is gross of the entry spread -- each sleeve's cost is still booked
in one lump at the close, because no upstream backtest records where inside the
trade it was charged. That makes MTM optimistic by at most one spread per open
trade, which is immaterial next to the open risk it exposes.

WINDOWS. 2025 in sample, 2026 out of sample, matching `portfolio_exposure.py`.
One caveat travels with it: the crypto sleeves were selected on 2019-2024 but
*shortlisted* by reading 2025-2026, so 2026 is not clean for them either.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
from dataclasses import replace
from datetime import datetime, timezone

from sandbox import data, execution, get_strategy
from sandbox.research import btc_donchian_regime
from sandbox.research import crypto_families_research as cf
from sandbox.research import maroy_intraday_momentum as maroy
from sandbox.research import crypto_portfolio as cp
from sandbox.research import commodity_families_research as commodity
from sandbox.research import drift_maroy_symbol_study as eth
from sandbox.research import drift_vwap_pullback as drift_vwap
from sandbox.research import index_families_research as ix
from sandbox.research import portfolio_price_vol as pv

#: Price the two IMPORTED NQ sleeves at the broker's own bars instead of the
#: vendor's. Off by default; `exness_combined_strategies --broker-fills` turns
#: it on so the whole canon book is priced on one feed rather than eighteen
#: sleeves on the broker and two on the vendor.
#:
#: BOTH SLEEVES NEED THE RESCALE, NOT JUST THE SWAP. `nq:ofi` runs on the raw
#: level-two tick series and `nq:drift_vwap` on `nq_1m`, a back-adjusted
#: futures continuum; Exness quotes USTEC, a cash CFD several percent away from
#: either. Their stops are absolute point distances, so filling at the raw
#: broker level is a different strategy rather than a different fill -- the
#: same trap that took the book's marked drawdown to 30.9%. `broker_fill_bars`
#: therefore goes through `exness_broker_fills.auto_fills`, which rescales onto
#: the deciding series' level with a previous-session ratio.
BROKER_FILLS = False

INITIAL = 400.0
IS = ("2025-01-01", "2026-01-01")
OOS = ("2026-01-01", "2026-08-05")
FULL = ("2025-01-01", "2026-08-05")

# --------------------------------------------------------------------------- #
# broker cost model
# --------------------------------------------------------------------------- #
#: WHICH ACCOUNT THE BOOK IS PRICED ON. `apply_cost_model` must be called before
#: any order builder runs; `main` does it, and anything importing this module
#: must do it too or the per-instrument defaults below silently price the book
#: on the wrong broker.
#:
#: The two account types are opposites, so this is not a tweak:
#:
#:     zero   spread 0.00, commission per lot  (the old account, 279659480)
#:     pro    NO commission, cost is the spread (the live account, 416209807)
#:
#: Pro is cheaper on every symbol this book trades and is the default because it
#: is what is actually traded. Priced on Zero the same book returns -8%; on Pro
#: it returns +243%. Getting this wrong does not shade a result, it inverts one.
COST_MODEL = "pro"

#: Median IN-SESSION spread on the Pro account, basis points of notional, from
#: seven days of tick history sampled only inside each sleeve's own trading
#: window. A spot reading is useless: sampled at 23:39 UTC with markets shut,
#: AUS200 quoted 12.7 bp against 1.1 bp in session.
PRO_SPREAD_BP = {"nq": 0.300, "ethusd": 3.686, "ethbtc": 4.066,
                 "xalusd": 8.895, "xngusd": 16.973, "aus200": 1.106,
                 "hk50": 1.964, "btc": 1.085}

#: The price each bp figure was measured at, needed to turn it back into an
#: absolute USD-per-lot charge.
PRO_PRICE = {"nq": 29731.0, "ethusd": 1876.77, "xalusd": 3310.02,
             "xngusd": 2.8336, "aus200": 9190.45, "hk50": 25286.7,
             "btc": 63399.0}

#: Commission per lot per ROUND TRIP on the Zero account, measured on the live
#: terminal by round-tripping the minimum lot. Billed entirely at entry.
ZERO_COMMISSION_PER_LOT = {"xalusd": 4.0, "xngusd": 70.0, "aus200": 1.50,
                           "hk50": 1.27, "fr40": 1.24, "ethusd": 1.0,
                           "ethbtc": 120.0, "btc": 9.0, "nq": 1.40}

#: Zero's index spread allowance, in bp. These were the broker's typical quoted
#: spreads; on Pro they are replaced by the measured in-session figures.
ZERO_INDEX_SPREAD_BP = {"aus200": 0.77, "de40": 0.54, "fr40": 0.79,
                        "hk50": 2.26, "stoxx50": 1.22, "uk100": 0.70,
                        "jp225": 1.56}

#: Set by `apply_cost_model`. The NQ sleeves fill through `execution.Execution`
#: and `drift_vwap.Account`, which take absolute price points rather than bp.
NQ_SPREAD_POINTS = 0.0
NQ_COMMISSION_PER_LOT = 0.0
#: Slippage allowance, charged on both account types. Measured median slippage
#: is 0 across 218 live fills, so this is deliberately conservative.
SLIPPAGE_POINTS = 0.2


def apply_cost_model(name=None):
    """Price the whole book on `pro` or `zero`, in place.

    The cost constants live in four different modules' global registries
    (`commodity.INSTRUMENTS`, `ix.INSTRUMENTS`, `cf.COMMISSION_PER_LOT` and this
    one), because each engine charges cost in its own units. Rather than thread
    an account through every call site, this sets them all once -- which is why
    it must run BEFORE any order builder, not after.

    Returns the name applied, so a caller can record what it priced.
    """
    global COST_MODEL, NQ_SPREAD_POINTS, NQ_COMMISSION_PER_LOT
    global ETH_COST_BP, INDEX_SPREAD_BP
    name = (name or COST_MODEL).lower()
    if name not in ("pro", "zero"):
        raise ValueError(f"unknown cost model {name!r}; use 'pro' or 'zero'")
    COST_MODEL = name
    pro = name == "pro"

    def usd_per_lot(symbol, multiplier):
        """A Pro spread in bp, as the USD-per-lot charge the engines expect."""
        return PRO_SPREAD_BP[symbol] / 1e4 * PRO_PRICE[symbol] * multiplier

    # NQ / USTEC. Pro pays a spread and no commission; Zero the reverse.
    NQ_SPREAD_POINTS = (PRO_SPREAD_BP["nq"] / 1e4 * PRO_PRICE["nq"]) if pro else 0.0
    NQ_COMMISSION_PER_LOT = 0.0 if pro else ZERO_COMMISSION_PER_LOT["nq"]

    # Commodities. `cost_price` reads `commission_per_lot` off the instrument.
    commodity.SPREAD_PIPS = 0.0
    commodity.SLIPPAGE_PIPS = SLIPPAGE_POINTS
    for key in ("xalusd", "xngusd"):
        cfg = commodity.INSTRUMENTS[key]
        cfg["commission_per_lot"] = (usd_per_lot(key, cfg["multiplier"]) if pro
                                     else ZERO_COMMISSION_PER_LOT[key])

    # Indices. On Pro the whole cost is the spread, carried by INDEX_SPREAD_BP,
    # so the commission channel is zeroed to avoid charging twice.
    INDEX_SPREAD_BP = dict(ZERO_INDEX_SPREAD_BP)
    for key, cfg in ix.INSTRUMENTS.items():
        if pro:
            cfg["commission_per_lot"] = 0.0
            if key in PRO_SPREAD_BP:
                INDEX_SPREAD_BP[key] = PRO_SPREAD_BP[key]
        else:
            cfg["commission_per_lot"] = ZERO_COMMISSION_PER_LOT.get(key, 0.0)

    # Crypto. `commission_bps` turns USD-per-lot into bp of the entry price, so
    # a Pro spread is expressed through the same channel.
    if pro:
        cf.COMMISSION_PER_LOT = {
            "ethusd": {"usd": usd_per_lot("ethusd", 1.0), "lot": 1.0},
            # One ETHBTC lot is 100 ETH, and it is quoted in BTC.
            "ethbtc": {"usd": PRO_SPREAD_BP["ethbtc"] / 1e4
                       * PRO_PRICE["ethusd"] * 100.0, "lot": 100.0},
            "btc": {"usd": usd_per_lot("btc", 1.0), "lot": 1.0},
        }
        ETH_COST_BP = PRO_SPREAD_BP["ethusd"] + 0.011
    else:
        cf.COMMISSION_PER_LOT = {
            "btc": {"usd": ZERO_COMMISSION_PER_LOT["btc"], "lot": 1.0},
            "ethusd": {"usd": ZERO_COMMISSION_PER_LOT["ethusd"], "lot": 1.0},
            "ethbtc": {"usd": ZERO_COMMISSION_PER_LOT["ethbtc"], "lot": 100.0},
        }
        ETH_COST_BP = 5.3
    return name


def nq_execution(ex):
    """`ex` repriced on the selected account.

    `Execution` is frozen and the strategies build theirs at import, so this
    returns a copy rather than mutating a shared instance.
    """
    return replace(ex, spread=NQ_SPREAD_POINTS, slippage=SLIPPAGE_POINTS,
                   commission_per_lot=NQ_COMMISSION_PER_LOT)

#: Calibrated on the Rust engine's mark-to-market account curve, not this
#: module's optimistic closed-trade replay. A target of 11.0 produced 14.93%
#: MTM drawdown and 12.0 produced 16.88%. The operator selected 12.0, accepting
#: the 0.88-point overshoot for the additional compounded return.
#:
#: `max_mult` still caps the multiplier at 3.0, and it binds in calm stretches,
#: which is why drawdown stops responding to the target above ~20.
EXPOSURE = {"target": 12.0, "halflife": 40, "max_mult": 3.0, "min_days": 30}

#: Sleeve labels are `market:rule` throughout, so a P&L table reads as a list of
#: instruments rather than a mix of registry display names and internal keys.
#: `{short: registry name}` -- the value is what `get_strategy` needs.
#: The canonical small-account book keeps NQ but deliberately excludes BTC.
#: BTC implementations remain available for standalone research and cannot
#: enter this account merely because the other sleeves compound.
NQ_SLEEVES = {"ofi": "Deep OFI Momentum", "hdr": "Hourly Delta Reversal"}
DRIFT_VWAP_SLEEVE = "nq:drift_vwap"
DRIFT_VWAP_RISK = 0.01
MAROY_ENABLED = False
CRYPTO_SLEEVES = (("ethusd", "vwap"), ("ethbtc", "orb"), ("ethbtc", "gap"))
#: Frozen from a Rust MTM sweep. 0.575 keeps the $400 book above 400% return
#: while staying below 17% true drawdown; 0.58 crosses a lot-step boundary and
#: jumps to 17.01%, so it is deliberately not rounded upward.
#: `aus200:momentum` is scaled to risk parity with the rest of the book, NOT to
#: a return or book-drawdown target: at 1.0 its own marked drawdown was 26.41%
#: of the account against a worst peer of 11.51%, and 0.45 brings it to 11.02%.
#: The scale was chosen by reading drawdowns only, so it is not fitted to P&L.
#:
#: It does NOT reduce the book's drawdown -- 28.43% to 27.63% -- because the
#: sleeve's worst run and the book's worst moment are different events
#: ([[book-drawdown-is-one-intraday-position]]). It costs 335 points of
#: full-window return and cuts the sleeve's P&L share from 13.8% to 6.7%. The
#: justification is concentration, not risk reduction: one sleeve carrying more
#: than twice any other's drawdown is a single point of failure, and this sleeve
#: also lost on the untouched 2018-2019 window.
#: `nq:hdr` is stood down at 0.0 rather than removed, matching the convention
#: `rust_schedule.DISABLED` uses: the sleeve still runs and stays warm, only its
#: order size is zero. Re-enable by restoring 0.575.
#:
#: It was disabled for open risk, not for its P&L. It made money (PF 1.31) and
#: its trade log shows nothing wrong, but it produced 87% of the book's worst
#: drawdown: two positions stacked an hour apart on 2025-02-26, both realised
#: +$4.99, and together they marked -$105 against a $528 account -- 20 of the
#: 27.63 drawdown points, from two winning trades
#: ([[book-drawdown-is-one-intraday-position]]).
#:
#: FOUR MORE STOOD DOWN, 2026-08-12, all at 0.0 and all for drawdown. Measured
#: over 2020-01-01..2026-08-05 on a $1,000 account, one change at a time:
#:
#:     xngusd:gap + xngusd:zscore   book MTM drawdown 17.95% -> 27.31%
#:     xngusd:gap alone             17.95% -> 21.46%
#:     aus200:pdr                   worst drawdown-per-return in the book,
#:                                  29.04% of its own contribution for 3.93%
#:                                  of book P&L
#:     fr40:gap                     ret/DD 0.11, PF 1.03 -- lowest of both
#:
#: The two XNG sleeves are the expensive pair and the reason is specific, not
#: general: they lose together in Jan-Feb 2021 while the account is still near
#: $3,700, and that single episode becomes the whole window's worst. Over the
#: book's OTHER worst episode (2024-08) they are neutral -- 17.95% against
#: 18.07% with them in -- so this is one event, not a standing risk premium.
#:
#: Scaling them down does not fix it. Fitted to peer-level drawdown on
#: 2020-2024 they came to 0.76 and 0.59, held that level out of sample (7.5%
#: and 6.7% against a 2.1-13.0% peer range), and the book still drew down
#: 27.31%. Per-sleeve `mtm_dd_pct_of_return` normalises by a sleeve's LIFETIME
#: contribution; book drawdown is point-in-time, so a loss that is modest
#: against six years is still large against the balance standing that week.
#: Equalising the first does not constrain the second.
#:
#: SIZING_EQUITY_CAP below is now unreachable: its only two keys are the XNG
#: sleeves stood down here. It is left in place so that re-enabling them
#: restores the capped behaviour rather than the uncapped 41.30% one.
#: `ethusd:maroy` 0.50 replaces its removed drawdown throttle -- see
#: OWN_PNL_THROTTLE. Chosen by reading BOOK drawdown only, never P&L, the same
#: rule `aus200:momentum` 0.45 was picked under. Its live twin is
#: `ETHUSD_MAROY_WEIGHT` in `live/portfolio.rs`; the two must move together.
#: TWO MORE STOOD DOWN, 2026-08-12: `ethbtc:orb` and `ethbtc:gap`, for SIZE
#: rather than for drawdown or P&L, and the reason is a live bug rather than a
#: research finding. The live path sent the crypto engine's COIN quantity
#: through as an MT5 lot volume, and ETHBTC's contract is 100 ETH, so every
#: entry was 100x intended -- 0.57 lots = 57 ETH = $107,434 notional on a
#: ~$1,100 account. Their live twin is `CANONICAL_STRATEGIES` in
#: `live/portfolio.rs`; the two must move together.
#:
#: They stay down after the conversion is fixed because ETHBTC's real minimum is
#: 0.01 lots = 1 ETH (~$1,885 notional). This book cannot afford one, so at the
#: correct size the sleeves take no trades at all -- see LOT_FLOOR_UNITS.
#: CUT TO SIX AND RE-SIZED, 2026-08-13, for the $400 Exness Pro account. Its
#: live twin is `CANONICAL_STRATEGIES` plus the `*_EQUITY_MULTIPLIER` constants
#: in `live/portfolio.rs`; the two are one decision recorded twice.
#:
#: Each surviving value is `sleeve weight x virtual-equity multiplier`. The
#: multiplier is the "shown as" sizing: on a $400 account a sleeve's risk-sized
#: order can land under the broker's `volume_min` and never fill at all --
#: `nq:ofi` had 100% of 2,438 entries refused, confirmed live with MT5 retcode
#: 10014. Showing the strategy a larger equity lifts the request over that
#: floor, and unlimited leverage means margin never binds (one minimum lot is
#: 0.08%-3.5% of the account).
#:
#:     sleeve             shown as   weight   x mult   = scale
#:     hk50:gap               $600    1.0        1.5     1.5
#:     ethusd:vwap            $800    1.0        2.0     2.0
#:     nq:ofi               $1,000    0.575      2.5     1.4375
#:     xngusd:donchian      $1,000    1.0        2.5     2.5
#:     xalusd:pdr             $400    1.0        1.0     1.0
#:     nq:drift_vwap          $500    1.0        1.25    1.25
#:     aus200:momentum        $600    0.45       1.5     0.675
#:
#: `nq:drift_vwap` RE-ADMITTED 2026-08-13, having been cut the previous day for
#: having 99% of its entries refused at $400. At 1.25x it fills 59% in the small
#: account and 100% once the balance compounds, and it earns the slot: +57.7
#: points of return for +3.1 of drawdown, and it is one of only three sleeves
#: profitable in BOTH 2025 and 2026 (PF 1.071 / 1.144). It reads OHLCV rather
#: than the level-two feed, so unlike `nq:ofi` it has history back to 2020.
#:
#: `xalusd:ma_cross` was re-admitted with it and REMOVED AGAIN the same day, on
#: measurement: ret/DD 0.79 and 0.75 in the two windows -- below 1.0 in both, so
#: it earns less than it risks -- costing 5.2 drawdown points for 31 of return,
#: a marginal efficiency of 6.0 against a book running at 11.7. It was also the
#: second-largest loser of 2025 at -$21.25, PF 0.910.
#:
#: The multipliers are per sleeve rather than global because each one's
#: return/drawdown peaks at a different level.
#:
#: `aus200:momentum` 1.5x is the only sizing change here confirmed on BOTH
#: windows and BOTH axes -- 2025-2026 +176.35%/16.73%dd to +185.96%/15.85%dd,
#: 2020-2026 +1038.25%/23.00%dd to +1413.70%/22.35%dd. It works because this is
#: the book's only hedge: through the drawdown that sets the book's worst trough
#: (2025-07-10..2025-10-03) the other five lost together and this one alone made
#: money. Its 0.45 weight was a CONCENTRATION control though, so 0.675 gives
#: part of that back -- see the constant in `live/portfolio.rs`.
#:
#: A `xngusd:donchian` trim to 1.25 was measured and REJECTED: it looked free on
#: 2025-2026 (+1.4 return, -1.8 drawdown) and cost 105 points of return on
#: 2020-2026 while not reducing drawdown at all. One-window artifact.
#:
#: `nq:ofi` is the exception and is sized for PARTICIPATION, not for its ret/DD
#: peak (which is 1.5x). USTEC's 0.05 minimum lot is $1,487, 3.7x a $400
#: account, and this sleeve sizes off a fixed 30-point stop, so its request is a
#: constant 0.0575 lots at 1.5x. The NQ volatility overlay scales that by
#: 0.31-1.00 and anything under 0.05 is refused, so at 1.5x only 8% of days
#: could trade and at 1.0x none could. Raised to 2.5x on 2026-08-13 for 69% of
#: days. 100% would need 4.16x, where a calm day sends 0.15 lots = 11x the
#: account; the real fix is a ~$1,100 balance, at which 1.5x fills everything at
#: the designed risk.
#:
#: THE FOUR NEWLY CUT, on evidence rather than on this window's returns:
#: `xalusd:overnight` loses under every cost model and balance tested and is the
#: only hold-through-the-gap family, so it alone pays an uncharged swap;
#: `ethusd:drift_vwap` loses at every multiplier (PF 0.91-0.95);
#: `ethusd:maroy` has no edge either way; `xalusd:ma_cross` has the weakest
#: ret/DD of anything still profitable; `nq:drift_vwap` had 99% of its entries
#: unaffordable at $400.
#:
#: The six on 2020-2026 at Pro pricing: +945.7%, PF 1.17, worst mark-to-market
#: drawdown 22.0%, and PF 1.18 fitted (2020-2024) against 1.17 holdout
#: (2025-2026) -- the fitted and out-of-sample numbers agree, which is the
#: reason to trust it at all.
SLEEVE_SCALE = {"nq:ofi": 1.4375, "nq:drift_vwap": 1.25, "hk50:gap": 1.5,
                "ethusd:vwap": 2.0, "xngusd:donchian": 2.5,
                "aus200:momentum": 0.675, "xalusd:pdr": 1.0,
                # everything else stands down
                "nq:hdr": 0.0,
                "ethusd:maroy": 0.0, "ethusd:drift_vwap": 0.0,
                "xalusd:ma_cross": 0.0, "xalusd:overnight": 0.0,
                "aus200:pdr": 0.0, "fr40:gap": 0.0,
                "xngusd:gap": 0.0, "xngusd:zscore": 0.0,
                "ethbtc:orb": 0.0, "ethbtc:gap": 0.0}
COMMODITY_SLEEVES = (("xalusd", "ma_cross"), ("xngusd", "donchian"),
                     ("xalusd", "pdr"), ("xalusd", "overnight"),
                     ("xngusd", "gap"), ("xngusd", "zscore"))
COMMODITY_PARAMS = {
    "xalusd:ma_cross": {"exit_mode": "rr_2", "fast": 46,
        "last_entry_minute": 720, "slow": 115, "stop_atr": 3.5,
        "trend": "none", "vol_mode": "none"},
    "xngusd:donchian": {"channel": 48, "exit_mode": "time_4",
        "last_entry_minute": 810, "stop_atr": 1.0,
        "trend": "ema_50d", "vol_mode": "none"},
    "xalusd:pdr": {"buffer_atr": 0.0, "direction": "breakout",
        "exit_mode": "rr_2", "last_entry_minute": 720,
        "stop_atr": 3.5, "trend": "none", "vol_mode": "calm"},
    "xalusd:overnight": {"buffer_atr": 0.25, "direction": "fade",
        "exit_mode": "trail_1.5", "last_entry_minute": 780,
        "stop_atr": 2.5, "trend": "none", "vol_mode": "calm"},
    "xngusd:gap": {"direction": "follow", "exit_mode": "time_4",
        "stop_atr": 1.0, "threshold_atr": 1.0,
        "trend": "ema_50d", "vol_mode": "none"},
    "xngusd:zscore": {"direction": "follow", "exit_mode": "time_4",
        "last_entry_minute": 810, "period": 24, "stop_atr": 1.0,
        "threshold_z": 1.5, "trend": "ema_50d", "vol_mode": "none"},
}

#: XNG's 10,000-dollar point multiplier and 0.01-lot floor make fractional
#: exposure multipliers erase trades on a $400 account. Gap and Z-score keep
#: their native 1.5% risk sizing, but size from at most $400 so their lots do
#: not compound into the 28% IS drawdown produced by uncapped shared equity.
#: If the account falls below $400 they still size from the lower real balance.
SIZING_EQUITY_CAP = {"xngusd:gap": 400.0, "xngusd:zscore": 400.0}

#: Same staged small-account gates emitted by ``rust_schedule.py``. A disabled
#: sleeve stays warm; only its order size is zero until shared equity clears the
#: resume level. Maroy also requires an eligible starting balance, because a
#: late activation path was not validated as equivalent to funding it from day 1.
BALANCE_GATES = {
    "btcusd:pdr": (1_350.0, 1_500.0),
    "btcusd:donchian": (1_800.0, 2_000.0),
    "btcusd:maroy_ladder": (2_700.0, 3_000.0),
}
#: The four index sleeves, added 2026-08-10. Frozen from the sealed cells in
#: `results/index_families_*.json` -- the `aus-mom + fr-gap + hk-gap + aus-pdr`
#: combination, which was the best sub-15% drawdown book in that study's sweep.
#:
#: READ THE CAVEAT BEFORE READING THEIR P&L. These four were selected on
#: 2020-2024 and 2025-2026 was their sealed holdout, so this book's window is
#: clean for them -- cleaner than the crypto legs, which were shortlisted by
#: reading 2025-2026. But every one of them LOST on the untouched 2018-2019
#: window, and as a four-sleeve book they lost 22% there while a coin-flip book
#: made money. They are in here to be measured against the rest of the account,
#: not because they earned a promotion. See INDEX_FAMILIES.md.
INDEX_SLEEVES = (("aus200", "momentum"), ("fr40", "gap"),
                 ("hk50", "gap"), ("aus200", "pdr"))
INDEX_PARAMS = {
    "aus200:momentum": {"direction": "fade", "exit_mode": "rr_2", "lookback": 30,
        "signal_minute": 120, "stop_atr": 1.0, "threshold_atr": 0.5,
        "trend": "none", "vol_mode": "none"},
    "fr40:gap": {"direction": "follow", "exit_mode": "trail_1.5", "stop_atr": 1.0,
        "threshold_atr": 0.5, "trend": "ema_20d", "vol_mode": "calm"},
    "hk50:gap": {"direction": "follow", "exit_mode": "trail_1.5", "stop_atr": 2.5,
        "threshold_atr": 0.25, "trend": "none", "vol_mode": "none"},
    "aus200:pdr": {"buffer_atr": 0.25, "direction": "fade", "exit_mode": "rr_1",
        "last_entry_minute": 360, "stop_atr": 3.5, "trend": "none",
        "vol_mode": "none"},
}
#: Index legs' SLIPPAGE, in basis points. These were the broker's typical quoted
#: spreads (only UK100's was read live), which on a zero-spread account is what
#: a slippage allowance looks like; the account itself quotes 0.00.
#:
#: Commission is charged SEPARATELY and comes from `ix.INSTRUMENTS[...]
#: ["commission_per_lot"]` via `ix.cost_price`, so it must not be folded in
#: here or the index legs pay twice. Measured commission is 0.42-1.77 bp, the
#: same order as these figures -- indices are the one family where the old cost
#: model was roughly the right size, and they move least under the correction.
INDEX_SPREAD_BP = {"aus200": 0.77, "de40": 0.54, "fr40": 0.79, "hk50": 2.26,
                   "stoxx50": 1.22, "uk100": 0.70, "jp225": 1.56}

INITIAL_BALANCE_GATES = {"btcusd:maroy_ladder": 3_000.0}
MAROY_CONFIG = "Ladder #1"
MAROY_SLEEVE = "btcusd:maroy_ladder"

# --------------------------------------------------------------------------- #
# ETHUSD sleeves, added from `drift_maroy_symbol_study`
# --------------------------------------------------------------------------- #
#: Two cells from the 15-symbol non-BTC non-NQ sweep.  ETHUSD was the only
#: symbol in that study where either strategy beat its own coin-flip control at
#: all three cost settings *and* made money while the underlying fell 43%.  Both
#: cells were selected on 2020-2024, so this book's 2025 IS and 2026 OOS windows
#: are untouched for them -- cleaner than the crypto sleeves above, which were
#: shortlisted by reading 2025-2026.
#:
#: READ THE CAVEAT BEFORE READING THEIR P&L.  The entire ETH edge sits inside the
#: 09:30-16:00 New York session, and that window was inherited from the BTC
#: studies rather than derived from ETH's own volume profile: applied to any
#: other six hours of this 24/7 market, both cells lose or lose to their null
#: ([[eth-us-hours-momentum-is-session-conditional]]).  They are here to be
#: measured against the rest of the account, not because they earned a promotion.
ETH_SESSION = (9 * 60 + 30, 16 * 60)

#: Maroy's Section 4.1 time exit: no stop, no target, no trail.  Enter when price
#: breaks 1.3x the typical 4-session intraday move from the open anchor, checked
#: every 30 minutes, hold to 30 minutes before the close.  `target_vol` 1.0 keeps
#: the paper's volatility scale-down inert so the leverage cap sets size alone.
ETH_MAROY_SLEEVE = "ethusd:maroy"
ETH_MAROY_CONFIG = {
    "name": "ethusd time exit", "exit_kind": "time", "family": "Time only",
    "lookback_days": 4, "k_enter": 1.3, "k_exit": None, "target_vol": 1.0,
    "start_after": 1, "frequency": 30, "exit_before": 30,
}
#: Flat leverage plus the drawdown throttle, the shape its solo 15%-drawdown
#: calibration selected.  The throttle reads the sleeve's OWN closed P&L, not the
#: shared balance -- it is a rule this sleeve follows about its own book, which
#: an account running many sleeves can still track separately.  That is why it
#: survives the reduction to `units_per_dollar` below.
ETH_MAROY_POLICY = {"sizing": "leverage_cap", "leverage": 0.95,
                    "dd_threshold": 0.05, "dd_throttle": 0.25}

#: EMPTY, 2026-08-12. maroy's drawdown throttle was the book's only stateful
#: rule and it is now removed everywhere -- here, and via `drawdown_throttle:
#: 1.0` on `MaroyParams::ETHUSD`.
#:
#: WHY. Its state was rebuilt by a rolling warm-up replay, so the size it chose
#: depended on which day the server last restarted: the replayed drawdown
#: measured 4.71%-5.16% against a 5% threshold, a 4x position-size coin flip.
#: A risk control you cannot predict is not a risk control.
#:
#: WHAT IT COST. At book level the throttle was genuinely efficient -- it cut
#: size exactly when maroy was losing, which a constant scale cannot imitate.
#: Removing it and holding the book near its old drawdown costs a little:
#:
#:     throttle ON,  scale 1.00   book mtmDD 15.39%   final 827,404
#:     throttle OFF, scale 0.50   book mtmDD 16.02%   final 903,465
#:     throttle OFF, scale 0.35   book mtmDD 15.63%   final 802,152
#:
#: 0.50 was chosen: +0.63 drawdown points against +9% return, and the best
#: return-per-drawdown of the three. 0.35 is worse than the throttle on BOTH
#: axes, so matching 15.39% exactly is not available -- book drawdown floors
#: near 15.6% whatever maroy does, because the other twelve sleeves carry it.
#:
#: The measured advantage of keeping the throttle was not obtainable live, which
#: is the whole reason it goes. Its standalone OOS return-per-drawdown (10.07)
#: and the unthrottled sleeve's (9.98) are indistinguishable.
#:
#: The format is kept for the next sleeve that needs one:
#: `{sleeve: (threshold, throttle)}`, applied at REPLAY time against the
#: sleeve's own realised P&L, seeded at the shared account's starting balance.
#:
#: WHY IT CANNOT LIVE IN THE EXTRACTION. `ETH_MAROY_POLICY` already carries this
#: throttle, and `eth_maroy_orders` runs the sleeve standalone at
#: ETH_SIZING_BALANCE to read its sizing. That standalone run throttles against
#: its OWN $1,000,000 equity path, and the resulting decision is then frozen
#: into `units_per_dollar`. But the engine (`MaroyParams::ETHUSD` carries
#: `throttle_on_own_pnl: true`) throttles against a curve seeded at the shared
#: account's balance and fed by the quantities the shared book actually took --
#: a different curve with a different shape, so the two throttle on different
#: days.
#:
#: The cost of getting this wrong is not small and not one-directional: paired
#: against the engine, py's biggest maroy trades were sized 5.0x too large in
#: 2025 and 0.30x too small in 2026 -- 1/0.25 and 0.25, the throttle firing on
#: one side and not the other. It doubled the sleeve's P&L (1.98x) while every
#: other sleeve matched to ~1%.
OWN_PNL_THROTTLE: dict[str, tuple[float, float]] = {}

#: Drift VWAP Pullback with symbol-relative brackets: the published 80/40-point
#: NQ pair is meaningless on ETH, so the stop is 0.55% of entry and the target
#: 1.5x the stop.  0.375% risk per stop is its solo 15%-drawdown calibration.
ETH_DRIFT_SLEEVE = "ethusd:drift_vwap"
ETH_DRIFT_PARAMS = {"stop_pct": 0.0055, "rr": 1.5, "momentum": 0.002}
ETH_DRIFT_RISK = 0.00375

#: Deliberately NOT the book's 0.2-pip convention.  On ETHUSD a pip is 0.01, so
#: 0.2 pips is 0.002 dollars -- 0.008 bp of a $2,500 price, which is close enough
#: to free that it cannot discriminate between a real edge and churn
#: ([[absolute-spread-inverts-the-symbol-ranking]]).  Both cells were validated
#: at 0.5 bp and still cleared their null at 2 bp, so they are charged the
#: validated figure here rather than the flattering one.
#: SUPERSEDED 2026-08-12 by a measurement. The 0.5 below was a validated
#: research figure, not a broker quote, and the broker's real charge on ETHUSD
#: is $1.00 per lot per round trip billed at entry -- 5.30 bp of a $1,886 price,
#: about 10x what these two sleeves were being charged. Slippage adds 0.011 bp
#: and the account quotes no spread at all, so commission is essentially the
#: whole cost.
ETH_COST_BP = 5.3

#: Run standalone at a large balance so the 0.01 lot floor does not quantise
#: `units_per_dollar`; `combine` re-applies the step against the real balance.
ETH_SIZING_BALANCE = 1_000_000.0

#: The broker's MINIMUM order size, per market, in the units that market's
#: `units_per_dollar` is denominated in. Read from the live terminal
#: (`volume_min` x `trade_contract_size`) on 2026-08-12.
#:
#: This is not the same thing as the quantity STEP, and conflating the two is
#: how the book came to fill trades the broker would reject. A step of 0.01 says
#: what increments are legal; `volume_min` says how small an order may be, and
#: on a large contract a small `volume_min` is still a large position:
#:
#:     ethbtc   0.01 lots x 100 ETH/lot = 1.0 ETH   (~$1,885)  100x the step
#:     ethusd   0.10 lots x   1 ETH/lot = 0.1 ETH   (~$188)     10x the step
#:     aus200   0.06 lots                           (~$390)
#:
#: On a $400-$1,000 book ETHBTC is therefore untradeable outright, which is the
#: honest reason `ethbtc:orb` and `ethbtc:gap` sit at 0.0 in SLEEVE_SCALE rather
#: than merely being small.
LOT_FLOOR_UNITS = {"nq": 0.05, "ethusd": 0.10, "ethbtc": 1.0, "btcusd": 0.01,
                   "xalusd": 0.01, "xngusd": 0.01, "aus200": 0.06,
                   "fr40": 0.05, "hk50": 0.07}

#: QuestDB and `cf.SYMBOLS` key BTC as `btc`, but the tradeable instrument is
#: BTCUSD and every other symbol here already carries its quote currency. This
#: renames it for display only; the data key is untouched.
MARKET_LABEL = {"btc": "btcusd"}


def label(symbol, rule):
    return f"{MARKET_LABEL.get(symbol, symbol)}:{rule}"

#: Entry spread, 0.2 pips, on the Exness rule that pip size is the last decimal
#: of the quoted price. Verified against the quotes themselves: ethusd prints two
#: decimals (2010.80) so a pip is 0.01; ethbtc prints five (0.02723) so a pip is
#: 0.00001.
#:
#:     ethusd    0.2 x 0.01     = 0.002
#:     ethbtc    0.2 x 0.00001  = 0.000002
#:
#: ethbtc is quoted in BTC, not dollars, so its spread is a BTC amount and the
#: engine crosses it into USD with the rest of that leg's P&L.
#:
#: btc quotes to two decimals, so 0.2 pips is 0.002 dollars -- but the BTC legs
#: keep the 0.2 absolute they already had via `cf.default_spread_bps()`, which is
#: the same 0.2 USD expressed relatively. The NQ legs are untouched:
#: `execution.Execution` already charges 0.2 at entry in index points.
PIP = {"ethusd": 0.01, "ethbtc": 0.00001}
PIPS_CHARGED = 0.2

#: `pdr` reads its sealed cell from `results/crypto_families_btc.json`:
#: buffer 0.25 ATR, breakout, stop 1.5 ATR, time_12, ema_50d.
#:
#: `donchian` does NOT. It is overridden to the cell compiled in
#: `live_trade/src/strategies/idk/btc_donchian.rs`, so the book trades what the
#: server actually runs rather than a same-named cell the study happened to
#: pick. The two differ substantially -- the study's is a two-session channel
#: (28 buckets) with a 50-session EMA filter and no VIX gate; the compiled one
#: is a one-session channel with a VIX ceiling and no trend filter.
#:
#: The Rust `CHANNEL` is `BARS_PER_SESSION`, which it counts as 13; this engine
#: counts the same 09:30-16:00 session as 14 buckets because its `in_session`
#: includes the 16:00 flatten bucket. The channel is therefore written as "one
#: session" rather than as a literal -- hard-coding 13 would be a 13/14-session
#: channel, a third rule again.
#:
#: MAX_VIX 25.0 is reproduced via `crypto_portfolio.accepts_vix`, which reads
#: `vix_1d` and gates on the PRIOR day's close.
#: No weekday filter. The Rust sleeve used to carry one, which is what produced
#: 385 trades here against the engine's 258 -- a 49% divergence that was entirely
#: the two weekend sessions a week, not the channel length it was first
#: attributed to. The gate has since been removed from `btc_donchian.rs` because
#: BTC prints those sessions, so both sides now trade all seven days.
BTC_DONCHIAN = {"channel": cf.BARS_PER_SESSION, "last_entry_minute": 780,
                "stop_atr": 1.5, "exit_mode": "trail_2.5", "trend": "none",
                "vol_mode": "none", "max_vix": 25.0}
OVERRIDES = {"btc:donchian": BTC_DONCHIAN}

#: The crypto legs' risk fraction. NOT comparable with the NQ sleeves' 0.5%
#: (`execution.Execution.risk`) -- at 1.5% the crypto legs are pressed three
#: times as hard as the NQ ones, and that choice, not their edge, is part of why
#: they carry ~46% of the book's P&L. Swept by `--crypto-risk`.
CRYPTO_RISK_PCT = 1.5

#: Which market's volatility drives each sleeve's exposure. NQ sleeves follow NQ,
#: everything crypto follows BTC -- throttling a BTC sleeve on NQ's volatility
#: would be an overlay driven by an unrelated market.
DRIVER = {"nq:ofi": "nq", "nq:hdr": "nq", DRIFT_VWAP_SLEEVE: "nq",
          "xalusd:ma_cross": "xalusd", "xngusd:donchian": "xngusd",
          "xalusd:pdr": "xalusd", "xalusd:overnight": "xalusd",
          # These two deliberately bypass the target-12 price-volatility
          # overlay. Their risk is bounded by SIZING_EQUITY_CAP instead.
          "xngusd:gap": "xng_native", "xngusd:zscore": "xng_native",
          # Each index sleeve is throttled on its own index's price volatility.
          "aus200:momentum": "aus200", "fr40:gap": "fr40",
          "hk50:gap": "hk50", "aus200:pdr": "aus200",
          # The two ETH sleeves follow BTC, matching every other crypto leg
          # here. ETH has no schedule of its own in `exposure_schedule`, and BTC
          # is the volatility both legs actually trade against.
          ETH_MAROY_SLEEVE: "btc", ETH_DRIFT_SLEEVE: "btc"}


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


# --------------------------------------------------------------------------- #
# orders: (sleeve, entry_ts, exit_ts, points_per_unit, units_per_dollar, step,
#          mark)
#
# `mark` is `(market, side, entry_price, usd_per_point)` and exists only so the
# mark-to-market pass can revalue the position while it is open: `market` keys
# the 1-minute table, `side` is +1/-1, and `usd_per_point` converts one unit's
# price move into dollars. `None` excludes an order from the MTM curve, and
# `mark_coverage` reports how much of the book that costs.
# --------------------------------------------------------------------------- #


#: Markets whose 1-minute table is not resolvable from the commodity or index
#: instrument registries. Crypto follows `{symbol}_1m`, matching
#: `symbol_spread_bp` and every `*_1m` table in this database.
MARK_TABLE = {"nq": "nq_1m", "btc": "btc_1m", "ethusd": "ethusd_1m",
              "ethbtc": "ethbtc_1m"}

#: A market key for the sleeves that FILL on level-two bars rather than on
#: `nq_1m`. The two NQ series are not the same price: level-two fills run about
#: 4.7% below the OHLCV table (19,630 against 20,599 on the same minute), so
#: marking a level-two position against `nq_1m` adds a fixed ~950-point error to
#: every unrealised figure -- roughly $48 on a 0.05 lot, which is the whole size
#: of the drawdowns this once produced. `nq:ofi` and `nq:hdr` mark here;
#: `nq:drift_vwap` reads OHLCV and stays on `nq`.
#:
#: The realised-P&L check cannot catch this class of bug: it uses the engine's
#: own entry and exit prices and never consults the marking series.
LEVEL_TWO_MARKET = "nq_l2"

#: `{market: ([ts], [close])}`, parallel lists rather than tuples so the MTM
#: walk can bisect the stamps without rebuilding a key list per trade.
#: `{(market, window_start, window_end): ([ts], [open], [close])}`. Keyed by the
#: WINDOW as well as the market -- see `price_series` for what keying on market
#: alone silently did to a second window's drawdown.
_SERIES = {}


def side_sign(side):
    """+1/-1 from either convention in this codebase.

    The engines disagree: `execution` and both Drift replicas record `"long"` /
    `"short"`, while the crypto, commodity, index and Maroy engines record the
    integer already. `None` propagates so the caller can drop the mark rather
    than guess a direction.
    """
    if side is None:
        return None
    if isinstance(side, str):
        return 1 if side == "long" else -1
    return 1 if side > 0 else -1


def mark_table(market):
    if market in MARK_TABLE:
        return MARK_TABLE[market]
    if market in commodity.INSTRUMENTS:
        return commodity.INSTRUMENTS[market]["table"]
    return ix.INSTRUMENTS[market]["table"]


def price_series(market, window=None):
    """One-minute stamps and closes for `market`, cached per process.

    Index tables carry a `shift_hours` internally, but index trades already
    subtract it back out before they are recorded, so raw table timestamps and
    order timestamps are both real time and need no adjustment here.

    `window` resolves at call time rather than in the signature: `FULL` is
    rebindable from the command line, and a default bound at definition would
    silently keep the original dates after `main` widened them.

    THE CACHE KEY INCLUDES THE WINDOW, and must. It was keyed on `market` alone,
    so the first window to load a market won and every later one silently reused
    it -- fine for a single run, wrong the moment one process evaluates two
    windows. Scoring 2025-2026 and then 2020-2026 in one process marked the
    second window's 2020-2024 positions against a series that began in 2025,
    which moved its reported mark-to-market drawdown from 21.99% to 21.56%
    while leaving return, profit factor and trade count untouched -- a
    discrepancy visible only in the drawdown, which is the number least likely
    to be checked.

    The level-two series is window-independent (it is loaded whole from
    `data.load_bars`) but is keyed the same way for uniformity; it is one entry
    per distinct window rather than one, which costs nothing at these sizes.
    """
    window = FULL if window is None else window
    key = (market, window[0], window[1])
    if market == LEVEL_TWO_MARKET and key not in _SERIES:
        bars = data.load_level_two_bars("nq")
        _SERIES[key] = ([int(b[data.TS]) for b in bars],
                        [float(b[data.O]) for b in bars],
                        [float(b[data.C]) for b in bars])
    if key not in _SERIES:
        tbl_name = mark_table(market)
        from sandbox import parquet_store as store
        raw_bars = store.read_bars(tbl_name, bar_minutes=1, start=window[0], end=window[1])
        stamps = [int(b[0]) for b in raw_bars]
        opens = [float(b[1]) for b in raw_bars]
        closes = [float(b[4]) for b in raw_bars]
        _SERIES[key] = (stamps, opens, closes)
    return _SERIES[key]


def price_at(market, ts):
    """The open of `market`'s minute containing `ts`, or None before its data.

    Used only where a sleeve's trade log omits its entry price: the shared
    crypto engine records `points_per_unit` and size but not the fill, and its
    entries are always a bar open.
    """
    stamps, opens, _closes = price_series(market)
    index = bisect.bisect_right(stamps, ts) - 1
    return opens[index] if index >= 0 else None


def broker_fill_bars(bars, symbol="nq", bar_minutes=1):
    """`bars` re-priced at Exness, level-matched, or `None` when switched off.

    Returns a list aligned one-to-one with `bars`; a minute Exness never quoted
    keeps its own bar, which is counted rather than interpolated.
    """
    if not BROKER_FILLS or not bars:
        return None
    from sandbox.research import exness_broker_fills as bf
    from sandbox.research import cfd_families as ef

    if not bf.has_fills(symbol):
        return None
    ef.resolve(symbol, allow_stale=True)
    lo, hi = bars[0][data.TS], bars[-1][data.TS] + 1
    fills, stats = bf.auto_fills(symbol, bars, bar_minutes, (lo, hi))
    print(f"  {symbol} imported sleeve: {stats['matched_pct']:.1f}% of bars "
          f"priced at Exness"
          + (f", rescaled {stats.get('level_shift_before_bp', 0):+.0f}bp -> "
             f"{stats['level_shift_bp']:+.0f}bp" if stats["rescaled"] else ""))
    return fills


def nq_orders(short, name):
    """One NQ replica's trades, reduced to the equity-independent form.

    `execution.resolve` returns per-unit fills that already carry the entry
    price and the stop distance, so the sleeve's own sizing rule can be
    evaluated symbolically instead of at some particular balance.
    """
    strategy = get_strategy(name)
    # Repriced on the selected account: the strategy's own `Execution` carries
    # whatever the registry was written with, not this book's broker.
    ex = nq_execution(strategy.execution)
    bars = data.load_bars(strategy.bars, strategy.symbol)
    context = strategy.context()
    params = strategy.all_params(None)
    signals = []
    for group in strategy.groups():
        signals.extend(strategy.signals(bars, context, group, params))
    out = []
    for fill in execution.resolve(bars, signals, ex,
                                  fill_bars=broker_fill_bars(bars)):
        if fill.stop <= 0 or fill.price <= 0:
            continue
        units_per_dollar = min(ex.risk / fill.stop,
                               1.0 / (ex.margin * fill.price)) * ex.leverage
        # These sleeves fill on `strategy.bars`, which is the level-two series
        # for both of them -- so they must be marked there too.
        market = (LEVEL_TWO_MARKET if strategy.bars == "level_two" else "nq")
        out.append((f"nq:{short}", fill.entry_ts, fill.exit_ts,
                    fill.points * ex.point_value, units_per_dollar, ex.step,
                    (market, side_sign(fill.side), fill.price, ex.point_value)))
    return out


def maroy_policy(config_name=MAROY_CONFIG):
    """The sizing policy `btc_maroy_ladder.rs` was compiled from.

    `run_config` defaults to `policy=None`, which is the *paper's* rule: the
    whole available margin, i.e. 4x notional on almost every trade. That is not
    what ships. The compiled sleeve is volatility-targeted and capped at 2x, and
    the stored policy for "Ladder #1" matches its constants exactly --
    vol_target_annual 0.3 = VOLATILITY_TARGET, leverage 2.0 = LEVERAGE_CAP,
    hours 10-13 = FIRST/LAST_ENTRY_HOUR, rv20 <= 100 = RV20_CEILING, and the
    missing weekday 2 = EXCLUDED_WEEKDAY.
    """
    path = os.path.join(os.path.dirname(__file__), "maroy_optimization_rth.json")
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    family = next(f for f in report["families"] if f["strategy"] == config_name)
    policy = {k: (set(v) if k in ("hours", "weekdays") else v)
              for k, v in family["policy"].items()}
    if "regime" in policy:
        policy["regime"] = {k: tuple(v) for k, v in policy["regime"].items()}
        # `_allowed` blocks a trade whose regime feature is missing rather than
        # waving it through, so a policy carrying `regime` without
        # `regime_features` takes no trades at all -- silently.
        policy["regime_features"] = btc_donchian_regime.daily_features()
    return policy


def maroy_orders(config_name=MAROY_CONFIG):
    """The Maroy Ladder's trades in the equity-independent form.

    The ladder exits in partials, so a "trade" here is one partial close. Equity
    at entry is reconstructed rather than recorded, leaving
    `maroy_intraday_momentum` untouched: the replica holds at most one position
    at a time and appends trades in close order, so consecutive records sharing
    an entry stamp are the partials of one position and the balance standing
    before that group is what it was sized against.
    """
    config = next(c for c in maroy.CONFIGS if c["name"] == config_name)
    sessions = maroy.load_sessions("rth", "btc")
    result = maroy.run_config(sessions, config, "rth", maroy_policy(config_name))
    out = []
    equity = maroy.INITIAL
    index = 0
    trades = result["trades"]
    while index < len(trades):
        entry_ts = trades[index]["ts"]
        group = []
        while index < len(trades) and trades[index]["ts"] == entry_ts:
            group.append(trades[index])
            index += 1
        opened_at = equity
        for trade in group:
            quantity = trade.get("quantity") or 0.0
            if quantity > 0 and opened_at > 0:
                out.append((MAROY_SLEEVE, entry_ts,
                            trade.get("exit_ts", entry_ts),
                            trade["pnl"] / quantity, quantity / opened_at, 0.0,
                            ("btc", trade["side"], trade["entry"], 1.0)))
            equity += trade["pnl"]
    return out


def mean_price(symbol):
    from sandbox import parquet_store as store
    raw_bars = store.read_bars(f"{symbol}_1m", bar_minutes=1, start=FULL[0], end=FULL[1])
    if not raw_bars:
        return 0.0
    return float(sum(b[4] for b in raw_bars) / len(raw_bars))


def symbol_spread_bp(symbol):
    """Slippage plus commission for `symbol`, in basis points of entry.

    Two components, and the second used to be missing entirely:

      * SLIPPAGE, 0.2 pips, converted at the window's mean price because the
        engine charges proportionally while pips are absolute. Prices move
        inside the window -- dogeusd roughly halves across 2025-2026 -- so the
        effective charge drifts by up to about 2x on that leg. BTC keeps the 0.2
        absolute it already had.
      * COMMISSION, measured per lot on the live terminal. This dominates: on
        ethusd it is 5.30 bp against slippage's 0.011 bp, a factor of ~500.

    There is no spread term because the account quotes none -- 218 live fills
    read 0.00 across nine symbols.
    """
    price = mean_price(symbol)
    if symbol in PIP:
        slippage_bp = 1e4 * (PIPS_CHARGED * PIP[symbol]) / price
    else:
        slippage_bp = cf.default_spread_bps()
    # ethbtc is quoted in BTC, so its dollar commission converts at the BTC
    # price before it can be a fraction of an ethbtc price.
    rate = mean_price("btc") if cf.SYMBOLS[symbol]["quote"] == "BTC" else 1.0
    return slippage_bp + cf.commission_bps(symbol, price, rate)


def crypto_orders(book, spread_bp=None, risk_pct=CRYPTO_RISK_PCT, window=None):
    """The crypto sleeves, via the shared-account engine they already run on.

    Run at a deliberately large balance so the lot step is smooth and
    `units_per_dollar` reflects the sizing rule rather than rounding; the step
    is re-applied against the real balance in `combine`.
    """
    window = FULL if window is None else window
    out = []
    # One run per symbol, because the spread is 0.2 *pips* and a pip is a
    # different price on each: charging one shared basis-point figure would put
    # dogeusd's cost on ethusd's scale, a 20x error.
    for symbol, family in book:
        bps = spread_bp if spread_bp is not None else symbol_spread_bp(symbol)
        sleeves = cp.load_sleeves([(symbol, family)], OVERRIDES)
        run = cp.simulate(sleeves, risk_pct,
                          lo=stamp(window[0]), hi=stamp(window[1]),
                          initial=1_000_000.0, spread_bp=bps)
        for trade in run["trades"]:
            if not trade["units_per_dollar"]:
                continue
            out.append((label(symbol, family), trade["entry_ts"], trade["exit_ts"],
                        trade["points_per_unit"], trade["units_per_dollar"],
                        cf.SYMBOLS[trade["symbol"]]["step"],
                        crypto_mark(trade)))
    return out


def crypto_mark(trade):
    """`(market, side, entry, usd_per_point)` for one shared-engine crypto trade.

    The engine records size and `points_per_unit` but not the fill, and its
    entries are always a bucket open, so the entry price is read back from the
    minute table. ethbtc is quoted in BTC, so one point of it is worth a BTC --
    priced at the entry, which is the same convention the engine's own `fx`
    lookup uses and drifts only with BTC over the life of one intraday trade.
    """
    market = trade["symbol"]
    side = side_sign(trade.get("side"))
    entry = price_at(market, trade["entry_ts"])
    if entry is None or side is None:
        return None
    if cf.SYMBOLS[market]["quote"] == "USD":
        return (market, side, entry, 1.0)
    if market == "ethbtc":
        rate = price_at("btc", trade["entry_ts"])
        return None if rate is None else (market, side, entry, rate)
    return None


def drift_vwap_order_rows(trades, risk=DRIFT_VWAP_RISK,
                          account=drift_vwap.Account(),
                          rules=drift_vwap.Rules(),
                          mark_market=LEVEL_TWO_MARKET):
    """Reduce Drift VWAP trades to the shared book's symbolic order form.

    The standalone backtest's quantity is deliberately ignored. The shared
    account re-applies the same 1%-of-live-equity risk and broker margin cap at
    each entry, after all other sleeves' earlier closes have changed equity.
    ``net_points`` already includes the requested 0.2-point entry spread.
    """
    out = []
    for trade in trades:
        entry = float(trade["entry_price"])
        if entry <= 0 or rules.stop <= 0:
            continue
        units_per_dollar = min(
            risk / rules.stop,
            1.0 / (account.margin * entry),
        ) * account.leverage
        side = side_sign(trade.get("side"))
        mark = (None if side is None else
                (mark_market, side, entry, account.point_value))
        out.append((DRIFT_VWAP_SLEEVE, trade["entry_ts"], trade["exit_ts"],
                    trade["net_points"] * account.point_value,
                    units_per_dollar, account.quantity_step, mark))
    return out


def drift_vwap_fill_map(bars_5m):
    """`{ts: Bar}` of Exness 5-minute bars matching `bars_5m`, or `None`.

    `drift_vwap` carries `Bar` dataclasses rather than the tuples the rest of
    the book uses, so the rows come back through `auto_fills` as tuples and are
    rebuilt as `Bar`s here. A bucket Exness never quoted is simply absent from
    the map and executes against its own bar.
    """
    if not BROKER_FILLS or not bars_5m:
        return None
    tuples = [(bar.ts, bar.open, bar.high, bar.low, bar.close, bar.volume)
              for bar in bars_5m]
    fills = broker_fill_bars(tuples, "nq", 5)
    if fills is None:
        return None
    return {row[0]: drift_vwap.Bar(int(row[0]), row[1], row[2], row[3],
                                   row[4], row[5])
            for row, original in zip(fills, tuples) if row is not original}


def drift_vwap_orders(window=None, risk=DRIFT_VWAP_RISK, source="hybrid"):
    """YouTube Drift VWAP Pullback fills for the shared-account replay."""
    window = FULL if window is None else window

    def run(minutes, lower, upper, market):
        if not minutes or lower >= upper:
            return []
        bars_5m = drift_vwap.aggregate(minutes, 5, rth_only=True)
        # The broker's own 5-minute bars for the SAME buckets. Built from
        # `bars_5m` rather than from `minutes` so the two lists cannot drift
        # apart: `aggregate` decides which buckets exist, and the fill map is
        # keyed on the timestamps it produced.
        fills = drift_vwap_fill_map(bars_5m)
        bars_15m = drift_vwap.aggregate(minutes, 15, rth_only=False)
        states = drift_vwap.trend_states(bars_15m)
        account = drift_vwap.Account(initial=1_000_000.0, risk=risk,
                                     spread=NQ_SPREAD_POINTS,
                                     slippage=SLIPPAGE_POINTS,
                                     commission_per_lot=NQ_COMMISSION_PER_LOT)
        rules = drift_vwap.Rules()
        result = drift_vwap.backtest(
            bars_5m,
            states,
            account=account,
            rules=rules,
            from_ts=lower,
            to_ts=upper,
            sizing=drift_vwap.Sizing(mode="equity_risk", risk=risk),
            volatility={},
            fills=fills,
        )
        return drift_vwap_order_rows(result["trades"], risk, account, rules,
                                     mark_market=market)

    lower, upper = stamp(window[0]), stamp(window[1])
    if source != "hybrid":
        minutes = drift_vwap.load_minutes("nq", window[0], window[1],
                                          source=source)
        market = LEVEL_TWO_MARKET if source == "level_two" else "nq"
        return run(minutes, lower, upper, market)

    # Do NOT concatenate the price arrays.  nq_1m is the broker CFD while the
    # L2 tick feed has a different absolute price level; joining them creates a
    # fictitious gap that contaminates VWAP, momentum and marked drawdown.  Run
    # two independent causal segments and switch at the first timestamp that
    # actually exists in L2, rather than at a hard-coded calendar date.
    l2 = drift_vwap.load_minutes("nq", window[0], window[1], source="level_two")
    native = drift_vwap.load_minutes("nq", window[0], window[1], source=None)
    if not l2:
        return run(native, lower, upper, "nq")
    boundary = l2[0].ts
    rows = run(native, lower, min(boundary, upper), "nq")
    rows.extend(run(l2, max(boundary, lower), upper, LEVEL_TWO_MARKET))
    rows.sort(key=lambda row: (row[1], row[2]))
    return rows


def _eth_prepare():
    """Register ETHUSD's session with the study module and warm its bars."""
    eth.SYMBOLS["ethusd"]["session"] = ETH_SESSION
    maroy.SESSIONS["ethusd"] = ETH_SESSION
    maroy.SESSIONS_PER_YEAR = eth.SYMBOLS["ethusd"]["sessions_per_year"]
    maroy.COST_BPS = ETH_COST_BP


def eth_maroy_orders(window=None):
    """ETHUSD Maroy time-exit fills in the shared book's symbolic order form.

    Equity at entry is reconstructed rather than recorded, exactly as
    `maroy_orders` does for the BTC ladder, so `maroy_intraday_momentum` stays
    untouched. The time exit closes a position whole, so each record is its own
    trade, but the grouping is kept so a future partial-exit cell cannot silently
    mis-attribute its size.
    """
    window = FULL if window is None else window
    _eth_prepare()
    saved = maroy.INITIAL
    maroy.INITIAL = ETH_SIZING_BALANCE
    # The drawdown throttle is stripped here and re-applied by `combine` against
    # the shared book's own-P&L curve -- see OWN_PNL_THROTTLE. Left in, it would
    # freeze this standalone run's $1,000,000 drawdown state into
    # `units_per_dollar`, where it neither scales nor matches the engine.
    policy = {**ETH_MAROY_POLICY, "dd_threshold": None}
    try:
        sessions = eth._maroy_sessions("ethusd")
        result = maroy.run_config(sessions, ETH_MAROY_CONFIG, "ethusd",
                                  policy,
                                  (stamp(window[0]), stamp(window[1])))
    finally:
        maroy.INITIAL = saved

    out = []
    equity = ETH_SIZING_BALANCE
    trades = result["trades"]
    index = 0
    while index < len(trades):
        entry_ts = trades[index]["ts"]
        group = []
        while index < len(trades) and trades[index]["ts"] == entry_ts:
            group.append(trades[index])
            index += 1
        opened_at = equity
        for trade in group:
            quantity = trade.get("quantity") or 0.0
            if quantity > 0 and opened_at > 0:
                out.append((ETH_MAROY_SLEEVE, entry_ts,
                            trade.get("exit_ts", entry_ts),
                            trade["pnl"] / quantity, quantity / opened_at,
                            eth.QUANTITY_STEP,
                            ("ethusd", side_sign(trade["side"]), trade["entry"],
                             eth.POINT_VALUE)))
            equity += trade["pnl"]
    return out


def eth_drift_vwap_orders(window=None, risk=ETH_DRIFT_RISK):
    """ETHUSD Drift VWAP Pullback fills for the shared-account replay.

    The standalone quantity is ignored: the shared account re-applies the same
    risk fraction and margin cap at each entry, after every other sleeve's
    earlier closes have moved the balance. `net_points` already carries the
    entry cost.
    """
    window = FULL if window is None else window
    _eth_prepare()
    minutes = eth.load_minutes("ethusd")
    if not minutes:
        return []
    bars_5m = eth.aggregate(minutes, 5, ETH_SESSION)
    states = eth.drift_states(eth.aggregate(minutes, 15), ETH_SESSION,
                              ETH_DRIFT_PARAMS["momentum"])
    saved = eth.DRIFT_RISK_FRACTION
    eth.DRIFT_RISK_FRACTION = risk
    try:
        result = eth.drift_backtest(bars_5m, states, ETH_SESSION,
                                    ETH_DRIFT_PARAMS,
                                    (stamp(window[0]), stamp(window[1])),
                                    ETH_COST_BP)
    finally:
        eth.DRIFT_RISK_FRACTION = saved

    out = []
    for trade in result["trades"]:
        entry = float(trade["entry_price"])
        stop = entry * ETH_DRIFT_PARAMS["stop_pct"]
        if entry <= 0 or stop <= 0:
            continue
        units_per_dollar = min(risk / stop, 1.0 / (eth.MARGIN * entry))
        out.append((ETH_DRIFT_SLEEVE, trade["entry_ts"], trade["exit_ts"],
                    trade["net_points"] * eth.POINT_VALUE, units_per_dollar,
                    eth.QUANTITY_STEP,
                    ("ethusd", side_sign(trade["side"]), entry,
                     eth.POINT_VALUE)))
    return out


def commodity_order_rows(symbol, family, trades, initial):
    """Convert one commodity trade log to shared-book order coefficients.

    Commodity quantities are MT5 lots. ``points_per_unit`` is therefore USD
    per lot, using the terminal-verified tick-value multiplier, while
    ``units_per_dollar`` is lots requested per dollar of strategy equity.
    """
    cfg = commodity.INSTRUMENTS[symbol]
    equity = initial
    out = []
    for trade in trades:
        lots = trade.get("quantity") or 0.0
        if lots > 0 and equity > 0:
            out.append((f"{symbol}:{family}", trade["entry_ts"], trade["exit_ts"],
                        trade["points"] * cfg["multiplier"], lots / equity,
                        cfg["volume_step"], lot_mark(symbol, trade, cfg)))
        equity += trade["pnl"]
    return out


def lot_mark(symbol, trade, cfg):
    """`(market, side, entry, usd_per_point)` for a lot-denominated CFD trade.

    Shared by the commodity and index legs: both quote quantity in MT5 lots and
    both record the entry price and side, so one lot's price move converts to
    dollars through the terminal-verified `multiplier`.
    """
    side = side_sign(trade.get("side"))
    if side is None or trade.get("entry") is None:
        return None
    return (symbol, side, trade["entry"], cfg["multiplier"])


def commodity_orders(book=COMMODITY_SLEEVES, window=None):
    """Frozen commodity cells in the book's equity-independent order form.

    The extraction balance is large enough to smooth the 0.01-lot step, which
    is reapplied later by ``combine`` against the real shared $1,000 balance.
    """
    window = FULL if window is None else window
    initial = 10_000.0
    lo, hi = stamp(window[0]), stamp(window[1])
    out = []
    for symbol, family in book:
        bars, ctx = commodity.context(symbol, "validate")
        result = commodity.backtest(
            family, bars, ctx, COMMODITY_PARAMS[f"{symbol}:{family}"],
            lo=lo, hi=hi, initial=initial,
            spread_pips=commodity.SPREAD_PIPS, include_trades=True)
        out += commodity_order_rows(symbol, family, result["trade_log"], initial)
    return out


def index_order_rows(symbol, family, trades, initial):
    """One index trade log in the book's equity-independent order form.

    Identical in shape to `commodity_order_rows`: index CFD quantities are MT5
    lots, `points_per_unit` is USD per lot from the terminal-verified tick-value
    multiplier, and `units_per_dollar` is lots requested per dollar of that
    sleeve's own equity.
    """
    cfg = ix.INSTRUMENTS[symbol]
    equity = initial
    out = []
    for trade in trades:
        lots = trade.get("quantity") or 0.0
        if lots > 0 and equity > 0:
            out.append((f"{symbol}:{family}", trade["entry_ts"], trade["exit_ts"],
                        trade["points"] * cfg["multiplier"], lots / equity,
                        cfg["volume_step"], lot_mark(symbol, trade, cfg)))
        equity += trade["pnl"]
    return out


def index_orders(book=INDEX_SLEEVES, window=None):
    """Frozen index cells as shared-book orders, at realistic spreads.

    Extracted at 10,000 for the same reason the commodity legs are: at 1,000 the
    lot step, not the signal, decides which trades exist. `combine` reapplies the
    real step against the shared balance.
    """
    window = FULL if window is None else window
    initial = 10_000.0
    lo, hi = stamp(window[0]), stamp(window[1])
    out = []
    for symbol, family in book:
        bars, ctx = ix.context(symbol, "validate")
        result = ix.backtest(family, bars, ctx, INDEX_PARAMS[f"{symbol}:{family}"],
                             lo=lo, hi=hi, initial=initial,
                             spread_bp=INDEX_SPREAD_BP[symbol], include_trades=True)
        out += index_order_rows(symbol, family, result["trade_log"], initial)
    return out


# --------------------------------------------------------------------------- #
# the shared account
# --------------------------------------------------------------------------- #


def combine(orders, initial=INITIAL, window=None, exposure=None, scale=None,
            sizing_equity_cap=None, balance_gates=None, initial_balance_gates=None,
            gross_cap=None, own_pnl_throttle=None, lot_floor=None):
    """Every sleeve's orders replayed against one compounding balance.

    Mirrors `execution.size`: a fill's size depends on every trade that closed
    before it opened, so an event queue replays them in entry order. The
    difference is that the queue is fed by five strategies at once, which is the
    whole point -- their gains and losses land on the same balance and compound
    against each other.

    `gross_cap` optionally bounds *simultaneous* exposure: an entry that would
    push the summed notional of everything currently open past `gross_cap`
    multiples of equity is refused outright rather than trimmed, because a
    partial fill would change the sleeve's own risk model rather than the book's.
    It is the one lever that can reach an intraday drawdown, since the drawdown
    is made of positions that are open at the same moment. Notional needs the
    order's `mark`; unmarked orders are never refused, and `refused` in the
    result reports how many entries the cap actually stopped.
    """
    window = FULL if window is None else window
    lo, hi = stamp(window[0]), stamp(window[1])
    scale = SLEEVE_SCALE if scale is None else scale
    sizing_equity_cap = (SIZING_EQUITY_CAP if sizing_equity_cap is None
                         else sizing_equity_cap)
    balance_gates = BALANCE_GATES if balance_gates is None else balance_gates
    initial_balance_gates = (INITIAL_BALANCE_GATES if initial_balance_gates is None
                             else initial_balance_gates)
    own_pnl_throttle = (OWN_PNL_THROTTLE if own_pnl_throttle is None
                        else own_pnl_throttle)
    lot_floor = LOT_FLOOR_UNITS if lot_floor is None else lot_floor
    #: `{sleeve: count}` of entries dropped for being under the broker's
    #: minimum. Reported rather than silently discarded: a sleeve that cannot
    #: afford a single legal order looks identical to one that simply had no
    #: signals, and the two mean very different things.
    rejected = {}
    funded = {sleeve: initial >= initial_balance_gates.get(sleeve, 0.0)
              for sleeve in set(balance_gates) | set(initial_balance_gates)}
    # Each throttled sleeve's own realised curve, fed only by its own closes.
    #
    # THE SEED IS NOT `initial`, and this is the engine's behaviour rather than
    # a choice. `engine.rs` applies the exposure schedule as a factor on the
    # equity each slot is SHOWN, and `MaroyTime::update` seeds `own_equity` from
    # whatever equity it is handed on its first bar -- so the high-water mark it
    # measures drawdown against starts at the THROTTLED balance. On
    # 2020-01-01 the ETH multiplier is 0.2055, so a $1,000 book seeds this
    # sleeve's curve at ~$205 and a 5% drawdown is ~$10 rather than ~$50.
    #
    # Seeding at `initial` instead put py and the engine on opposite sides of
    # the threshold for 152 of 240 trades in 2020 and 84 in 2021, each one a
    # clean 4x size difference. It self-corrects as accumulated P&L swamps the
    # seed, which is why 2022 onward already agreed to 97-100%.
    first_day = datetime.fromtimestamp(lo, tz=timezone.utc).strftime("%Y-%m-%d")
    own = {}
    for sleeve in own_pnl_throttle:
        seed = initial
        if exposure is not None:
            seed *= exposure.get(DRIVER.get(sleeve, "btc"), {}).get(first_day, 1.0)
        own[sleeve] = [seed, seed]
    equity = initial
    peak = equity
    drawdown = 0.0
    queue = []
    curve = []
    booked = []
    sequence = 0
    exposed = 0.0
    refused = 0
    for order in sorted(orders, key=lambda o: o[1]):
        sleeve, entry_ts, exit_ts, points, units, step = order[:6]
        mark = order[6] if len(order) > 6 else None
        if not lo <= entry_ts < hi:
            continue
        queue.sort()
        while queue and queue[0][0] <= entry_ts:
            _ts, _seq, pnl, name, opened, size, held, notional = queue.pop(0)
            exposed -= notional
            equity += pnl
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 0.0)
            curve.append((_ts, equity))
            booked.append({"sleeve": name, "entry_ts": opened, "exit_ts": _ts,
                           "pnl": pnl, "quantity": size, "mark": held})
            if name in own:
                own[name][0] += pnl
                own[name][1] = max(own[name][1], own[name][0])
        multiplier = 1.0
        if exposure is not None:
            day = datetime.fromtimestamp(entry_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            multiplier = exposure.get(DRIVER.get(sleeve, "btc"), {}).get(day, 1.0)
        multiplier *= scale.get(sleeve, 1.0)
        if sleeve in own_pnl_throttle:
            threshold, throttle = own_pnl_throttle[sleeve]
            value, own_peak = own[sleeve]
            if own_peak > 0 and value < own_peak * (1.0 - threshold):
                multiplier *= throttle
        if sleeve in balance_gates:
            minimum, resume = balance_gates[sleeve]
            funded[sleeve] = (equity >= minimum if funded.get(sleeve, True)
                              else equity >= resume)
            if not funded[sleeve]:
                continue
        if initial < initial_balance_gates.get(sleeve, 0.0):
            continue
        sizing_equity = min(equity, sizing_equity_cap.get(sleeve, equity))
        quantity = units * sizing_equity * multiplier
        # The step says which sizes are legal; the broker's `volume_min` says
        # how small an order may be, and they are not the same number. Taking
        # the step alone as the floor is what let this book fill ETHBTC orders
        # 100x below the smallest one the broker accepts.
        minimum = max(step, lot_floor.get(sleeve.split(":", 1)[0], 0.0))
        if step:
            quantity = math.floor(quantity / step) * step
        if quantity <= 0 or quantity < minimum:
            # A sleeve held at scale 0.0 is STOOD DOWN, not unaffordable. Both
            # produce a zero quantity here and counting them together would
            # report six deliberately-disabled sleeves as if the account could
            # not afford them, which is the opposite of the thing this counter
            # exists to reveal.
            if multiplier > 0:
                rejected[sleeve] = rejected.get(sleeve, 0) + 1
            continue
        # `sequence` only breaks ties: two sleeves can close on the same second,
        # and without it `sorted` would fall through to comparing a mark tuple
        # against None.
        notional = 0.0
        if mark is not None:
            _market, _side, entry_price, usd_per_point = mark
            notional = abs(quantity * entry_price * usd_per_point)
            if gross_cap is not None and equity > 0:
                if exposed + notional > gross_cap * equity:
                    refused += 1
                    continue
        exposed += notional
        sequence += 1
        queue.append((exit_ts, sequence, points * quantity, sleeve, entry_ts,
                      quantity, mark, notional))
    for exit_ts, _seq, pnl, name, opened, size, held, _notional in sorted(queue):
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 0.0)
        curve.append((exit_ts, equity))
        booked.append({"sleeve": name, "entry_ts": opened, "exit_ts": exit_ts,
                       "pnl": pnl, "quantity": size, "mark": held})
        # No entry follows this drain, so the throttle state is never read
        # again; advanced anyway so `own` describes the whole run rather than
        # stopping at the last entry.
        if name in own:
            own[name][0] += pnl
            own[name][1] = max(own[name][1], own[name][0])
    return {"final": equity, "curve": curve, "trades": booked,
            "max_dd_pct": round(100.0 * drawdown, 2), "refused": refused,
            "below_broker_minimum": rejected}


# --------------------------------------------------------------------------- #
# mark to market
# --------------------------------------------------------------------------- #


def open_value(trades):
    """`{minute: unrealised USD}` summed over every open position.

    One pass per trade across its own market's minutes between entry and exit.
    A trade whose `mark` is missing contributes nothing, which is why
    `mark_coverage` is reported next to any MTM figure that uses this.
    """
    out = {}
    for trade in trades:
        mark = trade.get("mark")
        quantity = trade.get("quantity") or 0.0
        if not mark or quantity <= 0:
            continue
        market, side, entry, usd_per_point = mark
        stamps, _opens, closes = price_series(market)
        start = bisect.bisect_left(stamps, trade["entry_ts"])
        stop = bisect.bisect_left(stamps, trade["exit_ts"])
        scale = side * usd_per_point * quantity
        for index in range(start, stop):
            value = scale * (closes[index] - entry)
            stamp_key = stamps[index]
            out[stamp_key] = out.get(stamp_key, 0.0) + value
    return out


def mark_coverage(trades):
    """Share of trades, and of gross P&L, that the MTM pass can actually mark."""
    marked = [t for t in trades if t.get("mark")]
    gross = sum(abs(t["pnl"]) for t in trades)
    return {
        "trades_pct": round(100.0 * len(marked) / len(trades), 2) if trades else 0.0,
        "pnl_pct": round(100.0 * sum(abs(t["pnl"]) for t in marked) / gross, 2)
                   if gross else 0.0,
        "unmarked": sorted({t["sleeve"] for t in trades if not t.get("mark")}),
    }


def mtm_drawdown(closes, opens, start, lo, hi):
    """Peak-to-trough of realised equity plus open positions, over [lo, hi).

    `closes` is the realised step function as `(ts, equity)`; `opens` is
    `open_value`'s minute map. The peak entering the window is the equity
    standing at `lo`, matching `summarise`, so a window is never handed a fresh
    peak it did not earn.
    """
    closes = sorted(closes)
    stamps = sorted(set([ts for ts, _equity in closes]) | set(opens))
    equity, index = start, 0
    # Consume everything before the window first, so the peak entering it is the
    # equity actually standing at `lo`. Seeding the peak from the first in-window
    # observation instead would discard a high carried in from the previous
    # window, and would hide an opening loss against the starting balance.
    while index < len(closes) and closes[index][0] < lo:
        equity = closes[index][1]
        index += 1
    peak, drawdown, trough = equity, 0.0, 0.0
    peak_ts = peak_at = trough_at = None
    for stamp_key in stamps:
        if stamp_key < lo:
            continue
        if stamp_key >= hi:
            break
        while index < len(closes) and closes[index][0] <= stamp_key:
            equity = closes[index][1]
            index += 1
        value = equity + opens.get(stamp_key, 0.0)
        if value > peak:
            peak, peak_ts = value, stamp_key
        fall = peak - value
        if fall > trough:
            trough = fall
        if peak > 0 and fall / peak > drawdown:
            drawdown = fall / peak
            peak_at, trough_at = peak_ts, stamp_key
    day = lambda ts: (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                      if ts else None)
    return round(100.0 * drawdown, 2), round(trough, 2), day(peak_at), day(trough_at)


def sleeve_drawdown(trades, opens, lo, hi, account):
    """One sleeve's own peak-to-trough, in dollars, closed and marked.

    A sleeve inside a shared book has no balance of its own, so its drawdown is
    measured on its cumulative contribution: the worst dollar giveback from its
    own high-water mark.

    TWO NORMALISATIONS, AND ONLY ONE IS COMPARABLE. `pct_of_account` divides the
    dollar trough by the book equity standing at that moment. That answers "what
    did this sleeve's bad run cost the account", but it is path-dependent: the
    same $248 giveback reads 11.8% if it lands on a $2,100 balance and 3.6% on a
    $6,900 one. Comparing it between two books that compounded differently
    measures when the trough happened, not how bad the sleeve is -- which is
    exactly how `aus200:pdr` came to read 3.65% against 11.83% while its dollar
    drawdown agreed to within 1%.

    `pct_of_return` is the scale-free one: every mark is divided by the equity
    standing at that instant *before* accumulating, so the series is the sleeve's
    contribution to account return and its drawdown is comparable across books.
    """
    closed = sorted(((t["exit_ts"], t["pnl"]) for t in trades
                     if lo <= t["exit_ts"] < hi))
    equity_at = sorted(account)
    stamps = sorted({ts for ts, _pnl in closed}
                    | {ts for ts in opens if lo <= ts < hi})
    cumulative, index = 0.0, 0
    balance, position = None, 0
    peak, worst, worst_balance = 0.0, 0.0, None
    # The scale-free series: each realised close divided by the balance it
    # landed on, accumulated. Open marks are divided by the balance standing at
    # the instant they are read.
    contribution, peak_return, worst_return = 0.0, 0.0, 0.0
    for stamp_key in stamps:
        while index < len(closed) and closed[index][0] <= stamp_key:
            if balance:
                contribution += closed[index][1] / balance
            cumulative += closed[index][1]
            index += 1
        while (position < len(equity_at)
               and equity_at[position][0] <= stamp_key):
            balance = equity_at[position][1]
            position += 1
        value = cumulative + opens.get(stamp_key, 0.0)
        peak = max(peak, value)
        if peak - value > worst:
            worst = peak - value
            worst_balance = balance
        marked = contribution + (opens.get(stamp_key, 0.0) / balance
                                 if balance else 0.0)
        peak_return = max(peak_return, marked)
        worst_return = max(worst_return, peak_return - marked)
    return {
        "dd_usd": round(worst, 2),
        "pct_of_account": (round(100.0 * worst / worst_balance, 2)
                           if worst_balance else None),
        "pct_of_return": round(100.0 * worst_return, 2),
    }


def sleeve_table(run, lo, hi):
    """Per-sleeve P&L, both drawdowns, PF and win rate over one window."""
    # By EXIT, for the same reason as `summarise`, and additionally because
    # `sleeve_drawdown` below already books closes by exit: filtering the two on
    # different ends made a sleeve's P&L and its drawdown describe different
    # trade sets.
    trades = [t for t in run["trades"] if lo <= t["exit_ts"] < hi]
    account = run["curve"]
    # Share of the book's net P&L, so a sleeve's contribution reads the same
    # whether it earned its dollars early on a small balance or late on a large
    # one.
    #
    # The denominator is the ABSOLUTE total. Over a window the book lost, the
    # signed total is negative, and dividing by it flips every sign: a sleeve
    # that lost money reads as a large positive "share" of the book. Taking the
    # magnitude keeps the sign of the share the sign of the sleeve's own P&L,
    # which is the only reading that survives a losing window.
    total = abs(sum(t["pnl"] for t in trades))
    out = {}
    for name in sorted({t["sleeve"] for t in trades}):
        rows = [t for t in trades if t["sleeve"] == name]
        opens = open_value(rows)
        wins = sum(t["pnl"] for t in rows if t["pnl"] > 0)
        losses = -sum(t["pnl"] for t in rows if t["pnl"] < 0)
        closed_only = sleeve_drawdown(rows, {}, lo, hi, account)
        marked = sleeve_drawdown(rows, opens, lo, hi, account)
        pnl = sum(t["pnl"] for t in rows)
        out[name] = {
            "pnl": round(pnl, 2),
            "pct_of_book": round(100.0 * pnl / total, 2) if total else 0.0,
            "trades": len(rows),
            "closed_dd_usd": closed_only["dd_usd"],
            "mtm_dd_usd": marked["dd_usd"],
            "closed_dd_pct_of_account": closed_only["pct_of_account"],
            "mtm_dd_pct_of_account": marked["pct_of_account"],
            "closed_dd_pct_of_return": closed_only["pct_of_return"],
            "mtm_dd_pct_of_return": marked["pct_of_return"],
            "return_over_mtm_dd": (round(pnl / marked["dd_usd"], 2)
                                   if marked["dd_usd"] > 0 else None),
            "pf": round(wins / losses, 3) if losses else 0.0,
            "win_pct": round(100.0 * sum(1 for t in rows if t["pnl"] > 0)
                             / len(rows), 1) if rows else 0.0,
            "marked_pct": round(100.0 * sum(1 for t in rows if t.get("mark"))
                                / len(rows), 1) if rows else 0.0,
        }
    return out


def summarise(run, initial, lo, hi, opens=None):
    before = [(ts, eq) for ts, eq in run["curve"] if ts < lo]
    curve = [(ts, eq) for ts, eq in run["curve"] if lo <= ts < hi]
    if not curve:
        return {}
    # Attributed by EXIT, matching the equity curve, which only moves when a
    # trade closes. Filtering by entry instead breaks the identity that a
    # window's P&L is its equity change: a position opened in December and
    # closed in January counts its whole result in the earlier window while the
    # curve books it in the later one. On a 2020-01-01 boundary that produced a
    # window reporting profit factor 1.09 and 40 of 60 positive months next to a
    # -84.79% return.
    trades = [t for t in run["trades"] if lo <= t["exit_ts"] < hi]
    # A sub-window inherits the actual account equity immediately before it.
    # Using its first close as the denominator silently discards that trade and
    # biases every IS/OOS return even though the full-book final balance is right.
    start = before[-1][1] if before else initial
    peak, drawdown = start, 0.0
    for _ts, equity in curve:
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak > 0 else 0.0)
    months = {}
    for trade in trades:
        moment = datetime.fromtimestamp(trade["exit_ts"], tz=timezone.utc)
        stampkey = f"{moment.year}-{moment.month:02d}"
        months[stampkey] = months.get(stampkey, 0.0) + trade["pnl"]
    monthly = list(months.values())
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    mtm_pct, mtm_usd, mtm_peak, mtm_trough = (
        mtm_drawdown(run["curve"], opens, start, lo, hi)
        if opens is not None else (None, None, None, None))
    return {
        "return_pct": round(100.0 * (curve[-1][1] - start) / start, 2),
        "max_dd_pct": round(100.0 * drawdown, 2),
        "mtm_dd_pct": mtm_pct,
        "mtm_dd_usd": mtm_usd,
        "mtm_dd_peak": mtm_peak,
        "mtm_dd_trough": mtm_trough,
        "trades": len(trades),
        "pf": round(wins / losses, 3) if losses else 0.0,
        "positive_months": sum(1 for v in monthly if v > 0),
        "n_months": len(monthly),
        "by_sleeve": {s: round(sum(t["pnl"] for t in trades if t["sleeve"] == s), 2)
                      for s in sorted({t["sleeve"] for t in trades})},
        "trades_by_sleeve": {s: sum(t["sleeve"] == s for t in trades)
                             for s in sorted({t["sleeve"] for t in trades})},
    }


def exposure_schedule(settings=None):
    """Per-market daily multipliers from EWMA price volatility."""
    settings = settings or EXPOSURE
    # NQ is capped at 1.0, matching `DriverTimeline::new(1.0)` in
    # `live/portfolio.rs`: the NQ overlay is de-levering only, where every other
    # driver may lever up to `max_mult`. This changes nothing measurable -- a
    # multiplier above 1.0 needs annualised volatility below the 12% target, and
    # NQ has never printed that on this data (0 of 343 real days; BTC 0 of
    # 3,251). It is aligned so the two cannot diverge if it ever does.
    nq_settings = {**settings, "max_mult": min(settings["max_mult"], 1.0)}
    schedules = {
        "nq": pv.multipliers(pv.daily_closes("nq", "level_two"), **nq_settings),
        "btc": pv.multipliers(pv.daily_closes("btc", "ohlcv"), **settings),
    }
    tables = [(symbol, commodity.INSTRUMENTS[symbol]["table"])
              for symbol, _family in COMMODITY_SLEEVES]
    tables += [(symbol, ix.INSTRUMENTS[symbol]["table"])
               for symbol, _family in INDEX_SLEEVES]
    for symbol, table in tables:
        from sandbox import parquet_store as store
        raw_bars = store.read_bars(table, bar_minutes=1440)
        closes = [(datetime.fromtimestamp(int(row[0]), timezone.utc).strftime("%Y-%m-%d"),
                   float(row[4])) for row in raw_bars if row[4] is not None]
        schedules[symbol] = pv.multipliers(closes, **settings)
    return schedules


def main():
    # Declared up front: the argparse defaults below read these, and a `global`
    # after a read is a syntax error.
    global IS, OOS, FULL, INITIAL
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-exposure", action="store_true")
    parser.add_argument("--without-eth", action="store_true",
                        help="drop the two ETHUSD sleeves, for a before/after "
                             "against the book as it stood before they were added")
    parser.add_argument("--from", dest="start", default=FULL[0],
                        metavar="YYYY-MM-DD",
                        help="window start (default %(default)s)")
    parser.add_argument("--to", dest="end", default=FULL[1],
                        metavar="YYYY-MM-DD",
                        help="window end, exclusive (default %(default)s)")
    parser.add_argument("--split", default=IS[1], metavar="YYYY-MM-DD",
                        help="boundary between the reported IS and OOS windows "
                             "(default %(default)s)")
    parser.add_argument("--balance", type=float, default=INITIAL,
                        help="starting balance (default %(default)s). Raising it "
                             "does not simply scale the result: the lot step "
                             "stops erasing orders the account cannot afford, so "
                             "more trades fill and drawdown rises")
    parser.add_argument("--costs", choices=("pro", "zero"), default=COST_MODEL,
                        help="broker account the book is priced on (default "
                             "%(default)s, the live account). `zero` is the "
                             "retired account and prices the SAME book at -8%% "
                             "where `pro` gives +243%%, so this is not a tweak")
    args = parser.parse_args()
    INITIAL = args.balance
    # Before any order builder: the cost constants live in four modules'
    # registries and the builders read them as they go.
    apply_cost_model(args.costs)
    print(f"cost model: {COST_MODEL}  "
          f"(nq spread {NQ_SPREAD_POINTS:.3f} pts + {SLIPPAGE_POINTS} slippage "
          f"+ ${NQ_COMMISSION_PER_LOT:.2f}/lot commission)")

    # Widening the window is not the same as extending the book: a sleeve whose
    # data starts later simply contributes no orders before then, so the early
    # years are traded by whichever sleeves existed. `first trade` below reports
    # when each one actually joins, because a sleeve that silently contributes
    # nothing looks identical to one that is merely flat.
    FULL = (args.start, args.end)
    IS = (args.start, args.split)
    OOS = (args.split, args.end)

    orders = []
    for short, name in NQ_SLEEVES.items():
        got = nq_orders(short, name)
        print(f"  {'nq:' + short:<29} {len(got):>5} fills")
        orders += got
    got = drift_vwap_orders()
    print(f"  {DRIFT_VWAP_SLEEVE:<29} {len(got):>5} fills")
    orders += got
    if MAROY_ENABLED:
        got = maroy_orders()
        print(f"  {MAROY_SLEEVE:<29} {len(got):>5} fills")
        orders += got
    if not args.without_eth:
        for builder, sleeve in ((eth_maroy_orders, ETH_MAROY_SLEEVE),
                                (eth_drift_vwap_orders, ETH_DRIFT_SLEEVE)):
            got = builder()
            print(f"  {sleeve:<29} {len(got):>5} fills")
            orders += got
    book = list(CRYPTO_SLEEVES)
    got = crypto_orders(book)
    for symbol, family in book:
        n = sum(1 for o in got if o[0] == label(symbol, family))
        print(f"  {label(symbol, family):<29} {n:>5} fills")
    orders += got
    got = commodity_orders()
    for symbol, family in COMMODITY_SLEEVES:
        sleeve = f"{symbol}:{family}"
        n = sum(1 for order in got if order[0] == sleeve)
        print(f"  {sleeve:<29} {n:>5} fills")
    orders += got
    got = index_orders()
    for symbol, family in INDEX_SLEEVES:
        sleeve = f"{symbol}:{family}"
        n = sum(1 for order in got if order[0] == sleeve)
        print(f"  {sleeve:<29} {n:>5} fills")
    orders += got

    print("\nWHEN EACH SLEEVE JOINS (first order in the window):")
    joined = {}
    for order in orders:
        if order[0] not in joined or order[1] < joined[order[0]]:
            joined[order[0]] = order[1]
    for sleeve, first in sorted(joined.items(), key=lambda kv: kv[1]):
        moment = datetime.fromtimestamp(first, tz=timezone.utc)
        count = sum(1 for order in orders if order[0] == sleeve)
        print(f"  {sleeve:<26} {moment.strftime('%Y-%m-%d')}  {count:>6} orders")
    absent = sorted({o[0] for o in orders} ^ set(joined))
    if absent:
        print(f"  no orders at all: {', '.join(absent)}")

    schedule = None if args.no_exposure else exposure_schedule()
    print(f"\n${INITIAL:,.0f} shared account, "
          f"{'no overlay' if args.no_exposure else 'book exposure ' + json.dumps(EXPOSURE)}")
    # `initial` is passed explicitly: `combine` binds INITIAL as a default
    # argument at definition time, so rebinding the global from --balance never
    # reached it. Every run then sized off 400 while `summarise` scored the
    # result against the requested balance -- --balance 1000 read -55.56% over
    # Jan 2020 where the same orders truly return +7.90%.
    run = combine(orders, initial=INITIAL, exposure=schedule)
    # A sleeve whose orders are all under the broker's minimum contributes
    # nothing and looks exactly like one that had no signals. On a small account
    # this is the difference between "no edge" and "cannot afford to trade", so
    # it is reported rather than left to be inferred from a missing table row.
    dropped = run.get("below_broker_minimum") or {}
    if dropped:
        print("\nENTRIES BELOW THE BROKER'S MINIMUM ORDER SIZE (not filled):")
        placed = {}
        for trade in run["trades"]:
            placed[trade["sleeve"]] = placed.get(trade["sleeve"], 0) + 1
        for sleeve, count in sorted(dropped.items(), key=lambda kv: -kv[1]):
            took = placed.get(sleeve, 0)
            share = 100.0 * count / (count + took) if count + took else 0.0
            note = "  <-- sleeve never trades" if not took else ""
            print(f"  {sleeve:<26} {count:>6} dropped, {took:>6} filled "
                  f"({share:.0f}% unaffordable){note}")

    print("\nmarking open positions against 1m bars ...")
    opens = open_value(run["trades"])
    coverage = mark_coverage(run["trades"])
    print(f"  {coverage['trades_pct']:.1f}% of trades and "
          f"{coverage['pnl_pct']:.1f}% of gross P&L are markable"
          + (f"; unmarked: {', '.join(coverage['unmarked'])}"
             if coverage["unmarked"] else ""))

    header = (f"{'window':<12}{'return%':>10}{'closeDD%':>10}{'mtmDD%':>9}"
              f"{'mtmDD$':>11}{'PF':>7}{'+months':>10}{'trades':>8}")
    print("\n" + header)
    print("-" * len(header))
    windows = {}
    # Labelled from the dates in force, not hard-coded: the window is a command
    # line argument now, and a fixed "2025 IS" would misname every other run.
    periods = ((f"IS {IS[0][:7]}", IS), (f"OOS {OOS[0][:7]}", OOS),
               ("full", FULL))
    keys = {f"IS {IS[0][:7]}": "is", f"OOS {OOS[0][:7]}": "oos", "full": "full"}
    for window, (a, b) in periods:
        s = summarise(run, INITIAL, stamp(a), stamp(b), opens)
        if not s:
            continue
        windows[window] = s
        print(f"{window:<12}{s['return_pct']:>10,.2f}{s['max_dd_pct']:>10.2f}"
              f"{s['mtm_dd_pct']:>9.2f}{s['mtm_dd_usd']:>11,.2f}"
              f"{s['pf']:>7.2f}"
              f"{str(s['positive_months']) + '/' + str(s['n_months']):>10}"
              f"{s['trades']:>8}"
              f"   {s['mtm_dd_peak']} -> {s['mtm_dd_trough']}")

    tables = {name: sleeve_table(run, stamp(a), stamp(b))
              for name, (a, b) in periods}
    # Both drawdowns are shares of the shared account standing at the trough,
    # never dollars: this book compounds from 400 to nearly 9,000, so a dollar
    # giveback in 2025 and the same figure in 2026 are not the same risk.
    columns = (f"{'sleeve':<22}{'ret%book':>10}{'closeDD%':>10}{'mtmDD%':>9}"
               f"{'ret/DD':>8}{'PF':>7}{'win%':>7}{'trades':>8}")
    for window in ("full", *(name for name, _ in periods if name != "full")):
        print(f"\nBY SLEEVE -- {window}  ({FULL[0]} .. {FULL[1]} run)")
        print(columns)
        print("-" * len(columns))
        for name, s in sorted(tables[window].items(),
                              key=lambda kv: -(kv[1]["pct_of_book"] or 0.0)):
            closed = ("         -" if s["closed_dd_pct_of_account"] is None
                      else f"{s['closed_dd_pct_of_account']:>10.2f}")
            share = ("        -" if s["mtm_dd_pct_of_account"] is None
                     else f"{s['mtm_dd_pct_of_account']:>9.2f}")
            ratio = ("       -" if s["return_over_mtm_dd"] is None
                     else f"{s['return_over_mtm_dd']:>8.2f}")
            print(f"{name:<22}{s['pct_of_book']:>10.2f}{closed}{share}{ratio}"
                  f"{s['pf']:>7.2f}{s['win_pct']:>7.1f}{s['trades']:>8}")

    destination = os.path.join(cf.RESULTS, "combined_book.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"initial": INITIAL, "exposure": None if args.no_exposure
                   else EXPOSURE,
                   "drawdown_caveat": "max_dd_pct is closed-trade and understates "
                                      "risk; mtm_dd_pct re-marks open positions "
                                      "against 1m closes and is the figure to read",
                   "mark_coverage": coverage,
                   # Stable keys regardless of the dates, so a consumer does not
                   # have to parse a label; `window_dates` records what they mean.
                   "window_dates": {"is": IS, "oos": OOS, "full": FULL},
                   "by_sleeve": {keys[name]: table for name, table in tables.items()},
                   "windows": {keys[window]: summarise(run, INITIAL, stamp(a),
                                                       stamp(b), opens)
                               for window, (a, b) in periods}},
                  handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {destination}")


if __name__ == "__main__":
    main()
