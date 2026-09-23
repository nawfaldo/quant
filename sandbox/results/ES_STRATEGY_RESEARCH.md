# ES intraday strategy research

## Verdict

No tested price-only ES strategy is ready to trade. The strongest new candidate
passed a deliberately strict 2017-2024 consistency protocol, then lost money in
both 2025 and 2026. It is rejected rather than retuned on the holdout.

## Account and execution model

- Initial equity: USD 1,000; every trade sizes from current equity, so returns compound.
- Product model: Exness regular `US500`, USD 1 per index point per lot.
- Quantity: 0.01 lot step, 0.03 lot minimum, floored so intended stop risk is not exceeded.
- Margin: 0.25% fixed margin requirement.
- Cost: the complete 0.20-point spread is paid at entry.
- Time: `es_1m` is Chicago wall clock; 08:30-15:00 CT maps to the permitted 09:30-16:00 New York session.
- Positions are flat by 16:00 New York and no weekend/session carry is allowed.

## Evidence reviewed

The main external candidates were opening-range breakout, VWAP reversion,
overnight-range breakout, classic first-to-last-half-hour momentum, the
gap-adjusted noise-area momentum strategy, opening-gap reversal, narrow-range
opening breakout, failed initial-balance breakout, prior-session rejection,
and trend-conditioned VWAP reclaim. Sources included:

- Gao, Han, Li, and Zhou, [Market intraday momentum](https://www.sciencedirect.com/science/article/pii/S0304405X18301351).
- Zarattini, Aziz, and Barbon, [Beat the Market: An Effective Intraday Momentum Strategy for SPY](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172).
- The authors' [noise-band plus overnight-gap-reversal implementation](https://concretumgroup.com/backtesting-7-years-of-free-data-beat-the-market-an-effective-intraday-momentum-strategy-for-the-sp500-etf-spy/).
- [Intraday price reversals in the US stock index futures market](https://www.sciencedirect.com/science/article/pii/S0378426604000949), including its warning that costs materially erode opening-gap reversal results.
- A published [trading opening-range breakout study](https://doaj.org/article/3976caa87d1c48e4837a4f5d606b54a2) and the classic NR7 volatility-contraction pattern.
- Exness's official [US500 contract specification](https://get.exness.help/hc/en-us/articles/17854383867548-Indices).

## Results

| Family | 2017/18-2024 selection | 2025-2026 holdout | Verdict |
| --- | ---: | ---: | --- |
| Existing ORB / Donchian / broad momentum | Best momentum +66.26%, PF 1.338, DD 8.74% | -5.76%, PF 0.846, DD 13.27% | Reject |
| Existing VWAP / gap / overnight anchors | Overnight +184.67%, PF 1.236, DD 13.38% | +3.28%, PF 1.030, DD 16.09% | Reject: negligible edge and poor risk-adjusted consistency |
| Original first/last-half-hour momentum | Best cell +6.97%, PF 1.026; only 5/8 profitable years | Not opened | Reject in sample |
| Noise-band momentum + opening gap fade | +317.00%, PF 1.523, DD 11.16%; all 8 years +8.72% or better | -3.64%, PF 0.939, DD 15.21%; 2025 and 2026 both negative | Reject out of sample |
| Opening-gap reversal | +13.85%, PF 1.013, DD 48.52%; 5/8 positive years | Not opened | Reject in sample |
| Failed initial-balance fade | +28.64%, PF 1.048, DD 30.00%; 6/8 positive years | Not opened | Reject in sample |
| Prior-session high/low rejection | +14.48%, PF 1.016, DD 62.57%; 5/8 positive years | Not opened | Reject in sample |
| Trend-conditioned VWAP reclaim | +7.40%, PF 1.221, DD 13.19%; only 38 trades | Not opened | Reject: insufficient sample |
| NR7 + 15-minute opening-range breakout | +175.08%, PF 1.722, DD 8.31%; all 8 years positive | Deliberately not opened | Research candidate only: 0/6 immediate parameter neighbours pass the annual gate |

The new candidate used a 10-session time-of-day move lookback, 1.0x noise
bands, long-only momentum, a 1.5% overnight-gap fade, a 0.25 daily-ATR hard
stop, and 1% live-equity risk. Six of seven adjacent numeric parameter cells
also passed the in-sample gates. Its failure therefore looks like edge decay or
an ES/SPY transfer failure, not merely a single bad parameter choice.

At 0.40 and 0.80 points of spread, the new candidate's holdout returns decline
to -5.37% and -8.81%, respectively. Costs worsen the result but do not explain
the base failure: it already loses at the requested 0.20 spread.

## Non-momentum and alternate-family search

The second search compared 1,080 cells across five different structures using
five-minute bars. Entries execute at the next bar open, hard stops are resolved
before profit targets when both occur in one bar, every position is closed by
15:00 Chicago time, and position risk compounds from current equity. Only
2017-2024 data was available to this search.

NR7 was the sole credible alternate family. Its balanced cell requires the
previous session to have the narrowest range of the last seven sessions, then
trades the first close outside the first 15-minute range in either direction.
It uses a 0.50 daily-ATR stop, 1.5R target, and 2% current-equity stop risk. Its
annual returns were 3.90%, 4.72%, 11.43%, 8.59%, 13.74%, 36.31%, 29.01%, and
4.47% from 2017 through 2024.

This attractive exact result is not stable enough to promote. Changing only
one nearby numeric input at a time (NR lookback, opening-range length, breakout
buffer, or stop distance) produced no neighbour that retained every annual
gate. Several neighbours were profitable overall, but each introduced a weak
or losing year; the result is also concentrated in 2022-2023. Given the user's
priority on consistency, opening the holdout would add information without a
deployable selection and invite holdout shopping.

## Research discipline

The holdout was unavailable to selection SQL, and the winning parameter file
was hash-sealed before validation. The holdout result must not be used to add a
VIX, weekday, side, or time filter. Any such change would turn 2025-2026 into a
second training set.

The most defensible next step is a genuinely new information set—ES order flow,
SPY/ES breadth, dealer gamma, or point-in-time macro-event data, followed by a
new future paper-trading period. Further filtering of the same OHLCV history is
not recommended.
