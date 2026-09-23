# The fourth wave: daily strategies and overnight positions

`exness_families.py`, 2026-08-22. Twenty-one families, four groups, one new
holding regime. Nothing that came before it changed.

## Why it was needed

The study had 75 families and looked like this:

| holding regime | families | what it means |
|---|---:|---|
| `session` | 63 | flattened at the close, at most one entry a day |
| `swing` | 12 | holds through the close, exits on a stop/target/trail/day count |

So `--bar-minutes 1440` produced the same 75 intraday rules on a coarser bar.
Daily was a bar size, not a subject: not one family was designed around anything
a *day* contains, and nothing in the module had ever held a position from one
close to the next open.

After: **96 families, 17 groups, 63 session / 26 swing / 7 overnight.**

## The third holding regime

`hold="overnight"` enters at the open of a session's last fillable bar and exits
at an **open**, one to three sessions later. Holding is counted in
`session_index`, so a Friday entry sleeps through the weekend as *one* night
rather than three.

Two things make it honest rather than flattering:

- **The stop is checked against a window with no bars in it.** `context` filters
  the bar list to in-session rows, so between one close and the next open there
  is nothing for a stop test to see and a naive overnight backtest reports gap
  risk as free. The engine tests the stop against `ctx["overnight"]` — the
  out-of-session high and low — at the first bar of every session a position
  slept into. On DE40 that turns **382 of 1,296** single-night trades into stops
  rather than open-to-open exits.
- **The regime is refused above 60m** (`NIGHT_BAR_MAX`). At 30m the entry is
  half an hour of daylight plus the gap; at 240m the same rule would be buying
  the afternoon and calling the answer overnight.

`nights_N` also gets its own guard in `exit_plan_for`, because `exit_plan`
would otherwise fall through to its trailing branch and read `"nights_1"[6:]`
as a `1.0` trail multiple — a valid float, a plausible number, completely wrong.

## The families

### `night` — 7, `scope="night"`, `hold="overnight"`

A session return is the sum of two exposures with different signs, variances and
mechanisms. The close series has already added them together, so no rolling
statistic on it can separate them.

| family | reads |
|---|---|
| `night_drift` | a fixed direction, by weekday. The base rate, and the group's control |
| `night_close_location` | where the session closed inside its own range |
| `night_day_return` | the daytime leg, followed or faded across the night |
| `night_gap_echo` | last night's gap, echoed or reversed by tonight's |
| `night_vol` | the night held only in a chosen volatility state |
| `night_streak` | consecutive up or down *sessions*, then the night |
| `night_relative` | the night taken only by a name that led or lagged its index today |

The night grid carries `vol_mode=("none",)` alone: the shared `calm` filter is a
boolean on the same two series `night_vol` reads as its thesis, and leaving it in
would apply the reading twice.

A `weekend` family was designed and **dropped**: it is exactly `night_drift` with
`weekday=4, nights_1`, because the bar list has no weekend rows and "one session
later" is already Monday.

### `almanac` — 4, `scope="daily"`, `hold="swing"`

Dates `seasonality` (month) and `turn_of_month` (month boundary) cannot name.

| family | reads |
|---|---|
| `almanac_window` | six windows named in print before this study: halloween, summer, santa, january, september, midsummer |
| `opex` | the third Friday, the week into it, the week out of it — date *derived*, not tabulated |
| `holiday` | the session before an exchange closure, or the first one back |
| `turn_of_quarter` | the quarter boundary, which carries reconstitution flows a month boundary does not |

`SEASONAL_WINDOWS` is a fixed list, not a searchable axis: a family free to pick
its own start and end day would be `seasonality`'s 24 coin flips raised to a
power. `halloween` ships with its complement `summer` so the pair is one test.

`holiday` is **too rare to test** — 18 trades in 7 years on DE40, 0 on ETHUSD
(crypto never closes). Kept at 180 cells because the rarity is worth having
measured; a passing cell is a curiosity, not a result.

### `horizon` — 7, `scope="daily"`, `hold="swing"`, `POSITION_EXIT_MODES`

The longest lookback was 252 sessions and the longest hold 15, so a rule could
look back a year and had to act on the next three weeks.

| family | reads |
|---|---|
| `tsmom` | the sign of a 3/6/12-month return, at a *monthly* rebalance |
| `skip_momentum` | the 12-1 construct — a return with its most recent month **cut out** |
| `faber` | the 200-session simple average, unreachable by a `trend` axis that stops at `ema_50d` |
| `drawdown_depth` | how far below its peak, **and how long that peak has stood** |
| `trend_age` | how long price has held one side of a long average — young or old |
| `night_share` | how much of the recent trend arrived overnight rather than in the day |
| `calendar_break` | a break of last week's / month's / quarter's range — a *fixed* level, not a rolling channel |

Three readings nothing else in the module can produce: the **age** of a state,
a return with a **hole** in it, and a level fixed by the calendar.

`POSITION_EXIT_MODES` (`rr_3`, `days_20`, `days_60`, `trail_2.5`) is a new
vocabulary rather than an addition to `SWING_EXIT_MODES`, so no published swing
grid widened.

### `crossasset` — 3, `needs="benchmark"`, `hold="swing"`

`relative` divides; these regress. A ratio answers one question — which went up
more — so it cannot tell "index +2%, name +0.4%" from "index −0.4%, name −2%",
and it cannot see a beta that is not one.

| family | reads |
|---|---|
| `bench_correlation` | the name's own direction, taken only while it has decoupled |
| `residual_momentum` | the move net of beta times the benchmark's |
| `lead_lag` | the benchmark moved and the name has not followed — *yet* |

## Cost

| | before | after | delta |
|---|---:|---:|---:|
| ETHUSD 30m | 104,292 | 106,920 | +2,628 (+2.5%) |
| ETHUSD 1d | 46,908 | 52,578 | +5,670 (+12%) |

Per group, on ETHUSD: `night` 1,188 (30m only), `almanac` 1,350 and `horizon`
2,880 (daily only), `crossasset` 1,440 at each timeframe. 21 families for 2,628
cells at 30m, against `gate`'s 22,968 for four.

## Selections

    --groups night,almanac,horizon,crossasset   one line of enquiry at a time
    --groups fourth                             all 21, one stateable budget
    --groups held                               the 33 that survive a close, old and new

## Proof that nothing else moved

Every sealed winner replayed and diffed on `pnl`, `trades`, `pf`, `max_dd_pct`,
`win_rate`, `monthly_sharpe`, `fill_rate`, `gross_bp_per_trade`:

| sealed file | winners | identical | moved |
|---|---:|---:|---:|
| `ethusd_30m` | 19 | 19 | 0 |
| `ethusd_1d` | 2 | 2 | 0 * |
| `btc_1d` | 0 | – | 0 |
| `de40_30m` | 10 | 10 | 0 |
| `xauaud_30m` | 5 | 5 | 0 |

\* at the sealed spread. The live spec has since been re-measured from 3.7261 bp
to 3.7211 bp, which moves `ethusd_1d` P&L by ~0.1 on 2,592 — a cost input, not
the engine.

## First read: the `night` group over the seven index CFDs, 30m

42 family/symbol slots, **2 survivors**, and both are the *unconditional*
control rather than a conditional rule.

| symbol | family | cell | IS return | dd | n | pf | win | gross/trade | edge vs drift | robust |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|
| jp225 | `night_drift` | long, Friday, `nights_2`, `ema_20d`, stop 0.7 | +67.5% | 9.9% | 202 | 1.431 | 57.9% | +17.40 bp | +13.92 bp, t=1.73 | 1/1 |
| de40 | `night_drift` | long, Friday, `nights_1`, `ema_50d`, stop 0.7 | +11.9% | 11.5% | 181 | 1.193 | 57.5% | +5.43 bp | +5.08 bp, t=1.38 | 1/1 |

aus200, fr40, hk50, stoxx50, uk100: nothing passed on any of the six families.

Annual, JP225: 2018 +1%/21n · 2019 +13%/36n · 2020 +12%/33n · 2021 +4%/26n ·
2022 −5%/26n · 2023 +24%/33n · 2024 +7%/27n
Annual, DE40: 2018 +5%/11n · 2019 +3%/41n · 2020 −10%/10n · 2021 +6%/37n ·
2022 +1%/10n · 2023 +3%/32n · 2024 +5%/40n

**Read this as machinery working, not as an edge.** Three reasons:

1. Both cells are `weekday=4` — the Friday-into-the-weekend hold, which is the
   one cell of `night_drift` that was designed to subsume a whole family.
2. t = 1.38 and 1.73 on the drift-adjusted edge. Neither clears 2.
3. `robust_neighbours` is **1/1** for both, and that is structural rather than
   bad luck: `neighbours` steps over every `CATEGORICAL` axis, and for
   `night_drift` that is `side`, `weekday`, `exit_mode`, `trend` and `vol_mode`
   — all genuinely labels. Only `stop_day` is a scale, so the gate asked one
   question. The night group needs `why` to price it; the neighbour gate has
   not filtered it.

No null control has been run. Nothing here should be read as a result until it
has beaten its own coin-flip search on the holdout.
