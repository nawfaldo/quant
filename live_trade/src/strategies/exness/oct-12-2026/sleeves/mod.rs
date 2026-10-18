//! One file per sleeve: every strategy the book has carried, one definition each.
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
/// Everything the book knows about one sleeve.

pub(crate) mod audusd_zscore;
pub(crate) mod btc_jump;
pub(crate) mod de40_lux_sweep_reclaim_60m;
pub(crate) mod ethusd_break_retest;
pub(crate) mod ethusd_cci;
pub(crate) mod ethusd_confluence;
pub(crate) mod ethusd_efficiency;
pub(crate) mod ethusd_kalman;
pub(crate) mod ethusd_level_confluence;
pub(crate) mod ethusd_linreg_trend;
pub(crate) mod ethusd_lux_body_momentum_15m;
pub(crate) mod ethusd_obv_break;
pub(crate) mod ethusd_pullback;
pub(crate) mod ethusd_roofing;
pub(crate) mod ethusd_sb_bollinger_240m_nat;
pub(crate) mod ethusd_td_ema_macd_15m;
pub(crate) mod ethusd_volatility_breakout;
pub(crate) mod eurjpy_dt_rsi_dip_vol_15m;
pub(crate) mod eurjpy_gated_orb;
pub(crate) mod eurjpy_lux_ny_vwap_pullback_15m;
pub(crate) mod eurjpy_qc_ema_cross_30m;
pub(crate) mod eurjpy_qp_ma_cross_60m;
pub(crate) mod eurjpy_swing_ma;
pub(crate) mod eurjpy_two_stage;
pub(crate) mod gbpjpy_lux_htf_manipulation_15m;
pub(crate) mod gbpjpy_lux_supply_demand_30m;
pub(crate) mod gbpjpy_luxalgo_manipulation_120m;
pub(crate) mod gbpjpy_mql_big_move_cont_60m;
pub(crate) mod gbpjpy_qp_ma_cross_15m;
pub(crate) mod gbpjpy_trap;
pub(crate) mod gbpusd_qp_rsi2_pullback_30m_off;
pub(crate) mod jp225_break_retest;
pub(crate) mod jp225_cusum;
pub(crate) mod jp225_kalman;
pub(crate) mod jp225_momentum_stack;
pub(crate) mod jp225_obv_divergence;
pub(crate) mod jp225_vol_regime;
pub(crate) mod jp225_volatility_breakout;
pub(crate) mod jp225_volume_thrust;
pub(crate) mod uk100_xma_cross;
pub(crate) mod ukoil_level_confluence;
pub(crate) mod ukoil_xma_cross;
pub(crate) mod us500_lux_swing_sweep_mss_5m;
pub(crate) mod usdjpy_adaptive_pullback_60m_barlb;
pub(crate) mod usdjpy_aroon;
pub(crate) mod usdjpy_failed_break_5m_barlb;
pub(crate) mod usdjpy_fracdiff;
pub(crate) mod usdjpy_half_life;
pub(crate) mod usdjpy_kendall;
pub(crate) mod usdjpy_lux_htf_manipulation_60m;
pub(crate) mod usdjpy_momentum_stack_30m_barlb_off;
pub(crate) mod usdjpy_pullback;
pub(crate) mod usdjpy_qp_ma_cross_30m;
pub(crate) mod usdjpy_rvol;
pub(crate) mod usdjpy_sar_30m_barlb_off;
pub(crate) mod usdjpy_volume_thrust;
pub(crate) mod usdjpy_yf_alphatrend_30m;
pub(crate) mod usoil_xma_cross;
pub(crate) mod ustec_lux_body_momentum_5m;
