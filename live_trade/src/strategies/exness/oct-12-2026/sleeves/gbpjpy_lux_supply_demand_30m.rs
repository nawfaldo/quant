//! `gbpjpy:lux_supply_demand@30m`, a TikTok-creator cell (`cfd_tt_families`),
//! seated 2026-09-30.
//!
//! A one-bar base left by an impulse bar three times its range that closes
//! through the last 5-bar swing; the first return into it that closes back out
//! is traded, stop beyond the zone, 2R.

use crate::strategies::cfd::Instrument;
use crate::strategies::cfd::{Direction, Exit, Family, Params, Trend, VolMode};
use crate::strategies::cfd::{Book, EngineKind, SleeveSpec, Timeframe};

pub(crate) const SPEC: SleeveSpec = SleeveSpec {
    display: "GBPJPY Lux Supply Demand 30m",
    id: "gbpjpy_lux_supply_demand_30m",
    code: "GBPJPY_LUX_SUPPLY_DEMAND_30M",
    python_key: "gbpjpy:lux_supply_demand@30m",
    market: "gbpjpy",
    book: Book::Exness,
    contract: Instrument::GBPJPY,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    bar_lookbacks: false,
    off_hours: None,
    clock_window: false,
    broker_hours: false,
    fills_only: false,
    multiday: false,
    entry_days: None,
    timeframe: Some(Timeframe {
        bar_minutes: 30,
        per_session: 28,
        vol_target: 0.088_587_750_860_468_38,
    }),
    engine: EngineKind::Family(Params {
        family: Family::LuxSupplyDemand {
            base_len: 1,
            impulse: 3.0,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(2.0),
        last_entry_minute: 690,
        stop_day: 0.2,
        trend: Trend::None,
        vol_mode: VolMode::Any,
    }),
};
