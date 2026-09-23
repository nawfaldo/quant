use super::{
    data::{format_day, parse_iso_days, valid_date},
    engine::{EngineConfig, RunResult, execute, execute_combined},
    request::RunRequest,
    types::{Bar, OrderFlowFeatures},
};
use crate::api::parquet_store::{self, NANOS_PER_MINUTE, NANOS_PER_SECOND, ParquetStore};
use crate::strategies::idk::exness_combined;
use crate::{
    error::ApiError,
    strategies::idk::{PreferredData, preferred_data},
};
use arrow_array::RecordBatch;
use std::collections::{BTreeMap, HashMap};

pub async fn run(store: &ParquetStore, request: &RunRequest) -> Result<RunResult, ApiError> {
    let prepared = prepare(store, request).await?;
    execute(&prepared, request)
}

/// Runs several strategies over one shared account. The bars are loaded once
/// from the union of what each strategy prefers, so a level-two strategy and an
/// OHLCV strategy can trade the same account without either losing its data.
pub async fn run_combined(
    store: &ParquetStore,
    request: &RunRequest,
    strategies: &[String],
) -> Result<RunResult, ApiError> {
    if strategies.len() < 2 {
        return Err(ApiError::BadRequest("need at least 2 strategies".into()));
    }
    let names: Vec<&str> = strategies.iter().map(String::as_str).collect();
    let prepared = prepare_strategies(store, request, &names).await?;
    execute_combined(&prepared, strategies)
}

pub struct PreparedRun {
    pub(crate) bars: Vec<Bar>,
    pub(crate) engine: EngineConfig,
    /// One bar list per distinct market, for a combined run whose strategies do
    /// not all trade the same one. Empty for the common single-market case,
    /// where `bars` is used directly.
    pub(crate) symbol_bars: Vec<Vec<Bar>>,
    /// Index into `symbol_bars` for each requested strategy, in request order.
    pub(crate) strategy_symbols: Vec<usize>,
}

pub async fn prepare(store: &ParquetStore, request: &RunRequest) -> Result<PreparedRun, ApiError> {
    prepare_strategies(store, request, &[request.strategy.as_str()]).await
}

/// The source that satisfies every listed strategy: mixing a level-two
/// strategy with an OHLCV one means loading both and letting level-two minutes
/// win, which is exactly `PreferredData::Combined`.
fn merged_source(strategies: &[&str]) -> Result<PreferredData, ApiError> {
    let mut merged: Option<PreferredData> = None;
    for strategy in strategies {
        let source = preferred_data(strategy)
            .ok_or_else(|| ApiError::BadRequest("strategy has no market-data preference".into()))?;
        merged = Some(match merged {
            None => source,
            Some(current) if current == source => current,
            Some(_) => PreferredData::Combined,
        });
    }
    merged.ok_or_else(|| ApiError::BadRequest("no strategy selected".into()))
}

/// Cuts a SHIFTED market's tail at the instant the Python's window ends.
///
/// `exness_families.all_bars` adds the shift as it loads and `ef.backtest` then
/// stops at `shifted_ts >= hi`, so for JP225 and HK50 the window really ends six
/// hours earlier in real time than the date says. The loader here cuts on the
/// REAL stamp, so a run ending on a date hands those two markets one extra
/// session -- the one that BELONGS to the next shifted day, because their cash
/// session opens at 19:00 New York the evening before.
///
/// It is worth exactly one trade a sleeve and it is not a rounding difference:
/// on the canon window `jp225:volatility_breakout` took 342 against Python's 341
/// and `hk50:level_confluence` 123 against 122, both entered after 19:00 on the
/// last day.
///
/// ONLY FOR A BOOK RUN, like the preroll above. A research run on one sleeve is
/// asking for a date range and should get every bar inside it; a book run is
/// asking to reproduce a sealed record, and the record's window is the shifted
/// one.
fn trim_to_shifted_window(bars: &mut Vec<Bar>, market: &str, to: &str, canonical_book: bool) {
    if !canonical_book {
        return;
    }
    let Some(shift) = exness_combined::BOOK
        .into_iter()
        .find(|sleeve| sleeve.market() == market)
        .map(exness_combined::Sleeve::shift_hours)
        .filter(|shift| *shift != 0)
    else {
        return;
    };
    // `load_bars` keeps `timestamp < to + 1 day`, so that is the boundary the
    // shift is measured against.
    let Some(end) = parse_iso_days(to).map(|day| (day + 1) * 86_400) else {
        return;
    };
    bars.retain(|bar| bar.ts + shift * 3_600 < end);
}

async fn prepare_strategies(
    store: &ParquetStore,
    request: &RunRequest,
    strategies: &[&str],
) -> Result<PreparedRun, ApiError> {
    let symbol = request.symbol.to_ascii_lowercase();
    // Both allowlists are derived from the registry rather than transcribed. A
    // name listed here that `build_strategy` cannot construct makes the strategy
    // selectable in the UI and then fails the run with "unknown strategy", and a
    // market listed here with no loader does the same thing one layer down.
    if !crate::strategies::known_markets().contains(&symbol.as_str()) {
        return Err(ApiError::BadRequest("unknown symbol".into()));
    }
    let registered = crate::strategies::for_environment("idk");
    for strategy in strategies {
        if !registered.iter().any(|entry| entry.name == *strategy) {
            return Err(ApiError::BadRequest("unknown strategy".into()));
        }
    }
    let label = strategies.join(" + ");
    // Markets are now read from `market_symbol` rather than from a BTC-only
    // list, because the crypto sleeves added a third and fourth market and a
    // boolean cannot express "which one". A run is mixed when its strategies do
    // not all trade the same market; a single-market run still pins the
    // requested symbol to that market.
    let mut markets: Vec<&'static str> = strategies
        .iter()
        .map(|strategy| crate::strategies::market_symbol(strategy))
        .collect();
    markets.sort_unstable();
    markets.dedup();
    let mixed = markets.len() > 1;
    // A mixed set is served by loading each market separately and feeding every
    // strategy only its own bars. Point value is the one thing that stops being
    // shareable, and `valid_instrument` below already pins this environment to
    // Forex, where `point_value` is 1.0 for every symbol; Mini and Micro differ
    // (es 50, nq 20) and would need a per-slot config before a mixed set could
    // run on them.
    // Single-market runs still pin the requested symbol; a mixed run has no one
    // symbol to pin, and each strategy's market is decided by `market_symbol`.
    if !mixed {
        let expected_symbol = markets[0];
        if symbol != expected_symbol {
            return Err(ApiError::BadRequest(format!(
                "{} only supports {}",
                label,
                expected_symbol.to_ascii_uppercase()
            )));
        }
    }
    // Name the offending value: "invalid date range" alone sends people looking
    // at their strategy selection when the real cause is a stray space or a
    // slash in one of two free-text fields.
    for (field, value) in [("from", &request.from_date), ("to", &request.to_date)] {
        if !valid_date(value) {
            return Err(ApiError::BadRequest(format!(
                "invalid {field} date {value:?}; expected YYYY-MM-DD"
            )));
        }
    }
    // The only instrument. Every market in this book is an Exness CFD quoted in
    // 0.01-lot steps; the futures contract multipliers that used to live behind
    // this string are gone, so anything else is a request for a spec that does
    // not exist rather than one this run declines.
    if !request.instrument.eq_ignore_ascii_case("forex") {
        return Err(ApiError::BadRequest(format!(
            "{label} only supports the Forex instrument"
        )));
    }
    let source = merged_source(strategies)?;
    // Distinct (market, source) pairs in request order, and each strategy's
    // index into them. A single-market run leaves this a one-element list and
    // behaves exactly as before; `symbol_bars` stays empty so nothing downstream
    // has to branch.
    //
    // THE SOURCE IS PART OF THE KEY, not just the market. `nq_1m` is a
    // ratio-adjusted continuum and the level-two bars are raw traded prices --
    // the same minute differs by a median 475 points and up to 1,117. One series
    // cannot serve both: giving every NQ sleeve a single merged stream either
    // steps between two bases mid-session, or rebases one group away from the
    // prices it was validated on. Keyed this way, `nq` simply appears twice and
    // each sleeve reads exactly the series its `preferred_data` declares, which
    // is what its research replica reads too.
    let mut symbols: Vec<String> = Vec::new();
    let mut sources: Vec<PreferredData> = Vec::new();
    let mut strategy_symbols: Vec<usize> = Vec::new();
    for strategy in strategies {
        let market = if mixed {
            crate::strategies::market_symbol(strategy).to_owned()
        } else {
            symbol.clone()
        };
        let wanted = preferred_data(strategy)
            .ok_or_else(|| ApiError::BadRequest("strategy has no market-data preference".into()))?;
        let index = match symbols
            .iter()
            .zip(&sources)
            .position(|(known, known_source)| *known == market && *known_source == wanted)
        {
            Some(index) => index,
            None => {
                symbols.push(market);
                sources.push(wanted);
                symbols.len() - 1
            }
        };
        strategy_symbols.push(index);
    }

    // More than one series is now possible without more than one market, so the
    // per-series path has to run whenever the keys are not all identical.
    let split = symbols.len() > 1;
    let canonical_book = crate::strategies::is_book_run(strategies.iter().copied());
    // The Python cells build their long-horizon ATR, volatility and trend
    // state from history preceding the requested report window. Loading only
    // from `from_date` left ETH cold until March and changed later commodity
    // and index signals. The NQ exemption this used to carry -- its OFI and
    // Drift reference cells used their own selected window and feed -- went with
    // the symbol on 2026-09-03.
    //
    // PER SERIES, ASKED OF THE SLEEVES THAT READ IT. This was a flat 400
    // calendar days for every market until 2026-09-07, which is one number
    // standing in for two things it cannot both be: the deepest requirement in
    // the book is the sizing throttle's daily EWMA, and 400 CALENDAR days is 400
    // observations of it on ETHUSD's seven-day week against about 286 on a
    // weekday market. So the constant over-warmed the one market that needed
    // least and under-warmed the six that needed most, while looking uniform.
    //
    // Each sleeve now answers for its own filters and its own market calendar,
    // and a series loads the deepest answer among the sleeves that read it. A
    // series with no book sleeve on it -- which is every series of a non-book
    // run -- keeps the report window itself, exactly as before.
    let warm_days = |series: usize| -> i64 {
        strategies
            .iter()
            .enumerate()
            .filter(|(index, _)| strategy_symbols[*index] == series)
            .filter_map(|(_, strategy)| crate::strategies::warmup_calendar_days(strategy))
            .max()
            .unwrap_or(0)
    };
    let mut symbol_bars: Vec<Vec<Bar>> = Vec::new();
    if split {
        for (series, (market, per_series)) in symbols.iter().zip(&sources).enumerate() {
            let warm_from = parse_iso_days(&request.from_date)
                .map(|day| format_day(day - warm_days(series)))
                .unwrap_or_else(|| request.from_date.clone());
            let from = if canonical_book {
                &warm_from
            } else {
                &request.from_date
            };
            let mut loaded = load_bars(store, market, from, &request.to_date, *per_series).await?;
            trim_to_shifted_window(&mut loaded, market, &request.to_date, canonical_book);
            // NOT SESSION-FILTERED HERE, AND THAT IS LOAD-BEARING.
            //
            // Filtering to the sleeve's session at load looks free -- a
            // `FamilyEngine` already ignores an out-of-session candle, and
            // `exness_families.context` filters in exactly this place -- and it
            // would have cut the long window's memory again. It was tried on
            // 2026-08-24 and REGRESSED the holdout hard: marked drawdown went
            // 10.23% -> 19.61% and the trade count moved off Python's 4,978.
            //
            // The engine is not the Python. `last_bar[symbol]` is what every
            // open position across the book is marked against, so dropping a
            // market's out-of-session bars leaves its last in-session price
            // standing through the whole overnight gap, and every other sleeve's
            // marked equity is then measured against a stale quote. Python does
            // not have that failure mode because its `replay` marks a position
            // ONLY at stamps where that symbol actually printed.
            //
            // Memory is solved in `OrderFlowFeatures` instead, by not carrying
            // eighteen columns nothing reads.
            if loaded.is_empty() {
                return Err(ApiError::Data(format!(
                    "no {} data in requested range",
                    market.to_ascii_uppercase()
                )));
            }
            symbol_bars.push(loaded);
        }
    }
    // `bars` still carries the first market so every existing consumer -- the
    // report window, single-strategy runs, tuning -- keeps working unchanged.
    let bars = if split {
        symbol_bars[0].clone()
    } else {
        // One series for the whole run: every strategy declared the same source,
        // so `sources[0]` is that source and `merged_source` cannot disagree
        // with it. A single-strategy run lands here too.
        load_bars(
            store,
            &symbol,
            &request.from_date,
            &request.to_date,
            sources.first().copied().unwrap_or(source),
        )
        .await?
    };
    if bars.is_empty() {
        return Err(ApiError::Data("no data in requested range".into()));
    }
    let start_day = parse_iso_days(&request.from_date)
        .ok_or_else(|| ApiError::BadRequest("invalid from date".into()))?;
    let balance = request.balance()?;
    Ok(PreparedRun {
        bars,
        symbol_bars,
        strategy_symbols,
        engine: EngineConfig {
            initial: balance,
            symbol,
            start_day,
            // The 2026-09-07 book's own cap, applied only when the run IS that
            // book. A partial selection is not the configuration the cap was
            // chosen for, and capping it would size a research run by a policy
            // its members were never measured under.
            //
            // CANON IS `None` -- the cap is OFF at twenty sleeves, where it
            // allocates by trade frequency rather than by quality.
            // `EXNESS_GROSS_CAP` switches one back on for bisecting a
            // disagreement with the Python replay, which takes the same
            // argument, and 0 means off.
            gross_cap: canonical_book
                .then(|| {
                    std::env::var("EXNESS_GROSS_CAP")
                        .ok()
                        .and_then(|value| value.parse::<f64>().ok())
                        .or(exness_combined::CANON_GROSS_CAP)
                })
                .flatten()
                .filter(|cap| *cap > 0.0),
        },
    })
}

#[allow(dead_code)]
async fn load_bars(
    store: &ParquetStore,
    symbol: &str,
    from: &str,
    to: &str,
    source: PreferredData,
) -> Result<Vec<Bar>, ApiError> {
    match source {
        PreferredData::Ohlcv => load_ohlcv_bars(store, symbol, from, to).await,
        PreferredData::OhlcvWithBenchmark => {
            let mut bars = load_ohlcv_bars(store, symbol, from, to).await?;
            // `exness_families.BENCHMARK`, which is a per-MARKET table there
            // too. It used to be asked of the sleeve, so the mapping lived with
            // the cell that read it; no cell reads one since 2026-09-04, and a
            // loader that could only answer for a market some sleeve currently
            // benchmarks would have nothing to say at all.
            let market = match symbol {
                "ethusd" => "btc",
                _ => {
                    return Err(ApiError::BadRequest(format!(
                        "{symbol} declares no benchmark market"
                    )));
                }
            };
            let benchmark = load_ohlcv_bars(store, market, from, to).await?;
            // A hard failure rather than a silent `None`: the alternative is the
            // cell quietly producing no signals and being reported as a sleeve
            // that simply did not trade.
            if benchmark.is_empty() {
                return Err(ApiError::Data(format!(
                    "{symbol} needs the {market} benchmark and it returned no rows"
                )));
            }
            join_benchmark(&mut bars, &benchmark);
            Ok(bars)
        }
        PreferredData::Combined | PreferredData::CombinedDaily => {
            // TWO INDEPENDENT CAUSAL SEGMENTS, SPLIT AT THE FIRST LEVEL-TWO
            // MINUTE -- native OHLCV before it, RAW level-two after it.
            //
            // This is `combined_book.drift_vwap_orders` and
            // `exness_combined_strategies.external_trades` written out. Both
            // refuse to concatenate the price arrays, for the reason recorded
            // in the Python: `nq_1m` is the broker CFD and the level-two tick
            // feed sits at a different absolute level, so joining them creates
            // a fictitious gap that contaminates VWAP, momentum and marked
            // drawdown ([[nq-has-two-incompatible-price-series]]). They run one
            // segment per source and switch at the first timestamp that
            // actually EXISTS in level two, rather than at a hard-coded date.
            //
            // WHAT THIS REPLACED, AND WHY IT WAS WRONG TO KEEP. The port used
            // to substitute level-two minutes into the OHLCV series after
            // rescaling each onto the OHLCV basis by that day's median ratio.
            // That is a defensible design -- arguably a better one, since it
            // has no cliff at a coverage boundary -- but it is NOT the book
            // Python measured, and it moved two sleeves a long way: with one
            // continuous series `nq:volatility_breakout` earned $72.25 against
            // Python's $44.46 and `nq:drift_vwap` took 1,215 trades against
            // Python's 1,229. Every trade before the boundary already matched
            // to the bit; the divergence began at the first level-two minute
            // and nowhere else.
            //
            // THE SCALES REALLY ARE DIFFERENT, so nothing here should be read
            // as the two sources being interchangeable. Measured over
            // 2025-2026 they differ on the SAME minute by a median of 475
            // points and up to 1,117, stepping down at each quarterly roll.
            // What makes the split safe is that no indicator ever spans it:
            // `ExnessCombined` rebuilds the sleeve at the boundary, exactly as
            // Python rebuilds its context for the second segment.
            let ohlcv = load_ohlcv_bars(store, symbol, from, to).await?;
            let level_two = load_order_flow_bars(store, symbol, from, to).await?;
            // THE HANDOVER IS AT A DAY BOUNDARY, NOT AT THE FIRST LEVEL-TWO
            // MINUTE, because the Python that defines it filters by whole days.
            //
            // `drift_vwap_pullback.backtest` groups its bars `by_day` and skips
            // a day entirely with `if day * 86_400 < from_ts: continue`. The
            // native segment is asked for `..first_l2_ts` and the level-two
            // segment for `first_l2_ts..`, so the day CONTAINING the first
            // level-two minute has `day * 86_400 < from_ts` and belongs to the
            // NATIVE segment in full -- the level-two segment starts at the next
            // midnight. Splitting at 09:30 on that day instead put the two
            // engines on different feeds for one session, and that was the
            // FIRST divergence in the whole book: a drift trade at 2025-02-12
            // 10:45 took the stop on one side (-0.802) and the target on the
            // other (+0.398), and every later quantity inherited the resulting
            // equity gap.
            let Some(first_level_two) = level_two.first().map(|bar| bar.ts) else {
                // No level-two coverage at all, which is every market but NQ.
                // Python's `if not l2: return run(native, ...)` -- one segment,
                // the native one.
                return Ok(ohlcv);
            };
            let boundary = if source == PreferredData::CombinedDaily {
                (first_level_two.div_euclid(86_400) + 1) * 86_400
            } else {
                first_level_two
            };
            let mut bars: Vec<Bar> = ohlcv
                .into_iter()
                .take_while(|bar| bar.ts < boundary)
                .collect();
            bars.extend(level_two.into_iter().filter(|bar| bar.ts >= boundary));
            Ok(bars)
        }
        PreferredData::LevelTwo => load_order_flow_bars(store, symbol, from, to).await,
    }
}

pub(crate) async fn load_live_ohlcv_history(
    store: &ParquetStore,
    symbol: &str,
    from: &str,
    to: &str,
) -> Result<Vec<Bar>, ApiError> {
    load_bars(store, symbol, from, to, PreferredData::Ohlcv).await
}

pub(crate) async fn load_live_level_two_history(
    store: &ParquetStore,
    symbol: &str,
    from: &str,
    to: &str,
) -> Result<Vec<Bar>, ApiError> {
    load_bars(store, symbol, from, to, PreferredData::LevelTwo).await
}

/// The table, volume column and bar length one market is read from.
///
/// ONE MARKET HAS NO ONE-MINUTE TABLE. `xniusd_30m` is the native source for
/// spot nickel and quotes `tick_volume` rather than `volume`. Its 30-minute rows
/// ARE the candles the sleeve trades, so the strategy's own aggregation sees one
/// bar per slot and passes it through unchanged -- and `ohlcv_step` reads this
/// rather than assuming 60, or the live loop would fetch the same bar
/// twenty-nine times.
fn live_ohlcv_source(symbol: &str) -> (String, &'static str, i64) {
    match symbol {
        "xniusd" => ("xniusd_30m".into(), "tick_volume", 1_800),
        _ => (format!("{symbol}_1m"), "volume", 60),
    }
}

/// Seconds between one bar of `symbol`'s source table and the next.
///
/// THE LIVE LOOP HAS TO ASK RATHER THAN ASSUME 60. It walks a market forward one
/// step at a time and stops at the first minute that has not printed, so a
/// thirty-minute feed stepped by a minute fetches the same bar twenty-nine times
/// over and pushes each one into the strategy. `xniusd` has no one-minute table
/// at all, which is why this is derived from the same rule the loader reads
/// instead of transcribed beside it.
pub(crate) fn ohlcv_step(symbol: &str) -> i64 {
    live_ohlcv_source(symbol).2
}

// --------------------------------------------------------------------------- #
// market data
//
// These replace one QuestDB query each. The SQL is gone but the SHAPE of every
// result is unchanged: the same rollups, the same source precedence, the same
// microsecond-to-second conversions at the same boundaries, so a strategy sees
// the bars it always saw.
// --------------------------------------------------------------------------- #

/// Newest fully-closed minute in `<symbol>_1m`, for feeds that have no
/// level-two watermark.
///
/// The newest row in the table is the minute currently being written, so it is
/// stepped back one: a strategy that traded it would be reading a bar whose
/// close is still moving. Mirrors the `- 60` in
/// `latest_completed_bookmap_minute` for the same reason.
pub(crate) async fn latest_completed_ohlcv_minute(
    store: &ParquetStore,
    symbol: &str,
) -> Result<Option<i64>, ApiError> {
    let (table, _, step) = live_ohlcv_source(symbol);
    let Some(latest) = store.max_timestamp(&table)? else {
        return Ok(None);
    };
    if latest <= 0 {
        return Ok(None);
    }
    let latest_minute = latest.div_euclid(NANOS_PER_SECOND).div_euclid(60) * 60;
    Ok(Some(latest_minute - step))
}

/// One completed `<symbol>_1m` bar, or `None` when that minute never printed.
///
/// Read directly rather than through `load_live_ohlcv_history`, which opens its
/// range with a 90-day preroll for indicator warm-up -- correct at startup,
/// ruinous once per minute.
pub(crate) async fn load_live_ohlcv_minute(
    store: &ParquetStore,
    symbol: &str,
    minute: i64,
) -> Result<Option<Bar>, ApiError> {
    let (table, volume, step) = live_ohlcv_source(symbol);
    let from = minute * NANOS_PER_SECOND;
    let to = (minute + step) * NANOS_PER_SECOND;
    let batches = store.scan(
        &table,
        &["open", "high", "low", "close", volume],
        Some(from),
        Some(to),
    )?;
    Ok(ohlcv_bars(&batches, volume)?.into_iter().next())
}

pub(crate) async fn latest_completed_bookmap_minute(
    store: &ParquetStore,
    symbol: &str,
) -> Result<Option<i64>, ApiError> {
    // Scoped to `source='bm'`, so the Databento history in the same table cannot
    // stand in for a Bookmap collector that has stopped. Footer statistics span
    // both sources, so this reads the column rather than the metadata.
    let table = format!("{symbol}_l2_features_1s");
    let mut latest = 0i64;
    for batch in store.scan(&table, &["source"], None, None)? {
        let stamps = parquet_store::timestamp_column(&batch)?;
        let bookmap = parquet_store::text_equals(&batch, "source", "bm")?;
        for (stamp, is_bookmap) in stamps.iter().zip(&bookmap) {
            if *is_bookmap && *stamp > latest {
                latest = *stamp;
            }
        }
    }
    if latest <= 0 {
        return Ok(None);
    }
    let latest_minute = latest.div_euclid(NANOS_PER_SECOND).div_euclid(60) * 60;
    Ok(Some(latest_minute - 60))
}

pub(crate) async fn load_live_bookmap_minute(
    store: &ParquetStore,
    symbol: &str,
    minute: i64,
) -> Result<Option<Bar>, ApiError> {
    let from = minute * NANOS_PER_SECOND;
    let to = (minute + 60) * NANOS_PER_SECOND;

    let ticks = store.scan(
        &format!("bm_{symbol}_ticks"),
        &["price", "size", "side"],
        Some(from),
        Some(to),
    )?;
    let mut minutes = tick_minutes(&ticks)?;
    let Some((_, mut bar)) = minutes.pop_first() else {
        return Ok(None);
    };

    let depth = store.scan(&format!("bm_{symbol}_depth"), &[], Some(from), Some(to))?;
    bar.depth_events = depth_counts(&depth)?.get(&bar.ts).copied().unwrap_or(0);

    let features = l2_feature_minutes(store, symbol, "bm", Some(from), Some(to))?;
    if let Some(feature) = features.get(&bar.ts) {
        bar.order_flow = *feature;
    }
    Ok(Some(bar))
}

async fn load_order_flow_bars(
    store: &ParquetStore,
    symbol: &str,
    from: &str,
    to: &str,
) -> Result<Vec<Bar>, ApiError> {
    let (lower, upper) = day_range(from, to)?;
    let mut merged = BTreeMap::new();
    for prefix in ["dbento", "bm"] {
        // Bookmap is the live source and is loaded second, so it wins any minute
        // where both sources printed. An absent table contributes nothing --
        // `scan` returns no batches rather than failing, which is what the
        // "table does not exist" arm of the SQL version did.
        let ticks = store.scan(
            &format!("{prefix}_{symbol}_ticks"),
            &["price", "size", "side"],
            lower,
            upper,
        )?;
        for (ts, bar) in tick_minutes(&ticks)? {
            merged.insert(ts, bar);
        }
    }
    let mut bars: Vec<Bar> = merged.into_values().collect();
    attach_l2_features(store, symbol, lower, upper, &mut bars)?;
    Ok(bars)
}

/// The per-minute trade rollup: `first`/`max`/`min`/`last` price, summed size,
/// and size signed by aggressor.
///
/// Zero-size rows are excluded, matching `WHERE size > 0`. Both feeds carry
/// nanosecond timestamps, so one minute rule serves both; the SQL needed two
/// because `SAMPLE BY` and `date_trunc` disagreed about which could be applied
/// to a non-designated timestamp.
///
/// `depth_events` STAYS ZERO on a backtest bar, which is what the SQL loader
/// left it at too. `depth_event_count` was one of the eighteen feature columns
/// dropped from `OrderFlowFeatures` to get a bar from 288 bytes to 136, and no
/// strategy reads the field -- so counting the billion-row depth table per run
/// would be the loader's single most expensive act, performed for nobody. The
/// live Bookmap path fills it because it reads one minute.
fn tick_minutes(batches: &[RecordBatch]) -> Result<BTreeMap<i64, Bar>, ApiError> {
    let mut out: BTreeMap<i64, Bar> = BTreeMap::new();
    for batch in batches {
        let stamps = parquet_store::timestamp_column(batch)?;
        let prices = parquet_store::floats(batch, "price")?;
        let sizes = parquet_store::floats(batch, "size")?;
        let buys = parquet_store::text_equals(batch, "side", "BUY")?;
        let sells = parquet_store::text_equals(batch, "side", "SELL")?;
        for index in 0..stamps.len() {
            let size = sizes[index];
            if size <= 0.0 {
                continue;
            }
            let minute = stamps[index].div_euclid(NANOS_PER_MINUTE) * 60;
            let price = prices[index];
            let signed = if buys[index] {
                size
            } else if sells[index] {
                -size
            } else {
                0.0
            };
            match out.get_mut(&minute) {
                Some(bar) => {
                    bar.high = bar.high.max(price);
                    bar.low = bar.low.min(price);
                    bar.close = price;
                    bar.volume += size;
                    bar.volume_delta += signed;
                }
                None => {
                    out.insert(
                        minute,
                        Bar {
                            ts: minute,
                            open: price,
                            high: price,
                            low: price,
                            close: price,
                            volume: size,
                            volume_delta: signed,
                            depth_events: 0,
                            level_two: true,
                            order_flow: OrderFlowFeatures::default(),
                            benchmark: None,
                        },
                    );
                }
            }
        }
    }
    Ok(out)
}

/// `{minute: events}` for a depth table. Only the timestamp column is decoded.
fn depth_counts(batches: &[RecordBatch]) -> Result<BTreeMap<i64, u64>, ApiError> {
    let mut out: BTreeMap<i64, u64> = BTreeMap::new();
    for batch in batches {
        for stamp in parquet_store::timestamp_column(batch)? {
            *out.entry(stamp.div_euclid(NANOS_PER_MINUTE) * 60)
                .or_default() += 1;
        }
    }
    Ok(out)
}

fn attach_l2_features(
    store: &ParquetStore,
    symbol: &str,
    from: Option<i64>,
    to: Option<i64>,
    bars: &mut [Bar],
) -> Result<(), ApiError> {
    let mut features = HashMap::with_capacity(bars.len());
    // Databento supplies the older history; Bookmap is read second and wins for
    // any minute where both normalized sources are present.
    for source in ["dbento", "bm"] {
        features.extend(l2_feature_minutes(store, symbol, source, from, to)?);
    }
    for bar in bars {
        if let Some(feature) = features.get(&bar.ts) {
            bar.order_flow = *feature;
        }
    }
    Ok(())
}

/// The per-minute feature rollup for one collector: `last` on the snapshot
/// columns, `sum` on the flow columns.
///
/// Only the seven fields `OrderFlowFeatures` actually carries are read. The SQL
/// selected twenty-six columns and threw eighteen away, which on this store
/// would mean decoding eighteen columns of an eight-million-row file per run.
fn l2_feature_minutes(
    store: &ParquetStore,
    symbol: &str,
    source: &str,
    from: Option<i64>,
    to: Option<i64>,
) -> Result<HashMap<i64, OrderFlowFeatures>, ApiError> {
    const SUMMED: [&str; 5] = [
        "bid_add_volume",
        "bid_cancel_volume",
        "ask_add_volume",
        "ask_cancel_volume",
        "trade_delta",
    ];
    let mut columns = vec!["source", "spread", "top5_imbalance", "book_valid"];
    columns.extend(SUMMED);

    let mut out: HashMap<i64, OrderFlowFeatures> = HashMap::new();
    for batch in store.scan(&format!("{symbol}_l2_features_1s"), &columns, from, to)? {
        let stamps = parquet_store::timestamp_column(&batch)?;
        let selected = parquet_store::text_equals(&batch, "source", source)?;
        let spread = parquet_store::floats(&batch, "spread")?;
        let top5 = parquet_store::floats(&batch, "top5_imbalance")?;
        let valid = parquet_store::booleans(&batch, "book_valid")?;
        let summed: Vec<Vec<f64>> = SUMMED
            .iter()
            .map(|name| parquet_store::floats(&batch, name))
            .collect::<Result<_, _>>()?;

        for index in 0..stamps.len() {
            if !selected[index] {
                continue;
            }
            let minute = stamps[index].div_euclid(NANOS_PER_MINUTE) * 60;
            let feature = out.entry(minute).or_default();
            // Snapshot columns take the minute's LAST value; rows arrive in
            // time order, so the last write wins by construction.
            feature.spread = spread[index];
            feature.top5_imbalance = top5[index];
            feature.book_valid = valid[index];
            feature.bid_add_volume += summed[0][index];
            feature.bid_cancel_volume += summed[1][index];
            feature.ask_add_volume += summed[2][index];
            feature.ask_cancel_volume += summed[3][index];
            feature.trade_delta += summed[4][index];
        }
    }
    Ok(out)
}

/// One market's minute bars, plus a 90-day preroll so indicators are warm at
/// `from` rather than at the first reported bar.
async fn load_ohlcv_bars(
    store: &ParquetStore,
    symbol: &str,
    from: &str,
    to: &str,
) -> Result<Vec<Bar>, ApiError> {
    // XALUSD HAS NO ONE-MINUTE TABLE, so the source is named rather than
    // spelled `{symbol}_1m`. Its 30-minute rows ARE the candles the sleeve
    // trades and its volume column is `tick_volume`; the strategy's own
    // aggregation then sees one bar per slot and passes it through unchanged.
    let (table, volume, _) = live_ohlcv_source(symbol);
    let (lower, upper) = day_range(from, to)?;
    // The preroll is 90 days before `from`, exactly as `dateadd('d',-90,...)`.
    let lower = lower.map(|value| value - 90 * 86_400 * NANOS_PER_SECOND);
    let batches = store.scan(
        &table,
        &["open", "high", "low", "close", volume],
        lower,
        upper,
    )?;
    ohlcv_bars(&batches, volume)
}

/// Decoded OHLCV rows, ONE BAR PER TIMESTAMP, oldest first.
///
/// No bar-size aggregation: `<symbol>_1m` rows are already the minute bars and
/// `xniusd_30m` rows are already the slots that sleeve trades. Rows arrive in
/// file order, which is time order for every table in the store.
///
/// ROWS THAT SHARE A TIMESTAMP ARE MERGED, because the store holds tables where
/// they do and Python has always merged them. `xniusd_30m` carries TWO rows per
/// slot -- two price series about sixteen points apart -- and `ethusd_1m`,
/// `btc_1m` and `ukoil_1m` carry a few hundred duplicates each.
/// `parquet_store.read_bars` buckets by `ts // width` and reduces each bucket to
/// `open` first, `high` max, `low` min, `close` last, `volume` summed, so a
/// duplicated stamp is ONE bar there. This is that reduction.
///
/// IT IS NOT COSMETIC. Handing the engine both rows steps the strategy TWICE at
/// one instant, and `FamilyEngine::update_all` clears `action_timestamp` at the
/// top of every call -- so the second step erased the stamp the first one
/// recorded and every `xniusd:cci` fill was booked against the bar that FLUSHED
/// the candle instead of the candle itself, half an hour late on both legs.
fn ohlcv_bars(batches: &[RecordBatch], volume: &str) -> Result<Vec<Bar>, ApiError> {
    let mut bars: Vec<Bar> = Vec::new();
    for batch in batches {
        let stamps = parquet_store::timestamp_column(batch)?;
        let open = parquet_store::floats(batch, "open")?;
        let high = parquet_store::floats(batch, "high")?;
        let low = parquet_store::floats(batch, "low")?;
        let close = parquet_store::floats(batch, "close")?;
        let volume = parquet_store::floats(batch, volume)?;
        for index in 0..stamps.len() {
            let ts = stamps[index].div_euclid(NANOS_PER_SECOND);
            // The reduction, and it looks only at the bar in hand because the
            // rows are in time order: a repeat of the previous stamp folds into
            // the bar already built rather than starting a new one.
            if let Some(last) = bars.last_mut()
                && last.ts == ts
            {
                last.high = last.high.max(high[index]);
                last.low = last.low.min(low[index]);
                last.close = close[index];
                last.volume += volume[index];
                continue;
            }
            bars.push(Bar {
                ts,
                open: open[index],
                high: high[index],
                low: low[index],
                close: close[index],
                volume: volume[index],
                volume_delta: 0.0,
                depth_events: 0,
                // OHLCV rows carry no book at all, so `order_flow` here is a
                // real zero rather than a reading. Level-two strategies must
                // skip these.
                level_two: false,
                order_flow: OrderFlowFeatures::default(),
                benchmark: None,
            });
        }
    }
    Ok(bars)
}

/// `[from, to)` in epoch nanoseconds for an inclusive ISO day range.
///
/// The upper bound opens the day AFTER `to`, which is the half-open form of the
/// SQL's `timestamp < dateadd('d',1,'{to}')`. An unparseable date yields no
/// bound rather than an error, matching the old behaviour of passing the string
/// through to the database and reading whatever it made of it.
fn day_range(from: &str, to: &str) -> Result<(Option<i64>, Option<i64>), ApiError> {
    let lower = parse_iso_days(from).map(|day| day * 86_400 * NANOS_PER_SECOND);
    let upper = parse_iso_days(to).map(|day| (day + 1) * 86_400 * NANOS_PER_SECOND);
    Ok((lower, upper))
}

/// The benchmark is reduced to ONE CLOSE PER 30-MINUTE SLOT -- the last minute
/// in the slot, which is what `all_bars` produces at that bar size -- and each
/// symbol bar then takes the last benchmark slot AT OR BEFORE its own. Never a
/// future one and never interpolated, so a benchmark that has not printed for a
/// while goes stale rather than wrong.
///
/// Reading it is not lookahead even though the two closes are simultaneous:
/// every entry fills at the NEXT candle's open.
pub(crate) fn join_benchmark(bars: &mut [Bar], benchmark: &[Bar]) {
    let mut slots: Vec<(i64, f64)> = Vec::new();
    for bar in benchmark {
        let slot = bar.ts.div_euclid(1_800);
        match slots.last_mut() {
            Some((last, close)) if *last == slot => *close = bar.close,
            _ => slots.push((slot, bar.close)),
        }
    }
    let mut cursor = 0usize;
    for bar in bars {
        let slot = bar.ts.div_euclid(1_800);
        while cursor < slots.len() && slots[cursor].0 <= slot {
            cursor += 1;
        }
        bar.benchmark = (cursor > 0).then(|| slots[cursor - 1].1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow_array::{Float64Array, StringArray};
    use arrow_schema::{DataType, Field, Schema};
    use std::sync::Arc;

    /// One source row: an ISO stamp and the five float columns.
    type Row<'a> = (&'a str, f64, f64, f64, f64, f64);

    /// One batch shaped like a stored OHLCV table: ISO string stamps and float
    /// columns, in file order.
    fn batch(rows: &[Row<'_>]) -> RecordBatch {
        let schema = Schema::new(vec![
            Field::new("timestamp", DataType::Utf8, false),
            Field::new("open", DataType::Float64, false),
            Field::new("high", DataType::Float64, false),
            Field::new("low", DataType::Float64, false),
            Field::new("close", DataType::Float64, false),
            Field::new("volume", DataType::Float64, false),
        ]);
        let column = |pick: fn(&Row<'_>) -> f64| {
            Arc::new(rows.iter().map(pick).collect::<Float64Array>()) as _
        };
        RecordBatch::try_new(
            Arc::new(schema),
            vec![
                Arc::new(rows.iter().map(|row| Some(row.0)).collect::<StringArray>()) as _,
                column(|row| row.1),
                column(|row| row.2),
                column(|row| row.3),
                column(|row| row.4),
                column(|row| row.5),
            ],
        )
        .unwrap()
    }

    /// `xniusd_30m` holds TWO rows for every slot and `ethusd_1m`, `btc_1m` and
    /// `ukoil_1m` hold a few hundred repeats each. Python reduces a bucket to
    /// first open, max high, min low, last close and summed volume, and this
    /// must do the same -- a second bar at one stamp steps every strategy twice
    /// at that instant, which erased `action_timestamp` and booked `xniusd:cci`
    /// half an hour late on both legs.
    #[test]
    fn rows_sharing_a_timestamp_become_one_bar() {
        let bars = ohlcv_bars(
            &[batch(&[
                (
                    "2025-01-06T08:30:00.000000Z",
                    15_339.02,
                    15_359.99,
                    15_278.32,
                    15_307.32,
                    7.0,
                ),
                (
                    "2025-01-06T08:30:00.000000Z",
                    15_322.64,
                    15_343.91,
                    15_262.32,
                    15_291.24,
                    5.0,
                ),
                (
                    "2025-01-06T09:00:00.000000Z",
                    15_307.81,
                    15_320.41,
                    15_201.62,
                    15_247.20,
                    3.0,
                ),
            ])],
            "volume",
        )
        .unwrap();

        assert_eq!(bars.len(), 2, "the duplicated slot is one bar");
        assert_eq!(bars[0].ts, 1_736_152_200);
        assert_eq!(bars[0].open, 15_339.02, "the FIRST row's open");
        assert_eq!(bars[0].high, 15_359.99, "the higher high");
        assert_eq!(bars[0].low, 15_262.32, "the lower low");
        assert_eq!(bars[0].close, 15_291.24, "the LAST row's close");
        assert_eq!(bars[0].volume, 12.0, "both rows' volume");
        assert_eq!(bars[1].ts, 1_736_154_000);
    }
}
