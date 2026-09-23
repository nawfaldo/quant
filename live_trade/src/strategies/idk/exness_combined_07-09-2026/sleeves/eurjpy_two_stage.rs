//! `eurjpy:two_stage`, one `exness_families` cell.
//!
//! A Bollinger squeeze arms the trade; the break of the coil's own range takes
//! it.
//!
//! NOT `squeeze` WITH AN EXTRA BAR. That family enters ON the compression bar,
//! because the engine fills at the next open and cannot rest a stop order on a
//! level -- so it trades the pause and guesses the direction from where price
//! came from. This one waits for the break and takes whichever side actually
//! went, which on a coil that breaks downward after an uptrend is the OPPOSITE
//! trade.
//!
//! Added 2026-08-25 on DRAWDOWN contribution rather than on return
//! ([[low-loss-lift-sleeves-are-drawdown-dampers]]).

use super::super::contracts::Instrument;
use super::super::family::{Coil, Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::EURJPY;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "EURJPY Two Stage",
    id: "eurjpy_two_stage",
    code: "EURJPY_TWO_STAGE",
    python_key: "eurjpy:two_stage",
    market: "eurjpy",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::TwoStage {
            coil: Coil::Squeeze,
            // BARS, NOT SESSIONS, and that is deliberate: the coil is a shape in
            // the bar series, so cutting it at the close would make the family's
            // meaning depend on where in the day the compression happened.
            window: 6,
            // `p["compress"][0]`, the floor the bandwidth is measured against.
            compress: 5 * SESSION,
            // `p["session"]`: the Bollinger width's own period, which is NOT one
            // of the `zscore` periods.
            width_period: SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::RewardMultiple(1.0),
        last_entry_minute: 810,
        stop_day: 0.7,
        trend: Trend::Ema20d,
        vol_mode: VolMode::Any,
    }),
};
