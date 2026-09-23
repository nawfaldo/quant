//! One file per sleeve: twenty-two strategies, twenty-two definitions.
//!
//! WHAT THIS REPLACED, and why the shape matters. Every sleeve used to be
//! described by an arm in each of ten separate per-sleeve `match`
//! statements -- one for its display name, one for its id, one for its storage
//! code, one for its Python key, one for its market, one for its risk scale, one
//! for its shown equity, one for whether it is sized as an import, one for its
//! contract spec and one for its fitted parameters. Reading what a single sleeve
//! WAS meant scrolling through all ten and holding the answers in your head, and
//! adding one meant touching all ten, where a missed arm was a sleeve that
//! silently inherited another's settings.
//!
//! Each sleeve is now one `SleeveSpec` in one file. `spec` below is the only
//! `match` left, and the compiler makes a missing arm a build error rather than
//! a wrong number.
//!
//! EVERY MEMBER IS A FAMILY CELL SINCE 2026-09-04. The 29-08 book carried two
//! hand-written IMPORTS whose engine lived in their own sleeve file, because for
//! them the implementation WAS the definition; both traded NQ, which was barred
//! as a symbol on 2026-09-03. So every file here is now parameters alone and the
//! machine that runs them is shared in `family`.

use super::contracts::Instrument;
use super::family::Params;

mod audusd_zscore;
mod ethusd_confluence;
mod ethusd_kalman;
mod ethusd_obv_break;
mod ethusd_pullback;
mod ethusd_volatility_breakout;
mod eurjpy_gated_orb;
mod eurjpy_two_stage;
mod gbpjpy_trap;
mod jp225_break_retest;
mod jp225_cusum;
mod jp225_kalman;
mod jp225_momentum_stack;
mod jp225_obv_divergence;
mod jp225_vol_regime;
mod jp225_volatility_breakout;
mod jp225_volume_thrust;
mod ukoil_level_confluence;
mod ukoil_xma_cross;
mod usdjpy_fracdiff;
mod usdjpy_half_life;
mod usdjpy_kendall;
mod usdjpy_pullback;
mod usdjpy_volume_thrust;

/// Everything the book knows about one sleeve.
pub(super) struct SleeveSpec {
    /// The name the API and the UI use.
    pub(super) display: &'static str,
    /// The stable identifier the API exposes and the live rows key on, matching
    /// the Python sleeve key with its colon replaced so it is URL-safe.
    pub(super) id: &'static str,
    /// The identifier a finished backtest is STORED under: the uppercase of
    /// `id`. A third spelling exists because saved rows outlive the code that
    /// wrote them, so a display name can be reworded without orphaning them.
    pub(super) code: &'static str,
    /// `"<market>:<family>"`, the key Python identifies this sleeve by.
    ///
    /// It exists for ONE reason: the gross-exposure cap is
    /// first-come-first-served, so the order entries are offered the budget in
    /// decides who gets it. `replay` sorts its pending trades alphabetically on
    /// this string, and a book that offers them in a different order refuses a
    /// different set.
    pub(super) python_key: &'static str,
    /// The market whose bars step this sleeve, lower-cased to match
    /// `RunRequest::symbol`.
    pub(super) market: &'static str,
    /// The frozen broker spec this sleeve sizes against.
    pub(super) contract: Instrument,
    /// `exness_combined_strategies.SLEEVE_SCALE`.
    ///
    /// EVERY MEMBER SITS AT 1.0, and the dict is not empty -- it still names
    /// `jp225:swing_donchian` at 1.6, which left the book on 2026-08-29. So the
    /// field is a lookup that currently answers the same for all twenty-two,
    /// not a knob nobody has turned: the last sleeve to carry a boost carried it
    /// for DRAWDOWN rather than for return
    /// ([[swing-donchian-is-load-bearing-for-drawdown]],
    /// [[low-loss-lift-sleeves-are-drawdown-dampers]]).
    pub(super) scale: f64,
    /// `exness_combined_strategies.SHOWN_EQUITY`: the virtual balance this
    /// sleeve is SHOWN, as a multiple of the real one.
    ///
    /// ACCESS, NOT LEVERAGE. Each value is the smallest rung that leaves the
    /// sleeve with no refused orders at its calibration risk; the two ETHUSD
    /// cells at 3.0 are there because ETHUSD realises about 60% annualised
    /// against a 20% target, so the EWMA throttle sits at a 0.33 median and
    /// every order is sized to a third. 3.0 is 1/0.33 -- it restores the size
    /// the sleeve had BEFORE vol targeting rather than adding risk on top of it.
    ///
    /// `ethusd:pullback` joined the book on 2026-09-04 WITHOUT one, and that is
    /// the evidence the field is a measured setting rather than a per-market
    /// constant: it fills at 1.0, so a multiplier would be leverage.
    ///
    /// It scales the risk REQUEST only. The margin ceiling in `quantity` reads
    /// real equity, so this cannot lift a margin refusal
    /// ([[shown-equity-cannot-fix-a-margin-refusal]]).
    pub(super) shown_equity: f64,
    /// Whether the book sizes this sleeve as an IMPORT rather than as a cell.
    ///
    /// FALSE FOR EVERY MEMBER SINCE 2026-09-04, and the field is kept rather
    /// than deleted because the distinction it draws is real and the next
    /// imported cell will need it. `exness_combined_strategies.EXTERNAL` still
    /// lists six keys; all six are NQ or ETHUSD drift cells that left the book.
    ///
    /// The import branch floors to the 0.01 lot STEP and never consults
    /// `volume_min`, which on a market with a 0.05 floor is a five-fold
    /// difference on every small order -- so a cell sized on the wrong branch
    /// does not merely round differently, it eats the gross-exposure budget and
    /// crowds other sleeves out.
    pub(super) sized_as_import: bool,
    /// Which machine trades it.
    pub(super) engine: EngineKind,
}

/// Which machine a sleeve runs on.
///
/// ONE ARM, AND THE ENUM IS KEPT ANYWAY. It carried `Ofi` and `Drift` until
/// 2026-09-04 -- two hand-written NQ implementations that were never
/// `exness_families` cells -- and both left with the symbol. Keeping the enum
/// costs nothing and is what makes `params()` return an `Option` rather than a
/// bare `Params`, which is the shape a future import needs; collapsing it to a
/// bare field would have to be undone the first time one is seated.
pub(super) enum EngineKind {
    Family(Params),
}

impl SleeveSpec {
    /// The fitted cell. `None` is unreachable while every member is a family
    /// cell, and is the return the two imports used to take.
    pub(super) fn params(&self) -> Option<Params> {
        match &self.engine {
            EngineKind::Family(params) => Some(*params),
        }
    }
}

/// THE ONLY REMAINING `match` OVER SLEEVES.
///
/// Every other per-sleeve answer is a field read off what this returns, so a new
/// sleeve is a new file plus one arm here rather than an arm in ten places.
pub(super) const fn spec(sleeve: super::Sleeve) -> &'static SleeveSpec {
    use super::Sleeve as S;
    match sleeve {
        S::UsdjpyVolumeThrust => &usdjpy_volume_thrust::SPEC,
        S::AudusdZscore => &audusd_zscore::SPEC,
        S::EthusdConfluence => &ethusd_confluence::SPEC,
        S::EthusdKalman => &ethusd_kalman::SPEC,
        S::EthusdVolatilityBreakout => &ethusd_volatility_breakout::SPEC,
        S::GbpjpyTrap => &gbpjpy_trap::SPEC,
        S::UkoilXmaCross => &ukoil_xma_cross::SPEC,
        S::EurjpyTwoStage => &eurjpy_two_stage::SPEC,
        S::UsdjpyPullback => &usdjpy_pullback::SPEC,
        S::EthusdObvBreak => &ethusd_obv_break::SPEC,
        S::Jp225VolumeThrust => &jp225_volume_thrust::SPEC,
        S::UkoilLevelConfluence => &ukoil_level_confluence::SPEC,
        S::Jp225VolRegime => &jp225_vol_regime::SPEC,
        S::Jp225MomentumStack => &jp225_momentum_stack::SPEC,
        S::UsdjpyKendall => &usdjpy_kendall::SPEC,
        S::Jp225Cusum => &jp225_cusum::SPEC,
        S::Jp225ObvDivergence => &jp225_obv_divergence::SPEC,
        S::Jp225Kalman => &jp225_kalman::SPEC,
        S::EurjpyGatedOrb => &eurjpy_gated_orb::SPEC,
        S::UsdjpyFracdiff => &usdjpy_fracdiff::SPEC,
        S::UsdjpyHalfLife => &usdjpy_half_life::SPEC,
        S::Jp225BreakRetest => &jp225_break_retest::SPEC,
        S::Jp225VolatilityBreakout => &jp225_volatility_breakout::SPEC,
        S::EthusdPullback => &ethusd_pullback::SPEC,
    }
}
