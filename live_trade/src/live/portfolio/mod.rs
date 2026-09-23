use crate::{
    backtest::{
        format_ts,
        prepare::{
            latest_completed_bookmap_minute, latest_completed_ohlcv_minute,
            load_live_bookmap_minute, load_live_level_two_history, load_live_ohlcv_history,
            load_live_ohlcv_minute,
        },
        types::{Action, Bar, Side, Strategy},
    },
    database::{Database, LiveCloseRequest, LiveStrategyTarget, StrategyPosition},
    state::AppState,
    strategies::idk::exness_combined::{self, ExnessCombined, Sleeve},
};
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// The default market, and the one the runtime's NQ-specific plumbing names.
const SYMBOL: &str = "nq";
/// ETHUSD, named because the runtime tests and the stream ranking both single
/// it out: it is the one market in the book that prints every calendar day.
#[cfg_attr(
    not(test),
    allow(
        dead_code,
        reason = "the tests are its only reader since the warm-up calendar moved to the sleeve"
    )
)]
const ETHUSD_SYMBOL: &str = "ethusd";

/// How much benchmark history the alignment ring keeps, in minutes.
///
/// The lookup only ever reaches back to the start of the current 30-minute slot,
/// so a few hours is already generous; it exists so a benchmark feed that stalls
/// briefly still has something to align against rather than dropping the sleeve
/// silent.
const BENCHMARK_RING_MINUTES: usize = 240;

/// A live stream is identified by both market and source.  NQ is the one
/// market where that distinction is load-bearing: the OHLCV continuum is
/// ratio-adjusted while the Bookmap stream is raw traded price.  Treating both
/// as merely `nq` makes Drift VWAP jump onto OFI's price basis as soon as the
/// two strategies run together ([[nq-has-two-incompatible-price-series]]).
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum LiveFeed {
    Ohlcv,
    LevelTwo,
}
mod routing;
mod slot;

#[cfg(test)]
mod go_live_tests;
#[cfg(test)]
mod tests;

pub use routing::{
    LIVE_STRATEGIES, benchmark_pairs, required_symbol, supports, symbol_matches_strategy,
};

use routing::*;
use slot::{BlockReason, LiveSlot, PendingExit, normalize_history};

#[derive(Clone, Default)]
pub struct PortfolioStatusStore(Arc<Mutex<PortfolioStatus>>);

#[derive(Clone, serde::Serialize)]
pub struct PortfolioStatus {
    pub phase: String,
    pub last_minute: i64,
    pub strategies: usize,
    pub detail: String,
    /// Strategies that have stopped taking entries. This used to exist only as
    /// a log line, so `ofi_momentum` sat blocked for three and a half hours on
    /// 2026-07-28 and the evidence was gone by the time anyone looked.
    pub blocked: Vec<String>,
}

impl Default for PortfolioStatus {
    fn default() -> Self {
        Self {
            phase: "starting".into(),
            last_minute: 0,
            strategies: 0,
            detail: String::new(),
            blocked: Vec::new(),
        }
    }
}

impl PortfolioStatusStore {
    pub fn snapshot(&self) -> PortfolioStatus {
        self.0.lock().expect("portfolio status poisoned").clone()
    }

    fn set(&self, phase: &str, last_minute: i64, strategies: usize, detail: impl Into<String>) {
        let mut status = self.0.lock().expect("portfolio status poisoned");
        // `blocked` outlives a phase change: it is cleared only by the runtime
        // reporting which strategies are actually blocked.
        let blocked = std::mem::take(&mut status.blocked);
        *status = PortfolioStatus {
            phase: phase.into(),
            last_minute,
            strategies,
            detail: detail.into(),
            blocked,
        };
    }

    fn set_blocked(&self, blocked: Vec<String>) {
        self.0.lock().expect("portfolio status poisoned").blocked = blocked;
    }
}

pub fn spawn(state: AppState) {
    actix_web::rt::spawn(async move {
        loop {
            if let Err(error) = run(state.clone()).await {
                state.portfolio_status.set("error", 0, 0, error.to_string());
                tracing::error!(%error, "multi-strategy live runtime stopped; retrying");
            }
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    });
}

async fn run(state: AppState) -> Result<(), crate::error::ApiError> {
    let initial_targets = state.db.live_strategy_targets().await?;
    let warmup = WarmupPlan::for_targets(&initial_targets);
    if warmup.strategies.is_empty() {
        state
            .portfolio_status
            .set("ready", 0, 0, "no active live strategies");
        return Ok(());
    }
    state.portfolio_status.set(
        "waiting_for_feed",
        0,
        0,
        "waiting for completed active-market data",
    );
    let Some(mut latest) = wait_for_completed_minute(&state, &initial_targets).await else {
        return Ok(());
    };
    state
        .portfolio_status
        .set("warming", latest, 0, "replaying strategy history");
    let to = crate::backtest::data::format_day(latest.div_euclid(86_400));
    tracing::info!(markets = warmup.calendar_days.len(), %to, "warming active strategies");
    let mut level_two_history = Vec::new();
    let mut ohlcv_history = Vec::new();
    if let Some(from) = warmup.from(SYMBOL, latest) {
        tracing::info!(symbol = SYMBOL, %from, %to, "loading active-market warm-up");
        level_two_history = load_live_level_two_history(&state.store, SYMBOL, &from, &to).await?;
        normalize_history(&mut level_two_history, latest);
        if initial_targets.iter().any(|target| {
            supports(&target.strategy)
                && strategy_symbol(&target.strategy) == SYMBOL
                && strategy_feed(&target.strategy) == LiveFeed::Ohlcv
        }) {
            ohlcv_history = load_live_ohlcv_history(&state.store, SYMBOL, &from, &to).await?;
            normalize_history(&mut ohlcv_history, latest);
        }
    }
    let mut market_histories = HashMap::new();
    let mut market_latest = HashMap::new();
    // Every market the book trades except NQ, which has its own level-two and
    // OHLCV histories loaded above.
    for symbol in ohlcv_markets() {
        if !warmup.calendar_days.contains_key(symbol) {
            continue;
        }
        let completed = latest_completed_ohlcv_minute(&state.store, symbol)
            .await?
            .unwrap_or(latest);
        let from = warmup
            .from(symbol, completed)
            .expect("checked active warm-up market");
        let market_to = crate::backtest::data::format_day(completed.div_euclid(86_400));
        tracing::info!(%symbol, %from, to = %market_to, "loading active-market warm-up");
        let mut history = load_live_ohlcv_history(&state.store, symbol, &from, &market_to).await?;
        normalize_history(&mut history, completed);
        market_histories.insert(symbol, history);
        market_latest.insert(symbol, completed);
    }
    // THE SECOND MARKET, for any sleeve that reads one. Loaded after the markets
    // themselves so the join has both sides, and carried onto the warm-up bars
    // exactly as `PreferredData::OhlcvWithBenchmark` does for a backtest --
    // otherwise `ethusd:idio_break` replays its whole warm-up with no benchmark
    // and reaches the live loop with a cold channel.
    let mut benchmark_closes: HashMap<&'static str, std::collections::VecDeque<(i64, f64)>> =
        HashMap::new();
    let mut benchmark_latest: HashMap<&'static str, i64> = HashMap::new();
    for (market, benchmark) in benchmark_markets() {
        if !market_histories.contains_key(market) {
            continue;
        }
        let completed = latest_completed_ohlcv_minute(&state.store, benchmark)
            .await?
            .unwrap_or(latest);
        let from = warmup
            .from(market, completed)
            .unwrap_or_else(|| crate::backtest::data::format_day(completed.div_euclid(86_400)));
        let market_to = crate::backtest::data::format_day(completed.div_euclid(86_400));
        tracing::info!(%market, %benchmark, %from, to = %market_to, "loading benchmark warm-up");
        let mut history =
            load_live_ohlcv_history(&state.store, benchmark, &from, &market_to).await?;
        normalize_history(&mut history, completed);
        if let Some(bars) = market_histories.get_mut(market) {
            crate::backtest::prepare::join_benchmark(bars, &history);
        }
        let ring = history
            .iter()
            .rev()
            .take(BENCHMARK_RING_MINUTES)
            .map(|bar| (bar.ts, bar.close))
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        benchmark_closes.insert(benchmark, ring);
        benchmark_latest.insert(benchmark, completed);
    }
    let mut level_two_latest = level_two_history.last().map(|bar| bar.ts).unwrap_or(latest);
    let mut nq_ohlcv_latest = ohlcv_history.last().map(|bar| bar.ts).unwrap_or(latest);
    latest = latest.max(level_two_latest).max(nq_ohlcv_latest);
    let mut runtime = PortfolioRuntime {
        db: state.db.clone(),
        slots: HashMap::new(),
        level_two_history,
        ohlcv_history,
        market_histories,
        market_latest,
        benchmark_closes,
        benchmark_latest,
        warm_strategies: warmup.strategies,
        // Learned from the first bar. Until then the clock-driven close simply
        // does not fire, which is the right answer for a runtime that has not
        // yet been told what time New York thinks it is.
        ny_skew_seconds: None,
    };
    if runtime.refresh_targets(latest).await? {
        return Err(crate::error::ApiError::Internal(
            "active strategy warm-up requirements changed during startup".into(),
        ));
    }
    state
        .portfolio_status
        .set("ready", latest, runtime.slots.len(), "");
    tracing::info!(
        strategies = runtime.slots.len(),
        last_minute = latest,
        "multi-strategy live runtime ready"
    );

    // 200ms, NOT 500, AND NOT 100 EITHER.
    //
    // This is the last wait between the feed daemon writing a bar and the
    // strategy seeing it, and it is spent on average half the interval -- 250ms
    // at 500. Against a ~2.5s fetch that is small, but it is the cheapest
    // remaining millisecond on the entry path, so it is worth taking.
    //
    // It does not go lower because a tick is not free: every market's watermark
    // is a `bounds` call, which opens each file of the table and walks its
    // row-group statistics. That is footer work rather than a decode, but at
    // seven markets it already runs fourteen times a second at 500ms and would
    // run seventy at 100 -- on the same host as the terminal it executes
    // through. 200 buys most of the latency for 2.5x the metadata work instead
    // of 5x.
    let mut market = tokio::time::interval(Duration::from_millis(200));
    let mut refresh_ticks = 0u8;
    let mut feed_last_advance = HashMap::from([
        ((SYMBOL, LiveFeed::LevelTwo), Instant::now()),
        ((SYMBOL, LiveFeed::Ohlcv), Instant::now()),
    ]);
    let mut feed_watchdog_fired = HashSet::new();
    loop {
        market.tick().await;
        let mut ready_bars: Vec<(&'static str, LiveFeed, Bar)> = Vec::new();
        let mut completed_watermarks: HashMap<(&'static str, LiveFeed), i64> = HashMap::new();
        // ONCE PER TICK, NOT ONCE PER MARKET. It is a small file read, and the
        // answer is a property of the daemon rather than of any one market, so
        // reading it seven times would be seven times the syscalls for the same
        // boolean. See `feed_daemon_is_polling`.
        let daemon_polling = feed_daemon_is_polling(&state.store);
        refresh_ticks = refresh_ticks.wrapping_add(1);
        if refresh_ticks.is_multiple_of(4) {
            match runtime.refresh_targets(latest).await {
                Ok(false) => {
                    // Do not hide an NQ watchdog alarm merely because account
                    // targets refreshed successfully. Recovery below is what
                    // changes the phase back to ready.
                    if state.portfolio_status.snapshot().phase != "feed_stale" {
                        state
                            .portfolio_status
                            .set("ready", latest, runtime.slots.len(), "");
                    }
                }
                Ok(true) => {
                    return Err(crate::error::ApiError::Internal(
                        "active strategy set changed; restarting with its required warm-up".into(),
                    ));
                }
                Err(error) => {
                    tracing::warn!(%error, "unable to refresh live strategy targets");
                }
            }
        }

        // BENCHMARK FEEDS FIRST, so a market that reads one has its second
        // series in hand before its own bars are built. A benchmark is never
        // traded and has no slot, so it needs no watchdog and no flatten -- it
        // is a price the alignment reads and nothing more.
        for (market, benchmark) in benchmark_markets() {
            if !runtime.needs_market(market) {
                continue;
            }
            let mut cursor = runtime
                .benchmark_latest
                .get(benchmark)
                .copied()
                .unwrap_or(latest);
            match latest_completed_ohlcv_minute(&state.store, benchmark).await {
                Ok(Some(completed)) => {
                    while cursor < completed {
                        let minute = cursor + market_step(benchmark);
                        match load_live_ohlcv_minute(&state.store, benchmark, minute).await {
                            Ok(Some(bar)) => {
                                runtime.push_benchmark(benchmark, bar.ts, bar.close);
                                cursor = minute;
                            }
                            // Same hole rule as a traded market: a watermark can
                            // become visible just before its own bar, and
                            // stepping across the gap would skip that minute for
                            // good.
                            Ok(None) => break,
                            Err(error) => {
                                tracing::warn!(%error, minute, benchmark, "unable to load benchmark minute");
                                break;
                            }
                        }
                    }
                    runtime.benchmark_latest.insert(benchmark, cursor);
                }
                Ok(None) => {}
                Err(error) => {
                    tracing::warn!(%error, benchmark, "unable to read benchmark watermark")
                }
            }
        }

        // Secondary markets advance before the NQ watchdog is evaluated. The
        // NQ branches below may deliberately continue while Bookmap is absent
        // or stale; neither condition may pause independent OHLCV strategies.
        for symbol in ohlcv_markets() {
            if !runtime.needs_market(symbol) {
                continue;
            }
            let mut market_latest = runtime.market_latest.get(symbol).copied().unwrap_or(latest);
            match latest_completed_ohlcv_minute(&state.store, symbol).await {
                Ok(Some(completed)) => {
                    let completed = release_settled_bar(completed, symbol, runtime.store_now());
                    completed_watermarks.insert((symbol, LiveFeed::Ohlcv), completed);
                    while market_latest < completed {
                        let minute = market_latest + market_step(symbol);
                        match load_live_ohlcv_minute(&state.store, symbol, minute).await {
                            Ok(Some(mut bar)) => {
                                runtime.attach_benchmark(symbol, &mut bar);
                                ready_bars.push((symbol, LiveFeed::Ohlcv, bar));
                                feed_last_advance.insert((symbol, LiveFeed::Ohlcv), Instant::now());
                                feed_watchdog_fired.remove(&(symbol, LiveFeed::Ohlcv));
                                market_latest = minute;
                            }
                            // A HOLE. TWO KINDS, AND THEY MUST BE TOLD APART.
                            //
                            // A watermark may become visible just before its
                            // individual bar, so a hole at the very front of the
                            // feed is a bar that has not landed YET. Moving the
                            // cursor across that one permanently skipped a bar
                            // live while a later backtest loaded it normally,
                            // which is why this used to `break` unconditionally.
                            //
                            // But a minute the vendor never printed never
                            // arrives, and `break` without advancing means the
                            // next poll retries the same minute and breaks
                            // again -- FOREVER. On 2026-09-07 usdjpy printed
                            // 13:00-13:14 and no 13:15; the cursor jammed at
                            // 13:14, the 13:00 candle never rolled, and because
                            // this family sets `flattens_itself()` the runtime's
                            // own flatten is skipped too. Two positions sat open
                            // five hours past a 13:00 session close with no exit
                            // logic left alive. Sparse minutes are normal here:
                            // the feed daemon drops a market to a five-minute
                            // sweep once it leaves its session, which is exactly
                            // when a session-end flatten still has to fire.
                            //
                            // So: wait on a hole that is still near the
                            // watermark, step over one the feed has already
                            // moved past.
                            Ok(None) => {
                                if completed - minute > LATE_BAR_GRACE_SECONDS {
                                    market_latest = minute;
                                    continue;
                                }
                                break;
                            }
                            Err(error) => {
                                tracing::warn!(%error, minute, symbol, "unable to load completed market minute");
                                break;
                            }
                        }
                    }
                    runtime.market_latest.insert(symbol, market_latest);
                }
                Ok(None) => {}
                Err(error) => tracing::warn!(%error, symbol, "unable to read market watermark"),
            }
            let key = (symbol, LiveFeed::Ohlcv);
            let stale = feed_last_advance
                .entry(key)
                .or_insert_with(Instant::now)
                .elapsed()
                >= effective_feed_stale_after(symbol, daemon_polling);
            if stale
                && runtime.has_unmanaged_positions(symbol, LiveFeed::Ohlcv)
                && feed_watchdog_fired.insert(key)
            {
                runtime
                    .emergency_flatten(
                        symbol,
                        Some(LiveFeed::Ohlcv),
                        "OHLCV feed stopped advancing",
                    )
                    .await;
                state.portfolio_status.set(
                    "feed_stale",
                    latest,
                    runtime.slots.len(),
                    format!("{symbol} OHLCV feed stopped; managed positions were flattened"),
                );
            } else if !stale {
                runtime
                    .clear_feed_blocks(symbol, LiveFeed::Ohlcv, latest)
                    .await;
            }
        }

        if runtime.needs_feed(LiveFeed::Ohlcv) {
            let key = (SYMBOL, LiveFeed::Ohlcv);
            match latest_completed_ohlcv_minute(&state.store, SYMBOL).await {
                Ok(Some(completed)) => {
                    completed_watermarks.insert(key, completed);
                    while nq_ohlcv_latest < completed {
                        let minute = nq_ohlcv_latest + 60;
                        match load_live_ohlcv_minute(&state.store, SYMBOL, minute).await {
                            Ok(Some(bar)) => {
                                ready_bars.push((SYMBOL, LiveFeed::Ohlcv, bar));
                                feed_last_advance.insert(key, Instant::now());
                                feed_watchdog_fired.remove(&key);
                                nq_ohlcv_latest = minute;
                            }
                            Ok(None) => break,
                            Err(error) => {
                                tracing::warn!(%error, minute, "unable to load completed NQ OHLCV minute");
                                break;
                            }
                        }
                    }
                }
                Ok(None) => tracing::warn!("unable to read NQ OHLCV watermark"),
                Err(error) => tracing::warn!(%error, "unable to read NQ OHLCV watermark"),
            }
            let stale = feed_last_advance
                .entry(key)
                .or_insert_with(Instant::now)
                .elapsed()
                >= effective_feed_stale_after(SYMBOL, daemon_polling);
            if stale
                && runtime.has_unmanaged_positions(SYMBOL, LiveFeed::Ohlcv)
                && feed_watchdog_fired.insert(key)
            {
                runtime
                    .emergency_flatten(
                        SYMBOL,
                        Some(LiveFeed::Ohlcv),
                        "NQ OHLCV feed stopped advancing",
                    )
                    .await;
                state.portfolio_status.set(
                    "feed_stale",
                    latest,
                    runtime.slots.len(),
                    "NQ OHLCV feed stopped; managed positions were flattened",
                );
            } else if !stale {
                runtime
                    .clear_feed_blocks(SYMBOL, LiveFeed::Ohlcv, latest)
                    .await;
            }
        }

        if runtime.needs_feed(LiveFeed::LevelTwo) {
            let key = (SYMBOL, LiveFeed::LevelTwo);
            match latest_completed_bookmap_minute(&state.store, SYMBOL).await {
                Ok(Some(completed)) => {
                    completed_watermarks.insert(key, completed);
                    while level_two_latest < completed {
                        let minute = level_two_latest + 60;
                        match load_live_bookmap_minute(&state.store, SYMBOL, minute).await {
                            Ok(Some(bar)) => {
                                ready_bars.push((SYMBOL, LiveFeed::LevelTwo, bar));
                                feed_last_advance.insert(key, Instant::now());
                                feed_watchdog_fired.remove(&key);
                                level_two_latest = minute;
                            }
                            Ok(None) => break,
                            Err(error) => {
                                tracing::warn!(%error, minute, "unable to load completed Bookmap minute");
                                break;
                            }
                        }
                    }
                }
                Ok(None) => tracing::warn!("unable to read Bookmap L2 watermark"),
                Err(error) => {
                    tracing::warn!(%error, "unable to read Bookmap L2 watermark");
                }
            }
            let stale = feed_last_advance
                .entry(key)
                .or_insert_with(Instant::now)
                .elapsed()
                >= effective_feed_stale_after(SYMBOL, daemon_polling);
            if stale
                && runtime.has_unmanaged_positions(SYMBOL, LiveFeed::LevelTwo)
                && feed_watchdog_fired.insert(key)
            {
                runtime
                    .emergency_flatten(
                        SYMBOL,
                        Some(LiveFeed::LevelTwo),
                        "Bookmap L2 feed stopped advancing",
                    )
                    .await;
                state.portfolio_status.set(
                    "feed_stale",
                    latest,
                    runtime.slots.len(),
                    "Bookmap L2 feed stopped; managed positions were flattened",
                );
            } else if !stale {
                runtime
                    .clear_feed_blocks(SYMBOL, LiveFeed::LevelTwo, latest)
                    .await;
            }
        }
        ready_bars.sort_by_key(|(symbol, feed, bar)| {
            (
                bar.ts + market_step(symbol),
                live_stream_rank(symbol, *feed),
            )
        });
        for (symbol, feed, bar) in ready_bars {
            let source_latest = if symbol == SYMBOL && feed == LiveFeed::LevelTwo {
                level_two_latest
            } else if symbol == SYMBOL {
                nq_ohlcv_latest
            } else {
                runtime.market_latest.get(symbol).copied().unwrap_or(bar.ts)
            };
            // Replay missing bars to keep indicators and existing-position
            // exits correct, but never open a market order from an old signal.
            // Only the newest completed bar of that exact source may add risk.
            runtime
                .on_bar(
                    symbol,
                    feed,
                    bar,
                    bar.ts == source_latest
                        && completed_watermarks.get(&(symbol, feed)) == Some(&source_latest),
                )
                .await;
        }
        // AFTER the bars, so a strategy that DID get its bar this tick has
        // already emitted its own exit and has nothing left for this to find.
        // Runs every tick rather than on a bar, because the case it exists for
        // is the market that has stopped producing them.
        runtime.flatten_overdue_sessions().await;
        latest = latest.max(level_two_latest).max(nq_ohlcv_latest);
        state.portfolio_status.set_blocked(runtime.blocked_names());
        if state.portfolio_status.snapshot().phase != "feed_stale" {
            state
                .portfolio_status
                .set("ready", latest, runtime.slots.len(), "");
        }
    }
}

async fn wait_for_completed_minute(
    state: &AppState,
    targets: &[LiveStrategyTarget],
) -> Option<i64> {
    let needs_bookmap = targets.iter().any(|target| {
        supports(&target.strategy)
            && strategy_symbol(&target.strategy) == SYMBOL
            && strategy_feed(&target.strategy) == LiveFeed::LevelTwo
    });
    let needs_nq_ohlcv = targets.iter().any(|target| {
        supports(&target.strategy)
            && strategy_symbol(&target.strategy) == SYMBOL
            && strategy_feed(&target.strategy) == LiveFeed::Ohlcv
    });
    let ohlcv_markets: HashSet<&str> = targets
        .iter()
        .filter(|target| supports(&target.strategy))
        .map(|target| strategy_symbol(&target.strategy))
        .filter(|symbol| *symbol != SYMBOL)
        .collect();
    loop {
        let mut ready = true;
        let mut latest = None;
        if needs_bookmap {
            match latest_completed_bookmap_minute(&state.store, SYMBOL).await {
                Ok(Some(minute)) => {
                    latest = Some(latest.map_or(minute, |known: i64| known.max(minute)))
                }
                Ok(None) => {
                    ready = false;
                    tracing::warn!("waiting for completed Bookmap L2 minute");
                }
                Err(error) => {
                    ready = false;
                    tracing::warn!(%error, "waiting for Bookmap L2 features");
                }
            }
        }
        if needs_nq_ohlcv {
            match latest_completed_ohlcv_minute(&state.store, SYMBOL).await {
                Ok(Some(minute)) => {
                    latest = Some(latest.map_or(minute, |known: i64| known.max(minute)))
                }
                Ok(None) => {
                    ready = false;
                    tracing::warn!(symbol = SYMBOL, "waiting for completed OHLCV minute");
                }
                Err(error) => {
                    ready = false;
                    tracing::warn!(symbol = SYMBOL, %error, "waiting for OHLCV data");
                }
            }
        }
        for symbol in &ohlcv_markets {
            match latest_completed_ohlcv_minute(&state.store, symbol).await {
                Ok(Some(minute)) => {
                    latest = Some(latest.map_or(minute, |known: i64| known.max(minute)))
                }
                Ok(None) => {
                    ready = false;
                    tracing::warn!(%symbol, "waiting for completed OHLCV minute");
                }
                Err(error) => {
                    ready = false;
                    tracing::warn!(%symbol, %error, "waiting for OHLCV data");
                }
            }
        }
        if ready && latest.is_some() {
            return latest;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

struct PortfolioRuntime {
    db: Database,
    slots: HashMap<i64, LiveSlot>,
    level_two_history: Vec<Bar>,
    ohlcv_history: Vec<Bar>,
    market_histories: HashMap<&'static str, Vec<Bar>>,
    market_latest: HashMap<&'static str, i64>,
    /// Recent `(minute, close)` of each BENCHMARK market, newest last.
    ///
    /// `ind.align` carries the last benchmark close at or before a bar onto that
    /// bar, so a lookup needs history rather than only the newest reading: the
    /// benchmark's feed can run ahead of the market's, and taking whatever it
    /// last printed would then be a close from the future. The ring is trimmed
    /// to `BENCHMARK_RING_MINUTES`, which is far more than the alignment can
    /// reach back for.
    benchmark_closes: HashMap<&'static str, std::collections::VecDeque<(i64, f64)>>,
    benchmark_latest: HashMap<&'static str, i64>,
    /// Strategy kinds covered by the startup history. Activating a strategy
    /// with a longer or different market requirement restarts the runtime so it
    /// can warm correctly instead of silently constructing it from an empty
    /// slice.
    warm_strategies: HashSet<String>,
    /// Seconds to add to real UTC to get New York wall clock, LEARNED FROM THE
    /// BARS rather than from a timezone database this process does not have.
    ///
    /// WHY IT IS LEARNED AND NOT COMPUTED. Every bar table stores New York wall
    /// clock, and the crate has no `chrono-tz`, so the only honest way to know
    /// what New York thinks the time is has been to read the newest bar. That
    /// works until the bars stop, which is exactly when a session close still
    /// has to be honoured.
    ///
    /// So the offset is taken from each arriving bar -- its close in NY terms
    /// minus real UTC now, rounded to the hour, which absorbs both the feed lag
    /// and any sub-hour jitter -- and then SURVIVES the feed. Re-learning on
    /// every bar means daylight saving is picked up on the first bar after the
    /// change rather than encoded as a rule that a law can invalidate.
    ///
    /// `None` until the first bar. A runtime that has never seen one cannot
    /// know the time and does not guess.
    ny_skew_seconds: Option<i64>,
}

impl PortfolioRuntime {
    async fn refresh_targets(&mut self, latest: i64) -> Result<bool, crate::error::ApiError> {
        let targets = self.db.live_strategy_targets().await?;
        if targets.iter().any(|target| {
            supports(&target.strategy) && !self.warm_strategies.contains(&target.strategy)
        }) {
            return Ok(true);
        }
        let active: HashSet<i64> = targets
            .iter()
            .filter(|target| supports(&target.strategy))
            .map(|target| target.account_strategy_id)
            .collect();
        self.slots.retain(|id, _| active.contains(id));

        for mut target in targets {
            if !supports(&target.strategy) {
                continue;
            }
            if let Some(slot) = self.slots.get_mut(&target.account_strategy_id) {
                slot.target.connected = target.connected;
                slot.target.equity = target.equity;
                // THE ANSWER IS ABOUT RIGHT NOW, SO IT IS ALLOWED TO CHANGE
                // ITS MIND. This asks whether the broker's open positions match
                // the durable rows THIS INSTANT, and the two writers race: the
                // durable row lands when an execution command completes, the
                // bridge snapshot arrives on its own schedule, so a poll
                // between a fill and its write is a mismatch that is gone a
                // moment later. Recording that as a permanent block retired
                // `jp225_volume_thrust` and `jp225_volatility_breakout` at
                // 02:42Z on 2026-09-09, three hours before the operator's
                // session, and neither took another entry that day.
                //
                // ONLY ASKED WHILE CONNECTED. A disconnected account has no
                // broker snapshot to compare against, so it cannot clear a
                // block any more than it can raise one -- an earlier draft of
                // this let a disconnect fall into the clearing branch and
                // resume a sleeve on no evidence at all.
                if target.connected {
                    // Reconcile any positions that were closed manually or externally in MT5
                    let missing_tickets = self
                        .db
                        .reconcile_missing_bridge_positions(target.account_strategy_id)
                        .await?;
                    if !missing_tickets.is_empty() {
                        tracing::info!(
                            account_strategy_id = target.account_strategy_id,
                            strategy = target.strategy,
                            count = missing_tickets.len(),
                            "reconciled missing MT5 positions in database"
                        );
                    }

                    // Reconcile any in-memory positions that are no longer open in durable state
                    if !slot.positions.is_empty() {
                        let open_durable = self
                            .db
                            .strategy_positions(target.account_strategy_id)
                            .await?;
                        let open_keys: HashSet<&str> = open_durable
                            .iter()
                            .map(|p| p.position_key.as_str())
                            .collect();
                        let in_memory_keys: Vec<String> = slot.positions.keys().cloned().collect();
                        for key in in_memory_keys {
                            if !open_keys.contains(key.as_str()) {
                                slot.reconcile_closed_position(&key);
                                tracing::info!(
                                    account_strategy_id = target.account_strategy_id,
                                    strategy = target.strategy,
                                    position_key = key,
                                    "position was closed externally; reconciled to flat, ready for next signal"
                                );
                            }
                        }
                    }

                    let mismatch = self
                        .db
                        .strategy_position_mismatch(target.account_strategy_id)
                        .await?;
                    if let Some(next) = ownership_transition(slot.blocked, mismatch) {
                        slot.blocked = next;
                        // ON THE TRANSITION, NOT ON EVERY REFRESH, and to the
                        // DATABASE as well as the console. This runs once every
                        // four 200ms ticks, so a mismatch that persisted printed
                        // roughly 4,500 lines an hour per sleeve and left
                        // nothing behind once the terminal scrolled -- the
                        // server writes no log file, and `live_events` had no
                        // row for it. That is what the operator saw on
                        // 2026-09-09 and read as "it happened many times".
                        let (kind, detail) = if next.is_some() {
                            tracing::error!(
                                account_strategy_id = target.account_strategy_id,
                                strategy = target.strategy,
                                "broker positions do not match durable strategy ownership; entries blocked"
                            );
                            (
                                "blocked",
                                "broker positions do not match durable strategy ownership",
                            )
                        } else {
                            tracing::info!(
                                account_strategy_id = target.account_strategy_id,
                                strategy = target.strategy,
                                "broker positions match durable strategy ownership again; entries resumed"
                            );
                            (
                                "unblocked",
                                "broker positions match durable strategy ownership again",
                            )
                        };
                        self.db
                            .log_live_event(
                                target.account_strategy_id,
                                &target.strategy,
                                kind,
                                "",
                                &format_ts(latest),
                                detail,
                            )
                            .await;
                    }
                }
                continue;
            }
            if target.live_started_at == 0 {
                target.live_started_at = self
                    .db
                    .set_live_strategy_start(target.account_strategy_id, latest)
                    .await?;
            }
            let market = strategy_symbol(&target.strategy);
            // WARM-UP MUST READ THE STREAM THE SLOT WILL BE STEPPED WITH, and
            // the only thing that knows which is `strategy_feed` -- the same
            // function `on_bar` routes with. This used to name two ids by hand,
            // and both were retired on 2026-08-23: `nq_drift_vwap` and
            // `nq_volatility_breakout` matched neither, so they warmed on the
            // raw traded price of the level-two feed and then went live on the
            // back-adjusted `nq_1m` continuum. The two are not one series, and
            // mixing them fabricates a gap at the handover
            // ([[nq-has-two-incompatible-price-series]]).
            let history = if market != SYMBOL {
                self.market_histories
                    .get(market)
                    .map(Vec::as_slice)
                    .unwrap_or(&[])
            } else if strategy_feed(&target.strategy) == LiveFeed::LevelTwo {
                &self.level_two_history
            } else {
                &self.ohlcv_history
            };
            match LiveSlot::warm(target, history, &self.db).await {
                Ok(slot) => {
                    self.slots.insert(slot.target.account_strategy_id, slot);
                }
                Err(error) => tracing::error!(%error, "live strategy warm-up refused"),
            }
        }
        Ok(false)
    }

    /// Steps only the slots that trade this exact `(symbol, feed)` stream.
    ///
    /// Every slot used to receive every bar, which was safe while the runtime
    /// was single-symbol and is a correctness bug the moment it is not: a BTC
    /// strategy stepped with an NQ bar would breakout off the wrong price and
    /// send a real order for it.
    async fn on_bar(&mut self, symbol: &str, feed: LiveFeed, bar: Bar, entries_current: bool) {
        self.observe_clock(symbol, bar);
        if symbol == SYMBOL {
            match feed {
                LiveFeed::LevelTwo => self.level_two_history.push(bar),
                LiveFeed::Ohlcv => self.ohlcv_history.push(bar),
            }
        } else if let Some(history) = self.market_histories.get_mut(symbol) {
            history.push(bar);
        }
        let stepped: Vec<i64> = self
            .slots
            .values()
            .filter(|slot| {
                strategy_symbol(&slot.target.strategy) == symbol
                    && (symbol != SYMBOL || strategy_feed(&slot.target.strategy) == feed)
            })
            .map(|slot| slot.target.account_strategy_id)
            .collect();
        for id in stepped {
            let Some(slot) = self.slots.get_mut(&id) else {
                continue;
            };
            slot.on_bar(&self.db, bar, entries_current).await;
        }
    }

    /// A market is needed exactly when a slot trades it.
    ///
    /// The old book also pulled BTC in as a market nothing traded -- it was the
    /// ETH sleeves' exposure driver and ETHBTC's quote currency. Neither
    /// dependency survives: there is no exposure overlay any more, and every
    /// sleeve here settles in its own profit currency.
    /// Records one benchmark minute, trimming the ring to its bound.
    fn push_benchmark(&mut self, benchmark: &'static str, ts: i64, close: f64) {
        let ring = self.benchmark_closes.entry(benchmark).or_default();
        ring.push_back((ts, close));
        while ring.len() > BENCHMARK_RING_MINUTES {
            ring.pop_front();
        }
    }

    /// `ind.align`: the last benchmark close AT OR BEFORE this bar.
    ///
    /// Never a later one, even when the benchmark's feed has run ahead — that
    /// would hand the sleeve a price from the future, which on a breakout
    /// comparison is the most flattering bug available. A market with no
    /// benchmark, or one whose benchmark has not printed yet, leaves the bar's
    /// `benchmark` at `None` and the sleeve simply does not fire.
    fn attach_benchmark(&self, market: &str, bar: &mut Bar) {
        let Some(benchmark) = benchmark_for(market) else {
            return;
        };
        bar.benchmark = self.benchmark_closes.get(benchmark).and_then(|ring| {
            ring.iter()
                .rev()
                .find(|(ts, _)| *ts <= bar.ts)
                .map(|(_, close)| *close)
        });
    }

    fn needs_market(&self, symbol: &str) -> bool {
        self.slots
            .values()
            .any(|slot| strategy_symbol(&slot.target.strategy) == symbol)
    }

    fn needs_feed(&self, feed: LiveFeed) -> bool {
        self.slots.values().any(|slot| {
            strategy_symbol(&slot.target.strategy) == SYMBOL
                && strategy_feed(&slot.target.strategy) == feed
        })
    }

    /// What minute of the New York day it is, as far as this runtime can tell.
    ///
    /// THERE IS NO WALL CLOCK TO ASK. The process has no timezone database and
    /// the machine it runs on need not be in New York, while every bar table
    /// stores New York wall clock ([[store-stores-ny-wall-clock]]). So the
    /// newest bar ACROSS EVERY STREAM is the clock: whichever market is still
    /// printing says what time it is, and one dead feed cannot stop it.
    ///
    /// Reading only the stalled stream's own last bar would be enough to tell a
    /// scheduled close from a feed that stopped early, but not to notice a feed
    /// that died during a scheduled close and never came back -- its last bar
    /// stays parked outside the session for as long as it is broken. Ten of the
    /// eleven streams keep running in that case, and they are what says the
    /// session has reopened without it.
    fn market_clock_minute(&self) -> Option<i64> {
        [&self.level_two_history, &self.ohlcv_history]
            .into_iter()
            .chain(self.market_histories.values())
            .filter_map(|history| history.last())
            .map(|bar| bar.ts)
            .max()
            .map(|ts| ts.rem_euclid(86_400) / 60)
    }

    /// Whether a stall on this stream would leave a real position unmanaged.
    ///
    /// THE POSITION IS NOT ENOUGH; THE MARKET HAS TO BE OPEN. This asked only
    /// whether any slot on the stream was holding, and a market that closes on
    /// schedule looks exactly like a feed that died: no bar arrives, the 180
    /// second watchdog trips, and `emergency_flatten` sends a real close and
    /// blocks the sleeve until the process restarts.
    ///
    /// EVERY SLEEVE FLATTENS AT ITS OWN SESSION CLOSE NOW, so none holds
    /// anything when its market shuts and the window costs nothing today. It is
    /// kept because the book has already carried a sleeve that did hold:
    /// `jp225:swing_donchian` ran multi-day positions through JP225's two-hour
    /// daily break and its weekend, and without this window every one of them
    /// would have been emergency-flattened at a scheduled close. It left the
    /// book on 2026-08-29; the next sleeve that holds must not have to
    /// rediscover this ([[feed-watchdog-fights-the-swing-sleeve]]).
    ///
    /// The session is judged against `market_clock_minute`, so a feed that dies
    /// during its market's scheduled close is still caught once the OTHER
    /// streams say its session has reopened.
    fn has_unmanaged_positions(&self, symbol: &str, feed: LiveFeed) -> bool {
        let Some(minute) = self.market_clock_minute() else {
            return false;
        };
        self.slots.values().any(|slot| {
            !slot.positions.is_empty()
                && strategy_symbol(&slot.target.strategy) == symbol
                && (symbol != SYMBOL || strategy_feed(&slot.target.strategy) == feed)
                && slot.in_trading_session(minute)
        })
    }

    /// Strategies that have stopped taking entries, plus any that still owe the
    /// broker an exit — both are states a person needs to see, not discover in
    /// a log file days later.
    fn blocked_names(&self) -> Vec<String> {
        let mut names: Vec<String> = self
            .slots
            .values()
            .filter(|slot| slot.blocked.is_some() || !slot.pending_exits.is_empty())
            .map(|slot| match slot.blocked {
                // The reason is shown, because the two need different actions
                // from the reader: an ownership block clears itself once the
                // broker and the durable rows agree, a sticky one waits for a
                // person.
                Some(BlockReason::Ownership) => {
                    format!("{} (ownership)", slot.target.strategy)
                }
                Some(BlockReason::Feed) => format!("{} (feed)", slot.target.strategy),
                Some(BlockReason::Sticky) => slot.target.strategy.clone(),
                None => format!("{} (exit owed)", slot.target.strategy),
            })
            .collect();
        names.sort();
        names
    }

    /// Real UTC now, in seconds. `None` if the host clock is before the epoch,
    /// which is a broken machine rather than a case to reason about.
    fn utc_now(&self) -> Option<i64> {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .ok()
            .map(|since| since.as_secs() as i64)
    }

    /// Now, on the clock the STORE is written in.
    ///
    /// Every bar table holds New York wall clock relabelled UTC, so a timestamp
    /// from the store cannot be compared against `utc_now` directly. The offset
    /// is already learned from arriving bars by `observe_clock`; this is the
    /// same conversion applied the other way, and it is `None` until some bar
    /// has taught it -- which is the correct answer at start-up, when nothing
    /// may be assumed about the clock.
    fn store_now(&self) -> Option<i64> {
        Some(self.utc_now()? + self.ny_skew_seconds?)
    }

    /// Re-learn the New York offset from a bar that has just arrived.
    ///
    /// The bar's CLOSE is the instant it describes, and it reaches here a few
    /// seconds later, so the raw difference is the offset minus the feed lag.
    /// Rounding to the hour discards that lag -- and any clock drift short of
    /// half an hour -- while keeping the only part that matters, which of the
    /// two US offsets is currently in force.
    fn observe_clock(&mut self, symbol: &str, bar: Bar) {
        let Some(utc) = self.utc_now() else { return };
        let raw = bar.ts + market_step(symbol) - utc;
        self.ny_skew_seconds = Some((raw as f64 / 3_600.0).round() as i64 * 3_600);
    }

    /// Seconds into the New York day, from THIS PROCESS'S OWN CLOCK.
    ///
    /// Unlike `market_clock_minute` this does not need any market to be
    /// printing: once the offset has been learned the host clock carries it,
    /// which is what lets a session close be honoured while the vendor is dark.
    fn ny_second_of_day(&self) -> Option<i64> {
        let (utc, skew) = (self.utc_now()?, self.ny_skew_seconds?);
        Some((utc + skew).rem_euclid(86_400))
    }

    /// Close everything still open more than one bar past its own session
    /// close, using the clock rather than that market's own bars.
    ///
    /// THIS IS THE ONLY EXIT IN THE RUNTIME THAT A DEAD FEED CANNOT SILENCE.
    /// The strategy's own flatten, the generic `in_session_end_window` one and
    /// every stop are driven by a bar ARRIVING on the market being exited, so a
    /// vendor that stops publishing switches all of them off together and the
    /// broker leg stays open with nobody managing it -- two USDJPY sleeves sat
    /// nearly six hours past a 13:00 close that way on 2026-09-07. Here the
    /// clock comes from `market_clock_minute`, the newest bar across EVERY
    /// stream, so the market that went quiet is not the one being asked the
    /// time.
    ///
    /// IT DOES NOT BLOCK THE SLEEVE, and that is the difference from the feed
    /// watchdog. A stalled feed means the runtime no longer knows what the
    /// market is doing, so refusing further entries until a person looks is
    /// right. Being past a session close is a NORMAL daily event that this
    /// merely finishes; blocking on it would retire a sleeve for good the first
    /// time a vendor was late.
    async fn flatten_overdue_sessions(&mut self) {
        let Some(second) = self.ny_second_of_day() else {
            return;
        };
        let minute = second / 60;
        let mut markets: Vec<String> = self
            .slots
            .values()
            .filter(|slot| slot.session_overdue(second))
            .map(|slot| strategy_symbol(&slot.target.strategy).to_string())
            .collect();
        markets.sort();
        markets.dedup();
        for market in markets {
            tracing::warn!(
                market,
                minute,
                "position held past its session close; flattening on the clock"
            );
            self.forced_flatten(&market, None, "session_end_overdue", false)
                .await;
        }
    }

    async fn emergency_flatten(&mut self, market: &str, feed: Option<LiveFeed>, reason: &str) {
        self.forced_flatten(market, feed, reason, true).await;
    }

    /// Hand back a sleeve the feed watchdog stopped, once its market is
    /// printing again.
    ///
    /// THE OTHER HALF OF `BlockReason::Feed`. The watchdog's claim is "the
    /// runtime cannot see this market", and a bar arriving is the proof that it
    /// can -- the most directly re-checkable thing any block asserts. Without
    /// this the flatten was permanent for the session: on 2026-09-09 Dukascopy
    /// went quiet for three minutes and `eurjpy_gated_orb` was retired for the
    /// rest of the day over it.
    ///
    /// ONLY `Feed`. A sticky block is a past event a live bar cannot unmake,
    /// and an ownership block is the other check's business.
    ///
    /// Called on every tick the feed is NOT stale, so it must cost nothing in
    /// the normal case: the `any` below is the whole price when nothing is
    /// blocked.
    async fn clear_feed_blocks(&mut self, market: &str, feed: LiveFeed, latest: i64) {
        if !self.slots.values().any(|slot| {
            slot.blocked == Some(BlockReason::Feed)
                && strategy_symbol(&slot.target.strategy) == market
                && (market != SYMBOL || strategy_feed(&slot.target.strategy) == feed)
        }) {
            return;
        }
        let mut resumed = Vec::new();
        for slot in self.slots.values_mut() {
            if slot.blocked != Some(BlockReason::Feed)
                || strategy_symbol(&slot.target.strategy) != market
                || (market == SYMBOL && strategy_feed(&slot.target.strategy) != feed)
            {
                continue;
            }
            slot.blocked = None;
            resumed.push((
                slot.target.account_strategy_id,
                slot.target.strategy.clone(),
            ));
        }
        for (id, strategy) in resumed {
            tracing::info!(
                account_strategy_id = id,
                %strategy,
                %market,
                "feed is advancing again; entries resumed"
            );
            self.db
                .log_live_event(
                    id,
                    &strategy,
                    "unblocked",
                    "",
                    &format_ts(latest),
                    "feed is advancing again",
                )
                .await;
        }
    }

    /// The shared body. `block` is what separates a watchdog stop -- the runtime
    /// has lost sight of the market and must not trade it again unattended --
    /// from a routine overdue close, which is the day ending as it should.
    async fn forced_flatten(
        &mut self,
        market: &str,
        feed: Option<LiveFeed>,
        reason: &str,
        block: bool,
    ) {
        let history = if market == SYMBOL && feed == Some(LiveFeed::Ohlcv) {
            &self.ohlcv_history
        } else if market == SYMBOL {
            &self.level_two_history
        } else {
            self.market_histories
                .get(market)
                .unwrap_or(&self.level_two_history)
        };
        let timestamp = history
            .last()
            .map(|bar| format_ts(bar.ts + market_step(market)))
            .unwrap_or_default();
        let price = history.last().map(|bar| bar.close).unwrap_or(0.0);
        let mut flattened = false;
        for slot in self.slots.values_mut() {
            if strategy_symbol(&slot.target.strategy) != market {
                continue;
            }
            if market == SYMBOL
                && feed.is_some_and(|wanted| strategy_feed(&slot.target.strategy) != wanted)
            {
                continue;
            }
            if slot.positions.is_empty() {
                continue;
            }
            flattened = true;
            let keys = slot.positions.keys().cloned().collect::<Vec<_>>();
            for key in keys {
                let symbol = match route_symbol(&slot.target.symbol) {
                    Ok(symbol) => symbol,
                    Err(error) => {
                        tracing::error!(%error, "cannot flatten invalid live symbol");
                        continue;
                    }
                };
                if let Err(error) = self
                    .db
                    .enqueue_live_close(&LiveCloseRequest {
                        account_strategy_id: slot.target.account_strategy_id,
                        account_id: slot.target.account_id,
                        strategy: &slot.target.strategy,
                        position_key: &key,
                        symbol,
                        fraction: 1.0,
                        timestamp: &timestamp,
                        price,
                        exit_reason: "emergency_flatten",
                    })
                    .await
                {
                    tracing::error!(%error, position_key = key, "emergency flatten enqueue failed");
                }
                // The command still needs a broker acknowledgement. Retain
                // ownership and let the normal owed-exit loop retry a failed
                // or rejected emergency close.
                slot.pending_exits.insert(
                    key.clone(),
                    PendingExit {
                        price,
                        fraction: 1.0,
                    },
                );
                self.db
                    .log_live_event(
                        slot.target.account_strategy_id,
                        &slot.target.strategy,
                        "emergency_flatten",
                        &key,
                        &timestamp,
                        reason,
                    )
                    .await;
            }
            // NEVER `= block`: an overdue close must not CLEAR a block a
            // watchdog stop already set, or a dead feed would be forgiven by
            // the next ordinary session close.
            //
            // `Feed`, NOT `Sticky`: the only caller that passes `block` is the
            // feed watchdog, and its claim -- "the runtime cannot see this
            // market" -- is disproved by the next bar that arrives. A sticky
            // block outlived the outage by the rest of the trading day.
            // `clear_feed_blocks` is the other half.
            //
            // It must not DOWNGRADE a sticky block that is already there, so
            // only a slot that is not blocked at all takes the new reason.
            if block && slot.blocked.is_none() {
                slot.blocked = Some(BlockReason::Feed);
            }
        }
        if flattened && block {
            tracing::error!(
                reason,
                "live safety watchdog flattened all managed positions"
            );
        }
    }
}
