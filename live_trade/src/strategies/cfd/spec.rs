//! What a sleeve IS: the spec every member file declares, read by the
//! engine and by the backtest through `Sleeve`.

use super::contracts::Instrument;
use super::family::Params;

pub(crate) struct SleeveSpec {
    /// The name the API and the UI use.
    pub(crate) display: &'static str,
    /// The stable identifier the API exposes and the live rows key on, matching
    /// the Python sleeve key with its colon replaced so it is URL-safe.
    pub(crate) id: &'static str,
    /// The identifier a finished backtest is STORED under: the uppercase of
    /// `id`. A third spelling exists because saved rows outlive the code that
    /// wrote them, so a display name can be reworded without orphaning them.
    pub(crate) code: &'static str,
    /// `"<market>:<family>"`, the key Python identifies this sleeve by.
    ///
    /// It exists for ONE reason: the gross-exposure cap is
    /// first-come-first-served, so the order entries are offered the budget in
    /// decides who gets it. `replay` sorts its pending trades alphabetically on
    /// this string, and a book that offers them in a different order refuses a
    /// different set.
    pub(crate) python_key: &'static str,
    /// The market whose bars step this sleeve, lower-cased to match
    /// `RunRequest::symbol`.
    pub(crate) market: &'static str,
    /// The sealed book this sleeve is a member of, which decides the broker
    /// that prices it and the book's own dials (`Book`).
    pub(crate) book: super::Book,
    /// The frozen broker spec this sleeve sizes against.
    pub(crate) contract: Instrument,
    /// `exness_combined_strategies.SLEEVE_SCALE`.
    ///
    /// EVERY MEMBER SITS AT 1.0, and the dict is not empty -- it still names
    /// `jp225:swing_donchian` at 1.6, which left the book on 2026-08-29. So the
    /// field is a lookup that currently answers the same for all twenty-two,
    /// not a knob nobody has turned: the last sleeve to carry a boost carried it
    /// for DRAWDOWN rather than for return
    /// ([[swing-donchian-is-load-bearing-for-drawdown]],
    /// [[low-loss-lift-sleeves-are-drawdown-dampers]]).
    pub(crate) scale: f64,
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
    pub(crate) shown_equity: f64,
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
    pub(crate) sized_as_import: bool,
    /// `EXNESS_BAR_LOOKBACKS=1` (the barlb wave): every INDICATOR length is a
    /// fixed BAR count at its daily-chart setting -- ATR 14, volatility 20/100,
    /// trend EMAs 20/50 -- instead of a number of sessions. Stops, exits and
    /// annualising stay in sessions (`periods`).
    pub(crate) bar_lookbacks: bool,
    /// The `off` window (`EXNESS_WINDOW=off`): `Some(cash session)` in REAL New
    /// York minutes. The sleeve then runs on the broker day (`contract` carries
    /// it, on the +7h clock) and takes no entry, and fills nothing, while the
    /// cash session is open.
    pub(crate) off_hours: Option<(i64, i64)>,
    /// A New York CLOCK WINDOW (`EXNESS_WINDOW=w0500-1100`, the ncf search):
    /// the sleeve runs on the broker day's +7h clock with the window as its
    /// session (`Instrument::window`). Like `off`, it marks and throttles on
    /// every bar of the broker day and never fills on the day's last candle;
    /// unlike `off`, no cash session is refused.
    pub(crate) clock_window: bool,
    /// `CFD_BROKER_HOURS=1`: a vendor candle the broker's own minute table did
    /// not quote at all is dropped before anything reads it, so no entry or
    /// exit can land in a closed market.
    pub(crate) broker_hours: bool,
    /// `EXNESS_FILLS_ONLY=1`: a signal whose fill candle has no broker entry
    /// price is not taken.
    pub(crate) fills_only: bool,
    /// `hold="multiday"` (the swing-bar `sb_` wave): positions hold through the
    /// session close until their own exit, paying the broker's rollover.
    pub(crate) multiday: bool,
    /// Weekdays a NEW entry may be taken on, Monday = bit 0; `None` for every
    /// day. `exness_combined_strategies.WEEKEND_ONLY` cells carry `WEEKEND`.
    /// It gates the signal only, as `cfd_families.ENTRY_DAYS` gates
    /// `tradeable`, so a position still exits by its own rules.
    pub(crate) entry_days: Option<u8>,
    /// The sleeve's own bar, when it is not the 30-minute candle every
    /// `cfd_families` cell trades. `None` is 30 minutes and the contract's own
    /// session count and `vol_target`.
    pub(crate) timeframe: Option<Timeframe>,
    /// Which machine trades it.
    pub(crate) engine: EngineKind,
}

/// A TikTok cell's bar size and the two figures its context builds from it.
#[derive(Clone, Copy)]
pub(crate) struct Timeframe {
    pub(crate) bar_minutes: i64,
    /// `periods(symbol, bar)["session"]`.
    pub(crate) per_session: usize,
    /// `ctx["vol_target"]` at this bar: the median trailing volatility over the
    /// cell's in-sample window, the admission throttle's target.
    pub(crate) vol_target: f64,
}

/// Which machine a sleeve runs on.
///
/// ONE ARM, AND THE ENUM IS KEPT ANYWAY. It carried `Ofi` and `Drift` until
/// 2026-09-04 -- two hand-written NQ implementations that were never
/// `cfd_families` cells -- and both left with the symbol. Keeping the enum
/// costs nothing and is what makes `params()` return an `Option` rather than a
/// bare `Params`, which is the shape a future import needs; collapsing it to a
/// bare field would have to be undone the first time one is seated.
/// Saturday and Sunday: bits 5 and 6 with Monday at bit 0.
pub(crate) const WEEKEND: u8 = 0b0110_0000;

pub(crate) enum EngineKind {
    Family(Params),
}

impl SleeveSpec {
    /// The fitted cell. `None` is unreachable while every member is a family
    /// cell, and is the return the two imports used to take.
    pub(crate) fn params(&self) -> Option<Params> {
        match &self.engine {
            EngineKind::Family(params) => Some(*params),
        }
    }
}

/// THE ONLY REMAINING `match` OVER SLEEVES.
///
/// Every other per-sleeve answer is a field read off what this returns, so a new
/// sleeve is a new file plus one arm here rather than an arm in ten places.
pub(crate) const fn spec(sleeve: super::Sleeve) -> &'static SleeveSpec {
    use super::Sleeve as S;
    use crate::strategies::exness::oct_12_2026::sleeves::*;
    use crate::strategies::fundednext::oct_21_2026::sleeves as fn_book;
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
        S::UsdjpyAroon => &usdjpy_aroon::SPEC,
        S::EthusdBreakRetest => &ethusd_break_retest::SPEC,
        S::EurjpySwingMa => &eurjpy_swing_ma::SPEC,
        S::EthusdRoofing => &ethusd_roofing::SPEC,
        S::EthusdLevelConfluence => &ethusd_level_confluence::SPEC,
        S::UsdjpyRvol => &usdjpy_rvol::SPEC,
        S::EthusdEfficiency => &ethusd_efficiency::SPEC,
        S::EthusdCci => &ethusd_cci::SPEC,
        S::EthusdLinregTrend => &ethusd_linreg_trend::SPEC,
        S::UstecLuxBodyMomentum5m => &ustec_lux_body_momentum_5m::SPEC,
        S::UsdjpyQpMaCross30m => &usdjpy_qp_ma_cross_30m::SPEC,
        S::Us500LuxSwingSweepMss5m => &us500_lux_swing_sweep_mss_5m::SPEC,
        S::GbpjpyQpMaCross15m => &gbpjpy_qp_ma_cross_15m::SPEC,
        S::EurjpyQpMaCross60m => &eurjpy_qp_ma_cross_60m::SPEC,
        S::EthusdTdEmaMacd15m => &ethusd_td_ema_macd_15m::SPEC,
        S::GbpjpyLuxalgoManipulation120m => &gbpjpy_luxalgo_manipulation_120m::SPEC,
        S::GbpjpyLuxHtfManipulation15m => &gbpjpy_lux_htf_manipulation_15m::SPEC,
        S::EthusdLuxBodyMomentum15m => &ethusd_lux_body_momentum_15m::SPEC,
        S::GbpjpyLuxSupplyDemand30m => &gbpjpy_lux_supply_demand_30m::SPEC,
        S::De40LuxSweepReclaim60m => &de40_lux_sweep_reclaim_60m::SPEC,
        S::UsdjpyLuxHtfManipulation60m => &usdjpy_lux_htf_manipulation_60m::SPEC,
        S::EurjpyLuxNyVwapPullback15m => &eurjpy_lux_ny_vwap_pullback_15m::SPEC,
        S::Uk100XmaCross => &uk100_xma_cross::SPEC,
        S::UsoilXmaCross => &usoil_xma_cross::SPEC,
        S::BtcJump => &btc_jump::SPEC,
        S::EthusdSbBollinger240mNat => &ethusd_sb_bollinger_240m_nat::SPEC,
        S::GbpusdQpRsi2Pullback30mOff => &gbpusd_qp_rsi2_pullback_30m_off::SPEC,
        S::UsdjpySar30mBarlbOff => &usdjpy_sar_30m_barlb_off::SPEC,
        S::UsdjpyMomentumStack30mBarlbOff => &usdjpy_momentum_stack_30m_barlb_off::SPEC,
        S::UsdjpyFailedBreak5mBarlb => &usdjpy_failed_break_5m_barlb::SPEC,
        S::UsdjpyAdaptivePullback60mBarlb => &usdjpy_adaptive_pullback_60m_barlb::SPEC,
        S::EurjpyDtRsiDipVol15m => &eurjpy_dt_rsi_dip_vol_15m::SPEC,
        S::EurjpyQcEmaCross30m => &eurjpy_qc_ema_cross_30m::SPEC,
        S::GbpjpyMqlBigMoveCont60m => &gbpjpy_mql_big_move_cont_60m::SPEC,
        S::UsdjpyYfAlphatrend30m => &usdjpy_yf_alphatrend_30m::SPEC,
        S::FnUsdchfSkew1hAfternoon => &fn_book::usdchf_skew_1h_afternoon::SPEC,
        S::FnChfjpyOvernight5mNight => &fn_book::chfjpy_overnight_5m_night::SPEC,
        S::FnXauusdObvDivergence5mLondonOpen => &fn_book::xauusd_obv_divergence_5m_london_open::SPEC,
        S::FnUstecConfluence15mTokyoOpen => &fn_book::ustec_confluence_15m_tokyo_open::SPEC,
        S::FnGbpaudCci1hMorning => &fn_book::gbpaud_cci_1h_morning::SPEC,
        S::FnUstecPtlOrbCandle30mOff => &fn_book::ustec_ptl_orb_candle_30m_off::SPEC,
        S::FnUs500DayOfWeek5mEvening => &fn_book::us500_day_of_week_5m_evening::SPEC,
        S::FnUs30Amihud30mNyOpen => &fn_book::us30_amihud_30m_ny_open::SPEC,
        S::FnChfjpyStochastic15mEvening => &fn_book::chfjpy_stochastic_15m_evening::SPEC,
        S::FnUsdcadXmaCross5mMorning => &fn_book::usdcad_xma_cross_5m_morning::SPEC,
        S::FnXauusdFloorPivot15mMorning => &fn_book::xauusd_floor_pivot_15m_morning::SPEC,
        S::FnGbpusdGatedFade30mMorning => &fn_book::gbpusd_gated_fade_30m_morning::SPEC,
        S::FnUsdjpyTwoStage30mLondonOpen => &fn_book::usdjpy_two_stage_30m_london_open::SPEC,
        S::FnAudjpyGatedDonchian1hMorning => &fn_book::audjpy_gated_donchian_1h_morning::SPEC,
        S::FnGbpjpyQuantileBreak5mNyMidday => &fn_book::gbpjpy_quantile_break_5m_ny_midday::SPEC,
        S::FnUs500AvFirstHourFib30mAfternoon => &fn_book::us500_av_first_hour_fib_30m_afternoon::SPEC,
        S::FnXauusdQuantileBreak15mEvening => &fn_book::xauusd_quantile_break_15m_evening::SPEC,
        S::FnUs500TwoStage15mNyOpen => &fn_book::us500_two_stage_15m_ny_open::SPEC,
        S::FnFr40Efficiency1hMorning => &fn_book::fr40_efficiency_1h_morning::SPEC,
        S::FnUstecGatedOrb30mLondonOpen => &fn_book::ustec_gated_orb_30m_london_open::SPEC,
        S::FnUs2000AdaptivePullback30mMorning => &fn_book::us2000_adaptive_pullback_30m_morning::SPEC,
        S::FnUs500XmaCross30mLondonOpen => &fn_book::us500_xma_cross_30m_london_open::SPEC,
        S::FnXauusdQpRsiExtreme15mOffhour => &fn_book::xauusd_qp_rsi_extreme_15m_offhour::SPEC,
        S::FnUs2000SwingBreak30mMorning => &fn_book::us2000_swing_break_30m_morning::SPEC,
        S::FnXauusdXmaCross30mNyClose => &fn_book::xauusd_xma_cross_30m_ny_close::SPEC,
        S::FnNzdusdTrap1hOffhour => &fn_book::nzdusd_trap_1h_offhour::SPEC,
        S::FnUstecKendall5mLondonOpen => &fn_book::ustec_kendall_5m_london_open::SPEC,
        S::FnGbpusdRegimeBreakout5mNyMidday => &fn_book::gbpusd_regime_breakout_5m_ny_midday::SPEC,
        S::FnGbpjpyConfluence30mTokyo => &fn_book::gbpjpy_confluence_30m_tokyo::SPEC,
        S::FnEurusdStructure30mBarlbOff => &fn_book::eurusd_structure_30m_barlb_off::SPEC,
        S::FnEurjpyRvol60mBarlb => &fn_book::eurjpy_rvol_60m_barlb::SPEC,
        S::FnUstecFracdiff5mLondon => &fn_book::ustec_fracdiff_5m_london::SPEC,
        S::FnGbpjpyDayOfWeek30mBarlb => &fn_book::gbpjpy_day_of_week_30m_barlb::SPEC,
        S::FnUsdjpyKalman30mNyOpen => &fn_book::usdjpy_kalman_30m_ny_open::SPEC,
        S::FnAudjpyDayOfWeek30mLondon => &fn_book::audjpy_day_of_week_30m_london::SPEC,
        S::FnUs500LinregTrend30mAfternoon => &fn_book::us500_linreg_trend_30m_afternoon::SPEC,
        S::FnGbpjpyQuantileBreak30mAfternoon => &fn_book::gbpjpy_quantile_break_30m_afternoon::SPEC,
        S::FnUsdcadAdaptivePullback1hAfternoon => &fn_book::usdcad_adaptive_pullback_1h_afternoon::SPEC,
        S::FnAudjpyEfficiency30mBarlb => &fn_book::audjpy_efficiency_30m_barlb::SPEC,
        S::FnEuraudDayOfWeek5mAfternoon => &fn_book::euraud_day_of_week_5m_afternoon::SPEC,
        S::FnEurusdAdaptivePullback15mMorning => &fn_book::eurusd_adaptive_pullback_15m_morning::SPEC,
        S::FnEurjpyTrOscillatorRebound60m => &fn_book::eurjpy_tr_oscillator_rebound_60m::SPEC,
        S::FnNzdusdTwoStage5mRth => &fn_book::nzdusd_two_stage_5m_rth::SPEC,
        S::FnEurjpyCusum1hEarlyMorning => &fn_book::eurjpy_cusum_1h_early_morning::SPEC,
        S::FnChfjpyAdaptivePullback5mOffhour => &fn_book::chfjpy_adaptive_pullback_5m_offhour::SPEC,
        S::FnUsdchfFloorPivot15mMorning => &fn_book::usdchf_floor_pivot_15m_morning::SPEC,
        S::FnUk100Confluence1hMorning => &fn_book::uk100_confluence_1h_morning::SPEC,
        S::FnUk100ValueArea60mBarlb => &fn_book::uk100_value_area_60m_barlb::SPEC,
        S::FnGbpusdValueArea30mBarlbOff => &fn_book::gbpusd_value_area_30m_barlb_off::SPEC,
        S::FnCadjpyMomentum5mRth => &fn_book::cadjpy_momentum_5m_rth::SPEC,
    }
}
