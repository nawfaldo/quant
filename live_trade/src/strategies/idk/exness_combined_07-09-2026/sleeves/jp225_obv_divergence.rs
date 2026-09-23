//! `jp225:obv_divergence`, one `exness_families` cell.
//!
//! Price makes a new four-session extreme; cumulative volume does not follow --
//! and this cell trades WITH the break anyway.
//!
//! A PARTITION OF THE BREAKOUT FAMILY RATHER THAN A COMPETITOR. It fires on
//! exactly `donchian`'s trigger bars and keeps the subset where the flow
//! disagreed, so it is not merely uncorrelated with `jp225:volatility_breakout`
//! -- it is a slice of the same population, selected by the only reading an
//! OHLCV series has for "this move is not being paid for".
//!
//! `follow` INVERTS THE RAW SIDE HERE. The raw reading is the fade -- an
//! unpaid-for break is a break to sell -- so this cell buys the break, and the
//! polarity convention has to be read off the Python rather than off the axis
//! name. See `FamilyEngine::obv_divergence`.
//!
//! Seated 2026-09-03 by the decay screen's rebuild.

use super::super::contracts::Instrument;
use super::super::family::{Direction, Exit, Family, Params, Trend, VolMode};
use super::{EngineKind, SleeveSpec};

const CONTRACT: Instrument = Instrument::JP225;

/// 30-minute buckets in one session, the unit every period below counts in.
const SESSION: usize = CONTRACT.per_session;

pub(super) const SPEC: SleeveSpec = SleeveSpec {
    display: "JP225 OBV Divergence",
    id: "jp225_obv_divergence",
    code: "JP225_OBV_DIVERGENCE",
    python_key: "jp225:obv_divergence",
    market: "jp225",
    contract: CONTRACT,
    scale: 1.0,
    shown_equity: 1.0,
    sized_as_import: false,
    engine: EngineKind::Family(Params {
        family: Family::ObvDivergence {
            // `4 * p["session"]`, the shorter of the two rungs, and the SAME
            // window for both series: the price channel and the OBV channel have
            // to span the same bars or the comparison is a horizon difference
            // wearing a divergence label.
            channel: 4 * SESSION,
        },
        direction: Direction::Follow,
        exit: Exit::Trail(1.5),
        last_entry_minute: 420,
        stop_day: 0.2,
        trend: Trend::Ema50d,
        vol_mode: VolMode::Any,
    }),
};
