//! `us500:lux_swing_sweep_mss@5m`, a TikTok-creator cell (`cfd_tt_families`), seated 2026-09-28.
//!
//! A sweep of the last 5-bar swing, then within 5 bars a close through the last
//! 2-bar swing on the other side, with the 200-bar EMA; stop beyond the sweep, 2R.
//!
//! Its stop is the family's OWN distance, so `stop_day` is inert, and it runs
//! on 5-minute candles with the timeframe below.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec, Timeframe};

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "US500 Lux Swing Sweep MSS 5m",
    id: "us500_lux_swing_sweep_mss_5m",
    code: "US500_LUX_SWING_SWEEP_MSS_5M",
    python_key: "us500:lux_swing_sweep_mss@5m",
    market: "us500",
    contract: Instrument::US500,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 5,
        per_session: 79,
        vol_target: 0.131_522_007_209_779_1,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxSwingSweepMss {
            k: 5,
            w: 5,
            mss_entry: true,
            ema: 200,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 840,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
