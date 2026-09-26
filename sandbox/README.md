# sandbox

`sandbox` is a reusable Python replica of the Rust strategy backtester. It
loads the same Parquet data, generates strategy signals, resolves fills, sizes
trades, and evaluates parameter grids without rebuilding the Rust server.

The Rust engine remains the ground truth. Use the `validate` command to compare
a replica with `/api/run`.

## Start here

Run commands from the repository root:

```powershell
py -B -m sandbox list
py -B -m sandbox backtest "Hourly Delta Reversal"
py -B -m sandbox backtest "Hourly Delta Reversal" --set buy_stop=40
py -B -m sandbox sweep "Hourly Delta Reversal" --top 25
py -B -m sandbox walkforward "Hourly Delta Reversal (tied)"
py -B -m sandbox validate "Hourly Delta Reversal"
```

Parameter overrides use `NAME=VALUE`. Values are converted to booleans,
integers, or floats when possible.

The reusable API is equally small:

```python
from sandbox import backtest, get_strategy, list_strategies, sweep

print(list_strategies())

stats, trades = backtest(
    "Hourly Delta Reversal",
    params={"buy_stop": 40},
)

strategy = get_strategy("Hourly Delta Reversal")
ranked = sweep(strategy, overrides={"short_trend_days": 0}, top=10)
```

`backtest()` and `sweep()` also accept a strategy instance. Both accept the
lower-level engine keyword arguments, so tests and notebooks can pass in-memory
bars and context instead of re-reading the store.

## Layout

| path | purpose |
| --- | --- |
| `api.py` | stable, minimal Python API |
| `cli.py` | command-line parsing only |
| `data.py` | bar and feature loaders, fingerprinted disk cache |
| `parquet_store.py` | the one door to `data/parquet/` -- layout, timestamps, pruning |
| `volume_profile.py` | price-binned profiles, value areas, and points of control |
| `execution.py` | fills, brackets, costs, and position sizing |
| `metrics.py` | PnL, drawdown, monthly statistics, and splits |
| `search.py` | backtests, parameter grids, and plateau ranking |
| `walkforward.py` | anchored walk-forward selection |
| `purged_cv.py` | purged cross-validation |
| `validation.py` | Python replica versus Rust `/api/run` |
| `strategies/` | signal generation and parameter spaces |
| `research/` | one-off experiments and diagnostics |
| `results/` | generated and retained machine-readable reports |
| `tests/` | fast tests that do not require the store |

Runtime artifacts stay out of the code:

- `.cache/` holds fingerprinted market-data caches.
- `results/` holds sweeps, walk-forward reports, and research results.
- `trials.json` records the cumulative search budget.
- `ofi_ml_model.json` is the model consumed by the OFI replica.

The core uses the Python standard library. ML research additionally uses the
packages in `requirements.txt`.

## Run research

### Family studies: one module, symbols as a parameter

`cfd_families` replaces the eight per-universe family scripts
(`commodity_`, `index_`, `crypto_`, `stock_`, `usoil_`, `xauusd_`, `btc_` and
`es_diverse_families_research`). They ran the same protocol and disagreed only
about how to charge cost, and each hardcoded its own instrument table, so adding
a symbol meant editing code. Here the symbol is an argument:

```powershell
py -B -m sandbox.research.cfd_families coverage --symbols all
py -B -m sandbox.research.cfd_families specs    --symbols all
py -B -m sandbox.research.cfd_families spreads  --symbols index --minutes 15
py -B -m sandbox.research.cfd_families families --bar-minutes 30,1440
py -B -m sandbox.research.cfd_families budget   --symbols ethusd --bar-minutes 5,30,1440
py -B -m sandbox.research.cfd_families select   --symbols ethusd --bar-minutes 30
py -B -m sandbox.research.cfd_families validate --symbols ethusd --bar-minutes 30
py -B -m sandbox.research.cfd_families why      --symbols ethusd --bar-minutes 30
```

`--symbols` takes names, an asset class (`commodity` / `index` / `crypto`), or
`all`. Nothing about a symbol is written down twice: `resolve` reads the table,
coverage and volume-profile session from the store, and the contract size, tick
value, lot floor, profit currency and spread from the running MT5 terminal.
Only what cannot be inferred is in the module -- broker aliases (`de40` is
quoted `DE30`), the Asian indices' 6-hour shift, and 365- vs 252-day calendars.
A symbol nobody registered works with no code change: `specs --symbols nq` then
`select --symbols nq`.

Cost is Exness Pro account 416209807, and Pro's cost is the **spread alone** --
measured, not assumed: a minimum-lot BTCUSD round trip on 2026-08-16 booked
`commission 0.0000` on both deals. Everything is charged in basis points of
notional, the one unit that compares a $2.83 gas contract with a $30,000 index;
a fixed absolute spread ranks price level rather than edge.

**Every fill is the live one.** `select`, `validate` and `why` score the path
`fill_models.exness` reads out of the runtime, not the idealised bar fill:
the spread charged is the broker's own quote in the minute the entry happened
(`exness_<broker>_1m`), the entry is that bar's open moved by the ratio the
broker's price travelled over the feed's publish lag plus the bridge queue, and
the exit is a market order one **whole bar** plus that lag and queue after the
bar it fired on -- because this account carries no broker-side stop, so a stop
is a level the runtime watches and can only act on when its candle rolls. The
exit leg is the expensive one: on `usdjpy:volume_thrust` the sealed in-sample
+97.6% at 10.8% drawdown becomes +73.7% at 22.8%, which fails the 20% gate.

A symbol with no `exness_<broker>_1m` table is **refused in `resolve`** -- 21 of
the 48 have one. Scoring the rest on the bar fill would put two execution
models in one sweep and rank the model rather than the rules. `coverage` prints
a `fills` column saying which is which, and a sweep skips the refused symbols
and names them at the end instead of dying on the first one.

`validate`'s cost sweep suppresses the measured spread and charges the swept
constant instead; the lagged entry and the late exit stay, since those are not
a cost assumption. The lagged entry is a no-op on this feed by construction --
1.6s of lag lands in the same minute bar -- and the sweep is the reason that is
visible rather than hidden.

Spreads must be sampled **in session**. A quote read while a market is shut is
the last one before the close: `fr40` reads 4.858 bp at the weekend against
0.79 bp in session. `specs` therefore stamps every reading `session` or
`weekend_close` from the tick age, and `select` refuses a stale one unless
`--stale-spreads` says otherwise. Run `spreads` during the session to replace
them with a median over live ticks.

`coverage` is the first command to run. A symbol needs both a stored table and
a live broker quote, and as of 2026-08-16 only 26 of the 47 the old scripts
referenced have both -- every stock table and six of nine crypto tables are
gone, so those studies can no longer be re-derived.

#### Timeframes

`--bar-minutes` takes 5, 15, 30, 60, 120, 240, or 1440 for daily; a
comma-separated list runs each in turn and seals one file per timeframe. Daily
is treated as a different regime, not a coarser bar: the session filters and the
flatten-at-the-close disappear, every family holds through the close, families
that read a position *within* a session (opening range, initial balance, VWAP,
time of day) are dropped rather than degraded, and `seasonality` becomes
available. 28 families run intraday, 22 daily.

**Stop distance is a fraction of the average true daily range at every
timeframe.** It is deliberately not a multiple of bar ATR, which would tighten
every stop as the bar got finer and turn a resolution test into a comparison of
two different strategies. On ETHUSD bar ATR is 6.99 at 30m against 30.27 daily,
while the daily anchor holds at 34.80 and 34.31. Signal thresholds stay in bar
ATR on purpose -- reacting to smaller moves is what a finer timeframe is for.

#### Families

29 entry theses, grouped by what they read. `families` lists them with their
holding mode and scope:

| group | families |
| --- | --- |
| structure | `orb` `ib` `overnight` `pdr` `donchian` `ma_cross` `keltner` `failed_break` `nr` `inside` `squeeze` `range_expansion` |
| exhaustion | `zscore` `rsi` `consecutive` `key_reversal` `climax` |
| participation | `volume_thrust` `vwap` `gap` `momentum` |
| calendar and drift | `time_of_day` `turn_of_month` `seasonality` |
| multi-day | `swing_donchian` `swing_ma` `swing_zscore` `high_52w` |
| symbol-specific | `storage` (XNGUSD only) |

Two of them are there as controls rather than candidates. `time_of_day` enters
at a fixed clock time in a fixed direction with no trigger at all, so a breakout
that cannot beat "just be long at 10:00" has not shown anything. `seasonality`
is twenty-four coin flips by construction and exists to price how good
"excellent" has to look before it means anything.

The `session` and `swing` pairs are the other deliberate comparison:
`donchian` is flattened at the close and can capture at most one day of a move,
`swing_donchian` holds until its stop or day count. The difference reads whether
an edge lives in the entry or in the holding period.

#### The search budget is now the main risk

29 families across 7 timeframes is **163,152 cells for a single symbol**, and
roughly four million across the universe. Every cell is a draw and the best of N
is selected by construction. Run `budget` before a sweep, and treat `why` -- the
identical search with entry directions replaced by coin flips -- as mandatory.
A coin-flip search on this data has already returned +622% at t=4.19, and crypto
has a strongly positive null. Nothing here is a result until it has beaten its
own null on the holdout.

### Everything else

Research scripts are modules so their package imports stay explicit and reliable:

```powershell
py -B -m sandbox.research.ofi_ml --export sandbox/ofi_ml_model.json
py -B -m sandbox.research.air_pocket_research `
  --out sandbox/results/air_pocket_research_result.json
py -B -m sandbox.research.toxicity_gate_research `
  --out sandbox/results/toxicity_gate_research_result.json --record-trials
py -B -m sandbox.research.s5_cancel_at_distance_research `
  --out sandbox/results/s5_cancel_at_distance_result.json --record-trials
py -B -m sandbox.research.s6_iceberg_exhaustion_research
py -B -m sandbox.research.maroy_intraday_momentum --session both
py -B -m sandbox.research.maroy_intraday_momentum --session both --cost-bps 5
py -B -m sandbox.research.maroy_optimization
py -B -m sandbox.research.zarattini_vwap_trend
py -B -m sandbox.research.zarattini_vwap_trend --balance 100000
py -B -m sandbox.research.zarattini_vwap_trend --cost-sweep 0,1,5,10
py -B -m sandbox.research.zarattini_orb
py -B -m sandbox.research.zarattini_orb --balance 100000
py -B -m sandbox.research.zarattini_orb --cost-sweep 0,1,5,15,30
py -B -m sandbox.research.zarattini_orb_optimization --record-trials
py -B -m sandbox.research.microprice_optimization --record-trials
py -B -m sandbox.research.microprice_optimization --edge-scan
py -B -m sandbox.research.microprice_ml_gate --record-trials
py -B -m sandbox.research.defaults_test --record-trials
```

`defaults_test` is the one experiment that adds information to a sample whose
search budget is already spent. Every L2 strategy failed its Stage 6 gate, was
compiled anyway, and carries a negative deflated Sharpe -- and because
`trials.json` is cumulative, no further sweep can improve that. So instead of
searching, it runs each strategy once on constants nobody chose (round numbers,
symmetric sides, structural filters kept and tuned cuts dropped, each declared
with its reason in the module) and compares that with what is compiled.

Read the two windows together. The incumbents were selected on history starting
2025-02, so the full sample is partly their own training set while 2025-08
onward is not; an incumbent that wins on the full sample and ties after 2025-08
has an advantage confined to the window it was fit on. The evaluation window is
pinned to `PINNED_TO` because the live Bookmap capture writes to
`nq_l2_features_1s` continuously, so an unpinned "full sample" moves between
runs.

Use the same pattern for every file in `research/`:
`python -m sandbox.research.<module>`.

`nq_families_research` applies the commodity/crypto/index nine-family protocol
to native `nq_30m` bars. It selects on 2020-2024 and opens the sealed
2025-2026 holdout only in `validate`, using a $1,000 NQ Forex account, 0.01-lot
steps, 0.5% live-equity stop risk, 25% margin, and a 0.2-point entry spread:

```bash
py -B -m sandbox.research.nq_families_research preflight
py -B -m sandbox.research.nq_families_research select --workers 6
py -B -m sandbox.research.nq_families_research validate
py -B -m sandbox.research.nq_families_research why --workers 6
```

`drift_vwap_pullback` implements Matteo Conti's NQ strategy from the IQCapital
interview: a 09:30-anchored VWAP calculated from 15-minute bars, a three-part
trend filter, and first-counter-colour pullbacks on the 5-minute chart. It uses
the video's fixed 80-point stop, 40/50-point long/short targets, daily trade and
loss limits, 10:30-15:30 entry window, and 15:55 flatten. The default account is
the requested $1,000 Forex model with a 0.2-point entry spread and the repo's
standard 0.5% live-equity risk sizing. Run it against `nq_1m` with:

```bash
py -B -m sandbox.research.drift_vwap_pullback
py -B -m sandbox.research.drift_vwap_pullback --from 2025-01-01 --to 2025-12-31
py -B -m sandbox.research.drift_vwap_pullback --sizing-study
```

The video says a pullback need not touch VWAP but does not formalize when one
pullback ends and another begins. The replica treats the first candle in each
contiguous counter-colour run as the trigger and records that interpretation,
along with the closed-bar/no-look-ahead conventions, in its JSON output.

`--sizing-study` keeps the strategy rules fixed and compares two live-equity
compounding families. Equity-risk sizing divides 0.25/0.5/1/2% of current
equity by the fixed 80-point stop. Volatility sizing targets 10/20/30/40%
annualized NQ volatility using only the previous 20 completed RTH session
returns, capped by 4x notional and the Forex margin ceiling. Each family is
ranked only on 2020-2024 monthly Sharpe; its selected policy then starts over
from $1,000 for the half-open 2025-2027 OOS window (or the available prefix of
that window). Results are written to
`research/drift_vwap_pullback_sizing_result.json`.

`big_trades_absorption` implements Marco Boesing's order-flow method from the
IQCapital interview: the tape bucketed into 500ms clusters, a "big trade"
threshold calibrated from each session's own first half hour, and the two setups
he names — *accretion* (the aggressor moved the market, go with it) and
*absorption* (it could not, go against it) — with the stop one tick past the
cluster's extreme and the next session-VWAP band as the target. It runs on
`dbento_nq_ticks` against the requested $1,000 Forex account priced on the live
Exness **Pro** model from `combined_book.py`: no commission, a 0.892-point
spread, 0.2 points of slippage.

```bash
py -B -m sandbox.research.big_trades_absorption edge     --workers 6
py -B -m sandbox.research.big_trades_absorption select   --workers 6
py -B -m sandbox.research.big_trades_absorption validate --workers 6
py -B -m sandbox.research.big_trades_absorption null     --workers 6
```

Run `edge` first, for the reason the oil study established: at zero cost the
mean points a trade is the break-even spread, and only 7 of 432 cells clear the
1.09-point live round trip. `null` is not optional here — the band target sits
~95 ticks from a ~22-tick stop, so a coin toss at the same clusters earns a
positive gross figure, and only three real cells beat the random maximum. The
sealed cell then inverted on the 2026 holdout. Read
[`results/BIG_TRADES_ABSORPTION.md`](results/BIG_TRADES_ABSORPTION.md).

`maroy_intraday_momentum` ports Maroy (2025) — noise-boundary intraday momentum
with eight exit families — to BTCUSD on a $1,000 forex account, using the
paper's published QQQ optima unchanged. It is a transfer test, not a search: no
parameter is refitted.

The default 0.2-point spread with no commission is the Exness Zero BTCUSD cost
model, where 1 lot is 1 BTC and the quantity is already in BTC. `--spread` sets
another venue's figure, `--spread-sweep` reports each exit type's break-even
spread, and `--cost-bps` swaps the fixed spread for a proportional one if the
venue ever charges a percentage of notional instead.

`zarattini_vwap_trend` ports Zarattini & Aziz (2023) — anchor VWAP at the
session open, take the side price closes on, and flip whenever a bar closes
through the line — to BTCUSD on a $1,000 forex account. The rule has no free
parameter, so this is a pure transfer test.

Because the account is sized at 100% of equity and one lot is 0.01 BTC, the
paper's sizing on $1,000 can only deploy whatever the step allows;
`notional_use_pct` reports that and `--balance` re-runs the same rule on an
account where the step no longer binds. `size=flat` rows hold one step
regardless of equity — their return columns are blanked because the series is a
dollar sum, not a return; they exist for `gross_bps_per_trade`, the sizing- and
cost-free measure of whether the rule predicts direction at all.

`zarattini_orb` ports Zarattini & Aziz (2023) 5-minute Opening Range Breakout —
take the direction of the session's first 5-minute candle at the open of the
second, stop at the range extreme, target 10R, liquidate at the session close —
to BTCUSD on the same $1,000 forex account. The authors state they did not
optimise, so nothing is refitted. It shares the `size=flat` /
`gross_bps_per_trade` diagnostic with `zarattini_vwap_trend`, and adds `avg R`,
the per-trade PnL in units of risk that the paper itself reports.

`zarattini_orb_optimization` sweeps that rule over ~104k cells — session and
range length, stop multiple, target R, trailing stop, a trend / volatility /
weekday filter, and three equity-based sizing families (risk-and-leverage,
volatility target, fixed fraction). It ranks on 2020-2024 and prints the same
cells' 2025-2026 figures beside them; nothing is selected on the later window.

The search is tractable because which bar a trade exits on depends only on price
levels, never on sizing or filters: the five path axes are resolved once, and
the filter and sizing axes sweep the pre-resolved trade lists. Fixed stops and
targets resolve by binary search over the running high/low; only a trailing stop
needs a bar scan, and a test pins the two against each other.

Pass `--record-trials` to charge the grid to `trials.json`. Reading a
top-of-grid table is still picking the best of N draws, so that count is what
any later significance claim has to be deflated against.

Note that `btc_orb_mean_reversion_research` is a different hypothesis — fading
the opening range rather than following its break — and the two are not variants
of each other.

`microprice_optimization` sweeps NQ Microprice Divergence on a $1,000 Forex
account: signal axes (EWMA halflife, tilt cut, continuation or reversion, the
spread refusal) crossed with bracket axes (ATR fraction, reward/risk, time stop,
trailing stop), then regime, time-of-day and weekday filters, then four equity
sizing families. It ranks on 2025 alone under hard gates — drawdown under 15%,
a majority of profitable months, no losing run past a quarter, no single month
carrying half the gross profit, and at least eight months traded — and scores
the chosen cells on 2026 exactly once.

It shares the ORB module's factorisation: exits depend only on the bracket and
the bars, so each path cell is resolved once for every candidate bar and both
sides, and the remaining axes sweep those pre-resolved outcomes. The resolver is
pinned against `execution.resolve` through the registered replica, which in turn
matches `/api/run`.

`microprice_ml_gate` is the meta-label answer to the same question: the base
rule proposes, a model trained on 2025 alone decides which signals to take, and
the gate is applied before the single-position selection so a rejected signal
frees the slot. Two base rules, two model families and a threshold curve are
reported; the headline is always the pre-declared 0.5 cut, and every point on
the curve is charged as a separate trial. Read the AUC before the P&L — a model
that fits 0.73 and tests 0.52 has memorised, and the curve below it is noise.

`--edge-scan` skips the search and reports the thing that decides whether the
search had anything to find: the entry rule's mean per-trade points before the
spread, with its t-statistic, for every signal cell in both windows. The month
floor in the gates is load-bearing — without it the ranking is won by cells whose
volatility filter only warms up late, and three profitable months in a row score
a monthly Sharpe near 60.

`usoil_families_research` runs ten intraday families on USOIL 30-minute RTH
bars — three breakout, two trend following, two momentum, two mean reversion,
and the Wednesday EIA inventory release — over 74,700 cells crossed with eight
sizing schemes, selecting on 2018-2024 and scoring 2025-2026 once. Four phases:

```powershell
py -B -m sandbox.research.usoil_families_research edge     --workers 14
py -B -m sandbox.research.usoil_families_research select   --workers 14 --spread 0.0
py -B -m sandbox.research.usoil_families_research validate --spread 0.0
py -B -m sandbox.research.usoil_families_research sizing   --spread 0.0
```

`edge` is the phase to run *first* on any new instrument. It sets the spread to
zero and reports each family's best mean points a trade, which is by
construction the break-even spread. If no cell clears the real spread, the
selection pass has nothing to find and can be skipped — on USOIL that is
exactly what happened, and `select` at the 0.20 spread returned no cell in any
family. `--spread` re-runs the whole protocol under another cost assumption and
seals to its own file rather than overwriting the first; `--spread 0.0` is the
free-lunch bound, and USOIL fails even there.

`sizing` is deliberately a separate phase rather than an axis in the grid.
Sizing is a monotone rescaling of an already-fixed trade sequence — it cannot
change which bar a position exits on — so gross points a trade is invariant
across all eight schemes, and putting it in the grid would multiply the
multiple-testing burden eightfold for no information. Run it on the sealed
winners instead; the constant `OOS gross` column is the proof. The schemes are
stop-based risk at 1/1.5/2%, the same throttled by a volatility target, fixed
fractional notional at 1x/2x, and `flat` (one minimum lot, no compounding).

Selection runs on a 10,000 account and reports on 1,000. Exness USOIL is 1000
barrels a lot, so the size step is 10 barrels, and at 1,000 dollars a stop
wider than 1.50 a barrel cannot round up to one step — roughly half the signals
go unfilled and the ranking would be measuring lot arithmetic. The retained
write-up is [`results/USOIL_FAMILIES.md`](results/USOIL_FAMILIES.md).

`xauusd_families_research` is the same protocol pointed at gold, reusing the
oil module's signal functions and summary code so the two are comparable. It
adds four things oil did not need, each because gold forced it:

```powershell
py -B -m sandbox.research.xauusd_families_research edge     --workers 14
py -B -m sandbox.research.xauusd_families_research select   --workers 14
py -B -m sandbox.research.xauusd_families_research validate
py -B -m sandbox.research.xauusd_families_research why      --workers 14
py -B -m sandbox.research.xauusd_families_research micro
py -B -m sandbox.research.xauusd_families_research pretest
```

- **Edges are measured in ATR units, not dollars.** Gold's mean 30-minute bar
  went 2.09 (2018) to 19.84 (2026), so a dollar figure mostly reports the era.
  A raw scan made `gap` look like it improved 1.07 to 7.18; normalised it was
  0.211 to 0.409, and the dollar ranking favoured different families than the
  real one.
- **`benchmarks()` runs on every window.** Gold rose +114% across the holdout,
  so a long-biased rule could look good for free. The control shows it cannot:
  always-long the 08:00-16:00 session *lost* 4.3% over the same period, because
  gold's rally happens outside New York hours.
- **`micro` separates the two legs that can zero a position** — broker margin
  versus the strategy's own risk budget. On a $1,000 gold account the risk leg
  binds and leverage is irrelevant (Exness 1:2000 would allow 517 ounces), so
  the fix is a tighter stop, never more leverage.
- **`pretest` scores the sealed cells on 2006-2015**, twelve years the study
  never warmed up into. This is the phase that decided the outcome: `momentum`
  passed the 2025-2026 holdout on 132 trades and then failed the pretest on
  948, which is the better-powered estimate. Read
  [`results/XAUUSD_FAMILIES.md`](results/XAUUSD_FAMILIES.md) before reusing any
  of it — the headline lesson is that one small holdout is not enough.

`commodity_families_research` adapts the family protocol to every commodity and
energy table currently in the store: XAG, XCU, XNG, XPD, XPT, XAL, XNI and XZN.
It selects directly on the requested $1,000 account, uses 2020-2024 where the
table permits it (2022-2024 for XPD/XPT and 2023-2024 for XAL/XNI/XZN), and
keeps 2025-2026 sealed for validation. Costs are conventional pips rather than
raw price units, and P&L uses the Exness MT5 `tick_value / tick_size` contract
multiplier with broker lot limits:

```powershell
py -B -m sandbox.research.commodity_families_research preflight
py -B -m sandbox.research.commodity_families_research select   --workers 6 --spread-pips 0.2
py -B -m sandbox.research.commodity_families_research validate --spread-pips 0.2
py -B -m sandbox.research.commodity_families_research why --symbols xngusd --families zscore gap orb donchian --workers 6
```

Use `--symbols xngusd xagusd` to run only selected instruments. Natural gas
also gets a Thursday storage-report reaction family. XCU output carries a data
warning because its stored history uses a 2.8-6.6 quote scale while the live
Exness symbol currently uses a roughly 14,000 scale; do not deploy XCU until
that mapping is reconciled.

S5's retained research write-up is
[`results/S5_CANCEL_AT_DISTANCE.md`](results/S5_CANCEL_AT_DISTANCE.md). Its
raw reconstruction is read-only and caches only under `sandbox/.cache/`;
the research command does not require or modify the Rust server.

S6's iceberg-exhaustion proxy is registered as
`S6 Iceberg Exhaustion Breakout`. Its retained report is
[`results/S6_ICEBERG_EXHAUSTION.md`](results/S6_ICEBERG_EXHAUSTION.md). The
single ML challenge uses the system Python's scikit-learn installation; the
rule implementation and tests remain standard-library compatible.

## Footprint data

`data.load_footprint_features()` builds per-minute footprint facts — unfinished
auctions at the bar extremes, the longest run of diagonal imbalances on each
side, the minute POC — straight from `dbento_<symbol>_ticks`. The full price
ladder is roughly fifteen million rows over the sample, so it is reduced one
session at a time on the way in and only the summary is cached (about 139k
minutes, built in around a minute the first time).

The imbalance ratio (3x) and volume floor (4 lots) are fixed at the values
every retail footprint platform ships. Strategies vary the required *run
length*, which is the axis practitioners actually vary.

## Add a strategy

Subclass `Strategy`, register it, and implement `signals()`:

```python
from sandbox.execution import Execution, LONG, Signal
from sandbox.strategies.base import Strategy, register


@register
class MyStrategy(Strategy):
    name = "My Strategy"
    bars = "level_two"
    execution = Execution(session_end_min=960)
    defaults = {"stop": 50, "target": 75}
    grid = {
        "stop": [30, 40, 50],
        "target": [50, 75, 100],
    }

    def signals(self, bars, context, group, params):
        return [
            Signal(index, LONG, params["stop"], params["target"])
            for index, bar in enumerate(bars)
            if should_enter(bar)
        ]
```

Then import the module in `strategies/__init__.py` so it registers on package
load.

A strategy owns signals and parameter declarations. Shared entry/exit logic,
costs, sizing, and session flattening belong in `execution.py`. Use:

- `context()` for expensive data loaded once per run;
- `valid(params)` to reject impossible grid corners;
- `groups()` when independent signal groups use different parameter axes;
- `configured(overrides)` to create an isolated copy without mutating the
  shared registry.

## Research discipline

Use `walkforward` for parameter decisions. A whole-sample sweep with a holdout
can turn the holdout into a second training set; walk-forward confines selection
to each fold's training window and scores the chosen cell once.

The ranking favors broad parameter plateaus and rejects candidates that rely on
too few trades, long losing streaks, or one dominant month. Treat diagnostics
as hypotheses, judge filters per fold, and use matched controls before accepting
structural changes.

Market data comes from `data/parquet/`; nothing needs a database running.
Validation also needs the Rust server on `127.0.0.1:4000` (`PORT` overrides it)
with the `idk` environment (id 2), which reads the same store.
