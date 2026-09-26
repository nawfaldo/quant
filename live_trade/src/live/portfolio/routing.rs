//! Per-strategy static tables: which market a sleeve trades, which feed it
//! reads, how its quantity becomes MT5 lots, and how much history it must be
//! warmed with.
//!
//! Every one of these is keyed on the live strategy id and answered from
//! `Sleeve`, so a backtest and its live twin cannot disagree about the
//! instrument, the lot floor or the warm-up depth.

use super::*;

/// The sleeve a live strategy id names, or `None` for an id the book retired.
pub(super) fn sleeve_of(strategy: &str) -> Option<Sleeve> {
    Sleeve::from_id(strategy)
}

/// Every market the book trades EXCEPT NQ, in `BOOK` order and deduplicated.
///
/// THE EXCLUSION IS VACUOUS SINCE 2026-09-03, when NQ was barred as a symbol,
/// and it is kept because the reason is a property of the FEED rather than of
/// the membership: the runtime loads NQ twice by hand -- once from the level-two
/// feature table and once from `nq_1m` -- and the two are not one series.
/// `nq_1m` is a back-adjusted continuum while the L2 feed is raw traded price,
/// so merging them fabricates a gap ([[nq-has-two-incompatible-price-series]]).
/// A market seated here without that split needs no special case; NQ would need
/// this one again.
pub(super) fn ohlcv_markets() -> Vec<&'static str> {
    let mut markets = Vec::new();
    for sleeve in exness_combined::BOOK {
        let market = sleeve.market();
        if market != SYMBOL && !markets.contains(&market) {
            markets.push(market);
        }
    }
    markets
}

/// Every `(market, benchmark)` pair the book needs a SECOND market's prices for.
///
/// EMPTY SINCE 2026-09-04, and the machinery around it is kept. The one cell
/// that read a second market was `ethusd:idio_break` -- "did ETHUSD break its
/// own channel while BTC stayed inside its own" -- and the decay screen removed
/// it. Nothing else in the book asks a joint question about two markets.
///
/// It stays a function rather than becoming a deleted concept because the
/// failure it guards against is silent: a benchmarked sleeve whose second market
/// is not fetched does not error, it simply never fires, and the live account
/// would then run one sleeve fewer than the backtest with nothing to say so.
pub(super) fn benchmark_markets() -> Vec<(&'static str, &'static str)> {
    Vec::new()
}

/// The same pairs, for callers outside the live runtime.
///
/// `exness_live_book` reports every market that must be FED, and a benchmark
/// market has to be fed even though no sleeve trades it. Exposed rather than
/// duplicated so the market-data daemon and the runtime cannot disagree about
/// which tables have to be current.
pub fn benchmark_pairs() -> Vec<(&'static str, &'static str)> {
    benchmark_markets()
}

/// The benchmark market this market's bars must carry, or `None`.
pub(super) fn benchmark_for(market: &str) -> Option<&'static str> {
    benchmark_markets()
        .into_iter()
        .find(|(owner, _)| *owner == market)
        .map(|(_, benchmark)| benchmark)
}

/// Which feed a live slot is driven from.
///
/// An NQ strategy reads the level-two feature table because a live NQ OHLCV feed
/// is unavailable; every other market runs on OHLCV. NO SLEEVE REACHES THE FIRST
/// ARM since NQ was barred on 2026-09-03, so every live slot is on OHLCV --
/// which `no_live_strategy_reads_the_level_two_feed` asserts as a consequence
/// rather than by deleting the rule.
pub(super) fn strategy_feed(strategy: &str) -> LiveFeed {
    match sleeve_of(strategy) {
        Some(sleeve) if sleeve.market() == SYMBOL => LiveFeed::LevelTwo,
        _ => LiveFeed::Ohlcv,
    }
}

/// How long after the session-end flatten bar the flatten may still fire.
///
/// Only a feed gap over the exact minute should need this, so the window is
/// generous against a stalled feed and still far shorter than any session it
/// guards — which is what stops it wrapping into the session it just closed.
pub(super) const SESSION_END_GRACE_MINUTES: usize = 120;

/// How far behind the feed's own watermark a missing minute has to be before
/// the market cursor steps over it instead of waiting.
///
/// THE TWO FAILURES THIS SITS BETWEEN ARE OPPOSITE. Step over a hole too eagerly
/// and a bar that was merely late is skipped for good, so live silently trades a
/// different series from the backtest. Never step over one and a minute the
/// vendor simply did not print jams the cursor forever, which is worse: the
/// strategy stops receiving bars at all, its candles stop rolling, and every
/// exit it owns -- session flatten included -- stops firing while the broker leg
/// stays open.
///
/// Five minutes is chosen against the feed daemon rather than guessed. In
/// session it writes every minute, so a hole this old cannot be pending. Out of
/// session it sweeps a market every fifth minute, which is precisely the gap
/// that has to be crossed, and the watermark is by then well past it.
pub(super) const LATE_BAR_GRACE_SECONDS: i64 = 300;

/// How long past its own session close a slot may still be holding before the
/// runtime closes it on the clock alone, with no bar from that market required.
///
/// ONE BAR, BECAUSE THAT IS WHERE THE RESEARCH MODEL PUTS THE EXIT.
/// `fill_models.exness` prices a session flatten at `bar_seconds + lag` past
/// the close and does so unconditionally -- 1801.6s for every Dukascopy market
/// -- so this is not a grudging backstop but the same instant the study already
/// assumes. Shorter would cut the exit in front of the model; longer reopens the
/// window this exists to close.
pub(super) const SESSION_OVERDUE_MINUTES: i64 = 30;

/// Seconds past that bar before the clock-driven close fires.
///
/// On a healthy feed the strategy emits its own exit about three seconds into
/// the new slot -- 0.6s vendor publish, ~1s chase, 0.25s poll, ~1s bridge. This
/// is only wide enough to let that land first and avoid a duplicate close; it is
/// deliberately seconds rather than minutes, because every second beyond the
/// model's own 1.6s is time the position is held that the study never charged.
pub(super) const SESSION_OVERDUE_GRACE_SECONDS: i64 = 10;

/// Hand back the bar `latest_completed_ohlcv_minute` is still holding, once the
/// clock proves it finished.
///
/// THAT FUNCTION STEPS BACK ONE WHOLE PERIOD AND IT COSTS A MINUTE OF LATENCY.
/// It returns `max_timestamp - step` because the newest row COULD be a bar still
/// being written -- true of a store a vendor streams into mid-bar, and false of
/// every producer that writes this one: the feed daemon's `write_rows` drops any
/// row at or after the current minute, and `binance_stream_1m` only writes a
/// kline once its `x` flag marks it closed. So the newest row is complete, the
/// step-back buys nothing, and the strategy waits a minute for a bar the store
/// already holds.
///
/// Rather than trust those writers -- there are others on `<symbol>_1m`, and a
/// backfill is easy to add -- this asks the clock. `held` is `newest - step`, so
/// the row behind it spans `[held + step, held + 2 * step)` and has certainly
/// closed once that end is behind us. Where it has, the bar is released; where
/// the clock is unknown, or the row is genuinely still forming, nothing changes
/// and the old conservative answer stands.
///
/// `now` is on the STORE's clock (New York wall time relabelled UTC), not UTC.
pub(super) fn release_settled_bar(held: i64, symbol: &str, now: Option<i64>) -> i64 {
    let step = market_step(symbol);
    match now {
        Some(now) if now >= held + 2 * step => held + step,
        _ => held,
    }
}

/// How old a bar's decision may be, by the clock, and still open a position.
///
/// A market order sent on an old signal is a different trade from the one the
/// backtest took, so a real catch-up -- a restart, a feed outage replayed in one
/// poll -- must still only warm indicators and run exits. But the feed routinely
/// hands over a minute ~70s after it closed (Dukascopy publishes ~18s after the
/// close and the daemon polls once a minute), and one poll late is ~130s. Three
/// minutes admits both and nothing older. The fill model the backtest charges
/// puts an entry 2s after the candle opens, so every second of this is cost the
/// research did not pay -- which is why it is minutes and not the half hour a
/// late entry used to carry.
pub(super) const ENTRY_FRESH_SECONDS: i64 = 180;

/// Whether a bar stamped `bar_ts` is recent enough, at store-clock `now`, for
/// its signal to open a position. Its decision moment is the bar's CLOSE.
pub(super) fn entry_is_fresh(bar_ts: i64, symbol: &str, now: i64) -> bool {
    now - (bar_ts + market_step(symbol)) <= ENTRY_FRESH_SECONDS
}

pub(super) fn market_step(symbol: &str) -> i64 {
    // NOT ALWAYS 60. `xniusd` has no one-minute table -- `xniusd_30m` is its
    // native source. Stepping it by a minute would refetch that 30-minute bar
    // twenty-nine times and hand every copy to the strategy, so the step is read
    // from the same rule the loader resolves its table with.
    crate::backtest::prepare::ohlcv_step(symbol)
}

/// How long a feed may stop advancing before an open position on it is treated
/// as unmanaged.
///
/// A MARKET THAT STOPS FOR LUNCH LOOKS EXACTLY LIKE A DEAD FEED, and nothing
/// here can tell them apart: `has_unmanaged_positions` asks whether the slot is
/// in its trading session, and `derive_session` produces ONE contiguous window
/// per market, so an intraday break sits inside it and reads as open
/// ([[feed-watchdog-fights-the-swing-sleeve]]).
///
/// THIS BOOK HAS NO MARKET THAT NEEDS AN ALLOWANCE, and that is measured rather
/// than assumed. `hk50` did -- it stopped for 61 minutes on 397 of 498 days and
/// 61% of its trades were open across the gap -- and it left the book on
/// 2026-09-07 ([[hk50-stops-for-lunch-inside-its-session]]). The next-gappiest
/// is `jp225` at 92 gaps over 517 days, and 70 of those fall in the hour the
/// session OPENS, where nothing is held yet; the remaining 22 are real outages
/// and are exactly what this is for. Every other market is under 13.
///
/// So a per-market allowance was written and then deleted with the sleeve that
/// needed it. Measure a new market's in-session gaps before seating it: a
/// backtest cannot see this at all.
pub(super) fn feed_stale_after(symbol: &str) -> Duration {
    // A source cannot be stale before one of its own bars could have completed.
    Duration::from_secs((market_step(symbol) + 90).max(180) as u64)
}

/// Processing order for a catch-up, so a backlog is drained deterministically
/// rather than one whole market at a time.
///
/// The level-two NQ stream leads, then its OHLCV twin, then every other market
/// in the order `BOOK` records it.
pub(super) fn live_stream_rank(symbol: &str, feed: LiveFeed) -> usize {
    match (symbol, feed) {
        (SYMBOL, LiveFeed::LevelTwo) => 0,
        (SYMBOL, LiveFeed::Ohlcv) => 1,
        (market, _) => {
            2 + exness_combined::BOOK
                .iter()
                .position(|sleeve| sleeve.market() == market)
                .unwrap_or(exness_combined::BOOK.len())
        }
    }
}

/// The twenty-two sleeves the live runtime can be asked to run.
///
/// Transcribed rather than derived because it is a `const`, and
/// `live_strategies_match_the_book` is the test that keeps it equal to `BOOK`.
/// A name here that `build_strategy` cannot construct is a slot that looks live
/// and silently never fires.
pub const LIVE_STRATEGIES: &[&str] = &[
    "usdjpy_volume_thrust",
    "audusd_zscore",
    "ethusd_confluence",
    "ethusd_volatility_breakout",
    "gbpjpy_trap",
    "eurjpy_two_stage",
    "usdjpy_pullback",
    "ethusd_obv_break",
    "ukoil_level_confluence",
    "eurjpy_gated_orb",
    "usdjpy_aroon",
    "ethusd_break_retest",
    "usdjpy_fracdiff",
    "ethusd_pullback",
    "ethusd_kalman",
    "usdjpy_half_life",
    "ethusd_roofing",
    "ethusd_level_confluence",
    "usdjpy_rvol",
    "ethusd_efficiency",
    "ethusd_cci",
    "ethusd_linreg_trend",
];

/// The market a live strategy trades. Slots are fed only their own symbol's
/// bars, so this is what keeps a JP225 sleeve from being stepped with NQ prices
/// -- the markets share one runtime but never one bar.
///
/// Mirrors `strategies::market_symbol`, which does the same job for the backtest
/// engine; both read `Sleeve::market`, so a backtest and its live twin cannot
/// trade different instruments.
pub(super) fn strategy_symbol(strategy: &str) -> &'static str {
    sleeve_of(strategy).map(Sleeve::market).unwrap_or(SYMBOL)
}

/// What a strategy's `quantity` counts. MT5 orders are denominated in LOTS, and
/// the two families in this directory disagree about what they emit, so every
/// live strategy must declare it here.
///
/// This is not a formality. `Coins` quantities are a number of the base asset --
/// `ethbtc_orb::quantity` says so outright ("a number of ETH") -- and one lot is
/// `contract_size` of them. Sending a coin count as a lot volume is only
/// harmless where `contract_size == 1`, which was true of every live symbol
/// except ETHBTC, whose contract is 100 ETH. That gap traded 0.57 lots = 57 ETH
/// = $107,434 notional on a ~$1,100 account on 2026-08-12.
///
/// `Lots` strategies have already applied the contract multiplier themselves --
/// `commodity_book` states "Quantity is MT5 lots" and sizes XNGUSD through its
/// own 10,000 multiplier, `index_book` likewise. Converting those a second time
/// would divide XNGUSD's size by 10,000. The whole point of naming the unit is
/// that neither mistake can be made silently.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[allow(dead_code)]
pub(super) enum QuantityUnit {
    /// A number of the base asset; divide by `contract_size` to get lots.
    Coins,
    /// Already MT5 lots; pass through untouched.
    Lots,
}

/// Keep this beside `strategy_symbol` for the same reason `warmup_sessions` is
/// here: a newly registered live strategy must not inherit a default silently.
///
/// EVERY SLEEVE OF THIS BOOK EMITS LOTS. `exness_combined`'s sizing goes through
/// each market's `volume_min`, `volume_step` and `contract_size` itself, so the
/// number it hands back is already an MT5 lot volume. Converting it a second
/// time would divide a USDJPY order by 100,000.
///
/// The enum survives the book that needed it. `ethbtc_orb` emitted a COIN count
/// against a 100-ETH contract, and sending that through as a lot volume traded
/// $107,434 of notional on a ~$1,100 account
/// ([[ethbtc-live-size-is-100x-the-contract-size]]). Naming the unit is what
/// makes that mistake impossible to make silently, so it stays even while every
/// current member answers the same way.
pub(super) fn quantity_unit(strategy: &str) -> QuantityUnit {
    match sleeve_of(strategy) {
        Some(_) => QuantityUnit::Lots,
        // An unregistered id cannot be sized at all; `build_strategy` refuses it
        // first, and this is the conservative answer if that ever changes.
        None => QuantityUnit::Lots,
    }
}

/// Contract size and minimum order size, read from the sleeve's own frozen
/// `Instrument` rather than from a second table kept out here.
///
/// `min_lots` is the broker's `volume_min` and is NOT 0.01 everywhere: NQ is
/// 0.05, ETHUSD 0.10, and JP225 refuses anything under THREE whole lots. A
/// single hard-coded 0.01 guarding `open` would let through orders the broker
/// rejects.
pub(super) struct LotSpec {
    pub(super) contract_size: f64,
    pub(super) min_lots: f64,
}

/// A strategy's `quantity` expressed as an MT5 lot volume.
///
/// Free rather than a method so it is testable without standing up a whole
/// `LiveSlot`; `LiveSlot::to_lots` delegates here.
pub(super) fn to_lot_volume(strategy: &str, quantity: f64) -> f64 {
    match quantity_unit(strategy) {
        QuantityUnit::Lots => quantity,
        QuantityUnit::Coins => quantity / lot_spec(strategy).contract_size,
    }
}

/// KEYED ON THE STRATEGY, NEVER ON THE CONFIGURED SYMBOL.
///
/// `mt5_account_strategies.symbol` is free text typed into a form, and the API
/// stores it verbatim apart from rewriting `nq` to `USTEC`. This table used to
/// be keyed on that string with lowercase market names and a fall-through
/// default of NQ's `(1.0, 0.05)`, so every symbol a person would naturally type
/// missed: `"JP225"` resolved to a 0.05 floor against a real minimum of THREE
/// lots -- admitting orders the broker rejects -- `"ETHUSD"` to 0.05 against a
/// real 0.10, and `"USDJPY"` to 0.05 against a real 0.01, silently refusing
/// every entry that sleeve sizes below a twentieth of a lot. Only the accident
/// that NQ's spec IS the default kept it working at all.
///
/// The sleeve knows its own market and its own contract, so ask it. An id the
/// book does not carry gets an unreachable floor: `build_strategy` refuses it
/// first, and refusing to open is the safe answer if that ever changes.
pub(super) fn lot_spec(strategy: &str) -> LotSpec {
    match sleeve_of(strategy) {
        Some(sleeve) => LotSpec {
            contract_size: sleeve.contract_size(),
            min_lots: sleeve.broker_minimum_lots(),
        },
        None => LotSpec {
            contract_size: 1.0,
            min_lots: f64::INFINITY,
        },
    }
}

/// Completed sessions needed to reconstruct every indicator, filter and sizing
/// input used by a strategy. Startup takes the maximum for each active market;
/// inactive markets are not queried.
///
/// ASKED OF THE SLEEVE RATHER THAN WRITTEN DOWN. This returned a flat 100 for
/// every sleeve, justified by the `calm` gate being the deepest window in the
/// book -- which was true of the WINDOWS and missed the recursive filters
/// entirely: a 50-session trend EMA is seeded at its first value and never
/// refuses, so it does not fill a window, it CONVERGES, and 100 sessions leaves
/// several percent of the seed still in it. `Sleeve::warmup_sessions` derives
/// the answer from the cell own parameters, so a sleeve whose deepest
/// requirement changes cannot keep a number someone wrote here.
pub(super) fn warmup_sessions(strategy: &str) -> i64 {
    sleeve_of(strategy).map_or(1, |sleeve| sleeve.warmup_sessions())
}

pub(super) fn warmup_calendar_days(symbol: &str, sessions: i64) -> i64 {
    crate::strategies::idk::exness_combined::warmup::calendar_days(symbol, sessions)
}

/// How far back each active market must be replayed before the account goes
/// live, and which strategies that history covers.
#[derive(Default)]
pub(super) struct WarmupPlan {
    pub(super) calendar_days: HashMap<&'static str, i64>,
    pub(super) strategies: HashSet<String>,
}

impl WarmupPlan {
    pub(super) fn for_targets(targets: &[LiveStrategyTarget]) -> Self {
        let mut plan = Self::default();
        for target in targets.iter().filter(|target| supports(&target.strategy)) {
            let symbol = strategy_symbol(&target.strategy);
            let days = warmup_calendar_days(symbol, warmup_sessions(&target.strategy));
            plan.calendar_days
                .entry(symbol)
                .and_modify(|known| *known = (*known).max(days))
                .or_insert(days);
            plan.strategies.insert(target.strategy.clone());
        }
        plan
    }

    pub(super) fn from(&self, symbol: &str, latest: i64) -> Option<String> {
        self.calendar_days
            .get(symbol)
            .map(|days| crate::backtest::data::format_day(latest.div_euclid(86_400) - days))
    }
}

pub fn supports(strategy: &str) -> bool {
    LIVE_STRATEGIES.contains(&strategy)
}

/// The MT5 symbol a live strategy MUST be configured with, or `None` for an id
/// the book does not carry.
///
/// `mt5_account_strategies.symbol` is free text: the add-strategy form is a text
/// box, and the API stores what was typed apart from rewriting `nq` to `USTEC`.
/// Nothing downstream cross-checks it against the sleeve, so
/// `jp225_break_retest` configured with `UK100` would be stepped with JP225
/// bars and send its real orders to the FTSE -- the same class of failure as stepping a
/// strategy with another market's prices, which cost 800 dollars at an 88%
/// drawdown while the standalone backtest looked fine.
pub fn required_symbol(strategy: &str) -> Option<&'static str> {
    if !supports(strategy) {
        return None;
    }
    route_symbol(strategy_symbol(strategy)).ok()
}

/// Whether `symbol` names the instrument `strategy` is supposed to trade.
///
/// Spelling-insensitive, because `route_symbol` is: `nq`, `NQ`, `USTEC` and
/// `nas100` all name the same contract, and refusing a person's capitalisation
/// would only teach them to work around the check.
pub fn symbol_matches_strategy(strategy: &str, symbol: &str) -> bool {
    match (required_symbol(strategy), route_symbol(symbol)) {
        (Some(required), Ok(routed)) => routed == required,
        // An unregistered strategy has no requirement to violate; the live
        // runtime refuses to build it long before this matters.
        (None, _) => true,
        (Some(_), Err(_)) => false,
    }
}
/// Builds one sleeve of the 2026-09-07 Exness book.
pub(super) fn build_strategy(strategy: &str) -> Option<Box<dyn Strategy>> {
    // 0.01 is the Forex quantity step, matching the backtest instrument. Each
    // sleeve floors to its own market's `volume_step` on top of it.
    //
    // ENTRIES GO OUT ON THE FILL CANDLE'S FIRST MINUTE, not once it has closed:
    // the backtest fills at that candle's open, and waiting for the close sent
    // every live entry half an hour late ([[live-entries-are-one-candle-late]]).
    sleeve_of(strategy).map(|sleeve| {
        let mut strategy = ExnessCombined::new(sleeve, 0.01);
        strategy.enable_early_fills();
        Box::new(strategy) as Box<dyn Strategy>
    })
}

pub(super) fn side_name(side: Side) -> &'static str {
    match side {
        Side::Long => "long",
        Side::Short => "short",
    }
}

/// The MT5 symbol behind a market name.
///
/// The broker's spelling is not the table's: NQ is `USTEC` and BTC is `BTCUSD`;
/// the rest agree once upper-cased. Anything not in the book is refused rather than
/// guessed at -- a wrong symbol here sends a real order for the wrong
/// instrument.
pub(super) fn route_symbol(symbol: &str) -> Result<&str, crate::error::ApiError> {
    let symbol = symbol.trim();
    if symbol.eq_ignore_ascii_case("nq")
        || symbol.eq_ignore_ascii_case("ustec")
        || symbol.to_ascii_lowercase().contains("nas")
    {
        return Ok("USTEC");
    }
    let lower = symbol.to_ascii_lowercase();
    match lower.as_str() {
        "usdjpy" => Ok("USDJPY"),
        "audusd" => Ok("AUDUSD"),
        "gbpjpy" => Ok("GBPJPY"),
        "gbpusd" => Ok("GBPUSD"),
        "jp225" => Ok("JP225"),
        "ethusd" => Ok("ETHUSD"),
        "ukoil" => Ok("UKOIL"),
        "eurjpy" => Ok("EURJPY"),
        "uk100" => Ok("UK100"),
        "btc" | "btcusd" => Ok("BTCUSD"),
        "xniusd" => Ok("XNIUSD"),
        _ => Err(crate::error::ApiError::BadRequest(
            "unsupported live IDK symbol".into(),
        )),
    }
}

/// What an ownership answer does to a slot's block, or `None` if nothing
/// changes.
///
/// Split out of `refresh_targets` so the rule can be tested without a database
/// or a broker, because the two cases that matter are the two that are easiest
/// to write by accident: a sticky block must survive an ownership answer that
/// says everything is fine, and a standing ownership block must not re-announce
/// itself on every one of the five refreshes a second.
///
/// The caller asks this ONLY while the account is connected. A disconnected
/// account has no snapshot to compare, so `mismatch` would be an answer to a
/// question nobody could ask.
pub(super) fn ownership_transition(
    current: Option<BlockReason>,
    mismatch: bool,
) -> Option<Option<BlockReason>> {
    match (current, mismatch) {
        // Trading, and the books disagree: stop taking new risk.
        (None, true) => Some(Some(BlockReason::Ownership)),
        // Blocked for this reason, and the reason is gone: resume.
        (Some(BlockReason::Ownership), false) => Some(None),
        // Already blocked for this reason, still true -- say nothing.
        // Blocked for a reason this cannot speak to -- leave it alone.
        // Trading and the books agree -- nothing to do.
        _ => None,
    }
}

#[cfg(test)]
mod ownership_transition_tests {
    use super::*;

    #[test]
    fn a_mismatch_blocks_a_trading_sleeve() {
        assert_eq!(
            ownership_transition(None, true),
            Some(Some(BlockReason::Ownership))
        );
    }

    #[test]
    fn agreement_clears_an_ownership_block() {
        assert_eq!(
            ownership_transition(Some(BlockReason::Ownership), false),
            Some(None)
        );
    }

    /// The whole point of carrying a reason. A feed watchdog stopped this
    /// sleeve because the runtime lost sight of the market; the broker and the
    /// durable rows agreeing about positions says nothing about that, and must
    /// not hand the sleeve back to the strategy unattended.
    #[test]
    fn agreement_never_clears_a_sticky_block() {
        assert_eq!(ownership_transition(Some(BlockReason::Sticky), false), None);
        assert_eq!(ownership_transition(Some(BlockReason::Sticky), true), None);
    }

    /// Silence while nothing changes is what keeps the console readable: this
    /// is asked about five times a second per sleeve.
    #[test]
    fn a_standing_block_does_not_re_announce() {
        assert_eq!(
            ownership_transition(Some(BlockReason::Ownership), true),
            None
        );
        assert_eq!(ownership_transition(None, false), None);
    }
}

/// How long a feed may stop advancing when the daemon feeding it is provably
/// healthy and the vendor is simply publishing nothing.
///
/// THE WATCHDOG CANNOT TELL THOSE TWO APART AND IT FLATTENS THE BOOK OVER THE
/// DIFFERENCE. From the store, "no new rows" looks the same whether the feed
/// daemon died or Dukascopy went quiet -- and Dukascopy going quiet for two or
/// three minutes is routine, three times on 2026-09-09 alone. One of them
/// closed `eurjpy_gated_orb` at 14:25Z on a vendor gap that ended seconds
/// later, where the backtest simply held.
///
/// So the daemon publishes the one fact the server cannot derive: its fetch
/// loop came back, empty or not (`publish_health` in
/// `tools/idk_market_live_data_feeds.py`). With that evidence the fuse is
/// longer, because the failure it guards against -- nobody is polling and
/// nobody will be -- is ruled out.
///
/// IT IS LONGER, NOT INFINITE. A position whose market is not printing is
/// unmanaged whatever the reason: no stop, no target, no strategy exit, since
/// all three are driven by a bar arriving. Ten minutes is the point past which
/// "brief vendor gap" stops being the likely explanation. The clock-driven
/// `flatten_overdue_sessions` is what covers a position past its session end,
/// and it needs no bars at all.
pub(super) const FEED_STALE_VENDOR_QUIET_SECONDS: u64 = 600;

/// How fresh the daemon's health file has to be to count as evidence.
///
/// It is rewritten every five seconds by the stall watchdog's thread -- chosen
/// so it keeps being written while the main loop sits inside a fetch, which is
/// most of a minute. Anything older than this means the daemon is not running
/// or is itself wedged, which is exactly the case the short fuse is for.
pub(super) const FEED_HEALTH_FRESH_SECONDS: i64 = 30;

/// How recently a fetch must have COME BACK for the daemon to count as polling.
///
/// NOT THE SAME NUMBER AS THE FILE FRESHNESS, AND THE FIRST DRAFT USED ONE FOR
/// BOTH. The file is rewritten every five seconds; a fetch happens once a
/// minute, on the chase, so on a perfectly healthy daemon `fetch_age` sweeps
/// from zero to about sixty and back. Judged against thirty it read "not
/// polling" for most of every minute -- measured at 38.8 seconds on the first
/// live run of this file, on a daemon that was storing bars normally.
///
/// Two minutes is two missed cycles: enough that a single slow or skipped
/// minute is not read as a wedge, short enough that a stopped loop is.
pub(super) const FEED_FETCH_FRESH_SECONDS: f64 = 120.0;

/// The daemon's own report, or `None` if there is not one worth trusting.
///
/// ABSENCE OF EVIDENCE KEEPS THE SHORT FUSE. A missing, stale, or unparseable
/// file is never an error here -- the daemon may simply not be running, which
/// is the most dangerous case and the one the 180-second default exists for.
/// Only a fresh file with a fresh fetch widens anything.
pub(super) fn feed_daemon_is_polling(store: &crate::parquet_store::ParquetStore) -> bool {
    let path = match std::env::var("FEED_HEALTH_FILE") {
        Ok(value) => std::path::PathBuf::from(value),
        // Written beside the store: `data/feed_health.json` to the store's
        // `data/parquet`.
        Err(_) => match store.root().parent() {
            Some(parent) => parent.join("feed_health.json"),
            None => return false,
        },
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return false;
    };
    let Ok(health) = serde_json::from_str::<serde_json::Value>(&text) else {
        return false;
    };
    let Some(wrote_at) = health.get("wrote_at").and_then(serde_json::Value::as_i64) else {
        return false;
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|since| since.as_secs() as i64)
        .unwrap_or(0);
    // A file from the future is a clock problem, not evidence.
    if now - wrote_at > FEED_HEALTH_FRESH_SECONDS || wrote_at > now + FEED_HEALTH_FRESH_SECONDS {
        return false;
    }
    health
        .get("fetch_age")
        .and_then(serde_json::Value::as_f64)
        .is_some_and(|age| age <= FEED_FETCH_FRESH_SECONDS)
}

/// How long this feed may stop advancing before an open position on it is
/// treated as unmanaged, given what is known about the daemon.
pub(super) fn effective_feed_stale_after(
    symbol: &str,
    daemon_polling: bool,
) -> std::time::Duration {
    let base = feed_stale_after(symbol);
    if daemon_polling {
        base.max(std::time::Duration::from_secs(
            FEED_STALE_VENDOR_QUIET_SECONDS,
        ))
    } else {
        base
    }
}

#[cfg(test)]
mod feed_stale_tests {
    use super::*;

    #[test]
    fn a_silent_daemon_keeps_the_short_fuse() {
        assert_eq!(
            effective_feed_stale_after("usdjpy", false),
            feed_stale_after("usdjpy")
        );
    }

    /// The 2026-09-09 case: fetches returning, vendor publishing nothing.
    #[test]
    fn a_polling_daemon_buys_a_longer_one() {
        let quiet = effective_feed_stale_after("usdjpy", true);
        assert!(quiet > feed_stale_after("usdjpy"));
        assert_eq!(
            quiet,
            std::time::Duration::from_secs(FEED_STALE_VENDOR_QUIET_SECONDS)
        );
    }

    /// The bug the first draft had: a fetch happens once a minute, so the
    /// window that judges it must be wider than a minute or a healthy daemon
    /// reads as dead for most of every one.
    #[test]
    fn the_fetch_window_spans_more_than_one_poll_cycle() {
        assert!(FEED_FETCH_FRESH_SECONDS > 60.0);
        assert!(FEED_FETCH_FRESH_SECONDS > FEED_HEALTH_FRESH_SECONDS as f64);
    }

    /// Never SHORTER than the base, whatever a market's own step implies.
    #[test]
    fn the_longer_fuse_never_shortens_a_slow_market() {
        for symbol in ["usdjpy", "ethusd", "jp225", "xniusd"] {
            assert!(effective_feed_stale_after(symbol, true) >= feed_stale_after(symbol));
        }
    }
}

#[cfg(test)]
mod feed_health_file_tests {
    use super::*;
    use std::io::Write;

    fn with_health(json: &str) -> bool {
        let dir = std::env::temp_dir().join(format!("feed_health_{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let path = dir.join(format!("health_{n}.json"));
        let mut file = std::fs::File::create(&path).expect("write health");
        file.write_all(json.as_bytes()).expect("write health");
        drop(file);
        unsafe { std::env::set_var("FEED_HEALTH_FILE", &path) };
        let answer = feed_daemon_is_polling(&crate::parquet_store::ParquetStore::new("unused"));
        unsafe { std::env::remove_var("FEED_HEALTH_FILE") };
        let _ = std::fs::remove_file(&path);
        answer
    }

    fn now() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|since| since.as_secs() as i64)
            .unwrap_or(0)
    }

    /// The shape `publish_health` actually writes, with the fetch age measured
    /// on a real run -- 38.8s, which the first draft rejected.
    #[test]
    fn a_live_daemons_file_reads_as_polling() {
        assert!(with_health(&format!(
            r#"{{"wrote_at": {}, "fetch_age": 38.8, "row_age": 38.7, "in_session": ["usdjpy"]}}"#,
            now()
        )));
    }

    #[test]
    fn a_stale_file_is_not_evidence() {
        assert!(!with_health(&format!(
            r#"{{"wrote_at": {}, "fetch_age": 1.0}}"#,
            now() - 600
        )));
    }

    /// The daemon is running and writing, but its fetches stopped coming back.
    /// That is the wedge the short fuse exists for.
    #[test]
    fn a_wedged_fetch_loop_is_not_polling() {
        assert!(!with_health(&format!(
            r#"{{"wrote_at": {}, "fetch_age": 400.0}}"#,
            now()
        )));
    }

    #[test]
    fn rubbish_and_absence_are_both_no_evidence() {
        assert!(!with_health("not json at all"));
        assert!(!with_health(r#"{"wrote_at": 0}"#));
        unsafe { std::env::set_var("FEED_HEALTH_FILE", "C:/nonexistent/feed_health.json") };
        let answer = feed_daemon_is_polling(&crate::parquet_store::ParquetStore::new("unused"));
        unsafe { std::env::remove_var("FEED_HEALTH_FILE") };
        assert!(!answer);
    }
}
