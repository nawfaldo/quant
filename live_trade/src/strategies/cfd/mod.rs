//! The shared `cfd_families` engine: one streaming port of
//! `cfd_families.backtest` (and the `cfd_tt_families` creator families) that
//! every sealed book runs on.
//!
//! WHAT LIVES HERE AND WHAT DOES NOT. The machine -- candle assembly, the
//! indicators, every family's signal, the two-stage sizing, the live-fill hooks
//! -- is the same whichever broker a cell was sealed against, exactly as the
//! Python is one `cfd_families` module priced by `CFD_BROKER`. A BOOK is the
//! dated selection: its members (`sleeves/`), its broker's frozen contracts and
//! its own dials. Two books exist:
//!
//!   `exness/oct-12-2026/`      the live Exness Pro book
//!   `fundednext/oct-21-2026/`  the FundedNext book, backtest only
//!
//! `Sleeve` is the one handle the engine and the backtest key on; every member
//! of every book is a variant, and `Sleeve::book` says whose it is.

use std::collections::VecDeque;

use crate::backtest::types::{Action, Bar, Side, Strategy};

// -------------------------------------------------------------------------- //
// engine constants, the same for every book
// -------------------------------------------------------------------------- //

// -------------------------------------------------------------------------- //
// book constants
// -------------------------------------------------------------------------- //

/// `cfd_families.RISK_FRACTION`. The per-trade stop risk a sleeve asks for
/// before any book-level dial is applied.
pub(crate) const RISK_FRACTION: f64 = 0.015;

/// `cfd_families.MARGIN_FRACTION`: a self-imposed 4x notional ceiling. It
/// reads REAL equity rather than the scaled or capped figure, because margin is
/// a broker constraint on the account and does not shrink because the book chose
/// to bet less ([[shown-equity-cannot-fix-a-margin-refusal]]).
pub(crate) const MARGIN_FRACTION: f64 = 0.25;

/// `exness_combined_strategies.FORCE_MINIMUM_LOT`, which the canon run has ON.
///
/// An opt-in execution policy for an operator who requires every positive sizing
/// request to trade: a request that lands under `volume_min` is rounded UP to it
/// rather than refused. The excess risk from that rounding stays in the
/// marked-to-market curve and is not netted out anywhere.
///
/// It is not a rounding convenience. Without it a $400 account drops the trades
/// it cannot afford, and a dropped trade means the backtest and the live account
/// are running different strategies
/// ([[small-balance-hides-drawdown-by-dropping-trades]]). The margin ceiling
/// still refuses outright, because that one is the broker's and not a policy.
pub(crate) const FORCE_MINIMUM_LOT: bool = true;

/// `a_margin_refusal_survives_force_minimum_lot` only tests a MARGIN refusal
/// while the policy above is on; flipping it off would silently turn that test
/// into an assertion about something else, so fail the build instead.
#[allow(clippy::assertions_on_constants, reason = "pinning a policy constant")]
pub(crate) const _: () = assert!(FORCE_MINIMUM_LOT);

/// `cfd_families.INITIAL_BALANCE`, and the equity that decides WHICH TRADES
/// EXIST rather than how large they are.
///
/// THE BOOK IS SIZED IN TWO STAGES AND THIS IS THE FIRST. `sleeve_trades` builds
/// each cell's log by running `cfd_families.backtest` at this balance with
/// the FAMILY sizing rule -- plain `RISK_FRACTION`, a one-sided volatility
/// throttle, no book scale -- and `quantity` returns 0 for any order that lands
/// under `volume_min`. Such a trade never enters the log at all. The portfolio
/// replay then re-sizes the SURVIVORS against the live shared balance using the
/// book's own rule, which is `FamilyEngine::quantity`.
///
/// THE ADMISSION EQUITY COMPOUNDS. `backtest` starts that standalone run at
/// this balance and books each cell's own P&L into it, so the admission
/// decision late in the window is made against whatever the cell had made by
/// then -- `FamilyEngine` therefore carries a SHADOW ACCOUNT, sized and settled
/// by the standalone rule, purely to answer "does this trade exist". Holding it
/// at a constant $1,000 is close but not the same, and on
/// `jp225:swing_donchian` the difference was three trades and a cascade: a
/// refusal frees the position slot, so the next signal fires where it would
/// otherwise have been blocked.
///
/// So a sleeve's trade universe is fixed by this balance and its trade SIZES are
/// not. Collapsing the two stages into one looks like a simplification and is
/// not: it changed `jp225:swing_donchian` from 52 trades to 70 and its share of
/// the book's P&L from 3.6% to 12.7%. Every sleeve reconciles as
/// `(trades admitted here) - (gross-cap refusals) = trades in the book`, which is
/// what pins this reading of the Python.
///
/// `SHOWN_EQUITY` MULTIPLIES IT, AND THAT IS THE WHOLE POINT OF THE KNOB.
/// `sleeve_trades` passes `initial=ef.INITIAL_BALANCE * shown`, so a sleeve shown
/// 2.5x is admitted against $2,500 rather than $400 -- because the refusal the
/// multiplier exists to lift happens INSIDE `ef.backtest`, where an order under
/// `volume_min` never becomes a trade at all. Applying it only in the replay
/// re-sizes the orders that got through and does nothing about the ones that did
/// not, which is the circular measurement that once reported a 100% fill rate for
/// every sleeve. Reading it as a flat $1,000 here cost `jp225:swing_donchian` 29
/// trades and `jp225:volume_thrust` 14.
///
/// The two imported sleeves are NOT filtered: `external_trades` carries a
/// symbolic `units_per_dollar` and never calls `quantity`, so nothing is dropped
/// before the replay sees it.
pub(crate) const ADMISSION_BALANCE: f64 = 1_000.0;

/// `cfd_families.DAILY_RANGE_BARS`: days behind the average true daily range
/// that every stop, target and trail is quoted in.
pub(crate) const DAILY_RANGE_DAYS: usize = 14;

/// `cfd_families.SLIPPAGE_BP`, the conservative allowance added to every
/// quoted spread. The engine folds the same 0.2 into `sleeve_spread_bp`.
pub(crate) const SLIPPAGE_BP: f64 = 0.2;

/// Candle size the whole study runs on.
pub(crate) const BAR_SECONDS: i64 = 1_800;

/// `daily_multipliers`, which is `VolTargetConfig::default` written out. Two
/// sided on purpose: the old `min(1, target/realized)` throttle could only ever
/// cut risk, so in a calm regime every sleeve sat at full size and the book's
/// risk was whatever the raw fraction happened to produce.
pub(crate) const VOL_TARGET: f64 = 0.20;
pub(crate) const VOL_HALFLIFE: f64 = 20.0;
pub(crate) const VOL_MAX_MULTIPLIER: f64 = 3.0;
pub(crate) const VOL_MIN_DAYS: u32 = 30;

// -------------------------------------------------------------------------- //
// the members
// -------------------------------------------------------------------------- //

/// One member of the book. The display name is `"<SYMBOL> <Family>"` so
/// `strategies::market_symbol` can route it by its first word, which is the
/// convention that stopped a BTC strategy being stepped with NQ prices.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Sleeve {
    UsdjpyVolumeThrust,
    UsdjpyPullback,
    UsdjpyKendall,
    UsdjpyFracdiff,
    UsdjpyHalfLife,
    AudusdZscore,
    EthusdConfluence,
    EthusdKalman,
    EthusdVolatilityBreakout,
    EthusdObvBreak,
    EthusdPullback,
    /// DROPPED FROM `BOOK` 2026-09-19, KEPT AS CODE, and the two are different
    /// claims. The enum is what this engine can run; `BOOK` is what it is
    /// seated to run. Both cells were profitable over 2022-2026 and left on
    /// CONCENTRATION -- eight of twenty-two sleeves on one index -- so deleting
    /// their implementations would throw away the parity fixtures that hold
    /// `BreakRetest` and the `rr_N` exit path to their Python, and make
    /// re-seating a port rather than a line.
    ///
    /// `Family::BreakRetest` now has no seated cell and is reached only by its
    /// fixture. That is the one place this book carries a family the book does
    /// not select, and it is deliberate rather than an oversight.
    Jp225BreakRetest,
    Jp225VolatilityBreakout,
    GbpjpyTrap,
    UkoilXmaCross,
    UkoilLevelConfluence,
    EurjpyTwoStage,
    EurjpyGatedOrb,
    Jp225VolumeThrust,
    Jp225VolRegime,
    Jp225MomentumStack,
    Jp225Cusum,
    Jp225ObvDivergence,
    Jp225Kalman,
    /// SEATED 2026-09-22 into the freed jp225 seats.
    UsdjpyAroon,
    EthusdBreakRetest,
    EurjpySwingMa,
    /// SEATED 2026-09-23 after `ukoil:xma_cross` left on decay.
    EthusdRoofing,
    EthusdLevelConfluence,
    UsdjpyRvol,
    EthusdEfficiency,
    EthusdCci,
    EthusdLinregTrend,
    /// The TikTok-creator cells (`cfd_tt_families`), SEATED 2026-09-28. Each
    /// runs on its own bar size and sets its own stop.
    UstecLuxBodyMomentum5m,
    UsdjpyQpMaCross30m,
    Us500LuxSwingSweepMss5m,
    GbpjpyQpMaCross15m,
    EurjpyQpMaCross60m,
    EthusdTdEmaMacd15m,
    GbpjpyLuxalgoManipulation120m,
    GbpjpyLuxHtfManipulation15m,
    /// SEATED 2026-09-30 with the 23-sleeve book: five more TikTok cells and
    /// three `cfd_families` cells, on four new markets (DE40, UK100, USOIL, BTC).
    EthusdLuxBodyMomentum15m,
    GbpjpyLuxSupplyDemand30m,
    De40LuxSweepReclaim60m,
    UsdjpyLuxHtfManipulation60m,
    EurjpyLuxNyVwapPullback15m,
    Uk100XmaCross,
    UsoilXmaCross,
    BtcJump,
    UsdjpyYfAlphatrend30m,
    GbpjpyMqlBigMoveCont60m,
    EurjpyQcEmaCross30m,
    EurjpyDtRsiDipVol15m,
    UsdjpyAdaptivePullback60mBarlb,
    UsdjpyFailedBreak5mBarlb,
    UsdjpyMomentumStack30mBarlbOff,
    UsdjpySar30mBarlbOff,
    GbpusdQpRsi2Pullback30mOff,
    EthusdSbBollinger240mNat,
    /// THE FUNDEDNEXT BOOK (`fundednext/oct-21-2026`): the 50 members
    /// `fundednext_combined_strategies` sealed as f61f9b946b90, in its order.
    FnUsdchfSkew1hAfternoon,
    FnChfjpyOvernight5mNight,
    FnXauusdObvDivergence5mLondonOpen,
    FnUstecConfluence15mTokyoOpen,
    FnGbpaudCci1hMorning,
    FnUstecPtlOrbCandle30mOff,
    FnUs500DayOfWeek5mEvening,
    FnUs30Amihud30mNyOpen,
    FnChfjpyStochastic15mEvening,
    FnUsdcadXmaCross5mMorning,
    FnXauusdFloorPivot15mMorning,
    FnGbpusdGatedFade30mMorning,
    FnUsdjpyTwoStage30mLondonOpen,
    FnAudjpyGatedDonchian1hMorning,
    FnGbpjpyQuantileBreak5mNyMidday,
    FnUs500AvFirstHourFib30mAfternoon,
    FnXauusdQuantileBreak15mEvening,
    FnUs500TwoStage15mNyOpen,
    FnFr40Efficiency1hMorning,
    FnUstecGatedOrb30mLondonOpen,
    FnUs2000AdaptivePullback30mMorning,
    FnUs500XmaCross30mLondonOpen,
    FnXauusdQpRsiExtreme15mOffhour,
    FnUs2000SwingBreak30mMorning,
    FnXauusdXmaCross30mNyClose,
    FnNzdusdTrap1hOffhour,
    FnUstecKendall5mLondonOpen,
    FnGbpusdRegimeBreakout5mNyMidday,
    FnGbpjpyConfluence30mTokyo,
    FnEurusdStructure30mBarlbOff,
    FnEurjpyRvol60mBarlb,
    FnUstecFracdiff5mLondon,
    FnGbpjpyDayOfWeek30mBarlb,
    FnUsdjpyKalman30mNyOpen,
    FnAudjpyDayOfWeek30mLondon,
    FnUs500LinregTrend30mAfternoon,
    FnGbpjpyQuantileBreak30mAfternoon,
    FnUsdcadAdaptivePullback1hAfternoon,
    FnAudjpyEfficiency30mBarlb,
    FnEuraudDayOfWeek5mAfternoon,
    FnEurusdAdaptivePullback15mMorning,
    FnEurjpyTrOscillatorRebound60m,
    FnNzdusdTwoStage5mRth,
    FnEurjpyCusum1hEarlyMorning,
    FnChfjpyAdaptivePullback5mOffhour,
    FnUsdchfFloorPivot15mMorning,
    FnUk100Confluence1hMorning,
    FnUk100ValueArea60mBarlb,
    FnGbpusdValueArea30mBarlbOff,
    FnCadjpyMomentum5mRth,
}

/// The sealed book a sleeve belongs to, and so the broker that prices it.
///
/// A book is ONE selection made on one day against one data snapshot, sized by
/// its own dials. The engine is shared; what differs is read from here.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub enum Book {
    /// `exness/oct-12-2026`, the live Exness Pro book.
    Exness,
    /// `fundednext/oct-21-2026`, the FundedNext book. Backtest only.
    FundedNext,
}

impl Book {
    /// The book-level risk dial every member's request is multiplied by
    /// (`replay(..., risk_scale=...)`).
    pub fn risk_scale(self) -> f64 {
        match self {
            Self::Exness => crate::strategies::exness::oct_12_2026::CANON_RISK_SCALE,
            Self::FundedNext => crate::strategies::fundednext::oct_21_2026::RISK_SCALE,
        }
    }

    /// The standalone balance each cell's own log was generated at, which is
    /// what decides which trades exist (`ADMISSION_BALANCE`). The FundedNext
    /// pool logs ran at the challenge's $6,000 (`fn.ACCOUNT`).
    pub(crate) fn admission_balance(self) -> f64 {
        match self {
            Self::Exness => ADMISSION_BALANCE,
            Self::FundedNext => crate::strategies::fundednext::oct_21_2026::INITIAL_BALANCE,
        }
    }
}

impl Sleeve {
    /// Everything this sleeve is, in one place: `sleeves/<id>.rs`.
    pub(crate) fn spec(self) -> &'static SleeveSpec {
        spec::spec(self)
    }

    pub fn display(self) -> &'static str {
        self.spec().display
    }

    pub fn id(self) -> &'static str {
        self.spec().id
    }

    pub fn code(self) -> &'static str {
        self.spec().code
    }

    pub fn python_key(self) -> &'static str {
        self.spec().python_key
    }

    pub fn market(self) -> &'static str {
        self.spec().market
    }

    /// The book this sleeve is a member of.
    pub fn book(self) -> Book {
        self.spec().book
    }

    /// Every book's members: the Exness registry, then the FundedNext book.
    /// The live runtime refuses anything whose `book` is not Exness, so a
    /// FundedNext name resolving here reaches the backtest only.
    fn every_registered() -> impl Iterator<Item = Self> {
        crate::strategies::exness::oct_12_2026::registry()
            .iter()
            .chain(crate::strategies::fundednext::oct_21_2026::BOOK.iter())
            .copied()
    }

    pub fn from_display(name: &str) -> Option<Self> {
        Self::every_registered().find(|sleeve| sleeve.display() == name)
    }

    pub fn from_id(id: &str) -> Option<Self> {
        Self::every_registered().find(|sleeve| sleeve.id() == id)
    }

    pub(crate) fn scale(self) -> f64 {
        self.spec().scale
    }

    pub(crate) fn shown_equity(self) -> f64 {
        self.spec().shown_equity
    }

    /// `WEEKEND_ONLY`: the weekdays this sleeve may ENTER on, `None` for all.
    pub(crate) fn entry_days(self) -> Option<u8> {
        self.spec().entry_days
    }

    /// Hours `cfd_families.all_bars` adds to this market's timestamps as it
    /// loads them, before anything else sees them.
    ///
    /// The Asian cash sessions straddle New York midnight, so without the shift
    /// one session lands in two calendar days and every prior-day anchor breaks.
    /// `FamilyEngine` applies it internally; this exposes it to the merged
    /// stream, which has to ORDER a shifted market against unshifted ones the
    /// way Python's replay does.
    pub fn shift_hours(self) -> i64 {
        self.spec().contract.shift_hours
    }

    /// `(open, close)` in minutes past New York midnight on the SHIFTED clock,
    /// inclusive of both ends -- the window `cfd_families.in_session` keeps.
    pub fn session(self) -> (i64, i64) {
        self.spec().contract.session
    }

    /// Whether a timestamp falls in this sleeve's session, shift applied.
    ///
    /// `cfd_families.in_session`: `opened <= ts % 86400 // 60 <= closed`,
    /// against bars `all_bars` has ALREADY shifted. Rust loads unshifted, so the
    /// shift is applied here.
    pub fn in_session(self, ts: i64) -> bool {
        let (open, close) = self.session();
        let minute = (ts + self.shift_hours() * 3_600).rem_euclid(86_400) / 60;
        (open..=close).contains(&minute)
    }

    /// One night of carry on this sleeve, in PRICE units per lot, always >= 0.
    ///
    /// CHARGED ON EVERY MEMBER, because every member is an `ef.backtest` cell
    /// and that function bills `nights * financing_price(...)`. The exemption
    /// this used to carry was for the two imports, whose Python path bills only
    /// `entry_cost` and never looks at swap; both left the book on 2026-09-03.
    ///
    /// It is nearly always zero anyway: a session cell spans no date boundary,
    /// so the charge lands on holidays and half days alone.
    /// The trading-day calendar a `multiday` sleeve's swap is billed on
    /// (`costs::rollover_charges`); `None` for every session sleeve, which keeps
    /// calendar-date `financing_nights`.
    pub fn rollover_calendar(self) -> Option<f64> {
        self.multiday().then(|| self.spec().contract.calendar)
    }

    pub fn financing_per_night(self, long: bool) -> f64 {
        let contract = self.spec().contract;
        if long {
            contract.financing_long
        } else {
            contract.financing_short
        }
    }

    /// Whether this sleeve builds its own THIRTY-MINUTE CANDLES out of the bars
    /// it is fed, and so cannot act on one until the next slot opens.
    ///
    /// EVERY MEMBER DOES, since the book lost the two imports that read the bar
    /// they were given and acted on it. The distinction is not cosmetic to a
    /// combined run: an aggregating sleeve emits candle S's exit and fill on the
    /// FIRST BAR OF SLOT S+1, so half an hour of the rest of the book lands
    /// between the decision and its application, and the shared balance it sizes
    /// against has moved. `execute_combined` offers these streams half an hour
    /// early to put them back where Python acts.
    pub fn aggregates_candles(self) -> bool {
        self.params().is_some()
    }

    pub(crate) fn sized_as_import(self) -> bool {
        self.spec().sized_as_import
    }

    pub(crate) fn contract(self) -> Instrument {
        self.spec().contract
    }

    /// Seconds in one of this sleeve's candles: 1,800 unless it carries a
    /// timeframe of its own.
    pub(crate) fn bar_seconds(self) -> i64 {
        self.spec()
            .timeframe
            .map_or(BAR_SECONDS, |timeframe| timeframe.bar_minutes * 60)
    }

    /// `bar_seconds`, for the engine: the width of the candle this sleeve
    /// aggregates, which is how late its stream acts and so how early the
    /// engine must offer it.
    pub fn candle_seconds(self) -> i64 {
        self.bar_seconds()
    }

    /// Candles in one session at this sleeve's bar size.
    pub(crate) fn per_session(self) -> usize {
        self.spec()
            .timeframe
            .map_or(self.contract().per_session, |timeframe| {
                timeframe.per_session
            })
    }

    /// `EXNESS_BAR_LOOKBACKS`: indicator lengths in bars, not sessions.
    pub(crate) fn bar_lookbacks(self) -> bool {
        self.spec().bar_lookbacks
    }

    /// The cash session an `off`-window sleeve may not enter or fill inside,
    /// real New York minutes; `None` for every in-session sleeve.
    pub(crate) fn off_hours(self) -> Option<(i64, i64)> {
        self.spec().off_hours
    }

    /// Whether this sleeve runs on the broker day rather than the cash session:
    /// the `off` window or a clock window. Python's `window_of(spec) != "rth"`.
    pub(crate) fn broker_day(self) -> bool {
        self.spec().off_hours.is_some() || self.spec().clock_window
    }

    /// `CFD_BROKER_HOURS=1`.
    pub fn broker_hours(self) -> bool {
        self.spec().broker_hours
    }

    /// `EXNESS_FILLS_ONLY=1`.
    pub(crate) fn fills_only(self) -> bool {
        self.spec().fills_only
    }

    /// Whether positions hold through the session close (`multiday`).
    pub(crate) fn multiday(self) -> bool {
        self.spec().multiday
    }

    /// The admission throttle's target at this sleeve's bar size.
    pub(crate) fn vol_target(self) -> f64 {
        self.spec()
            .timeframe
            .map_or(self.contract().vol_target, |timeframe| timeframe.vol_target)
    }

    /// The broker's `volume_min` for this sleeve's market, in MT5 lots.
    ///
    /// THE LIVE RUNTIME MUST ASK THIS RATHER THAN KEEP A TABLE OF ITS OWN. The
    /// floor is not 0.01 everywhere -- NQ is 0.05, ETHUSD 0.10, and JP225
    /// refuses anything under THREE whole lots -- and
    /// `mt5_account_strategies.symbol` is free text a person types into a form.
    /// A runtime that keyed its own copy of this table on that string resolved
    /// `"JP225"` and `"ETHUSD"` to no entry at all and fell through to a 0.05
    /// default, which both admitted orders JP225 rejects and refused ETHUSD
    /// orders the broker would have taken. Reading the frozen spec the sleeve
    /// already sizes against is what makes the runtime and the strategy agree
    /// on what is placeable.
    pub fn broker_minimum_lots(self) -> f64 {
        self.contract().volume_min
    }

    /// Units of the base asset in one lot of this sleeve's market.
    pub fn contract_size(self) -> f64 {
        self.contract().contract_size
    }

    /// Account-currency value of one full price unit per lot.
    ///
    /// The engine keeps its own copy in `costs::market_point_value`, because it
    /// prices runs that are not this book. They must agree, and
    /// `frozen_point_values_match_the_strategy_specs` is what keeps them equal:
    /// that map falls through to 1.0, so a market seated without a line in it is
    /// not a build error but a silently wrong P&L.
    pub fn point_value(self) -> f64 {
        self.contract().multiplier
    }

    /// The contract's sealed session spread in bp, slippage allowance NOT
    /// included.
    pub fn contract_spread_bp(self) -> f64 {
        self.contract().spread_bp
    }

    pub(crate) fn params(self) -> Option<Params> {
        self.spec().params()
    }
}

/// One completed 30-minute candle on the sleeve's SHIFTED clock.
///
/// Shared by the modules below it -- `family` builds them and `indicators` folds
/// them -- so it lives here rather than inside either.
#[derive(Clone, Copy)]
pub(crate) struct Candle {
    pub(crate) ts: i64,
    pub(crate) open: f64,
    pub(crate) high: f64,
    pub(crate) low: f64,
    pub(crate) close: f64,
    pub(crate) volume: f64,
}

/// `CFD_BROKER_HOURS` for the LIVE runtime: whether the broker printed any
/// minute in `[start, end)` (real New York seconds). The backtest drops those
/// candles before the engine sees them (`execute_combined`); live has only the
/// vendor feed, so it asks the broker's own minute table as each candle closes.
pub trait BrokerQuotes: Send + Sync {
    fn quoted_real(&self, start: i64, end: i64) -> bool;
    /// `EXNESS_FILLS_ONLY`: whether an entry at the open of the candle opening
    /// at `minute` (real New York seconds) has a broker price -- the backtest's
    /// `MarketFills::entry`, which needs the broker's minute row there with a
    /// positive open, close AND spread. `None` while live cannot tell yet (the
    /// row has not landed); the backtest always knows.
    fn fill_minute(&self, _minute: i64) -> Option<bool> {
        Some(true)
    }
}

mod contracts;
mod family;
mod indicators;
mod spec;
mod tt;
pub mod warmup;

use crate::backtest::fills::FillCoverage;
pub(crate) use contracts::*;
pub(crate) use family::*;
pub(crate) use indicators::*;
pub(crate) use spec::*;

// -------------------------------------------------------------------------- //
// the strategy the engine builds
// -------------------------------------------------------------------------- //

/// One member of the 2026-09-07 Exness book.
///
/// A combined run builds all twenty-two and steps each with its own market's
/// bars; the engine's shared balance is what couples them, which is the whole
/// reason this is not an addition of return streams.
///
/// A THIN WRAPPER SINCE 2026-09-04, AND IT IS KEPT ANYWAY. Until then it chose
/// between three engines and carried a book-level multiplier, a lot floor and a
/// level-two rebuild for the two that sized themselves. Both were NQ cells and
/// left with the symbol, so every member now runs `FamilyEngine`, which applies
/// the book's dials inside its own `size`. What survives here is the TYPE the
/// engine builds and the `Strategy` surface it is reached through -- deleting
/// that would push the sleeve type into the backtest engine, which is the
/// coupling this file exists to avoid.
pub struct CfdSleeve {
    sleeve: Sleeve,
    engine: Box<FamilyEngine>,
}

impl CfdSleeve {
    pub fn new(sleeve: Sleeve, quantity_step: f64) -> Self {
        Self {
            sleeve,
            engine: Box::new(FamilyEngine::new(sleeve, quantity_step)),
        }
    }

    #[allow(dead_code)]
    pub(crate) fn sleeve(&self) -> Sleeve {
        self.sleeve
    }

    /// Research only: new signals on these weekdays alone (Monday = bit 0).
    pub(crate) fn restrict_entry_days(&mut self, mask: u8) {
        self.engine.restrict_entry_days(mask);
    }

    /// Sends entries on the fill candle's first minute; the live runtime's
    /// setting, see `FamilyEngine::enable_early_fills`.
    pub(crate) fn enable_early_fills(&mut self) {
        self.engine.enable_early_fills();
    }

    /// Backtest: the series' `daily_multipliers` table (`FamilyEngine::throttle_table`).
    pub(crate) fn install_throttle(&mut self, table: std::sync::Arc<std::collections::HashMap<i64, f64>>) {
        self.engine.throttle_table = Some(table);
    }

    /// FundedNext: size off `min(balance, cap)` (`FamilyEngine::sizing_cap`).
    pub(crate) fn set_sizing_cap(&mut self, cap: f64) {
        self.engine.sizing_cap = Some(cap);
    }

    /// Live `CFD_BROKER_HOURS`: see `BrokerQuotes`.
    pub(crate) fn install_quotes(&mut self, quotes: std::sync::Arc<dyn BrokerQuotes>) {
        self.engine.quotes = Some(quotes);
    }

    /// Prices this sleeve's fills the way the account gets them. See
    /// `backtest::fills`; the live runtime never calls this.
    pub(crate) fn install_fills(
        &mut self,
        fills: std::sync::Arc<crate::backtest::fills::MarketFills>,
    ) {
        self.engine.install_fills(fills);
    }

    /// Steps the sleeve on one bar, for callers outside the crate.
    ///
    /// `Strategy` is crate-private -- nothing outside the engine should be
    /// deciding what a fill means -- but `exness_sleeve_probe` has to drive a
    /// sleeve by hand to establish which layer a wrong number came from, and
    /// that is worth one delegating method.
    pub fn step(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        self.update_all(bar, equity)
    }
}

impl Strategy for CfdSleeve {
    fn update(&mut self, bar: Bar, equity: f64) -> Action {
        self.engine.update(bar, equity)
    }

    fn update_all(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        self.engine.update_all(bar, equity)
    }

    /// FALSE FOR EVERY MEMBER, and the override is kept because the answer is a
    /// property of the sleeve rather than a constant.
    ///
    /// It was true for the two imports, whose Python path reads `trade["step"]`
    /// and never consults `volume_min` at all -- so on NQ it filled 0.01-lot
    /// orders against a broker floor of 0.05. That is a gap in the Python model
    /// rather than a rule worth having, and nothing in this book reaches it now.
    fn sizes_below_broker_minimum(&self) -> bool {
        self.sleeve.sized_as_import()
    }

    fn resize_entry(&self, price: f64, quantity: f64, shown: f64, actual: f64) -> f64 {
        self.engine.resize_entry(price, quantity, shown, actual)
    }

    fn discard(&mut self, action: Action) {
        self.engine.discard(action);
    }

    fn discard_all(&mut self, actions: Vec<Action>) {
        self.engine.discard_all(actions);
    }

    fn action_timestamp(&self, default: i64) -> i64 {
        self.engine.action_timestamp(default)
    }

    fn exit_timestamp(&self, default: i64) -> i64 {
        self.engine.exit_timestamp(default)
    }

    fn reset_trading_state(&mut self) {
        self.engine.reset_trading_state();
    }

    fn entry_cost_bp(&self) -> Option<f64> {
        self.engine.entry_cost_bp()
    }

    fn fill_coverage(&self) -> Option<FillCoverage> {
        Some(self.engine.coverage)
    }

    fn abandon_open_position(&mut self) {
        self.engine.abandon_open_position();
    }

    fn set_next_bar(&mut self, next_ts: Option<i64>, data_continues: bool) {
        self.engine.next_bar = Some(next_ts);
        self.engine.data_continues = data_continues;
        self.engine.canon_replay = true;
    }

    fn session_end_minute(&self) -> Option<usize> {
        self.engine.session_end_minute()
    }

    fn session_start_minute(&self) -> Option<usize> {
        self.engine.session_start_minute()
    }

    fn flattens_itself(&self) -> bool {
        self.engine.flattens_itself()
    }

    fn holds_overnight(&self) -> bool {
        self.engine.holds_overnight()
    }

    fn entry_risk_fraction(&self) -> Option<f64> {
        self.engine.entry_risk_fraction()
    }

    fn entry_stop_price(&self) -> Option<f64> {
        self.engine.entry_stop_price()
    }

    fn entry_sizing(&self) -> Option<(f64, f64)> {
        self.engine.entry_sizing()
    }

    fn prime_throttle(&mut self, marks: &[(i64, f64)]) {
        if self.sleeve.book() == Book::FundedNext {
            self.engine.prime_throttle(marks);
        }
    }

    fn exit_points(&self) -> Option<f64> {
        self.engine.exit_points
    }
}
