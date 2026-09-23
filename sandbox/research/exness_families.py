"""One intraday family study for any Exness Pro symbol, taken as a parameter.

READ THIS FIRST IF YOU ARE AN AGENT RUNNING THIS MODULE.

**Terminal output does NOT reach the user.** Everything these commands print
goes to the agent's tool result, not to the person who asked. Running `select`,
`validate`, `why` or any comparison script and then discussing the numbers is
the same, from the user's side, as showing them nothing at all -- they see an
analysis of figures they have never been shown, over and over.

So when the user asks for results:

  1. PASTE THE NUMBERS INTO THE REPLY. A table in the chat message is the only
     thing they actually see. Do this the FIRST time they ask, not the fifth.
  2. Write the consolidated view to a file they can open --
     `results/FAMILY_STUDY_<date>.md` is the convention -- and give the full
     path. `results/*.json` is the raw form and is not readable by eye.
  3. Do not answer "show me the result" with commentary, caveats, or a plan for
     further validation. Numbers first, in the message. Interpretation after.

This cost an entire session once: the same comparison was run four times, the
user asked five times to see it, and every time the output went to the agent's
terminal instead of the reply.

WHAT THIS REPLACES.

Eight scripts ran the same protocol on different universes and disagreed only
about how to charge cost::

    commodity_families_research.py   16 metals/energy   pips + commission/lot
    index_families_research.py        7 index CFDs      points + bp
    crypto_families_research.py       8 crypto          bp + an FX leg
    stock_families_research.py       15 US stock CFDs   bp
    usoil_families_research.py        USOIL             flat $0.20
    xauusd_families_research.py       XAUUSD            flat $0.20
    btc_families_research.py          BTC               inherited from ES
    es_diverse_families_research.py   ES                5m session objects

Each one hardcoded its own instrument table, so adding a symbol meant editing
code and re-deriving contract metadata by hand.  Here the symbol is an argument
and everything about it is looked up:

    python -m sandbox.research.exness_families select --symbols ukoil,aus200
    python -m sandbox.research.exness_families select --symbols all

Nothing about a symbol is written down twice.  `resolve` builds its spec from
three sources, in this order:

    Parquet      which table backs it, what years are complete, and -- from the
                 traded volume profile -- where its liquid session sits.
    MT5 terminal contract size, tick size/value, lot floor and step, profit
                 currency, and the quoted spread.  Read live from the running
                 Exness Pro terminal, not transcribed.
    OVERRIDES    the short list a machine cannot infer: broker symbols whose
                 name differs from the table (`de40` is quoted `DE30`), the
                 Asian indices' day shift, and 365- versus 252-day calendars.

THE COST MODEL IS THE POINT.

Every result here is priced on Exness Pro account 416209807, and Pro's cost is
the spread alone.  That is measured, not assumed: round-tripping the minimum
BTCUSD lot on 2026-08-16 booked `commission 0.0000` on both the opening and the
closing deal, and the $0.06 round-trip on $630 of notional came to 0.95 bp
against a 1.111 bp quote.  Slippage was *positive* on both legs.

This inverts results rather than shading them.  `combined_book` records the same
book returning -8% priced on the Zero account and +243% on Pro, so a study that
charges Zero's commission on a Pro account is not conservative, it is wrong.

AND EVERY FILL IS THE ONE THE LIVE ACCOUNT GETS, NOT THE ONE THE BAR SAYS.

A backtest fill is three assumptions and the account honours none of them: the
entry is the next bar's open, the exit is the stop or the target to the digit,
and the cost is one median spread per symbol at every hour of every day. What
this module scores instead is the path `exness_live_execution` reads out of the
runtime, on every `select`, `validate` and `why`:

    spread   the broker's own quote in the minute the entry happened, from
             `exness_<broker>_1m`, and the constant only where that table has
             no row
    entry    the vendor open moved by what the BROKER did over the feed's
             publish lag plus the bridge queue -- a ratio, so the vendor/broker
             level difference cancels and only the delay is charged
    exit     a market order one WHOLE BAR plus that same lag and queue after
             the bar it fired on, because this account carries no broker-side
             stop: the payload `live_trade/src/live/mt5/bridge.rs` sends has
             nowhere to put one, so a stop is a level the runtime watches and
             can only act on when its candle rolls

THE EXIT LEG IS THE BIG ONE. Every other term is seconds; that one starts at a
whole bar, which at 30m is thirty minutes of unmanaged price after the stop was
already breached. `exness_live_execution cells` splits entry from exit if a
result needs attributing.

A SYMBOL WITH NO `exness_<broker>_1m` TABLE IS REFUSED IN `resolve`. Twenty-one
of the forty-eight have one. Scoring the rest on the idealised fill would put
two execution models in one sweep and rank the model rather than the rules, so
they are not scored at all until the table is imported.

SPREADS MUST BE SAMPLED IN SESSION.

A spot reading taken while a market is shut is the last quote before the close,
which is not a tradeable spread.  Measured here on 2026-08-16, a Sunday::

    hk50      5.859 bp at the weekend against 1.964 bp in session   3.0x
    fr40      4.858 bp against 0.79 bp                              6.1x
    xalusd   12.852 bp against 8.895 bp                             1.4x

so `spreads` records a provenance tag with every number and `resolve` refuses to
price a study on a `weekend_close` reading unless `--stale-spreads` says so.
Crypto is exempt: it trades continuously, and the readings taken that Sunday
matched `combined_book`'s independently measured in-session figures to within
1% (btc 1.111 vs 1.085, ethusd 3.723 vs 3.686, ethbtc 4.022 vs 4.066).

WHAT IS ACTUALLY RUNNABLE.

A symbol needs a Parquet table *and* a live broker symbol.  As of 2026-08-16
that is 26 of the 47 the eight scripts referenced:

    commodity  16   every one except usoil and xauusd, whose tables are gone
    index       7   all of them
    crypto      3   btc, ethusd, ethbtc
    stock       0   no table survives, and MU/PLTR/CVX/GS are not quoted at all

`coverage` prints that matrix rather than letting a study fail halfway in.

TIMEFRAMES, AND WHY RISK IS NOT MEASURED ON THE BAR.

`--bar-minutes` takes 15, 30, 60, 120, 240 or 1440, and 1440 is daily -- a
different regime rather than a coarser bar, so the session filters go away, every
family holds through the close, and the families that read a position WITHIN a
session are dropped instead of being handed a degenerate answer.

The `night` group runs the other way and is refused ABOVE 60m. It enters at the
open of a session's last fillable bar, so at 30m it is buying half an hour of
daylight plus the gap and at 240m it would be buying the afternoon and calling
the answer overnight. `NIGHT_BAR_MAX` is where that stops being a study of the
close-to-open leg.

THREE HOLDING REGIMES, AND MOST OF THE STUDY IS STILL IN THE FIRST.

    session    flattened at the close, at most one entry a day.  63 families
    swing      holds through the close, exits on a stop, target,  26 families
               trail or DAY count
    overnight  enters near a close, exits at an OPEN one to three  7 families
               sessions later; the exposure IS the gap

That table is the honest answer to "are these all intraday". Until the fourth
wave, sixty-three of seventy-five were flattened every evening and the twelve
that were not had been written as intraday rules that happened to be allowed to
hold -- so `--bar-minutes 1440` produced the same seventy-five rules on a
coarser bar rather than a daily study. `--groups held` is the selection that
survives a close, and `--groups fourth` is what was added to answer the
question.

THE OVERNIGHT REGIME'S STOP IS CHECKED AGAINST A WINDOW WITH NO BARS IN IT.
`context` filters the bar list down to in-session rows, so between one close and
the next open there is nothing for a stop test to see, and a naive overnight
backtest would report gap risk as free. `backtest` tests the stop against the
out-of-session high and low instead, at the first bar of every session a
position slept into. On DE40 that turns 382 of 1,296 single-night trades into
stops rather than open-to-open exits, which is the whole difference between
pricing the regime and flattering it.

Stop distance is a fraction of the average true DAILY range at every timeframe.
It used to be a multiple of bar ATR, which quietly tightened every stop as the
bar got finer and turned a resolution test into a comparison of two different
strategies ([[bar-size-confound-is-stop-distance]]). Measured on ETHUSD, bar ATR
runs 6.99 at 30m against 30.27 daily -- more than 4x -- while the daily anchor
holds at 34.80 and 34.31. Signal thresholds stay in bar ATR on purpose: reacting
to smaller moves is what a finer timeframe is FOR, and that is the effect being
measured.

ONE HUNDRED AND THIRTY FAMILIES, IN TWENTY-FOUR GROUPS.

The original twenty-nine read price against a reference window, an oscillator,
or the calendar. A hundred and one more were added because there were whole
classes of reading none of them could express, and each one had to answer the
same question before it was let in: NAME A BAR ON WHICH THIS FIRES AND NO
EXISTING FAMILY DOES, AND SAY WHY THE TWO DISAGREE. `GROUPS` is the taxonomy:

    core        the published nine plus the event study
    structure   today's shape against a reference window
    reversal    an extreme in an oscillator, faded
    calendar    a return attached to a date rather than to a price
    swing       the same entry, held past the close
    xma         does the CHOICE of average matter, or only the periods?
    regime      does the market tell you which of momentum or reversion to run?
    oscillator  the constructs a z-score and an RSI cannot reach
    geometry    are levels the market did not trade at still levels?
    flow        does cumulative volume say anything one bar cannot?
    relative    what is left of a name once its benchmark is divided out?
    gate        does a second, independent reading earn the trades it refuses?
    combo       do two theses fused into ONE rule beat either of them alone?
    night       is the close-to-open leg a different asset from the daytime one?
    almanac     does a date nobody chose carry a return?
    horizon     does anything survive being held for a quarter instead of a day?
    crossasset  what does the REGRESSION on a benchmark say that the ratio cannot?
    exogenous   does a series that is NOT a price say anything the traded one cannot?
    carry       the one thing financing can be tested as
    pathstat    what KIND of process is this -- its scaling law, the ORDER of its
                returns, its fourth moment, whether its signs are random
    micro       what the bar says about liquidity: which part of the variance was
                a jump, which side it came from, what the move cost
    filter      the things a moving average is not -- a bandpass, a
                distributional transform, an adaptive gain, a measured period
    adaptive    the rule sizes its own horizon, or its threshold accumulates
    fusion      the archetypes that actually passed, recombined so the result is
                not a subset of either parent

THE LAST FIVE GROUPS ARE THE SIXTH WAVE, AND THEY WERE DESIGNED BACKWARDS FROM
THE SCOREBOARD. Scored across 39 symbols at 30m, what passed its holdout and its
own coin-flip null clusters into a handful of archetypes and nothing else:
`volatility_breakout` on 14 of 20 symbols, `vol_regime` on 5 of 7, `swing_ma` on
9 of 13, `pullback` on 6 of 10, `floor_pivot` on 9 of 19, and the vote and level
families -- `confluence`, `level_confluence` -- carrying the largest single
returns. What lost is equally legible: `xma_slope` 1 of 14, `cross_timing` 1 of
12, `cmf` 0 of 4, `rsi` 0 of 3, and those are the families that read a SMOOTHED
PRICE and nothing else.

The reading is that the winners are statements about the STATE OF THE PROCESS --
is it trending, is it expanding, is anybody there, is this level real -- rather
than about the level of a smoothed price. `pathstat`, `micro`, `filter` and
`adaptive` are twenty-one more ways of asking that, each using a statistic the
module could not previously compute; `fusion` recombines the six that actually
passed. The whole wave costs 7.9 seconds of context build on a 60,000-bar series
and about 50,000 cells at 30m -- half again what `gate` spends on four families.

AND IT IS THE WAVE MOST IN NEED OF `why`. It was designed AFTER seeing which
families passed, so its priors are conditioned on the same holdout every earlier
wave was scored against ([[exness-survivor-pool-is-oos-conditioned]]). Its
coin-flip null and a window neither it nor its parents were chosen on are the
only things that can price it.

THE FOURTH-WAVE GROUPS ARE WHAT THE STUDY LOOKED LIKE FROM THE OUTSIDE WHEN
SOMEBODY ASKED FOR DAILY STRATEGIES, and the answer was uncomfortable: every
family it had was an intraday rule, and daily was a bar size rather than a
subject. Twenty-one more were added around the four things a DAY contains that
no window on a close series can reach.

`night` is the leg the module had never priced. A session return is the sum of
two exposures with different signs, different variances and different mechanisms
-- the gap taken with the market shut and no ability to react, and the daytime
move every other family here trades -- and the close series has already added
them together, so no rolling statistic on it can separate them. Seven families
hold nothing but the gap, in a third holding regime the engine did not have.

`almanac` is the dates `seasonality` and `turn_of_month` cannot name: an options
expiry that moves every month, an exchange closure, a quarter boundary carrying
the reconstitution flows a month boundary does not. The windows are a FIXED
list that was named in print before this study existed, because a family free to
choose its own start and end day would be `seasonality`'s twenty-four coin flips
raised to a power.

`horizon` is position scale. The longest lookback in the module was 252 sessions
and the longest hold fifteen, so a rule could look back a year and had to act on
the next three weeks -- which excludes every published construct about the next
quarter. Its seven read three things nothing else reads at all: the AGE of a
state rather than its level, a trailing return with its most recent month CUT
OUT, and a level fixed by the calendar rather than recomputed every bar.

`crossasset` regresses where `relative` divides. A ratio answers exactly one
question -- which of the two went up more -- so it cannot tell "index +2%, name
+0.4%" from "index -0.4%, name -2%", and it cannot see a beta that is not one,
which means on a high-beta name it calls every market rally outperformance.

ONE CANDIDATE FAILED THE ADMISSION TEST AND WAS DROPPED RATHER THAN SHIPPED. A
`weekend` family holding Friday's close to Monday's open is exactly `night_drift`
with `weekday=4` and `nights_1`, because the context's bar list has no weekend
rows and "one session later" is already Monday.

THE FOURTH WAVE IS CHEAP, WHICH IS THE POINT. On ETHUSD it adds 2,628 cells at
30m -- 104,292 to 106,920, +2.5% -- and 5,670 daily -- 46,908 to 52,578, +12% --
for twenty-one families, against `gate`'s 22,968 for four. None of the ninety-six that came before it changed -- not a
grid, not an axis, not a constant they read, which is why `NIGHT_EXIT_MODES` and
`POSITION_EXIT_MODES` are new vocabularies rather than additions to the existing
two. The forty-four sealed results stay reproducible.

THE LAST TWO GROUPS PAIR READINGS RATHER THAN ADDING THEM, and thirteen of them
had to survive an objection the first sixty-two never faced: the shared `common`
grid ALREADY gates every family in the study with an `ema_20d`/`ema_50d` trend
filter and a `calm` volatility filter. "ORB plus a moving average" is therefore
not a new strategy, it is a cell that has been scored since the first run, and
adding it as a family would be a way of counting one hypothesis twice while
calling the duplicate a discovery.

What was genuinely missing is every other thing a combination can mean. `gate`
runs four entries against eight confirmers that are NOT moving averages -- ADX
separates "trending" from "up", OBV reads participation instead of price, and
relative strength asks whether the move belongs to the name or to its index --
with a `polarity` axis, because "break the range while the slow oscillator is
still on the other side" is a real setup rather than the null of confirmation.
`combo` holds the nine that cannot be written as a filter at all, and those are
the ones that matter: a gated family's trades are always a SUBSET of the
ungated family's, so its result is bounded by it, while `two_stage` enters on
the breakout bar where `squeeze` entered on the compression bar three bars
earlier, at a different price and often on the other side.

    confluence        five readings vote; trade the margin, or fade the crowd
    two_stage         a coil arms it, the break of the coil's range takes it
    break_retest      the level breaks, price returns to it, and it holds
    regime_router     the regime picks WHICH rule runs, not which sign it takes
    nested            the same construct at two horizons, agreeing or clashing
    cross_timing      two oscillators crossing their midlines days apart
    level_confluence  two independently derived levels landing on one price
    trap              a breakout that CLOSED through and then closed back in
    idio_break        the name breaks out and the benchmark does not

The indicator math lives in `exness_indicators`, which is arithmetic on lists
and knows nothing about symbols, sessions or cost. Six moving averages there --
`sma`, `wma`, `lsma`, `hma`, `tema`, `kama` -- span a lag spectrum rather than
repeat one idea, and `xma_cross` takes the TYPE as an axis so "which average"
becomes a measured question instead of an assumption.

WHY THE STOCK SWEEP ONLY LIKED TSLA, AND WHAT WAS DONE ABOUT IT.

The 30m stock study passed 17 of 27 families on TSLA, 5 on AMD, 1 on JPM and 0
on ORCL, which reads as "TSLA is the tradeable one". It is not a fact about the
strategies. Risk per trade is fixed at `RISK_FRACTION`, and the stop is a
fraction of the daily range, so what decides everything is the ratio of spread
to range -- the share of each unit of risk handed to the broker before a rule
has done anything. At `stop_day = 0.4`, over the in-sample years (`cost`):

    symbol   spread bp   daily range bp   cost as a share of the stop
    tsla          2.41              485                          1.3%
    nvda          4.94              398                          3.2%
    amzn          4.09              264                          4.1%
    tsm           4.66              289                          4.2%
    aapl          3.91              220                          4.7%
    avgo          6.21              299                          5.4%
    msft          4.43              209                          5.5%
    googl         6.36              237                          6.9%
    jpm           6.07              217                          7.2%
    amd          13.48              416                          8.2%
    orcl         14.02              233                         15.2%

TSLA has the cheapest spread on the account AND the widest range, and the two
compound into a twelve-fold advantage over ORCL. Across the eleven, the cost
share and the number of families that passed rank against each other at
Spearman -0.79 -- cheaper risk, more passes -- with AMD, expensive but the
second most volatile, the only real exception. The three cheapest names took 40
of the 61 passes; the three dearest took 6.

`cost` prints that table, because the first thing to know about a cross-symbol
comparison is whether it is ranking edge or pricing.

The gates were NOT loosened per symbol -- a threshold that moves with the
instrument stops being a comparison. Instead the `relative` group divides each
name by its benchmark (`BENCHMARK`: the US names by NQ, JPM by ES, ETHUSD by
BTC). Eleven US large caps are mostly one index plus a remainder, so eleven
single-name studies are close to eleven noisy copies of one study -- and the
copy that wins a fixed-gate sweep is whichever kept the most of its gross edge.
On the ratio the shared factor cancels and what is left can genuinely differ
between names.

THE SEARCH BUDGET IS THE MAIN RISK NOW.

A hundred and thirty families is roughly 157,000 cells for one symbol at 30m and
76,000 daily -- 106,920 before the sixth wave, 76,356 before the combinations,
and the universe across seven timeframes runs past twenty million. Every cell is a draw, and the
best of N draws is selected by construction. A coin-flip search on this data has
already returned +622% at t=4.19 ([[coin-flip-control-beats-real-signals]]), and
crypto in particular has a strongly positive null
([[crypto-null-baseline-is-strongly-positive]]).

A COMBINATION STUDY IS THE ONE THAT INFLATES A BUDGET QUIETLY, which is why the
thirteen new families are two groups and not thirteen more entries in an
alphabetical list. Pairing multiplies two axis sets and then multiplies the
72-cell `common` grid on top, so `gate` alone is 32,940 cells -- the largest
group in the study, ahead of `xma` -- and the two together are +37%. Worse, the
gate vocabulary is an axis over EIGHT confirmers precisely so that "which
confirmer" is one selection with a stateable budget rather than the best of
eight separate winners, which is the same search wearing a smaller number.

So `budget` prints the number before a run and breaks it down by group, and
`--groups` exists so a sweep can take one line of enquiry at a time instead of
producing a winner whose selection budget nobody can state. A restricted `select`
writes to its own file and cannot overwrite a full sweep.

`why` -- the same search with entry directions replaced by coin flips -- is not
optional, and it matters more at seventy-five families than it did at
twenty-nine. Nothing here should be read as a result until it has beaten its own
null on the holdout. `time_of_day` and `day_of_week` exist partly as second,
cheaper controls: a breakout that cannot beat "just be long at 10:00" has not
shown anything -- and for the combinations there is a third, free one, because
`gated_*` is bounded by its own ungated family and `nested`'s `opposed` cells are
the control for its `aligned` cells inside the same grid.
"""
from __future__ import annotations

import argparse
import bisect
import gc
import traceback
import hashlib
import itertools
import json
import math
import multiprocessing
import os
import statistics
import time
from datetime import datetime, timezone

import numpy

from sandbox import data
from sandbox import parquet_store as store
from sandbox.research import es_strategy_research as es
from sandbox.research import exness_indicators as ind
from sandbox.research.usoil_families_research import (
    annual_detail, donchian_signal, exit_plan, ma_cross_signal,
    momentum_signal, overnight_signal, pdr_signal, random_side,
    rolling_mean_sigma, summarize, valid, vwap_signal, zscore_signal,
)

RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")
SPEC_PATH = os.path.join(RESULTS, "exness_pro_specs.json")
TS, O, H, L, C, V = range(6)

# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #

INITIAL_BALANCE = 1_000.0
RISK_FRACTION = 0.015
MARGIN_FRACTION = 0.25          # self-imposed 4x notional ceiling

IS_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
OOS_END = int(datetime(2026, 8, 16, tzinfo=timezone.utc).timestamp())
LAST_IS_YEAR = 2024

#: Selection gates. Deliberately identical across asset classes: the eight old
#: scripts used four different sets, which made their winners incomparable.
MIN_PROFIT_FACTOR = 1.05
MAX_DD = 20.0
ANNUAL_DD = 22.0
NEIGHBOUR_DD = 24.0
MIN_FILL_RATE = 70.0

#: Cost sweep in basis points of notional -- the one unit every asset class
#: shares. A fixed absolute spread ranks price level rather than edge: 0.2 points
#: is 80 bp on silver and 0.008 bp on DE40.
COST_SWEEP_BP = (0.0, 1.0, 3.0, 5.0, 10.0, 20.0, 30.0)

#: Execution allowance on top of the quoted spread, in bp, charged once at entry
#: with it. The BTCUSD probe filled 0.31 better than the ask going in and 0.59
#: better than the bid coming out, so this is a conservative allowance rather
#: than an observation. It is swept, so its influence is always visible.
SLIPPAGE_BP = 0.2

#: Share of a day's volume the inferred session must contain. Only used for a
#: symbol `SESSION` does not pin, and a session is a research choice about which
#: part of the day to trade rather than a fact in the data -- so `profile` prints
#: the distribution and the inferred window is a starting point, not an answer.
SESSION_VOLUME_SHARE = 0.70

#: Bar size the study runs on, in minutes. Set by `--bar-minutes` and passed
#: explicitly into every worker, because a spawned process re-imports this
#: module and would otherwise silently fall back to the default.
#:
#: Anything below 30 needs a 1m table; the native 30m series cannot be cut finer.
BAR_MINUTES = 30
#: 5m was measured and REMOVED rather than left as an option. On BTC its gross
#: edge per trade decayed to 13.23 bp against 16.08 at 30m, because the
#: ATR-scaled signal thresholds shrink with the bar and start firing on noise;
#: it produced more trades (1,035 against 739) and worse ones, and its family
#: pass rate fell to 17/27 from a peak of 21/27 at 15m. The usable band is
#: 30-60m, so the option is gone instead of being a trap that costs an hour.
BAR_CHOICES = (15, 30, 60, 120, 240, 1440)

#: 1440 means one bar a day, and daily is a different regime rather than a
#: coarser intraday one:
#:
#:   * there is no session to sit inside, so the session filters, the entry
#:     cutoffs and the flatten-at-the-close all stop meaning anything;
#:   * every family holds through the close by definition, so exits are counted
#:     in days;
#:   * families built on where price sits WITHIN a session -- opening range,
#:     initial balance, session VWAP, time of day -- have nothing to read and are
#:     excluded rather than quietly given a degenerate answer;
#:   * families that only exist across days -- turn of the month, seasonality,
#:     distance from a 52-week high, inside days -- become available.
#:
#: `Family.scope` records which side of that line each one lives on.
DAILY = 1440


def is_daily(bar=None):
    return (BAR_MINUTES if bar is None else bar) >= DAILY

#: RISK IS MEASURED ON THE DAY, NOT ON THE BAR, and this is the whole reason a
#: timeframe sweep here means anything.
#:
#: Stops used to be `stop_atr * ATR(bar)`. ATR of a 5-minute bar is a fraction of
#: ATR of a 4-hour bar, so moving to a finer timeframe silently tightened every
#: stop -- and a "resolution test" then compared two different strategies and
#: reported the difference as an effect of resolution
#: ([[bar-size-confound-is-stop-distance]]).
#:
#: So stop distance is now a fraction of the AVERAGE DAILY RANGE, which does not
#: know what bar size it is being asked about. Signal thresholds stay in bar-ATR
#: on purpose: reacting to smaller moves is what a finer timeframe is FOR, and
#: that is the effect being measured. Risk is held constant so it cannot be
#: mistaken for that effect.
STOP_DAY = (0.2, 0.4, 0.7)
DAILY_RANGE_BARS = 14

#: The two edges `quantile_channel` is built at, as an axis value.
#:
#: 0.98 is very nearly the Donchian extreme and exists as the control: if the
#: robust channel only works at 0.98, what it found is `donchian` and the
#: robustness was not the reason. 0.90 discards a tenth of the window, which on
#: a 20-session channel is two full sessions of the most extreme prints.
QUANTILE_EDGES = (0.90, 0.98)

#: Bands for `supertrend`, precomputed because the construct is STATEFUL --
#: its band ratchets, so it cannot be derived inside a signal from an ATR
#: reading at one index. Two multiples: one that flips on a normal pullback and
#: one that only flips on a real reversal.
SUPERTREND_MULTIPLES = (2.0, 3.0)

#: The averages `xma_slope` and `xma_ribbon` sweep -- the two ends of the lag
#: spectrum plus the adaptive one. Named here rather than written into each
#: family, because `extra_context` has to build exactly this set and no more:
#: when the two lists drifted apart it built six kinds over every period either
#: family could ask for, a third of them unreachable by any cell.
XMA_SHAPE_KINDS = ("sma", "hma", "kama")

#: Wilder's own defaults for the parabolic SAR. Not swept: the acceleration is
#: the construct rather than a knob, and sweeping it would turn one hypothesis
#: into a grid over how fast to chase -- which is what the trailing exit modes
#: already measure, on a shared axis, for every family at once.
SAR_STEP = 0.02
SAR_CAP = 0.2

EXIT_MODES = ("rr_1", "rr_2", "time_4", "trail_1.5")

#: Exits for the `live` group: a bar COUNT, every one of them.
#:
#: THIS IS THE ONE EXIT THE TWO MODELS AGREE ON. A stop, a target and a trail
#: are all levels, and this account holds none of them at the broker -- the MT5
#: payload has nowhere to put one -- so the runtime watches the level and can
#: only act when its candle rolls, which prices the exit a whole bar plus a lag
#: late (3.73 bp median, 12.49 bp at p90 on usdjpy). A `time_N` exit is decided
#: by the same candle roll the backtest books it on, so the model error
#: collapses to the feed lag, which lands inside the same minute bar.
#:
#: Four horizons rather than one, because "how long does the edge last" is the
#: question a clock exit asks and a single count would answer it by assumption.
#:
#: `days_1` IS THE LIMIT CASE AND THE MOST INTERESTING ONE. It resolves to one
#: SESSION of bars, and under `SESSION_ONLY` the position is flattened at the
#: close regardless -- so the cell holds from entry to the bell and exits on the
#: one event the runtime and this loop schedule identically. It is the only
#: exit in the study with no model error at all.
LIVE_EXIT_MODES = ("time_2", "time_4", "time_8", "days_1")
#: Swing families carry positions through the session close, so a bar-count time
#: exit is meaningless to them and they get day counts instead.
SWING_EXIT_MODES = ("rr_2", "rr_3", "days_5", "days_15", "trail_2.5")

#: The overnight regime counts SESSIONS SLEPT, not bars or days.
#:
#: A night family enters near one session's close and leaves at the open of a
#: later one, so the only honest unit for its holding period is "how many opens
#: away". `days_N` cannot express it -- `days_1` is a whole session of bars at
#: 30m and a single bar daily -- and `time_N` is worse, because the context's bar
#: list has the out-of-session buckets filtered out, so "one bar later" already
#: means "after the night" at some timeframes and "still this afternoon" at
#: others. `nights_1` is close-to-open at every bar size the regime allows.
#:
#: THREE VALUES AND NO TARGET, DELIBERATELY. The thesis of every family in the
#: `night` group is the close-to-open return itself; handing the grid an `rr_2`
#: target would let the search quietly replace that thesis with an intraday one
#: on the far side of the gap and report the result under the wrong name.
NIGHT_EXIT_MODES = ("nights_1", "nights_2", "nights_3")

#: The position-trading regime. `SWING_EXIT_MODES` tops out at fifteen sessions,
#: which is three weeks -- fine for a channel break and far too short for a
#: twelve-month momentum reading, whose whole claim is about the next quarter.
#:
#: KEPT SEPARATE RATHER THAN ADDED TO THE SWING GRID. Appending `days_60` to
#: `SWING_EXIT_MODES` would widen the grid of every swing family already
#: published and make the forty-four sealed results irreproducible for the sake
#: of a horizon none of them was making a claim about.
POSITION_EXIT_MODES = ("rr_3", "days_20", "days_60", "trail_2.5")

#: Coarsest bar an overnight family may run on.
#:
#: The engine fills at the NEXT bar's open and refuses a fill once the clock has
#: passed the session close, so the latest entry any rule can reach is the open
#: of the session's last-but-one bar. At 30m that is half an hour of daylight
#: before the night being traded; at 240m it is most of the afternoon, and a
#: "close-to-open" study would then be measuring the afternoon. So the regime is
#: refused above 60m rather than being allowed to mean something different at
#: each timeframe.
NIGHT_BAR_MAX = 60

#: Pre-registered seasonal windows, from the literature and NOT searched.
#:
#: `seasonality` already exists as a countable overfitting hazard -- twelve
#: months by two directions is twenty-four coin flips and one of them looks
#: excellent on any series. A family that swept arbitrary start/end date pairs
#: would be that hazard raised to a power, so this is a FIXED list of windows
#: that were named in print before this study existed, and adding one is a
#: deliberate act rather than a widening of a search.
#:
#: `(month, day)` inclusive bounds; a window whose start is after its end wraps
#: through the new year.
SEASONAL_WINDOWS = {
    "halloween": ((11, 1), (4, 30)),     # Bouman & Jacobsen's "sell in May"
    "summer": ((5, 1), (10, 31)),        # its complement, so the pair is one test
    "santa": ((12, 20), (1, 3)),         # the turn-of-year window
    "january": ((1, 1), (1, 31)),        # the January effect, stated plainly
    "september": ((9, 1), (9, 30)),      # the one month with a negative folklore
    "midsummer": ((7, 1), (8, 31)),      # the thin-liquidity stretch
}

#: Axes whose values are labels rather than points on a scale, so the
#: neighbour-robustness test steps over them instead of treating "fade" as
#: adjacent to "follow".
#:
#: A NUMERIC-LOOKING AXIS THAT IS NOT ORDERED BELONGS HERE TOO. `macd_set` is a
#: `(fast, slow, signal)` triple and `ribbon` is a ladder of periods; stepping
#: "one along" either of them is not a small perturbation of anything, so the
#: robustness test would be measuring a different strategy and calling the
#: original fragile.
CATEGORICAL = ("direction", "vol_mode", "trend", "exit_mode", "fast", "slow",
               "side", "location", "reference", "anchor",
               "kind", "fast_kind", "slow_kind", "macd_set", "regime", "level",
               "zone", "mode", "wick", "against",
               # The combination axes. Every one of these names a CHOICE OF
               # CONSTRUCT rather than a point on a scale -- `adx` is not
               # adjacent to `obv`, and `confirm` is the negation of `oppose`
               # rather than one step from it -- so the neighbour test steps over
               # them. `window`, `votes`, `tolerance` and `channel` are genuine
               # scales and are deliberately NOT here.
               "gate", "polarity", "trigger", "pool", "coil", "retest",
               "strictness", "construct", "agreement", "pair",
               # The fourth wave. `weekday` mixes `"any"` with integers on
               # purpose and is not a scale either way -- Tuesday is not one
               # step from Monday in any sense a robustness test could read.
               # `nights`, `count`, `depth`, `age` and `threshold` ARE scales
               # and are deliberately absent.
               # `season` is a label; `window` was one too until the fifth
               # wave needed it as a VIX lookback in sessions, so the almanac
               # axis was renamed rather than left to mean two things.
               "weekday", "season", "unit", "when", "check", "state", "zone",
               "bias",
               # The sixth wave. `band` is a `(highpass, lowpass)` PAIR and
               # `estimator` and `evidence` name constructs -- Parkinson is not
               # one step from Garman-Klass. Everything else the wave adds is a
               # genuine scale and is deliberately absent: `threshold`, `share`,
               # `ratio`, `depth`, `response`, `multiple`, `dispersion`,
               # `amplitude_atr`, `order`, `lag`, `wick_ratio` and `band`'s
               # numeric cousins are all points on a line, and the neighbour
               # test has to be able to step along them or the whole wave
               # reports `1/1` and has never been perturbed.
               "band", "estimator", "evidence")


# --------------------------------------------------------------------------- #
# per-symbol overrides
#
# Everything a lookup cannot infer, and nothing else. A symbol absent from this
# table is resolved entirely from the Parquet store and the terminal.
# --------------------------------------------------------------------------- #

#: Broker symbol where it differs from the Parquet table name.
#: `es` has no Exness symbol at all -- the account quotes the S&P as the US500
#: cash CFD, not the future. Pricing es_1m bars off US500's spread assumes the
#: two cost the same to trade, which is the closest available approximation and
#: not a measurement.
BROKER_ALIAS = {"de40": "DE30", "btc": "BTCUSD", "nq": "USTEC", "es": "US500"}

#: Trading days a year, for annualising realised volatility. Crypto is the only
#: family that quotes through the weekend.
CALENDAR = {"btc": 365.0, "ethusd": 365.0, "ethbtc": 365.0}

#: Hours added to the timestamp before the day is cut. The Asian cash sessions
#: straddle midnight New York, and without the shift one session lands in two
#: calendar days, which breaks every prior-day anchor.
SHIFT_HOURS = {"aus200": 6, "hk50": 6, "jp225": 6}

#: Sessions pinned by hand, minutes past midnight New York, `(open, close)`.
#: `derive_session` infers the rest from the volume profile; these are the ones
#: where a study already established the window and the inference should not be
#: allowed to drift away from a published result.
SESSION = {
    "xagusd": (8 * 60, 16 * 60),
    "xcuusd": (8 * 60, 14 * 60 + 30),
    "xngusd": (9 * 60, 14 * 60 + 30),
    "xpdusd": (8 * 60, 16 * 60),
    "xptusd": (8 * 60, 16 * 60),
    "xalusd": (3 * 60, 14 * 60),
    "xniusd": (3 * 60, 14 * 60),
    "xznusd": (3 * 60, 14 * 60),
    "ukoil": (9 * 60, 14 * 60 + 30),
    #: WTI pit hours, the window [[usoil-1m-is-new-york-clock]] established.
    "usoil": (9 * 60, 14 * 60 + 30),
    "xauaud": (8 * 60, 16 * 60), "xaueur": (8 * 60, 16 * 60),
    "xaugbp": (8 * 60, 16 * 60), "xagaud": (8 * 60, 16 * 60),
    "xageur": (8 * 60, 16 * 60), "xaggbp": (8 * 60, 16 * 60),
    "de40": (3 * 60, 11 * 60 + 30), "fr40": (3 * 60, 11 * 60 + 30),
    "stoxx50": (3 * 60, 11 * 60 + 30), "uk100": (3 * 60, 11 * 60 + 30),
    "aus200": (60, 8 * 60), "hk50": (3 * 60, 10 * 60), "jp225": (60, 8 * 60),
    #: US cash hours. `ustec` is the Nasdaq 100 CFD Exness actually quotes,
    #: as opposed to `nq`, whose table is the back-adjusted futures continuum
    #: ([[nq-has-two-incompatible-price-series]]).
    "ustec": (9 * 60 + 30, 16 * 60),
    #: `us500` is to `es` what `ustec` is to `nq`, and the pairing is exact.
    #: `es_1m` is the back-adjusted CME continuum -- a ratio-adjusted level
    #: nobody traded ([[es-bars-are-back-adjusted]]) -- and it stopped updating
    #: on 2026-06-24. `us500_1m` is Dukascopy's S&P 500 CFD at real index
    #: levels, imported 2026-09-20, and it is the series the broker's own
    #: `exness_us500_1m` prices: 1,333 overlapping minutes on 2026-09-17 agree
    #: to a median -0.107%, a constant offset rather than a different market.
    "us500": (9 * 60 + 30, 16 * 60),
    "btc": (9 * 60 + 30, 16 * 60), "ethusd": (9 * 60 + 30, 16 * 60),
    "ethbtc": (9 * 60 + 30, 16 * 60),
}

#: Prefer the native 30m table where both exist. The metal crosses have a
#: Dukascopy 1m archive that skips 2015-2023 and an Exness 30m series that is
#: continuous from 2021-07; the broker's own bars win.
PREFER_30M = {"xauaud", "xaueur", "xaugbp", "xagaud", "xageur", "xaggbp"}

#: The series a symbol is measured AGAINST, for the `rel_*` families.
#:
#: THIS IS THE ANSWER TO "THE STOCK STUDY ONLY LIKES TSLA", and it is worth
#: being explicit about why. A US large cap is mostly its index: on 30m bars the
#: market factor is the largest single driver of every name here, so eleven
#: stock studies are close to eleven noisy copies of one index study plus an
#: idiosyncratic remainder. Whichever name has the best cost-to-range ratio wins
#: that comparison -- and `cost` shows that is TSLA by a factor of eight -- so
#: the ranking is reporting the spread, not the strategy.
#:
#: Dividing by the benchmark removes the shared factor and leaves the
#: remainder, which is the part that could plausibly differ between names. The
#: ratio is itself a price series, so every existing construct -- a channel, a
#: z-score, a moving average -- applies to it unchanged.
#:
#: Only pairs where the benchmark is genuinely the symbol's dominant factor are
#: listed. Pairing, say, XAGUSD with an equity index would produce a ratio that
#: is neither instrument and mean nothing.
BENCHMARK = {}
for _s in ("aapl", "amd", "amzn", "avgo", "googl", "msft", "nvda", "orcl",
           "tsla", "tsm"):
    BENCHMARK[_s] = "nq"
#: JPM is a bank, not a Nasdaq name; the S&P is the closer factor, and `es` is
#: the only broad US index with a table here.
BENCHMARK["jpm"] = "es"
#: ETHUSD against BTC is the one non-equity pair where the benchmark really is
#: the dominant factor. ETHBTC is already that ratio and so is excluded -- it
#: would be divided by its own denominator.
BENCHMARK["ethusd"] = "btc"

#: Symbols for which VIX is the right volatility factor: the US equity complex
#: and nothing else.
#:
#: DELIBERATELY NOT A UNIVERSAL GATE. VIX is thirty-day implied volatility on the
#: S&P 500. Against NQ or a US large cap it is a factor reading -- the same
#: sellers, the same risk premium, mechanically linked through index options.
#: Against XAUAUD or ETHBTC it is a correlated macro series at best, and a
#: family that read it there would be testing "does US equity fear predict the
#: gold-Aussie cross", which is a different and much weaker claim wearing the
#: same name. The `exogenous` families are dropped for any symbol absent here.
VIX_FACTOR = frozenset((
    "nq", "ustec", "es", "aapl", "amd", "amzn", "avgo", "googl", "jpm", "msft", "nvda",
    "orcl", "tsla", "tsm",
))

#: Carried forward so a resolved spec can warn about the same things the old
#: registries did.
WARNING = {
    "xcuusd": "stored quote scale (2.8-6.6) differs from live MT5 (~14,000); "
              "do not deploy until the symbol conversion is reconciled",
    "de40": "Exness quotes this symbol as DE30, not DE40",
}

CLASS = {}
for _s in ("xagusd", "xcuusd", "xngusd", "xpdusd", "xptusd", "xalusd", "xniusd",
           "xznusd", "ukoil", "usoil", "xauaud", "xaueur", "xaugbp", "xagaud",
           "xageur", "xaggbp"):
    CLASS[_s] = "commodity"
for _s in ("de40", "fr40", "stoxx50", "uk100", "aus200", "hk50", "jp225",
           "ustec", "us500"):
    CLASS[_s] = "index"
for _s in ("btc", "ethusd", "ethbtc"):
    CLASS[_s] = "crypto"
#: US stock CFDs. Dukascopy 1m bars, split-adjusted -- NVDA reads $120 in 2024
#: when it traded $1,200 -- so no rule here may use a dollar threshold. CVX, GS,
#: MU and PLTR have tables but are NOT quoted on this Exness account, so they
#: cannot be priced and are excluded.
for _s in ("aapl", "amd", "amzn", "avgo", "googl", "jpm", "msft", "nvda",
           "orcl", "tsla", "tsm"):
    CLASS[_s] = "stock"
#: Index futures, back-adjusted continuous series. Levels are a ratio-adjusted
#: continuum rather than real prices, so historical point costs are distorted.
for _s in ("nq", "es"):
    CLASS[_s] = "future"
#: FX majors and JPY crosses, chosen on spread against the move they offer
#: rather than on spread alone -- EURGBP is cheaper than AUDJPY in bp and ranks
#: far worse, because it moves 4.5bp an hour against AUDJPY's 10.4
#: ([[absolute-spread-inverts-the-symbol-ranking]]). Backed by Dukascopy
#: 1-minute bid bars, 2015-2026.
for _s in ("eurusd", "usdjpy", "gbpusd", "audjpy", "audusd", "eurjpy",
           "gbpjpy", "usdcad"):
    CLASS[_s] = "forex"

#: The universe `--symbols all` expands to. Membership is not an assertion that
#: a symbol is runnable -- `coverage` decides that against live data.
UNIVERSE = tuple(sorted(CLASS))


# --------------------------------------------------------------------------- #
# terminal: contract specs and spreads
# --------------------------------------------------------------------------- #

def _mt5():
    """The MetaTrader5 module, connected, or a clear failure.

    Imported lazily so that selection and validation runs -- which read only the
    sealed spec snapshot -- work on a machine with no terminal attached.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError as error:            # pragma: no cover - environment
        raise SystemExit("MetaTrader5 is not installed; run `specs` on the "
                         "machine running the terminal") from error
    if not mt5.initialize():
        raise SystemExit(f"cannot reach the MT5 terminal: {mt5.last_error()}")
    return mt5


def broker_symbol(symbol):
    return BROKER_ALIAS.get(symbol, symbol.upper())


def read_specs(symbols=UNIVERSE, path=SPEC_PATH):
    """Contract metadata and a spot spread per symbol, straight off the terminal.

    `multiplier` is ACCOUNT currency -- USD on this account -- per unit of price
    per lot, i.e. `tick_value / tick_size`; it is what turns a price move into
    money. MT5 reports `trade_tick_value` already converted into the account
    currency, so `multiplier` needs no FX conversion and must never be multiplied
    by `fx_to_usd`: doing so converts twice and is a 159x error on a JPY cross.
    Verify against `contract_size * tick_size`, which is the tick in
    `currency_profit`: for XAUAUD that is 0.1 AUD while `tick_value` stores
    0.070841, the USD figure.

    `fx_to_usd` is still needed where a value really is in the profit currency --
    `quantity` uses it on `price * contract_size` to get notional in USD.

    The spread is tagged `session` or `weekend_close` from the age of the tick
    that produced it. A quote that has not moved for hours is the last one before
    the close, and for the index CFDs that is several times the tradeable spread.
    """
    mt5 = _mt5()
    account = mt5.account_info()
    now = int(time.time())
    for symbol in symbols:
        mt5.symbol_select(broker_symbol(symbol), True)
    time.sleep(2)               # let the server push a first tick for each

    out = {}
    for symbol in symbols:
        name = broker_symbol(symbol)
        info = mt5.symbol_info(name)
        if info is None:
            out[symbol] = {"broker": name, "error": "not quoted on this account"}
            continue
        tick = mt5.symbol_info_tick(name)
        bid, ask = (tick.bid, tick.ask) if tick else (0.0, 0.0)
        mid = (bid + ask) / 2.0 if bid and ask else 0.0
        age = now - tick.time if tick and tick.time else None
        out[symbol] = {
            "broker": name,
            "digits": info.digits,
            "point": info.point,
            "tick_size": info.trade_tick_size,
            "tick_value": info.trade_tick_value,
            "multiplier": (info.trade_tick_value / info.trade_tick_size
                           if info.trade_tick_size else 0.0),
            "contract_size": info.trade_contract_size,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "volume_max": info.volume_max,
            "currency_profit": info.currency_profit,
            #: OVERNIGHT FINANCING, WHICH THE STUDY IGNORED ENTIRELY UNTIL THE
            #: FOURTH WAVE. `swap_mode` 1 is SYMBOL_SWAP_MODE_POINTS, which is
            #: what this account uses on every symbol: the charge is a number of
            #: POINTS per lot per night, so `swap_long * point` is a price
            #: distance and slots into the engine beside `cost_price` with no
            #: unit conversion at all.
            #:
            #: `swap_rollover3days` is the weekday charged triple. It is
            #: recorded and deliberately NOT used -- see `financing_price`.
            "swap_mode": info.swap_mode,
            "swap_long": info.swap_long,
            "swap_short": info.swap_short,
            "swap_rollover3days": info.swap_rollover3days,
            "price": mid,
            "spread_bp": round(1e4 * (ask - bid) / mid, 4) if mid else None,
            "spread_price": round(ask - bid, 10) if mid else None,
            "tick_age_s": age,
            "quoted_at": ("session" if age is not None and age < 900
                          else "weekend_close"),
        }
    #: Merged into whatever is already on disk rather than replacing it, so
    #: reading one symbol during its session does not discard the other
    #: twenty-four -- which would silently throw away every in-session spread
    #: sampled on an earlier day.
    existing = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            existing = json.load(handle)
    symbols = dict(existing.get("symbols") or {})
    symbols.update(out)
    rates = dict(existing.get("fx_to_usd") or {})
    rates.update(_read_fx(mt5, out))
    return {
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": {"login": account.login, "name": account.name,
                    "server": account.server, "currency": account.currency,
                    "balance": account.balance},
        "cost_model": "pro: spread only, commission measured at 0.0000",
        "fx_to_usd": rates,
        "symbols": symbols,
    }


def _read_fx(mt5, specs):
    """USD per unit of every profit currency the universe quotes in.

    A snapshot, not a series. The rate cancels between risk sizing and P&L --
    both scale with it -- and survives only in lot rounding and the margin
    ceiling, which is why one reading is enough and why a cross's `multiplier`
    is a convenience rather than a constant.
    """
    wanted = {row.get("currency_profit") for row in specs.values()}
    rates = {"USD": 1.0}
    for currency in sorted(c for c in wanted if c and c != "USD"):
        for name, invert in ((f"{currency}USD", False), (f"USD{currency}", True)):
            mt5.symbol_select(name, True)
            tick = mt5.symbol_info_tick(name)
            if tick and tick.bid:
                rates[currency] = 1.0 / tick.bid if invert else tick.bid
                break
        else:
            rates[currency] = None
    return rates


def read_swaps(symbols=UNIVERSE, path=SPEC_PATH):
    """Merge ONLY the financing fields into the snapshot on disk.

    SEPARATE FROM `read_specs` BECAUSE `read_specs` IS DESTRUCTIVE OUT OF HOURS.
    It replaces each symbol's whole entry, including the spread and its
    `quoted_at` tag -- so running it on a Saturday to pick up a swap number
    would overwrite twenty-five in-session spreads with weekend quotes, and
    `resolve` would then refuse every symbol until they were all re-sampled.
    Swaps are contract terms rather than quotes: they do not go stale over a
    weekend, so they can be read at any time and merged on their own.

    Returns `(snapshot, missing)` where `missing` names symbols the terminal
    would not quote.
    """
    mt5 = _mt5()
    snapshot = load_specs()
    symbols_out = dict(snapshot.get("symbols") or {})
    missing = []
    for symbol in symbols:
        name = broker_symbol(symbol)
        mt5.symbol_select(name, True)
        info = mt5.symbol_info(name)
        entry = symbols_out.get(symbol)
        if info is None or entry is None or "error" in entry:
            missing.append(symbol)
            continue
        entry.update({
            "swap_mode": info.swap_mode,
            "swap_long": info.swap_long,
            "swap_short": info.swap_short,
            "swap_rollover3days": info.swap_rollover3days,
            # `point` was never carried before and `financing_price` needs it to
            # turn a points quote into a price distance.
            "point": info.point,
        })
        symbols_out[symbol] = entry
    snapshot["symbols"] = symbols_out
    snapshot["swaps_read_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    return snapshot, missing


def report_swaps(symbols):
    """Financing per night beside the spread, in the one unit that compares them.

    The whole argument for the second cost leg is in this table: on the index
    CFDs a single night costs several times the entire round-trip spread, and
    the study was charging the spread alone.
    """
    print(f"{'symbol':9}{'mode':>5}{'spread bp':>11}{'long bp':>10}"
          f"{'short bp':>10}{'worst/night':>13}{'20 nights':>11}{'60 nights':>11}")
    for symbol in symbols:
        spec = INSTRUMENTS.get(symbol)
        if spec is None:
            continue
        price = spec.get("reference_price") or 0.0
        notional = price * spec["contract_size"]
        if not notional:
            print(f"{symbol:9}{'-':>5}{'no reference price':>56}")
            continue

        def bp(side):
            return 1e4 * financing_price(symbol, side) * spec["multiplier"] / notional

        long_bp, short_bp = bp(1), bp(-1)
        worst = max(long_bp, short_bp)
        spread = spec.get("spread_bp") or 0.0
        mode = spec.get("swap_mode")
        print(f"{symbol:9}{('-' if mode is None else mode):>5}{spread:>11.3f}"
              f"{long_bp:>10.3f}{short_bp:>10.3f}"
              f"{worst / spread if spread else 0:>12.1f}x"
              f"{20 * worst:>11.1f}{60 * worst:>11.1f}")
    print("\n  bp of notional. `worst/night` is financing against the whole "
          "round-trip spread,\n  per night -- above 1.0x a single night costs "
          "more than the trade's entire\n  modelled cost did before this leg "
          "existed.")


def save_specs(snapshot, path=SPEC_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


_SPECS = None


def load_specs(path=SPEC_PATH):
    """The sealed snapshot, read once per process."""
    global _SPECS
    if _SPECS is None:
        if not os.path.exists(path):
            raise SystemExit(
                f"no spec snapshot at {path}; run `specs` against the terminal "
                "before selecting")
        with open(path, encoding="utf-8") as handle:
            _SPECS = json.load(handle)
    return _SPECS


def tick_spreads(symbols, days=7.0, path=SPEC_PATH, quiet=False):
    """Median in-session spread per symbol, from the terminal's TICK HISTORY.

    This replaces both `spreads` (polls live ticks, useless when the market is
    shut) and `watch` (blocks for hours waiting for one to open).
    `copy_ticks_range` serves stored bid/ask on a Sunday, so the study can be
    priced correctly at any time
    ([[mt5-tick-history-gives-spreads-without-waiting]]).

    It is also a better measurement than either. A live poll samples whatever
    ten minutes happened to be running; this spans days and is restricted to the
    hours the strategy actually trades, which matters because an overnight quote
    on a CFD is several times the in-session one and would drag the median the
    same way the weekend reading did.

    TIMEZONES. `tick.time` is a UTC epoch on this server -- verified against
    `time.time()`, which agreed to within seconds. `SESSION` is in New York
    wall-clock minutes, and `SHIFT_HOURS` is added the same way `all_bars` adds
    it, so a tick is tested against exactly the window `in_session` would use.
    DST is handled by `zoneinfo` rather than a fixed offset, because a window
    sampled across a transition would otherwise move by an hour.
    """
    from zoneinfo import ZoneInfo

    mt5 = _mt5()
    new_york = ZoneInfo("America/New_York")
    stop = datetime.now(timezone.utc)
    start = stop - __import__("datetime").timedelta(days=days)

    snapshot = load_specs(path)
    for symbol in symbols:
        mt5.symbol_select(broker_symbol(symbol), True)
    time.sleep(1)

    if not quiet:
        print(f"{'symbol':9}{'session bp':>12}{'all-hours bp':>14}"
              f"{'ticks':>10}{'in-session':>12}  window")
    for symbol in symbols:
        row = snapshot["symbols"].get(symbol)
        if row is None:
            continue
        ticks = mt5.copy_ticks_range(broker_symbol(symbol), start, stop,
                                     mt5.COPY_TICKS_INFO)
        if ticks is None or not len(ticks):
            if not quiet:
                print(f"{symbol:9}{'no tick history':>12}  {mt5.last_error()}")
            continue

        opened, closed = SESSION.get(symbol, (0, 1440))
        shift = SHIFT_HOURS.get(symbol, 0) * 60
        every, inside = [], []
        for tick in ticks:
            bid, ask = float(tick["bid"]), float(tick["ask"])
            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            mid = (bid + ask) / 2.0
            spread = 1e4 * (ask - bid) / mid
            every.append(spread)
            moment = datetime.fromtimestamp(int(tick["time"]), tz=timezone.utc)
            local = moment.astimezone(new_york)
            minute = (local.hour * 60 + local.minute + shift) % 1440
            if opened <= minute <= closed:
                inside.append(spread)
        if not every:
            continue

        # The session median where there is one, else the all-hours median.
        # Falling back rather than skipping keeps a symbol priced; the count is
        # printed so a thin session is visible rather than implied.
        chosen = inside if len(inside) >= 100 else every
        row["spread_bp"] = round(statistics.median(chosen), 4)
        row["spread_samples"] = len(chosen)
        row["spread_all_hours_bp"] = round(statistics.median(every), 4)
        row["quoted_at"] = "session"
        row["spread_source"] = (
            f"tick history {start:%Y-%m-%d}..{stop:%Y-%m-%d}, "
            + ("session-filtered" if chosen is inside else "all hours"))
        row["sampled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not quiet:
            print(f"{symbol:9}{row['spread_bp']:>12.4f}"
                  f"{row['spread_all_hours_bp']:>14.4f}{len(every):>10,}"
                  f"{len(inside):>12,}  "
                  f"{opened//60:02d}:{opened%60:02d}-{closed//60:02d}:{closed%60:02d}"
                  + ("" if chosen is inside else "  (all-hours fallback)"))
    save_specs(snapshot, path)
    return snapshot


#: A tick older than this is the last quote before a close, not a live market.
#: 900s is generous for an illiquid CFD mid-session and still an order of
#: magnitude below the ~35 hours a weekend produces.
FRESH_TICK_SECONDS = 900

#: Ticks a symbol needs before its median is trusted. Below this the median is
#: one or two prints and no better than the spot reading it replaces.
MIN_SPREAD_SAMPLES = 30


def watch_spreads(symbols, hours=18.0, min_samples=MIN_SPREAD_SAMPLES,
                  interval=5.0, path=SPEC_PATH, quiet=False):
    """Wait for each market to OPEN, then record its median in-session spread.

    `sample_spreads` polls for a fixed window and reports whatever it saw, which
    works only if every symbol is already trading. The universe here is not:
    crypto runs continuously, the European indices open around 07:00 UTC, the
    US metals later, and energy reopens Sunday evening. A single ten-minute
    window catches whichever markets happen to be live and silently leaves the
    rest on their weekend quote -- which is how a whole study came to be priced
    on spreads 1.4-6x too wide.

    So each symbol is tracked INDEPENDENTLY and to its own sample count: the
    loop keeps running until every symbol has `min_samples` live ticks or the
    deadline passes. A symbol whose market never opens is reported as still
    stale rather than being averaged from nothing.

    Only ticks whose timestamp actually MOVED are counted. A shut market
    re-serves its last quote forever, and counting those would manufacture a
    confident median from a single dead print.
    """
    mt5 = _mt5()
    for symbol in symbols:
        mt5.symbol_select(broker_symbol(symbol), True)
    readings = {symbol: [] for symbol in symbols}
    seen = {}
    deadline = time.time() + hours * 3600.0
    announced = set()

    while time.time() < deadline:
        if all(len(readings[s]) >= min_samples for s in symbols):
            break
        now = int(time.time())
        for symbol in symbols:
            if len(readings[symbol]) >= min_samples:
                continue
            tick = mt5.symbol_info_tick(broker_symbol(symbol))
            if not tick or not tick.bid or not tick.ask:
                continue
            # Two independent staleness tests. `time_msc` not moving catches a
            # market that is shut now; the age test catches one that was shut
            # when this started and whose first served quote is Friday's.
            if seen.get(symbol) == tick.time_msc:
                continue
            seen[symbol] = tick.time_msc
            if tick.time and now - tick.time > FRESH_TICK_SECONDS:
                continue
            mid = (tick.bid + tick.ask) / 2.0
            if mid > 0:
                readings[symbol].append(1e4 * (tick.ask - tick.bid) / mid)
                if symbol not in announced and not quiet:
                    announced.add(symbol)
                    print(f"{symbol:9} opened, collecting", flush=True)
        time.sleep(interval)

    snapshot = load_specs(path)
    fresh = stale = 0
    for symbol in symbols:
        values = readings[symbol]
        row = snapshot["symbols"].get(symbol)
        if row is None:
            continue
        if len(values) < min_samples:
            stale += 1
            if not quiet:
                print(f"{symbol:9} only {len(values)} live ticks -- left as "
                      f"{row.get('quoted_at')} at {row.get('spread_bp')} bp")
            continue
        fresh += 1
        row["spread_bp"] = round(statistics.median(values), 4)
        row["spread_samples"] = len(values)
        row["quoted_at"] = "session"
        row["sampled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not quiet:
            print(f"{symbol:9} {row['spread_bp']:8.3f} bp  "
                  f"median of {len(values)}", flush=True)
    save_specs(snapshot, path)
    print(f"\n{fresh} symbols now in-session, {stale} still stale")
    return snapshot


def sample_spreads(symbols, minutes=10.0, interval=2.0, path=SPEC_PATH):
    """Poll the book and record the MEDIAN spread per symbol, in bp.

    This is the honest way to price the study and the only way to get an
    in-session number for a market that is shut when `specs` runs. Run it while
    the symbols are trading; the median is taken over live ticks only, so a
    symbol whose quote never moves is reported as stale rather than averaged in.
    """
    mt5 = _mt5()
    for symbol in symbols:
        mt5.symbol_select(broker_symbol(symbol), True)
    readings = {symbol: [] for symbol in symbols}
    deadline = time.time() + minutes * 60.0
    seen = {}
    while time.time() < deadline:
        for symbol in symbols:
            tick = mt5.symbol_info_tick(broker_symbol(symbol))
            if not tick or not tick.bid or not tick.ask:
                continue
            stamp = tick.time_msc
            if seen.get(symbol) == stamp:
                continue            # quote has not moved; not a new observation
            seen[symbol] = stamp
            mid = (tick.bid + tick.ask) / 2.0
            if mid > 0:
                readings[symbol].append(1e4 * (tick.ask - tick.bid) / mid)
        time.sleep(interval)

    snapshot = load_specs(path)
    for symbol, values in readings.items():
        row = snapshot["symbols"].get(symbol)
        if row is None or not values:
            print(f"{symbol:9} no live ticks -- left as "
                  f"{row and row.get('quoted_at')}")
            continue
        row["spread_bp"] = round(statistics.median(values), 4)
        row["spread_samples"] = len(values)
        row["quoted_at"] = "session"
        row["sampled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"{symbol:9} {row['spread_bp']:8.3f} bp  median of {len(values)}")
    save_specs(snapshot, path)
    return snapshot


# --------------------------------------------------------------------------- #
# data: table discovery, coverage, session inference
# --------------------------------------------------------------------------- #

_TABLES = None


def tables():
    """Every table in the Parquet store. There is no other source any more."""
    global _TABLES
    if _TABLES is None:
        _TABLES = store.tables()
    return _TABLES


def table_for(symbol):
    """`(table, source)` for `symbol`, preferring the broker's own 30m bars."""
    minute, half = f"{symbol}_1m", f"{symbol}_30m"
    have = tables()
    if symbol in PREFER_30M and half in have:
        return half, "30m"
    if minute in have:
        return minute, "1m"
    if half in have:
        return half, "30m"
    return None, None


def coverage(symbol):
    """What data exists, and which years are complete enough to select on."""
    table, source = table_for(symbol)
    if table is None:
        return {"symbol": symbol, "table": None, "runnable": False,
                "reason": "no data table"}
    span = store.bounds(table)
    if span is None:
        return {"symbol": symbol, "table": table, "runnable": False,
                "reason": "table has no rows"}
    first, last = span
    #: A year counts as complete only if the table starts before it does. The
    #: partial first year is warm-up, never a selection year.
    start_year = int(first[:4]) + (0 if first[5:] <= "01-07" else 1)
    return {"symbol": symbol, "table": table, "source": source,
            "first_row": first, "last_row": last,
            "first_full_year": max(start_year, 2018), "runnable": True}


def derive_session(symbol, table, source):
    """The liquid window, inferred from the traded volume profile.

    Takes the busiest 30-minute buckets of a recent year until they hold 80% of
    its volume, then spans first to last. The span therefore covers at least
    that 80% and usually more, since it fills any quiet buckets caught between
    two busy ones -- which is what makes it a session rather than a mask.

    Used only where `SESSION` does not pin the symbol, so a published result
    never moves because a volume profile shifted.
    """
    buckets = _half_hour_volume(symbol, table)
    total = sum(buckets)
    if total <= 0:
        raise SystemExit(f"{symbol}: no 2024 volume to infer a session from")

    # The SHORTEST contiguous run of buckets holding `SESSION_VOLUME_SHARE` of
    # the day's volume, searched circularly so a window may cross midnight.
    best = None
    for start in range(48):
        run = 0.0
        for length in range(1, 49):
            run += buckets[(start + length - 1) % 48]
            if run >= SESSION_VOLUME_SHARE * total:
                if best is None or length < best[0]:
                    best = (length, start)
                break
    if best is None:
        raise SystemExit(f"{symbol}: volume profile has no {SESSION_VOLUME_SHARE:.0%} window")
    length, start = best
    return start * 30, ((start + length - 1) % 48) * 30


def volume_profile(symbol):
    """`(buckets, shift)` -- 2024 volume in each of the day's 48 half hours."""
    have = coverage(symbol)
    if not have["runnable"]:
        raise SystemExit(f"{symbol}: {have['reason']}")
    return _half_hour_volume(symbol, have["table"]), have


def _half_hour_volume(symbol, table):
    """2024 traded volume in each of the day's 48 half hours, shift applied.

    ONE IMPLEMENTATION FOR BOTH CALLERS. `derive_session` and `volume_profile`
    each carried their own copy of this, and a copy is how the two came to
    disagree about which volume column a table has -- the rule was "30m means
    `tick_volume`", which is right for the MT5 exports and wrong for `es_30m`.
    The store reads the column the file actually contains.
    """
    buckets = [0.0] * 48
    for bar in store.read_bars(table, bar_minutes=30, start="2024-01-01",
                               end="2025-01-01",
                               shift_hours=SHIFT_HOURS.get(symbol, 0)):
        buckets[bar[TS] % 86_400 // 1_800] += bar[V]
    return buckets


def report_profile(symbol):
    """Where a symbol actually trades, so a session can be chosen rather than
    guessed. Choosing which part of the day to trade is a research decision; the
    inferred window is only where the volume is densest."""
    buckets, have = volume_profile(symbol)
    total = sum(buckets) or 1.0
    peak = max(buckets) or 1.0
    pinned = SESSION.get(symbol)
    derived = derive_session(symbol, have["table"], have["source"])
    print(f"\n{symbol}  {have['table']}  2024 volume by half hour"
          + (f"  (shifted {SHIFT_HOURS[symbol]}h)" if symbol in SHIFT_HOURS else ""))
    for index, value in enumerate(buckets):
        minute = index * 30
        mark = ""
        if pinned and pinned[0] <= minute <= pinned[1]:
            mark += " pinned"
        if derived[0] <= minute <= derived[1]:
            mark += " derived"
        print(f"  {minute//60:02d}:{minute%60:02d}  "
              f"{'#' * int(40 * value / peak):<40} {100*value/total:5.2f}%{mark}")
    print(f"  pinned  {pinned[0]//60:02d}:{pinned[0]%60:02d}-"
          f"{pinned[1]//60:02d}:{pinned[1]%60:02d}" if pinned else "  pinned  none")
    print(f"  derived {derived[0]//60:02d}:{derived[0]%60:02d}-"
          f"{derived[1]//60:02d}:{derived[1]%60:02d} "
          f"(smallest window holding {SESSION_VOLUME_SHARE:.0%})")


def all_bars(symbol, phase, bar=None):
    """Every `bar`-minute bucket, oldest first, out-of-session buckets included.

    The overnight and gap anchors are built from the out-of-session buckets, so
    they are kept here and filtered out of the traded list in `context`. In
    `select` phase the range itself cannot return a holdout row.

    One rollup serves both sources. On a native 30m table asking for 30m is a
    no-op aggregation and asking for 60m or more re-buckets correctly; asking
    for less is refused rather than silently interpolated.
    """
    spec = INSTRUMENTS[symbol]
    bar = BAR_MINUTES if bar is None else bar
    if is_daily(bar):
        return daily_bars(symbol, phase)
    if spec["source"] == "30m" and bar < 30:
        raise SystemExit(
            f"{symbol}: {spec['table']} is native 30m and cannot be cut to "
            f"{bar}m -- pick 30 or coarser, or use a symbol with a 1m table")
    table, shift = spec["table"], spec["shift_hours"]
    upper = "2025-01-01" if phase == "select" else None
    volume = store.volume_column(table, spec.get("volume_column"))
    key = (f"bars:v2:{table}:{bar}m:{spec['warmup']}:{upper}:{shift}:{volume}:"
           f"{data._table_fingerprint([table])}")

    def build():
        return store.read_bars(table, bar_minutes=bar, start=spec["warmup"],
                               end=upper, shift_hours=shift, volume_col=volume)

    scope = "is" if phase == "select" else "full"
    return [tuple(row) for row in
            data._cached(f"families_{symbol}_{bar}m_{scope}", key, build)]


def daily_bars(symbol, phase):
    """One bar a day, aggregated from the SESSION rather than the calendar day.

    These are CFDs that quote around the clock, so a raw `SAMPLE BY 1d` would
    open each day in thin overnight trade and hand every gap and open-based
    family a price nobody traded size at. Building the day out of the session
    buckets instead makes the daily open the session open and the daily close the
    session close -- the prices a daily trader on this instrument actually sees.

    It also keeps the Asian indices honest: `SHIFT_HOURS` is applied to the 30m
    buckets before they are grouped, so one cash session lands in one day instead
    of being cut in half by midnight New York.
    """
    grouped = {}
    for bar in all_bars(symbol, phase, 30):
        if not in_session(symbol, bar[TS]):
            continue
        day = bar[TS] // 86_400
        row = grouped.get(day)
        if row is None:
            grouped[day] = [day * 86_400, bar[O], bar[H], bar[L], bar[C], bar[V]]
        else:
            row[H] = max(row[H], bar[H])
            row[L] = min(row[L], bar[L])
            row[C] = bar[C]
            row[V] += bar[V]
    return [tuple(grouped[day]) for day in sorted(grouped)]


# --------------------------------------------------------------------------- #
# resolution: a symbol becomes a spec
# --------------------------------------------------------------------------- #

#: Collapse every holding regime to a single RTH: enter and exit inside one
#: session, never hold through a close. Set by operator instruction 2026-08-29
#: -- "one day, one rth".
#:
#: It is a switch on WHAT IS BEING TESTED, not a parameter. The 33 `swing` and 7
#: `overnight` families were selected while they could hold overnight, so under
#: this flag every one of them is an unselected cell. `backtest` is the only
#: place it is read, and daily bars are unaffected because they have no session
#: to flatten against.
SESSION_ONLY = True

INSTRUMENTS = {}


def register_series(name):
    """Install a DATA-ONLY spec for `name`, enough to load its bars and no more.

    A benchmark is read, never traded, so it needs a table, a session and a
    timestamp shift -- and none of the contract metadata, spread or FX rate that
    `resolve` demands and that would make a study fail because the reference
    series happens not to be quoted on this account. `es` is exactly that case:
    it has no Exness symbol at all, and it is JPM's benchmark.

    A real spec always wins. If the benchmark is also being studied, `resolve`
    has already put the full entry in `INSTRUMENTS` and this leaves it alone.
    """
    if name in INSTRUMENTS:
        return INSTRUMENTS[name]
    have = coverage(name)
    if not have["runnable"]:
        raise SystemExit(f"{name}: cannot be used as a benchmark -- {have['reason']}")
    session = SESSION.get(name) or derive_session(name, have["table"],
                                                  have["source"])
    INSTRUMENTS[name] = {
        "symbol": name, "asset_class": CLASS.get(name, "other"),
        "table": have["table"], "source": have["source"],
        "warmup": f"{max(int(have['first_row'][:4]), 2011)}-01-01",
        "first_full_year": have["first_full_year"],
        "first_row": have["first_row"], "last_row": have["last_row"],
        "session": tuple(session), "shift_hours": SHIFT_HOURS.get(name, 0),
        "calendar": CALENDAR.get(name, 252.0), "reference_only": True,
    }
    return INSTRUMENTS[name]


def have_vix():
    """Whether the VIX series exists at all.

    IT DOES NOT, ON THIS STORE. `vix_1d` was a QuestDB table and was never
    exported to Parquet, so every `exogenous` family is unrunnable rather than
    wrong. `families_for` refuses them on this, which is the same refusal path a
    symbol outside `VIX_FACTOR` takes -- so they are reported as not selected
    instead of quietly scoring against a series of zeros.
    """
    return store.has_table("vix_1d")


def vix_closes(symbol, phase, bars):
    """VIX daily closes carried onto `bars`' timestamps, or `None`.

    `vix_1d` RATHER THAN `vix_1h`, WHICH WOULD BE THE ONLY OTHER CHOICE. The
    hourly table started 2024-07-24 and the daily one 2017-01-03, and this
    study's in-sample window opens in 2018 -- so the hourly series would supply
    nothing at all for the selection phase and then appear, mid-holdout, as a
    step from no readings to readings.

    Aligned with `ind.align`, which takes the last close at or before each bar
    and never interpolates -- so a bar sees yesterday's VIX print until today's
    has landed, and the holdout cut in `phase` is respected because the range is
    cut, not the alignment.
    """
    if symbol not in VIX_FACTOR or not have_vix():
        return None
    upper = "2025-01-01" if phase == "select" else None
    key = f"vix:v2:{upper}:{data._table_fingerprint(['vix_1d'])}"

    def build():
        return [row for row in store.read_bars("vix_1d", bar_minutes=1440,
                                               end=upper)
                if row[C] > 0]

    rows = [tuple(row) for row in
            data._cached(f"families_vix_1d_{phase}", key, build)]
    return ind.align(bars, rows) if rows else None


def benchmark_closes(symbol, phase, bars, bar=None):
    """The benchmark's close carried onto `bars`' timestamps, or `None`.

    Alignment is by timestamp with `ind.align`, which takes the last benchmark
    bar at or before each symbol bar. Both series bucket on the same grid, so
    intraday that is the benchmark bar that CLOSES at the same instant as the
    symbol's -- and since every entry fills at the next bar's open, reading it
    is not lookahead. Daily is the same statement one bar coarser: both closes
    belong to session date D and the fill is D+1's open.

    A benchmark whose table is missing is a hard failure rather than a silent
    `None`: the alternative is a `rel_*` family quietly producing no signals and
    being reported as "no cell passed".
    """
    name = BENCHMARK.get(symbol)
    if name is None:
        return None
    register_series(name)
    return ind.align(bars, all_bars(name, phase, bar))


def resolve(symbol, allow_stale=False, specs=None):
    """Everything the engine needs about `symbol`, assembled from live sources.

    Raises rather than guesses. A symbol with no table, no broker quote, or only
    a weekend spread is refused here instead of silently producing a number.
    """
    snapshot = specs or load_specs()
    spec = snapshot["symbols"].get(symbol)
    if spec is None:
        raise SystemExit(f"{symbol}: absent from the spec snapshot; re-run `specs`")
    if "error" in spec:
        raise SystemExit(f"{symbol}: {spec['error']}")

    have = coverage(symbol)
    if not have["runnable"]:
        raise SystemExit(f"{symbol}: {have['reason']}")

    # THE BROKER'S OWN MINUTE TABLE IS NOT OPTIONAL, because it is what every
    # fill in this study is priced from: the spread in the minute the entry
    # happened, the entry moved by the feed's publish lag, and the exit as a
    # late market order (`live_fills`). Without it there is nothing to price
    # against and the only alternative is the idealised bar fill.
    #
    # REFUSED RATHER THAN SCORED ON THE OLD MODEL. A sweep that silently mixed
    # the two would rank the execution model instead of the rules -- an
    # uncovered symbol would keep a stop filled to the digit while a covered
    # one paid a whole bar for the same stop, and the ranking would be the
    # coverage map. Twenty-one of the forty-eight have the table.
    if not has_broker_minutes(symbol):
        raise SystemExit(
            f"{symbol}: no {broker_minute_table(symbol)} in the store, so its "
            f"fills cannot be priced the way the account fills them. Import "
            f"the broker's minute bars for {broker_symbol(symbol)} first; "
            f"this study does not score a symbol on the idealised bar fill.")

    quoted_at = spec.get("quoted_at")
    if quoted_at != "session" and not allow_stale:
        raise SystemExit(
            f"{symbol}: spread was read at {quoted_at}, which is the last quote "
            "before the close and not tradeable. Run `spreads` while the market "
            "is open, or pass --stale-spreads to accept it knowingly.")

    currency = spec.get("currency_profit") or "USD"
    fx = (snapshot.get("fx_to_usd") or {}).get(currency)
    if fx is None:
        raise SystemExit(f"{symbol}: no USD rate for {currency}")

    session = SESSION.get(symbol) or derive_session(
        symbol, have["table"], have["source"])
    warmup = f"{max(int(have['first_row'][:4]), 2011)}-01-01"

    resolved = {
        "symbol": symbol, "asset_class": CLASS.get(symbol, "other"),
        "broker": spec["broker"], "table": have["table"],
        "source": have["source"], "warmup": warmup,
        "first_full_year": have["first_full_year"],
        "first_row": have["first_row"], "last_row": have["last_row"],
        "session": tuple(session), "shift_hours": SHIFT_HOURS.get(symbol, 0),
        "calendar": CALENDAR.get(symbol, 252.0),
        "multiplier": spec["multiplier"], "contract_size": spec["contract_size"],
        "tick_size": spec["tick_size"], "tick_value": spec["tick_value"],
        "volume_min": spec["volume_min"], "volume_step": spec["volume_step"],
        "volume_max": spec["volume_max"],
        "currency_profit": currency, "fx_to_usd": fx,
        # Financing. `point` and the swap fields arrive from `swaps`; a snapshot
        # taken before that command existed has neither, and `financing_price`
        # reads `swap_mode is not 1` and charges nothing -- which reproduces the
        # old behaviour exactly rather than crashing or guessing.
        "point": spec.get("point"),
        "swap_mode": spec.get("swap_mode"),
        "swap_long": spec.get("swap_long"),
        "swap_short": spec.get("swap_short"),
        "swap_rollover3days": spec.get("swap_rollover3days"),
        "spread_bp": spec["spread_bp"], "spread_quoted_at": quoted_at,
        "reference_price": spec.get("price"),
        "warning": WARNING.get(symbol),
    }
    INSTRUMENTS[symbol] = resolved
    return resolved


def is_years(symbol):
    first = INSTRUMENTS[symbol]["first_full_year"]
    return tuple(year for year in range(first, LAST_IS_YEAR + 1))


def min_positive_years(symbol):
    years = is_years(symbol)
    return len(years) if len(years) <= 3 else len(years) - 1


def is_start(symbol):
    return int(datetime(is_years(symbol)[0], 1, 1,
                        tzinfo=timezone.utc).timestamp())


def periods(symbol, bar=None):
    """Every lookback the study uses, expressed in BARS of the current size.

    All of them are defined as a number of sessions and then converted, so
    "20-day EMA" stays a 20-day EMA whether that is 20 bars daily or 480 bars at
    5 minutes. A lookback written as a fixed bar count would silently become a
    different amount of history at every timeframe.
    """
    opened, closed = INSTRUMENTS[symbol]["session"]
    per_session = (1 if is_daily(bar)
                   else max(2, (closed - opened) // (bar or BAR_MINUTES) + 1))
    return {
        "session": per_session, "atr": 2 * per_session,
        "vol": 20 * per_session, "long_vol": 100 * per_session,
        "trend": {"ema_20d": 20 * per_session, "ema_50d": 50 * per_session},
        "zscore": (per_session, 2 * per_session, 5 * per_session),
        "fast": (per_session, 2 * per_session),
        "slow": (5 * per_session, 10 * per_session, 20 * per_session),
        # 252 sessions is the real 52 weeks. 55 is a quarter, and using it for
        # `high_52w` would have named the family after a year and handed it
        # eleven weeks.
        "channels": (per_session, 2 * per_session, 4 * per_session,
                     10 * per_session, 20 * per_session, 55 * per_session,
                     252 * per_session),
        "year": 252 * per_session,
        # 14 sessions is Wilder's default and 2 sessions is the short
        # mean-reversion variant. Written in sessions so daily gets (2, 14)
        # rather than the (1, 2) a bar-count would have collapsed to.
        "rsi": (max(2, 2 * per_session), max(3, 14 * per_session)),
        "volume": (5 * per_session, 20 * per_session),
        "compress": (5 * per_session, 10 * per_session, 20 * per_session),
        "swing": {"days_5": 5 * per_session, "days_15": 15 * per_session},

        # ---- lookbacks for the families added in the second wave ------------
        # Same rule as everything above: written in SESSIONS and converted, so a
        # "14-day ADX" is 14 days at every bar size rather than 14 bars that
        # mean two hours at 30m and three weeks daily. Each tuple is kept to two
        # values wherever one short and one long reading answer the question,
        # because these are multiplied by the shared 72-cell `common` grid and a
        # third value costs 144 cells per own-axis combination.
        "xma_fast": (per_session, 2 * per_session),
        "xma_slow": (5 * per_session, 20 * per_session),
        # A ladder rather than an axis: `xma_ribbon` reads the SPREAD of all
        # five at once, so they are one setting, not five.
        "ribbon": tuple(m * per_session for m in (1, 2, 4, 8, 16)),
        "stoch": (per_session, 5 * per_session),
        "stoch_smooth": max(2, per_session // 2),
        "cci": (2 * per_session, 10 * per_session),
        # 12/26/9 sessions is the textbook setting in this study's units; the
        # short triple is the same shape compressed into a single session, which
        # is what a 30m trader would actually watch.
        "macd": ((max(2, per_session // 2), per_session, max(2, per_session // 3)),
                 (12 * per_session, 26 * per_session, 9 * per_session)),
        "adx": (max(4, 2 * per_session), max(6, 14 * per_session)),
        "aroon": (5 * per_session, 20 * per_session),
        # The SAME periods as `rsi`, deliberately. The whole thesis of `mfi` is
        # that weighting by money rather than by price picks different bars out
        # of the identical construction, and running the two at different
        # lookbacks would confound that with a horizon difference. A 14-session
        # MFI at a 75 threshold also barely fires -- 8 signals in eleven
        # thousand bars -- because the flow sums smooth far harder than the
        # price sums do, so the thresholds sit lower than `rsi`'s as well.
        "mfi": (max(2, 2 * per_session), max(3, 14 * per_session)),
        "cmf": (5 * per_session, 20 * per_session),
        # OBV is a cumulative line, so it is read through a channel on itself
        # rather than through a level. Longer than the price channels on
        # purpose: a divergence needs both series to have made a swing.
        "obv": (10 * per_session, 40 * per_session),
        "linreg": (2 * per_session, 10 * per_session),
        "er": (2 * per_session, 10 * per_session),
        # The variance ratio needs a horizon AND a sample to measure it over.
        # The window is fixed at 100 sessions so the step is the only axis --
        # sweeping both would make a regime reading a two-dimensional search.
        "vr_step": (per_session, 5 * per_session),
        "vr_window": 100 * per_session,
        "skew": (20 * per_session,),
        # A fractal wing in BARS, not sessions: it is a shape in the bar series
        # and a 14-bar wing at 30m would need a full session either side before
        # a pivot could confirm, which is not a swing point, it is a channel.
        "wing": (2, 3, 5),
        "rel": (per_session, 5 * per_session, 20 * per_session),

        # ---- the fourth wave: the night, the almanac, the horizon -----------
        # SESSION COUNTS, LEFT UNCONVERTED, and that is a deliberate exception
        # to the rule every line above obeys.
        #
        # Everything else here is written in sessions and multiplied into bars
        # because the family indexes a bar array. These five are read by
        # families that run on DAILY bars only -- where the two units are the
        # same number -- or by the night families, which index a table keyed by
        # SESSION rather than by bar. Multiplying them would turn "the last
        # twelve months" into twelve months of half-hours, which is three weeks.
        #
        # `axes` enforces the other half of the deal by refusing to build these
        # families anywhere the assumption does not hold.
        "nights": (20, 60),
        # A quarter, a half and a year. The three horizons the time-series
        # momentum literature is written in, and none of them reachable by
        # `p["channels"]`, which stops at 55 sessions before jumping to 252.
        "horizon": (63, 126, 252),
        # The month that 12-1 momentum SKIPS. One value: the construct is "skip
        # the reversal month", and sweeping the skip length would turn a stated
        # hypothesis into a search for whichever gap happened to work.
        "skip": (21,),
        # Faber's ten-month filter in sessions, and its half.
        "faber": (100, 200),
        # The windows a peak and its age are measured over: a quarter, a year.
        "peak": (63, 252),
        # Turtle-scale channels. `swing_donchian` stops at 55 sessions, so the
        # entire months-scale breakout literature -- the thing most people mean
        # by "trend following" -- was unreachable at any timeframe.
        "long_channel": (100, 200, 252),
        # The three horizons a trend-agreement rule votes over. The SAME numbers
        # as `horizon` on purpose: `multi_horizon_trend` is `tsmom`'s ensemble,
        # and voting over different lookbacks than the single-horizon family
        # uses would make the comparison between them meaningless.
        "vote": (63, 126, 252),
        # Long-horizon reversal. Two and four years rather than the literature's
        # three-to-five, because the stock tables begin 2019-12-31 with no
        # warm-up at all: a five-year lookback would not produce its first
        # reading until 2025, which is outside the in-sample window entirely.
        # Even at two years a stock cell is blind until 2022.
        "reversal": (504, 1008),
        # VIX windows, in sessions: a fortnight and a quarter.
        "vix": (10, 63),
        # THE REGRESSION WINDOW, AND IT IS NOT `rel`. `crossasset` estimates a
        # correlation and a beta rather than reading a ratio, and an estimate
        # needs a sample: `p["rel"]` starts at ONE SESSION, which on a daily bar
        # is a single return and cannot produce a covariance at all. Measured on
        # ETHUSD against BTC, the daily one-session cell returned no readings
        # for the entire history -- a third of the group's budget spent on cells
        # that could never fire, and three neighbours the robustness test would
        # have counted as failures for the wrong reason. A month and a quarter
        # of returns are the shortest windows worth regressing.
        "pair": (20 * per_session, 60 * per_session),

        # ---- the sixth wave: path statistics, microstructure, filters ------
        # EVERY ONE CARRIES A FLOOR IN BARS, and that is the exception this wave
        # needs. The rule above is "write it in sessions and convert", which on
        # a daily bar makes `5 * per_session` equal to five. Five bars is a
        # legal moving-average period and is NOT a legal sample for a scaling
        # exponent, an entropy, a runs test or a fourth moment -- those return
        # `None` for the entire history at that length, which the grid would
        # spend budget on and `select` would report as "no cell passed".
        #
        # So each of these is `max(floor_in_bars, sessions * per_session)`: the
        # session reading wherever there is enough of it, and the shortest
        # window the statistic is defined on otherwise. The floors are the
        # estimators' own requirements, not preferences -- `hurst_exponent`
        # needs four times its longest lag, `permutation_entropy` needs enough
        # bars to fill a six-bin histogram, `tail_ratio` needs five returns in
        # each tail.
        "hurst": (max(64, 10 * per_session), max(128, 40 * per_session)),
        "entropy": (max(40, 5 * per_session), max(120, 20 * per_session)),
        # One value, exactly like `skew`. The fourth moment is a slow reading
        # and two lookbacks of it would be two noisy copies of one statement.
        "kurtosis": (max(120, 20 * per_session),),
        "autocorr": (max(60, 5 * per_session), max(160, 20 * per_session)),
        # LAGS ONE AND TWO ONLY. Lag 1 is "does the last bar continue" and lag 2
        # is the first lag at which a bid-ask bounce has decayed, so the pair is
        # a real question. Past that the estimate's standard error swamps the
        # coefficient at any window a trading rule can afford.
        "lags": (1, 2),
        "runs": (max(40, 5 * per_session), max(120, 20 * per_session)),
        "kendall": (max(30, 2 * per_session), max(100, 10 * per_session)),
        "tails": (max(150, 20 * per_session),),
        "jump": (max(30, 2 * per_session), max(100, 10 * per_session)),
        "semi": (max(40, 5 * per_session), max(120, 20 * per_session)),
        # SHORT AND LONG, AND THE FAMILY READS THE RATIO. Amihud's level is in
        # whatever volume unit the table happens to carry -- a tick count on one
        # feed and a contract count on another -- so a threshold on it would
        # rank feeds rather than markets. The short window against the long one
        # is dimensionless and needs no extra array to normalise it.
        "amihud": (max(20, 5 * per_session), max(120, 20 * per_session)),
        "estimator": (max(20, 5 * per_session), max(120, 20 * per_session)),
        "bulk": (max(10, per_session), max(40, 5 * per_session)),
        # `(high_period, low_period)` pairs for the roofing filter, in bars. The
        # band is everything between them, so the pair IS the construct and the
        # two numbers cannot be swept independently without producing cells
        # whose passband is empty or inverted. A LABEL, therefore, and in
        # `CATEGORICAL` for the same reason `macd_set` is.
        "roof": ((max(20, 2 * per_session), max(5, per_session // 2)),
                 (max(60, 10 * per_session), max(8, 2 * per_session))),
        "fisher": (max(10, per_session), max(30, 5 * per_session)),
        # FRACTIONAL DIFFERENCING ORDERS, not periods. 0.3 keeps most of the
        # memory and is barely stationary; 0.6 is past the point where most
        # price series pass an ADF test and still keeps far more history than a
        # first difference. Two values because the question is whether memory
        # helps at all, not what the optimal order is -- searching d would be
        # fitting the transform to the answer.
        "fracdiff": (0.3, 0.6),
        # `(minimum, maximum)` clamp for the cycle estimator, in bars. Not an
        # axis: it is the range the measurement is allowed to report, and
        # sweeping it would be sweeping the answer.
        "cycle": (max(8, per_session // 2), max(20, 4 * per_session)),
        "halflife": (max(60, 10 * per_session), max(200, 40 * per_session)),
        "quantile": (max(20, 4 * per_session), max(120, 20 * per_session)),
        # CUSUM thresholds as a multiple of trailing volatility. The filter
        # resets after every event, so the multiple sets how many events a year
        # there are, and one and two sigma are the two orders of magnitude worth
        # separating at any timeframe.
        "cusum": (1.0, 2.0),
    }


# --------------------------------------------------------------------------- #
# cost
# --------------------------------------------------------------------------- #

def cost_bp(symbol, spread_bp=None):
    """Everything charged, once at entry, in basis points of notional.

    Pro has no commission -- measured, see the module docstring -- so the whole
    cost is the quoted spread plus a slippage allowance. Basis points because
    that is the only unit that compares a $2.83 gas contract with a $30,000
    index without ranking them by price level.
    """
    spread = (INSTRUMENTS[symbol]["spread_bp"] if spread_bp is None else spread_bp)
    return (spread or 0.0) + SLIPPAGE_BP


def cost_price(symbol, entry, spread_bp=None):
    """`cost_bp` against a specific entry price, in price units."""
    return entry * cost_bp(symbol, spread_bp) / 1e4


def financing_price(symbol, side):
    """Overnight financing for one night, per lot, in PRICE units. Always >= 0.

    THE SECOND COST LEG, AND FOR A MULTI-DAY FAMILY IT IS THE LARGER ONE. Until
    this existed the study charged the spread once at entry and nothing else, so
    a position held sixty days was priced exactly like a thirty-minute scalp.
    Measured on this account 2026-08-22, per night against the quoted spread:

        de40    1.838 bp/night against a 0.189 bp spread     9.7x, per night
        nq      1.949 bp/night against 0.299 bp              6.5x
        es      1.851 bp/night against 0.463 bp              4.0x
        orcl    5.043 bp/night short, against 14.019 bp      0.4x

    At `days_60` that is 127-424 bp of cost the study was not charging, which is
    larger than almost any edge it has ever found -- so the `horizon` group could
    not have been read honestly without it.

    UNITS FALL OUT FOR FREE. `swap_mode == 1` is SYMBOL_SWAP_MODE_POINTS, so the
    charge is points per lot per night and `points * point` is a price distance,
    exactly like `cost_price`. The engine then multiplies by lots and
    `multiplier` the same way it does for the gross move, and no basis-point
    round trip is involved.

    A POSITIVE SWAP IS TREATED AS ZERO. Every reading on this account is
    negative or zero -- the broker's markup exceeds the rate differential on
    every symbol and both sides -- but a credit is not something to bank in a
    backtest, because it is the first number a broker changes.

    Returns 0.0 for any symbol whose `swap_mode` is not points, which is a
    refusal to guess rather than a claim that financing is free; `report_cost`
    prints the mode so a symbol that starts quoting in percent is visible.
    """
    spec = INSTRUMENTS[symbol]
    if spec.get("swap_mode") != 1:
        return 0.0
    points = spec["swap_long"] if side == 1 else spec["swap_short"]
    return abs(min(0.0, points or 0.0)) * spec["point"]


def financing_nights(entry_ts, exit_ts):
    """Calendar nights a position spanned -- date boundaries crossed.

    COUNTING CALENDAR NIGHTS IS THE TRIPLE-SWAP RULE, NOT AN APPROXIMATION OF IT.
    A broker charges one swap per trading night and three on one weekday
    (`swap_rollover3days`) to cover Saturday and Sunday. Over a full week that is
    3 + 1 + 1 + 1 + 1 = 7 charges, and a week has 7 calendar nights. The two
    conventions agree exactly, so counting date boundaries gets the weekend right
    by construction and needs no weekday table -- which is why the rollover day
    is recorded in the spec and never read.
    """
    return max(0, exit_ts // 86_400 - entry_ts // 86_400)


# --------------------------------------------------------------------------- #
# context
# --------------------------------------------------------------------------- #

def in_session(symbol, ts):
    opened, closed = INSTRUMENTS[symbol]["session"]
    return opened <= ts % 86_400 // 60 <= closed


def trailing_volatility(bars, n, annual_periods):
    returns = [0.0] + [math.log(b[C] / a[C]) if a[C] > 0 and b[C] > 0 else 0.0
                       for a, b in zip(bars, bars[1:])]
    out, total, total_sq = [None] * len(bars), 0.0, 0.0
    for index, value in enumerate(returns):
        total += value
        total_sq += value * value
        if index >= n:
            old = returns[index - n]
            total -= old
            total_sq -= old * old
        if index >= n - 1:
            mean = total / n
            out[index] = math.sqrt(
                max(0.0, total_sq / n - mean * mean) * annual_periods)
    return out


def rolling_mean(values, period):
    """Causal mean of the `period` values ending at each index, inclusive."""
    out, total = [None] * len(values), 0.0
    for index, value in enumerate(values):
        total += value
        if index >= period:
            total -= values[index - period]
        if index >= period - 1:
            out[index] = total / period
    return out


def rolling_min(values, period):
    out = [None] * len(values)
    for index in range(len(values)):
        if index >= period - 1:
            out[index] = min(values[index - period + 1:index + 1])
    return out


def wilder_rsi(closes, period):
    """Wilder's RSI. Non-linear in a way a z-score is not: it saturates, so an
    extreme reading means "one-sided for a while" rather than "far from the
    mean", which is a different claim about the same price path."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for index in range(1, period + 1):
        change = closes[index] - closes[index - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    gains /= period
    losses /= period
    out[period] = 100.0 - 100.0 / (1.0 + gains / losses) if losses else 100.0
    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains = (gains * (period - 1) + max(change, 0.0)) / period
        losses = (losses * (period - 1) + max(-change, 0.0)) / period
        out[index] = 100.0 - 100.0 / (1.0 + gains / losses) if losses else 100.0
    return out


def daily_risk(bars, days=DAILY_RANGE_BARS):
    """Average true DAILY range, carried onto every bar, causal.

    The unit every stop, target and trail is quoted in. It is computed from whole
    days no matter what bar size is being traded, which is what makes a stop mean
    the same thing at 5 minutes and at 4 hours
    ([[bar-size-confound-is-stop-distance]]).

    Only days strictly BEFORE a bar's own day contribute, so nothing leaks from
    the day being traded into the size of the risk taken on it.
    """
    order, highs, lows, closes = [], {}, {}, {}
    for bar in bars:
        day = bar[TS] // 86_400
        if day not in highs:
            order.append(day)
            highs[day], lows[day] = bar[H], bar[L]
        else:
            highs[day] = max(highs[day], bar[H])
            lows[day] = min(lows[day], bar[L])
        closes[day] = bar[C]

    ranges = []
    for index, day in enumerate(order):
        span = highs[day] - lows[day]
        if index:
            previous = closes[order[index - 1]]
            span = max(span, abs(highs[day] - previous), abs(lows[day] - previous))
        ranges.append(span)

    averages, total = {}, 0.0
    for index, day in enumerate(order):
        total += ranges[index]
        if index >= days:
            total -= ranges[index - days]
        # Recorded against the NEXT day, so a bar only ever reads completed days.
        if index >= days - 1 and index + 1 < len(order):
            averages[order[index + 1]] = total / days
    return [averages.get(bar[TS] // 86_400) for bar in bars]


def month_position(bars):
    """`(from_start, from_end)` trading-day position within each bar's month.

    1-based from the start and -1-based from the end, so "the last two sessions
    of the month" is `from_end >= -2` regardless of holidays or month length.
    """
    days, seen = [], set()
    for bar in bars:
        day = bar[TS] // 86_400
        if day not in seen:
            seen.add(day)
            days.append(day)
    months = {}
    for day in days:
        moment = datetime.fromtimestamp(day * 86_400, tz=timezone.utc)
        months.setdefault((moment.year, moment.month), []).append(day)
    start, end = {}, {}
    for member in months.values():
        for index, day in enumerate(member):
            start[day] = index + 1
            end[day] = index - len(member)
    return ([start.get(bar[TS] // 86_400) for bar in bars],
            [end.get(bar[TS] // 86_400) for bar in bars])


def anchors(full, symbol, daily=False):
    """Per day: the out-of-session high/low, and the prior in-session close.

    On daily bars there is no "outside the session" to measure, so the overnight
    window degenerates to the previous day's own range and the families that read
    it are excluded at that timeframe rather than fed a duplicate of `pdr`.
    """
    if daily:
        closes = {bar[TS] // 86_400: bar[C] for bar in full}
        ordered = sorted(closes)
        prior = {day: closes[ordered[i - 1]] for i, day in enumerate(ordered) if i}
        return {}, prior
    opened, closed = INSTRUMENTS[symbol]["session"]
    outside, closes = {}, {}
    for bar in full:
        minute, day = bar[TS] % 86_400 // 60, bar[TS] // 86_400
        if minute >= closed:
            day += 1
        if minute < opened or minute >= closed:
            item = outside.setdefault(day, [bar[H], bar[L]])
            item[0], item[1] = max(item[0], bar[H]), min(item[1], bar[L])
        else:
            closes[bar[TS] // 86_400] = bar[C]
    ordered = sorted(closes)
    prior = {day: closes[ordered[i - 1]] for i, day in enumerate(ordered) if i}
    return {day: tuple(value) for day, value in outside.items()}, prior


def prior_ranges(bars):
    got = {}
    for bar in bars:
        item = got.setdefault(bar[TS] // 86_400, [bar[H], bar[L]])
        item[0], item[1] = max(item[0], bar[H]), min(item[1], bar[L])
    days = sorted(got)
    return {day: tuple(got[days[i - 1]]) for i, day in enumerate(days) if i}


def opening_ranges(bars, counts=(1,)):
    """`(high, low)` of the first `n` bars of each session, day-keyed.

    Precomputed for the same reason `session_running` is, and the docstring
    there states it: the engine calls a signal only on bars where no position is
    open, so a rule accumulating its own opening range measures a SHORT range on
    exactly the days it was already busy. `orb` still accumulates and carries
    that flaw; `gated_orb` reads this instead, which is also why the two do not
    produce identical trade sets on a symbol that trades early.
    """
    out = {n: {} for n in counts}
    day = seen = None
    for bar in bars:
        current = bar[TS] // 86_400
        if current != day:
            day, seen = current, 0
        seen += 1
        for n in counts:
            if seen > n:
                continue
            window = out[n].get(day)
            out[n][day] = ((bar[H], bar[L]) if window is None
                           else (max(window[0], bar[H]), min(window[1], bar[L])))
    return out


#: How many bins a session's volume profile is cut into.
#:
#: A COUNT RATHER THAN A PRICE STEP, deliberately. A fixed tick-size bin makes
#: the profile's resolution a function of how expensive the instrument is: fifty
#: cents is a fine bin on DE40 and one bin for the whole session on EURUSD. A
#: fixed count of bins spanning the session's own range gives every symbol and
#: every day the same resolution in units of that day's activity, which is the
#: only thing a value area is comparable across.
PROFILE_BINS = 50

#: Share of a session's volume the value area covers. Steidlmayer's 70% -- the
#: one number in market-profile practice that was fixed before this study and
#: is not swept here for exactly that reason.
VALUE_AREA_SHARE = 0.70


def session_value_areas(bars):
    """Per session day: the PRIOR session's `(value_high, value_low, poc)`.

    A LEVEL SOURCE THE STUDY DID NOT HAVE, and the distinction from the two it
    does have is the admission argument. `prior_ranges` gives yesterday's high
    and low, which are the two prices at which trade STOPPED; `floor_pivots` are
    an arithmetic function of the same three numbers. Both are extremes. The
    value area is where the business was actually done -- it is set by volume,
    not by the two most extreme prints, and on a day with one spike and an
    otherwise tight auction the two answers are far apart and point in opposite
    directions about where support is.

    Built from the previous session because the current one is not finished, and
    that is not a convenience: a value area computed from bars the rule has not
    yet lived through is the purest form of lookahead this module could commit.

    Minute-resolution volume is spread evenly across the bins a bar's range
    touches, which `volume_profile` documents as the only honest reconstruction
    available without a volume-at-price feed. It is enough here: a value area is
    a wide object and the reconstruction error is a fraction of one bin.
    """
    from sandbox import volume_profile as vp

    rows = {}
    for bar in bars:
        rows.setdefault(bar[TS] // 86_400, []).append(bar)
    out = {}
    days = sorted(rows)
    for index, day in enumerate(days):
        if not index:
            continue
        session = rows[days[index - 1]]
        top = max(bar[H] for bar in session)
        bottom = min(bar[L] for bar in session)
        if top <= bottom:
            continue
        size = (top - bottom) / PROFILE_BINS
        if size <= 0:
            continue
        bins = vp.profile([(bar, bar[V]) for bar in session], size)
        area = vp.value_area(bins, VALUE_AREA_SHARE, size)
        poc = vp.point_of_control(bins, size)
        if area is None or poc is None:
            continue
        out[day] = (area[1], area[0], poc)
    return out


def session_running(bars):
    """Running `(high, low, open)` within each day, inclusive of the bar.

    Precomputed rather than accumulated in signal state, because the engine only
    calls a signal on bars where no position is open and the volatility filter
    accepts -- so a rule that built its own running range would silently skip
    bars and measure a short day. Anything reading the day's path so far must
    read it from here.
    """
    highs, lows, opens = [], [], []
    day = high = low = opened = None
    for bar in bars:
        current = bar[TS] // 86_400
        if current != day:
            day, high, low, opened = current, bar[H], bar[L], bar[O]
        else:
            high, low = max(high, bar[H]), min(low, bar[L])
        highs.append(high)
        lows.append(low)
        opens.append(opened)
    return highs, lows, opens


def session_vwap(bars):
    out, day, notional, volume = [], None, 0.0, 0.0
    for bar in bars:
        current = bar[TS] // 86_400
        if current != day:
            day, notional, volume = current, 0.0, 0.0
        notional += (bar[H] + bar[L] + bar[C]) / 3 * bar[V]
        volume += bar[V]
        out.append(notional / volume if volume > 0 else None)
    return out


def session_ordinals(bars, closed, daily):
    """`(ordinal, last_entry)` per bar: which session it is in, and where that
    session's last fillable bar sits.

    THE OVERNIGHT REGIME NEEDS BOTH AND NEITHER IS DERIVABLE IN A SIGNAL.

    `ordinal` counts sessions rather than calendar days, so "one night later" is
    one step even across a weekend or a holiday -- which is the whole point:
    Friday's night IS the weekend, and a rule that counted calendar days would
    call it three nights and price it as three.

    `last_entry` is the LAST bar of the session whose minute is still inside the
    entry window. The engine reads a signal on bar `i` and fills at bar `i + 1`'s
    open, and refuses a fill once the clock has reached the close -- so a night
    family has to fire on the bar BEFORE this one, and no signal can work out
    which bar that is from its own index alone.
    """
    if daily:
        return list(range(len(bars))), list(range(len(bars)))
    days = [bar[TS] // 86_400 for bar in bars]
    seen, ordinal = {}, []
    for day in days:
        if day not in seen:
            seen[day] = len(seen)
        ordinal.append(seen[day])
    latest = {}
    for index, bar in enumerate(bars):
        if bar[TS] % 86_400 // 60 < closed:
            latest[days[index]] = index
    return ordinal, [latest.get(day) for day in days]


def night_legs(bars):
    """Per session: the overnight leg, the daytime leg, and the close streak.

    THE DECOMPOSITION NOTHING ELSE IN THE STUDY HAS. Every reading here is built
    on a session's close, and a session return is the sum of two entirely
    different exposures -- the gap from the last close to this open, taken with
    the market shut and no ability to react, and the move from that open to this
    close, which is what every other family in the module trades. On equity
    indices those two legs have historically had opposite signs and very
    different variances, and no rolling window on the close series can separate
    them, because the close series has already added them together.

    Returned day-keyed and in logs, so the two legs sum exactly to the session
    return and a trailing mean of either is an average per session rather than a
    compounding artefact.
    """
    order, opens, closes = [], {}, {}
    for bar in bars:
        day = bar[TS] // 86_400
        if day not in opens:
            order.append(day)
            opens[day] = bar[O]
        closes[day] = bar[C]
    night, day_leg, streak = {}, {}, {}
    run = 0
    for index, day in enumerate(order):
        if opens[day] > 0 and closes[day] > 0:
            day_leg[day] = math.log(closes[day] / opens[day])
        if index:
            previous = closes[order[index - 1]]
            if previous > 0 and opens[day] > 0:
                night[day] = math.log(opens[day] / previous)
            up = closes[day] > previous
            run = (run + 1 if run > 0 and up
                   else run - 1 if run < 0 and not up
                   else 1 if up else -1)
        streak[day] = run
    return order, night, day_leg, streak


def night_profile(bars, windows):
    """The two legs' trailing means, carried onto every bar, causal.

    STRICTLY PRIOR SESSIONS. A trailing mean that included the bar's own session
    would be reading a close the bar has not printed yet -- and a night family
    fires on the last-but-one bar of the day, so "today's close" is genuinely in
    its future rather than merely in the same row.

    `prior_night` is the exception and is not lookahead: it is the gap that
    OPENED this session, which every bar of the session can see behind it.
    """
    order, night, day_leg, streak = night_legs(bars)
    means = {n: {} for n in windows}
    hits = {n: {} for n in windows}
    day_means = {n: {} for n in windows}
    for n in windows:
        for index, day in enumerate(order):
            window = order[max(0, index - n):index]
            values = [night[d] for d in window if d in night]
            legs = [day_leg[d] for d in window if d in day_leg]
            if len(values) >= max(5, n // 2):
                means[n][day] = statistics.fmean(values)
                hits[n][day] = statistics.fmean([1.0 if v > 0 else 0.0
                                                 for v in values])
            if len(legs) >= max(5, n // 2):
                day_means[n][day] = statistics.fmean(legs)
    prior_streak = {day: (streak[order[index - 1]] if index else 0)
                    for index, day in enumerate(order)}
    keys = [bar[TS] // 86_400 for bar in bars]
    return {
        "night": [night.get(day) for day in keys],
        "night_mean": {n: [means[n].get(day) for day in keys] for n in windows},
        "night_hit": {n: [hits[n].get(day) for day in keys] for n in windows},
        "day_mean": {n: [day_means[n].get(day) for day in keys] for n in windows},
        "prior_streak": [prior_streak.get(day, 0) for day in keys],
    }


def calendar_spans(bars):
    """The PREVIOUS completed calendar week, month and quarter: `(high, low)`.

    NOT A ROLLING CHANNEL, AND THE DIFFERENCE IS THE FAMILY. `donchian` at five
    sessions re-computes its level every day, so its "weekly high" on Thursday
    is Friday-through-Wednesday -- a window nobody is watching. Last week's high
    is a fixed number for all five sessions of this week, printed in every
    market report, and it is where resting orders actually sit. The two levels
    coincide only by accident, and a break of one is routinely not a break of
    the other.
    """
    units = ("week", "month", "quarter")
    spans, order, belongs = ({u: {} for u in units}, {u: [] for u in units},
                             {u: {} for u in units})
    for bar in bars:
        day = bar[TS] // 86_400
        moment = datetime.fromtimestamp(bar[TS], tz=timezone.utc)
        iso = moment.isocalendar()
        keys = {"week": (iso[0], iso[1]),
                "month": (moment.year, moment.month),
                "quarter": (moment.year, (moment.month - 1) // 3)}
        for unit in units:
            key = keys[unit]
            item = spans[unit].get(key)
            if item is None:
                spans[unit][key] = [bar[H], bar[L]]
                order[unit].append(key)
            else:
                item[0], item[1] = max(item[0], bar[H]), min(item[1], bar[L])
            belongs[unit][day] = key
    out = {}
    for unit in units:
        prior = {key: tuple(spans[unit][order[unit][index - 1]])
                 for index, key in enumerate(order[unit]) if index}
        out[unit] = {day: prior.get(key) for day, key in belongs[unit].items()}
    return out


def state_age(values, reference):
    """Bars since `values` last crossed `reference`; 0 on the crossing bar.

    THE STUDY READS LEVELS EVERYWHERE AND RECENCY ALMOST NOWHERE. "Price is
    above its fifty-day average" is the same reading on the day it crossed and
    in the eleventh month of a bull market, and the momentum-crash literature is
    entirely about those two being different states. Only `aroon` reads age at
    all, over a twenty-session window and flattened at the close.

    Missing readings are skipped rather than treated as a cross, so a warm-up
    gap does not reset an age to zero and manufacture a young trend.
    """
    out = [None] * len(values)
    sign = age = None
    for index, (value, level) in enumerate(zip(values, reference)):
        if value is None or level is None:
            continue
        now = 1 if value > level else -1 if value < level else sign
        if now != sign:
            sign, age = now, 0
        elif age is not None:
            age += 1
        out[index] = age
    return out


def rolling_pair_stats(own, other, period):
    """Rolling `(correlation, beta, residual)` of one return series on another.

    WHAT THE `relative` GROUP CANNOT SAY. Those three families read the RATIO of
    the two prices, and a ratio answers exactly one question: which of the two
    went up more. It cannot distinguish "the index rose two percent and the name
    rose a fifth of that" from "the index fell a fifth of a percent and the name
    fell two", because the ratio moved the same amount both times and in the
    same direction. It also cannot see a beta that is not one, so on a name that
    moves twice its index the ratio drifts with the market and calls that
    outperformance.

    `residual` is the window's own return net of `beta` times the benchmark's --
    the part of the move that belonged to the name.
    """
    correlation = [None] * len(own)
    slope = [None] * len(own)
    residual = [None] * len(own)
    sx = sy = sxx = syy = sxy = 0.0
    count = 0
    floor = max(5, period // 2)
    for index in range(len(own)):
        a, b = own[index], other[index]
        if a is not None and b is not None:
            sx += a
            sy += b
            sxx += a * a
            syy += b * b
            sxy += a * b
            count += 1
        if index >= period:
            a, b = own[index - period], other[index - period]
            if a is not None and b is not None:
                sx -= a
                sy -= b
                sxx -= a * a
                syy -= b * b
                sxy -= a * b
                count -= 1
        if count < floor:
            continue
        cx = sxx - sx * sx / count
        cy = syy - sy * sy / count
        cxy = sxy - sx * sy / count
        if cx > 0 and cy > 0:
            correlation[index] = cxy / math.sqrt(cx * cy)
        if cy > 0:
            slope[index] = cxy / cy
            residual[index] = sx - slope[index] * sy
    return correlation, slope, residual


def context(symbol, phase, bar=None, only=None, full_bars=None):
    spec, p = INSTRUMENTS[symbol], periods(symbol, bar)
    daily = is_daily(bar)
    # ``full_bars`` lets a caller run the frozen family logic on an alternate
    # feed without registering a fake instrument/table.  The normal
    # research path remains byte-for-byte the same when it is omitted.  This is
    # used by the combined NQ book after the real L2 feed begins: the pre-L2
    # segment stays on nq_1m and the post-boundary segment builds an independent
    # context from L2-derived bars, avoiding a manufactured price gap between
    # the two differently based series.
    full = all_bars(symbol, phase, bar) if full_bars is None else full_bars
    bars = full if daily else [b for b in full if in_session(symbol, b[TS])]
    closes = [b[C] for b in bars]
    highs = [b[H] for b in bars]
    lows = [b[L] for b in bars]
    volumes = [b[V] for b in bars]
    spans = [b[H] - b[L] for b in bars]
    outside, prior = anchors(full, symbol, daily)
    annual = spec["calendar"] * p["session"]
    means, sigmas = {}, {}
    for n in p["zscore"]:
        means[n], sigmas[n] = rolling_mean_sigma(closes, n)
    short = trailing_volatility(bars, p["vol"], annual)
    long = trailing_volatility(bars, p["long_vol"], annual)
    # `long_channel` breaks 100 and 200 sessions and `p["channels"]` jumps from
    # 55 to 252, so those two are added HERE rather than to `p["channels"]`
    # itself -- `obv_divergence` takes the union of that tuple to build its own
    # channels, so widening it would widen a published family's grid for a
    # family that does not run at its timeframe. Daily only, because that is the
    # only scope `long_channel` has: at 30m the same numbers would be 1,400 and
    # 2,800-bar extremes that no cell can reach.
    channels = (sorted({*p["channels"], *p["long_channel"]}) if daily
                else p["channels"])
    #: Bollinger bandwidth, and how compressed it is against its own recent
    #: history. A squeeze is a low reading, not a low absolute width, so it has
    #: to be scaled by price or it just reports which symbol is expensive.
    width = [None if m is None or s is None or m <= 0 else s / m
             for m, s in zip(means[p["session"]], sigmas[p["session"]])]
    from_start, from_end = month_position(bars)
    # `ts // 86400` and `ts % 86400 // 60` were recomputed for every bar of every
    # cell -- roughly a billion divmods across a 29,736-cell job. They depend
    # only on the bar, so they are computed once here.
    stamps = [b[TS] for b in bars]
    day_of = [t // 86_400 for t in stamps]
    minute_of = [t % 86_400 // 60 for t in stamps]
    running_high, running_low, running_open = session_running(bars)
    # Cheap enough to carry unconditionally -- two integer lists -- and the
    # overnight regime in `backtest` needs `session_index` for EVERY family it
    # runs, so it cannot live behind a `reads` declaration the way the heavy
    # blocks do.
    session_index, session_last = session_ordinals(bars, spec["session"][1], daily)
    ctx = {
        "session_high": running_high, "session_low": running_low,
        "session_open": running_open,
        "atr": es.average_true_range(bars, periods=p["atr"]),
        "risk": daily_risk(bars),
        "ema": {n: es.ema(closes, n) for n in p["trend"].values()},
        "fast": {n: es.ema(closes, n) for n in p["fast"]},
        "slow": {n: es.ema(closes, n) for n in p["slow"]},
        "high": {n: es.rolling_extreme(highs, n, True) for n in channels},
        "low": {n: es.rolling_extreme(lows, n, False) for n in channels},
        "mean": means, "sigma": sigmas,
        "vwap": None if daily else session_vwap(bars),
        "overnight": outside, "prior_close": prior,
        "prior_range": prior_ranges(bars), "volatility": short,
        # Kept as well as `calm`, which is only its sign. `vol_regime` reads the
        # RATIO -- how far volatility has moved against its own baseline -- and
        # a boolean cannot express "twice normal" or say when it crossed.
        "long_volatility": long,
        "calm": [None if s is None or l is None or l <= 0 else s < l
                 for s, l in zip(short, long)],
        "rsi": {n: wilder_rsi(closes, n) for n in p["rsi"]},
        "volume_mean": {n: rolling_mean(volumes, n) for n in p["volume"]},
        "span_min": {n: rolling_min(spans, n) for n in p["compress"]},
        "width": width,
        "width_min": {n: rolling_min([w if w is not None else math.inf
                                      for w in width], n)
                      for n in p["compress"]},
        "month_start": from_start, "month_end": from_end,
        "day": day_of, "minute": minute_of, "ts": stamps,
        "session_index": session_index, "session_last": session_last,
        "cfg": spec, "periods": p, "symbol": symbol, "daily": daily,
        "bar_minutes": bar or BAR_MINUTES,
    }
    ctx.update(extra_context(symbol, phase, bars, ctx, p, daily, bar,
                             context_blocks(only)))
    lo = is_start(symbol)
    sample = [v for v, b in zip(short, bars)
              if v is not None and lo <= b[TS] < IS_END]
    ctx["vol_target"] = statistics.median(sample) if sample else 0.3
    return bars, compact(ctx)


#: Missing reading. Replaces `None` in every float array the context carries.
#:
#: THE WHOLE POINT IS MEMORY. A Python list of 59,701 floats costs 32 bytes an
#: element -- 8 for the list slot and 24 for the boxed float -- and the context
#: holds about 172 such arrays, which is 328 MB on the FX pairs. The same data
#: as float64 is 8 bytes an element, 82 MB. Since Windows spawns rather than
#: forks, every worker holds a private copy, so that difference is the
#: difference between four workers on EURUSD and fifteen.
#:
#: THE PRICE IS THAT NaN IS SILENT WHERE `None` WAS LOUD. `None + 1` raises;
#: `nan + 1` is `nan`. Three idioms had to change everywhere they appear:
#:
#:     value is None        ->  value != value        (NaN is not equal to itself)
#:     if not risk          ->  if not risk or risk != risk   (NaN is TRUTHY)
#:     min(a, b) with a NaN ->  guard first; min is order-dependent on NaN
#:
#: Comparisons are the one place NaN behaves well: `nan > x` is False and
#: `nan < x` is False, so a threshold test on a missing reading declines to
#: trade all by itself. That is why the conversion is survivable at all.
#:
#: `exness_regression` exists to prove it changed nothing -- it scores a fixed
#: sample of every family before and after and compares the statistic dicts
#: exactly. Do not trust this conversion on inspection; run it.
MISSING = float("nan")

#: THE CONVERSION IS OFF UNTIL EVERY GUARD IS DONE, and this flag is why.
#:
#: `compact` is finished and correct; the sixty-four signal functions that read
#: its output are not. `regression verify` caught the failure mode on the first
#: attempt: `regime_switch` guards its variance ratio with `if ratio is None`,
#: which NaN passes, and then ends `return side if ratio >= 1.0 + band else
#: -side` -- where `nan >= x` is False, so the else branch fires and the family
#: TRADES ON A MISSING READING. It went from no trades at all to 82 trades on
#: TSLA, silently.
#:
#: Half-converted is the one state that must not ship: the arrays would be NaN
#: and the guards would still be looking for `None`. So the switch stays off,
#: the module keeps its list-and-`None` behaviour exactly as the 44 sealed
#: results were produced with, and turning it on is a single deliberate change
#: once `exness_regression verify` reports IDENTICAL.
COMPACT_CONTEXT = False


#: NOT named `valid`. `valid(params)` is imported from
#: `usoil_families_research` and decides whether a grid cell is legal
#: (`fast < slow`); defining a second `valid` here shadowed it and would have
#: silently accepted every impossible `ma_cross` corner.
def present(value):
    """True when a float reading is there -- against BOTH sentinels.

    `value == value` alone is wrong and cost a verify cycle to find: it detects
    NaN, but `None == None` is True, so a `None` reading sailed through the
    hot-path guard that `realized is not None` had been rejecting. Both
    sentinels have to be tested for as long as the conversion is staged, because
    an array is a list of `None` before the switch and float64 with NaN after,
    and the same guard has to be correct in both worlds.
    """
    return value is not None and value == value


#: Context keys holding one float per bar, or a dict of them. Converted to
#: float64 after the context is built rather than at each builder, so the
#: indicator code stays readable list-of-float Python and there is exactly one
#: place where the representation is decided.
FLOAT_ARRAYS = (
    "session_high", "session_low", "session_open", "atr", "risk", "vwap",
    "volatility", "long_volatility", "width", "obv", "rvol",
    "wick_upper", "wick_lower", "body",
    # The execution series. Float64 like every other reading, so a bar the
    # broker table never covered arrives at a signal as NaN and is refused by
    # `present` rather than raising on a `None` comparison.
    "spread", "spread_rank", "spread_ratio", "cover", "cover_rank",
)
#: NOT converted, deliberately: `sar_flip` and the `supertrend` direction hold
#: -1, 0 and 1, which CPython interns -- a list of them already costs 8 bytes an
#: element, so there is nothing to win, and float64 would turn an integer side
#: into 1.0 and quietly change the type flowing into the trade record.
FLOAT_TABLES = (
    "ema", "fast", "slow", "high", "low", "mean", "sigma", "rsi",
    "volume_mean", "span_min", "width_min", "plus_di", "minus_di", "adx",
    "aroon_up", "aroon_down", "stoch_fast", "stoch_slow", "cci", "macd_hist",
    "macd_line", "slope", "fit", "er", "vr", "skew", "mfi", "cmf",
    "obv_high", "obv_low", "supertrend_band", "pivot_high",
    "pivot_low", "prior_pivot_high", "prior_pivot_low", "avwap",
)
#: `xma` is a dict of dicts, and `relative` is a dict mixing arrays and tables.
#: Both are handled explicitly rather than by walking the structure, so a new
#: context key cannot be silently missed by a generic recursion.


def _floats(values):
    return numpy.array([MISSING if v is None else v for v in values],
                       dtype=numpy.float64)


def compact(ctx):
    """Convert every per-bar float array in `ctx` to float64 in place.

    Deliberately explicit about which keys it touches. A generic "convert
    anything that looks like a list of numbers" walk would also convert `day`,
    `minute` and `ts` -- which are integers read by `bisect` and by dict lookups
    where a float64 would silently change behaviour -- and `calm`, which is
    three-valued and whose `None` genuinely means "unknown" rather than
    "missing number".
    """
    if not COMPACT_CONTEXT:
        return ctx
    for key in FLOAT_ARRAYS:
        if isinstance(ctx.get(key), list):
            ctx[key] = _floats(ctx[key])
    for key in FLOAT_TABLES:
        table = ctx.get(key)
        if isinstance(table, dict):
            ctx[key] = {k: _floats(v) if isinstance(v, list) else v
                        for k, v in table.items()}
    if isinstance(ctx.get("xma"), dict):
        ctx["xma"] = {kind: {n: _floats(v) for n, v in table.items()}
                      for kind, table in ctx["xma"].items()}
    relative = ctx.get("relative")
    if relative:
        relative["line"] = _floats(relative["line"])
        for key in ("mean", "sigma", "high", "low", "own_high", "own_low",
                    "bench_high", "bench_low"):
            relative[key] = {n: _floats(v) for n, v in relative[key].items()}
    return ctx


def context_blocks(only=None):
    """The optional context blocks the requested families need, as a set.

    `None` for `only` means every family, which is what an unrestricted run asks
    for and what costs 201 MB a worker. Anything narrower pays only for what it
    reads.
    """
    wanted = FAMILIES if only is None else only
    return {block for name in wanted for block in FAMILIES[name].reads}


#: How much recent history a bar's spread is judged against, in sessions.
#:
#: TEN, because the question is "is this minute cheap for this market" and the
#: answer has to survive a one-day event. One session is 13 bars at 30m -- a
#: percentile over 13 points moves a whole decile per bar -- and a quarter
#: would average over a regime change in the quoting itself. Ten sessions is
#: about 130 bars at 30m, enough for a percentile to mean something and short
#: enough that a symbol whose spreads halve stays comparable with itself.
SPREAD_WINDOW_SESSIONS = 10


def _spread_statistics(values, window):
    """`(rank, ratio)` per bar: where this spread sits in its own recent past.

    `rank` is the fraction of the trailing `window` readings at or below this
    one, in [0, 1], and `ratio` is this reading over their median. Both are
    scale-free on purpose: the measured spread runs from 0.45 bp on usdjpy to
    24 bp on xniusd, so a threshold in basis points would be a different
    question on every symbol while a percentile is the same one.

    CAUSAL AND INCLUSIVE OF THE CURRENT BAR. The window is `(i-window, i]`, so
    nothing here reads a quote that had not printed. A bar with no reading
    contributes nothing to the window and gets none of its own.

    O(n*window) and deliberately so: it is built once per worker at start-up,
    where `extra_context` already spends seconds, and never inside the search
    loop. At 60,000 bars and a 130-bar window that is eight million
    comparisons, which is under a second.
    """
    rank = [None] * len(values)
    ratio = [None] * len(values)
    for index, value in enumerate(values):
        if value is None:
            continue
        recent = [v for v in values[max(0, index - window + 1):index + 1]
                  if v is not None]
        if len(recent) < 10:
            continue
        below = sum(1 for v in recent if v <= value)
        rank[index] = below / len(recent)
        middle = statistics.median(recent)
        if middle > 0:
            ratio[index] = value / middle
    return rank, ratio


def extra_context(symbol, phase, bars, ctx, p, daily, bar, blocks=None):
    """The second wave's readings -- BUILT ONLY WHERE ASKED FOR.

    KEPT SEPARATE FROM `context` ON PURPOSE. The block above is the original
    study and every result already published was produced by it; folding a
    hundred new arrays into the same dict literal would make it impossible to
    see, in a diff, that none of the old readings changed. Nothing here is
    referenced by any of the first twenty-nine families.

    BUILDING ALL OF IT UNCONDITIONALLY CRASHED A MACHINE, and that is the whole
    reason for `blocks`. The full set is 116 full-length arrays on top of the
    original 56 -- on ES, 201 MB per worker against 60 -- and Windows
    `multiprocessing` spawns rather than forks, so fifteen workers each build a
    complete private copy. That is 3.6 GB at peak against the original study's
    1.4 GB, before counting fifteen interpreters. Cell count only went up 1.5x;
    memory went up 3.3x, and memory is what fell over.

    So each family declares what it `reads` and this builds the union. A
    `--groups original` run now allocates nothing here at all, and `--groups
    xma` builds the average zoo and none of the other twenty blocks.

    THE COST IS STILL PAID ONCE. A worker builds this at start-up and then runs
    hundreds of thousands of cells against it, so an O(n*p) construction --
    `cci`, `swing_pivots` -- is a few seconds at launch rather than a multiplier
    on the search. What is NOT acceptable is a signal that computes anything
    windowed itself, and none of them do.
    """
    if blocks is None:
        blocks = context_blocks()
    closes = [b[C] for b in bars]
    out = {}

    if "spread" in blocks:
        # THE COST MODEL, READ AS A SERIES. `install_live_fills` puts the
        # broker's own per-minute spread in `TICK_SPREAD_BP`, and `backtest`
        # already charges it; this exposes the same numbers to the signals so a
        # rule can decide whether to trade at all at the price it is being
        # offered.
        #
        # NOT A LOOK-AHEAD. The spread of bar `i`'s own minute is quoted at
        # that minute and the entry it produces fills at bar `i+1`'s open, so
        # the reading is older than the fill by a whole bar -- the same
        # ordering [[entry-must-be-next-bar-open]] records. The rolling
        # statistics are causal for the same reason: the window ends at `i`.
        #
        # ABSENT BEFORE THE BROKER TABLE STARTS, which is 2020 on FX and
        # 2022-07-31 on the index CFDs while the study window opens earlier.
        # Those bars carry `None` here and every family that reads this refuses
        # to fire on them, which is the honest behaviour: a cost-aware rule
        # cannot be scored over a stretch whose cost is not known.
        by_ts = TICK_SPREAD_BP.get(symbol) or {}
        measured = [by_ts.get(b[TS]) for b in bars]
        window = max(20, SPREAD_WINDOW_SESSIONS * p["session"])
        out["spread"] = measured
        out["spread_rank"], out["spread_ratio"] = _spread_statistics(
            measured, window)
        # COVER: the day's range over what this minute charges to enter it,
        # which is `report_cost`'s symbol-level ratio computed per bar. Ranked
        # rather than used raw, and that is not a detail -- the raw ratio's
        # median runs from 14 on hk50 to 116 on ustec, so a fixed threshold
        # would refuse four fifths of one symbol and nothing at all on another
        # and the axis would be measuring the symbol. The rank asks the only
        # question that ports: is the move on offer large against this book's
        # own usual charge?
        # NOT NAMED `daily`, WHICH IT WAS, AND WHICH SHADOWED THIS FUNCTION'S
        # `daily` FLAG WITH A FLOAT. Every block built after this one then read
        # a truthy `daily` and took its daily branch: `opening` came back None
        # on 30-minute bars and `gated_orb` died on it. A block must never bind
        # a name this function's parameters use.
        cover = []
        for row, spread in zip(bars, measured):
            day_range = ctx["risk"][len(cover)]
            cover.append(None if not (present(day_range) and spread
                                      and spread > 0 and row[C] > 0)
                         else (1e4 * day_range / row[C]) / spread)
        out["cover"] = cover
        out["cover_rank"], _ = _spread_statistics(cover, window)

    if "xma" in blocks:
        # Only the (kind, period) pairs any cell can actually reach. All six
        # averages are crossed against the fast/slow periods by `xma_cross`, but
        # `xma_slope` and `xma_ribbon` read only the three ends of the lag
        # spectrum -- so building six kinds over the union of every period set
        # allocated a third more arrays than any cell could ever index.
        cross = sorted({*p["xma_fast"], *p["xma_slow"]})
        shape = sorted({*p["xma_slow"], *p["ribbon"]})
        both = sorted({*cross, *shape})
        out["xma"] = {
            kind: {n: ind.moving_average(kind, closes, n)
                   for n in (both if kind in XMA_SHAPE_KINDS else cross)}
            for kind in ind.MA_KINDS}

    if "wick" in blocks:
        upper, lower, body = ind.wick_ratios(bars)
        out.update(wick_upper=upper, wick_lower=lower, body=body)

    if "adx" in blocks:
        plus, minus, adx = {}, {}, {}
        for n in p["adx"]:
            plus[n], minus[n], adx[n] = ind.adx_dmi(bars, n)
        out.update(plus_di=plus, minus_di=minus, adx=adx)

    if "aroon" in blocks:
        up, down = {}, {}
        for n in p["aroon"]:
            up[n], down[n] = ind.aroon(bars, n)
        out.update(aroon_up=up, aroon_down=down)

    if "stoch" in blocks:
        fast, slow = {}, {}
        for n in p["stoch"]:
            fast[n], slow[n] = ind.stochastic(bars, n, p["stoch_smooth"])
        out.update(stoch_fast=fast, stoch_slow=slow)

    if "cci" in blocks:
        out["cci"] = {n: ind.cci(bars, n) for n in p["cci"]}

    if "macd" in blocks:
        line, histogram = {}, {}
        for setting in p["macd"]:
            values, _trigger, gap = ind.macd(closes, *setting)
            line[setting], histogram[setting] = values, gap
        out.update(macd_line=line, macd_hist=histogram)

    if "linreg" in blocks:
        slope, fit = {}, {}
        for n in p["linreg"]:
            slope[n], fit[n] = ind.linreg(closes, n)
        out.update(slope=slope, fit=fit)

    if "er" in blocks:
        out["er"] = {n: ind.efficiency_ratio(closes, n) for n in p["er"]}

    if "vr" in blocks:
        out["vr"] = {n: ind.variance_ratio(closes, n, p["vr_window"])
                     for n in p["vr_step"]}

    if "skew" in blocks:
        out["skew"] = {n: ind.rolling_skew(closes, n) for n in p["skew"]}

    if "mfi" in blocks:
        out["mfi"] = {n: ind.money_flow_index(bars, n) for n in p["mfi"]}

    if "cmf" in blocks:
        out["cmf"] = {n: ind.chaikin_money_flow(bars, n) for n in p["cmf"]}

    if "obv" in blocks:
        # `obv_divergence` compares the OBV channel with the PRICE channel at
        # the same lookback, so the two have to exist over the same period set:
        # a channel the family can ask for and the OBV cannot is a KeyError, not
        # a missing reading.
        obv = ind.on_balance_volume(bars)
        channels = sorted({*p["obv"], *p["channels"]})
        out.update(
            obv=obv,
            obv_high={n: es.rolling_extreme(obv, n, True) for n in channels},
            obv_low={n: es.rolling_extreme(obv, n, False) for n in channels})

    if "rvol" in blocks:
        # `relative_volume` compares a bar with the same clock minute on earlier
        # days, which has no meaning when there is one bar a day -- every day
        # would be its own minute. The family is intraday-scoped for that
        # reason; the `None` keeps its guard honest if that ever changes.
        out["rvol"] = None if daily else ind.relative_volume(bars)

    if "supertrend" in blocks:
        trend, band = {}, {}
        for multiple in SUPERTREND_MULTIPLES:
            trend[multiple], band[multiple] = ind.supertrend(
                bars, ctx["atr"], multiple)
        out.update(supertrend=trend, supertrend_band=band)

    if "sar" in blocks:
        out["sar_flip"] = ind.parabolic_sar(bars, SAR_STEP, SAR_CAP)[1]

    if "pivots" in blocks:
        high, prior_high, low, prior_low = {}, {}, {}, {}
        for wing in p["wing"]:
            (high[wing], prior_high[wing],
             low[wing], prior_low[wing]) = ind.swing_pivots(bars, wing)
        out.update(pivot_high=high, pivot_low=low,
                   prior_pivot_high=prior_high, prior_pivot_low=prior_low)

    if "gaps" in blocks:
        out["gaps"] = ind.fair_value_gaps(bars)

    if "floor_pivots" in blocks:
        out["floor_pivots"] = ind.floor_pivots(bars)

    if "avwap" in blocks:
        out["avwap"] = {anchor: ind.anchored_vwap(bars, anchor)
                        for anchor in ("week", "month")}

    if "opening" in blocks:
        # One bar, because `gated_orb` fixes the opening window and sweeps the
        # gate instead -- asking "how long is the range" and "which confirmer"
        # in the same grid would answer neither.
        out["opening"] = None if daily else opening_ranges(bars, (1,))

    if "relative" in blocks:
        reference = benchmark_closes(symbol, phase, bars, bar)
        if reference is None:
            out["relative"] = None
        else:
            line = ind.relative_line(closes, reference)
            # The ratio is a price series, so it gets exactly the machinery a
            # price gets -- channels and a z-score -- and nothing bespoke.
            #
            # The leading `None`s, from before the benchmark's own first row,
            # are CUT rather than filled. Substituting a placeholder and running
            # the rolling statistics over the whole array would leave every mean
            # and every channel wrong for a further `n` bars past the join, in a
            # way no signal-side `is None` test could catch -- the numbers would
            # be present, finite and meaningless.
            first = next((i for i, v in enumerate(line) if v is not None),
                         len(line))
            tail = line[first:]
            pad = [None] * first

            def carry(values):
                return pad + list(values)

            means, sigmas = {}, {}
            for n in p["rel"]:
                mean, sigma = rolling_mean_sigma(tail, n)
                means[n], sigmas[n] = carry(mean), carry(sigma)
            # The two series' OWN channels, at the ratio's own periods.
            # `idio_break` compares "did the name break out" with "did the
            # benchmark break out", which is a pair of absolute statements the
            # ratio line cannot make: a ratio extreme happens when the name
            # merely falls less, and nothing broke out at all.
            benchmark_tail = reference[first:]
            out["relative"] = {
                "line": line, "mean": means, "sigma": sigmas,
                "high": {n: carry(es.rolling_extreme(tail, n, True))
                         for n in p["rel"]},
                "low": {n: carry(es.rolling_extreme(tail, n, False))
                        for n in p["rel"]},
                "own_high": {n: es.rolling_extreme(closes, n, True)
                             for n in p["rel"]},
                "own_low": {n: es.rolling_extreme(closes, n, False)
                            for n in p["rel"]},
                "bench_high": {n: carry(es.rolling_extreme(benchmark_tail, n, True))
                               for n in p["rel"]},
                "bench_low": {n: carry(es.rolling_extreme(benchmark_tail, n, False))
                              for n in p["rel"]},
                "benchmark": reference, "name": BENCHMARK[symbol],
            }

    # ---- the fourth wave -------------------------------------------------- #

    if "nights" in blocks:
        out["nights"] = night_profile(bars, p["nights"])

    if "spans" in blocks:
        out["spans"] = calendar_spans(bars)

    if "ages" in blocks:
        peak, peak_age, trough, trough_age = {}, {}, {}, {}
        for n in p["peak"]:
            # INCLUSIVE of the bar, unlike `ctx["high"]`. A breakout test asks
            # whether this close exceeds the window BEFORE it and so must
            # exclude the bar; a drawdown asks how far below the peak price sits
            # and must include it, or a new high reads as a 0% drawdown from a
            # peak set yesterday and the age is wrong by a day forever after.
            peak[n] = ind.rolling_extreme_inclusive(closes, n, True)
            peak_age[n] = ind.rolling_argextreme(closes, n, True)
            trough[n] = ind.rolling_extreme_inclusive(closes, n, False)
            trough_age[n] = ind.rolling_argextreme(closes, n, False)
        # A SIMPLE average, not the EMA the `trend` axis uses. Faber's rule is
        # stated on a ten-month simple average and an EMA of the same length is
        # a different line with a different crossing date; using the EMA would
        # be testing a rule nobody published and reporting it under his name.
        long_sma = {n: ind.sma(closes, n) for n in p["faber"]}
        out.update(peak=peak, peak_age=peak_age, trough=trough,
                   trough_age=trough_age, long_sma=long_sma,
                   sma_age={n: state_age(closes, long_sma[n])
                            for n in p["faber"]})

    if "breaks" in blocks:
        # Calendar days between consecutive SESSIONS, either side of each bar.
        #
        # `after` looks one session into the future, and that is defensible for
        # exactly one reason: an exchange holiday calendar is published years in
        # advance, so "tomorrow is a holiday" is knowable at today's close in a
        # way tomorrow's price is not. THE HAZARD IS THAT A DATA OUTAGE IS
        # INDISTINGUISHABLE FROM A HOLIDAY HERE, and an outage was NOT knowable
        # in advance. `holiday` therefore bounds the gap it will trade: a normal
        # weekend is three days and a holiday weekend four, so anything past a
        # week is treated as missing data rather than as a closure.
        days = sorted({bar[TS] // 86_400 for bar in bars})
        before = {day: (day - days[index - 1] if index else None)
                  for index, day in enumerate(days)}
        after = {day: (days[index + 1] - day if index + 1 < len(days) else None)
                 for index, day in enumerate(days)}
        keys = [bar[TS] // 86_400 for bar in bars]
        out.update(break_before=[before.get(day) for day in keys],
                   break_after=[after.get(day) for day in keys])

    if "bench" in blocks:
        reference = benchmark_closes(symbol, phase, bars, bar)
        if reference is None:
            out["bench"] = None
        else:
            def logs(series):
                values = [None]
                for a, b in zip(series, series[1:]):
                    values.append(math.log(b / a)
                                  if a is not None and b is not None
                                  and a > 0 and b > 0 else None)
                return values

            own, other = logs(closes), logs(reference)
            correlation, slope, residual, move = {}, {}, {}, {}
            for n in p["pair"]:
                correlation[n], slope[n], residual[n] = rolling_pair_stats(
                    own, other, n)
                # The benchmark's OWN move over the window, which the ratio
                # cannot express and `lead_lag` is entirely about.
                move[n] = [None if index < n or reference[index] is None
                           or reference[index - n] is None
                           or reference[index - n] <= 0
                           else math.log(reference[index] / reference[index - n])
                           for index in range(len(reference))]
            out["bench"] = {"correlation": correlation, "beta": slope,
                            "residual": residual, "move": move,
                            "own": own, "name": BENCHMARK[symbol]}

    if "vix" in blocks:
        level = vix_closes(symbol, phase, bars)
        if level is None:
            out["vix"] = None
        else:
            # Realised volatility on the SAME scale VIX is quoted on -- annual
            # percent -- or the premium is a difference of two different units
            # and its sign means nothing. `trailing_volatility` returns an
            # annualised fraction, so it is multiplied by 100 and not otherwise
            # touched.
            # `extra_context` takes no spec of its own; the resolved entry
            # rides on the context it is extending.
            annual = ctx["cfg"]["calendar"] * p["session"]
            realised = {n: trailing_volatility(bars, n * p["session"], annual)
                        for n in p["vix"]}
            # `rolling_mean` cannot take the leading `None`s that `ind.align`
            # produces before the VIX table's first row, and filling them with a
            # number would put a fabricated level into every mean for a further
            # `n` bars. So the mean is computed over PRESENT readings only and
            # withheld until the window is genuinely full.
            def present_mean(values, period):
                out, total, seen = [None] * len(values), 0.0, 0
                for index, value in enumerate(values):
                    if value is not None:
                        total += value
                        seen += 1
                    if index >= period:
                        gone = values[index - period]
                        if gone is not None:
                            total -= gone
                            seen -= 1
                    if seen == period:
                        out[index] = total / period
                return out

            means = {n: present_mean(level, n * p["session"])
                     for n in p["vix"]}

            def present_rank(values, period):
                """Fraction of the last `period` readings below this one.

                A PERCENTILE RATHER THAN A RATIO, BECAUSE VIX DEVIATIONS ARE NOT
                SYMMETRIC. It spikes upward and decays downward, so "40% above
                its own mean" is common and "40% below" essentially never
                happens -- a threshold expressed as a ratio therefore prices a
                live hypothesis on one side and a dead cell on the other, which
                is 80 of 576 cells on NQ. A rank puts a fixed fraction of the
                sample in each zone whatever the shape of the distribution.
                """
                out = [None] * len(values)
                for index in range(len(values)):
                    value = values[index]
                    if value is None or index + 1 < period:
                        continue
                    window = [v for v in values[index - period + 1:index + 1]
                              if v is not None]
                    if len(window) < period:
                        continue
                    out[index] = sum(1 for v in window if v < value) / len(window)
                return out

            ranks = {n: present_rank(level, n * p["session"])
                     for n in p["vix"]}
            out["vix"] = {
                "level": level,
                "mean": means,
                "rank": ranks,
                "premium": {n: [None if a is None or b is None
                                else a - 100.0 * b
                                for a, b in zip(level, realised[n])]
                            for n in p["vix"]},
                "realised": realised,
            }

    # ---- the sixth wave ---------------------------------------------------- #
    #
    # TWENTY MORE BLOCKS, AND THE `reads` DISCIPLINE IS WHY THAT IS SAFE. Built
    # end to end on a 60,000-bar series -- which is what a 30m table holds over
    # the full window -- the whole wave costs 7.9 seconds and about forty
    # arrays. That is only paid by a run that asks for all of it; `--groups
    # pathstat` builds six blocks and none of the other fourteen.
    #
    # NOTHING HERE IS WINDOWED INSIDE A SIGNAL, with one deliberate exception:
    # `adaptive` reads a z-score at a period the bar itself chooses, which
    # cannot be tabulated in advance. It is served from prefix sums instead, so
    # the signal still does O(1) work -- see the `prefix` block.

    if "hurst" in blocks:
        out["hurst"] = {n: ind.hurst_exponent(closes, n) for n in p["hurst"]}

    if "entropy" in blocks:
        out["entropy"] = {n: ind.permutation_entropy(closes, n)
                          for n in p["entropy"]}

    if "kurtosis" in blocks:
        out["kurtosis"] = {n: ind.rolling_kurtosis(closes, n)
                           for n in p["kurtosis"]}

    if "autocorr" in blocks:
        # Keyed by `(period, lag)`, because the family sweeps both and a nested
        # dict would put the two axes at different depths for no reason.
        out["autocorr"] = {(n, lag): ind.rolling_autocorr(closes, n, lag)
                           for n in p["autocorr"] for lag in p["lags"]}

    if "runs" in blocks:
        out["runs"] = {n: ind.runs_z(closes, n) for n in p["runs"]}

    if "kendall" in blocks:
        out["kendall"] = {n: ind.mann_kendall_z(closes, n) for n in p["kendall"]}

    if "tails" in blocks:
        out["tails"] = {n: ind.tail_ratio(closes, n) for n in p["tails"]}

    if "jump" in blocks:
        out["jump"] = {n: ind.bipower_jump(closes, n) for n in p["jump"]}

    if "semi" in blocks:
        out["semi"] = {n: ind.signed_jump(closes, n) for n in p["semi"]}

    if "amihud" in blocks:
        out["amihud"] = {n: ind.amihud(bars, n) for n in p["amihud"]}

    if "estimators" in blocks:
        parkinson, garman, close_var = {}, {}, {}
        for n in p["estimator"]:
            parkinson[n], garman[n], close_var[n] = ind.variance_estimators(
                bars, n)
        out.update(parkinson=parkinson, garman_klass=garman,
                   close_var=close_var)

    if "bulk" in blocks:
        out["bulk"] = {n: ind.bulk_imbalance(bars, n) for n in p["bulk"]}

    if "roofing" in blocks:
        out["roofing"] = {pair: ind.roofing_filter(closes, pair[0], pair[1])
                          for pair in p["roof"]}

    if "fisher" in blocks:
        out["fisher"] = {n: ind.fisher_transform(bars, n) for n in p["fisher"]}

    if "kalman" in blocks:
        level, slope = ind.kalman_trend(closes)
        out.update(kalman_level=level, kalman_slope=slope)

    if "fracdiff" in blocks:
        # The differenced series is not zero-mean and its scale depends on the
        # order, so a raw threshold on it would mean a different thing at each
        # `d`. It is standardised against its OWN trailing statistics, and the
        # leading `None`s are CUT before the rolling window rather than filled
        # -- the same treatment, and for the same reason, as the `relative`
        # block above: a substituted placeholder would leave every mean wrong
        # for a further `n` bars in a way no signal-side guard could catch.
        series, means, sigmas = {}, {}, {}
        window = 20 * p["session"]
        for order in p["fracdiff"]:
            line = ind.frac_diff(closes, order)
            series[order] = line
            first = next((i for i, v in enumerate(line) if v is not None),
                         len(line))
            tail = line[first:]
            pad = [None] * first
            mean, sigma = rolling_mean_sigma(tail, window)
            means[order] = pad + list(mean)
            sigmas[order] = pad + list(sigma)
        out.update(fracdiff=series, fracdiff_mean=means, fracdiff_sigma=sigmas)

    if "cycle" in blocks:
        out["cycle"] = ind.dominant_cycle(closes, p["cycle"][0], p["cycle"][1])

    if "halflife" in blocks:
        out["halflife"] = {n: ind.ou_half_life(closes, n)
                           for n in p["halflife"]}

    if "prefix" in blocks:
        # PREFIX SUMS OF THE CLOSES AND THEIR SQUARES, so a mean and a standard
        # deviation over ANY window ending at any bar are two subtractions.
        #
        # This exists for exactly one family. `half_life` measures its own
        # lookback every bar and then wants a z-score over that many bars, and
        # the lookback is a different integer at every bar -- so there is no
        # finite set of periods to tabulate, and the alternative was a signal
        # that summed a window itself, which is the one thing `extra_context`'s
        # contract forbids. With these it is O(1) per bar like everything else.
        total = [0.0] * (len(closes) + 1)
        square = [0.0] * (len(closes) + 1)
        for index, value in enumerate(closes):
            total[index + 1] = total[index] + value
            square[index + 1] = square[index] + value * value
        out.update(prefix_sum=total, prefix_square=square)

    if "cusum" in blocks:
        # One event series per threshold multiple. The filter is STATEFUL --
        # it resets after every event -- so it cannot be evaluated at a
        # threshold the signal picks; each multiple is its own precomputed run.
        # PER BAR, NOT ANNUALISED. `ctx["volatility"]` is an annualised
        # fraction and `cusum_events` accumulates one-bar log returns, so
        # thresholding one with the other is out by sqrt(annual_periods) --
        # about 90 at 30m -- and the filter fires a couple of dozen times in
        # seven years instead of a few hundred. ATR over the close is the bar's
        # own typical log move and is the unit every other threshold in this
        # module is expressed in.
        unit = [None if not present(a) or not present(c) or c <= 0
                else a / c
                for a, c in zip(ctx["atr"], closes)]
        out["cusum"] = {
            multiple: ind.cusum_events(
                closes, [None if v is None else multiple * v for v in unit])
            for multiple in p["cusum"]}

    if "quantile" in blocks:
        upper, lower = {}, {}
        for n in p["quantile"]:
            for share in QUANTILE_EDGES:
                upper[(n, share)], lower[(n, share)] = ind.quantile_channel(
                    closes, n, share)
        out.update(quantile_high=upper, quantile_low=lower)

    if "value_area" in blocks:
        out["value_area"] = None if daily else session_value_areas(bars)

    return out


# --------------------------------------------------------------------------- #
# signals
#
# The symbol-agnostic ones are imported from `usoil_families_research`, which is
# their single implementation. Only the three that need this symbol's session or
# period table are defined here.
# --------------------------------------------------------------------------- #

def accepts_trend(price, ctx, index, side, mode):
    if mode == "none":
        return True
    reference = ctx["ema"][ctx["periods"]["trend"][mode]][index]
    return reference is not None and (price > reference if side == 1
                                      else price < reference)


def accepts_vol(ctx, index, mode):
    return mode == "none" or (ctx["calm"][index] == (mode == "calm"))


def orb_signal(index, bars, ctx, params, state):
    bar, opened = bars[index], ctx["cfg"]["session"][0]
    day, minute = bar[TS] // 86_400, bar[TS] % 86_400 // 60
    if state.get("day") != day:
        state.clear()
        state.update(day=day, high=None, low=None)
    end = opened + 30 * params["range_bars"]
    if minute < end:
        state["high"] = bar[H] if state["high"] is None else max(state["high"], bar[H])
        state["low"] = bar[L] if state["low"] is None else min(state["low"], bar[L])
        return None
    if minute > params["last_entry_minute"] or state["high"] is None:
        return None
    atr = ctx["atr"][index]
    if not atr:
        return None
    upper = state["high"] + params["breakout_atr"] * atr
    lower = state["low"] - params["breakout_atr"] * atr
    side = 1 if bar[C] > upper else (-1 if bar[C] < lower else None)
    return -side if side and params["direction"] == "fade" else side


def gap_signal(index, bars, ctx, params, _state):
    """The open against the prior close.

    Survives onto daily bars unchanged -- a daily bar's open IS the session open,
    because `daily_bars` builds the day out of session buckets. The only
    difference is that there is no clock test to pass first.
    """
    bar = bars[index]
    if not ctx["daily"] and bar[TS] % 86_400 // 60 != ctx["cfg"]["session"][0]:
        return None
    previous, atr = ctx["prior_close"].get(bar[TS] // 86_400), ctx["atr"][index]
    if previous is None or not atr:
        return None
    drift = (bar[O] - previous) / atr
    threshold = params["threshold_atr"]
    side = -1 if drift > threshold else (1 if drift < -threshold else None)
    return -side if side and params["direction"] == "follow" else side


def storage_signal(index, bars, ctx, params, _state):
    """The Thursday 10:30 EIA natural gas storage report."""
    bar = bars[index]
    if es.weekday(bar[TS]) != 3 or bar[TS] % 86_400 // 60 != params["signal_minute"]:
        return None
    atr = ctx["atr"][index]
    if not atr:
        return None
    move, threshold = bar[C] - bar[O], params["threshold_atr"] * atr
    side = 1 if move > threshold else (-1 if move < -threshold else None)
    return -side if side and params["direction"] == "fade" else side


# --------------------------------------------------------------------------- #
# additional families
#
# Every one returns +1, -1 or None and is called only on an in-session bar with
# no position and no pending entry. `rolling_extreme` already excludes the
# current bar, so a close CAN exceed a channel it is being compared against.
#
# The point of a family is to be a distinct THESIS, not a distinct formula. Two
# rules that fire on the same bars for the same reason are one hypothesis wearing
# two hats, and they double the search budget while adding nothing -- so each of
# these is here because it reads something the others cannot see.
# --------------------------------------------------------------------------- #

def _late(ctx, bar, params):
    """True once the session is past this cell's entry cutoff."""
    return (not ctx["daily"]
            and bar[TS] % 86_400 // 60 > params["last_entry_minute"])


def ib_signal(index, bars, ctx, params, state):
    """Initial balance: the first `ib_minutes` of the session, broken by a
    fraction of ITS OWN width.

    Not a duplicate of `orb`. `orb` measures the first one or two bars and
    extends by ATR, so at 5 minutes it is reading ten minutes of trade; `ib`
    holds a fixed wall-clock window whatever the bar size, and normalises the
    breakout by the balance's own width, so a wide balance demands a wide break.
    """
    bar, opened = bars[index], ctx["cfg"]["session"][0]
    day, minute = bar[TS] // 86_400, bar[TS] % 86_400 // 60
    if state.get("day") != day:
        state.clear()
        state.update(day=day, high=None, low=None)
    if minute < opened + params["ib_minutes"]:
        state["high"] = bar[H] if state["high"] is None else max(state["high"], bar[H])
        state["low"] = bar[L] if state["low"] is None else min(state["low"], bar[L])
        return None
    if _late(ctx, bar, params) or state["high"] is None:
        return None
    width = state["high"] - state["low"]
    if width <= 0:
        return None
    edge = params["extension"] * width
    side = (1 if bar[C] > state["high"] + edge
            else -1 if bar[C] < state["low"] - edge else None)
    return -side if side and params["direction"] == "fade" else side


def failed_break_signal(index, bars, ctx, params, _state):
    """Trades through yesterday's extreme and closes back inside it.

    The thesis is the opposite of `pdr`: the level held. `pdr` cannot express
    this, because it reads only where the close sits and a failed break closes
    on the wrong side of its own trigger.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    window = ctx["prior_range"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if window is None or not atr:
        return None
    high, low = window
    edge = params["buffer_atr"] * atr
    side = None
    if bar[H] > high + edge and bar[C] < high:
        side = -1
    elif bar[L] < low - edge and bar[C] > low:
        side = 1
    return -side if side and params["direction"] == "follow" else side


def nr_signal(index, bars, ctx, params, _state):
    """The narrowest bar of the last `compress`, traded with the move into it.

    Compression is the setup, not the direction, so the side comes from where
    price has come from. The engine fills at the next open rather than on a stop
    order, so this reads the squeeze and joins the prevailing move instead of
    waiting for a break the engine cannot place.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    window = params["compress"]
    floor = ctx["span_min"][window][index]
    if floor is None or index < window:
        return None
    if bar[H] - bar[L] > floor + 1e-12:
        return None
    move = bar[C] - bars[index - window][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def inside_signal(index, bars, ctx, params, _state):
    """An inside bar -- range wholly within the previous bar's.

    A pause rather than a range. Where it closes inside the mother bar is the
    only information it carries.
    """
    if index == 0:
        return None
    bar, mother = bars[index], bars[index - 1]
    if _late(ctx, bar, params):
        return None
    if not (bar[H] <= mother[H] and bar[L] >= mother[L]):
        return None
    span = mother[H] - mother[L]
    if span <= 0:
        return None
    at = (bar[C] - mother[L]) / span
    side = (1 if at > params["location"]
            else -1 if at < 1.0 - params["location"] else None)
    return -side if side and params["direction"] == "fade" else side


def keltner_signal(index, bars, ctx, params, _state):
    """An EMA plus or minus a multiple of ATR.

    A VOLATILITY channel, where `donchian` is a price channel and `zscore` is a
    dispersion channel. The three disagree exactly when volatility and range
    disagree, which is the case worth separating.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    middle = ctx["ema"][ctx["periods"]["trend"][params["reference"]]][index]
    atr = ctx["atr"][index]
    if middle is None or not atr:
        return None
    band = params["mult"] * atr
    side = (1 if bar[C] > middle + band
            else -1 if bar[C] < middle - band else None)
    return -side if side and params["direction"] == "fade" else side


def squeeze_signal(index, bars, ctx, params, _state):
    """Bollinger bandwidth at a `compress`-bar low.

    Bandwidth is scaled by price, so this reports compression rather than which
    symbol happens to be cheap. Where `nr` sees one narrow bar, this sees a
    stretch of narrow ones -- a different kind of quiet.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    width = ctx["width"][index]
    floor = ctx["width_min"][params["compress"]][index]
    session = ctx["periods"]["session"]
    middle = ctx["mean"][session][index]
    if width is None or floor is None or middle is None or not math.isfinite(floor):
        return None
    if width > floor * params["tolerance"]:
        return None
    side = 1 if bar[C] > middle else -1
    return -side if params["direction"] == "fade" else side


def consecutive_signal(index, bars, ctx, params, _state):
    """`count` closes in a row in the same direction.

    The purest statement of short-horizon serial correlation there is: no
    threshold, no lookback window, no normalisation to argue about.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    count = params["count"]
    if index < count:
        return None
    ups = sum(1 for step in range(count)
              if bars[index - step][C] > bars[index - step - 1][C])
    side = 1 if ups == count else -1 if ups == 0 else None
    return -side if side and params["direction"] == "fade" else side


def rsi_signal(index, bars, ctx, params, _state):
    """Wilder's RSI through a threshold.

    Saturating where `zscore` is linear: RSI says "one-sided for a while", a
    z-score says "far from the mean". A grinding drift maxes RSI and leaves the
    z-score modest; one gap does the reverse.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    value = ctx["rsi"][params["period"]][index]
    if value is None:
        return None
    top = params["threshold"]
    side = (-1 if value > top else 1 if value < 100.0 - top else None)
    return -side if side and params["direction"] == "follow" else side


def key_reversal_signal(index, bars, ctx, params, _state):
    """A new `channel`-bar extreme that closes back through the prior close.

    Rejection at the edge of a range, which is `donchian`'s trigger read as a
    failure rather than a confirmation.
    """
    if index == 0:
        return None
    bar, previous = bars[index], bars[index - 1]
    if _late(ctx, bar, params):
        return None
    top = ctx["high"][params["channel"]][index]
    bottom = ctx["low"][params["channel"]][index]
    if top is None or bottom is None:
        return None
    side = None
    if bar[H] > top and bar[C] < previous[C]:
        side = -1
    elif bar[L] < bottom and bar[C] > previous[C]:
        side = 1
    return -side if side and params["direction"] == "follow" else side


def climax_signal(index, bars, ctx, params, _state):
    """Heavy volume, a wide bar, and a close pinned at one end.

    The only family that requires all three of volume, range and close location
    to agree. That conjunction is what "someone was forced" looks like in OHLCV,
    and it is the nearest thing to the order-flow absorption idea that bars can
    express.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    average = ctx["volume_mean"][params["volume_period"]][index]
    atr = ctx["atr"][index]
    span = bar[H] - bar[L]
    if not average or not atr or span <= 0:
        return None
    if bar[V] < params["volume_mult"] * average:
        return None
    if span < params["range_mult"] * atr:
        return None
    at = (bar[C] - bar[L]) / span
    side = (-1 if at > params["location"]
            else 1 if at < 1.0 - params["location"] else None)
    return -side if side and params["direction"] == "follow" else side


def volume_thrust_signal(index, bars, ctx, params, _state):
    """A directional bar on unusual volume.

    `climax` reads the same volume as exhaustion; this reads it as
    participation. They fire on overlapping bars and disagree about the sign,
    which is the point -- run both and let the holdout separate them.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    average = ctx["volume_mean"][params["volume_period"]][index]
    atr = ctx["atr"][index]
    if not average or not atr or bar[V] < params["volume_mult"] * average:
        return None
    move = bar[C] - bar[O]
    if abs(move) < params["threshold_atr"] * atr:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def time_of_day_signal(index, bars, ctx, params, _state):
    """Enter at a fixed clock time, in a fixed direction, unconditionally.

    Deliberately the dumbest rule here: it has no trigger at all, so whatever it
    earns is structural drift in that part of the session rather than a signal.
    That makes it the natural control for every other intraday family -- a
    breakout that cannot beat "just be long at 10:00" has not shown anything.
    """
    bar = bars[index]
    if bar[TS] % 86_400 // 60 != params["entry_minute"]:
        return None
    return 1 if params["side"] == "long" else -1


def range_expansion_signal(index, bars, ctx, params, _state):
    """The session has already travelled more than a normal day's range.

    Reads the day's accumulated movement rather than any single bar, so it is
    the one intraday family whose trigger is a path property. It takes that path
    from `session_running` rather than accumulating it itself -- the engine skips
    signal calls on bars where the volatility filter rejects, so a self-built
    range would come up short exactly on the `calm` cells.
    """
    bar = bars[index]
    if bar[TS] % 86_400 // 60 < params["signal_minute"] or _late(ctx, bar, params):
        return None
    risk = ctx["risk"][index]
    travelled = ctx["session_high"][index] - ctx["session_low"][index]
    if not risk or travelled < params["expansion"] * risk:
        return None
    side = 1 if bar[C] > ctx["session_open"][index] else -1
    return -side if params["direction"] == "fade" else side


def swing_donchian_signal(index, bars, ctx, params, _state):
    """A multi-day channel break, held through the close.

    Same trigger as `donchian`, different animal: `donchian` is forced flat at
    the session close and so can only ever capture one day of a trend, while this
    one holds until its stop or day count. The comparison between the two is a
    clean read on whether the edge is the entry or the holding period.
    """
    bar = bars[index]
    top = ctx["high"][params["channel"]][index]
    bottom = ctx["low"][params["channel"]][index]
    if top is None or bottom is None:
        return None
    side = 1 if bar[C] > top else -1 if bar[C] < bottom else None
    return -side if side and params["direction"] == "fade" else side


def swing_ma_signal(index, bars, ctx, params, _state):
    """A multi-day moving-average cross, held through the close."""
    if index == 0:
        return None
    fast, slow = ctx["fast"][params["fast"]], ctx["slow"][params["slow"]]
    if fast[index] is None or slow[index] is None or fast[index - 1] is None \
            or slow[index - 1] is None:
        return None
    now = fast[index] - slow[index]
    before = fast[index - 1] - slow[index - 1]
    side = 1 if before <= 0 < now else -1 if before >= 0 > now else None
    return -side if side and params["direction"] == "fade" else side


def swing_zscore_signal(index, bars, ctx, params, _state):
    """A multi-day stretch from the mean, held until it reverts or stops out."""
    bar = bars[index]
    period = params["period"]
    mean, sigma = ctx["mean"][period][index], ctx["sigma"][period][index]
    if mean is None or sigma is None or sigma <= 0:
        return None
    z = (bar[C] - mean) / sigma
    threshold = params["threshold_z"]
    side = -1 if z > threshold else 1 if z < -threshold else None
    return -side if side and params["direction"] == "follow" else side


def turn_of_month_signal(index, bars, ctx, params, _state):
    """The last `before` and first `after` trading sessions of a month.

    A calendar effect with a mechanism that has nothing to do with price --
    pension and index flows land on a schedule. It is the only family here whose
    trigger cannot be produced by any amount of curve fitting on the price
    series, which is why it is worth carrying even if it fails.
    """
    at_end, at_start = ctx["month_end"][index], ctx["month_start"][index]
    if at_end is None or at_start is None:
        return None
    if not (at_end >= -params["before"] or at_start <= params["after"]):
        return None
    return 1 if params["side"] == "long" else -1


def seasonality_signal(index, bars, ctx, params, _state):
    """A fixed direction in a fixed calendar month.

    Included as an explicit, countable overfitting hazard rather than a belief:
    twelve months times two directions is twenty-four coin flips, and one of them
    will look excellent on any series. It is here so that the null control can
    price exactly how good "excellent" has to be before it means anything.
    """
    moment = datetime.fromtimestamp(bars[index][TS], tz=timezone.utc)
    if moment.month != params["month"]:
        return None
    return 1 if params["side"] == "long" else -1


def high_52w_signal(index, bars, ctx, params, _state):
    """How far price sits below its own one-year high.

    Long-horizon position rather than a short-horizon move -- the only family
    that asks where price is in its own multi-year range. On a daily bar the
    channel is a real 52 weeks; intraday it is the same span in bars.
    """
    bar = bars[index]
    top = ctx["high"][params["channel"]][index]
    if top is None or top <= 0:
        return None
    distance = (top - bar[C]) / top
    side = (1 if distance <= params["proximity"]
            else -1 if distance >= params["depth"] else None)
    return -side if side and params["direction"] == "fade" else side


# --------------------------------------------------------------------------- #
# second wave
#
# Thirty-three more theses. The bar for adding one was not "is this a known
# indicator" -- it was: NAME A BAR ON WHICH THIS FIRES AND NO EXISTING FAMILY
# DOES, AND SAY WHY THE TWO DISAGREE. Every docstring below answers that
# question against the family it is closest to, because a rule that fires on the
# same bars for the same reason as an existing one is not a new hypothesis; it
# is the same hypothesis charged twice to the search budget.
#
# Three things were deliberately NOT added, and it is worth recording why:
#
#   * Williams %R -- an inverted stochastic, identical bars, identical sign.
#   * A Bollinger %B family -- `zscore` with the axis renamed.
#   * A gap-fill family -- `gap` already reads the same event; whether it fills
#     is the exit question, and exits are a shared axis.
#
# WHAT THIS COSTS. The budget roughly triples. That is the price of covering
# ground the first twenty-nine could not see, and it is only defensible because
# `--groups` lets a run take one thesis group at a time and because `why` prices
# the whole grid against coin flips. Read `budget` before starting, and do not
# read a winner out of a 60-family sweep without the null beside it.
# --------------------------------------------------------------------------- #

# ---- the moving-average zoo ------------------------------------------------ #

def xma_cross_signal(index, bars, ctx, params, _state):
    """A fast average crossing a slow one, where the AVERAGE TYPE is the axis.

    Not `ma_cross` with more settings. `ma_cross` fires when an EMA pair crosses
    and that is one statement about lag; `lsma` extrapolates and turns before
    price does, `kama` freezes when the path gets noisy and refuses to cross at
    all, `hma` crosses early and then crosses back. On the same periods these
    produce genuinely different entry SETS, not the same entries shifted -- so
    which type wins is itself the finding, and the answer "none of them" is the
    most likely and most useful one.

    EMA is excluded from the axis because `ma_cross` already is that cell.
    """
    if index == 0:
        return None
    table = ctx["xma"][params["kind"]]
    fast, slow = table[params["fast"]], table[params["slow"]]
    if (fast[index] is None or slow[index] is None
            or fast[index - 1] is None or slow[index - 1] is None):
        return None
    if _late(ctx, bars[index], params):
        return None
    now = fast[index] - slow[index]
    before = fast[index - 1] - slow[index - 1]
    side = 1 if before <= 0 < now else -1 if before >= 0 > now else None
    return -side if side and params["direction"] == "fade" else side


def xma_slope_signal(index, bars, ctx, params, _state):
    """The SLOPE of a single average, measured in daily range per bar.

    A cross needs two averages to change places, which is a statement about
    their relative position. This reads one average's own rate of change, so it
    fires while a trend is running rather than at the moment it began -- the
    two are furthest apart in a long grind, where a cross fires once and a slope
    test stays true for weeks.

    Scaled by the average true daily range so the threshold means the same at
    every timeframe and on every instrument, exactly as the stops are.
    """
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    series = ctx["xma"][params["kind"]][params["period"]]
    now, before = series[index], series[index - lookback]
    risk = ctx["risk"][index]
    if now is None or before is None or not risk:
        return None
    if _late(ctx, bars[index], params):
        return None
    move = (now - before) / risk
    threshold = params["slope"]
    side = 1 if move > threshold else -1 if move < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def xma_ribbon_signal(index, bars, ctx, params, _state):
    """Five averages of the same type, and whether they are STACKED and TIGHT.

    A ribbon is not a slower cross. A cross says the fast one is above the slow
    one; a ribbon asks whether ALL FIVE are in order -- which is a statement
    about every horizon at once and is false through most of a chop that a
    single cross would call a trend. Its width, scaled by the daily range, is
    trend MATURITY: a fan that has just opened is early, one at maximum spread
    is late, and no other family here can tell those apart.
    """
    table = ctx["xma"][params["kind"]]
    ladder = [table[n][index] for n in ctx["periods"]["ribbon"]]
    if any(value is None for value in ladder):
        return None
    risk = ctx["risk"][index]
    if not risk or _late(ctx, bars[index], params):
        return None
    rising = all(a > b for a, b in zip(ladder, ladder[1:]))
    falling = all(a < b for a, b in zip(ladder, ladder[1:]))
    if not rising and not falling:
        return None
    if (max(ladder) - min(ladder)) / risk > params["width"]:
        return None
    side = 1 if rising else -1
    return -side if params["direction"] == "fade" else side


# ---- trend quality and regime ---------------------------------------------- #

def linreg_trend_signal(index, bars, ctx, params, _state):
    """A least-squares slope that is BOTH steep enough and well fitted enough.

    The only family that separates direction from conviction. `momentum` reads
    net displacement, so a violent chop that ends higher passes it; this one
    demands that the path was close to a straight line as well, which is a
    different set of days entirely. Requiring both is the point -- either half
    alone is already covered.
    """
    period = params["period"]
    slope = ctx["slope"][period][index]
    fit = ctx["fit"][period][index]
    risk = ctx["risk"][index]
    if slope is None or fit is None or not risk:
        return None
    if _late(ctx, bars[index], params):
        return None
    if fit < params["min_fit"]:
        return None
    move = slope * period / risk
    threshold = params["slope"]
    side = 1 if move > threshold else -1 if move < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def efficiency_signal(index, bars, ctx, params, _state):
    """Kaufman's efficiency ratio: trade the move only when the PATH was clean.

    Direction comes from the move, so this is `momentum` with a shape test
    bolted on -- and the shape test is what makes it a different family. Two
    moves of identical size, one a straight line and one a sawtooth, are the
    same signal to `momentum` and opposite signals here. Which of the two pays
    is a real question, and nothing already in the study asks it.
    """
    period = params["period"]
    ratio = ctx["er"][period][index]
    if ratio is None or index < period:
        return None
    if _late(ctx, bars[index], params):
        return None
    clean = ratio >= params["min_er"]
    if clean != (params["regime"] == "clean"):
        return None
    move = bars[index][C] - bars[index - period][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def regime_switch_signal(index, bars, ctx, params, _state):
    """The variance ratio DECIDES whether to follow the move or fade it.

    Every other family in the study fixes its `direction` on the grid and lets
    the search pick one. This one does not have a direction axis at all: above
    the threshold the series is trending at that horizon and it follows, below
    it the series is reverting and it fades. That makes it the only family whose
    sign changes within a single backtest -- so it cannot correlate with either
    a pure momentum family or a pure reversion one, because it is sometimes
    each.
    """
    step = params["step"]
    ratio = ctx["vr"][step][index]
    if ratio is None or index < step:
        return None
    if _late(ctx, bars[index], params):
        return None
    band = params["band"]
    if 1.0 - band < ratio < 1.0 + band:
        return None
    move = bars[index][C] - bars[index - step][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return side if ratio >= 1.0 + band else -side


def skew_signal(index, bars, ctx, params, _state):
    """Rolling return skewness past a threshold, traded in a fixed direction.

    The third distribution moment, after level and dispersion, and the only
    family reading it. Sustained negative skew -- many small ups, occasional
    violent downs -- is a regime, not an event, so this fires on a state rather
    than on a bar. Direction has to be declared rather than derived, because
    skew has no sign convention that maps onto a trade, and declaring it is what
    makes the result readable.
    """
    period = ctx["periods"]["skew"][0]
    value = ctx["skew"][period][index]
    if value is None:
        return None
    threshold = params["threshold"]
    if params["mode"] == "negative" and value > -threshold:
        return None
    if params["mode"] == "positive" and value < threshold:
        return None
    return 1 if params["side"] == "long" else -1


def vol_regime_signal(index, bars, ctx, params, _state):
    """Short-horizon volatility crossing a multiple of its own long baseline.

    `vol_mode` uses the same two readings as a FILTER -- it asks whether a cell
    only works in the quiet half. This asks the opposite question: is the
    TRANSITION itself tradeable, and in which direction. A filter can never
    answer that, because it never fires on anything; it only removes bars.
    """
    short = ctx["volatility"][index]
    long = ctx["long_volatility"][index]
    if short is None or long is None or long <= 0:
        return None
    if _late(ctx, bars[index], params):
        return None
    ratio = short / long
    expanding = ratio >= params["ratio"]
    if expanding != (params["regime"] == "expanding"):
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


# ---- oscillators ----------------------------------------------------------- #

def stochastic_signal(index, bars, ctx, params, _state):
    """%K crossing %D while both sit in the outer band of the RANGE.

    Where a z-score divides by dispersion, this divides by the range, and the
    two come apart hardest after a shock: one violent bar inflates a z-score's
    denominator so nothing reads extreme for a whole window, while the same bar
    MOVES a stochastic's boundary so every later close is measured against the
    new extreme. `rsi` has the same problem from the other side -- it saturates
    on a grind and never on a gap.
    """
    if index == 0:
        return None
    period = params["period"]
    fast, slow = ctx["stoch_fast"][period], ctx["stoch_slow"][period]
    if (fast[index] is None or slow[index] is None
            or fast[index - 1] is None or slow[index - 1] is None):
        return None
    if _late(ctx, bars[index], params):
        return None
    edge = params["threshold"]
    now = fast[index] - slow[index]
    before = fast[index - 1] - slow[index - 1]
    side = None
    if before <= 0 < now and fast[index] < 100.0 - edge:
        side = 1
    elif before >= 0 > now and fast[index] > edge:
        side = -1
    return -side if side and params["direction"] == "follow" else side


def cci_signal(index, bars, ctx, params, _state):
    """CCI past a threshold: a z-score with a MEAN-ABSOLUTE denominator.

    The distinction is not cosmetic on a fat-tailed series. A squared
    denominator is dominated by the single largest move in its window, so for
    the whole window after a shock `zscore` reads "nothing is extreme any more"
    while `cci` keeps its scale and keeps firing. Those are different days, and
    which of the two is right about them is precisely the question.
    """
    value = ctx["cci"][params["period"]][index]
    if value is None:
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    side = -1 if value > threshold else 1 if value < -threshold else None
    return -side if side and params["direction"] == "follow" else side


def macd_hist_signal(index, bars, ctx, params, _state):
    """The MACD HISTOGRAM turning, not the lines crossing.

    A line cross is `ma_cross` at different periods. The histogram is the gap
    between the two averages, so its turn says that gap has stopped widening --
    an acceleration reading that necessarily comes BEFORE the cross and is
    frequently followed by no cross at all. Those bars, where momentum rolled
    over without the averages ever changing places, are the ones no existing
    family sees.
    """
    if index < 2:
        return None
    histogram = ctx["macd_hist"][params["macd_set"]]
    now, before = histogram[index], histogram[index - 1]
    if now is None or before is None:
        return None
    if _late(ctx, bars[index], params):
        return None
    line = ctx["macd_line"][params["macd_set"]][index]
    if line is None:
        return None
    side = None
    if params["level"] == "zero":
        side = 1 if before <= 0 < now else -1 if before >= 0 > now else None
    else:
        earlier = histogram[index - 2]
        if earlier is None:
            return None
        # A turn in the histogram while it is still on one side of zero: the
        # gap has peaked. This is the reading a cross cannot reach.
        if earlier < before > now and line > 0:
            side = -1
        elif earlier > before < now and line < 0:
            side = 1
    return -side if side and params["direction"] == "fade" else side


def dmi_signal(index, bars, ctx, params, _state):
    """+DI crossing -DI, admitted only when ADX says a trend exists.

    Directional movement is built from how far each bar extended PAST THE
    PREVIOUS BAR'S EXTREMES, which is the one part of a bar nothing else here
    reads -- every other trend family works off closes. A bar that spiked up and
    closed unchanged is invisible to a close-based rule and is a large +DM. ADX
    is strength without sign, so it can only ever be a gate, and this is the
    family that gates on it.
    """
    if index == 0:
        return None
    period = params["period"]
    plus, minus, adx = ctx["plus_di"][period], ctx["minus_di"][period], ctx["adx"][period]
    if (plus[index] is None or minus[index] is None or adx[index] is None
            or plus[index - 1] is None or minus[index - 1] is None):
        return None
    if _late(ctx, bars[index], params):
        return None
    if adx[index] < params["min_adx"]:
        return None
    now = plus[index] - minus[index]
    before = plus[index - 1] - minus[index - 1]
    side = 1 if before <= 0 < now else -1 if before >= 0 > now else None
    return -side if side and params["direction"] == "fade" else side


def aroon_signal(index, bars, ctx, params, _state):
    """Aroon-up crossing Aroon-down: which extreme is more RECENT.

    Denominated in time, not price. A market that keeps setting new highs scores
    100 whether it has doubled or crept up a fraction, and one that has stalled
    decays towards zero without falling at all. `donchian` fires on the bar an
    extreme is exceeded and then says nothing; this measures how long ago that
    was, every bar, and so can report a trend going stale -- a state no level
    based family can name.
    """
    if index == 0:
        return None
    period = params["period"]
    up, down = ctx["aroon_up"][period], ctx["aroon_down"][period]
    if (up[index] is None or down[index] is None
            or up[index - 1] is None or down[index - 1] is None):
        return None
    if _late(ctx, bars[index], params):
        return None
    if max(up[index], down[index]) < params["min_strength"]:
        return None
    now = up[index] - down[index]
    before = up[index - 1] - down[index - 1]
    side = 1 if before <= 0 < now else -1 if before >= 0 > now else None
    return -side if side and params["direction"] == "fade" else side


def rsi_divergence_signal(index, bars, ctx, params, _state):
    """Price sets a new `channel`-bar extreme and RSI does not confirm it.

    A non-confirmation, which is a statement about two series disagreeing --
    the only kind of statement `rsi` (a level) and `donchian` (an extreme)
    cannot make individually. The bar fires the same trigger as `donchian` and
    takes the opposite side of it, conditional on momentum having weakened, so
    the two are anti-correlated by construction rather than merely different.
    """
    period = params["period"]
    channel = params["channel"]
    values = ctx["rsi"][period]
    if index < channel or values[index] is None:
        return None
    if _late(ctx, bars[index], params):
        return None
    bar = bars[index]
    top, bottom = ctx["high"][channel][index], ctx["low"][channel][index]
    if top is None or bottom is None:
        return None
    window = range(index - channel, index)
    readings = [values[j] for j in window if values[j] is not None]
    if len(readings) < channel // 2:
        return None
    side = None
    if bar[C] > top and values[index] < max(readings) - params["gap"]:
        side = -1
    elif bar[C] < bottom and values[index] > min(readings) + params["gap"]:
        side = 1
    return -side if side and params["direction"] == "follow" else side


# ---- price geometry -------------------------------------------------------- #

def supertrend_signal(index, bars, ctx, params, _state):
    """The bar a ratcheting ATR band flips side.

    `keltner` recomputes its band every bar, so a breathing ATR can put the same
    price inside and outside it on consecutive bars. This band only ever moves
    TOWARDS price while the trend holds, so the level that finally breaks is the
    tightest one the whole move produced -- and in a widening range the two
    disagree on almost every bar.
    """
    if index == 0:
        return None
    trend = ctx["supertrend"][params["mult"]]
    if trend[index] is None or trend[index - 1] is None:
        return None
    if _late(ctx, bars[index], params):
        return None
    if trend[index] == trend[index - 1]:
        return None
    side = trend[index]
    return -side if params["direction"] == "fade" else side


def sar_signal(index, bars, ctx, params, _state):
    """The bar a parabolic SAR flips.

    The only construct here whose speed depends on how many NEW EXTREMES a move
    has made rather than on elapsed bars or on volatility. A move that keeps
    extending is chased hard and flips on a shallow pullback; one that stalls at
    the same high is given room. Neither `supertrend`, which is volatility
    paced, nor a bar-count exit, which is clock paced, can express that.
    """
    flip = ctx["sar_flip"][index]
    if not flip:
        return None
    if _late(ctx, bars[index], params):
        return None
    return -flip if params["direction"] == "fade" else flip


def swing_break_signal(index, bars, ctx, params, _state):
    """A close through the last CONFIRMED fractal pivot.

    Not `donchian`. A rolling extreme updates the instant a new high prints, so
    its level is always the most recent one; a swing high is a level the market
    turned away from and then failed to reclaim for `wing` bars, so it can sit
    well BELOW the running high and be broken while no new channel high is made.
    Those bars are the entire difference, and they are the ones where price is
    reclaiming ground rather than extending.
    """
    bar = bars[index]
    wing = params["wing"]
    top, bottom = ctx["pivot_high"][wing][index], ctx["pivot_low"][wing][index]
    if top is None or bottom is None:
        return None
    if _late(ctx, bar, params):
        return None
    side = 1 if bar[C] > top else -1 if bar[C] < bottom else None
    return -side if side and params["direction"] == "fade" else side


def structure_signal(index, bars, ctx, params, _state):
    """A break of the pivot sequence: higher-high/higher-low, then a failure.

    The only family that reads a SEQUENCE of swing points rather than a level.
    `swing_break` fires on any close through the last pivot; this one first
    requires the last two pivots to establish a direction and then fires when
    price closes through the OPPOSITE side -- an uptrend's low giving way. That
    is a first break, and by construction it happens on bars where a
    continuation rule has just been proved wrong, so the two take opposite sides
    of the same structure.
    """
    bar = bars[index]
    wing = params["wing"]
    high = ctx["pivot_high"][wing][index]
    low = ctx["pivot_low"][wing][index]
    previous_high = ctx["prior_pivot_high"][wing][index]
    previous_low = ctx["prior_pivot_low"][wing][index]
    if None in (high, low, previous_high, previous_low):
        return None
    if _late(ctx, bar, params):
        return None
    up = high > previous_high and low > previous_low
    down = high < previous_high and low < previous_low
    side = None
    if params["mode"] == "continuation":
        if up and bar[C] > high:
            side = 1
        elif down and bar[C] < low:
            side = -1
    else:
        if up and bar[C] < low:
            side = -1
        elif down and bar[C] > high:
            side = 1
    return -side if side and params["direction"] == "fade" else side


def fvg_signal(index, bars, ctx, params, _state):
    """Price trading back into an unfilled three-bar imbalance.

    The only LEVEL in the study defined by an ABSENCE of trade. Every other
    reference here -- a channel, a mean, a pivot, a session extreme -- is a price
    the market spent time at; a fair value gap is a band it crossed in one bar
    and never returned to. Whether that band is a level at all is the
    hypothesis, and no rule built on extremes or averages can pose it.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    risk = ctx["risk"][index]
    if not risk:
        return None
    for bottom, top, gap_side in reversed(ctx["gaps"][index]):
        if top - bottom < params["min_width"] * risk:
            continue
        # Touched the zone this bar without closing through it: the retest.
        if bar[L] <= top and bar[H] >= bottom and bottom <= bar[C] <= top:
            side = gap_side
            return -side if params["direction"] == "fade" else side
    return None


def floor_pivot_signal(index, bars, ctx, params, _state):
    """A reaction at yesterday's floor-trader pivot, R1/S1 or R2/S2.

    A different ANCHOR from `pdr`, not a different rule on the same one. `pdr`
    reads yesterday's high and low, two prices that actually traded; the pivot
    is `(H+L+C)/3` and R/S are reflections of the range around it -- levels
    almost nobody transacted at. Whether such a level works is a question about
    self-fulfilment, and it can only be asked with a price that has no trade
    behind it.
    """
    bar = bars[index]
    levels = ctx["floor_pivots"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if levels is None or not atr:
        return None
    if _late(ctx, bar, params):
        return None
    pivot, r1, s1, r2, s2 = levels
    edge = params["buffer_atr"] * atr
    side = None
    if params["level"] == "pivot":
        # The central pivot is a POSITION rather than a barrier -- price above
        # it was the floor's definition of a bid day. The R/S levels are the
        # opposite claim, that a level turns price back, so the two cannot share
        # a branch: reading a touch-and-reject at the pivot would be a third
        # hypothesis smuggled in under the same axis value.
        side = 1 if bar[C] > pivot + edge else -1 if bar[C] < pivot - edge else None
    else:
        resistance, support = (r1, s1) if params["level"] == "first" else (r2, s2)
        if bar[H] >= resistance - edge and bar[C] < resistance:
            side = -1
        elif bar[L] <= support + edge and bar[C] > support:
            side = 1
    return -side if side and params["direction"] == "follow" else side


def pullback_signal(index, bars, ctx, params, _state):
    """A retracement from a recent extreme, measured in daily range, inside a
    trend that is still intact.

    The one family that requires a trend and then waits to be paid a WORSE
    price. Everything else here fires at the edge of a move -- a channel break,
    a new extreme, a thrust bar -- so its entries cluster on the bars this one
    is explicitly sitting out. That makes them close to mutually exclusive by
    construction, which is exactly the property being looked for.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    channel = params["channel"]
    top, bottom = ctx["high"][channel][index], ctx["low"][channel][index]
    reference = ctx["ema"][ctx["periods"]["trend"][params["reference"]]][index]
    risk = ctx["risk"][index]
    if top is None or bottom is None or reference is None or not risk:
        return None
    depth = params["depth"]
    side = None
    if bar[C] > reference and 0 < (top - bar[C]) / risk <= depth:
        side = 1
    elif bar[C] < reference and 0 < (bar[C] - bottom) / risk <= depth:
        side = -1
    return -side if side and params["direction"] == "fade" else side


def volatility_breakout_signal(index, bars, ctx, params, _state):
    """Today's OPEN plus or minus a fraction of yesterday's range.

    Larry Williams' construction, and it is not `orb` and not `pdr`. `orb`
    measures the first bars of today and extends by ATR; `pdr` uses yesterday's
    actual high and low as levels. This one anchors on today's open -- a price
    from this session -- and scales by yesterday's range, so its trigger moves
    with the gap. On a day that opens beyond yesterday's high, `pdr` is already
    triggered at the bell and this one still requires a further push.
    """
    bar = bars[index]
    if ctx["daily"] or _late(ctx, bar, params):
        return None
    day = bar[TS] // 86_400
    window = ctx["prior_range"].get(day)
    opening = ctx["session_open"][index]
    if window is None or opening is None:
        return None
    span = window[0] - window[1]
    if span <= 0:
        return None
    edge = params["fraction"] * span
    side = (1 if bar[C] > opening + edge
            else -1 if bar[C] < opening - edge else None)
    return -side if side and params["direction"] == "fade" else side


def avwap_signal(index, bars, ctx, params, _state):
    """Distance from the volume-weighted average price since the week or month
    began.

    `vwap` resets every session, so it can only say where price sits against
    today's participants -- by definition a one-day horizon. Anchor the same
    arithmetic to a calendar boundary and it answers whether everyone who bought
    this MONTH is above water, which is a position measure no daily reset can
    reach and which barely moves within a session.
    """
    bar = bars[index]
    reference = ctx["avwap"][params["anchor"]][index]
    atr = ctx["atr"][index]
    if reference is None or not atr:
        return None
    distance = (bar[C] - reference) / atr
    threshold = params["threshold_atr"]
    side = 1 if distance > threshold else -1 if distance < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def wick_signal(index, bars, ctx, params, _state):
    """A rejection tail: most of the bar's range on one side of a small body.

    Bar ANATOMY, and nothing else reads it. `climax` will look at where a bar
    closed only after volume and range have both agreed, and every other family
    discards the wicks entirely. A long lower wick is a price the market visited
    and rejected inside a single bar; whether that rejection carries information
    is a question in its own right, and it fires on ordinary bars that no
    volume or range filter would ever admit.
    """
    bar = bars[index]
    upper, lower, body = (ctx["wick_upper"][index], ctx["wick_lower"][index],
                          ctx["body"][index])
    if upper is None or lower is None or body is None:
        return None
    if _late(ctx, bar, params):
        return None
    if body > params["max_body"]:
        return None
    tail = params["min_tail"]
    side = None
    if lower >= tail and upper < tail:
        side = 1
    elif upper >= tail and lower < tail:
        side = -1
    if side is None:
        return None
    if params["wick"] == "with_trend":
        reference = ctx["ema"][ctx["periods"]["trend"]["ema_20d"]][index]
        if reference is None or (bar[C] > reference) != (side == 1):
            return None
    return -side if params["direction"] == "fade" else side


# ---- volume, read cumulatively --------------------------------------------- #

def obv_break_signal(index, bars, ctx, params, _state):
    """On-balance volume breaking its own channel, price ignored.

    `climax` and `volume_thrust` read ONE bar's volume, so they see events. This
    accumulates, so it sees whether the events have been one-sided over a
    stretch -- and it can break out while price has not, which is the whole
    reason to run it. The channel is on the OBV line itself, so no price level
    enters the trigger at all.
    """
    channel = params["channel"]
    top, bottom = ctx["obv_high"][channel][index], ctx["obv_low"][channel][index]
    if top is None or bottom is None:
        return None
    if _late(ctx, bars[index], params):
        return None
    value = ctx["obv"][index]
    side = 1 if value > top else -1 if value < bottom else None
    return -side if side and params["direction"] == "fade" else side


def obv_divergence_signal(index, bars, ctx, params, _state):
    """Price makes a new channel extreme; cumulative volume does not follow.

    The only way an OHLCV series can say "this move is not being paid for". It
    fires on exactly `donchian`'s trigger bars and takes the other side of a
    subset of them -- the subset where the flow disagreed -- so it is not merely
    uncorrelated with the breakout family, it is a partition of it.
    """
    bar = bars[index]
    channel = params["channel"]
    top, bottom = ctx["high"][channel][index], ctx["low"][channel][index]
    obv_top = ctx["obv_high"][channel][index]
    obv_bottom = ctx["obv_low"][channel][index]
    if None in (top, bottom, obv_top, obv_bottom):
        return None
    if _late(ctx, bar, params):
        return None
    value = ctx["obv"][index]
    side = None
    if bar[C] > top and value < obv_top:
        side = -1
    elif bar[C] < bottom and value > obv_bottom:
        side = 1
    return -side if side and params["direction"] == "follow" else side


def mfi_signal(index, bars, ctx, params, _state):
    """Money Flow Index past a threshold: RSI weighted by money, not by price.

    `rsi` counts how far price moved up against down; this counts how much value
    changed hands doing it. They separate on a drift that happens on nothing --
    the RSI saturates and the MFI stays mid-range -- and on a violent bar that
    retraces, where the reverse happens. Those are the bars the pair was added
    to distinguish.
    """
    value = ctx["mfi"][params["period"]][index]
    if value is None:
        return None
    if _late(ctx, bars[index], params):
        return None
    top = params["threshold"]
    side = -1 if value > top else 1 if value < 100.0 - top else None
    return -side if side and params["direction"] == "follow" else side


def cmf_signal(index, bars, ctx, params, _state):
    """Chaikin money flow crossing a threshold.

    Volume weighted by where each bar CLOSED inside its own range, so a bar that
    ranged wide and closed at its high counts as fully bought while one that
    closed mid-range counts for nothing. `obv` credits an entire bar's volume to
    the sign of one close-to-close change, so it cannot tell a decisive bar from
    a marginal one; on a choppy stretch the two produce opposite readings.
    """
    value = ctx["cmf"][params["period"]][index]
    if value is None:
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    side = 1 if value > threshold else -1 if value < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def rvol_signal(index, bars, ctx, params, _state):
    """A directional bar on volume that is unusual FOR THAT TIME OF DAY.

    Intraday volume is U-shaped, so a rolling mean calls every session open and
    every close unusual -- which is why `volume_thrust` and `climax` pile their
    entries onto the edges of the day and end up measuring the open. Comparing a
    bar only with the same clock minute on earlier days removes the shape and
    leaves the surprise, so this fires in the middle of the day where those two
    almost never do.
    """
    bar = bars[index]
    relative = ctx["rvol"]
    if relative is None:
        return None
    value = relative[index]
    atr = ctx["atr"][index]
    if value is None or not atr:
        return None
    if _late(ctx, bar, params):
        return None
    if value < params["rvol"]:
        return None
    move = bar[C] - bar[O]
    if abs(move) < params["threshold_atr"] * atr:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


# ---- relative to a benchmark ----------------------------------------------- #

def rel_momentum_signal(index, bars, ctx, params, _state):
    """The symbol's move against its benchmark's, over the same window.

    THE FAMILY THAT ANSWERS "why does the stock study only like TSLA". A US
    large cap is mostly its index, so eleven single-name studies are close to
    eleven noisy copies of one index study -- and the copy that wins is the one
    whose spread is smallest against its range, which `cost` shows is TSLA by a
    factor of eight. Dividing by the benchmark removes the shared factor and
    leaves the part that could actually differ between names.

    Read off the ratio SERIES rather than a difference of returns, so the number
    is a price and the usual machinery means what it always means.
    """
    relative = ctx["relative"]
    lookback = params["lookback"]
    if relative is None or index < lookback:
        return None
    line = relative["line"]
    now, before = line[index], line[index - lookback]
    if now is None or before is None or before <= 0:
        return None
    move = now / before - 1.0
    threshold = params["threshold"] / 100.0
    side = 1 if move > threshold else -1 if move < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def rel_zscore_signal(index, bars, ctx, params, _state):
    """How far the ratio to the benchmark sits from its own mean.

    Pairs mean-reversion, stated in this study's units. `zscore` on the raw
    price is dominated by the market move that every name shares, so on a broad
    selloff it calls all eleven stocks oversold at once and the eleven results
    are one result. On the ratio the shared move cancels and what is left is
    whether THIS name has moved too far against its index -- a claim that can be
    different for each of them on the same day.
    """
    relative = ctx["relative"]
    if relative is None:
        return None
    period = params["period"]
    line = relative["line"][index]
    mean = relative["mean"][period][index]
    sigma = relative["sigma"][period][index]
    if line is None or mean is None or sigma is None or sigma <= 0:
        return None
    if _late(ctx, bars[index], params):
        return None
    z = (line - mean) / sigma
    threshold = params["threshold_z"]
    side = -1 if z > threshold else 1 if z < -threshold else None
    return -side if side and params["direction"] == "follow" else side


def rel_break_signal(index, bars, ctx, params, _state):
    """The ratio to the benchmark breaking its own channel.

    Relative-strength breakout: the name starting to outperform, independent of
    whether the market is up or down. `donchian` on the price cannot separate
    those two -- on a rising index every name breaks out together -- so its
    winners across a stock universe are close to one trade held eleven times.
    """
    relative = ctx["relative"]
    if relative is None:
        return None
    channel = params["channel"]
    line = relative["line"][index]
    top = relative["high"][channel][index]
    bottom = relative["low"][channel][index]
    if line is None or top is None or bottom is None:
        return None
    side = 1 if line > top else -1 if line < bottom else None
    return -side if side and params["direction"] == "fade" else side


# ---- the calendar, once more ----------------------------------------------- #

def day_of_week_signal(index, bars, ctx, params, _state):
    """A fixed direction on one weekday.

    The weekly counterpart to `seasonality`, and carried for the same reason:
    five days times two directions is ten coin flips and one of them will look
    excellent on any series. It is here so the null control can price how good
    "excellent" has to be, and so that a calendar effect anyone can name is
    measured rather than assumed away.
    """
    if es.weekday(bars[index][TS]) != params["weekday"]:
        return None
    return 1 if params["side"] == "long" else -1


# --------------------------------------------------------------------------- #
# COMBINATIONS
#
# Thirteen families whose thesis is not a reading but a PAIRING of readings, and
# the reason they are a section rather than thirteen more entries in the list
# above is that every one of them had to survive the same objection first:
#
#   THE SHARED GRID ALREADY GATES EVERY FAMILY, TWICE.
#
# `common` hands every one of the sixty-two an `ema_20d`/`ema_50d` trend filter
# and a `calm` volatility filter. So "ORB plus a moving average" and "VWAP plus
# a volatility regime" are not new strategies -- they are cells the study has
# been scoring since the first run, and adding them as families would be a way
# of counting the same hypothesis twice while calling the duplicate a discovery.
#
# What is genuinely absent is everything a combination can mean BEYOND
# `price > EMA`:
#
#   a confirmer that is not a moving average    `gate_opinion`
#   a confirmer read the WRONG way round        `polarity`
#   many weak readings voting                   `confluence`
#   one reading choosing which RULE runs        `regime_router`
#   a setup and a trigger on DIFFERENT bars     `two_stage`, `break_retest`
#   a trigger that fired and then FAILED        `trap`
#   the same construct at two horizons          `nested`
#   two constructs turning at the same time     `cross_timing`
#   two independent LEVELS at the same price    `level_confluence`
#   a move the benchmark did not make           `idio_break`
#
# The last six matter most, because they fire on bars where no existing family
# fires AT ALL rather than on a subset of some family's bars. A gate can only
# ever refuse trades an ungated family already took, so its result is bounded by
# that family's; a two-stage rule enters on the breakout bar while `squeeze`
# entered on the compression bar three bars earlier, at a different price and
# often on the other side.
#
# WHY NONE OF THESE ACCUMULATE STATE. `session_running` says it plainly: the
# engine calls a signal only on bars with no open position, so a rule that built
# its own running window would silently skip bars and measure a short day.
# Everything here therefore reads precomputed arrays or scans BACKWARDS over a
# bounded window -- at most eight bars -- and `gated_orb` reads the `opening`
# block rather than accumulating the range the way `orb` still does.
#
# WHAT THIS COSTS. Roughly a third more search budget on top of the 76,356 cells
# the other sixty-two spend at 30m, and a combination study is exactly the kind
# that inflates a budget quietly: a pairing multiplies two axis sets and then
# multiplies the 72-cell `common` grid on top of that. `budget --groups
# gate,combo` prints the number, and nothing here should be read before `why`
# has priced it -- a coin flip picking the best of 28,000 draws is not obviously
# worse than a thesis picking the best of 28,000.
# --------------------------------------------------------------------------- #

#: The confirmers a gated family can consult, and the context block each needs.
#:
#: DELIBERATELY NOT A MOVING AVERAGE. `common` already supplies two of those to
#: every family in the study, so a moving-average gate here would measure a cell
#: the original grid has scored since the first run. Each of these says
#: something an EMA cannot: `adx` separates "trending" from "up", `obv` reads
#: participation rather than price, `rvol` asks whether anybody was there, and
#: `relative` asks whether the move belongs to the name or to its index.
GATE_READS = {
    "rsi": (),
    "vwap": (),
    "adx": ("adx",),
    "macd": ("macd",),
    "supertrend": ("supertrend",),
    "obv": ("obv",),
    "rvol": ("rvol",),
    "relative": ("relative",),
}
GATES = tuple(GATE_READS)


def gate_reads():
    """Every block the gate vocabulary can reach, as a tuple for `Family.reads`.

    The union rather than the per-cell requirement, because a worker builds its
    context once for the whole grid and the grid sweeps every gate. That is six
    blocks -- `adx`, `macd`, `supertrend`, `obv`, `rvol`, `relative` -- and it is
    why `--groups gate` is not a cheap run.
    """
    return tuple(sorted({block for blocks in GATE_READS.values()
                         for block in blocks}))


def gate_choices(symbol, daily):
    """The gates that can actually be READ here, as an axis.

    Dropped rather than left in to return `None` forever: an axis value that
    never produces a signal is not a negative result about the gate, it is a
    dead cell that still costs budget and still dilutes the neighbour test.

      * `vwap` and `rvol` need a session -- a daily bar has one VWAP point and no
        same-clock-minute comparison to make -- so they go on daily bars.
      * `relative` needs a benchmark, which only the US names, JPM and ETHUSD
        have.
    """
    return tuple(name for name in GATES
                 if not (daily and name in ("vwap", "rvol"))
                 and not (name == "relative" and symbol not in BENCHMARK))


def gate_opinion(name, index, bars, ctx):
    """One confirmer's view at `index`: 1 long, -1 short, 0 none, `None` unreadable.

    THE THREE-VALUED RETURN IS THE POINT. A gate that is merely readable is not
    the same as a gate that has something to say, and collapsing the two would
    turn `adx` -- which is explicitly silent below 20, because a direction read
    off a flat DMI is noise -- into a coin flip on exactly the bars it was built
    to sit out. `0` refuses the trade under either polarity; `None` refuses it
    too, but for a different reason and without claiming the market was
    undecided.
    """
    bar, p = bars[index], ctx["periods"]
    if name == "rsi":
        # The SLOW RSI, at 50. `rsi` the family trades the fast one at an
        # extreme; this reads the same construct as a standing bias, which is
        # the opposite use of it and is why the two do not collide.
        value = ctx["rsi"][p["rsi"][1]][index]
        if not present(value):
            return None
        return 1 if value > 50.0 else -1 if value < 50.0 else 0
    if name == "vwap":
        line = ctx["vwap"]
        if line is None:
            return None
        value = line[index]
        if not present(value):
            return None
        return 1 if bar[C] > value else -1 if bar[C] < value else 0
    if name == "adx":
        period = p["adx"][1]
        strength = ctx["adx"][period][index]
        plus, minus = ctx["plus_di"][period][index], ctx["minus_di"][period][index]
        if not present(strength) or not present(plus) or not present(minus):
            return None
        # Wilder's own threshold, and the only gate in the vocabulary that can
        # decline to have a view while being perfectly readable.
        if strength < 20.0:
            return 0
        return 1 if plus > minus else -1
    if name == "macd":
        value = ctx["macd_hist"][p["macd"][1]][index]
        if not present(value):
            return None
        return 1 if value > 0 else -1 if value < 0 else 0
    if name == "supertrend":
        # The wide band. The narrow one flips on ordinary pullbacks, which makes
        # it a trigger rather than a state, and a gate wants the state.
        value = ctx["supertrend"][SUPERTREND_MULTIPLES[-1]][index]
        if value is None:
            return None
        return value if value in (1, -1) else 0
    if name == "obv":
        channel = p["obv"][0]
        line = ctx["obv"][index]
        top, bottom = ctx["obv_high"][channel][index], ctx["obv_low"][channel][index]
        if (not present(line) or not present(top) or not present(bottom)
                or top <= bottom):
            return None
        middle = 0.5 * (top + bottom)
        return 1 if line > middle else -1 if line < middle else 0
    if name == "rvol":
        # PARTICIPATION, GIVEN A SIGN. Relative volume has no direction of its
        # own, so a bare "volume is high" gate would refuse nothing on one side
        # and everything on the other. Signing it by this bar's own body makes it
        # the only gate in the set that asks whether the move being entered was
        # one anybody actually traded.
        table = ctx["rvol"]
        if table is None:
            return None
        value = table[index]
        if not present(value):
            return None
        if value < 1.5:
            return 0
        move = bar[C] - bar[O]
        return 1 if move > 0 else -1 if move < 0 else 0
    if name == "relative":
        relative = ctx.get("relative")
        if not relative:
            return None
        period = p["rel"][1]
        line = relative["line"][index]
        mean, sigma = relative["mean"][period][index], relative["sigma"][period][index]
        if (not present(line) or not present(mean) or not present(sigma)
                or sigma <= 0):
            return None
        z = (line - mean) / sigma
        return 1 if z > 0.5 else -1 if z < -0.5 else 0
    raise KeyError(f"unknown gate {name!r}")


def gate_accepts(name, index, bars, ctx, side, polarity):
    """Whether the gate lets `side` through.

    `polarity` IS A HYPOTHESIS, NOT A CONTROL. `oppose` is not the null of
    `confirm` -- it is the claim that the confirmer is a contrary indicator, and
    on a breakout it names a specific and well-known setup: break the range while
    the slow oscillator is still on the other side of its midline, which is the
    first leg out of a base rather than the fifth bar of a trend. If both
    polarities pass the gates, the gate is reading noise, and the thing to look
    at is the neighbour count rather than the return.
    """
    view = gate_opinion(name, index, bars, ctx)
    if view is None or view == 0:
        return False
    return view == side if polarity == "confirm" else view == -side


#: The mean-reversion entries `gated_fade` chooses between, at FIXED thresholds.
#:
#: Fixed on purpose. Each of these already exists as a family with its own swept
#: threshold, and re-sweeping them here would ask two questions at once -- "does
#: the gate help" and "was the threshold wrong" -- and answer neither. The values
#: are the middle of each family's own published sweep, so a gated result is
#: comparable with the ungated one cell for cell.
FADE_TRIGGERS = ("zscore", "vwap_dev", "rsi_extreme", "keltner")


def fade_choices(daily):
    """`vwap_dev` needs a session VWAP, which a daily bar does not have."""
    return tuple(n for n in FADE_TRIGGERS if not (daily and n == "vwap_dev"))


def fade_side(name, index, bars, ctx):
    """The side a mean-reversion construct would take, or `None` for no extreme.

    `None` here means "nothing is stretched", which is the ordinary state of the
    market and not a missing reading. `confluence` maps it to a zero vote for
    that reason; a gated family simply does not fire.
    """
    bar, p = bars[index], ctx["periods"]
    atr = ctx["atr"][index]
    if not atr or not present(atr):
        return None
    if name == "zscore":
        period = p["zscore"][1]
        mean, sigma = ctx["mean"][period][index], ctx["sigma"][period][index]
        if not present(mean) or not present(sigma) or sigma <= 0:
            return None
        z = (bar[C] - mean) / sigma
        return -1 if z > 2.0 else 1 if z < -2.0 else None
    if name == "vwap_dev":
        line = ctx["vwap"]
        if line is None:
            return None
        value = line[index]
        if not present(value):
            return None
        distance = (bar[C] - value) / atr
        return -1 if distance > 1.0 else 1 if distance < -1.0 else None
    if name == "rsi_extreme":
        value = ctx["rsi"][p["rsi"][0]][index]
        if not present(value):
            return None
        return -1 if value > 75.0 else 1 if value < 25.0 else None
    if name == "keltner":
        reference = ctx["ema"][p["trend"]["ema_20d"]][index]
        if not present(reference):
            return None
        distance = (bar[C] - reference) / atr
        return -1 if distance > 2.0 else 1 if distance < -2.0 else None
    raise KeyError(f"unknown fade trigger {name!r}")


def _breakout_levels(name, index, bars, ctx):
    """`(upper, lower)` for a level construct AT `index`, or `(None, None)`.

    Evaluated at the index asked for rather than at the current bar, because
    `trap` and `break_retest` both need the level AS IT STOOD when it broke. A
    Donchian channel that has since expanded past the failed break would make
    every trap look like a level that is still holding.
    """
    p = ctx["periods"]
    if name == "pdr":
        window = ctx["prior_range"].get(ctx["day"][index])
        return window if window else (None, None)
    if name == "donchian":
        channel = 4 * p["session"]
        return ctx["high"][channel][index], ctx["low"][channel][index]
    if name == "swing":
        wing = p["wing"][1]
        return ctx["pivot_high"][wing][index], ctx["pivot_low"][wing][index]
    raise KeyError(f"unknown level {name!r}")


# ---- gated entries: one thesis, one independent confirmer ------------------ #

def gated_orb_signal(index, bars, ctx, params, _state):
    """The opening-range break, taken only when a second reading agrees.

    NOT `orb` WITH THE `trend` AXIS SET. That axis is `price > EMA`, which on a
    breakout bar is very nearly implied by the breakout itself -- a close above
    the opening range on a normal day is usually a close above a slow average --
    so the filter refuses almost nothing and the two cells are close to the same
    trade. Every gate here is orthogonal to price level by construction: `adx`
    reads how ORDERED the move has been, `obv` whether volume went with it,
    `rvol` whether the session was busy at all, `relative` whether the index did
    it too. Those disagree with the breakout constantly, and the bars they refuse
    are the whole experiment.

    The range comes from the `opening` block rather than from signal state. `orb`
    accumulates its own and is therefore wrong on any day where a position was
    still open during the first bars -- the engine does not call a signal then,
    so the range it measures is a short one.
    """
    day, minute = ctx["day"][index], ctx["minute"][index]
    window = ctx["opening"][1].get(day)
    if window is None or _late(ctx, bars[index], params):
        return None
    if minute < ctx["cfg"]["session"][0] + ctx["bar_minutes"]:
        return None
    top, bottom = window
    close = bars[index][C]
    side = 1 if close > top else -1 if close < bottom else None
    if side is None:
        return None
    if not gate_accepts(params["gate"], index, bars, ctx, side, params["polarity"]):
        return None
    return side


def gated_donchian_signal(index, bars, ctx, params, _state):
    """The channel break, taken only when a second reading agrees.

    The multi-day counterpart to `gated_orb`, and the pair is the point: if
    gating helps one and not the other, the gate is timing the SESSION rather
    than the move, which is a different claim and a much weaker one.
    """
    channel = params["channel"]
    top, bottom = ctx["high"][channel][index], ctx["low"][channel][index]
    if not present(top) or not present(bottom) or _late(ctx, bars[index], params):
        return None
    close = bars[index][C]
    side = 1 if close > top else -1 if close < bottom else None
    if side is None:
        return None
    if not gate_accepts(params["gate"], index, bars, ctx, side, params["polarity"]):
        return None
    return side


def gated_fade_signal(index, bars, ctx, params, _state):
    """A mean-reversion entry, taken only when a second reading agrees.

    THE ASYMMETRY WITH THE BREAKOUT FAMILIES IS THE HYPOTHESIS. A confirmer that
    helps a breakout is saying "this move is real"; the same confirmer on a fade
    is saying "this move is real, so do not fade it" -- which means `confirm` and
    `oppose` should swap sign between `gated_donchian` and this family if the
    gates are reading anything at all. They are the same eight readings and the
    same two polarities on purpose, so that swap is directly checkable rather
    than a story told afterwards.
    """
    side = fade_side(params["trigger"], index, bars, ctx)
    if side is None or _late(ctx, bars[index], params):
        return None
    if not gate_accepts(params["gate"], index, bars, ctx, side, params["polarity"]):
        return None
    return side


def gated_swing_signal(index, bars, ctx, params, _state):
    """A gated entry that is HELD PAST THE CLOSE.

    The same question `swing_donchian` asks of `donchian` -- does the edge live
    in the entry or in the holding -- asked of the gate instead. A confirmer that
    only pays inside the session is a statement about intraday follow-through;
    one that pays over five days is a statement about the move it selected. The
    two are routinely confused, and running the identical gate vocabulary under
    both holding regimes is the only way to tell them apart here.

    No `last_entry_minute`: swing cells do not carry one, so `_late` is not
    called and must not be.
    """
    p = ctx["periods"]
    close = bars[index][C]
    if params["trigger"] == "donchian":
        channel = 20 * p["session"]
        top, bottom = ctx["high"][channel][index], ctx["low"][channel][index]
        if not present(top) or not present(bottom):
            return None
        side = 1 if close > top else -1 if close < bottom else None
    else:
        if index == 0:
            return None
        fast, slow = ctx["fast"][p["fast"][0]], ctx["slow"][p["slow"][1]]
        if (not present(fast[index]) or not present(slow[index])
                or not present(fast[index - 1]) or not present(slow[index - 1])):
            return None
        now, before = fast[index] - slow[index], fast[index - 1] - slow[index - 1]
        side = 1 if before <= 0 < now else -1 if before >= 0 > now else None
    if side is None:
        return None
    if not gate_accepts(params["gate"], index, bars, ctx, side, params["polarity"]):
        return None
    return side


# ---- fused theses: neither reading is a filter of the other ---------------- #

#: The two ballots `confluence` counts. Each pool is five readings that answer
#: the SAME question in five different ways, which is what makes a vote over it
#: meaningful -- a vote over five unrelated questions is just a noisier version
#: of whichever one dominates.
CONFLUENCE_POOLS = {
    "trend": ("rsi", "macd", "supertrend", "obv", "higher_tf"),
    "exhaustion": ("rsi_extreme", "zscore", "vwap_dev", "wick_rej", "range_pos"),
}


def pool_view(name, index, bars, ctx):
    """One ballot: 1, -1, 0 for no view, `None` for unreadable.

    Trend members are signed as a BIAS and exhaustion members as the side the
    fade would take, so both pools count in the same direction and `net` means
    the same thing in either.
    """
    if name in ("rsi", "macd", "supertrend", "obv"):
        return gate_opinion(name, index, bars, ctx)
    p = ctx["periods"]
    if name == "higher_tf":
        # A five-session return: the coarsest reading in the study that does not
        # require a second bar table, and the only pool member that can disagree
        # with all four oscillators at once because it does not smooth.
        span = 5 * p["session"]
        if index < span:
            return None
        before = bars[index - span][C]
        if before <= 0:
            return None
        move = bars[index][C] / before - 1.0
        return 1 if move > 0 else -1 if move < 0 else 0
    if name == "range_pos":
        top, bottom = ctx["session_high"][index], ctx["session_low"][index]
        if not present(top) or not present(bottom) or top <= bottom:
            return None
        where = (bars[index][C] - bottom) / (top - bottom)
        return -1 if where > 0.8 else 1 if where < 0.2 else 0
    if name == "wick_rej":
        upper, lower = ctx["wick_upper"][index], ctx["wick_lower"][index]
        if not present(upper) or not present(lower):
            return None
        if upper >= 0.5 and upper > lower:
            return -1
        if lower >= 0.5 and lower > upper:
            return 1
        return 0
    value = fade_side(name, index, bars, ctx)
    return 0 if value is None else value


def confluence_signal(index, bars, ctx, params, _state):
    """Five readings vote; trade the margin when it is wide enough.

    THE ONE FAMILY HERE THAT CANNOT BE WRITTEN AS A FILTER. Every gated family is
    `A and B`, so its trades are a subset of A's. A vote of two out of five fires
    on bars where NO single member is at an extreme -- three mild agreements,
    none of which any standalone family would have traded -- and it also refuses
    bars where one member is screaming and the other four are flat, which is
    exactly where `rsi`, `zscore` and `wick` all enter. The overlap with any one
    of them is small by construction rather than by tuning.

    `mode` is the crowding question, and it is worth its two cells: when every
    reading agrees, is that a trend to join or a trade everybody is already in?
    The study has no other family that can even ask. `regime_switch` picks
    between momentum and reversion from the variance ratio, which is a statement
    about the price path, not about how many indicators point the same way.

    Three readable ballots are required, so a pool that has half degraded --
    `vwap_dev` on a daily bar, `range_pos` where a session barely exists -- fails
    to a refusal rather than to a two-member vote wearing a five-member name.
    """
    if _late(ctx, bars[index], params):
        return None
    views = [pool_view(name, index, bars, ctx)
             for name in CONFLUENCE_POOLS[params["pool"]]]
    counted = [v for v in views if v is not None]
    if len(counted) < 3:
        return None
    net = sum(counted)
    if abs(net) < params["votes"]:
        return None
    side = 1 if net > 0 else -1
    return side if params["mode"] == "follow" else -side


def two_stage_signal(index, bars, ctx, params, _state):
    """A coil arms the trade; the break of the coil's own range takes it.

    `squeeze` and `nr` both enter ON the compression bar, because the engine
    fills at the next open and cannot rest a stop order on a level. That means
    they trade the pause and guess the direction from where price came from. This
    one waits: the coil is a setup that expires, the entry is the close beyond
    the range built since the coil, and the direction is whichever side actually
    broke. On a coil that breaks downward after an uptrend the two families take
    OPPOSITE sides of the same setup, which is as clean a disagreement as this
    study contains.

    The window is bars, not sessions, and is allowed to span the overnight gap
    for the same reason a Donchian channel is: the coil is a shape in the bar
    series, and cutting it at the close would make the family's meaning depend on
    where in the day the compression happened.
    """
    if _late(ctx, bars[index], params):
        return None
    window = params["window"]
    if index < window + 1:
        return None
    compress = ctx["periods"]["compress"][0]
    armed = None
    for j in range(index - window, index):
        if params["coil"] == "squeeze":
            width, floor = ctx["width"][j], ctx["width_min"][compress][j]
            if present(width) and present(floor) and width <= 1.1 * floor:
                armed = j
                break
        else:
            floor = ctx["span_min"][compress][j]
            if present(floor) and bars[j][H] - bars[j][L] <= floor + 1e-12:
                armed = j
                break
    if armed is None:
        return None
    top = max(bars[k][H] for k in range(armed, index))
    bottom = min(bars[k][L] for k in range(armed, index))
    close = bars[index][C]
    side = 1 if close > top else -1 if close < bottom else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def break_retest_signal(index, bars, ctx, params, _state):
    """A level breaks, price comes back to it, and it holds.

    The entry every breakout family is structurally unable to take. `pdr` and
    `donchian` fire on the bar that closes through the level and are then in a
    position, so the pullback that follows is something they sit through rather
    than something they can act on -- and the engine allows one entry a day, so
    even a flat rule could not re-enter. This family DECLINES the break itself
    and waits for the retest, which is a strictly later bar and, on any break
    that pulls back at all, a better price.

    `retest` names what price has to come back TO. The broken level is the
    textbook version. Session VWAP is the version an intraday desk actually
    watches, and it is a genuinely different location: after a wide break the
    VWAP can sit a long way under the level, so the two ask for different
    pullback depths on the same day and disagree about whether one happened.

    Tolerance is fixed at a quarter of ATR rather than swept. The axis that
    matters is how long the break is allowed to take to come back; sweeping both
    would turn one hypothesis into a grid over what the word "retest" means.
    """
    bar = bars[index]
    if ctx["daily"] or _late(ctx, bar, params):
        return None
    atr = ctx["atr"][index]
    if not atr or not present(atr):
        return None
    day, window = ctx["day"][index], params["window"]
    broke, level = 0, None
    for j in range(max(1, index - window), index):
        if ctx["day"][j] != day:
            continue
        top, bottom = _breakout_levels(params["level"], j, bars, ctx)
        if not present(top) or not present(bottom):
            continue
        if bars[j][C] > top:
            broke, level = 1, top
            break
        if bars[j][C] < bottom:
            broke, level = -1, bottom
            break
    if not broke:
        return None
    if params["retest"] == "vwap":
        line = ctx["vwap"]
        if line is None or not present(line[index]):
            return None
        reference = line[index]
    else:
        reference = level
    edge = 0.25 * atr
    if broke == 1:
        return 1 if bar[L] <= reference + edge and bar[C] > reference else None
    return -1 if bar[H] >= reference - edge and bar[C] < reference else None


def regime_router_signal(index, bars, ctx, params, _state):
    """A regime reading chooses WHICH RULE runs -- breakout or fade.

    NOT `regime_switch`, and the difference is the whole family. That one reads
    the variance ratio and uses it to pick the SIGN of a single momentum rule, so
    it fires exactly where that rule fires and only argues about direction. This
    one switches the ENTRY CONDITION: in a trending regime it needs a channel
    break, in a choppy one a two-sigma stretch, and those two conditions are
    almost never true on the same bar. The set of bars it trades is therefore the
    union of two nearly disjoint sets rather than a re-signing of one.

    No `direction` axis, for the same reason `regime_switch` has none: the regime
    supplies the choice, and handing the search a switch to invert it would let
    it discover "always do the opposite of what the regime says", which is one
    cell away from having no regime reading at all.

    Every regime carries a DEAD BAND. A router with a single threshold is a coin
    flip at the boundary and spends most of its life within noise of it; refusing
    to trade in the middle is what makes the two branches mean something. That is
    also why `strictness` is a label and not a number -- the loose and strict
    pairs sit on three different scales, and stepping "one along" a shared axis
    would be meaningless.
    """
    if _late(ctx, bars[index], params):
        return None
    p = ctx["periods"]
    strict = params["strictness"] == "strict"
    regime = params["regime"]
    if regime == "er":
        value = ctx["er"][p["er"][1]][index]
        low, high = (0.15, 0.55) if strict else (0.20, 0.40)
    elif regime == "vr":
        value = ctx["vr"][p["vr_step"][1]][index]
        band = 0.35 if strict else 0.15
        low, high = 1.0 - band, 1.0 + band
    else:
        value = ctx["adx"][p["adx"][1]][index]
        low, high = (15.0, 35.0) if strict else (18.0, 30.0)
    if not present(value):
        return None
    if value >= high:
        channel = 4 * p["session"]
        top, bottom = ctx["high"][channel][index], ctx["low"][channel][index]
        if not present(top) or not present(bottom):
            return None
        close = bars[index][C]
        return 1 if close > top else -1 if close < bottom else None
    if value <= low:
        return fade_side("zscore", index, bars, ctx)
    return None


#: What `nested` reads at two horizons. Written out rather than inferred because
#: "the slow one agrees" is not the same shape for a breakout as for a fade: for
#: a breakout the slow horizon supplies a POSITION (which half of its own range
#: price sits in), for a fade it supplies a SIGN (which side of its own mean).
NESTED_CONSTRUCTS = ("donchian", "zscore", "rsi", "momentum")


def _nested_pair(construct, index, bars, ctx):
    """`(fast entry side, slow standing bias)`; either may be `None`."""
    bar, p = bars[index], ctx["periods"]
    if construct == "donchian":
        quick, slow = p["session"], 20 * p["session"]
        top, bottom = ctx["high"][quick][index], ctx["low"][quick][index]
        far_top, far_bottom = ctx["high"][slow][index], ctx["low"][slow][index]
        if (not present(top) or not present(bottom) or not present(far_top)
                or not present(far_bottom) or far_top <= far_bottom):
            return None, None
        fast = 1 if bar[C] > top else -1 if bar[C] < bottom else None
        middle = 0.5 * (far_top + far_bottom)
        return fast, (1 if bar[C] > middle else -1)
    if construct == "zscore":
        quick, slow = p["zscore"][0], p["zscore"][2]
        mean, sigma = ctx["mean"][quick][index], ctx["sigma"][quick][index]
        far_mean, far_sigma = ctx["mean"][slow][index], ctx["sigma"][slow][index]
        if (not present(mean) or not present(sigma) or sigma <= 0
                or not present(far_mean) or not present(far_sigma) or far_sigma <= 0):
            return None, None
        z = (bar[C] - mean) / sigma
        far = (bar[C] - far_mean) / far_sigma
        fast = -1 if z > 2.0 else 1 if z < -2.0 else None
        # The slow bias for a FADE is the side the slow horizon would buy, so it
        # is the sign of minus-z: still cheap on the long view means still long.
        return fast, (1 if far < 0 else -1)
    if construct == "rsi":
        quick, slow = p["rsi"][0], p["rsi"][1]
        value, far = ctx["rsi"][quick][index], ctx["rsi"][slow][index]
        if not present(value) or not present(far):
            return None, None
        fast = -1 if value > 75.0 else 1 if value < 25.0 else None
        return fast, (1 if far > 50.0 else -1)
    quick, slow = p["session"], 5 * p["session"]
    atr = ctx["atr"][index]
    if index < slow or not atr or not present(atr):
        return None, None
    move = bar[C] - bars[index - quick][C]
    far = bar[C] - bars[index - slow][C]
    fast = 1 if move > atr else -1 if move < -atr else None
    return fast, (1 if far > 0 else -1 if far < 0 else 0)


def nested_signal(index, bars, ctx, params, _state):
    """The SAME construct at two horizons, required to agree -- or to clash.

    Multi-timeframe confirmation is the most widely used combination there is and
    the study could not express it. `gated_*` pairs two DIFFERENT constructs,
    which confounds the horizon with the reading: if a Donchian break gated by
    MACD works, that is either "the slow view matters" or "MACD matters" and
    there is no way to tell. Holding the construct fixed and moving only the
    lookback isolates the horizon, which is the actual claim.

    `agreement` carries the counter-thesis in the same grid. `opposed` is the
    fast signal fired AGAINST the slow view -- the first oversold reading in an
    uptrend, the first channel break while price is still in the lower half of
    its longer range -- and those are the bars a confirmation rule throws away.
    Whichever value wins, the loser's result is the control for it, which is more
    than most families here get.
    """
    if _late(ctx, bars[index], params):
        return None
    fast, slow = _nested_pair(params["construct"], index, bars, ctx)
    if fast is None or slow is None or slow == 0:
        return None
    if (fast == slow) != (params["agreement"] == "aligned"):
        return None
    return fast


#: Oscillator pairs for `cross_timing`. Three of the three, and the midlines are
#: fixed by the constructs themselves: zero for a MACD histogram, fifty for RSI
#: and for the smoothed stochastic.
CROSS_PAIRS = {"macd_rsi": ("macd", "rsi"),
               "macd_stoch": ("macd", "stoch"),
               "rsi_stoch": ("rsi", "stoch")}


def _midline_cross(name, index, ctx):
    """1 or -1 if `name` crossed its midline at `index`, 0 if not, `None` unreadable."""
    if index == 0:
        return None
    p = ctx["periods"]
    if name == "macd":
        series, level = ctx["macd_hist"][p["macd"][0]], 0.0
    elif name == "rsi":
        series, level = ctx["rsi"][p["rsi"][1]], 50.0
    else:
        series, level = ctx["stoch_slow"][p["stoch"][0]], 50.0
    now, before = series[index], series[index - 1]
    if not present(now) or not present(before):
        return None
    if before <= level < now:
        return 1
    if before >= level > now:
        return -1
    return 0


def cross_timing_signal(index, bars, ctx, params, _state):
    """Two oscillators cross their midlines within a few bars of each other.

    A statement about COINCIDENCE IN TIME, which no family here can make. Every
    oscillator family reads a level -- is RSI above 70, is the histogram positive
    -- and a level stays true for long stretches, so those families fire on runs
    of bars and overlap each other enormously. A crossing is a single bar.
    Requiring two of them inside a window of two to five bars picks out the
    moment several independent smoothers turn together, and it fires a few times
    a month rather than continuously.

    The trade is taken on the bar that COMPLETES the pair, never on the first
    crossing, so the rule cannot be reading a signal it has not seen yet. If both
    cross on the same bar, that bar is the completion.

    `macd` uses the SHORT triple and `stoch` the short period on purpose: this
    family is about timing, and the textbook 12/26/9 in session units smooths the
    turn away entirely.
    """
    if _late(ctx, bars[index], params):
        return None
    first, second = CROSS_PAIRS[params["pair"]]
    now_a, now_b = _midline_cross(first, index, ctx), _midline_cross(second, index, ctx)
    if now_a is None or now_b is None:
        return None
    if now_a == 0 and now_b == 0:
        return None
    if now_a and now_b and now_a != now_b:
        return None
    side = now_a or now_b
    if not (now_a and now_b):
        other = second if now_a else first
        matched = False
        for j in range(max(1, index - params["window"] + 1), index):
            if _midline_cross(other, j, ctx) == side:
                matched = True
                break
        if not matched:
            return None
    return -side if params["direction"] == "fade" else side


#: The level systems `level_confluence` looks for agreement between. Three
#: pairings rather than every pair, because the question is whether AGREEMENT
#: between independently derived systems matters -- and the two that both come
#: out of yesterday's bar are not independent enough to ask it with.
LEVEL_PAIRS = {"pivot_pdr": ("pivot", "pdr"),
               "pivot_vwap": ("pivot", "vwap"),
               "pdr_vwap": ("pdr", "vwap")}


def _level_pair(name, index, bars, ctx):
    """`(resistance, support)` from one level system, or `(None, None)`."""
    if name == "pivot":
        levels = ctx["floor_pivots"].get(ctx["day"][index])
        if levels is None:
            return None, None
        _pivot, r1, s1, _r2, _s2 = levels
        return r1, s1
    if name == "pdr":
        window = ctx["prior_range"].get(ctx["day"][index])
        return window if window else (None, None)
    line = ctx["vwap"]
    if line is None or not present(line[index]):
        return None, None
    # VWAP is one price acting as both sides: above it, the level price has to
    # hold; below it, the one it has to reclaim.
    return line[index], line[index]


def level_confluence_signal(index, bars, ctx, params, _state):
    """Two independent level systems landing on the same price.

    `floor_pivot` and `pdr` each trade their own level, and each is a
    single-source claim: R1 matters because floor traders used it, yesterday's
    high matters because it traded. This family only fires where two systems that
    were derived DIFFERENTLY agree to within a fraction of ATR -- a coincidence,
    and one that says nothing about either system's own merit. That is the
    hypothesis: a level several methods find is a level more people are watching,
    and confluence is the only thing here that can test it.

    It is also a natural rarity filter. Two systems agreeing within half an ATR
    happens on a minority of days, so this trades a fraction of what
    `floor_pivot` does -- which is the point, and also the reason its trade count
    has to be checked against the gate before its return is read at all.

    `direction` decides which claim about the cluster is being tested: `fade`
    says price turns there, `follow` says the level breaking is the event. They
    are mutually exclusive on the same bar, so the pair is a built-in control.
    """
    bar = bars[index]
    if ctx["daily"] or _late(ctx, bar, params):
        return None
    atr = ctx["atr"][index]
    if not atr or not present(atr):
        return None
    first, second = LEVEL_PAIRS[params["pair"]]
    up_a, down_a = _level_pair(first, index, bars, ctx)
    up_b, down_b = _level_pair(second, index, bars, ctx)
    if (not present(up_a) or not present(down_a)
            or not present(up_b) or not present(down_b)):
        return None
    edge = params["tolerance"] * atr
    follow = params["direction"] == "follow"
    if abs(up_a - up_b) <= edge:
        cluster = 0.5 * (up_a + up_b)
        if bar[H] >= cluster - edge:
            if follow and bar[C] > cluster:
                return 1
            if not follow and bar[C] < cluster:
                return -1
    if abs(down_a - down_b) <= edge:
        cluster = 0.5 * (down_a + down_b)
        if bar[L] <= cluster + edge:
            if follow and bar[C] < cluster:
                return -1
            if not follow and bar[C] > cluster:
                return 1
    return None


def trap_signal(index, bars, ctx, params, _state):
    """A breakout that CLOSED through its level and then closed back inside.

    `failed_break` is the one-bar version and this is not it. There, price pokes
    through yesterday's extreme and closes back inside WITHIN THE SAME BAR, so no
    breakout family ever entered -- it is a rejection wick with a level attached.
    Here the market actually closed beyond the level, which means `pdr`,
    `donchian` or `swing_break` took the trade and is now positioned, and the
    fade is taken against a position the study's own families are holding. That
    is a materially stronger claim and a much rarer bar.

    The level is read AT THE BREAK BAR, so a channel that has since widened past
    the failure cannot make the trap look like a level still holding.

    No `direction` axis: the thesis IS the fade. A `follow` cell would be "buy a
    breakout that has already failed", which is `donchian` entered late and
    worse, and the search would be free to find it and call it a trap.
    """
    if _late(ctx, bars[index], params):
        return None
    for j in range(max(1, index - params["window"]), index):
        top, bottom = _breakout_levels(params["level"], j, bars, ctx)
        if not present(top) or not present(bottom):
            continue
        if bars[j][C] > top:
            return -1 if bars[index][C] < top else None
        if bars[j][C] < bottom:
            return 1 if bars[index][C] > bottom else None
    return None


def idio_break_signal(index, bars, ctx, params, _state):
    """The name breaks its own channel while the benchmark stays inside its own.

    NOT `rel_break`, and the difference is exactly the case that matters. That
    family runs a channel on the RATIO, so it fires whenever the ratio makes a
    new extreme -- including on a day the whole index gaps down and the name
    falls slightly less, which is a relative-strength break in which nothing
    about the name broke out at all. This one requires an ABSOLUTE break by the
    name and the ABSENCE of one by the benchmark, a joint statement neither the
    price series nor the ratio can make alone.

    On the eleven US large caps this is the only family in the study that can
    separate "the stock broke out" from "the Nasdaq broke out and the stock came
    along", which is the distinction the whole `relative` group exists for. Both
    channels use the SAME period so the comparison is not a horizon difference
    wearing an idiosyncrasy label.
    """
    relative = ctx.get("relative")
    if not relative or _late(ctx, bars[index], params):
        return None
    channel = params["channel"]
    close, reference = bars[index][C], relative["benchmark"][index]
    top = relative["own_high"][channel][index]
    bottom = relative["own_low"][channel][index]
    far_top = relative["bench_high"][channel][index]
    far_bottom = relative["bench_low"][channel][index]
    if (not present(close) or not present(reference) or not present(top)
            or not present(bottom) or not present(far_top)
            or not present(far_bottom)):
        return None
    side = None
    if close > top and reference <= far_top:
        side = 1
    elif close < bottom and reference >= far_bottom:
        side = -1
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side

# --------------------------------------------------------------------------- #
# THE FOURTH WAVE: THE NIGHT, THE ALMANAC, THE HORIZON, AND THE PAIR
#
# The first sixty-two families read a price against a window, an oscillator or a
# date. The thirteen combination families pair two of those readings. All
# seventy-five share one property that nobody wrote down until the study was
# asked for daily strategies: SIXTY-THREE OF THEM ARE FLATTENED AT THE SESSION
# CLOSE, and the twelve that are not were written as intraday rules that happen
# to be allowed to hold. Run at `--bar-minutes 1440` the same seventy-five
# become "the same rules on a coarser bar" rather than daily strategies, because
# not one of them was designed around anything a day contains.
#
# THE FOUR THINGS A DAY CONTAINS THAT NO EXISTING FAMILY READS:
#
#   the night        A session return is the sum of two exposures with different
#                    signs, different variances and different mechanisms -- the
#                    gap taken with the market shut, and the daytime move every
#                    other family here trades. The close series has already
#                    added them together, so no rolling window on it can
#                    separate them, and nothing in the study holds a position
#                    from one close to the next open. `night` is seven families
#                    whose entire exposure is the leg the module has never
#                    priced, in a THIRD holding regime the engine did not have.
#
#   the almanac      `seasonality` bets on a month and `turn_of_month` on a month
#                    boundary. Neither can express an expiry, a closure, a
#                    quarter boundary, or any window that was named in print
#                    before this study existed. `almanac` is four families over
#                    a FIXED list of such dates -- fixed because a family that
#                    swept start/end pairs would be `seasonality`'s twenty-four
#                    coin flips raised to a power.
#
#   the horizon      The longest lookback in the study is 252 sessions and the
#                    longest hold is fifteen. So a rule can look back a year and
#                    must act on the next three weeks, which excludes every
#                    published construct about the next quarter. `horizon` is
#                    seven families at position scale, on a grid whose exits run
#                    to sixty sessions, reading three things no window reads at
#                    all: the AGE of a state, a return with its most recent
#                    month CUT OUT, and a level fixed by the calendar rather
#                    than recomputed every bar.
#
#   the pair         The `relative` group divides the name by its benchmark, and
#                    a ratio answers exactly one question: which went up more.
#                    It cannot tell "index +2%, name +0.4%" from "index -0.4%,
#                    name -2%", and it cannot see a beta that is not one.
#                    `crossasset` is three families on the regression instead of
#                    the ratio -- correlation, beta and the residual.
#
# EVERY ONE STILL HAD TO ANSWER THE ADMISSION TEST the third wave introduced:
# name a bar on which this fires and no existing family does, and say why the
# two disagree. One candidate failed it and was dropped rather than shipped: a
# `weekend` family holding Friday's close to Monday's open is exactly
# `night_drift` with `weekday=4` and `nights_1`, because the context's bar list
# has no weekend rows and "one session later" is already Monday.
#
# NONE OF THE EXISTING SEVENTY-FIVE CHANGED. Not a grid, not an axis, not a
# constant they read -- `NIGHT_EXIT_MODES` and `POSITION_EXIT_MODES` are new
# vocabularies rather than additions to `EXIT_MODES` and `SWING_EXIT_MODES` for
# that reason alone. The forty-four sealed results stay reproducible, and a
# before/after comparison of any published family is a comparison of the same
# search.
# --------------------------------------------------------------------------- #

def _at_close(index, ctx):
    """True on the last bar from which an entry can still reach tonight.

    The engine reads a signal on bar `i`, fills at bar `i + 1`'s open, and
    refuses that fill once the clock has reached the session close -- so the
    latest position any rule in this module can hold overnight is one opened at
    the open of the session's last fillable bar, which means firing on the bar
    before it.

    THIS IS NOT "AT THE CLOSE" AND THE DIFFERENCE IS THE COST OF THE REGIME. At
    30m the entry is roughly half an hour of daylight before the night being
    traded, so a night family is really buying the last half hour AND the gap.
    That is why `NIGHT_BAR_MAX` refuses the regime above 60m: at 240m the same
    rule would be buying the whole afternoon and calling the result overnight.
    """
    last = ctx["session_last"][index]
    return last is not None and index + 1 == last


def _day_move(index, bars, ctx):
    """The session's move so far, in average-daily-range units, or `None`.

    Read off `session_open`, which is the running per-day open, so it is the
    part of today the bar can actually see. Quoted in daily range for the same
    reason every stop is: it means the same thing at every timeframe.
    """
    opened = ctx["session_open"][index]
    risk = ctx["risk"][index]
    if not opened or not risk or risk <= 0:
        return None
    return (bars[index][C] - opened) / risk


# ---- the night ------------------------------------------------------------- #

def night_drift_signal(index, bars, ctx, params, _state):
    """A fixed direction, held from one session's close to the next one's open.

    THE BASE RATE, AND THE CONTROL FOR THE WHOLE GROUP. It is to the night what
    `time_of_day` is to the session: if a conditional night rule cannot beat
    "just be long every night", it has shown nothing, and this is the cheapest
    possible way to find that out.

    THE WEEKEND IS ALREADY IN HERE, which is why there is no `weekend` family.
    The context's bar list contains only in-session rows, so the session after
    Friday's is Monday's -- `weekday=4` with `nights_1` IS the Friday-close to
    Monday-open trade, priced with the same stop and the same size as every
    other night. A separate family would have been the same hypothesis counted
    twice under a more exciting name.
    """
    if not _at_close(index, ctx):
        return None
    weekday = params["weekday"]
    if weekday != "any" and es.weekday(bars[index][TS]) != weekday:
        return None
    return 1 if params["side"] == "long" else -1


def night_close_location_signal(index, bars, ctx, params, _state):
    """Where the session closed inside its own range, held overnight.

    A close on the day's high is a different state from a close in the middle of
    an equally large up day: one says the buyers were still there at the bell,
    the other says they were done by lunchtime. Nothing in the study reads it at
    SESSION scale -- `climax` reads location within a single bar, `wick` reads
    the shape of one bar, and neither is looking at where the day finished.
    """
    if not _at_close(index, ctx):
        return None
    high, low = ctx["session_high"][index], ctx["session_low"][index]
    if high is None or low is None or high <= low:
        return None
    place = (bars[index][C] - low) / (high - low)
    edge = params["edge"]
    side = 1 if place >= edge else -1 if place <= 1.0 - edge else None
    return -side if side and params["direction"] == "fade" else side


def night_day_return_signal(index, bars, ctx, params, _state):
    """The daytime leg, followed or faded across the night.

    The one intraday-to-overnight link with a literature behind it, and the one
    reading that makes the two legs a PAIR rather than two independent series.
    `momentum` fires on the same number and is flattened at the close, so it can
    only ever ask what the rest of the afternoon does with it; this asks what
    the night does with it, which is a different question about the same bar.
    """
    if not _at_close(index, ctx):
        return None
    move = _day_move(index, bars, ctx)
    if move is None:
        return None
    threshold = params["threshold_day"]
    side = 1 if move > threshold else -1 if move < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def night_gap_echo_signal(index, bars, ctx, params, _state):
    """Last night's gap, echoed or reversed by tonight's.

    The autocorrelation of the overnight leg WITH ITSELF, which is the one
    statistic that decides whether the night is a tradeable series or a sequence
    of independent news draws. `gap` reads the same number and trades the day
    that follows it; this trades the next NIGHT, and the two answers routinely
    differ because the gap is usually reversed intraday and the reversal is the
    part `gap` is measuring.

    The stored leg is a log return, scaled here by the current close to put it
    in the same price units as the daily range it is measured against. Over a
    single overnight gap the approximation is worth a fraction of a basis point.
    """
    if not _at_close(index, ctx):
        return None
    last = ctx["nights"]["night"][index]
    risk, price = ctx["risk"][index], bars[index][C]
    if last is None or not risk or risk <= 0 or price <= 0:
        return None
    move = last * price / risk
    threshold = params["threshold_day"]
    side = 1 if move > threshold else -1 if move < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def night_vol_signal(index, bars, ctx, params, _state):
    """The night held only in a chosen volatility state.

    THE FAMILY THAT SUPPLIES THE GROUP'S MISSING FILTER. The night grid carries
    `vol_mode="none"` alone, deliberately: the shared `calm` filter is a boolean
    on the same two volatility series this family reads, so leaving it in the
    grid would apply the reading twice -- once as a filter nobody declared and
    once as the thesis -- and no result could be attributed to either.

    So the state is the hypothesis and it is graded rather than boolean.
    Overnight risk is unhedgeable by construction: the position cannot be
    reduced while the market is shut, so "is the gap distribution currently
    quiet" is the only risk control the regime has.
    """
    if not _at_close(index, ctx):
        return None
    short, long = ctx["volatility"][index], ctx["long_volatility"][index]
    if short is None or long is None or long <= 0:
        return None
    ratio = short / long
    if params["state"] == "quiet" and ratio > params["ratio"]:
        return None
    if params["state"] == "violent" and ratio < params["ratio"]:
        return None
    return 1 if params["side"] == "long" else -1


def night_streak_signal(index, bars, ctx, params, _state):
    """Consecutive up or down SESSIONS, and then the night.

    `consecutive` counts bars and is flattened at the close, so at 30m it counts
    ninety minutes and calls it a streak. This counts closes, which is the unit
    the effect is written in, and it takes the exposure that a session-flattened
    family cannot: the streak's next gap rather than its next hour.

    The completed sessions come from a causal table; the session in progress is
    added from this bar's own close against the prior close, both of which the
    bar can see. Reading the streak table alone would have used today's FINAL
    close, which this bar has not printed yet.
    """
    if not _at_close(index, ctx):
        return None
    run = ctx["nights"]["prior_streak"][index]
    previous = ctx["prior_close"].get(bars[index][TS] // 86_400)
    if previous is None or previous <= 0:
        return None
    today = 1 if bars[index][C] > previous else -1
    run = run + today if run and (run > 0) == (today > 0) else today
    count = params["count"]
    side = -1 if run >= count else 1 if run <= -count else None
    return -side if side and params["direction"] == "follow" else side


def night_relative_signal(index, bars, ctx, params, _state):
    """The night taken only by a name that led or lagged its index today.

    Overnight, a single name is mostly its index's futures plus its own news, and
    the two arrive through different channels. Whether today's outperformance
    persists through the gap or is given back at the open is a claim about which
    of those two channels carried it, and no session-flattened relative family
    can be asked it.
    """
    if not _at_close(index, ctx):
        return None
    relative = ctx["relative"]
    if relative is None:
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    line = relative["line"]
    now, before = line[index], line[index - lookback]
    if now is None or before is None or before <= 0:
        return None
    move = now / before - 1.0
    threshold = params["threshold"] / 100.0
    side = 1 if move > threshold else -1 if move < -threshold else None
    return -side if side and params["direction"] == "fade" else side


# ---- the almanac ----------------------------------------------------------- #

def almanac_window_signal(index, bars, ctx, params, _state):
    """A pre-registered seasonal window, from `SEASONAL_WINDOWS`.

    NOT A SEARCH OVER DATE PAIRS, and the distinction is the only thing that
    makes the family admissible. `seasonality` is already the study's declared
    overfitting hazard at twenty-four coin flips; a rule free to choose its own
    start and end day would be tens of thousands, and its winner would be
    unreadable. Every window here was named in print before this module existed,
    and `halloween` ships with its own complement `summer` so the pair is one
    test of one claim rather than two chances at a result.
    """
    moment = datetime.fromtimestamp(bars[index][TS], tz=timezone.utc)
    (open_month, open_day), (shut_month, shut_day) = SEASONAL_WINDOWS[params["season"]]
    now = (moment.month, moment.day)
    if (open_month, open_day) <= (shut_month, shut_day):
        inside = (open_month, open_day) <= now <= (shut_month, shut_day)
    else:
        inside = now >= (open_month, open_day) or now <= (shut_month, shut_day)
    if not inside:
        return None
    return 1 if params["side"] == "long" else -1


def opex_signal(index, bars, ctx, params, _state):
    """The monthly options expiry: the third Friday, and the days around it.

    A calendar effect with a mechanism that is not a flow and not a season:
    dealer hedges are pinned to strikes into the expiry and released after it,
    so the week into the third Friday and the week out of it are claims in
    opposite directions. `turn_of_month` cannot express it -- expiry is mid
    month -- and `seasonality` cannot, because it repeats twelve times a year on
    a date that moves.

    The expiry date is DERIVED rather than tabulated: the third Friday is the
    first Friday plus fourteen days, which needs no calendar file and cannot go
    stale.
    """
    moment = datetime.fromtimestamp(bars[index][TS], tz=timezone.utc)
    first = datetime(moment.year, moment.month, 1, tzinfo=timezone.utc).weekday()
    expiry = 1 + (4 - first) % 7 + 14
    day, when = moment.day, params["when"]
    if when == "week":
        inside = expiry - 4 <= day <= expiry
    elif when == "day":
        inside = day == expiry
    else:
        inside = expiry < day <= expiry + 5
    if not inside:
        return None
    return 1 if params["side"] == "long" else -1


def holiday_signal(index, bars, ctx, params, _state):
    """The session before an exchange closure, or the first one back.

    The pre-holiday return is one of the oldest documented calendar effects and
    the study has no way to express it: `day_of_week` has the wrong granularity
    and `turn_of_month` the wrong boundary.

    THE FUTURE-FACING HALF IS DEFENSIBLE AND THE HAZARD IS NAMED. "Tomorrow is a
    holiday" is published years ahead, so reading it at today's close is not
    lookahead in any sense that matters. What IS a hazard is that a data outage
    looks exactly like a closure in this table and was not knowable in advance,
    so the gap is bounded: a weekend explains three days and a holiday weekend
    four, and anything past a week is treated as missing data and declined.

    READ THE TRADE COUNT BEFORE THE RETURN. On DE40 daily the busiest cell in
    the whole grid takes EIGHTEEN trades over seven in-sample years, and the
    median cell takes eleven. That is well under any threshold this study can
    resolve, and `creamer` was dropped for exactly this
    ([[creamer-setup-is-too-rare-to-test]]). It is kept here only because 180
    cells is the cheapest slot in the module and because the rarity is a
    property worth having measured -- a passing cell is a curiosity, not a
    result, and on a 24/7 symbol like ETHUSD it never fires at all.
    """
    weekday = es.weekday(bars[index][TS])
    if params["when"] == "before":
        gap, normal = ctx["break_after"][index], 3 if weekday == 4 else 1
    else:
        gap, normal = ctx["break_before"][index], 3 if weekday == 0 else 1
    if gap is None or gap <= normal or gap > 7:
        return None
    return 1 if params["side"] == "long" else -1


def turn_of_quarter_signal(index, bars, ctx, params, _state):
    """The last and first sessions of a QUARTER, not of a month.

    `turn_of_month` fires twelve times a year on a boundary whose flows are
    payroll and index-fund contributions. A quarter boundary carries those AND
    the ones that only happen four times: index reconstitution, fund reporting
    dates, and the futures roll. Four of `turn_of_month`'s twelve firings are
    inside this family's set, which is the point -- if the effect is really
    quarterly, the monthly family has been averaging it away with eight null
    months, and the two results have to disagree for that to show.
    """
    moment = datetime.fromtimestamp(bars[index][TS], tz=timezone.utc)
    at_end, at_start = ctx["month_end"][index], ctx["month_start"][index]
    if at_end is None or at_start is None:
        return None
    closing = moment.month % 3 == 0 and at_end >= -params["before"]
    opening = moment.month % 3 == 1 and at_start <= params["after"]
    if not (closing or opening):
        return None
    return 1 if params["side"] == "long" else -1


# ---- the horizon ----------------------------------------------------------- #

def tsmom_signal(index, bars, ctx, params, _state):
    """The sign of a long trailing return, taken at a MONTHLY rebalance.

    Time-series momentum as it is actually published: look back a quarter, a
    half or a full year, and act once a month. `momentum` shares the arithmetic
    and nothing else -- it fires on any bar that clears an ATR threshold, is
    flattened at the close, and its longest lookback is two sessions. The
    rebalance clock is not decoration: it is what makes the trade count small
    enough for the holding period to be the strategy rather than the noise.

    `check="any"` is carried as the control. If the monthly cell wins and the
    any-bar cell does not, the result is about the clock and should be reported
    that way rather than as a momentum finding.
    """
    if params["check"] == "month" and ctx["month_start"][index] != 1:
        return None
    lookback = params["lookback"]
    if index < lookback:
        return None
    before = bars[index - lookback][C]
    if before <= 0:
        return None
    move = bars[index][C] / before - 1.0
    side = 1 if move > 0 else -1 if move < 0 else None
    return -side if side and params["direction"] == "fade" else side


def skip_momentum_signal(index, bars, ctx, params, _state):
    """The trailing return with its MOST RECENT MONTH cut out.

    THE ONE CONSTRUCT IN THE STUDY THAT IS NOT A WINDOW. Every other lookback
    here ends at the current bar; this one ends a month ago, because the last
    month is a reversal and the eleven before it are a continuation, and summing
    them hides both. The two signs disagree often enough that this is a
    different rule rather than a lagged copy -- a name that fell hard last month
    after rising all year is long here and short on any contiguous window of the
    same length.
    """
    lookback, skip = params["lookback"], ctx["periods"]["skip"][0]
    if index < lookback:
        return None
    start, stop = bars[index - lookback][C], bars[index - skip][C]
    if start <= 0 or stop <= 0:
        return None
    formation = stop / start - 1.0
    threshold = params["threshold"] / 100.0
    side = 1 if formation > threshold else -1 if formation < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def faber_signal(index, bars, ctx, params, _state):
    """Price against its ten-month simple average, checked at the month turn.

    UNREACHABLE BY THE SHARED GRID, WHICH IS WHY IT IS A FAMILY. The `trend`
    axis every family in the study carries offers `ema_20d` and `ema_50d` and
    stops there, so the most widely published trend filter in existence -- the
    two-hundred-session line -- has never been tested here, at any timeframe, as
    a filter or as a rule.

    A SIMPLE average, not an EMA of the same length: the published rule is
    stated on the simple one, the two cross on different dates, and testing the
    EMA under Faber's name would be reporting a different rule.
    """
    if params["check"] == "month" and ctx["month_start"][index] != 1:
        return None
    level = ctx["long_sma"][params["period"]][index]
    if level is None:
        return None
    side = 1 if bars[index][C] > level else -1
    return -side if params["direction"] == "fade" else side


def drawdown_depth_signal(index, bars, ctx, params, _state):
    """How far below its own peak price sits, AND how long that peak has stood.

    `high_52w` reads the depth alone, and depth alone cannot tell a market that
    fell twenty percent last week from one that fell twenty percent two years
    ago and has gone nowhere since. Those are opposite trades and the study
    scores them as one cell.

    The peak is measured INCLUSIVE of this bar, unlike every channel in the
    module: a breakout test must exclude the bar or it can never break out, and
    a drawdown must include it or a new high reads as a drawdown from
    yesterday's peak.

    THE AGE IS A FRACTION OF THE CHANNEL AND NOT A BAR COUNT. It cannot be
    anything else: the peak of a 63-session window is at most 62 sessions old,
    so a fixed "sixty sessions" is a live reading against the 252 channel and a
    near-impossible one against the 63. That combination fired on 136 of 576
    cells on DE40 -- dead budget, and neighbours the robustness test would score
    as failures for a reason that has nothing to do with the strategy.
    """
    channel = params["channel"]
    top, age = ctx["peak"][channel][index], ctx["peak_age"][channel][index]
    if top is None or age is None or top <= 0:
        return None
    if (top - bars[index][C]) / top < params["depth"]:
        return None
    if age < params["age_share"] * channel:
        return None
    return 1 if params["direction"] == "fade" else -1


def trend_age_signal(index, bars, ctx, params, _state):
    """How long price has held one side of a long average -- young, or old.

    The momentum-crash literature is entirely about a distinction the study
    cannot currently make: "above the two-hundred-day line" is the same reading
    on the day it crossed and in the eleventh month of a bull market, and the
    tail risk of being long is not remotely the same in the two states. Only
    `aroon` reads recency at all, over twenty sessions and flattened at the
    close, which is the wrong horizon for the claim by two orders of magnitude.
    """
    period = params["period"]
    level, age = ctx["long_sma"][period][index], ctx["sma_age"][period][index]
    if level is None or age is None:
        return None
    if params["state"] == "young" and age > params["age"]:
        return None
    if params["state"] == "old" and age < params["age"]:
        return None
    side = 1 if bars[index][C] > level else -1
    return -side if params["direction"] == "fade" else side


def night_share_signal(index, bars, ctx, params, _state):
    """How much of the recent trend arrived overnight rather than in the day.

    THE FAMILY THAT TIES THE TWO NEW WAVES TOGETHER, and a regime reading no
    price window can produce: it is a statement about the DECOMPOSITION of the
    last three months of returns, and the close series has already summed the
    two legs. A market whose whole advance has come in the gaps is one where the
    daytime rules the rest of this module trades are noise around a drift they
    never hold through, and that is a claim worth being able to state and score.
    """
    span = params["span"]
    night = ctx["nights"]["night_mean"][span][index]
    day = ctx["nights"]["day_mean"][span][index]
    if night is None or day is None:
        return None
    scale = abs(night) + abs(day)
    if scale <= 0:
        return None
    share = night / scale
    threshold = params["share"]
    side = 1 if share > threshold else -1 if share < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def calendar_break_signal(index, bars, ctx, params, _state):
    """A break of the PREVIOUS calendar week's, month's or quarter's range.

    A rolling five-session channel and last week's high are different numbers
    almost every day, and only one of them is a level: `donchian` recomputes its
    top every bar, so on Thursday its "weekly high" spans Friday to Wednesday --
    a window nobody is watching and no order is resting at. Last week's high is
    one number for all five sessions of this week, printed in every market
    report, and it does not move while price approaches it.

    That fixity is the whole thesis, and it is why the family is worth its
    budget next to `donchian` and `pdr`, which between them cover every rolling
    channel and the previous DAY.
    """
    window = ctx["spans"][params["unit"]].get(bars[index][TS] // 86_400)
    atr = ctx["atr"][index]
    if window is None or not atr:
        return None
    upper = window[0] + params["buffer_atr"] * atr
    lower = window[1] - params["buffer_atr"] * atr
    close = bars[index][C]
    side = 1 if close > upper else -1 if close < lower else None
    return -side if side and params["direction"] == "fade" else side


# ---- the pair, on the regression rather than the ratio --------------------- #

def bench_correlation_signal(index, bars, ctx, params, _state):
    """The name's own direction, taken only while it has DECOUPLED from its index.

    A ratio cannot see this at all. Correlation is a statement about how the two
    series move together, not about which is ahead: it can collapse while the
    ratio sits perfectly flat, and it can be one while the ratio trends for
    months. When it collapses the name is trading its own news, which is the
    only condition under which its own momentum is information rather than a
    slower copy of the index's.
    """
    bench = ctx["bench"]
    if bench is None:
        return None
    period = params["period"]
    rho = bench["correlation"][period][index]
    if rho is None or rho > params["threshold"] or index < period:
        return None
    before = bars[index - period][C]
    if before <= 0:
        return None
    move = bars[index][C] / before - 1.0
    side = 1 if move > 0 else -1 if move < 0 else None
    return -side if side and params["direction"] == "fade" else side


def residual_momentum_signal(index, bars, ctx, params, _state):
    """The part of the move that was not the benchmark's, beta-adjusted.

    `rel_momentum` reads the ratio, which is the residual only if beta is one.
    On a name that moves twice its index the ratio rises whenever the market
    does, so the family calls a high-beta rally outperformance and buys the
    market under another name -- which is precisely the failure the `relative`
    group was introduced to avoid. Regressing the returns and taking what is
    left removes the market at its measured loading instead of at an assumed
    one.
    """
    bench = ctx["bench"]
    if bench is None:
        return None
    residual = bench["residual"][params["period"]][index]
    if residual is None:
        return None
    threshold = params["threshold"] / 100.0
    side = 1 if residual > threshold else -1 if residual < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def lead_lag_signal(index, bars, ctx, params, _state):
    """The benchmark moved, and the name has not followed it -- yet.

    Conditioned on the BENCHMARK's own sign, which is the reading a ratio
    destroys: "index up two percent, name up a fifth of that" and "index down a
    fifth, name down two" produce the same ratio move in the same direction, and
    they are opposite trades. `idio_break` is the near neighbour and asks the
    other question -- the name broke out and the index did not -- so it fires on
    bars where the name is the one that moved, and this one fires on bars where
    it is the one that did not.

    The shortfall is measured against `beta` times the benchmark's move rather
    than against the move itself, so a low-beta name is not permanently in a
    catch-up state.
    """
    bench = ctx["bench"]
    if bench is None:
        return None
    period = params["period"]
    led, beta = bench["move"][period][index], bench["beta"][period][index]
    if led is None or beta is None or index < period:
        return None
    before = bars[index - period][C]
    if before <= 0 or abs(led) < params["threshold"] / 100.0:
        return None
    expected = beta * led
    shortfall = expected - math.log(bars[index][C] / before)
    if abs(shortfall) < params["catchup"] * abs(expected):
        return None
    side = 1 if shortfall > 0 else -1
    return -side if params["direction"] == "fade" else side


# --------------------------------------------------------------------------- #
# THE FIFTH WAVE: WHAT "HOLD IT FOR MONTHS" ACTUALLY NEEDED
#
# The fourth wave added a `horizon` group and it passed once in 168 slots. That
# was read as "position scale does not work here" and it was the wrong reading,
# for two reasons found afterwards.
#
# THE FIRST IS THAT THE COST MODEL WAS INCOMPLETE, and at this horizon it was
# incomplete by more than the entire edge. `cost_bp` charged the spread once at
# entry and nothing else, so a sixty-day hold was priced exactly like a scalp.
# Financing on this account runs 1.5-5 bp of notional PER NIGHT -- 9.6x the
# whole round-trip spread per night on DE40, 6.4x on NQ -- which is 97-294 bp
# over sixty days. `financing_price` now charges it, and every months-scale
# result in this module before that change was overstated by more than any edge
# it ever found.
#
# THE SECOND IS THAT THE ENTRIES WERE MISSING RATHER THAN FAILING. `horizon`
# held for up to sixty days but nothing in it looked back further than a year,
# and three constructs that define the literature had no expression at all:
#
#   long_channel         `swing_donchian` stops at 55 sessions, so turtle-scale
#                        breakout -- what most people mean by trend following --
#                        was unreachable at every timeframe the study offers.
#   multi_horizon_trend  `tsmom` votes on ONE lookback. The published rule is an
#                        ensemble over three, and an ensemble is not a filter:
#                        it fires where all three agree, which is a bar set
#                        neither of the singles produces.
#   long_reversal        the longest lookback anywhere was 252 sessions, so the
#                        three-to-five year reversal could not be stated.
#
# AND ONE FACTOR THE MODULE HAD NEVER TOUCHED. `vix_1d` covers 2017-2026 and was
# read by nothing: every exogenous series in the study was a PRICE of the same
# asset class, reached through `BENCHMARK`. Implied volatility is a different
# kind of input -- a forward-looking risk premium rather than a return -- and
# the two families that read it (`vix_regime`, `vol_risk_premium`) are the only
# ones here whose signal is not derived from the traded series at all.
#
# WHAT CARRY TURNED OUT TO BE. It was requested as a signal and it cannot be one
# here: MT5 exposes only TODAY's swap and the store has no rate history, so the
# sign of the carry is a single number known at the end of the sample. What is
# testable is the asymmetry it creates -- on NQ, ES, DE40 and BTC the short side
# is financed at zero and the long side at 1.8-2.1 bp a night -- and
# `swap_side_bias` tests exactly that and nothing more. Read its docstring
# before reading its result; it is the most compromised family in the module.
# --------------------------------------------------------------------------- #

def long_channel_signal(index, bars, ctx, params, _state):
    """A break of a 100, 200 or 252-session channel, held for months.

    THE GAP THAT MADE THE `horizon` GROUP INCOMPLETE. `donchian` runs at
    session scale and is flattened at the close; `swing_donchian` holds, but its
    longest channel is 55 sessions, which is a quarter. The Donchian systems the
    trend-following literature is actually written about break a hundred to two
    hundred DAYS, and no cell in this study could express one.

    The distinction from `swing_donchian` is not a longer number on the same
    idea. A 55-session channel breaks several times a year and a 252-session
    channel breaks once or twice, so the two rules have different trade counts,
    different holding periods and almost disjoint entry bars -- and the longer
    one is the only one whose exit horizon the `position` grid was built for.

    `direction="fade"` IS DEAD WHEREVER `trend` IS SET, AND THAT IS A PROPERTY
    OF THE WHOLE STUDY RATHER THAN OF THIS FAMILY. Fading an upside break means
    going short while price sits above a 252-session high, and the shared trend
    gate requires a short to be BELOW its moving average -- the two conditions
    cannot hold at once, so two of the three `trend` values kill every fade
    cell. On NQ daily that is 144 of this family's 432 cells, exactly the fade
    cells carrying a trend filter. Every breakout family in the module has the
    same interaction; it is recorded here because this is where it was measured,
    and it is NOT worked around, because changing the shared grid to suit it
    would move every published result.
    """
    bar = bars[index]
    channel = params["channel"]
    top = ctx["high"][channel][index]
    bottom = ctx["low"][channel][index]
    if top is None or bottom is None:
        return None
    atr = ctx["atr"][index]
    if not atr:
        return None
    buffer_ = params["buffer_atr"] * atr
    side = (1 if bar[C] > top + buffer_
            else -1 if bar[C] < bottom - buffer_ else None)
    return -side if side and params["direction"] == "fade" else side


def multi_horizon_trend_signal(index, bars, ctx, params, _state):
    """The sign of the trailing return at three horizons, voting.

    NOT A FILTERED `tsmom`, AND THE DIFFERENCE IS THE ADMISSION ARGUMENT. A
    filter can only remove trades from the family it filters, so its result is
    bounded by that family's. This is bounded by no single-horizon family,
    because `votes=2` fires on bars where the twelve-month reading is negative
    and the three- and six-month ones are positive -- a bar on which the
    twelve-month `tsmom` cell is SHORT and this is LONG. The two rules disagree
    about direction, not merely about whether to trade.

    `votes=3` is unanimity, which is the published construct; `votes=2` is the
    majority, and carrying both is what makes "does agreement matter" a measured
    question rather than an assumption.
    """
    if params["check"] == "month" and ctx["month_start"][index] != 1:
        return None
    close = bars[index][C]
    score = 0
    for lookback in ctx["periods"]["vote"]:
        if index < lookback:
            return None
        before = bars[index - lookback][C]
        if before <= 0:
            return None
        score += 1 if close > before else -1 if close < before else 0
    votes = params["votes"]
    side = 1 if score >= votes else -1 if score <= -votes else None
    return -side if side and params["direction"] == "fade" else side


def long_reversal_signal(index, bars, ctx, params, _state):
    """Two to four years of return, faded.

    The horizon at which momentum is known to invert. Every other lookback in
    this module tops out at 252 sessions, so the study could state "it went up
    this year" and could not state "it has gone up for four years", which is the
    condition the reversal literature is about.

    `direction` is carried rather than hard-coded to `fade` because the same
    window read the other way is the long-horizon momentum claim, and the two
    are the same measurement with opposite signs -- running only the one the
    literature favours would be choosing the answer.

    READ THE TRADE COUNT ON STOCKS BEFORE THE RETURN. Their tables begin
    2019-12-31 with no warm-up, so a 504-session reading first exists in 2022
    and a 1,008-session one in 2024: on an in-sample window of 2020-2024 those
    cells are blind for most of it, by construction and not by chance.
    """
    lookback = params["lookback"]
    if index < lookback:
        return None
    before = bars[index - lookback][C]
    if before <= 0:
        return None
    move = bars[index][C] / before - 1.0
    threshold = params["threshold"] / 100.0
    side = -1 if move > threshold else 1 if move < -threshold else None
    return -side if side and params["direction"] == "follow" else side


def momentum_crash_filter_signal(index, bars, ctx, params, _state):
    """Trailing-return momentum, refused in the state where it crashes.

    Momentum's losses are not spread evenly: they arrive in short, violent
    rebounds that happen almost exclusively in a falling, high-volatility market.
    `tsmom` cannot express the distinction -- the `trend` axis it shares with
    every other family asks where price sits against an average, which is a
    statement about direction and says nothing about whether volatility has
    doubled.

    So the state here is BOTH: a bear reading from price against its long
    average, and a panic reading from short volatility against its own baseline.
    `state="calm"` trades momentum everywhere except in that corner;
    `state="panic"` trades ONLY in it, which is the control -- if the corner is
    really where momentum dies, the two must have opposite signs, and if they do
    not then the filter has found nothing and the calm cell is a subset that got
    lucky.
    """
    lookback = params["lookback"]
    if index < lookback:
        return None
    before = bars[index - lookback][C]
    if before <= 0:
        return None
    level = ctx["long_sma"][params["period"]][index]
    short, long = ctx["volatility"][index], ctx["long_volatility"][index]
    if level is None or short is None or long is None or long <= 0:
        return None
    close = bars[index][C]
    panic = close < level and short / long > params["ratio"]
    if params["state"] == "calm" and panic:
        return None
    if params["state"] == "panic" and not panic:
        return None
    side = 1 if close > before else -1 if close < before else None
    return -side if side and params["direction"] == "fade" else side


def vix_regime_signal(index, bars, ctx, params, _state):
    """Implied volatility against its own recent level, as a state.

    THE FIRST FAMILY IN THE MODULE WHOSE SIGNAL IS NOT IN THE TRADED SERIES.
    Every other exogenous reading here is a benchmark PRICE reached through
    `BENCHMARK` -- another return series, of the same asset class, differing
    from the symbol by a factor loading. VIX is not a return: it is what index
    options are charging for the next thirty days, so it moves before realised
    volatility does rather than after it, and `vol_regime` -- which reads the
    symbol's own trailing volatility -- is looking at the opposite side of the
    same relationship and always one window late.

    `zone` is the hypothesis and both halves ship, because "buy when fear is
    high" and "buy when fear is low" are both widely believed and cannot both be
    right.
    """
    vix = ctx["vix"]
    if vix is None:
        return None
    window = params["window"]
    rank = vix["rank"][window][index]
    if rank is None:
        return None
    # A PERCENTILE, NOT A RATIO. VIX spikes up and decays down, so "40% above
    # its own mean" is common and "40% below" essentially never happens -- a
    # ratio threshold prices a live hypothesis on one side and a dead cell on
    # the other, which was 80 of 576 cells on NQ. `present_rank` puts a fixed
    # fraction of the sample in each zone whatever the shape of the
    # distribution, so `high` and `low` are the same size claim.
    quantile = params["quantile"]
    if params["zone"] == "high" and rank < quantile:
        return None
    if params["zone"] == "low" and rank > 1.0 - quantile:
        return None
    return 1 if params["side"] == "long" else -1


def vol_risk_premium_signal(index, bars, ctx, params, _state):
    """Implied volatility minus the volatility that actually arrived.

    The variance risk premium, stated in the only two series available: VIX, and
    the symbol's own trailing realised volatility annualised onto the same
    percentage scale. A wide positive premium means options are charging far
    more than the recent past delivered, which is the classic condition for
    being paid to carry risk; a negative one means the market has been more
    violent than it is priced to be.

    NEITHER LEG IS READABLE ALONE, which is what makes this a family rather than
    a filter. `vol_regime` reads realised volatility against its own baseline
    and cannot see what is being charged for it; `vix_regime` reads the charge
    and cannot see what was delivered. The premium is the difference, and it is
    routinely wide when both legs are low and narrow when both are high.
    """
    vix = ctx["vix"]
    if vix is None:
        return None
    window = params["window"]
    premium = vix["premium"][window][index]
    if premium is None:
        return None
    threshold = params["premium"]
    side = 1 if premium > threshold else -1 if premium < -threshold else None
    return -side if side and params["direction"] == "fade" else side


def swap_side_bias_signal(index, bars, ctx, params, _state):
    """Trend, taken only on the side the broker finances cheaply.

    READ THIS BEFORE READING ITS RESULT. IT IS THE MOST COMPROMISED FAMILY IN
    THE MODULE AND IT SHIPS ANYWAY, LABELLED.

    Carry was asked for as a signal and cannot be one here. A carry trade needs
    the rate differential through TIME, and this study has two sources: MT5,
    which exposes only today's swap, and the Parquet store, which has no rate table at
    all. So the sign of the carry is a single number observed at the very end of
    the sample and then applied to seven years of history -- across a period in
    which policy rates went from 2.5% to zero to 5%. On NQ, ES, DE40 and BTC the
    short side is financed at zero today and the long side at 1.8-2.1 bp a
    night; in 2021 that asymmetry was smaller and in 2018 it pointed elsewhere.
    THE FAMILY THEREFORE KNOWS SOMETHING ABOUT ITS OWN SAMPLE THAT NO TRADER IN
    2018 COULD HAVE KNOWN, and a good result is evidence of that, not of an edge.
    `why` cannot price this away either, because the leak is in the axis rather
    than in the search.

    It is here because the asymmetry is real and large TODAY, and a rule that
    refuses the financed side is a live constraint worth having measured -- and
    because leaving it out would have meant answering "what about carry" with
    silence rather than with the reason.

    `bias="cheap"` takes the trend only when it points at the cheap side;
    `bias="either"` is the same trend rule unconstrained, and is the control
    that says how much of any difference is the constraint rather than the trend.
    """
    lookback = params["lookback"]
    if index < lookback:
        return None
    before = bars[index - lookback][C]
    if before <= 0:
        return None
    close = bars[index][C]
    side = 1 if close > before else -1 if close < before else None
    if side is None:
        return None
    if params["bias"] == "cheap":
        symbol = ctx["symbol"]
        cost = (financing_price(symbol, 1), financing_price(symbol, -1))
        if cost[0] == cost[1]:
            return None
        cheap = 1 if cost[0] < cost[1] else -1
        if side != cheap:
            return None
    return side




# --------------------------------------------------------------------------- #
# THE SIXTH WAVE
#
# WHERE IT CAME FROM. The first five waves were scored on 39 symbols at 30m and
# the result is legible: what passed its holdout AND its own coin-flip null
# clusters into five archetypes and nothing else.
#
#   volatility_breakout  14/20 symbols   an ATR-scaled break of the day's open
#   vol_regime            5/7            the volatility TRANSITION, not a filter
#   swing_ma              9/13           a slow cross, held past the close
#   pullback              6/10           a retrace inside an established trend
#   floor_pivot           9/19           a level the market did not trade at
#   rvol / mfi            8/12           participation, not price
#   level_confluence      8/18           two independent levels at one price
#   confluence            4/13           a vote, and the winners all FADE
#   nested                2/14           but +108% on BTC and +43% on ETHUSD
#
# and the shared axes agree with each other across all of it: `trail_1.5` won
# 187 of 298 passing cells against 54 for the next best exit, and `breakout`
# beat `fade` and `follow` combined. What lost is equally legible -- `xma_slope`
# 1/14, `cross_timing` 1/12, `cmf` 0/4, `rsi` 0/3 -- and those are the families
# that read a SMOOTHED PRICE and nothing else.
#
# So the wave is built on the reading that the winners have in common: they are
# statements about the STATE OF THE PROCESS -- is it trending, is it expanding,
# is anybody there, is this level real -- rather than about the level of a
# smoothed price. Every family below is a different way of asking that, using a
# statistic the module could not previously compute.
#
# THE ADMISSION TEST IS UNCHANGED AND WAS APPLIED TO EVERY ONE: name a bar on
# which this fires and no existing family does, and say why the two disagree.
# Three candidates were rejected by it and are recorded rather than quietly
# dropped -- a Katz fractal dimension (a monotone transform of `efficiency`, see
# `exness_indicators`), a Bollinger %B (`zscore` with a different constant), and
# a Chande momentum oscillator (`rsi` rescaled).
#
# WHAT IT COSTS. Twenty context blocks, 7.9 seconds to build all of them on a
# 60,000-bar series, and roughly forty arrays -- against `gate`'s six blocks.
# The budget is what needs watching, not the memory: run `budget --groups
# pathstat,micro,filter,adaptive,fusion` before a sweep and read
# [[coin-flip-control-beats-real-signals]] before reading any winner.
# --------------------------------------------------------------------------- #

# ---- pathstat: what KIND of process is this ------------------------------- #

def hurst_signal(index, bars, ctx, params, _state):
    """The scaling exponent decides whether to follow the move or fade it.

    `regime_switch` is the family this has to be distinguished from, and the
    distinction is in the estimator rather than in the rule. A variance ratio
    compares dispersion at exactly two horizons -- one bar and `step` -- so it
    is one point on a curve, and the cell that reads it is asking "is this
    series trending AT THIS ONE HORIZON". The Hurst exponent is the SLOPE of
    that curve across five horizons at once.

    They disagree on a bar where the series reverts at two bars and trends at
    sixteen: the variance ratio at step 2 says revert, the ratio at step 5 says
    trend, and the scaling slope says the persistence is real and the two-bar
    reading was the bid-ask bounce. On that bar `regime_switch` takes one side
    at one step and the other at the other, and this takes one side.

    `band` is the dead zone either side of 0.5, and it is a genuine scale, which
    matters: this family's other axes are all labels, and a family whose entire
    grid is categorical reports `1/1` robust neighbours and has never been
    perturbed ([[all-categorical-axes-void-the-robustness-gate]]).
    """
    period = params["period"]
    value = ctx["hurst"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    band = params["band"]
    if 0.5 - band < value < 0.5 + band:
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    # Above the band the series is persistent and the move continues; below it
    # the series is antipersistent and the move is the thing to fade. No
    # `direction` axis, for the same reason `regime_switch` has none: the
    # reading names the regime, and letting the search overrule it would be
    # testing a different hypothesis under this one's name.
    return side if value >= 0.5 + band else -side


def entropy_signal(index, bars, ctx, params, _state):
    """Trade only when the ORDER of recent returns is unusually predictable.

    THE ONE READING IN THE STUDY THAT IS BLIND TO MAGNITUDE. Permutation entropy
    counts which of three consecutive returns was largest and nothing else, so
    doubling every return in the window leaves it unchanged. `efficiency` is its
    exact complement -- pure magnitude, blind to order -- and the two therefore
    disagree constantly: a stretch of alternating tiny moves has a near-zero
    efficiency ratio and a LOW entropy, because up-down-up-down is one pattern
    repeating and repetition is structure. `efficiency` calls that bar noise and
    refuses; this calls it the most structured bar of the week.

    `state` carries both sides because "low entropy is tradeable" is a
    hypothesis and not a fact. The published claim is that predictability is
    exploitable; the opposite cell -- trade only when the sequence looks random
    -- is the control for it, inside the same grid and at the same budget.
    """
    period = params["period"]
    value = ctx["entropy"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    ordered = value <= params["threshold"]
    if ordered != (params["state"] == "ordered"):
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def kurtosis_signal(index, bars, ctx, params, _state):
    """The FOURTH moment as a regime, with the side declared.

    Level, dispersion and asymmetry are already read -- by the price, by
    `trailing_volatility` and by `skew` -- and nothing read the weight of the
    tails. It is not a slower volatility reading, and the case where the two
    part company is common: a window of many tiny moves and two violent ones has
    ordinary volatility and enormous kurtosis, so `vol_mode="calm"` admits the
    bar and this refuses it. A window of uniformly large moves is the reverse.

    Direction has to be declared rather than derived, exactly as `skew` declares
    it, because kurtosis has no sign that maps onto a trade -- and declaring it
    is what makes the result readable instead of a search over which way to read
    a symmetric statistic.
    """
    period = ctx["periods"]["kurtosis"][0]
    value = ctx["kurtosis"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    fat = value >= params["threshold"]
    if fat != (params["state"] == "fat"):
        return None
    return 1 if params["side"] == "long" else -1


def autocorr_signal(index, bars, ctx, params, _state):
    """The sign of the return autocorrelation at ONE named lag IS the trade.

    Every other momentum or reversion family in this module fixes its direction
    on the grid and lets the search pick which one paid. This one does not have
    a `direction` axis: a positive autocorrelation at lag one is the statement
    that the last bar's move continues, and a negative one is the statement that
    it reverses, so the reading maps onto a side with no choice left to make.

    NOT A VARIANCE RATIO WITH A DIFFERENT NAME. A variance ratio at step k is a
    triangular-weighted SUM of the autocorrelations at lags 1 through k-1, so it
    cannot distinguish rho(1) = +0.2 from rho(1) = -0.2 with rho(2) = +0.4 --
    the two aggregate to nearly the same number and demand opposite trades on
    the very next bar. `lag` is an axis here precisely so that difference is
    measured rather than assumed: lag 2 is the first lag at which a bid-ask
    bounce has decayed, so if the whole effect lives at lag 1 it is the spread
    and not a forecast.
    """
    key = (params["period"], params["lag"])
    value = ctx["autocorr"][key][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    if abs(value) < params["threshold"]:
        return None
    lag = params["lag"]
    if index < lag:
        return None
    move = bars[index][C] - bars[index - lag][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return side if value > 0 else -side


def runs_signal(index, bars, ctx, params, _state):
    """Too few sign changes in the window, or too many -- Wald-Wolfowitz.

    `consecutive` is the family to hold this against, and they trade almost
    disjoint bars. That one reads the CURRENT streak and fires when it reaches
    two or three; this reads the whole window and asks whether the number of
    sign changes in it is what chance would produce. So it fires on a bar whose
    current streak is one, provided the window as a whole is too sticky, and it
    stays silent through a five-bar streak that sits inside an otherwise choppy
    window. The first is a bar `consecutive` cannot see at all; the second is a
    bar `consecutive` always takes.

    Negative z is too few runs, which is trending. Positive z is too many, which
    is the alternating signature of a market being made rather than moved -- and
    those two states want opposite trades, which is why `state` selects one
    rather than the search picking a direction.
    """
    period = params["period"]
    value = ctx["runs"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    sticky = value <= -threshold
    choppy = value >= threshold
    if not (sticky or choppy):
        return None
    if sticky != (params["state"] == "sticky"):
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    # Sticky windows continue and choppy ones alternate: the state names the
    # treatment of the move, so `direction` would be a second, contradictory
    # vote on the same question and is not carried.
    return side if sticky else -side


def kendall_signal(index, bars, ctx, params, _state):
    """A monotone trend test one outlier cannot move.

    `linreg_trend` reads a least-squares slope, which is a weighted mean, and a
    weighted mean is dominated by its largest term. Mann-Kendall counts only the
    SIGN of every pairwise comparison in the window, so a violent bar
    contributes at most its share of the count.

    Measured on a 300-bar series that ground steadily upward and then gapped 20%
    down on the last bar: this fell from z = 21.03 to 20.61, a 2.0% loss, while
    the least-squares slope over the same window lost 6.9% -- three times as
    much of its value from one bar. The gap is stated at its measured size
    rather than at the size the argument would prefer: a first draft of this
    docstring said "unchanged", and the test written to pin it failed. One
    outlier at the END of a 200-bar regression has only moderate leverage, so
    three times is what a single event buys. It widens with the number of
    outliers, which is what a contaminated month actually looks like.

    The z-score is also directly interpretable as significance, which no other
    trend reading here is -- so `threshold` is stated in sigma and 2.0 means the
    conventional thing rather than being a number found by search.
    """
    period = params["period"]
    value = ctx["kendall"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    side = 1 if value >= threshold else -1 if value <= -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def tail_signal(index, bars, ctx, params, _state):
    """Tail asymmetry as an ORDER STATISTIC rather than as a third moment.

    `skew` asks the same question with a formula that one bar can dominate: a
    single -8% return in a window of calm makes the third moment strongly
    negative whether or not the tails are otherwise symmetric, because the term
    is cubed. This compares the tenth percentile of the positive returns with
    the tenth percentile of the negative ones, so no single bar can move it by
    more than one rank.

    The two therefore disagree on precisely the windows that contain one event
    -- which are the windows a risk-premium story is about. On a symbol whose
    up-tail is genuinely fatter, both read positive; on a symbol that had one
    crash inside an otherwise symmetric quarter, `skew` reads negative and this
    reads one, and only one of those two is a statement about the distribution.
    """
    period = ctx["periods"]["tails"][0]
    value = ctx["tails"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    heavy_up = value >= 1.0 + threshold
    heavy_down = value <= 1.0 / (1.0 + threshold)
    if not (heavy_up or heavy_down):
        return None
    side = 1 if heavy_up else -1
    return -side if params["direction"] == "fade" else side


# ---- micro: what the bar says about liquidity and jumps ------------------- #

def jump_signal(index, bars, ctx, params, _state):
    """Trade the move only when the variance was, or was not, a JUMP.

    Realised variance cannot tell a violent diffusion from a single gap;
    bipower variation is robust to jumps and their difference is therefore the
    jump part. Nothing else in the module can make that separation --
    `trailing_volatility` adds the two together by construction and so does ATR,
    which means `vol_mode` is a filter on their SUM and admits both states
    identically.

    The bar where this and `climax` disagree is the interesting one. `climax`
    looks for one big bar and finds a jump at bar resolution; this reads the
    regime, so it fires on an ordinary-sized bar sitting inside a jumpy fortnight
    and refuses the biggest bar of a smoothly trending one. Whether continuation
    pays after a jump-dominated stretch or only after a diffusive one is a real
    question with a large literature and no answer this study could reach.
    """
    period = params["period"]
    value = ctx["jump"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    jumpy = value >= params["share"]
    if jumpy != (params["state"] == "jumpy"):
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def semivariance_signal(index, bars, ctx, params, _state):
    """Which SIDE the variance came from -- realised semivariance, signed.

    A difference of two variances rather than a standardised third moment, and
    that is what separates it from `skew`. Skew is dimensionless and hides
    magnitude; this is the up-variance minus the down-variance over the same
    window, scaled by their sum, so a window with a few large up-moves and many
    small down-moves reads strongly positive here and can read either sign as
    skew depending on how the small moves happen to be spread.

    The published claim it exists to test is specific: downside realised
    variance carries the risk premium and upside does not, so the two halves
    forecast differently and adding them back together -- which every volatility
    reading in this module does -- destroys the signal. `direction` is carried
    because that claim is about a premium, and a premium can be earned by taking
    the risk or lost by taking the other side.
    """
    period = params["period"]
    value = ctx["semi"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    side = 1 if value >= threshold else -1 if value <= -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def amihud_signal(index, bars, ctx, params, _state):
    """Price impact per unit of volume, short window against long.

    THE RATIO, WHICH IS NEITHER OF ITS PARTS. `rvol` reads volume against its
    own history and is silent about what the volume achieved; every return-based
    family here is silent about what the move cost. Amihud is the quotient, and
    it is the only reading in the study that can say "today's move was bought
    cheaply" -- a two percent day on enormous volume and a two percent day on
    nothing are the same bar to `momentum`, the same bar to `climax`, opposite
    bars to `rvol`, and opposite bars here for a different reason: `rvol` says
    which had more participation and this says which had more slippage.

    Read as a ratio of the short window to the long one rather than as a level,
    because the level is in whatever volume unit the table carries -- a tick
    count on one feed and a contract count on another -- so a threshold on it
    would rank feeds instead of markets.
    """
    short, long = ctx["periods"]["amihud"]
    fast = ctx["amihud"][short][index]
    slow = ctx["amihud"][long][index]
    if not present(fast) or not present(slow) or slow <= 0:
        return None
    if _late(ctx, bars[index], params):
        return None
    ratio = fast / slow
    illiquid = ratio >= params["ratio"]
    if illiquid != (params["state"] == "illiquid"):
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def estimator_signal(index, bars, ctx, params, _state):
    """Path volatility against close-to-close volatility -- whipping, or gapping.

    Three estimators of one quantity, and their DISAGREEMENT is the reading.
    Close-to-close uses only closes, so a bar that travelled two percent and came
    back counts as zero. Parkinson uses the high-low range and counts it in full.
    Their ratio is therefore how much of the movement was retraced WITHIN bars
    rather than carried between them, and it is high when the market is trading a
    range violently and low when it is gapping and trending.

    Neither ATR nor `trailing_volatility` can express that, because each is only
    one of the two numbers -- ATR IS a range estimator and the volatility IS a
    close-to-close one, and the module has never divided them. `vol_mode` is a
    filter on the second alone, so it admits a violently whipping session and a
    smoothly trending one identically whenever their close-to-close readings
    agree, which is exactly when the difference matters most.

    `estimator` chooses which numerator: Parkinson is pure range, Garman-Klass
    subtracts the body and is therefore the sharper statement of the same thing.
    """
    period = params["period"]
    table = ctx["parkinson" if params["estimator"] == "parkinson"
                else "garman_klass"]
    path = table[period][index]
    close = ctx["close_var"][period][index]
    if not present(path) or not present(close) or close <= 0:
        return None
    if _late(ctx, bars[index], params):
        return None
    ratio = path / close
    whipping = ratio >= params["ratio"]
    if whipping != (params["state"] == "whipping"):
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def bulk_signal(index, bars, ctx, params, _state):
    """Signed volume, classified by where each bar CLOSED inside its own range.

    NOT `obv_break` WITH A SMOOTHER. OBV assigns a bar's entire volume to
    whichever way the close moved, so a bar that rose one tick and a bar that
    rose two percent contribute identically and a doji contributes nothing.
    This is continuous in the close's location within the range, which means it
    can report the state OBV structurally cannot: heavy volume, and the buyers
    barely won.

    That state is the one that precedes a failed breakout, and OBV records it as
    an unambiguous accumulation bar -- so on the bar where a big-volume push
    closes mid-range, `obv_break` is making a new OBV high and this is reading
    near zero. They are looking at the same tape and disagreeing about who won.

    A share of the window's total volume, so it is already a proportion in
    [-1, 1] and the threshold means the same thing on every symbol.
    """
    period = params["period"]
    value = ctx["bulk"][period][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    side = 1 if value >= threshold else -1 if value <= -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


# ---- filter: the things a moving average is not --------------------------- #

def roofing_signal(index, bars, ctx, params, _state):
    """A zero crossing of Ehlers' BANDPASS -- one time scale, isolated.

    EVERY OTHER FILTER IN THIS MODULE IS A LOWPASS. Six moving averages span a
    lag spectrum and all six answer one question: what is left when the fast
    wiggles are removed. None can remove the SLOW component, so no cell of
    `xma_cross`, `ma_cross` or `linreg_trend` can state "price is high relative
    to the last two days, and that has nothing to do with the six-month trend".
    Subtracting a moving average is the naive detrend and it leaks: an SMA has a
    ragged frequency response, so the difference still carries trend energy.

    The roofing filter removes both ends properly -- a two-pole Butterworth
    highpass at the slow edge, Ehlers' SuperSmoother at the fast one -- and what
    is left has zero mean by construction. Verified: fed a pure linear ramp, the
    output is 0.0000 to four decimals, which is what "the trend is genuinely
    gone" looks like and is not true of any average-difference in this module.

    So the disagreement is concrete: during a strong uptrend with a shallow
    pullback, `ma_cross` and `xma_cross` are long and stay long, and this crosses
    below zero and goes short -- because the pullback IS the signal at the band
    it is tuned to, and the trend it is being measured against has been filtered
    out rather than subtracted approximately.
    """
    pair = params["band"]
    line = ctx["roofing"][pair]
    value = line[index]
    if not present(value) or index < 1:
        return None
    previous = line[index - 1]
    if not present(previous):
        return None
    if _late(ctx, bars[index], params):
        return None
    # A CROSSING, not a level. The filter's amplitude is in price units and
    # therefore symbol-specific and regime-specific; its sign change is not, and
    # it is the only event in the series that means the same thing everywhere.
    side = (1 if previous <= 0.0 < value
            else -1 if previous >= 0.0 > value else None)
    if side is None:
        return None
    # The band's own amplitude has to be worth trading. Scaled by ATR so the
    # gate means the same thing at every timeframe and on every symbol -- the
    # same convention every threshold in the module uses.
    atr = ctx["atr"][index]
    if not atr or not present(atr):
        return None
    recent = [abs(v) for v in line[max(0, index - 20):index + 1] if present(v)]
    if not recent or max(recent) < params["amplitude_atr"] * atr:
        return None
    return -side if params["direction"] == "fade" else side


def fisher_signal(index, bars, ctx, params, _state):
    """The Fisher transform turning at an extreme -- a DISTRIBUTIONAL reading.

    Where a price sits in its range is roughly uniformly distributed, which is
    why `stochastic` spends most of its life in the middle and why a threshold
    on it is arbitrary: 80 is not a rare reading, it is the top fifth of a flat
    distribution. The Fisher transform maps that uniform variable to something
    close to Gaussian, so its tails become genuinely rare and the SAME threshold
    means the same rarity on every symbol and in every regime.

    That makes it a different family and not a rescaling, because the transform
    is nonlinear. Two bars four stochastic points apart near the middle are
    almost the same Fisher reading; four points apart near the edge are far
    apart. The turns a linear oscillator smears are exactly the ones this
    sharpens, so on a slow drift up through the top of the range `stochastic`
    reports a plateau at 90 and this reports a series of distinct new extremes.

    `mode` fades the extreme or joins the turn back out of it, which are
    different trades on the same event and the second is the one `rsi` cannot
    express at all.
    """
    period = params["period"]
    line = ctx["fisher"][period]
    value = line[index]
    if not present(value) or index < 1:
        return None
    previous = line[index - 1]
    if not present(previous):
        return None
    if _late(ctx, bars[index], params):
        return None
    threshold = params["threshold"]
    if params["mode"] == "extreme":
        # AT the extreme, faded. The Gaussian-ised scale is what makes a fixed
        # threshold defensible here: |2| is roughly a two-sigma event whatever
        # the symbol, where a stochastic of 90 is a two-percentile event on one
        # series and a daily occurrence on another.
        if value >= threshold:
            return -1
        if value <= -threshold:
            return 1
        return None
    # Turning back OUT of the extreme: the reading was beyond the threshold and
    # has now turned toward the middle. A different bar from the one above --
    # later, at a worse price, and with the reversal confirmed rather than
    # assumed -- which is the whole trade-off the mode axis measures.
    if previous >= threshold and value < previous:
        return -1
    if previous <= -threshold and value > previous:
        return 1
    return None


def kalman_signal(index, bars, ctx, params, _state):
    """The state-space SLOPE, from a filter whose gain adapts to the noise.

    THE ONLY FILTER HERE WHOSE WEIGHTING IS NOT FIXED IN ADVANCE. Every moving
    average applies the same kernel to every bar forever; `kama` adapts its
    period from the efficiency ratio, which is a heuristic bolted onto a fixed
    filter. This carries a covariance and applies the gain that is optimal given
    how noisy the series has actually been -- trusting its own state through a
    quiet stretch and re-anchoring fast after a violent bar. No fixed kernel
    does both.

    And the slope is a STATE, not a difference of two smoothed points, which is
    what `linreg` and `xma_slope` both compute. A differenced slope is the slope
    of the past window, so after a turn it keeps the old sign for as long as the
    window is wide; this is the filter's current estimate of the rate and turns
    with the data. That is the bar where they disagree, and it is every turn.

    `mode` separates the two claims the state makes: `slope` trades the rate's
    sign, `residual` trades the price's distance from the filtered level, which
    is a mean-reversion statement about a line no other family here computes.
    """
    level = ctx["kalman_level"][index]
    slope = ctx["kalman_slope"][index]
    if not present(level) or not present(slope):
        return None
    if _late(ctx, bars[index], params):
        return None
    atr = ctx["atr"][index]
    if not atr or not present(atr):
        return None
    if params["mode"] == "slope":
        # The slope is a per-bar log return, so it is compared against a
        # threshold expressed the same way -- ATR over price is the bar's own
        # typical log move, and the multiple says how many of those a bar must
        # be trending by.
        close = bars[index][C]
        if close <= 0:
            return None
        unit = atr / close
        if unit <= 0:
            return None
        strength = slope / unit
        side = (1 if strength >= params["threshold"]
                else -1 if strength <= -params["threshold"] else None)
    else:
        gap = (bars[index][C] - level) / atr
        side = (1 if gap <= -params["threshold"]
                else -1 if gap >= params["threshold"] else None)
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def fracdiff_signal(index, bars, ctx, params, _state):
    """A stationary series that KEEPS its memory, z-scored.

    `zscore` is the family to hold this against and the difference is structural
    rather than parametric. A z-score subtracts a rolling mean, which is a full
    difference of a smoothed series: beyond the window it has forgotten
    everything. A fractional difference of order d in (0, 1) is the continuum
    between a price and a return -- stationary enough to threshold, with weights
    that decay as a POWER LAW rather than being truncated, so the reading still
    carries information from hundreds of bars back.

    So they disagree in one specific and very common situation: after a long
    slow drift. The z-score's mean has followed the drift and reports no
    deviation at all, and the fractional difference has not forgotten where the
    series started and reports a large one. Every slow trend in the sample is a
    bar on which `zscore` is silent and this is not.

    Verified numerically: at d -> 1 the series converges on the one-bar log
    return, and at d -> 0 it keeps the level -- so the axis really is the
    continuum it claims to be and not two arbitrary transforms.
    """
    order = params["order"]
    value = ctx["fracdiff"][order][index]
    mean = ctx["fracdiff_mean"][order][index]
    sigma = ctx["fracdiff_sigma"][order][index]
    if not present(value) or not present(mean) or not present(sigma):
        return None
    if sigma <= 0:
        return None
    if _late(ctx, bars[index], params):
        return None
    z = (value - mean) / sigma
    threshold = params["threshold_z"]
    side = -1 if z >= threshold else 1 if z <= -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "follow" else side


def cycle_signal(index, bars, ctx, params, _state):
    """The measured dominant PERIOD decides which of two rules is running.

    A MEASURED TIME SCALE, WHICH NOTHING ELSE IN THIS MODULE PRODUCES. Every
    lookback here is chosen -- fourteen because Wilder chose it, twenty because
    it is a month -- and the search then picks whichever of two happened to pay.
    This asks the series what its own time scale currently is and answers with a
    number of bars, verified against clean sines: a 16-bar cycle reads 16.5 and
    a 30-bar cycle reads 30.2.

    The rule is that a short measured period means the market is oscillating
    quickly, which is the condition under which a fade has a defined horizon,
    and a long one means it is not. So this fades in one state and follows in
    the other, and its sign changes within a single backtest -- which makes it
    uncorrelated with any fixed-direction family by construction, the same
    property that makes `regime_switch` worth carrying.

    It is not `regime_switch` with a new estimator. A variance ratio says
    whether the series trends; this says at WHAT PERIOD it oscillates, which is
    a statement a variance ratio has no way to make and which is silent about
    trending at all.
    """
    value = ctx["cycle"][index]
    if not present(value):
        return None
    if _late(ctx, bars[index], params):
        return None
    minimum, maximum = ctx["periods"]["cycle"]
    if maximum <= minimum:
        return None
    # Where the measured period sits between the estimator's own bounds, so
    # the threshold is a share and means the same thing at every timeframe --
    # the bounds themselves scale with the session.
    share = (value - minimum) / (maximum - minimum)
    fast = share <= params["share"]
    if fast != (params["state"] == "fast"):
        return None
    horizon = max(2, int(round(value)))
    if index < horizon:
        return None
    move = bars[index][C] - bars[index - horizon][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    # THE MOVE IS MEASURED OVER THE MEASURED PERIOD, which is the point: a
    # fixed-lookback momentum reading over a cycle that has changed length is
    # measuring across a turn. In a fast cycle the move over one period is what
    # reverts; in a slow one it is what continues.
    return -side if fast else side


# ---- adaptive: the rule sizes its own horizon ----------------------------- #

def half_life_signal(index, bars, ctx, params, _state):
    """A z-score whose lookback is the ESTIMATED mean-reversion half-life.

    `zscore` takes its period from a fixed tuple and the search picks whichever
    of three paid. This regresses the one-bar change on the level -- the
    Ornstein-Uhlenbeck fit -- and uses the half-life that comes out as its own
    lookback, re-measured every bar. On the same series it therefore uses a
    twelve-bar window in a fast-reverting stretch and a two-hundred-bar window
    in a slow one, and no single cell of `zscore` does both.

    IT ALSO REFUSES WHERE `zscore` CANNOT. When the fitted coefficient is not
    negative the series is not reverting at all, and the reading is `None` --
    so this family is silent through every trending stretch by construction,
    where `zscore` computes a perfectly well-defined deviation from a mean the
    price is walking away from and trades it. That is the single largest
    behavioural difference between them and it is not a tuning choice.

    Served from prefix sums, so the adaptive window costs two subtractions
    rather than a loop -- the `prefix` block exists for this family alone.
    """
    life = ctx["halflife"][params["period"]][index]
    if not present(life):
        return None
    if _late(ctx, bars[index], params):
        return None
    span = max(2, int(round(life * params["multiple"])))
    if index < span:
        return None
    total = ctx["prefix_sum"]
    square = ctx["prefix_square"]
    start = index + 1 - span
    mean = (total[index + 1] - total[start]) / span
    variance = (square[index + 1] - square[start]) / span - mean * mean
    if variance <= 1e-12:
        return None
    sigma = math.sqrt(variance)
    z = (bars[index][C] - mean) / sigma
    threshold = params["threshold_z"]
    side = -1 if z >= threshold else 1 if z <= -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "follow" else side


def cusum_signal(index, bars, ctx, params, _state):
    """A threshold on ACCUMULATED deviation, which resets after every event.

    Every breakout family in this module fires when one reading crosses one
    level, so a move made of twenty small steps in the same direction is
    invisible to all of them until it happens to clear a channel. The CUSUM
    filter accumulates the returns since its last event and fires when the
    running sum exceeds a volatility-scaled threshold -- so it fires on that
    move, at the point where it became a move, on a bar which by construction is
    not an extreme of anything.

    THE RESET IS WHAT MAKES IT AN EVENT SAMPLER RATHER THAN ANOTHER OSCILLATOR.
    The same drift cannot fire it twice without an intervening retracement, so
    its trades are spaced by market structure instead of by a bar count -- which
    is precisely the property `donchian`, `zscore` and every threshold rule here
    lack, and the reason Lopez de Prado uses it to sample events rather than to
    generate them.

    The disagreement with `donchian` is easy to name: on the twentieth small
    step of a grind, `donchian` is silent because no channel broke and this
    fires. On the bar where a spike clears a 20-session high, `donchian` fires
    and this has already fired, ten bars earlier and forty basis points better.
    """
    events = ctx["cusum"][params["multiple"]]
    side = events[index]
    if not side:
        return None
    if _late(ctx, bars[index], params):
        return None
    return -side if params["direction"] == "fade" else side


def vol_of_vol_signal(index, bars, ctx, params, _state):
    """How UNSTABLE the volatility itself is -- the second-order reading.

    `vol_regime` reads the level of volatility against its own baseline and
    fires on the transition. This reads the dispersion OF that ratio: whether
    volatility has been steady at whatever level it holds, or lurching. Those
    are different states and the module could not previously distinguish them --
    a fortnight at a steady twice-normal volatility and a fortnight alternating
    between half and four times normal have the same mean ratio, so `vol_regime`
    and `vol_mode` treat them identically, and one of them is a market with a
    settled opinion while the other is a market that has none.

    Computed from the volatility ratio series that `vol_regime` already reads,
    so it costs no new block -- the second moment of a reading the context
    carries, taken over a window with an O(1) sliding form.
    """
    period = ctx["periods"]["vol"]
    if index < period:
        return None
    if _late(ctx, bars[index], params):
        return None
    short = ctx["volatility"]
    long = ctx["long_volatility"]
    total = count = 0.0
    squares = 0.0
    # Bounded to the window and read from arrays the context already holds. The
    # module's rule is that a signal does no windowed work; this is the one
    # place a fixed 20-bar pass is cheaper than a whole extra context block, and
    # the window is the shared `vol` period rather than an axis so it cannot
    # grow.
    for offset in range(index - period + 1, index + 1):
        a, b = short[offset], long[offset]
        if not present(a) or not present(b) or b <= 0:
            return None
        ratio = a / b
        total += ratio
        squares += ratio * ratio
        count += 1
    if count < 2:
        return None
    mean = total / count
    variance = squares / count - mean * mean
    if variance <= 1e-18 or mean <= 0:
        return None
    # Coefficient of variation, so the reading is scale-free in the ratio and a
    # threshold means the same thing whether volatility is high or low.
    unsteady = math.sqrt(variance) / mean >= params["dispersion"]
    if unsteady != (params["state"] == "unsteady"):
        return None
    lookback = ctx["periods"]["session"]
    if index < lookback:
        return None
    move = bars[index][C] - bars[index - lookback][C]
    if move == 0:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def quantile_break_signal(index, bars, ctx, params, _state):
    """A break of a ROBUST channel -- an order statistic, not an extreme.

    `donchian` compares the close against the single most extreme print in the
    window, so one bad tick or one news spike sets the level for the whole
    lookback. The 90th percentile of the same window is set by a tenth of the
    bars, so it moves when the DISTRIBUTION moves and not when one bar does.

    The two disagree in a specific and common situation, and it is the reason
    this family exists: after a spike that is not revisited, the Donchian
    channel stays wide and refuses every entry until the spike rolls off the
    window, while the quantile channel narrows back within a few bars and takes
    the continuation. Over a 20-session channel that is up to a month of
    refusals bought with one print.

    `share = 0.98` IS THE CONTROL, INSIDE THE GRID. At 0.98 the level is very
    nearly the extreme, so if the family only works there, what it found is
    `donchian` and the robustness was not the reason. That comparison is free
    and is the honest way to price the claim.
    """
    key = (params["channel"], params["share"])
    top = ctx["quantile_high"][key][index]
    bottom = ctx["quantile_low"][key][index]
    if not present(top) or not present(bottom):
        return None
    if _late(ctx, bars[index], params):
        return None
    atr = ctx["atr"][index]
    if not atr or not present(atr):
        return None
    buffer_ = params["buffer_atr"] * atr
    close = bars[index][C]
    side = (1 if close > top + buffer_
            else -1 if close < bottom - buffer_ else None)
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


# ---- fusion: the winners, fused ------------------------------------------- #
#
# THE ADMISSION BAR IS HIGHER HERE THAN ANYWHERE ELSE IN THE MODULE, and the
# `gate` group's own docstring says why: a gated family's trades are always a
# SUBSET of the ungated family's, so its result is bounded by it and adding one
# is a way of counting one hypothesis twice. The shared `common` grid already
# gates every family in the study with an `ema_20d`/`ema_50d` trend filter and a
# `calm` volatility filter, so "volatility_breakout plus a moving average" is a
# cell that has been scored since the first run.
#
# So each of these six had to fire on a bar where NEITHER parent fires, and the
# reason is stated in each docstring. Two of them -- `regime_breakout` and
# `adaptive_pullback` -- change a parent's THRESHOLD from a constant into a
# function of a state, which moves the entry bar rather than removing it; the
# other four read a construct neither parent contains.

def regime_breakout_signal(index, bars, ctx, params, _state):
    """Williams' breakout, with the trigger distance set by the vol REGIME.

    THE TWO BEST-SCORING FAMILIES IN THE STUDY, AND NOT AS A FILTER.
    `volatility_breakout` passed 14 of 20 symbols and `vol_regime` 5 of 7, and
    the obvious combination -- take the breakout only while volatility is
    expanding -- is a subset of the breakout's trades and therefore bounded by
    it. This is not that.

    The breakout's trigger is `fraction * yesterday's range` from today's open,
    a CONSTANT distance. Here the fraction is scaled by the short-against-long
    volatility ratio, so in an expanding regime the trigger moves further away
    and in a contracting one it moves closer. That changes which bar fires:
    on a quiet morning this triggers at a price the fixed rule has not reached
    yet, and in a violent one it refuses a break the fixed rule takes. The two
    families' entry bars are neither a subset nor a superset of each other.

    `response` is how hard the scaling bites, and it is the axis that makes the
    claim falsifiable: at 0.0 this IS `volatility_breakout`, which is the
    control sitting inside the grid at no extra cost.
    """
    bar = bars[index]
    if ctx["daily"] or _late(ctx, bar, params):
        return None
    day = bar[TS] // 86_400
    window = ctx["prior_range"].get(day)
    opening = ctx["session_open"][index]
    if window is None or not present(opening):
        return None
    span = window[0] - window[1]
    if span <= 0:
        return None
    short = ctx["volatility"][index]
    long = ctx["long_volatility"][index]
    if not present(short) or not present(long) or long <= 0:
        return None
    ratio = short / long
    # A bounded scaling. An unbounded one would let a single volatile fortnight
    # push the trigger outside the day's whole range and silently stop the
    # family trading, which reads as "no edge" and is an arithmetic accident.
    scale = 1.0 + params["response"] * (min(3.0, max(0.33, ratio)) - 1.0)
    edge = params["fraction"] * span * scale
    side = (1 if bar[C] > opening + edge
            else -1 if bar[C] < opening - edge else None)
    return -side if side and params["direction"] == "fade" else side


def momentum_stack_signal(index, bars, ctx, params, _state):
    """Three horizons of momentum, weighted by how UNUSUAL each one is.

    `nested` scored +108% on BTC and +43% on ETHUSD on its `aligned`/`momentum`
    cell and is the highest single reading in the study -- and it compares two
    horizons and counts agreement as a boolean. `multi_horizon_trend` votes over
    three and is daily-only. Both throw away magnitude: a 0.1-sigma move and a
    3-sigma move are the same vote.

    This standardises each horizon's return by that horizon's own dispersion and
    sums the three z-scores. So it fires on a bar where one horizon is
    overwhelming and the other two are mildly against -- which is a REFUSAL for
    both voting families and, in the sample, the state after a genuine break --
    and it refuses a bar where all three agree feebly, which both voting
    families take at full size. The disagreement is in both directions, which is
    what stops it being a filter on either.

    Standardising is also what makes summing legal at all: a three-session
    return and a twenty-session return have different variances, so adding them
    raw would make the longest horizon the only one that mattered and would
    quietly reduce this to a one-horizon rule.
    """
    if _late(ctx, bars[index], params):
        return None
    close = bars[index][C]
    if close <= 0:
        return None
    total = ctx["prefix_sum"]
    square = ctx["prefix_square"]
    score = 0.0
    for span in (ctx["periods"]["session"], 5 * ctx["periods"]["session"],
                 20 * ctx["periods"]["session"]):
        if index < 2 * span:
            return None
        before = bars[index - span][C]
        if before <= 0:
            return None
        move = close - before
        # The dispersion of CLOSES over twice the horizon, from the prefix sums
        # -- the same O(1) trick `half_life` uses. A per-horizon standard
        # deviation of returns would need its own context block for each span;
        # the close dispersion over the same window is the scale the move has to
        # be read against and is already tabulated.
        start = index + 1 - 2 * span
        count = 2 * span
        mean = (total[index + 1] - total[start]) / count
        variance = (square[index + 1] - square[start]) / count - mean * mean
        if variance <= 1e-12:
            return None
        score += move / math.sqrt(variance)
    threshold = params["threshold"]
    side = 1 if score >= threshold else -1 if score <= -threshold else None
    if side is None:
        return None
    return -side if params["direction"] == "fade" else side


def adaptive_pullback_signal(index, bars, ctx, params, _state):
    """`pullback`, with the acceptable retrace depth set by the PERSISTENCE.

    `pullback` passed 6 of 10 symbols and is the only family in the study that
    requires a trend and then waits to be paid a worse price. Its weakness is
    that `depth` is a constant: the same retracement is a buying opportunity in
    a persistent market and the start of a reversal in an antipersistent one,
    and one number has to serve both.

    Here the depth is scaled by the Hurst exponent. Above 0.5 the series is
    persistent and a deeper retrace is still inside the trend, so the family
    waits for more; below 0.5 it is antipersistent and a shallow dip is already
    as much as the move will give back, so it takes less. That moves the entry
    bar in both directions relative to the parent -- deeper in one regime,
    shallower in the other -- so its trades are not a subset.

    The scaling is bounded to [0.5x, 2x] of the base depth for the same reason
    `regime_breakout`'s is: an unbounded multiplier turns a regime reading into
    an on/off switch and the family stops being a pullback rule.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    channel = params["channel"]
    top, bottom = ctx["high"][channel][index], ctx["low"][channel][index]
    reference = ctx["ema"][ctx["periods"]["trend"][params["reference"]]][index]
    risk = ctx["risk"][index]
    if (not present(top) or not present(bottom) or not present(reference)
            or not risk or risk != risk):
        return None
    exponent = ctx["hurst"][params["period"]][index]
    if not present(exponent):
        return None
    scale = min(2.0, max(0.5, 1.0 + params["response"] * (exponent - 0.5) * 2.0))
    depth = params["depth"] * scale
    side = None
    if bar[C] > reference and 0 < (top - bar[C]) / risk <= depth:
        side = 1
    elif bar[C] < reference and 0 < (bar[C] - bottom) / risk <= depth:
        side = -1
    return -side if side and params["direction"] == "fade" else side


def value_area_signal(index, bars, ctx, params, _state):
    """A reaction at the previous session's VALUE AREA -- a volume-set level.

    A LEVEL SOURCE THE STUDY DID NOT HAVE, and that is the whole admission
    argument. `pdr` reads yesterday's high and low, which are the two prices at
    which trade STOPPED. `floor_pivot` is an arithmetic function of those same
    three numbers. Both are extremes, and both were among the better performers
    -- `floor_pivot` passed 9 of 19 symbols with +60% on DE40 -- so the question
    of whether a level set by VOLUME rather than by extremes does better is
    worth a family.

    The two answers separate on exactly the day a level rule cares about: a
    session with one spike and an otherwise tight auction has a high far above
    everything and a value area that never went near it, so `pdr`'s resistance
    and this family's are a long way apart and only one of them has any trade
    behind it. On a balanced day the two nearly coincide, which is the control.

    `level` picks which of the three the rule reads. The point of control is a
    single price and behaves like a magnet; the two edges behave like barriers,
    so they cannot share a branch -- the same separation `floor_pivot` makes
    between its central pivot and its R/S levels, and for the same reason.
    """
    bar = bars[index]
    areas = ctx.get("value_area")
    if not areas:
        return None
    levels = areas.get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if levels is None or not atr or atr != atr:
        return None
    if _late(ctx, bar, params):
        return None
    high, low, poc = levels
    edge = params["buffer_atr"] * atr
    side = None
    if params["level"] == "poc":
        # A POSITION, not a barrier: above the point of control the auction is
        # accepting higher prices. Reading a rejection at the POC would be a
        # different hypothesis smuggled in under the same axis value.
        side = 1 if bar[C] > poc + edge else -1 if bar[C] < poc - edge else None
    else:
        if bar[H] >= high - edge and bar[C] < high:
            side = -1
        elif bar[L] <= low + edge and bar[C] > low:
            side = 1
    return -side if side and params["direction"] == "follow" else side


#: The ballots `stat_confluence` counts, as `(context block, reading)`.
#:
#: SIX READINGS THAT DO NOT SHARE AN INPUT, which is the only thing that makes a
#: vote mean anything. `confluence`'s `trend` pool is five views of a smoothed
#: price and its members agree most of the time by construction, so its net is
#: really one reading with a confidence attached. These six are a scaling
#: exponent, an ordinal entropy, a jump share, a semivariance, a volume
#: classification and a distributional transform -- and no two of them would
#: move together if the price were shuffled.
STAT_BALLOTS = ("hurst", "entropy", "jump", "semi", "bulk", "fisher")


def stat_view(name, index, bars, ctx):
    """One sixth-wave ballot: 1, -1, 0 for no view, `None` for unreadable.

    Signed so that `+1` always means "the evidence points long", whatever the
    underlying reading's natural orientation. Three of the six have no direction
    of their own -- a Hurst exponent, an entropy and a jump share are states,
    not sides -- so each is combined with the sign of the recent move in the way
    its own family does, and the result is the side that reading would take.
    That is what makes summing them legal.
    """
    p = ctx["periods"]
    span = p["session"]
    if index < span:
        return None
    before = bars[index - span][C]
    if before <= 0:
        return None
    move = bars[index][C] - before
    drift = 1 if move > 0 else -1 if move < 0 else 0

    if name == "hurst":
        value = ctx["hurst"][p["hurst"][0]][index]
        if not present(value):
            return None
        if value >= 0.55:
            return drift
        if value <= 0.45:
            return -drift
        return 0
    if name == "entropy":
        value = ctx["entropy"][p["entropy"][0]][index]
        if not present(value):
            return None
        # Low entropy is structure, and structure means the recent move is the
        # thing to read. High entropy is a random ordering and abstains rather
        # than voting against, because "unpredictable" is not a side.
        return drift if value <= 0.90 else 0
    if name == "jump":
        value = ctx["jump"][p["jump"][0]][index]
        if not present(value):
            return None
        # A jump-dominated stretch is one where the continuation claim is
        # weakest -- the variance arrived in gaps nobody could trade -- so it
        # votes against the drift rather than abstaining.
        return -drift if value >= 0.4 else drift
    if name == "semi":
        value = ctx["semi"][p["semi"][0]][index]
        if not present(value):
            return None
        return 1 if value >= 0.2 else -1 if value <= -0.2 else 0
    if name == "bulk":
        value = ctx["bulk"][p["bulk"][0]][index]
        if not present(value):
            return None
        return 1 if value >= 0.15 else -1 if value <= -0.15 else 0
    if name == "fisher":
        value = ctx["fisher"][p["fisher"][0]][index]
        if not present(value):
            return None
        # The one exhaustion member: a Gaussian-ised extreme is a level to fade,
        # so its vote is the side the fade would take.
        return -1 if value >= 1.5 else 1 if value <= -1.5 else 0
    return None


def stat_confluence_signal(index, bars, ctx, params, _state):
    """Six sixth-wave readings vote; trade the margin, or fade the crowd.

    `confluence` won big where it won -- +79% on ETHUSD and +55% on DE40, both
    on the `exhaustion` pool in `fade` mode -- and both its pools are built from
    the same family of inputs. The `trend` pool is five views of a smoothed
    price, so its members agree most of the time by construction and its net is
    really one reading wearing a confidence interval.

    These six share no input at all. A scaling exponent, an ordinal entropy, a
    jump share, a realised semivariance, a volume classification and a
    distributional transform would not move together if the price series were
    shuffled, which is the property that makes a vote informative rather than
    decorative. The consequence is that this pool reaches `votes=3` far less
    often and on different bars -- it cannot be triggered by one move showing up
    six times.

    `mode` carries the crowding question that `confluence` established is worth
    asking, and the evidence says to ask it: every passing `confluence` cell in
    the study was `fade`, not `follow`.

    Four readable ballots are required, so a pool half degraded by a symbol with
    no usable volume fails to a refusal rather than to a two-member vote wearing
    a six-member name.
    """
    if _late(ctx, bars[index], params):
        return None
    views = [stat_view(name, index, bars, ctx) for name in STAT_BALLOTS]
    counted = [v for v in views if v is not None]
    if len(counted) < 4:
        return None
    net = sum(counted)
    if abs(net) < params["votes"]:
        return None
    side = 1 if net > 0 else -1
    return side if params["mode"] == "follow" else -side


def pivot_exhaustion_signal(index, bars, ctx, params, _state):
    """A floor pivot touched AND rejected, with the rejection read off the bar.

    THE TWO STRONGEST GEOMETRY AND REVERSAL READINGS, AS ONE RULE. `floor_pivot`
    passed 9 of 19 symbols including +60% on DE40 and +52% on ETHUSD, and its
    R/S branch already requires the bar to close back inside the level -- which
    is a rejection stated at one-bar resolution and nothing more. `climax` and
    `wick` read the rejection properly and know nothing about where it happened.

    The fusion is not a filter on either. `floor_pivot` fires whenever the bar
    touched the level and closed back, however feebly; this additionally
    requires the bar to LOOK like a rejection -- a long wick into the level, or
    volume well above its own average -- so it refuses the feeble touches.
    Against `wick` and `climax` it goes the other way: those fire on any strong
    rejection anywhere, and this refuses every one that did not happen at a
    level. Each parent takes bars this refuses, and this takes no bar either
    parent would call its own, because neither parent is asking both questions.

    `evidence` is the axis that prices which half of the rejection matters --
    the shape of the bar or the participation in it -- and `both` is the
    conjunction, which is the strictest and rarest cell.
    """
    bar = bars[index]
    levels = ctx["floor_pivots"].get(bar[TS] // 86_400)
    atr = ctx["atr"][index]
    if levels is None or not atr or atr != atr:
        return None
    if _late(ctx, bar, params):
        return None
    _pivot, r1, s1, r2, s2 = levels
    resistance, support = (r1, s1) if params["level"] == "first" else (r2, s2)
    edge = params["buffer_atr"] * atr
    side = None
    if bar[H] >= resistance - edge and bar[C] < resistance:
        side = -1
    elif bar[L] <= support + edge and bar[C] > support:
        side = 1
    if side is None:
        return None

    upper = ctx["wick_upper"][index]
    lower = ctx["wick_lower"][index]
    if not present(upper) or not present(lower):
        return None
    # `wick_ratio`, not `wick`: that name is already in `CATEGORICAL`, where it
    # labels the `wick` family's zone axis, and a numeric threshold sharing it
    # would be stepped over by the neighbour test -- the family would report a
    # robustness it had never been asked for.
    rejected = (upper if side < 0 else lower) >= params["wick_ratio"]

    period = ctx["periods"]["volume"][0]
    average = ctx["volume_mean"][period][index]
    if not present(average) or average <= 0:
        return None
    heavy = bar[V] / average >= params["volume_mult"]

    evidence = params["evidence"]
    if evidence == "wick" and not rejected:
        return None
    if evidence == "volume" and not heavy:
        return None
    if evidence == "both" and not (rejected and heavy):
        return None
    return side



# --------------------------------------------------------------------------- #
# live: the families that read the execution itself
#
# EVERY OTHER FAMILY IN THIS MODULE TREATS COST AS A CONSTANT IT PAYS AND
# CANNOT SEE. Since `install_live_fills`, the spread the account crossed in
# each bar's own minute is on hand -- the same number `backtest` now charges --
# and these six read it as a READING rather than as a bill.
#
# Two separate claims, and they are tested separately so a result can be
# attributed:
#
#   COST. The measured spread is 4-12x the per-symbol constant on the index
#   CFDs and the gold crosses (de40 11.6x, jp225 7.6x, ustec 6.6x) and about
#   1.0x on FX. A rule that only trades where its own recent spread is cheap
#   keeps more of whatever gross edge it has, and `max_rank` carries a value
#   near 1.0 so the gate is measured against its own absence.
#
#   INFORMATION. A spread is what the market makers charge to be on the other
#   side, so a spread SPIKE is a statement about who is willing to trade -- a
#   move made while the book is three times its usual width was made without
#   them. `spread_shock` and `liquidity_router` are the two shapes of that
#   claim, fade and follow, which cannot both be right.
#
# AND ONE STRUCTURAL CLAIM ABOUT THE EXIT, which is where the live model
# actually bites. A stop or a target is booked by this backtest at the level
# and filled live one WHOLE BAR later, because the account holds no
# broker-side stop and the runtime can only act when its candle rolls -- 3.73
# bp median and 12.49 bp at p90 on usdjpy. A CLOCK exit does not have that
# error: the backtest already prices it at a bar's open and live it is the
# same open plus a lag that lands in the same minute. `clock_momentum` is the
# family built entirely out of that asymmetry, and its `stop_day` is a
# disaster brake rather than the exit.
# --------------------------------------------------------------------------- #


def _spread_reading(ctx, index):
    """`(bp, rank, ratio)` at `index`, or None where the broker table is silent.

    ONE ACCESSOR FOR ALL SIX, because the missing case is the normal case and
    must be handled identically everywhere. `exness_<broker>_1m` starts in 2020
    for FX and 2022 for the index CFDs while the study window opens earlier, so
    a bar before that carries no reading at all -- and NaN is truthy, so a bare
    `if not value` would let it through into a comparison that silently
    succeeds ([[the guard `present` exists for]]).
    """
    value = ctx["spread"][index]
    rank = ctx["spread_rank"][index]
    ratio = ctx["spread_ratio"][index]
    if not (present(value) and present(rank) and present(ratio)):
        return None
    return value, rank, ratio


def spread_gate_signal(index, bars, ctx, params, _state):
    """A channel break, taken only where the book is cheap by its own standard.

    THE GATE IS A PERCENTILE AND NOT A NUMBER OF BASIS POINTS, because the same
    threshold has to mean the same thing on `de40` at 2.2 bp and `xaugbp` at
    11.1 bp. `spread_rank` is where this bar's spread sits within its own
    trailing ten sessions, so `max_rank=0.3` is "the cheapest third of this
    market's recent minutes" on every symbol.

    `max_rank=0.9` IS THE CONTROL AND IT IS IN THE GRID ON PURPOSE. It admits
    all but the worst tenth, so a family that only wins at 0.3 has been shown
    to win BECAUSE of the gate rather than because a breakout works here.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    reading = _spread_reading(ctx, index)
    if reading is None:
        return None
    _bp, rank, _ratio = reading
    if rank > params["max_rank"]:
        return None
    channel = params["channel"]
    upper, lower = ctx["high"][channel][index], ctx["low"][channel][index]
    atr = ctx["atr"][index]
    if not (present(upper) and present(lower) and present(atr)) or atr <= 0:
        return None
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    return -side if side and params["direction"] == "fade" else side


def spread_shock_signal(index, bars, ctx, params, _state):
    """The move a widening book was made on, faded or followed.

    THE THESIS IS ABOUT WHO WAS THERE, not about cost. A spread three times its
    own recent median is the makers stepping back, and a bar that travels while
    they are away is a bar whose price nobody defended. Faded, the claim is
    that the move is an artefact and comes back; followed, that it is real
    information arriving and the book is right to flinch. Both are in the grid
    because the sign is exactly what is not known in advance.

    THE SPREAD IS READ ON THE BAR THAT MOVED and the entry is the next bar's
    open, so nothing here sees a price it could not have. The widening is
    observable at the moment it happens -- it is the quote, not a statistic
    over the future.

    A PERCENTILE AGAIN, AND FOR THE REASON `spread_trend` RECORDS. The first
    version asked for a spread 1.5x its own median, which is 2.6% of jp225
    bars, 0.5% of audusd and 0.0% of ethusd -- rare enough on FX that the cell
    could never accumulate a sample. The rank's expensive tail is reachable
    everywhere: at 0.9 it fires on 9-18% of bars on every symbol measured, and
    at 0.95 on 5-13%, because a percentile asks about order rather than size.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    reading = _spread_reading(ctx, index)
    if reading is None:
        return None
    _bp, rank, _ratio = reading
    if rank < params["min_rank"]:
        return None
    atr = ctx["atr"][index]
    if not present(atr) or atr <= 0:
        return None
    move = bar[C] - bar[O]
    if abs(move) < params["threshold_atr"] * atr:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def clock_momentum_signal(index, bars, ctx, params, _state):
    """Momentum whose exit is a bar count, because that is the exit the runtime
    can actually honour.

    THIS FAMILY EXISTS BECAUSE OF THE EXIT LEG AND NOTHING ELSE. Every
    level-based exit in this study -- stop, target, trail -- is booked here at
    the level and filled live one whole bar plus a lag later, since the MT5
    payload carries no `sl`/`tp` and `family.rs` can only test a level when its
    candle rolls. A `time_N` exit has no such gap: the backtest prices it at a
    bar's open and the account gets that same open moved by a lag that lands in
    the same minute. The two models agree on this family in a way they cannot
    agree on a stop.

    So the entry is deliberately ordinary -- a return over `lookback` bars that
    clears `threshold_atr` -- and the whole hypothesis is in the holding. The
    `stop_day` axis is still there and is a disaster brake: at the wide end it
    is a position size rather than an exit, which is exactly what is wanted
    here.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    lookback = params["lookback"]
    if index < lookback:
        return None
    atr = ctx["atr"][index]
    if not present(atr) or atr <= 0:
        return None
    move = bar[C] - bars[index - lookback][C]
    if abs(move) < params["threshold_atr"] * atr:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def cost_edge_signal(index, bars, ctx, params, _state):
    """A pullback entry that must clear ITS OWN minute's cost, not the average.

    WHAT `report_cost` DOES PER SYMBOL, DONE PER BAR. That table divides the
    constant spread by the daily range and reads the share of every unit of
    risk paid to the broker before the rule has done anything; it explains why
    a fixed-gate sweep ranks the broker's pricing rather than the rules. The
    same quantity is available here per bar -- `stop_day * daily range` is the
    risk this trade takes and `spread` is what this minute charges for it -- so
    the cell can REFUSE a trade whose stop is too small to pay for its own
    entry.

    `min_cover` IS A PERCENTILE AND THE FIRST VERSION'S FIXED MULTIPLE WAS
    WRONG. Measured over the scored window, the raw ratio's median is 116 on
    ustec, 71 on us500, 30 on uk100 and 14 on hk50 -- so "the stop must be
    worth ten spreads" refuses nothing whatsoever on ustec and four fifths of
    hk50, and the axis would have been reading the symbol rather than the
    condition. Against the symbol's own trailing distribution, 0.7 means the
    same thing everywhere: the best third of this market's own minutes.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    cover = ctx["cover_rank"][index]
    atr = ctx["atr"][index]
    if not (present(cover) and present(atr)) or atr <= 0:
        return None
    if cover < params["min_cover"]:
        return None
    trend = ctx["ema"][ctx["periods"]["trend"]["ema_20d"]][index]
    if not present(trend):
        return None
    side = 1 if bar[C] > trend else -1
    # A pullback INTO the trend: the bar closes against the direction it is
    # about to be taken in, which is the archetype `pullback` reads and the one
    # that survived the sixth-wave scoreboard.
    pulled = (bar[C] < bar[O]) if side == 1 else (bar[C] > bar[O])
    if not pulled:
        return None
    depth = abs(bar[C] - trend)
    if depth > params["max_depth_atr"] * atr:
        return None
    return side


def liquidity_router_signal(index, bars, ctx, params, _state):
    """One rule, two regimes, routed by what the book costs right now.

    THE ROUTER IS THE HYPOTHESIS AND THE TWO BRANCHES ARE NOT NEW. Breakouts
    win in some states and get faded in others, and every regime family in this
    module picks that state from PRICE -- volatility, efficiency, a trend
    slope. This one picks it from the ORDER BOOK, which is a different kind of
    evidence and is only observable at all because the fill model now carries
    it.

    Cheap book -- the tightest `calm_rank` of its own recent minutes -- takes
    the break. Expensive book -- above `stress_rank` -- fades it. In between,
    nothing: the middle is where the two claims would cancel, and leaving it
    empty is what makes the result readable rather than a blend.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    reading = _spread_reading(ctx, index)
    if reading is None:
        return None
    _bp, rank, _ratio = reading
    channel = params["channel"]
    upper, lower = ctx["high"][channel][index], ctx["low"][channel][index]
    if not (present(upper) and present(lower)):
        return None
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None:
        return None
    if rank <= params["calm_rank"]:
        return side
    if rank >= params["stress_rank"]:
        return -side
    return None


def spread_trend_signal(index, bars, ctx, params, _state):
    """With the trend, but only while the book is TIGHTENING.

    A NARROWING SPREAD IS ORDERLY PARTICIPATION. The distinction this family
    tries to draw is between a trend the makers are comfortable quoting into --
    their spread contracts as it runs -- and one they are widening against,
    which is the same price path with the opposite meaning underneath it.
    Nothing in the price series separates those two, which is the whole reason
    to have the book.

    `persist` is how many consecutive bars the book must stay in the cheap
    part of its own recent range, so one quote cannot qualify a trade.

    THE PERCENTILE, NOT THE RATIO, AND THAT WAS MEASURED RATHER THAN CHOSEN.
    The first version asked for `spread / trailing median < 0.9`, which happens
    on 23.5% of jp225 bars, 16.7% of ethusd and EXACTLY ZERO usdjpy, audusd and
    gbpusd bars in the scored window -- Exness pins the major FX spread for
    long stretches, so it is never ten percent below its own median. That
    version produced 2,297 signals over all history and 0 trades in the window
    that counts. The rank is reachable on every symbol here (26-35% of bars sit
    at or under 0.2) because it asks about ORDER rather than magnitude.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    persist = params["persist"]
    if index < persist:
        return None
    for back in range(persist):
        reading = _spread_reading(ctx, index - back)
        if reading is None or reading[1] > params["max_rank"]:
            return None
    fast = ctx["fast"][params["fast"]][index]
    slow = ctx["slow"][params["slow"]][index]
    if not (present(fast) and present(slow)):
        return None
    side = 1 if fast > slow else -1
    return -side if params["direction"] == "fade" else side



def anchored_clock_signal(index, bars, ctx, params, _state):
    """`clock_momentum`'s entry with the stop pushed out of reach of the clock.

    ONE HYPOTHESIS, AND IT COMES STRAIGHT OFF THE EXIT ATTRIBUTION. Under the
    live fill model a stop exit costs 5.61 points a trade and a clock exit
    1.58, so a cell that reaches its clock before its stop keeps the difference.
    `clock_momentum` could not express that -- widening its stop shrank its
    position into the lot floor -- so `stop_multiple` was added to size on one
    distance and stop at another.

    THE TRADE IS EXPLICIT: a 3.0 cell risks three times `RISK_FRACTION` on the
    stop-outs it still takes, in exchange for taking far fewer of them. Whether
    that is worth paying is the entire question and the grid carries 1.0 as its
    own control.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    lookback = params["lookback"]
    if index < lookback:
        return None
    atr = ctx["atr"][index]
    if not present(atr) or atr <= 0:
        return None
    move = bar[C] - bars[index - lookback][C]
    if abs(move) < params["threshold_atr"] * atr:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def session_carry_signal(index, bars, ctx, params, _state):
    """Take the session's direction early and hold it to the bell.

    THE ONLY EXIT THAT COSTS NOTHING IS THE ONE BOTH MODELS SCHEDULE. Measured
    over the same 1,660 trades, the session flatten moved the result by 0.000
    points a trade -- the backtest books it at the last in-session bar's open
    and the runtime acts on that same candle roll. So this rule is built to
    reach it: it enters in the first `window_minutes` of the session and its
    exit grid holds nothing but `days_1`, which the flatten reaches first.

    ONE ENTRY, ONE EXIT, ONE SPREAD. Against a rule that turns over four times
    a day, this pays a quarter of the crossings and none of the late-fill
    penalty, which is the whole of what the live model punishes.

    The direction is the opening move, measured against the session's own open
    rather than a rolling window, because a rule that must commit early has
    nothing else to read yet.
    """
    bar = bars[index]
    if ctx["daily"]:
        return None
    minute = bar[TS] % 86_400 // 60
    opened = ctx["cfg"]["session"][0]
    if not (opened <= minute < opened + params["window_minutes"]):
        return None
    opening = ctx["session_open"][index]
    atr = ctx["atr"][index]
    if not (present(opening) and present(atr)) or atr <= 0 or opening <= 0:
        return None
    move = bar[C] - opening
    if abs(move) < params["threshold_atr"] * atr:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def cheap_close_signal(index, bars, ctx, params, _state):
    """Enter late, when the book is cheapest, and leave on the bell.

    THE EXIT IS THE POINT AND IT IS FREE. Under `SESSION_ONLY` every position
    is flattened at the last in-session bar, and that flatten is the ONE exit
    the backtest and the runtime schedule identically -- both act when the
    session's final candle rolls, at that bar's open. There is no level to
    watch, so there is no whole-bar overshoot: the 3.73 bp median the stop
    exits pay simply does not arise. A cell that enters an hour before the
    close and holds to it has an execution model with no error in it at all.

    ENTERING LATE IS NOT A SEPARATE ASSUMPTION. `spread_rank` is measured, and
    on every symbol here the cheap tail is reachable -- so the family asks for
    the last stretch of the session AND the cheap part of the book, and takes
    whatever direction the session has already established. If the close-out
    drift is real, this is the cheapest possible way to own it.

    The direction is the session's own return so far, because a rule that has
    to be flat in an hour cannot afford to be early.
    """
    bar = bars[index]
    if ctx["daily"]:
        return None
    minute = bar[TS] % 86_400 // 60
    closed = ctx["cfg"]["session"][1]
    if minute < closed - params["window_minutes"] or minute >= closed:
        return None
    reading = _spread_reading(ctx, index)
    if reading is None or reading[1] > params["max_rank"]:
        return None
    opening = ctx["session_open"][index]
    atr = ctx["atr"][index]
    if not (present(opening) and present(atr)) or atr <= 0 or opening <= 0:
        return None
    move = bar[C] - opening
    if abs(move) < params["threshold_atr"] * atr:
        return None
    side = 1 if move > 0 else -1
    return -side if params["direction"] == "fade" else side


def spread_divergence_signal(index, bars, ctx, params, _state):
    """A new extreme the book refuses to follow.

    THE DIVERGENCE IS BETWEEN PRICE AND WHO IS QUOTING IT. A break to a new
    `channel`-bar high is the most ordinary bullish event in this module and
    every breakout family trades it. This one asks a question none of them can:
    what were the makers doing while it happened. A break made while the spread
    sits in the cheap part of its own range was quoted INTO -- somebody was
    willing to stand there. A break made while the spread is in its expensive
    tail was not: the book widened away from the move, which is what a venue
    does when it does not believe the price it is showing.

    Faded, because that is the side the reading argues for and a family should
    make one claim. `spread_shock` already carries the follow branch on a bar's
    own move, so putting both here would be the same axis twice.

    MEASURED, NOT ASSUMED, that this can fire: the expensive tail at 0.9 covers
    9-18% of bars on every symbol in this study, and a channel break is common
    enough that the intersection is a sample rather than an anecdote.
    """
    bar = bars[index]
    if _late(ctx, bar, params):
        return None
    reading = _spread_reading(ctx, index)
    if reading is None:
        return None
    _bp, rank, _ratio = reading
    if rank < params["min_rank"]:
        return None
    channel = params["channel"]
    upper, lower = ctx["high"][channel][index], ctx["low"][channel][index]
    if not (present(upper) and present(lower)):
        return None
    side = 1 if bar[C] > upper else -1 if bar[C] < lower else None
    if side is None:
        return None
    # The break has to be a break, not a brush against the level.
    atr = ctx["atr"][index]
    if not present(atr) or atr <= 0:
        return None
    edge = upper if side == 1 else lower
    if abs(bar[C] - edge) < params["extension_atr"] * atr:
        return None
    return -side


class Family:
    """One entry thesis, plus where it is allowed to run.

    `scope` is the honest part. A family that reads a session -- an opening
    range, a VWAP, a clock time -- has nothing to read on a daily bar, and a
    family that reads a calendar needs more than one day of history. Rather than
    let either degenerate quietly, each declares where it means something and is
    skipped everywhere else.

    `needs` extends the same principle to data rather than to timeframe. A
    relative-strength family is meaningless without a benchmark series, and a
    volume family is meaningless on a table whose volume column is a tick count
    of zero. Declaring the requirement means the family disappears from the grid
    for a symbol that cannot support it, instead of producing a run of `None`
    signals that `select` would then report as "no cell passed" -- which reads
    as a finding and is not one.

    `group` is the thesis group, and it exists because the budget does. Sixty-two
    families across seven timeframes is a search a person cannot hold in their
    head, so `--groups` runs one line of enquiry at a time and `budget` prices it
    before it starts.

    `reads` names the OPTIONAL context blocks this family needs, and it exists
    because building all of them cost a crashed machine. `extra_context` used to
    build every block unconditionally: 116 full-length arrays on top of the
    original 56, which on ES is 201 MB per worker against the original 60. Under
    Windows `multiprocessing` there is no fork and no copy-on-write, so fifteen
    workers each built their own complete copy -- 3.6 GB at peak against 1.4 GB
    for the original study. Declaring the requirement means a run only pays for
    the blocks its families actually read, so `--groups original` is back to
    60 MB and `--groups xma` builds the average zoo and nothing else.
    """

    __slots__ = ("signal", "build", "scope", "hold", "symbols", "group", "needs",
                 "reads")

    def __init__(self, signal, build, scope="any", hold="session", symbols=None,
                 group="core", needs=None, reads=()):
        self.signal = signal
        self.build = build
        self.scope = scope
        self.hold = hold
        self.symbols = symbols
        self.group = group
        self.needs = needs
        self.reads = reads


FAMILIES = {
    # ---- the original nine, unchanged in behaviour --------------------------
    "orb": Family(orb_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "range_bars": (1, 2),
        "breakout_atr": (0.0, .25), "last_entry_minute": g["last"], **g["common"]},
        scope="intraday"),
    "overnight": Family(overnight_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "buffer_atr": (0.0, .25),
        "last_entry_minute": g["last"], **g["common"]}, scope="intraday"),
    "pdr": Family(pdr_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "buffer_atr": (0.0, .25),
        "last_entry_minute": g["last"], **g["common"]}),
    "donchian": Family(donchian_signal, lambda p, g: {
        "channel": (p["session"], 2 * p["session"], 4 * p["session"]),
        "last_entry_minute": g["last"], **g["common"]}),
    "ma_cross": Family(ma_cross_signal, lambda p, g: {
        "fast": p["fast"], "slow": p["slow"],
        "last_entry_minute": g["last"], **g["common"]}),
    "momentum": Family(momentum_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "signal_minute": g["signal"],
        "lookback": (p["session"], 2 * p["session"]),
        "threshold_atr": (.5, 1.0), **g["common"]}),
    "gap": Family(gap_signal, lambda p, g: {
        "direction": ("fade", "follow"),
        "threshold_atr": (.25, .5, 1.0), **g["common"]}),
    "vwap": Family(vwap_signal, lambda p, g: {
        "direction": ("fade", "follow"), "threshold_atr": (.5, 1.0, 1.5),
        "last_entry_minute": g["last"], **g["common"]}, scope="intraday"),
    "zscore": Family(zscore_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["zscore"],
        "threshold_z": (1.5, 2.5), "last_entry_minute": g["last"], **g["common"]}),
    "storage": Family(storage_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "signal_minute": (10 * 60 + 30, 11 * 60),
        "threshold_atr": (.5, 1.0), **g["common"]},
        scope="intraday", symbols=("xngusd",)),

    # ---- live: the execution, read as a signal ------------------------------
    "spread_gate": Family(spread_gate_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "channel": p["channels"],
        # 0.9 is the control: it admits all but the worst tenth, so a cell that
        # only works at 0.2 has shown the gate did the work.
        "max_rank": (0.2, 0.4, 0.9),
        "last_entry_minute": g["last"], **g["live"]},
        group="live", needs="spread", reads=("spread",)),
    "spread_shock": Family(spread_shock_signal, lambda p, g: {
        "direction": ("fade", "follow"),
        "min_rank": (0.8, 0.9, 0.95),
        "threshold_atr": (0.5, 1.0),
        "last_entry_minute": g["last"], **g["live"]},
        group="live", needs="spread", reads=("spread",)),
    "clock_momentum": Family(clock_momentum_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "lookback": (p["session"] // 2 or 1, p["session"], 2 * p["session"]),
        "threshold_atr": (0.5, 1.0, 1.5),
        "last_entry_minute": g["last"], **g["live_clock"]},
        group="live"),
    "cost_edge": Family(cost_edge_signal, lambda p, g: {
        # 0.0 is the control: it admits every bar, so the family collapses to
        # its own pullback entry and says what the cost filter was worth.
        "min_cover": (0.0, 0.5, 0.7),
        "max_depth_atr": (1.0, 2.0),
        "last_entry_minute": g["last"], **g["live"]},
        group="live", needs="spread", reads=("spread",)),
    "liquidity_router": Family(liquidity_router_signal, lambda p, g: {
        "channel": p["channels"],
        "calm_rank": (0.2, 0.4),
        "stress_rank": (0.6, 0.8),
        "last_entry_minute": g["last"], **g["live"]},
        group="live", needs="spread", reads=("spread",)),
    "anchored_clock": Family(anchored_clock_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "lookback": (p["session"] // 2 or 1, p["session"]),
        "threshold_atr": (0.5, 1.0, 1.5),
        # 1.0 is the control: it is `clock_momentum` exactly.
        "stop_multiple": (1.0, 2.0, 3.0),
        "last_entry_minute": g["last"], **g["live_clock"]},
        group="live"),
    "session_carry": Family(session_carry_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "window_minutes": (30, 60, 120),
        "threshold_atr": (0.0, 0.5, 1.0),
        "stop_multiple": (1.0, 2.0, 3.0),
        # The bell and nothing else -- see the docstring.
        "exit_mode": ("days_1",), "stop_day": STOP_DAY,
        "trend": ("none",), "vol_mode": ("none",)},
        group="live", scope="intraday"),
    "cheap_close": Family(cheap_close_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "window_minutes": (60, 120),
        "max_rank": (0.4, 0.9),
        "threshold_atr": (0.0, 1.0),
        # Its own exit grid: the bell, and the bell only. `days_1` resolves to
        # one session of bars, which under SESSION_ONLY the flatten reaches
        # first -- so every cell here exits on the close by construction and
        # the family tests the entry rather than the exit.
        "exit_mode": ("days_1",), "stop_day": STOP_DAY,
        "trend": ("none",), "vol_mode": ("none",)},
        group="live", needs="spread", reads=("spread",), scope="intraday"),
    "spread_divergence": Family(spread_divergence_signal, lambda p, g: {
        "channel": p["channels"],
        "min_rank": (0.8, 0.9),
        "extension_atr": (0.0, 0.25),
        "last_entry_minute": g["last"], **g["live"]},
        group="live", needs="spread", reads=("spread",)),
    "spread_trend": Family(spread_trend_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "fast": p["fast"], "slow": p["slow"],
        "max_rank": (0.4, 0.6),
        "persist": (2, 4),
        "last_entry_minute": g["last"], **g["live"]},
        group="live", needs="spread", reads=("spread",)),

    # ---- structure ----------------------------------------------------------
    "ib": Family(ib_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "ib_minutes": (60, 120),
        "extension": (0.0, .25), "last_entry_minute": g["last"], **g["common"]},
        scope="intraday"),
    "failed_break": Family(failed_break_signal, lambda p, g: {
        "direction": ("fade", "follow"), "buffer_atr": (0.0, .25, .5),
        "last_entry_minute": g["last"], **g["common"]}),
    "nr": Family(nr_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "compress": p["compress"],
        "last_entry_minute": g["last"], **g["common"]}),
    "inside": Family(inside_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "location": (.6, .75),
        "last_entry_minute": g["last"], **g["common"]}),
    "keltner": Family(keltner_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "reference": ("ema_20d", "ema_50d"),
        "mult": (1.0, 2.0, 3.0), "last_entry_minute": g["last"], **g["common"]}),
    "squeeze": Family(squeeze_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "compress": p["compress"],
        "tolerance": (1.0, 1.25), "last_entry_minute": g["last"], **g["common"]}),
    "range_expansion": Family(range_expansion_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "signal_minute": g["signal"],
        "expansion": (.75, 1.0, 1.5), "last_entry_minute": g["last"],
        **g["common"]}, scope="intraday"),

    # ---- exhaustion and reversal -------------------------------------------
    "consecutive": Family(consecutive_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "count": (2, 3, 4),
        "last_entry_minute": g["last"], **g["common"]}),
    "rsi": Family(rsi_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["rsi"],
        "threshold": (65.0, 75.0, 85.0), "last_entry_minute": g["last"],
        **g["common"]}),
    "key_reversal": Family(key_reversal_signal, lambda p, g: {
        "direction": ("fade", "follow"),
        "channel": (p["session"], 4 * p["session"], 20 * p["session"]),
        "last_entry_minute": g["last"], **g["common"]}),
    "climax": Family(climax_signal, lambda p, g: {
        "direction": ("fade", "follow"), "volume_period": p["volume"],
        "volume_mult": (2.0, 3.0), "range_mult": (1.0, 1.5),
        "location": (.7, .85), "last_entry_minute": g["last"], **g["common"]}),
    "volume_thrust": Family(volume_thrust_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "volume_period": p["volume"],
        "volume_mult": (2.0, 3.0), "threshold_atr": (.5, 1.0),
        "last_entry_minute": g["last"], **g["common"]}),

    # ---- structural drift, and the calendar --------------------------------
    "time_of_day": Family(time_of_day_signal, lambda p, g: {
        "side": ("long", "short"), "entry_minute": g["entry"], **g["common"]},
        scope="intraday"),
    "turn_of_month": Family(turn_of_month_signal, lambda p, g: {
        "side": ("long", "short"), "before": (1, 2, 3), "after": (1, 2, 3),
        **g["swing"]}, hold="swing"),
    "seasonality": Family(seasonality_signal, lambda p, g: {
        "side": ("long", "short"), "month": tuple(range(1, 13)), **g["swing"]},
        scope="daily", hold="swing"),

    # ---- multi-day ----------------------------------------------------------
    "swing_donchian": Family(swing_donchian_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "channel": (10 * p["session"], 20 * p["session"], 55 * p["session"]),
        **g["swing"]}, hold="swing"),
    "swing_ma": Family(swing_ma_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "fast": p["fast"], "slow": p["slow"],
        **g["swing"]}, hold="swing"),
    "swing_zscore": Family(swing_zscore_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["zscore"],
        "threshold_z": (1.5, 2.5), **g["swing"]}, hold="swing"),
    "high_52w": Family(high_52w_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "channel": (p["year"],),
        "proximity": (.02, .05), "depth": (.15, .25), **g["swing"]},
        hold="swing"),

    # ---- the moving-average zoo --------------------------------------------
    # `kind` is the axis that makes these three worth their budget. It is a
    # LABEL, not a scale, so it lives in `CATEGORICAL` and the robustness test
    # steps over it rather than calling `hma` a neighbour of `kama`.
    "xma_cross": Family(xma_cross_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "kind": tuple(ind.MA_KINDS),
        "fast": p["xma_fast"], "slow": p["xma_slow"],
        "last_entry_minute": g["last"], **g["common"]}, reads=("xma",)),
    # Shape rather than position, and the difference between adjacent members of
    # the zoo is second-order for a slope -- so these two take the three ends of
    # the lag spectrum instead of all six, at a third of the cells.
    "xma_slope": Family(xma_slope_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "kind": XMA_SHAPE_KINDS,
        "period": p["xma_slow"], "slope": (.25, .75),
        "last_entry_minute": g["last"], **g["common"]}, reads=("xma",)),
    "xma_ribbon": Family(xma_ribbon_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "kind": XMA_SHAPE_KINDS,
        "width": (1.0, 2.0), "last_entry_minute": g["last"], **g["common"]}, reads=("xma",)),

    # ---- trend quality and regime ------------------------------------------
    "linreg_trend": Family(linreg_trend_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["linreg"],
        "slope": (.5, 1.5), "min_fit": (.3, .6),
        "last_entry_minute": g["last"], **g["common"]}, reads=("linreg",)),
    "efficiency": Family(efficiency_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["er"],
        "min_er": (.3, .5), "regime": ("clean", "noisy"),
        "last_entry_minute": g["last"], **g["common"]}, reads=("er",)),
    # No `direction` axis on purpose: the variance ratio supplies the sign, and
    # giving it one as well would let the search undo the only property that
    # makes this family uncorrelated with momentum and reversion alike.
    "regime_switch": Family(regime_switch_signal, lambda p, g: {
        "step": p["vr_step"], "band": (.15, .35),
        "last_entry_minute": g["last"], **g["common"]}, reads=("vr",)),
    "skew": Family(skew_signal, lambda p, g: {
        "side": ("long", "short"), "mode": ("negative", "positive"),
        "threshold": (.3, .8), **g["swing"]}, hold="swing", reads=("skew",)),
    "vol_regime": Family(vol_regime_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "ratio": (1.3, 1.8),
        "regime": ("expanding", "contracting"),
        "last_entry_minute": g["last"], **g["common"]}),

    # ---- oscillators --------------------------------------------------------
    "stochastic": Family(stochastic_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["stoch"],
        "threshold": (20.0, 30.0), "last_entry_minute": g["last"], **g["common"]}, reads=("stoch",)),
    "cci": Family(cci_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["cci"],
        "threshold": (100.0, 150.0, 200.0),
        "last_entry_minute": g["last"], **g["common"]}, reads=("cci",)),
    "macd_hist": Family(macd_hist_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "macd_set": p["macd"],
        "level": ("zero", "turn"), "last_entry_minute": g["last"], **g["common"]}, reads=("macd",)),
    "dmi": Family(dmi_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["adx"],
        "min_adx": (20.0, 30.0), "last_entry_minute": g["last"], **g["common"]}, reads=("adx",)),
    "aroon": Family(aroon_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["aroon"],
        "min_strength": (70.0, 90.0),
        "last_entry_minute": g["last"], **g["common"]}, reads=("aroon",)),
    "rsi_divergence": Family(rsi_divergence_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["rsi"],
        "channel": (4 * p["session"], 20 * p["session"]), "gap": (5.0, 10.0),
        "last_entry_minute": g["last"], **g["common"]}),

    # ---- price geometry -----------------------------------------------------
    "supertrend": Family(supertrend_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "mult": SUPERTREND_MULTIPLES,
        "last_entry_minute": g["last"], **g["common"]}, reads=("supertrend",)),
    "sar": Family(sar_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "last_entry_minute": g["last"], **g["common"]}, reads=("sar",)),
    "swing_break": Family(swing_break_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "wing": p["wing"],
        "last_entry_minute": g["last"], **g["common"]}, reads=("pivots",)),
    "structure": Family(structure_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "wing": p["wing"],
        "mode": ("continuation", "reversal"),
        "last_entry_minute": g["last"], **g["common"]}, reads=("pivots",)),
    "fvg": Family(fvg_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "min_width": (.05, .15),
        "last_entry_minute": g["last"], **g["common"]}, reads=("gaps",)),
    "floor_pivot": Family(floor_pivot_signal, lambda p, g: {
        "direction": ("fade", "follow"), "level": ("pivot", "first", "second"),
        "buffer_atr": (0.0, .25), "last_entry_minute": g["last"], **g["common"]}, reads=("floor_pivots",)),
    "pullback": Family(pullback_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "channel": (4 * p["session"], 20 * p["session"]), "depth": (.5, 1.0),
        "reference": ("ema_20d", "ema_50d"),
        "last_entry_minute": g["last"], **g["common"]}),
    "volatility_breakout": Family(volatility_breakout_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "fraction": (.3, .5, .8),
        "last_entry_minute": g["last"], **g["common"]}, scope="intraday"),
    "avwap": Family(avwap_signal, lambda p, g: {
        "direction": ("fade", "follow"), "anchor": ("week", "month"),
        "threshold_atr": (1.0, 2.0), **g["swing"]}, hold="swing", reads=("avwap",)),
    "wick": Family(wick_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "min_tail": (.5, .65),
        "max_body": (.2, .35), "wick": ("any", "with_trend"),
        "last_entry_minute": g["last"], **g["common"]}, reads=("wick",)),

    # ---- volume, read cumulatively -----------------------------------------
    "obv_break": Family(obv_break_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "channel": p["obv"],
        "last_entry_minute": g["last"], **g["common"]}, reads=("obv",)),
    "obv_divergence": Family(obv_divergence_signal, lambda p, g: {
        "direction": ("fade", "follow"),
        "channel": (4 * p["session"], 20 * p["session"]),
        "last_entry_minute": g["last"], **g["common"]}, reads=("obv",)),
    "mfi": Family(mfi_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["mfi"],
        "threshold": (60.0, 70.0), "last_entry_minute": g["last"], **g["common"]}, reads=("mfi",)),
    "cmf": Family(cmf_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["cmf"],
        "threshold": (.1, .2), "last_entry_minute": g["last"], **g["common"]}, reads=("cmf",)),
    "rvol": Family(rvol_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "rvol": (1.5, 2.5),
        "threshold_atr": (.5, 1.0), "last_entry_minute": g["last"],
        **g["common"]}, scope="intraday", reads=("rvol",)),

    # ---- relative to a benchmark -------------------------------------------
    "rel_momentum": Family(rel_momentum_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "lookback": p["rel"],
        "threshold": (1.0, 3.0), **g["swing"]},
        hold="swing", needs="benchmark", reads=("relative",)),
    "rel_zscore": Family(rel_zscore_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["rel"],
        "threshold_z": (1.5, 2.5), "last_entry_minute": g["last"],
        **g["common"]}, needs="benchmark", reads=("relative",)),
    "rel_break": Family(rel_break_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "channel": p["rel"], **g["swing"]},
        hold="swing", needs="benchmark", reads=("relative",)),

    # ---- the calendar, once more -------------------------------------------
    "day_of_week": Family(day_of_week_signal, lambda p, g: {
        "side": ("long", "short"), "weekday": (0, 1, 2, 3, 4), **g["swing"]},
        hold="swing"),

    # ---- gated: one thesis, one confirmer that is NOT a moving average ------
    # The confirmer is an AXIS rather than thirteen families, because "which
    # confirmer" is the question and thirteen one-gate families would answer it
    # by selection: the best of thirteen winners is not the same object as the
    # winner of one thirteen-value axis, and only the second has a stateable
    # budget. `polarity` doubles it on purpose -- see `gate_accepts`.
    "gated_orb": Family(gated_orb_signal, lambda p, g: {
        "gate": g["gates"], "polarity": ("confirm", "oppose"),
        "last_entry_minute": g["last"], **g["common"]},
        scope="intraday", reads=gate_reads() + ("opening",)),
    "gated_donchian": Family(gated_donchian_signal, lambda p, g: {
        "gate": g["gates"], "polarity": ("confirm", "oppose"),
        "channel": (4 * p["session"], 20 * p["session"]),
        "last_entry_minute": g["last"], **g["common"]}, reads=gate_reads()),
    "gated_fade": Family(gated_fade_signal, lambda p, g: {
        "trigger": g["fades"], "gate": g["gates"],
        "polarity": ("confirm", "oppose"),
        "last_entry_minute": g["last"], **g["common"]}, reads=gate_reads()),
    "gated_swing": Family(gated_swing_signal, lambda p, g: {
        "trigger": ("donchian", "ma"), "gate": g["gates"],
        "polarity": ("confirm", "oppose"), **g["swing"]},
        hold="swing", reads=gate_reads()),

    # ---- fused: two theses, and neither one is a filter on the other --------
    "confluence": Family(confluence_signal, lambda p, g: {
        "pool": tuple(CONFLUENCE_POOLS), "votes": (2, 3),
        "mode": ("follow", "fade"),
        "last_entry_minute": g["last"], **g["common"]},
        reads=("macd", "supertrend", "obv", "wick")),
    "two_stage": Family(two_stage_signal, lambda p, g: {
        "coil": ("squeeze", "nr"), "window": (3, 6),
        "direction": ("breakout", "fade"),
        "last_entry_minute": g["last"], **g["common"]}),
    "break_retest": Family(break_retest_signal, lambda p, g: {
        "level": ("pdr", "donchian"), "retest": ("level", "vwap"),
        "window": (3, 8), "last_entry_minute": g["last"], **g["common"]},
        scope="intraday"),
    "regime_router": Family(regime_router_signal, lambda p, g: {
        "regime": ("er", "vr", "adx"), "strictness": ("loose", "strict"),
        "last_entry_minute": g["last"], **g["common"]},
        reads=("er", "vr", "adx")),
    "nested": Family(nested_signal, lambda p, g: {
        "construct": NESTED_CONSTRUCTS, "agreement": ("aligned", "opposed"),
        "last_entry_minute": g["last"], **g["common"]}),
    "cross_timing": Family(cross_timing_signal, lambda p, g: {
        "pair": tuple(CROSS_PAIRS), "window": (2, 5),
        "direction": ("breakout", "fade"),
        "last_entry_minute": g["last"], **g["common"]},
        reads=("macd", "stoch")),
    "level_confluence": Family(level_confluence_signal, lambda p, g: {
        "pair": tuple(LEVEL_PAIRS), "tolerance": (.25, .5),
        "direction": ("fade", "follow"),
        "last_entry_minute": g["last"], **g["common"]},
        scope="intraday", reads=("floor_pivots",)),
    "trap": Family(trap_signal, lambda p, g: {
        "level": ("pdr", "donchian", "swing"), "window": (3, 8),
        "last_entry_minute": g["last"], **g["common"]}, reads=("pivots",)),
    "idio_break": Family(idio_break_signal, lambda p, g: {
        "channel": p["rel"][1:], "direction": ("breakout", "fade"),
        "last_entry_minute": g["last"], **g["common"]},
        needs="benchmark", reads=("relative",)),

    # ---- the night: seven families in a holding regime nothing else uses ----
    # Every one fires on the last bar from which an entry can still reach the
    # close, and exits at an OPEN one, two or three sessions later. `g["night"]`
    # carries `vol_mode=("none",)` alone -- see `night_vol` for why the shared
    # calm filter is withheld from this group.
    "night_drift": Family(night_drift_signal, lambda p, g: {
        "side": ("long", "short"), "weekday": ("any", 0, 1, 2, 3, 4),
        **g["night"]}, scope="night", hold="overnight"),
    "night_close_location": Family(night_close_location_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "edge": (.7, .85),
        **g["night"]}, scope="night", hold="overnight"),
    "night_day_return": Family(night_day_return_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "threshold_day": (.25, .5, 1.0),
        **g["night"]}, scope="night", hold="overnight"),
    "night_gap_echo": Family(night_gap_echo_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "threshold_day": (.1, .25),
        **g["night"]}, scope="night", hold="overnight", reads=("nights",)),
    "night_vol": Family(night_vol_signal, lambda p, g: {
        "side": ("long", "short"), "state": ("quiet", "violent"),
        "ratio": (.8, 1.2), **g["night"]}, scope="night", hold="overnight"),
    "night_streak": Family(night_streak_signal, lambda p, g: {
        "direction": ("fade", "follow"), "count": (2, 3, 4),
        **g["night"]}, scope="night", hold="overnight", reads=("nights",)),
    "night_relative": Family(night_relative_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "threshold": (.25, .5),
        **g["night"]}, scope="night", hold="overnight",
        needs="benchmark", reads=("relative",)),

    # ---- the almanac: dates that were named in print before this study ------
    "almanac_window": Family(almanac_window_signal, lambda p, g: {
        "side": ("long", "short"), "season": tuple(SEASONAL_WINDOWS),
        **g["swing"]}, scope="daily", hold="swing"),
    "opex": Family(opex_signal, lambda p, g: {
        "side": ("long", "short"), "when": ("week", "day", "after"),
        **g["swing"]}, scope="daily", hold="swing"),
    "holiday": Family(holiday_signal, lambda p, g: {
        "side": ("long", "short"), "when": ("before", "after"),
        **g["swing"]}, scope="daily", hold="swing", reads=("breaks",)),
    "turn_of_quarter": Family(turn_of_quarter_signal, lambda p, g: {
        "side": ("long", "short"), "before": (1, 3), "after": (1, 3),
        **g["swing"]}, scope="daily", hold="swing"),

    # ---- the horizon: position scale, on `g["position"]` --------------------
    # All seven are `scope="daily"` because all seven read `p["horizon"]`,
    # `p["faber"]`, `p["peak"]` or `p["nights"]`, which are session counts left
    # UNCONVERTED -- see `periods`. On a daily bar sessions and bars are the
    # same number and the reading means what it says; at 30m a 252 would be
    # five days.
    "tsmom": Family(tsmom_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "lookback": p["horizon"],
        "check": ("month", "any"), **g["position"]},
        scope="daily", hold="swing"),
    "skip_momentum": Family(skip_momentum_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "lookback": p["horizon"][1:],
        "threshold": (0.0, 5.0), **g["position"]},
        scope="daily", hold="swing"),
    "faber": Family(faber_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["faber"],
        "check": ("month", "any"), **g["position"]},
        scope="daily", hold="swing", reads=("ages",)),
    "drawdown_depth": Family(drawdown_depth_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "channel": p["peak"],
        "depth": (.1, .2), "age_share": (.15, .5), **g["position"]},
        scope="daily", hold="swing", reads=("ages",)),
    "trend_age": Family(trend_age_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["faber"],
        "state": ("young", "old"), "age": (20, 60), **g["position"]},
        scope="daily", hold="swing", reads=("ages",)),
    "night_share": Family(night_share_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "span": p["nights"],
        "share": (.3, .6), **g["position"]},
        scope="daily", hold="swing", reads=("nights",)),
    "calendar_break": Family(calendar_break_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "unit": ("week", "month", "quarter"),
        "buffer_atr": (0.0, .25), **g["position"]},
        scope="daily", hold="swing", reads=("spans",)),

    # ---- the pair, read off the regression rather than the ratio ------------
    # `threshold` is a CORRELATION here and a percentage in the other two.
    # Measured on ETHUSD against BTC over a quarter of daily returns, the
    # rolling correlation sits at a median of 0.87 and spends 4% of its life
    # below 0.5 and 31% below 0.8 -- so these two values are "genuinely
    # decoupled" and "loosened up", and 0.3, which the first draft carried,
    # is reached 2.5% of the time on the one crypto pair and would be dead on
    # anything tighter.
    "bench_correlation": Family(bench_correlation_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["pair"],
        "threshold": (.5, .8), **g["swing"]},
        hold="swing", needs="benchmark", reads=("bench",)),
    "residual_momentum": Family(residual_momentum_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["pair"],
        "threshold": (1.0, 3.0), **g["swing"]},
        hold="swing", needs="benchmark", reads=("bench",)),
    "lead_lag": Family(lead_lag_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["pair"],
        "threshold": (1.0, 2.0), "catchup": (.5, 1.0), **g["swing"]},
        hold="swing", needs="benchmark", reads=("bench",)),

    # ---- the fifth wave: the months-scale entries that were missing ---------
    "long_channel": Family(long_channel_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "channel": p["long_channel"],
        "buffer_atr": (0.0, .25), **g["position"]},
        scope="daily", hold="swing"),
    "multi_horizon_trend": Family(multi_horizon_trend_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "votes": (2, 3),
        "check": ("month", "any"), **g["position"]},
        scope="daily", hold="swing"),
    "long_reversal": Family(long_reversal_signal, lambda p, g: {
        "direction": ("fade", "follow"), "lookback": p["reversal"],
        "threshold": (0.0, 20.0), **g["position"]},
        scope="daily", hold="swing"),
    "momentum_crash_filter": Family(momentum_crash_filter_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "lookback": p["horizon"][1:],
        "period": p["faber"], "state": ("calm", "panic"), "ratio": (1.2,),
        **g["position"]}, scope="daily", hold="swing", reads=("ages",)),

    # ---- the fifth wave: a factor that is not a price ----------------------
    "vix_regime": Family(vix_regime_signal, lambda p, g: {
        "side": ("long", "short"), "zone": ("high", "low"),
        "window": p["vix"], "quantile": (.8, .9), **g["position"]},
        scope="daily", hold="swing", needs="vix", reads=("vix",)),
    "vol_risk_premium": Family(vol_risk_premium_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "window": p["vix"],
        "premium": (2.0, 6.0), **g["position"]},
        scope="daily", hold="swing", needs="vix", reads=("vix",)),

    # ---- the fifth wave: what carry could actually be tested as -------------
    "swap_side_bias": Family(swap_side_bias_signal, lambda p, g: {
        "bias": ("cheap", "either"), "lookback": p["horizon"],
        **g["position"]}, scope="daily", hold="swing"),

    # ---- the sixth wave: what KIND of process is this ----------------------
    # Every grid below carries at least two genuine SCALES, and that is not
    # decoration: a family whose axes are all labels reports `1/1` robust
    # neighbours and has never been perturbed
    # ([[all-categorical-axes-void-the-robustness-gate]]). `night` is the group
    # that fell into that hole and it is recorded there; these were built
    # against it.
    "hurst": Family(hurst_signal, lambda p, g: {
        "period": p["hurst"], "band": (.05, .1),
        "last_entry_minute": g["last"], **g["common"]}, reads=("hurst",)),
    "entropy": Family(entropy_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["entropy"],
        "threshold": (.85, .92), "state": ("ordered", "random"),
        "last_entry_minute": g["last"], **g["common"]}, reads=("entropy",)),
    "kurtosis": Family(kurtosis_signal, lambda p, g: {
        "side": ("long", "short"), "state": ("fat", "thin"),
        "threshold": (1.0, 3.0), "last_entry_minute": g["last"],
        **g["common"]}, reads=("kurtosis",)),
    "autocorr": Family(autocorr_signal, lambda p, g: {
        "period": p["autocorr"], "lag": p["lags"], "threshold": (.05, .12),
        "last_entry_minute": g["last"], **g["common"]}, reads=("autocorr",)),
    "runs": Family(runs_signal, lambda p, g: {
        "period": p["runs"], "state": ("sticky", "choppy"),
        "threshold": (1.0, 2.0), "last_entry_minute": g["last"],
        **g["common"]}, reads=("runs",)),
    "kendall": Family(kendall_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["kendall"],
        "threshold": (2.0, 3.5), "last_entry_minute": g["last"],
        **g["common"]}, reads=("kendall",)),
    "tails": Family(tail_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "threshold": (.2, .5),
        "last_entry_minute": g["last"], **g["common"]}, reads=("tails",)),

    # ---- the sixth wave: liquidity and jumps, read off the bar -------------
    "jump": Family(jump_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["jump"],
        "share": (.2, .4), "state": ("jumpy", "diffusive"),
        "last_entry_minute": g["last"], **g["common"]}, reads=("jump",)),
    "semivariance": Family(semivariance_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["semi"],
        "threshold": (.15, .35), "last_entry_minute": g["last"],
        **g["common"]}, reads=("semi",)),
    "amihud": Family(amihud_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "ratio": (1.3, 2.0),
        "state": ("illiquid", "liquid"), "last_entry_minute": g["last"],
        **g["common"]}, reads=("amihud",)),
    "estimator": Family(estimator_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["estimator"],
        "estimator": ("parkinson", "garman_klass"), "ratio": (1.5, 2.5),
        "state": ("whipping", "gapping"), "last_entry_minute": g["last"],
        **g["common"]}, reads=("estimators",)),
    "bulk_flow": Family(bulk_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "period": p["bulk"],
        "threshold": (.15, .3), "last_entry_minute": g["last"],
        **g["common"]}, reads=("bulk",)),

    # ---- the sixth wave: filters that are not moving averages --------------
    "roofing": Family(roofing_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "band": p["roof"],
        "amplitude_atr": (.25, .75), "last_entry_minute": g["last"],
        **g["common"]}, reads=("roofing",)),
    "fisher": Family(fisher_signal, lambda p, g: {
        "mode": ("extreme", "turn"), "period": p["fisher"],
        "threshold": (1.5, 2.5), "last_entry_minute": g["last"],
        **g["common"]}, reads=("fisher",)),
    "kalman": Family(kalman_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "mode": ("slope", "residual"),
        "threshold": (.5, 1.5), "last_entry_minute": g["last"],
        **g["common"]}, reads=("kalman",)),
    "fracdiff": Family(fracdiff_signal, lambda p, g: {
        "direction": ("fade", "follow"), "order": p["fracdiff"],
        "threshold_z": (1.5, 2.5), "last_entry_minute": g["last"],
        **g["common"]}, reads=("fracdiff",)),
    "cycle": Family(cycle_signal, lambda p, g: {
        "state": ("fast", "slow"), "share": (.35, .5),
        "last_entry_minute": g["last"], **g["common"]}, reads=("cycle",)),

    # ---- the sixth wave: the rule sizes its own horizon --------------------
    "half_life": Family(half_life_signal, lambda p, g: {
        "direction": ("fade", "follow"), "period": p["halflife"],
        "multiple": (1.0, 2.0), "threshold_z": (1.5, 2.5),
        "last_entry_minute": g["last"], **g["common"]},
        reads=("halflife", "prefix")),
    "cusum": Family(cusum_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "multiple": p["cusum"],
        "last_entry_minute": g["last"], **g["common"]}, reads=("cusum",)),
    "vol_of_vol": Family(vol_of_vol_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "state": ("unsteady", "steady"),
        "dispersion": (.2, .35), "last_entry_minute": g["last"],
        **g["common"]}),
    "quantile_break": Family(quantile_break_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "channel": p["quantile"],
        "share": QUANTILE_EDGES, "buffer_atr": (0.0, .25),
        "last_entry_minute": g["last"], **g["common"]}, reads=("quantile",)),

    # ---- the sixth wave: the winners, fused --------------------------------
    # `response = 0.0` in `regime_breakout` and the `0.98` share in
    # `quantile_break` are CONTROLS SITTING INSIDE THEIR OWN GRIDS: at those
    # values each family degenerates to the parent it claims to improve on, so
    # the comparison costs nothing and cannot be forgotten.
    "regime_breakout": Family(regime_breakout_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "fraction": (.3, .5),
        "response": (0.0, .5, 1.0), "last_entry_minute": g["last"],
        **g["common"]}, scope="intraday"),
    "momentum_stack": Family(momentum_stack_signal, lambda p, g: {
        "direction": ("breakout", "fade"), "threshold": (1.5, 3.0),
        "last_entry_minute": g["last"], **g["common"]}, reads=("prefix",)),
    "adaptive_pullback": Family(adaptive_pullback_signal, lambda p, g: {
        "direction": ("breakout", "fade"),
        "channel": (4 * p["session"], 20 * p["session"]), "depth": (.5, 1.0),
        "reference": ("ema_20d", "ema_50d"), "period": p["hurst"],
        "response": (.5, 1.0), "last_entry_minute": g["last"],
        **g["common"]}, reads=("hurst",)),
    "value_area": Family(value_area_signal, lambda p, g: {
        "direction": ("fade", "follow"), "level": ("poc", "edge"),
        "buffer_atr": (.1, .25), "last_entry_minute": g["last"],
        **g["common"]}, scope="intraday", reads=("value_area",)),
    "stat_confluence": Family(stat_confluence_signal, lambda p, g: {
        "mode": ("follow", "fade"), "votes": (2, 3),
        "last_entry_minute": g["last"], **g["common"]},
        reads=("hurst", "entropy", "jump", "semi", "bulk", "fisher")),
    "pivot_exhaustion": Family(pivot_exhaustion_signal, lambda p, g: {
        "level": ("first", "second"),
        "evidence": ("wick", "volume", "both"), "wick_ratio": (.4, .6),
        "volume_mult": (1.5, 2.5), "buffer_atr": (0.0, .25),
        "last_entry_minute": g["last"], **g["common"]},
        reads=("floor_pivots", "wick")),
}


#: THE TAXONOMY, AND THE ONLY PLACE IT IS WRITTEN DOWN.
#:
#: Seventy-five families is more than anyone can hold in their head, and a sweep
#: over all of them at once produces a winner nobody can defend. `--groups`
#: takes one line of enquiry at a time, and every group is a QUESTION rather
#: than a bag of indicators:
#:
#:   core        the original nine plus the event study -- the published set
#:   structure   does today's shape against a reference window predict?
#:   reversal    does an extreme in an oscillator revert?
#:   calendar    is there a return attached to a date rather than to a price?
#:   swing       does the same entry pay more if it is held past the close?
#:   xma         does the CHOICE of average matter, or only the periods?
#:   regime      does the market tell you which of momentum or reversion to run?
#:   geometry    are levels the market did not trade at still levels?
#:   flow        does cumulative volume say anything a single bar cannot?
#:   relative    what is left of a name once its benchmark is divided out?
#:   gate        does a second, independent reading earn the trades it refuses?
#:   combo       do two theses fused into ONE rule beat either of them alone?
#:   night       is the close-to-open leg a different asset from the daytime one?
#:   almanac     does a date nobody chose -- an expiry, a closure, a quarter
#:               boundary -- carry a return?
#:   horizon     does anything survive being held for a quarter instead of a day?
#:   crossasset  what does the REGRESSION on the benchmark say that the ratio
#:               cannot?
#:   exogenous   does a series that is NOT a price -- what options charge for
#:               the next thirty days -- say anything the traded series cannot?
#:   carry       the one thing financing can be tested as, given that its
#:               history is not available. Read `swap_side_bias` first.
#:   pathstat    what KIND of process is this -- its scaling law, the ORDER of
#:               its returns, its fourth moment, whether its signs are random
#:   micro       what the bar says about liquidity: which part of the variance
#:               was a jump, which side it came from, what the move cost
#:   filter      the things a moving average is not -- a bandpass, a
#:               distributional transform, an adaptive gain, a measured period
#:   adaptive    the rule sizes its own horizon, or its threshold accumulates
#:   fusion      the five archetypes that actually passed, recombined so the
#:               result is not a subset of either parent
#:
#: The assertion below is not decoration. A family absent from this table would
#: silently vanish from every `--groups` run while still costing budget in a
#: full sweep, and one listed twice would be selected twice and counted once.
GROUPS = {
    "core": ("orb", "overnight", "pdr", "donchian", "ma_cross", "momentum",
             "gap", "vwap", "zscore", "storage"),
    "structure": ("ib", "failed_break", "nr", "inside", "keltner", "squeeze",
                  "range_expansion"),
    "reversal": ("consecutive", "rsi", "key_reversal", "climax",
                 "volume_thrust"),
    "calendar": ("time_of_day", "turn_of_month", "seasonality", "day_of_week"),
    "swing": ("swing_donchian", "swing_ma", "swing_zscore", "high_52w"),
    "xma": ("xma_cross", "xma_slope", "xma_ribbon"),
    "regime": ("linreg_trend", "efficiency", "regime_switch", "skew",
               "vol_regime"),
    "oscillator": ("stochastic", "cci", "macd_hist", "dmi", "aroon",
                   "rsi_divergence"),
    "geometry": ("supertrend", "sar", "swing_break", "structure", "fvg",
                 "floor_pivot", "pullback", "volatility_breakout", "avwap",
                 "wick"),
    "flow": ("obv_break", "obv_divergence", "mfi", "cmf", "rvol"),
    "relative": ("rel_momentum", "rel_zscore", "rel_break"),
    "gate": ("gated_orb", "gated_donchian", "gated_fade", "gated_swing"),
    "combo": ("confluence", "two_stage", "break_retest", "regime_router",
              "nested", "cross_timing", "level_confluence", "trap",
              "idio_break"),
    "night": ("night_drift", "night_close_location", "night_day_return",
              "night_gap_echo", "night_vol", "night_streak", "night_relative"),
    "almanac": ("almanac_window", "opex", "holiday", "turn_of_quarter"),
    "horizon": ("tsmom", "skip_momentum", "faber", "drawdown_depth",
                "trend_age", "night_share", "calendar_break",
                "long_channel", "multi_horizon_trend", "long_reversal",
                "momentum_crash_filter"),
    "crossasset": ("bench_correlation", "residual_momentum", "lead_lag"),
    "exogenous": ("vix_regime", "vol_risk_premium"),
    "carry": ("swap_side_bias",),
    "pathstat": ("hurst", "entropy", "kurtosis", "autocorr", "runs", "kendall",
                 "tails"),
    "micro": ("jump", "semivariance", "amihud", "estimator", "bulk_flow"),
    "filter": ("roofing", "fisher", "kalman", "fracdiff", "cycle"),
    "adaptive": ("half_life", "cusum", "vol_of_vol", "quantile_break"),
    "fusion": ("regime_breakout", "momentum_stack", "adaptive_pullback",
               "value_area", "stat_confluence", "pivot_exhaustion"),
    #: The eighth wave, and the first one whose evidence is not a price. Five
    #: read the broker's own spread -- the same series the fill model charges --
    #: and one is built out of the exit asymmetry that model exposed.
    "live": ("spread_gate", "spread_shock", "clock_momentum", "cost_edge",
             "liquidity_router", "spread_trend", "cheap_close",
             "spread_divergence", "anchored_clock", "session_carry"),
}

for _group, _members in GROUPS.items():
    for _name in _members:
        FAMILIES[_name].group = _group

_covered = [name for members in GROUPS.values() for name in members]
assert sorted(_covered) == sorted(FAMILIES), (
    "GROUPS must partition FAMILIES exactly: "
    f"missing {sorted(set(FAMILIES) - set(_covered))}, "
    f"unknown {sorted(set(_covered) - set(FAMILIES))}, "
    f"duplicated {sorted({n for n in _covered if _covered.count(n) > 1})}")


#: Selections that cut ACROSS the thesis groups, so they cannot be groups
#: themselves without breaking the partition.
#:
#: `new` is the thirty-three families added in the second wave. They span six
#: whole groups plus `day_of_week`, which belongs with the other calendar rules
#: by thesis and with the second wave by provenance -- and provenance is what a
#: run needs to name when the question is "do the new ideas work". Without this
#: the same sweep is six group names plus a family name, which nobody will
#: retype identically a week later, and the scoped output file would be named
#: after a sixty-character join.
ALIASES = {
    "new": tuple(GROUPS["xma"] + GROUPS["regime"] + GROUPS["oscillator"]
                 + GROUPS["geometry"] + GROUPS["flow"] + GROUPS["relative"]
                 + ("day_of_week",)),
    #: Everything the published study already had, for the same comparison from
    #: the other side.
    "original": tuple(GROUPS["core"] + GROUPS["structure"] + GROUPS["reversal"]
                      + GROUPS["swing"]
                      + tuple(f for f in GROUPS["calendar"]
                              if f != "day_of_week")),
    #: The third wave: everything whose thesis is a PAIRING of two readings
    #: rather than a reading. Named as one selection because that is the
    #: question -- does combining help at all -- and because a combination sweep
    #: is the one most likely to be run against `why` on its own.
    "combined": tuple(GROUPS["gate"] + GROUPS["combo"]),
    #: The fourth wave, by provenance: everything added when the study was asked
    #: for daily strategies and overnight positions. Four groups, so the same
    #: reason `combined` exists applies -- "do the new ideas work" is one
    #: question and it should be one sweep with one stateable budget.
    "fourth": tuple(GROUPS["night"] + GROUPS["almanac"] + GROUPS["horizon"]
                    + GROUPS["crossasset"]),
    #: EVERYTHING THAT SURVIVES A SESSION CLOSE, old and new. The selection the
    #: original question was really asking for: sixty-three of the first
    #: seventy-five are flattened at the close, so "which of these is a daily
    #: strategy" was previously answerable only by reading the table. Computed
    #: from `Family.hold` rather than listed, so a family that changes regime
    #: cannot drift out of it.
    "held": tuple(name for name, entry in FAMILIES.items()
                  if entry.hold in ("swing", "overnight")),
    #: The fifth wave: the months-scale entries the fourth wave was missing,
    #: plus the first factor in the study that is not a price. Named separately
    #: from `fourth` because they were added AFTER financing started being
    #: charged, so a `fourth` result and a `fifth` result were produced under
    #: different cost models and must not be pooled without saying so.
    "fifth": ("long_channel", "multi_horizon_trend", "long_reversal",
              "momentum_crash_filter", "vix_regime", "vol_risk_premium",
              "swap_side_bias"),
    #: The sixth wave, by provenance: twenty-seven families built on the reading
    #: of what the first five actually produced. Named as one selection for the
    #: reason `combined` and `fourth` are -- "do the new ideas work" is one
    #: question and it should be one sweep with one stateable budget -- and
    #: because this is the wave most in need of `why`. It was designed AFTER
    #: seeing which families passed, so its priors are conditioned on the same
    #: holdout every earlier wave was scored against
    #: ([[exness-survivor-pool-is-oos-conditioned]]), and only its coin-flip
    #: null and a window neither it nor its parents were chosen on can price it.
    "sixth": tuple(GROUPS["pathstat"] + GROUPS["micro"] + GROUPS["filter"]
                   + GROUPS["adaptive"] + GROUPS["fusion"]),
}


def expand_families(argument, groups=None):
    """`--families` and `--groups` resolved to a set of names, or `None`.

    `None` means "every family", which is what both flags default to. Unknown
    names raise rather than being dropped: a typo that silently narrows a sweep
    to nothing is indistinguishable from a study that found nothing.
    """
    # Whitespace is stripped before splitting, so a name that arrived through
    # a file or a shell pipe on Windows is not rejected for carrying a trailing
    # carriage return. That cost a `why` launch: every one of twenty-nine
    # commands died with "unknown family" on a name that was spelled correctly,
    # and the whole run finished in fourteen seconds looking complete.
    def tokens(argument):
        return [t for t in "".join((argument or "").split()).split(",") if t]

    wanted = set()
    for token in tokens(groups):
        if not token:
            continue
        if token in ALIASES:
            wanted.update(ALIASES[token])
            continue
        if token not in GROUPS:
            raise SystemExit(f"unknown group {token!r}; have "
                             f"{sorted(GROUPS)} or {sorted(ALIASES)}")
        wanted.update(GROUPS[token])
    for token in tokens(argument):
        if token not in FAMILIES:
            raise SystemExit(f"unknown family {token!r}")
        wanted.add(token)
    return wanted or None


def family_hold(family):
    """Which of the three holding regimes a family runs in.

    `session`   flattened at the close, at most one entry a day. What every
                family did before the swing regime existed, and what sixty-three
                of the seventy-five still do.
    `swing`     holds through the close and exits only on its stop, target,
                trail or DAY count.
    `overnight` enters on the last bar from which a fill can still reach the
                close and exits at an OPEN, one to three sessions later. The
                exposure is the gap, so the holding period is counted in nights
                and the stop is checked against the out-of-session extreme --
                see `backtest`, which is the only place the difference lives.
    """
    entry = FAMILIES.get(family)
    return entry.hold if entry else "session"


SIGNALS = {name: entry.signal for name, entry in FAMILIES.items()}


def axes(symbol, bar=None, only=None):
    """The parameter grid for every family that means something here.

    A family is dropped rather than degraded when its scope does not fit the
    timeframe, and `last_entry_minute` collapses to a single inert value on daily
    bars so the shared signals keep working without carrying a dead axis that
    would multiply the grid for nothing.

    `needs` is the same idea applied to data instead of to the clock: a
    relative-strength family without a benchmark would emit no signal on every
    cell, and `select` would print that as "no cell passed" -- which reads like
    a result about the strategy and is a fact about the inputs.

    `only` restricts the grid to a set of names, from `--families`/`--groups`.
    It is applied LAST so a requested family that this symbol or timeframe
    genuinely cannot run still disappears, rather than being forced in.
    """
    p = periods(symbol, bar)
    daily = is_daily(bar)
    opened, closed = INSTRUMENTS[symbol]["session"]
    if daily:
        last, signal, entry = (DAILY,), (0,), (0,)
    else:
        last = tuple(sorted({closed - 120, closed - 60}))
        signal = tuple(m for m in (opened + 60, opened + 150) if m <= closed - 60)
        step = max(60, (closed - opened) // 3)
        entry = tuple(range(opened, closed - 30, step)) or (opened,)
    common = {"exit_mode": EXIT_MODES, "stop_day": STOP_DAY,
              "trend": ("none", "ema_20d", "ema_50d"),
              "vol_mode": ("none", "calm")}
    swing = {"exit_mode": SWING_EXIT_MODES, "stop_day": STOP_DAY,
             "trend": ("none", "ema_20d", "ema_50d"), "vol_mode": ("none",)}
    # THE NIGHT GRID WITHHOLDS THE CALM FILTER ON PURPOSE. `vol_mode` is a
    # boolean on the same short-against-long volatility comparison `night_vol`
    # reads as its thesis, so leaving it in would apply that reading twice --
    # once as an undeclared filter and once as the hypothesis -- and no result
    # in the group could be attributed to either. `stop_day` stays, and note
    # what it does here: at `nights_1` the stop cannot trigger during the night
    # itself unless the out-of-session extreme breaches it, so for most cells it
    # is a POSITION SIZE and not a stop.
    #
    # AND THE ROBUSTNESS TEST IS NEARLY VACUOUS HERE, WHICH IS NOT FIXABLE BY
    # CHOOSING BETTER AXES. `neighbours` steps over every name in `CATEGORICAL`,
    # and for `night_drift` that is `side`, `weekday`, `exit_mode`, `trend` and
    # `vol_mode` -- all of them genuinely labels, since Tuesday is not one step
    # from Monday. `stop_day` is the only scale left, so a winner at its edge
    # value reports `1/1` robust neighbours and has really been asked one
    # question. Read a night result's `robust_neighbours` before its return, and
    # treat the group as one that `why` has to price rather than one the
    # neighbour gate has already filtered.
    night = {"exit_mode": NIGHT_EXIT_MODES, "stop_day": STOP_DAY,
             "trend": ("none", "ema_20d", "ema_50d"), "vol_mode": ("none",)}
    position = {"exit_mode": POSITION_EXIT_MODES, "stop_day": STOP_DAY,
                "trend": ("none", "ema_20d", "ema_50d"), "vol_mode": ("none",)}
    # THE LIVE GRID KEEPS THE COMMON EXITS AND ADDS THE CLOCK ONES, except for
    # `clock_momentum`, which takes `live_clock` and is the only family in the
    # study with no level exit at all. The distinction is the experiment: five
    # of the six ask whether the BOOK carries information, and one asks whether
    # the EXIT MECHANISM was the thing costing the money.
    #
    # `trend` is dropped to `none` throughout. These rules already carry a
    # direction axis and a regime reading of their own, and stacking the study's
    # generic EMA filter on top would mean a winning cell could be the filter
    # rather than the thesis -- the confound `night` documents at length.
    live = {"exit_mode": EXIT_MODES, "stop_day": STOP_DAY,
            "trend": ("none",), "vol_mode": ("none",)}
    live_clock = {"exit_mode": LIVE_EXIT_MODES, "stop_day": STOP_DAY,
                  "trend": ("none",), "vol_mode": ("none",)}
    # `gates` and `fades` are the only axes in the study whose VALUES depend on
    # the symbol and the timeframe rather than on periods. A gate that cannot be
    # read here -- session VWAP on a daily bar, a benchmark this symbol does not
    # have -- is dropped from the axis rather than left in to return `None`
    # forever, because a dead axis value is not a negative result about the gate:
    # it is budget spent on a cell that could never fire, and a neighbour the
    # robustness test counts as a failure for the wrong reason.
    grid = {"last": last, "signal": signal, "entry": entry,
            "common": common, "swing": swing, "night": night,
            "position": position, "live": live, "live_clock": live_clock,
            "gates": gate_choices(symbol, daily), "fades": fade_choices(daily)}

    out = {}
    for name, family in FAMILIES.items():
        if only is not None and name not in only:
            continue
        if family.scope == "intraday" and daily:
            continue
        if family.scope == "daily" and not daily:
            continue
        # The overnight regime needs a session to end and a bar fine enough that
        # the last fill before it is still the evening. `NIGHT_BAR_MAX` says
        # where that stops being true; above it the same rule would be buying
        # the afternoon and reporting the result as a gap study.
        if family.scope == "night" and (daily or (bar or BAR_MINUTES) > NIGHT_BAR_MAX):
            continue
        if family.symbols and symbol not in family.symbols:
            continue
        if family.needs == "benchmark" and symbol not in BENCHMARK:
            continue
        # VIX is the US equity risk premium, not a universal fear index. Against
        # XAUAUD it is a loosely correlated macro series, and a family reading it
        # there would be testing a different and much weaker claim under the
        # same name.
        if family.needs == "vix" and (symbol not in VIX_FACTOR or not have_vix()):
            continue
        # A cost-aware family without the broker's minute table would read NaN
        # on every bar and fire never, which `select` would print as "no cell
        # passed" -- a sentence about the market that would be a fact about the
        # inputs. `resolve` already refuses such a symbol outright, so this is
        # the second lock rather than the first.
        if family.needs == "spread" and not has_broker_minutes(symbol):
            continue
        spec = family.build(p, grid)
        if any(len(values) == 0 for values in spec.values()):
            continue
        out[name] = spec
    return out


def exit_plan_for(mode, stop_distance, per_session):
    """`(target_distance, max_bars, trail_multiple)`, with day counts resolved.

    `days_N` is converted to bars here rather than in the axes, so "hold fifteen
    days" is fifteen days at every timeframe instead of fifteen bars that mean
    something different at each one.

    `nights_N` RESOLVES TO NOTHING, and that is not an oversight. An overnight
    position has no target, no trail and no bar count: it leaves at an open,
    which is an event in the session calendar rather than a price or a distance,
    so `backtest` carries it separately. Guarding the prefix here is what stops
    `exit_plan` falling through to its trailing-stop branch and reading
    `"nights_1"[6:]` as a 1.0 multiple -- which is a valid float, a plausible
    number, and completely wrong.
    """
    if mode.startswith("nights_"):
        return None, None, None
    if mode.startswith("days_"):
        return None, int(mode[5:]) * per_session, None
    return exit_plan(mode, stop_distance)


def nights_for(mode):
    """How many opens an overnight cell sleeps through, or `None`."""
    return int(mode[7:]) if mode.startswith("nights_") else None


def candidates(spec):
    cells = [dict(zip(spec, values))
             for values in itertools.product(*spec.values())]
    return [cell for cell in cells if valid(cell)]


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #

def quantity(equity, price, stop_distance, realized, ctx):
    """MT5 lots for a `RISK_FRACTION` stop risk, volatility-throttled.

    Money per point is `multiplier` alone -- already USD, see `read_specs` --
    so a cross whose profit currency is not USD is sized in the same units as
    its P&L. `fx_to_usd` belongs on `margin_lot` below, where `price` really is
    quoted in the profit currency, and nowhere else. Returns 0.0 when
    the request lands under the broker's `volume_min`, which is a refusal rather
    than a rounding: that is what `fill_rate` measures, and on a small account it
    is the binding constraint rather than the edge.
    """
    spec = ctx["cfg"]
    money_per_point = spec["multiplier"]
    if money_per_point <= 0 or stop_distance <= 0:
        return 0.0
    risk = equity * RISK_FRACTION
    if realized and realized > 0:
        risk *= min(1.0, ctx["vol_target"] / realized)
    raw = risk / (stop_distance * money_per_point)
    margin_lot = price * spec["contract_size"] * spec["fx_to_usd"] * MARGIN_FRACTION
    ceiling = min(spec["volume_max"], equity / margin_lot) if margin_lot > 0 else 0.0
    step = spec["volume_step"]
    lots = math.floor(min(raw, ceiling) / step + 1e-10) * step
    return round(lots, 8) if lots + 1e-10 >= spec["volume_min"] else 0.0


#: Bars between a signal being READ and the entry it produces being fillable.
#:
#: ZERO IS AN ASSUMPTION, NOT A NEUTRAL DEFAULT. Every sealed result in this
#: repository was measured at 0, which says bar `i` is in hand the instant it
#: closes and its entry goes on at bar `i+1`'s open. Live that is false: the
#: canon sleeves decide on VENDOR tables and Dukascopy's own publish lag is
#: about 1.3 minutes, so a 1m sleeve is still waiting for bar `i` when bar
#: `i+1` opens and cannot possibly trade it.
#:
#: `exness_latency` measures the real per-feed lag and re-runs the book across
#: this setting. Left at 0 the loop is byte-for-byte what it always was.
#:
#: SETTABLE FROM THE ENVIRONMENT because the book is re-run one SUBPROCESS per
#: lag. `exness_combined_strategies` memoises every sleeve's trades in process
#: and caches the imported ones on disk, and neither key mentions the lag; a
#: fresh process is the only way to be sure a run at lag 2 is not being served
#: lag 0's answer. Unset, this is 0 and nothing changes.
DECISION_LAG_BARS = int(os.environ.get("EXNESS_DECISION_LAG_BARS", "0"))

#: `{symbol: {bar_ts: spread_bp}}`, consulted at ENTRY instead of the symbol's
#: one constant. Filled by `install_live_fills` from the broker's own minute
#: bars, which every command here does before it scores anything.
#:
#: WHY A CONSTANT WAS EVER ACCEPTABLE, AND WHY IT IS NOT. A constant charges one
#: median spread per symbol, sampled over seven days of terminal history, at
#: every hour of every day a family trades. That is fine if a family's entries
#: are spread evenly through the session and pathological if they are not -- a
#: breakout family that fires on the open pays the open's spread, which is not
#: the median of the day. The constant cannot see the difference; this can.
TICK_SPREAD_BP = {}

#: `{symbol: {bar_ts: price}}` -- what the account actually got in, and out.
#:
#: Same fall-back rule as the spread map and the same reason: a bar the broker's
#: minute table does not reach keeps the bar's own open rather than being
#: skipped or zeroed. Filled by `install_live_fills`.
ENTRY_PRICE = {}
EXIT_PRICE = {}

#: Everything between the runtime deciding and MT5 executing, in seconds.
#:
#: NOT MEASURED -- ADDED UP FROM THE CODE THAT DOES IT: 0.25s for the Rust
#: runtime's 500ms store poll, 0.13s for `mt5/bridge.py`'s 0.25s command poll,
#: and about 0.6s for two HTTP round trips plus `order_send` itself. Small next
#: to every feed lag, and included anyway, because leaving it out is a claim
#: that the queue is free. `exness_live_execution.BRIDGE_QUEUE_SECONDS` is the
#: same number and the long-form derivation lives there.
BRIDGE_QUEUE_SECONDS = 1.0

#: Refuse an entry whose own minute's spread sits above this percentile of its
#: recent range, or `None` to take every entry. An OVERLAY on any family, set
#: by the study that asks whether skipping the dear bars helps a rule that
#: already has an edge -- see `backtest`.
ENTRY_MAX_SPREAD_RANK = None


def broker_minute_table(symbol):
    """The store table holding the broker's own one-minute bars for `symbol`."""
    return f"exness_{broker_symbol(symbol).lower()}_1m"


def has_broker_minutes(symbol):
    """Whether this symbol can be priced the way the account fills it."""
    return broker_minute_table(symbol) in tables()


def feed_lag_seconds(symbol):
    """Seconds between a bar CLOSING and the runtime being able to act on it.

    Per vendor, never one number for all: the deciding table is Dukascopy for
    the FX and index names, Binance for the crypto, Exness's own feed for
    `xalusd`. `exness_latency` measures them and is the single place they are
    written down; a symbol it does not name is on the Dukascopy path, which is
    where every unlisted name in this universe actually sits.
    """
    from sandbox.research import exness_latency as lat  # noqa: PLC0415

    return lat.MEASURED_LAG_SECONDS[lat.FEED.get(symbol, "dukascopy")]


def live_fills(symbol, bar=None):
    """`(spread_bp, entry, exit)` by bar stamp -- the account's own fill path.

    THE THREE THINGS A BACKTEST FILL GETS WRONG, MEASURED RATHER THAN ASSUMED.
    Built with `exness_live_execution`'s readers off `exness_<broker>_1m`, which
    is the broker's own minute series and carries its own per-minute spread:

        spread   the quote in the minute the entry happened. MT5's M1 `spread`
                 column IS the median of the ticks inside that minute -- ratio
                 1.00x and correlation 0.956-1.000 over 105,000 matched minutes
                 -- so nothing is given up by reading it instead of the tick
                 tables, while the minute bars cover 2020-2026 and the tick
                 tables start 2026-01 with daily holes.
        entry    the bar's own open moved by the RATIO the broker's price
                 travelled over `feed_lag_seconds` plus `BRIDGE_QUEUE_SECONDS`.
                 A ratio and never the broker's absolute price: the family
                 DECIDES on a vendor table and is FILLED at Exness, and on
                 `ukoil` those are Dukascopy Brent against Exness UKOIL. Pasting
                 the broker's level in put the entry at one price level while
                 the stop and target stayed at another and read one sleeve at
                 +69.6% against a sealed +28.6%. The ratio cancels the level and
                 leaves the delay, which is the one thing being charged.
        exit     the same ratio at a longer delay: one WHOLE BAR plus that lag
                 and queue after the bar the exit fired on. There is no
                 broker-side stop on this account -- the MT5 payload has nowhere
                 to put one -- so the stop is a level the runtime watches and
                 can act on only when its candle rolls, and the order then
                 travels the same path an entry does.

    KEYED ON THE RUN'S OWN BAR SIZE, because the exit delay IS a bar. At 30m the
    exit lands half an hour after the level was breached; on `--bar-minutes
    1440` it lands the next day. A map built at one size is wrong at every
    other, so the cache key carries it.

    ON THE FULL SERIES, not the phase's. The map is a price lookup and not a
    result, so building it once at `validate` scope lets `select` read the same
    file rather than reducing two and a half million minute bars twice.

    Reduced once and cached on disk -- reading one symbol's minute table is
    about eight seconds, and fifteen spawned workers would otherwise each pay
    it.
    """
    bar = BAR_MINUTES if bar is None else bar
    spec = INSTRUMENTS[symbol]
    table = broker_minute_table(symbol)
    lag = feed_lag_seconds(symbol)
    # v2: v1 read a shifted market's broker table six hours away
    #      (`exness_live_execution.clock_offset`), so every jp225, hk50
    #      and aus200 map in the cache is wrong and must not be reused.
    key = (f"livefills:v2:{table}:{spec['table']}:{bar}m:{spec['warmup']}:"
           f"{spec['shift_hours']}:{lag}:{BRIDGE_QUEUE_SECONDS}:"
           f"{data._table_fingerprint([table, spec['table']])}")

    def build():
        from sandbox.research import exness_live_execution as lx  # noqa: PLC0415

        bars = all_bars(symbol, "validate", bar)
        stamps = [row[TS] for row in bars]
        opens = {row[TS]: row[O] for row in bars}
        spreads = lx.spread_by_bar_1m(symbol, stamps)
        entries = lx.price_by_bar_1m(symbol, opens, lag + BRIDGE_QUEUE_SECONDS)
        exits = lx.price_by_bar_1m(
            symbol, opens,
            lx.exit_delay_seconds(bars, lag, BRIDGE_QUEUE_SECONDS))
        lx.release_minutes()
        return spreads, entries, exits

    return data._cached(f"livefills_{symbol}_{label_bar(bar)}", key, build)


def install_live_fills(symbol, bar=None, quiet=False):
    """Put `symbol`'s fill maps where `backtest` reads them.

    CALLED BY EVERY COMMAND THAT SCORES SOMETHING, in the parent and again in
    each spawned worker -- the worker hits the same disk cache, so it costs a
    read rather than a reduction.

    The coverage line is printed rather than counted silently: a bar the broker
    table does not reach keeps the vendor open and the constant spread, so a
    figure below 100% means part of the window is still on the idealised fill
    and the study is that much kinder there than the account.

    COUNTED FROM THE FIRST SELECTION YEAR, NOT FROM THE FIRST BAR. The series
    carries warm-up years the study never trades in -- on `usdjpy` that is 2011
    onward against a broker table starting 2020 -- and counting those reported
    57% for a symbol whose holdout trades are 100% priced. The window that can
    contain a trade is the only one this question is about
    ([[trade-coverage-not-bar-coverage-decides-live-validity]]).
    """
    spreads, entries, exits = live_fills(symbol, bar)
    TICK_SPREAD_BP[symbol] = spreads
    ENTRY_PRICE[symbol] = entries
    EXIT_PRICE[symbol] = exits
    # THE SELECTION WINDOW CANNOT START BEFORE THE COST DOES, and until this
    # was enforced the gate was asking the impossible. Every fill in this study
    # is now priced from `exness_<broker>_1m`, which starts 2020 on FX and
    # 2022-07-31 on the index CFDs -- while `first_full_year` was still 2018.
    # `passes` then required a cell to be profitable in six of seven years, four
    # of which HAVE NO MEASURED COST AT ALL and were scored on the idealised
    # fill. Cells were being refused on consistency across a seam in the cost
    # model rather than on their own record: sixteen cells at profit factor 1.2
    # or better over a hundred-plus trades, every one refused by
    # `positive_years`, on eight symbols.
    #
    # A YEAR COUNTS ONLY IF THE COST DATA STARTS BEFORE IT DOES, which is the
    # rule `coverage` already applies to the price table -- so a table opening
    # 2022-07-31 yields 2023 and a partial 2022 is warm-up, exactly as a partial
    # first price year is. Raised and never lowered: a symbol whose cost history
    # is longer than its price history keeps the price bound.
    if entries:
        first = datetime.fromtimestamp(min(entries), tz=timezone.utc)
        year = first.year + (0 if (first.month, first.day) <= (1, 7) else 1)
        spec = INSTRUMENTS[symbol]
        if year > spec["first_full_year"]:
            spec["first_full_year"] = year
    if not quiet:
        first = is_start(symbol)
        total = sum(1 for row in all_bars(symbol, "validate",
                                          BAR_MINUTES if bar is None else bar)
                    if row[TS] >= first)
        priced = sum(1 for ts in entries if ts >= first)
        share = 100.0 * priced / total if total else 0.0
        print(f"{symbol} {label_bar(bar)}: live fills from "
              f"{broker_minute_table(symbol)} -- {priced:,} of {total:,} "
              f"scoreable bars priced ({share:.0f}%), feed lag "
              f"{feed_lag_seconds(symbol):g}s + {BRIDGE_QUEUE_SECONDS:g}s "
              f"queue, exit one bar later", flush=True)
        if share < 99.0:
            print(f"  {100 - share:.0f}% of the window predates "
                  f"{broker_minute_table(symbol)} and keeps the vendor open "
                  f"and the constant spread -- that part is still the "
                  f"idealised fill", flush=True)
    return spreads, entries, exits


def backtest(family, bars, ctx, params, lo=None, hi=IS_END,
             initial=INITIAL_BALANCE, spread_bp=None, null_seed=None,
             include_trades=False, fill_bars=None, decision_lag=None,
             tick_spreads=None, entry_prices=None, exit_prices=None,
             broker_stops=False):
    """Signals to sized trades over `[lo, hi)`.

    TWO FEEDS, ONE CLOCK. `fill_bars` splits the series that DECIDES from the
    series that FILLS. Omitted, it is the ordinary single-feed backtest and
    every sealed result reproduces byte for byte. Supplied, it must be a list
    the same length as `bars` and on the same timestamps
    (`exness_broker_fills.aligned_fills` builds one), and then every price the
    loop pays or receives -- the entry open, the stop and target tests and
    their fills, the session and time-stop opens, the trailing close -- is read
    from it, while the signal, the context, the volatility and the stop
    DISTANCE stay on `bars`.

    That split is the whole point: the canon book signals off vendor tables
    (Dukascopy, Binance, Databento) and is executed at Exness, and those are
    not the same price series. Running the identical decisions against the
    broker's own bars measures what that difference costs instead of assuming
    it away.

    Ordering matches the Rust engine and every earlier pass: session flatten
    first, then stop, then target, then the time stop. A signal read on bar `i`
    fills at bar `i+1`'s open, enforced through `pending` -- consuming the same
    bar's close manufactures the fake edge recorded in
    [[entry-must-be-next-bar-open]]. At most one position at a time and one new
    entry per day.

    `decision_lag` pushes that fill further out, to bar `i+1+lag`'s open, which
    is what a feed that publishes late actually does to a sleeve. It defaults to
    `DECISION_LAG_BARS` (0, the sealed behaviour). The signal is still READ on
    bar `i` -- a late feed delays when a decision can be acted on, it does not
    change what the decision was -- and a pending entry still blocks a newer
    signal, because during the lag the strategy has not seen the newer bar
    either.

THREE HOLDING REGIMES. A `session` family is flattened at the close, as every
    family here always was. A `swing` family is not: it survives the close and
    exits only on its stop, target, trail or day count. That is a real difference
    in what is being tested rather than a setting -- a session family can capture
    at most one day of a move however right it is, so comparing `donchian` with
    `swing_donchian` reads whether an edge lives in the entry or in the holding.

    An `overnight` family holds NOTHING BUT the close-to-open leg. It enters at
    the open of the session's last fillable bar and leaves at the open one, two
    or three sessions later, counted in `session_index` so a Friday entry sleeps
    through the weekend as ONE night rather than three.

    THE NIGHT IS INVISIBLE TO THIS LOOP AND THE STOP IS CHECKED ANYWAY. `context`
    filters the bar list down to in-session rows, so between one close and the
    next open there are no bars for the stop test to see -- an overnight stop
    would be inert, and a regime whose entire exposure is unstopped would report
    gap risk as free. So at the first bar of every session an overnight position
    slept into, the stop is tested against `ctx["overnight"]`, the high and low
    of the out-of-session buckets, using the same
    `min(open, stop)`/`max(open, stop)` fill convention the intrabar test uses.
    That can only ever make a result worse.

    THE ASYMMETRY WITH `swing` IS DELIBERATE. Swing families have the same
    blindness and do NOT get this check, because they hold for days and their
    stop is live through every session in between -- and changing them would
    move every published swing result for the sake of a leg that is a small part
    of their exposure. For an overnight family it is the whole of it.

    On daily bars there is no session to flatten against, so every family holds
    by construction and the clock tests are skipped.

    RISK IS DAY-ANCHORED. `stop_day` is a fraction of the average true daily
    range, not a multiple of bar ATR, so the same cell means the same risk at
    every timeframe ([[bar-size-confound-is-stop-distance]]).

    THE FILL IS THE LIVE ONE UNLESS A CALLER SAYS OTHERWISE. `tick_spreads`,
    `entry_prices` and `exit_prices` each fall back to the module-level map
    `install_live_fills` filled for this symbol, so the spread charged is the
    broker's quote in the entry minute, the entry is the open moved by the
    feed's publish lag and the bridge queue, and the exit is a market order one
    whole bar later. Pass a map to override one, or `{}` to ask for the
    idealised bar fill back -- which is what `validate`'s cost sweep does, since
    a swept constant cannot move a bar whose real spread is already known.
    """
    spec = ctx["cfg"]
    symbol = ctx["symbol"]
    daily = ctx["daily"]
    hold = family_hold(family)
    # SESSION_ONLY collapses the three holding regimes into one: enter and exit
    # inside a single RTH, every family, every symbol. Set by operator
    # instruction 2026-08-29 -- "one day, one rth".
    #
    # THIS CHANGES WHAT THE SWING AND OVERNIGHT FAMILIES ARE, IT DOES NOT TUNE
    # THEM. `swing_donchian` exists to test whether an edge lives in the entry
    # or in the HOLDING, and an overnight family's entire exposure is the
    # close-to-open leg -- flattened at the close it has no position at all.
    # Their sealed parameters were selected while they could hold, so every one
    # of them is now an unselected cell and has to be re-tested before it is
    # trusted. On daily bars there is no session to flatten against, so the
    # switch cannot apply there and does not pretend to.
    overnight = not daily and hold == "overnight" and not SESSION_ONLY
    swing = daily or (hold in ("swing", "overnight") and not SESSION_ONLY)
    per_session = ctx["periods"]["session"]
    lo = is_start(symbol) if lo is None else lo
    opened, closed = spec["session"]
    money_per_point = spec["multiplier"]
    # Hoisted like every other per-cell lookup. Both sides are resolved up front
    # because a family with a `side` axis takes both across the grid.
    financing = (financing_price(symbol, 1), financing_price(symbol, -1))
    equity = peak = initial
    maximum_dd = 0.0
    trades = []
    position = pending = None
    traded_day = None
    state = {}
    signals = fills = 0

    # Hoisted out of the loop. Each of these was a dict lookup per bar per cell,
    # and the profiler put 60% of a run inside this loop rather than in the
    # signals it calls, so the lookups themselves were the cost.
    atr_of = ctx["atr"]
    risk_of = ctx["risk"]
    realized_of = ctx["volatility"]
    calm_of = ctx["calm"]
    day_of = ctx["day"]
    minute_of = ctx["minute"]
    signal_fn = SIGNALS[family]
    lag = DECISION_LAG_BARS if decision_lag is None else int(decision_lag)
    if lag < 0:
        raise ValueError(f"decision_lag must not be negative: {lag}")
    # `None` unless a tick table has been loaded for this symbol, and then the
    # single lookup the entry block does. A bar with no tick coverage falls back
    # to the constant rather than to zero: the tick history starts 2026-01-01
    # and most of the study window is before it, so a missing quote is the
    # normal case and must not read as a free trade.
    by_ts = (tick_spreads if tick_spreads is not None
             else TICK_SPREAD_BP.get(symbol))
    spread_at = None if not by_ts else by_ts.get
    # AN OVERLAY, NOT A SIGNAL, and the distinction is the whole experiment.
    # Five families that read the spread as EVIDENCE were tested on twelve
    # symbols and two timeframes and none beat chance: 30/60 holdout wins at
    # 30m, and on daily not one could clear eight of twelve even in sample. So
    # the question changes from "does the spread predict returns" -- answered,
    # no -- to "does refusing the dear bars improve a rule that already works".
    # `ENTRY_MAX_SPREAD_RANK` refuses an entry whose own minute sits above that
    # percentile of its recent range, and leaves everything else alone. `None`
    # is every sealed result.
    rank_of = ctx.get("spread_rank") if ENTRY_MAX_SPREAD_RANK is not None else None
    # Same fall-back rule and the same reason: a bar the broker's minute table
    # does not reach keeps the bar's own open rather than being skipped or
    # zeroed. `ENTRY_PRICE` is what `install_live_fills` puts there, so every
    # command in this module fills the way the account does without threading
    # three maps through every call site; a caller that passes its own -- the
    # combined book does -- overrides it, and `{}` asks for the bar open back.
    by_entry = (entry_prices if entry_prices is not None
                else ENTRY_PRICE.get(symbol))
    entry_at = None if not by_entry else by_entry.get
    # THE EXIT IS A MARKET ORDER LIVE AND THIS IS THE ONLY PLACE THAT SHOWS IT.
    # There is no broker-side stop or target on this account: the MT5 order
    # payload carries no SL and no TP (`live_trade/src/live/mt5/bridge.rs` sends
    # `ORDER|id|action|symbol|volume|magic|deviation|ticket|created_at`), so a
    # stop is a price the RUNTIME watches and then acts on. It can only act
    # once the bar it is watching has COMPLETED, and the order then travels the
    # same publish lag and bridge queue an entry does.
    #
    # This loop, like the live runtime's own `family.rs`, books the idealised
    # `min(open, stop)`. That price is real in neither place -- it is what the
    # bar says, not what the account received. `exit_prices` says instead "the
    # market was HERE when the close actually landed", keyed by the bar the
    # exit fired on, and replaces the idealised fill wherever the tick tables
    # reach that moment. Absent -- and it is absent for every sealed result --
    # nothing moves.
    by_exit = (exit_prices if exit_prices is not None
               else EXIT_PRICE.get(symbol))
    exit_at = None if not by_exit else by_exit.get
    # WHAT AN `sl`/`tp` ON THE ORDER WOULD BUY, and nothing else.
    #
    # `exit_prices` charges every exit as a late market order because that is
    # what this account does today -- the MT5 payload carries no stop, so the
    # runtime holds the level and can only act when its candle rolls. Set a
    # broker-side stop and that stops being true for the two exits that ARE
    # price levels: the broker triggers them intrabar, at the level, with no
    # reference to any bar clock or feed lag. Which is exactly the fill this
    # loop already books.
    #
    # So `broker_stops` exempts `stop` and `target` from the late fill and
    # leaves everything else on it. `session`, `time` and `night` are NOT price
    # levels -- they are the runtime deciding on a clock -- so they stay market
    # orders and stay late, which is why this is a partial recovery and never a
    # return to the sealed number.
    #
    # A TRAILING EXIT COUNTS AS A STOP AND THAT IS DELIBERATE. The trail is
    # recomputed at each bar's close and would live at the broker as a modify
    # on the position, so intrabar it sits at the level the last close set --
    # which is the level this loop tests against.
    broker_exits = frozenset(("stop", "target")) if broker_stops else frozenset()
    exit_mode = params["exit_mode"]
    nights = nights_for(exit_mode) if overnight else None
    # The regime is defined by the exit mode as much as by the declaration, so a
    # cell without one falls back to plain `swing` rather than comparing a
    # session count against `None` five thousand bars in.
    overnight = overnight and nights is not None
    session_of = ctx["session_index"]
    outside = ctx["overnight"]
    stop_day = params["stop_day"]
    # `accepts_vol` and `accepts_trend` resolved once instead of 1.4M times.
    vol_mode = params["vol_mode"]
    want_calm = None if vol_mode == "none" else (vol_mode == "calm")
    trend_mode = params["trend"]
    trend_of = (None if trend_mode == "none"
                else ctx["ema"][ctx["periods"]["trend"][trend_mode]])

    # Start at the first in-window bar instead of walking the warm-up years and
    # discarding them one `continue` at a time.
    # Against the PRECOMPUTED timestamp list. Building `[b[TS] for b in bars]`
    # here instead cost a fresh 37k-element list on every one of a job's 29,736
    # calls -- about a billion element copies to avoid a `continue` that was
    # nearly free, and it made the whole job 5% slower rather than faster.
    start = bisect.bisect_left(ctx["ts"], lo) if bars else 0
    if fill_bars is not None and len(fill_bars) != len(bars):
        raise ValueError("fill_bars must be aligned one-to-one with bars "
                         f"({len(fill_bars)} against {len(bars)})")
    for index in range(start, len(bars)):
        bar = bars[index]
        # The execution feed. `bar` decides, `fbar` pays -- the same object when
        # no alternate feed was handed in, so the single-feed path is untouched.
        fbar = bar if fill_bars is None else fill_bars[index]
        ts = bar[TS]
        if ts >= hi:
            break
        day, minute = day_of[index], minute_of[index]
        tradeable = daily or opened <= minute < closed

        if position is not None:
            side, price, reason = position["side"], None, None
            # FIRES ON THE LAST IN-SESSION BAR, NOT ON A FIXED MINUTE. The old
            # test was `minute >= closed`, which needs a bar to actually PRINT
            # at or after the configured close. On xalusd and xniusd the 30m
            # series stops at 13:30 while `INSTRUMENTS` closes them at 14:00, so
            # that bar existed on 34 of 411 days -- and on the other 92% the
            # flatten never fired at all. A session family then ran on its
            # trailing stop instead, holding a median of 20 hours and a maximum
            # of 35 days: 104 of xalusd:gated_fade's 120 exits were `stop` and
            # only 16 were `session`.
            #
            # Where the close bar does print -- uk100 99.8% of days, usdjpy
            # 99.8%, ukoil 97.4% -- the last in-session bar IS the bar at
            # `closed`, so those results are unchanged by construction. This can
            # only add flattens that were meant to happen, never move one.
            last_of_day = (index + 1 >= len(bars)
                           or day_of[index + 1] != day)
            if not swing and (minute >= closed or last_of_day):
                price, reason = fbar[O], "session"
            else:
                stop = position["stop"]
                if overnight and session_of[index] != position["seen"]:
                    # First bar of a session this position slept into. The night
                    # it just came through has no rows in `bars`, so its extreme
                    # is read from the out-of-session buckets or the stop never
                    # gets a chance to fire at all.
                    position["seen"] = session_of[index]
                    window = outside.get(day)
                    breached = window is not None and (
                        window[1] <= stop if side == 1 else window[0] >= stop)
                    if breached:
                        price = min(fbar[O], stop) if side == 1 else max(fbar[O], stop)
                        reason = "stop"
                    elif position["seen"] - position["session"] >= position["nights"]:
                        price, reason = fbar[O], "night"
                if price is None and (
                        (side == 1 and fbar[L] <= stop)
                        or (side == -1 and fbar[H] >= stop)):
                    price = min(fbar[O], stop) if side == 1 else max(fbar[O], stop)
                    reason = "stop"
                elif price is None and position["target"] is not None:
                    target = position["target"]
                    if (side == 1 and fbar[H] >= target) or (side == -1 and fbar[L] <= target):
                        price = max(fbar[O], target) if side == 1 else min(fbar[O], target)
                        reason = "target"
                if (price is None and position["max_bars"] is not None
                        and index - position["index"] >= position["max_bars"]):
                    price, reason = fbar[O], "time"
            trail_risk = risk_of[index]
            if (price is None and position["trail"] is not None
                    and trail_risk and present(trail_risk)):
                # Trailed on the daily range for the same reason the stop is:
                # a trail in bar-ATR would tighten every time the bar got finer.
                risk = trail_risk
                if side == 1:
                    position["best"] = max(position["best"], fbar[C])
                    position["stop"] = max(position["stop"],
                                           position["best"] - position["trail"] * risk)
                else:
                    position["best"] = min(position["best"], fbar[C])
                    position["stop"] = min(position["stop"],
                                           position["best"] + position["trail"] * risk)
            elif price is not None:
                # Applied to every reason the account sends as a market order.
                # With `broker_stops` the price levels are held at the broker
                # and keep the idealised fill; the clock-driven exits do not.
                if exit_at is not None and reason not in broker_exits:
                    price = exit_at(ts, price)
                gross = side * (price - position["entry"])
                # Two legs now: the spread, once, and financing for every
                # calendar night the position was actually open. A session
                # family spans no date boundary and so pays nothing here, which
                # is why every result sealed before this change is unaffected.
                nights = financing_nights(position["ts"], ts)
                carried = nights * financing[0 if side == 1 else 1]
                points = (gross - cost_price(symbol, position["entry"],
                                             position["spread_bp"])
                          - carried)
                pnl = points * position["lots"] * money_per_point
                equity += pnl
                peak = max(peak, equity)
                maximum_dd = max(maximum_dd, (peak - equity) / peak if peak > 0 else 1.0)
                trades.append({
                    "entry_ts": position["ts"], "exit_ts": ts, "side": side,
                    "points": points, "gross": gross, "pnl": pnl,
                    "atr": position["atr"], "entry": position["entry"],
                    "bars": index - position["index"], "quantity": position["lots"],
                    "reason": reason, "nights": nights, "financing": carried,
                    # Carried so a portfolio can re-size this trade against a
                    # DIFFERENT equity than the one it was taken on. Without the
                    # stop distance and the volatility reading, a combined book
                    # can only sum standalone P&L streams, which hides half the
                    # drawdown ([[blend-model-understates-portfolio-drawdown]]).
                    "distance": position["distance"],
                    "realized": position["realized"]})
                position = None

        if position is None and pending is not None and index >= pending["ready"]:
            # Fill at the next bar's open, or `lag` bars later than that when a
            # feed publishes late. Intraday that bar must still be in the SAME
            # session, so a signal on the last bar of the day is dropped rather
            # than filled at tomorrow's open -- and under a lag that drop is
            # itself part of the answer, because a late feed pushes some
            # entries off the end of their own session. On daily bars the next
            # bar is by definition the next day, so the same-day test would
            # reject every entry -- which it silently did until this was fixed.
            if daily or (day == pending["day"] and minute < closed):
                # THE ONE PRICE A LATE FEED ACTUALLY CHANGES. `decision_lag`
                # can only move an entry by whole bars, which at the 30m canon
                # bar is a 30-minute quantum for a delay measured in seconds.
                # `entry_prices` says instead "you got in at THIS price", read
                # from the tick table at the open plus the real publish lag, and
                # is the only way to charge 78 seconds rather than 30 minutes.
                # Absent -- and it is absent for every sealed result -- this is
                # `fbar[O]` and nothing moves.
                fill_price = (fbar[O] if entry_at is None
                              else entry_at(ts, fbar[O]))
                lots = quantity(equity, fill_price, pending["distance"],
                                pending["realized"], ctx)
                # THE STOP AND THE RISK UNIT ARE THE SAME NUMBER UNLESS A CELL
                # SAYS OTHERWISE, and that identity is what made the late-exit
                # bleed unavoidable. Measured on jp225 over 1,660 matched
                # trades, a stop exit costs 5.61 points a trade under the live
                # fill model against 1.58 for a clock exit and 0.000 for the
                # session flatten -- because the account holds no broker-side
                # stop, so the runtime watches the level and sends a market
                # order a whole bar later.
                #
                # The obvious answer, widening the stop, does not work while
                # `quantity` derives lots from that same distance: the position
                # shrinks until the broker's lot floor refuses it, measured at
                # 100% fill collapsing to 12% across stop_day 0.2 to 2.0.
                #
                # `stop_multiple` separates them. Lots stay sized on
                # `distance`, and the stop is placed `stop_multiple` times
                # further out, so the cell trades fewer stop-outs at a larger
                # loss each. THAT IS MORE RISK PER TRADE, NOT LESS -- a 2.0
                # cell risks twice `RISK_FRACTION` when the stop is actually
                # reached -- and it is in the grid to be measured rather than
                # because it is safe. Absent, it is 1.0 and every sealed result
                # is untouched.
                stop_distance = pending["distance"] * params.get(
                    "stop_multiple", 1.0)
                if lots > 0:
                    # The TARGET still keys on the risk unit, not on the
                    # widened stop: `rr_2` means twice what the trade risked by
                    # its own sizing, which is what every other family in the
                    # study means by it and what keeps the axis comparable.
                    target, max_bars, trail = exit_plan_for(
                        exit_mode, pending["distance"], per_session)
                    side = pending["side"]
                    position = {
                        "side": side, "entry": fill_price, "ts": ts,
                        "index": index,
                        "lots": lots, "atr": pending["atr"],
                        "stop": fill_price - side * stop_distance,
                        "target": (None if target is None
                                   else fill_price + side * target),
                        "max_bars": max_bars, "trail": trail,
                        "best": fill_price,
                        "distance": pending["distance"],
                        "realized": pending["realized"],
                        # RESOLVED AT ENTRY, NOT AT EXIT. The cost is the
                        # spread this position actually crossed when it opened;
                        # reading the tick table at exit time would charge it a
                        # quote from hours later, on the wrong side of whatever
                        # move it just traded.
                        "spread_bp": (spread_bp if spread_at is None
                                      else spread_at(ts, spread_bp)),
                        # Both stay `None` outside the overnight regime; the
                        # exit block only reads them when `overnight` is set.
                        "session": session_of[index] if overnight else None,
                        "seen": session_of[index] if overnight else None,
                        "nights": nights}
                    traded_day = day
                    fills += 1
            pending = None

        if (position is None and pending is None and traded_day != day
                and tradeable
                and (rank_of is None or (present(rank_of[index])
                                         and rank_of[index] <= ENTRY_MAX_SPREAD_RANK))
                and (want_calm is None or calm_of[index] == want_calm)):
            side = signal_fn(index, bars, ctx, params, state)
            if side is not None and null_seed is not None:
                side = random_side(ts, null_seed)
            atr, realized = atr_of[index], realized_of[index]
            risk = risk_of[index]
            # `present` on every one of these. NaN is TRUTHY, so `atr and risk`
            # alone lets a missing reading through -- and it then flows into
            # `distance`, `quantity` and the stop, where it stops being
            # recoverable. This is the guard that matters most in the module.
            if (side is not None and atr and present(atr)
                    and risk and present(risk) and present(realized)):
                if trend_of is None:
                    accepted = True
                else:
                    reference = trend_of[index]
                    accepted = reference is not None and (
                        bar[C] > reference if side == 1 else bar[C] < reference)
                if accepted:
                    # Counted only after every strategy filter has accepted, so a
                    # failure from here to entry is lot granularity alone -- which
                    # is exactly what fill_rate is meant to expose.
                    signals += 1
                    pending = {"side": side, "day": day, "atr": atr,
                               "distance": stop_day * risk,
                               "realized": realized,
                               "ready": index + 1 + lag}

    result = summarize(trades, maximum_dd, equity, initial)
    result["annual"] = annual_detail(trades, initial)
    result["signals"] = signals
    result["fills"] = fills
    result["fill_rate"] = round(100 * fills / signals, 1) if signals else 0.0
    if trades:
        bp = [1e4 * t["gross"] / t["entry"] for t in trades if t["entry"]]
        result["gross_bp_per_trade"] = round(statistics.fmean(bp), 4)
        result["breakeven_bp"] = round(statistics.fmean(bp), 4)
        result["long_share"] = round(sum(t["side"] == 1 for t in trades) / len(trades), 3)
        # Strip the return a mechanical rule with the same long/short mix and
        # holding period would earn from unconditional session drift.
        window = [bar for bar in bars if lo <= bar[TS] < hi]
        moves = [1e4 * (b[C] - a[C]) / a[C] for a, b in zip(window, window[1:]) if a[C] > 0]
        mu = statistics.fmean(moves) if moves else 0.0
        drift = statistics.fmean([t["side"] * t["bars"] * mu for t in trades])
        excess = [value - t["side"] * t["bars"] * mu for value, t in zip(bp, trades)]
        result["drift_bp_per_trade"] = round(drift, 4)
        result["edge_vs_drift_bp"] = round(statistics.fmean(excess), 4)
        if len(excess) > 1:
            sd = statistics.stdev(excess)
            result["edge_vs_drift_t_stat"] = (
                round(statistics.fmean(excess) / (sd / math.sqrt(len(excess))), 2)
                if sd else 0.0)
    if include_trades:
        result["trade_log"] = trades
    return result


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #

def passes(symbol, stat, dd=MAX_DD):
    years = is_years(symbol)
    annual = stat["annual"]
    positive = sum(annual.get(str(y), {}).get("pnl", 0) > 0 for y in years)
    worst = max((annual.get(str(y), {}).get("max_dd_pct", 100) for y in years),
                default=100)
    return (stat["trades"] >= max(50, 25 * len(years))
            and stat["pf"] >= MIN_PROFIT_FACTOR
            and stat["max_dd_pct"] <= dd
            and positive >= min_positive_years(symbol)
            and worst <= ANNUAL_DD
            and stat.get("fill_rate", 0) >= MIN_FILL_RATE)


def quality(symbol, stat):
    if not passes(symbol, stat):
        return -math.inf
    returns = [stat["annual"].get(str(y), {}).get("return_pct", 0)
               for y in is_years(symbol)]
    return (100 * math.log(stat["final"] / INITIAL_BALANCE) + min(returns)
            + .25 * statistics.median(returns) - .5 * statistics.pstdev(returns))


def gate_report(symbol, results, spec, dd=MAX_DD):
    """Which clause of `passes` eliminated the grid, and what the best cell was.

    WHY THIS EXISTS. `select` prints `-` when no cell passes, and this module's
    own docstring already complains that such a line "reads like a finding about
    the market and is a fact about the code". It is worse than that under the
    live fill model: a family can be refused for a reason that has nothing to
    do with its thesis -- too few trades because the broker's minute table does
    not reach the early years, or one losing year in seven -- and the printed
    output cannot tell that apart from a rule with no edge.

    So every clause is counted separately over the whole grid, and the best
    cell by return is carried with its stats. A clause whose count equals the
    grid size is the binding one: it refused everything on its own.

    Counted independently rather than in sequence. A cascade would attribute
    every cell to whichever clause happens to be tested first, which is an
    artefact of the code's order and would point at the wrong constraint.
    """
    years = is_years(symbol)
    need_trades = max(50, 25 * len(years))
    need_years = min_positive_years(symbol)
    counts = dict.fromkeys(
        ("trades", "profit_factor", "max_dd", "positive_years", "annual_dd",
         "fill_rate"), 0)
    total = 0
    best = None
    for params in candidates(spec):
        stat = results.get(es.frozen(params))
        if stat is None:
            continue
        total += 1
        annual = stat["annual"]
        positive = sum(annual.get(str(y), {}).get("pnl", 0) > 0 for y in years)
        worst = max((annual.get(str(y), {}).get("max_dd_pct", 100)
                     for y in years), default=100)
        counts["trades"] += stat["trades"] < need_trades
        counts["profit_factor"] += stat["pf"] < MIN_PROFIT_FACTOR
        counts["max_dd"] += stat["max_dd_pct"] > dd
        counts["positive_years"] += positive < need_years
        counts["annual_dd"] += worst > ANNUAL_DD
        counts["fill_rate"] += stat.get("fill_rate", 0) < MIN_FILL_RATE
        if best is None or stat["return_pct"] > best[1]["return_pct"]:
            best = (params, stat, positive)
    if not total:
        return None
    report = {
        "cells": total,
        "requires": {"trades": need_trades, "positive_years": need_years,
                     "of_years": len(years), "max_dd_pct": dd,
                     "profit_factor": MIN_PROFIT_FACTOR},
        "refused_by": {k: v for k, v in sorted(counts.items(),
                                               key=lambda kv: -kv[1])},
        "binding": [k for k, v in counts.items() if v == total],
    }
    if best is not None:
        params, stat, positive = best
        report["best_by_return"] = {
            "params": params,
            "return_pct": stat["return_pct"], "max_dd_pct": stat["max_dd_pct"],
            "trades": stat["trades"], "pf": stat["pf"],
            "positive_years": positive,
            "fill_rate": stat.get("fill_rate"),
        }
    return report


def neighbours(params, spec):
    out = []
    for key, values in spec.items():
        if key in CATEGORICAL:
            continue
        at = values.index(params[key])
        for j in (at - 1, at + 1):
            if 0 <= j < len(values):
                item = {**params, key: values[j]}
                if valid(item):
                    out.append(item)
    return out


def choose(symbol, family, results, bar=None):
    spec = axes(symbol, bar)[family]
    ranked = []
    for params in candidates(spec):
        stat = results[es.frozen(params)]
        own = quality(symbol, stat)
        if not math.isfinite(own):
            continue
        # The same cell with its discretionary filters switched off. A family
        # that only works once a trend and a volatility mask are applied has not
        # shown an entry edge, it has shown a filter. Swing cells carry a single
        # vol_mode, so `bare` is the cell itself and the test is vacuous there.
        bare = results.get(es.frozen({**params, "trend": "none",
                                      "vol_mode": spec["vol_mode"][0]}))
        if bare is None or bare["pnl"] <= 0 or bare["pf"] < 1:
            continue
        near = [results[es.frozen(p)] for p in neighbours(params, spec)]
        robust = [s for s in near if passes(symbol, s, NEIGHBOUR_DD)]
        if not near or len(robust) < math.ceil(.6 * len(near)):
            continue
        ranked.append((own, params, stat, len(robust), len(near)))
    if not ranked:
        return None
    score, params, stat, robust, total = max(ranked, key=lambda item: item[0])
    return {"params": params, "in_sample": stat, "score": round(score, 6),
            "robust_neighbours": f"{robust}/{total}"}


_WORKER = {}


def worker_specs(symbol):
    """The resolved entries a worker needs: the symbol, and its benchmark if any.

    Resolved HERE, in the parent, so that a missing benchmark table fails once
    with a clear message before a pool starts rather than sixteen times inside
    spawned children whose tracebacks interleave.
    """
    out = {symbol: INSTRUMENTS[symbol]}
    reference = BENCHMARK.get(symbol)
    if reference is not None:
        out[reference] = register_series(reference)
    return out


#: Memory a run may commit to worker processes, as `available - MEMORY_RESERVE`.
#:
#: THIS WAS A FLAT 35% OF AVAILABLE AND THAT WAS THE WRONG SHAPE. A fraction
#: throttles hardest exactly when the context is biggest, because a bigger
#: context means fewer workers AND those workers are the ones with the most bars
#: to walk. The FX pairs paid it twice: 60,000 bars against a stock's 11,000,
#: and 4 workers against a stock's 15. They took 19 minutes each where a stock
#: took 1.4 -- a 13x spread from a 5x difference in work.
#:
#: A fixed reserve is the honest model of the constraint. What the machine needs
#: is the OS and whatever the user has open; that is roughly constant,
#: not proportional to how big this study's arrays happen to be. Everything
#: above it is genuinely spare.
#:
#: 4 GB, because the store's own page cache runs to hundreds of megabytes and the box
#: has to stay usable -- the point of the guard is that a research job never
#: again takes the machine down ([[check-before-running-heavy-jobs]]).
MEMORY_RESERVE = 4.0e9


def process_rss():
    """This process's working set in bytes, or `None` off Windows.

    Measured with `GetProcessMemoryInfo` rather than `tracemalloc`, and the
    difference is not academic: `tracemalloc` counts only Python-level
    allocations and put the second-wave context at 222 MB when the process was
    actually holding 317 MB. Sizing a worker pool off the smaller number is how
    fifteen workers came to need 4.8 GB while the estimate said 3.3.
    """
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        psapi = ctypes.WinDLL("psapi", use_last_error=True)

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        # The signatures are declared rather than left to ctypes' defaults. A
        # bare `windll` call returns `c_int`, which truncates the 64-bit process
        # pseudo-handle, and the call then fails silently -- returning `None`
        # here, which disables the cap and is exactly the failure this guard
        # exists to prevent.
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(),
                ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    except Exception:                                   # pragma: no cover
        return None


def available_memory():
    """Bytes of physical memory currently free, or `None` if it cannot be read.

    Windows-only through `GlobalMemoryStatusEx`; anywhere else this returns
    `None` and the cap simply does not engage, which is the right failure --
    guessing a number would be worse than leaving the existing default alone.
    """
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPhys)
    except Exception:                                   # pragma: no cover
        return None


def fit_workers(symbol, phase, bar, only, workers):
    """`workers`, reduced until the pool's contexts fit in memory.

    THIS EXISTS BECAUSE A SWEEP CRASHED A MACHINE. Windows `multiprocessing`
    spawns rather than forks, so there is no copy-on-write and every worker
    holds a complete private copy of the context. At fifteen workers and the
    full sixty-two-family context that is 3 GB resident and 3.6 GB at peak, and
    the box went down partway through the second symbol.

    The context is measured rather than estimated -- it is built once here in
    the parent anyway, since `prewarm` has to touch the same bars -- so the cap
    responds to the actual symbol, the actual timeframe and the actual family
    selection instead of to a constant somebody guessed once and never revisited.

    A machine with room keeps every worker it asked for. The cap only ever
    reduces.
    """
    free = available_memory()
    if free is None or process_rss() is None:
        return workers, None
    # The context has to be ALIVE when the working set is read. Building it and
    # measuring after the call returns reports 65 MB for a context that really
    # costs 317, because CPython has already handed the arenas back -- and a cap
    # computed from that is no cap at all.
    held = context(symbol, phase, bar, only)
    footprint = process_rss()
    del held
    gc.collect()
    if not footprint:
        return workers, None
    # A spawned worker pays the whole working set, not a delta: it starts a
    # fresh interpreter, re-imports this module, and then builds its own copy.
    # The parent is holding one context of its own while this runs, so its
    # working set is already counted in what is no longer `free`.
    room = max(1, int((free - MEMORY_RESERVE) / footprint))
    return min(workers, room), footprint


def prewarm(symbol, phase, bar):
    """Pull every series this job needs ONCE, in the parent, before the pool.

    `_cached` is safe against fifteen workers cold-starting on the same entry --
    each stages its own temp file and swaps it in atomically -- but safe is not
    the same as cheap. Without this, every worker issues the same minute
    rollup over eleven years of one-minute bars and decodes the same row groups,
    so a cold symbol pays its load fifteen times over and the store serves
    fifteen identical scans. Warmed here, the children all hit the file.
    """
    all_bars(symbol, phase, bar)
    # The fill maps too, and for the same reason: reducing one symbol's minute
    # table is about eight seconds, and fifteen spawned workers would each pay
    # it cold. Built here, the children read the cache.
    #
    # BEFORE `fit_workers`, WHICH IS WHY THIS IS NOT AFTER THE BENCHMARK. That
    # function measures the parent's working set with a context alive and sizes
    # the pool from it, and each worker carries its own copy of these maps --
    # so they have to be resident when it reads, or the cap is computed against
    # a footprint the workers do not actually have.
    install_live_fills(symbol, bar)
    reference = BENCHMARK.get(symbol)
    if reference is not None:
        # RESOLVE IT FIRST. `all_bars` reads the spec out of `INSTRUMENTS`, and
        # a benchmark only lands there when something registers it. The full
        # sweep never noticed because `nq`, `es` and `btc` were themselves in
        # the symbol list and had already been resolved by `main`; running one
        # symbol on its own -- which is exactly what a scoped `why` does -- left
        # the benchmark unregistered and every command died with `KeyError:
        # 'nq'`. Registering here makes `prewarm` self-sufficient rather than
        # dependent on what else happens to be in the run.
        register_series(reference)
        all_bars(reference, phase, bar)


def _init_worker(symbol, phase, specs, bar, only=None, window=None):
    """A spawned worker re-imports this module, so the resolved instruments AND
    the bar size have to be handed over explicitly. Without the bar the child
    would rebuild its context at the 30m default and quietly score a different
    timeframe from the one the parent asked for.

    `specs` is a dict rather than one spec because a `relative` family needs its
    benchmark resolved too, and letting each of sixteen workers re-derive that
    from the store would mean thirty-two extra table scans per job to reproduce
    something the parent already knows.

    `only` travels for the same reason the bar size does: without it the child
    builds the context for ALL sixty-two families, which is 201 MB against the
    60 MB a scoped run needs -- and fifteen of those is the difference between
    0.9 GB and 3.0 GB of resident memory.
    """
    global BAR_MINUTES
    BAR_MINUTES = bar
    INSTRUMENTS.update(specs)
    # THE WINDOW HAS TO CROSS THE SPAWN OR IT IS NOT SET AT ALL. Windows
    # spawns rather than forks, so a child re-imports this module and gets
    # `IS_END` back at its declared value -- a parent that rebound it would be
    # scoring one window while every worker scored another, silently. Passed
    # explicitly and stored per worker instead.
    _WORKER["window"] = window
    # A spawned child re-imports this module, so `TICK_SPREAD_BP`, `ENTRY_PRICE`
    # and `EXIT_PRICE` come back EMPTY -- and empty means the idealised bar
    # fill, silently, in every worker while the parent scored the live one.
    # Re-installed here off the cache the parent's `prewarm` wrote.
    install_live_fills(symbol, bar, quiet=True)
    _WORKER["symbol"] = symbol
    _WORKER["bars"], _WORKER["ctx"] = context(symbol, phase, bar, only)


def _evaluate(job):
    family, params, spread_bp, null_seed = job
    # `(lo, hi)` when the caller asked for a window other than the selection
    # one -- `persistence_screen` scores the whole grid on the HOLDOUT, which
    # is the same cells over the other side of `IS_END`.
    window = _WORKER.get("window") or (None, IS_END)
    stat = backtest(family, _WORKER["bars"], _WORKER["ctx"], params,
                    lo=window[0], hi=window[1],
                    spread_bp=spread_bp, null_seed=null_seed)
    # The family travels back with the result because every family's cells are
    # now dispatched in ONE pass; `frozen(params)` alone is not unique across
    # families, and `donchian` and `swing_donchian` really do produce identical
    # parameter dicts.
    return family, es.frozen(params), stat


def _dispatch(pool, jobs, workers, note=""):
    """Every cell of every family in a single pass, grouped by family on return.

    Dispatching family by family drained and refilled the pool 27 times a job.
    A 432-cell family at chunksize 32 is 13 chunks across 16 workers, so three
    workers sat idle through it and every family paid a ragged tail. One queue
    keeps all of them fed until the work is genuinely gone.

    The chunk size is derived from the queue rather than fixed: big enough to
    amortise dispatch, small enough that the final chunks cannot leave workers
    idle while one straggler finishes.
    """
    grouped = {}
    chunk = max(1, min(64, len(jobs) // (workers * 8) or 1))
    done = step = max(1, len(jobs) // 10)
    for index, (family, key, stat) in enumerate(
            pool.imap_unordered(_evaluate, jobs, chunksize=chunk), start=1):
        grouped.setdefault(family, {})[key] = stat
        if index >= done:
            done += step
            print(f"      {note}{100 * index // len(jobs):>3}% "
                  f"({index:,}/{len(jobs):,})", flush=True)
    return grouped


def label_bar(bar=None):
    bar = BAR_MINUTES if bar is None else bar
    return "1d" if is_daily(bar) else f"{bar}m"


def scope_tag(only):
    """A filename fragment naming the restriction, or `None` for a full sweep.

    A partial run MUST NOT overwrite a full one. `select` seals its output and
    `validate` reads that seal back, so a sweep restricted to one group writing
    to the unrestricted path would quietly replace a complete study with a
    ten-family slice that still looks sealed and complete. A group named on its
    own gets its own name; anything else is spelled out.
    """
    if only is None:
        return None
    for table in (ALIASES, GROUPS):
        for name, members in table.items():
            if set(members) == set(only):
                return name
    return "+".join(sorted(only))[:60]


def output_path(symbol, bar=None, only=None):
    tag = scope_tag(only)
    return os.path.join(
        RESULTS, f"exness_families_{symbol}_{label_bar(bar)}"
                 f"{'_' + tag if tag else ''}.json")


def seal(payload, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["seal_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def select(symbol, workers, spread_bp=None, bar=None, only=None):
    bar = BAR_MINUTES if bar is None else bar
    spec = INSTRUMENTS[symbol]
    specs = axes(symbol, bar, only)
    if not specs:
        print(f"{symbol} {label_bar(bar)}: no requested family runs here")
        return
    jobs = [(family, params, spread_bp, None)
            for family, axis in specs.items() for params in candidates(axis)]
    prewarm(symbol, "select", bar)
    workers, footprint = fit_workers(symbol, "select", bar, only, workers)
    print(f"{symbol} {label_bar(bar)}: {len(jobs):,} cells over "
          f"{len(specs)} families, {workers} workers"
          + (f", {footprint / 1e6:.0f} MB context each "
             f"({workers * footprint / 1e9:.1f} GB)" if footprint else ""),
          flush=True)
    with multiprocessing.Pool(
            workers, _init_worker,
            (symbol, "select", worker_specs(symbol), bar, only)) as pool:
        results = _dispatch(pool, jobs, workers)
    family_rows = {}
    refusals = {}
    for family in specs:
        winner = choose(symbol, family, results.get(family, {}), bar)
        family_rows[family] = winner
        if winner is None:
            # WHY, not just that. See `gate_report`: a refused family is not a
            # result until the clause that refused it is named.
            report = gate_report(symbol, results.get(family, {}), specs[family])
            refusals[family] = report
            note = "-"
            if report:
                binding = ", ".join(report["binding"]) or min(
                    report["refused_by"], key=lambda k: -report["refused_by"][k])
                best = report.get("best_by_return") or {}
                note = (f"-  refused by {binding}; best cell "
                        f"{best.get('return_pct', float('nan')):+.1f}% "
                        f"dd {best.get('max_dd_pct', float('nan')):.1f}% "
                        f"n={best.get('trades', 0)} "
                        f"pf={best.get('pf', float('nan')):.2f}")
            print(f"{symbol:<8} {label_bar(bar):<4} {family:<16} {note}",
                  flush=True)
            continue
        print(f"{symbol:<8} {label_bar(bar):<4} {family:<16} "
              + (f"{winner['in_sample']['return_pct']:+.1f}% "
                 f"dd {winner['in_sample']['max_dd_pct']:.1f}% "
                 f"n={winner['in_sample']['trades']}"), flush=True)
    payload = {
        "sealed": True, "symbol": symbol, "asset_class": spec["asset_class"],
        "bar_minutes": bar, "timeframe": label_bar(bar),
        "scope": scope_tag(only) or "all",
        "protocol": {
            "family_groups": {f: FAMILIES[f].group for f in specs},
            "benchmark": BENCHMARK.get(symbol),
            "risk_unit": f"stop_day x average true daily range over "
                         f"{DAILY_RANGE_BARS} days -- timeframe invariant",
            "holding": {f: family_hold(f) for f in specs},
            "account": "Exness Pro 416209807",
            "cost_model": "spread only; commission measured at 0.0000",
            # NAMED IN THE SEAL because a number scored on the idealised bar
            # fill and one scored on the account's own path are not comparable,
            # and an older sealed file carries neither of these keys.
            "execution": {
                "model": "live",
                "spread_at_entry": broker_minute_table(symbol),
                "bars_priced": len(ENTRY_PRICE.get(symbol) or {}),
                "feed_lag_seconds": feed_lag_seconds(symbol),
                "bridge_queue_seconds": BRIDGE_QUEUE_SECONDS,
                "exit": "market order one whole bar + lag + queue after the "
                        "bar it fired on; no broker-side stop on this account",
            },
            "spread_bp": spec["spread_bp"] if spread_bp is None else spread_bp,
            "spread_quoted_at": spec["spread_quoted_at"],
            "slippage_bp": SLIPPAGE_BP,
            "total_cost_bp": cost_bp(symbol, spread_bp),
            "in_sample_years": list(is_years(symbol)),
            "holdout": "2025-01-01 through 2026-08-16",
            "initial_balance": INITIAL_BALANCE,
            "sizing": "1.5% volatility-throttled stop risk, MT5 lots, "
                      "4x notional ceiling",
            "contract": {k: spec[k] for k in (
                "broker", "multiplier", "contract_size", "tick_size",
                "tick_value", "volume_min", "volume_step", "volume_max",
                "currency_profit", "fx_to_usd")},
            "data": {k: spec[k] for k in ("table", "source", "first_row",
                                          "last_row", "warmup")},
            "session_minutes": list(spec["session"]),
            "shift_hours": spec["shift_hours"], "calendar": spec["calendar"],
            "candidate_counts": {f: len(candidates(a)) for f, a in specs.items()},
            "warning": spec["warning"],
        },
        "families": family_rows,
        #: Present only for families that selected nothing. A reader who sees
        #: `null` above needs the clause that did it, or the file says a family
        #: failed and cannot say at what.
        "refused": {k: v for k, v in refusals.items() if v},
    }
    seal(payload, output_path(symbol, bar, only))
    print(f"sealed {output_path(symbol, bar, only)}")


def rehydrate(params):
    """Restore the types a JSON round-trip flattens.

    JSON has no tuples, so `macd_set` -- a `(fast, slow, signal)` triple that
    indexes the precomputed histogram table -- comes back from a sealed file as
    a LIST, and a list cannot be a dict key. `validate` is the only path that
    reads params back off disk rather than holding the ones it searched, so it
    was the only place this fired, and it fired on every symbol.

    Converting every list back to a tuple is safe because no axis in the study
    is legitimately list-valued: the grid is built from tuples throughout.
    """
    return {key: tuple(value) if isinstance(value, list) else value
            for key, value in params.items()}


def validate(symbol, spread_bp=None, bar=None, only=None):
    bar = BAR_MINUTES if bar is None else bar
    path = output_path(symbol, bar, only)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    expected = payload.pop("seal_sha256")
    payload.pop("validation", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise SystemExit("selection seal mismatch")
    install_live_fills(symbol, bar)
    bars, ctx = context(symbol, "validate", bar, only)
    validation = {}
    for family, winner in payload["families"].items():
        if winner is None:
            continue
        params = rehydrate(winner["params"])
        base = backtest(family, bars, ctx, params, lo=IS_END, hi=OOS_END,
                        spread_bp=spread_bp)
        # THE SWEEP SUPPRESSES THE MEASURED SPREAD AND NOTHING ELSE. A swept
        # constant cannot move a bar whose real spread is already known -- with
        # the map in place `COST_SWEEP_BP` would only reach the handful of bars
        # the broker table misses, and would print seven near-identical numbers
        # that read as robustness. `tick_spreads={}` asks for the constant back,
        # so the sweep is still the question it was: what a flat cost of `v` bp
        # would do. The lagged entry and the late market exit STAY, because they
        # are not a cost assumption.
        sweep = {str(v): backtest(family, bars, ctx, params, lo=IS_END,
                                  hi=OOS_END, spread_bp=v, tick_spreads={})
                 for v in COST_SWEEP_BP}
        validation[family] = {"oos": base, "cost_sweep_bp": sweep}
        print(f"{symbol} {label_bar(bar)} {family:<16} OOS {base['return_pct']:+.1f}% "
              f"dd {base['max_dd_pct']:.1f}% n={base['trades']} "
              f"PF={base['pf']:.2f} BE={base.get('breakeven_bp', 0):.2f} bp")
    payload["seal_sha256"] = expected
    payload["validation"] = validation
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def why(symbol, workers, spread_bp=None, requested=None, bar=None):
    """Re-select with every signal direction replaced by a coin flip.

    Timing, filters, exits, sizing and the full parameter search stay intact;
    only long versus short is randomized. A real candidate has to beat the
    holdout result the same search budget manufactures from noise -- a random
    control has scored +622% at t=4.19 on this data
    ([[coin-flip-control-beats-real-signals]]), so an in-sample number read
    without one means nothing.
    """
    bar = BAR_MINUTES if bar is None else bar
    spec = INSTRUMENTS[symbol]
    available = axes(symbol, bar, requested)
    families = list(available)
    if not families:
        print(f"{symbol} {label_bar(bar)}: no requested family runs here")
        return
    prewarm(symbol, "validate", bar)
    # `prewarm` has installed the fill maps, and the control runs on them for
    # the same reason the real search does: a coin flip priced on the idealised
    # fill against a signal priced on the account's own path compares two
    # execution models rather than two signals.
    full_bars, full_ctx = context(symbol, "validate", bar, requested)
    rows = {family: [] for family in families}
    prewarm(symbol, "select", bar)
    workers, _footprint = fit_workers(symbol, "select", bar, requested, workers)
    with multiprocessing.Pool(
            workers, _init_worker,
            (symbol, "select", worker_specs(symbol), bar, requested)) as pool:
        # One queue per seed rather than one per family per seed: the same 81
        # dispatches collapse to 3, and a coin-flip grid is exactly as parallel
        # as the real one.
        for seed in (1, 2, 3):
            jobs = [(family, params, spread_bp, seed) for family in families
                    for params in candidates(available[family])]
            print(f"{symbol} {label_bar(bar)} flip{seed}: {len(jobs):,} cells",
                  flush=True)
            results = _dispatch(pool, jobs, workers, note=f"flip{seed} ")
            for family in families:
                selected = choose(symbol, family, results.get(family, {}), bar)
                if selected is None:
                    rows[family].append(None)
                    print(f"{symbol} {family:<16} flip{seed}: no IS cell")
                    continue
                outside = backtest(family, full_bars, full_ctx, selected["params"],
                                   lo=IS_END, hi=OOS_END, spread_bp=spread_bp,
                                   null_seed=seed)
                rows[family].append({"seed": seed, "params": selected["params"],
                                     "in_sample": selected["in_sample"],
                                     "out_of_sample": outside})
                print(f"{symbol} {family:<16} flip{seed}: IS "
                      f"{selected['in_sample']['return_pct']:+.1f}% OOS "
                      f"{outside['return_pct']:+.1f}%")
    tag = scope_tag(requested)
    destination = os.path.join(
        RESULTS, f"exness_families_null_{symbol}_{label_bar(bar)}"
                 f"{'_' + tag if tag else ''}.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump({"symbol": symbol, "bar_minutes": bar, "spread_bp": spread_bp,
                   "null_control": rows}, handle, indent=2, sort_keys=True)
        handle.write("\n")


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def expand(argument):
    """`--symbols` accepts names, asset classes, or `all`."""
    if not argument or argument == "all":
        return list(UNIVERSE)
    out = []
    for token in argument.replace(" ", "").split(","):
        if not token:
            continue
        members = [s for s in UNIVERSE if CLASS.get(s) == token]
        out.extend(members or [token])
    return list(dict.fromkeys(out))


def report_coverage(symbols):
    """Which symbols can be studied, and the broker column is now the gate.

    A DECIDING TABLE IS NO LONGER ENOUGH. Every fill here is priced off
    `exness_<broker>_1m`, so a symbol without one is refused in `resolve`
    rather than scored on the idealised bar fill -- and this is the command
    that says so before a sweep spends an hour finding out.
    """
    specs = load_specs() if os.path.exists(SPEC_PATH) else {"symbols": {}}
    print(f"{'symbol':9}{'class':11}{'table':13}{'broker':9}"
          f"{'spread':>9}{'quoted':>14}{'fills':>7}  data")
    studyable = 0
    for symbol in symbols:
        have = coverage(symbol)
        spec = specs["symbols"].get(symbol, {})
        table = have.get("table") or "-"
        spread = spec.get("spread_bp")
        fills = has_broker_minutes(symbol)
        studyable += bool(fills and have["runnable"])
        print(f"{symbol:9}{CLASS.get(symbol,'?'):11}{table:13}"
              f"{spec.get('broker','-'):9}"
              f"{(f'{spread:.3f}' if spread is not None else '-'):>9}"
              f"{spec.get('quoted_at','-'):>14}{('yes' if fills else 'NO'):>7}  "
              + (f"{have['first_row']}..{have['last_row']} "
                 f"IS from {have['first_full_year']}" if have["runnable"]
                 else have["reason"]))
    print(f"\n{studyable} of {len(symbols)} can be studied. `fills` is "
          f"whether the broker's own\n  minute table is in the store; without "
          f"it there is nothing to price the\n  entry spread, the feed lag or "
          f"the late market exit against, and `resolve`\n  refuses the symbol "
          f"rather than scoring it on the bar's own open.")


def report_families(bars, only=None):
    """Every family, its thesis group, how it holds, and where it runs.

    Grouped by thesis rather than alphabetically, because the taxonomy is the
    thing worth reading: sixty-two rules sorted by name look like a pile of
    indicators, and sorted by question they look like ten lines of enquiry.
    """
    print(f"{'family':22}{'holds':11}{'runs on':16}{'needs':11}{'only for':12}")
    for group, members in GROUPS.items():
        print(f"\n-- {group}")
        for name in sorted(members):
            if only is not None and name not in only:
                continue
            family = FAMILIES[name]
            where = ("intraday only" if family.scope == "intraday"
                     else "daily only" if family.scope == "daily"
                     else f"<={NIGHT_BAR_MAX}m only" if family.scope == "night"
                     else "any")
            print(f"{name:22}{family.hold:11}{where:16}"
                  f"{family.needs or '-':11}"
                  f"{','.join(family.symbols) if family.symbols else '-':12}")
    for bar in bars:
        allowed = [n for n, f in FAMILIES.items()
                   if not (f.scope == "intraday" and is_daily(bar))
                   and not (f.scope == "daily" and not is_daily(bar))
                   and not (f.scope == "night"
                            and (is_daily(bar) or bar > NIGHT_BAR_MAX))]
        print(f"\n{label_bar(bar):>5}: {len(allowed)} families available "
              "(before the per-symbol benchmark test)")


def report_budget(symbols, bars, only=None):
    """How many cells a run would score, and how many hypotheses that is.

    Printed rather than buried because the number is the argument against
    reading any single result. Every cell is a draw, the best of N draws is
    selected by construction, and this study now spans four asset classes, seven
    timeframes and twenty-nine families. A coin-flip search on this data has
    returned +622% at t=4.19 ([[coin-flip-control-beats-real-signals]]); the
    budget below is what makes that possible, and `why` is what prices it.
    """
    total = 0
    by_group = {}
    print(f"{'symbol':9}{'bar':>6}{'families':>10}{'cells':>12}")
    for symbol in symbols:
        for bar in bars:
            spec = INSTRUMENTS.get(symbol)
            if spec is None:
                continue
            if spec["source"] == "30m" and not is_daily(bar) and bar < 30:
                continue
            grid = axes(symbol, bar, only)
            cells = sum(len(candidates(a)) for a in grid.values())
            for family, axis in grid.items():
                by_group[FAMILIES[family].group] = (
                    by_group.get(FAMILIES[family].group, 0) + len(candidates(axis)))
            total += cells
            print(f"{symbol:9}{label_bar(bar):>6}{len(grid):>10}{cells:>12,}")
    print(f"\n{'TOTAL':9}{'':>6}{'':>10}{total:>12,} cells")
    print(f"{'':9}{'':>6}{'':>10}{'':>12}  one winner per family is the "
          "hypothesis count; the cells are the search budget behind each")
    if by_group:
        print(f"\n{'group':12}{'cells':>12}{'share':>8}")
        for group in GROUPS:
            cells = by_group.get(group, 0)
            if cells:
                print(f"{group:12}{cells:>12,}{100 * cells / total:>7.1f}%")
        print("\n  --groups takes one of these at a time. A sweep over all of "
              "them\n  produces a winner whose selection budget nobody can "
              "state, which is\n  the condition `why` exists to price.")


def report_cost(symbols, bar=None):
    """Cost against the RISK ACTUALLY TAKEN, which is what decides a family study.

    WHY THIS COMMAND EXISTS. The stock sweep passed 17 of 27 families on TSLA
    and 0 on ORCL, and reading that as "TSLA is the tradeable stock" is the
    wrong conclusion. Risk per trade is fixed at `RISK_FRACTION` of equity, so a
    result is scored in units of the stop -- and the stop is a fraction of the
    average daily range. What matters is therefore not the spread and not the
    volatility but the RATIO between them, because that is the fraction of every
    unit of risk that is paid to the broker before the strategy has done
    anything.

    Measured on 30m bars over the in-sample years, at `stop_day = 0.4`:

        tsla   2.41 bp spread, 485 bp daily range  ->  1.3% of the stop
        nvda   4.94 / 398                          ->  3.2%
        amzn   4.09 / 264                          ->  4.1%
        aapl   3.91 / 220                          ->  4.7%
        msft   4.43 / 209                          ->  5.5%
        googl  6.36 / 237                          ->  6.9%
        jpm    6.07 / 217                          ->  7.2%
        amd   13.48 / 416                          ->  8.2%
        orcl  14.02 / 233                          -> 15.2%

    TSLA has the CHEAPEST spread on the account and the WIDEST range, and the
    two compound: it keeps twelve times more of its gross edge than ORCL does.
    Across the eleven, this share and the number of families that passed rank
    against each other at Spearman -0.79 -- cheaper risk, more passes. The three
    cheapest took 40 of the 61 passes and the three dearest took 6. The study is
    ranking the broker's pricing, not the rules.

    Two things follow, and both are done rather than argued about. The gates are
    NOT loosened per symbol -- a threshold that moves with the instrument stops
    being a comparison. Instead the `relative` group divides each name by its
    benchmark, which removes the market factor the eleven stocks share and
    leaves the part that can actually differ between them; and this table is
    printed so that "only TSLA" is read as a cost fact, which it is.
    """
    bar = BAR_MINUTES if bar is None else bar
    print(f"{'symbol':9}{'spread':>8}{'cost':>7}{'daily range':>13}"
          f"{'bar atr':>9}{'cost/stop':>11}")
    print(f"{'':9}{'bp':>8}{'bp':>7}{'bp':>13}{'bp':>9}{'@0.4':>11}")
    rows = []
    for symbol in symbols:
        # `cost` reads only the daily range and the bar ATR, both of which the
        # base context carries, so it asks for no optional block at all.
        bars, ctx = context(symbol, "select", bar, set())
        ranges = [value for value in ctx["risk"] if value]
        spans = [value for value in ctx["atr"] if value]
        closes = [row[C] for row in bars]
        if not ranges or not closes:
            print(f"{symbol:9}  no bars")
            continue
        price = statistics.fmean(closes)
        daily = 1e4 * statistics.fmean(ranges) / price
        span = 1e4 * statistics.fmean(spans) / price
        cost = cost_bp(symbol)
        share = cost / (0.4 * daily)
        rows.append((share, symbol))
        print(f"{symbol:9}{INSTRUMENTS[symbol]['spread_bp']:>8.2f}{cost:>7.2f}"
              f"{daily:>13.0f}{span:>9.1f}{100 * share:>10.1f}%")
    if len(rows) > 1:
        rows.sort()
        print("\n  cheapest risk first: "
              + ", ".join(f"{s} {100 * v:.1f}%" for v, s in rows))
        print("  a family study compares these numbers before it compares any "
              "strategy;\n  the symbol keeping the most of its gross edge wins "
              "a fixed-gate sweep\n  whatever the rules are.")


def main():
    parser = argparse.ArgumentParser(
        description="Family study for any Exness Pro symbol, at any timeframe.")
    parser.add_argument("command", choices=("specs", "spreads", "swaps",
                                            "ticks",
                                            "watch", "coverage", "profile",
                                            "families", "budget", "cost",
                                            "select", "validate", "why"))
    parser.add_argument("--symbols", default="all",
                        help="comma-separated names, an asset class "
                             "(commodity/index/crypto/stock/forex/future), "
                             "or 'all'")
    parser.add_argument("--bar-minutes", default="30",
                        help="bar size: 15, 30, 60, 120, 240, or 1440 for "
                             "daily. Comma-separated runs each in turn.")
    parser.add_argument("--families", default=None,
                        help="restrict the run to these families, by name")
    parser.add_argument("--groups", default=None,
                        help="restrict the run to these thesis groups; "
                             f"one or more of {','.join(GROUPS)}. A restricted "
                             "`select` writes to its own file so it cannot "
                             "overwrite a full sweep.")
    parser.add_argument("--spread-bp", type=float, default=None,
                        help="override the measured spread, in bp")
    parser.add_argument("--stale-spreads", action="store_true",
                        help="accept a spread quoted outside session hours")
    parser.add_argument("--minutes", type=float, default=10.0,
                        help="`spreads` sampling window")
    parser.add_argument("--hours", type=float, default=18.0,
                        help="`watch` deadline: how long to wait for markets "
                             "to open before giving up on the stragglers")
    parser.add_argument("--min-samples", type=int, default=MIN_SPREAD_SAMPLES,
                        help="`watch` live ticks required per symbol")
    parser.add_argument("--days", type=float, default=7.0,
                        help="`ticks` history window, in days")
    parser.add_argument("--workers", type=int,
                        default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args()
    symbols = expand(args.symbols)
    only = expand_families(args.families, args.groups)
    bars = [int(value) for value in str(args.bar_minutes).replace(" ", "").split(",")
            if value]
    for bar in bars:
        if bar not in BAR_CHOICES:
            raise SystemExit(f"--bar-minutes {bar} is not one of {BAR_CHOICES}")

    if args.command == "specs":
        path = save_specs(read_specs(symbols))
        print(f"wrote {path}")
        return
    if args.command == "spreads":
        sample_spreads(symbols, minutes=args.minutes)
        return
    if args.command == "swaps":
        snapshot, missing = read_swaps(symbols)
        path = save_specs(snapshot)
        print(f"wrote {path}")
        if missing:
            print(f"not quoted, financing left unset: {', '.join(missing)}")
        return
    if args.command == "ticks":
        tick_spreads(symbols, days=args.days)
        return
    if args.command == "watch":
        watch_spreads(symbols, hours=args.hours, min_samples=args.min_samples)
        return
    if args.command == "coverage":
        report_coverage(symbols)
        return
    if args.command == "profile":
        for symbol in symbols:
            report_profile(symbol)
        return
    if args.command == "families":
        report_families(bars, only)
        return

    # A REFUSAL DROPS ONE SYMBOL, IT DOES NOT END THE SWEEP. `resolve` now
    # refuses any symbol the broker's minute table does not cover, and on
    # `--symbols all` that is twenty-seven of the forty-eight -- so raising
    # here would mean the first stock CFD in the list killed a run that could
    # have scored the other twenty-one. Named at the end for the same reason
    # the per-symbol failures below are: a skipped symbol the operator cannot
    # see is a study with a hole in it.
    refused = []
    runnable = []
    for symbol in symbols:
        try:
            resolve(symbol, allow_stale=args.stale_spreads)
        except SystemExit as error:
            refused.append(str(error))
            print(f"SKIPPED {error}", flush=True)
            continue
        runnable.append(symbol)
    symbols = runnable
    if refused and not symbols:
        raise SystemExit(f"none of the requested symbols can be studied "
                         f"({len(refused)} refused)")
    if args.command == "budget":
        report_budget(symbols, bars, only)
        return

    global BAR_MINUTES
    failures = []
    for bar in bars:
        BAR_MINUTES = bar
        if args.command == "cost":
            report_cost(symbols, bar)
            continue
        for symbol in symbols:
            spec = INSTRUMENTS[symbol]
            if spec["source"] == "30m" and not is_daily(bar) and bar < 30:
                print(f"{symbol}: skipped at {label_bar(bar)} -- "
                      f"{spec['table']} is native 30m")
                continue
            # ONE BAD SYMBOL MUST NOT COST THE OTHER FORTY-THREE. A sweep of
            # this size runs unattended for hours, and every symbol seals
            # independently the moment it finishes -- so a failure on symbol
            # thirty is worth a line of output and nothing more. Letting it
            # propagate discards thirteen completed hours of work that were
            # already safely on disk.
            try:
                if args.command == "select":
                    select(symbol, args.workers, args.spread_bp, bar, only)
                elif args.command == "validate":
                    validate(symbol, args.spread_bp, bar, only)
                else:
                    why(symbol, args.workers, args.spread_bp, only, bar)
            except KeyboardInterrupt:
                raise
            except Exception as error:                  # noqa: BLE001
                failures.append(f"{symbol} {label_bar(bar)}: "
                                f"{type(error).__name__}: {error}")
                print(f"FAILED {symbol} {label_bar(bar)}: "
                      f"{type(error).__name__}: {error}", flush=True)
                traceback.print_exc()

    if refused:
        print(f"\n{len(refused)} symbol(s) refused before the sweep -- no "
              f"broker minute table, so no fill to price:")
        for line in refused:
            print(f"  {line}")
    if failures:
        print(f"\n{len(failures)} symbol(s) failed and were skipped:")
        for line in failures:
            print(f"  {line}")


if __name__ == "__main__":
    main()
