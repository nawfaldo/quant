//! The FundedNext contracts this book's sleeves size against, frozen from the
//! `fundednext_specs.json` snapshot the 2026-10-13/16 searches priced, as each
//! parity fixture records it. The struct and its meaning are `cfd::Instrument`'s.
//!
//! Named `FN_<MARKET>` because the Exness book declares the same markets on the
//! same type, at different figures: USDJPY is 629.14 a point here and 627.65
//! there, and every lot floor is 0.01.

use crate::strategies::cfd::{Commission, Instrument};

/// The minute (on a sleeve's OWN shifted clock, of the shifted day `day`) at
/// which `market`'s vendor feed prints its LAST bar on a normal day, when that
/// is before the sleeve's session close; `None` where the feed prints to the
/// close.
///
/// WHY IT EXISTS. Three FundedNext markets stop printing before the session
/// their cells were sealed on ends, so no candle is ever the session's last by
/// the clock. Python flattens on the last bar it HAS (`index + 1` is another
/// day); a live runtime cannot know a bar was the last until the next day's
/// arrives. So both live and every live-rules backtest end the day on the
/// candle holding this minute -- one rule, the same in both. Measured on
/// 2024-01..2026-10 (`fundednext-live-schedule` memory):
///
///   de40   22:14 Berlin Mon-Thu, 21:59 Berlin Fri (DAX vendor close)
///   ustec  16:14 New York, us500 the same
///   hk50   16:29 Hong Kong (UTC+8, no summer time)
///
/// Holidays and half days are NOT here: on those the feed stops earlier still,
/// and live's clock-driven overdue close (modelled in the backtest engine)
/// takes the position, identically in both.
pub(crate) fn last_print_minute(market: &str, shift_hours: i64, session_close: i64, day: i64) -> Option<i64> {
    use crate::backtest::data::{berlin_offset_seconds, new_york_offset_seconds};
    // Local exchange time on the calendar date `day` -> NY wall -> shifted.
    let at = |local_minute: i64, local_offset: &dyn Fn(i64) -> i64| -> i64 {
        let guess = day * 86_400 + local_minute * 60;
        let utc = guess - local_offset(guess);
        let ny = utc + new_york_offset_seconds(utc);
        ny + shift_hours * 3_600 - day * 86_400
    };
    let weekday = (day + 3).rem_euclid(7); // Monday = 0
    let seconds = match market {
        // Fridays the DAX feed stops at 21:59 Berlin -- every Friday of 2026 so
        // far; 2024-25 Fridays often printed one more bar at 22:00, which then
        // falls outside the day (no cell may enter that late).
        "de40" => at(if weekday == 4 { 21 * 60 + 59 } else { 22 * 60 + 14 }, &berlin_offset_seconds),
        // The US index CFDs print to 16:14 on EVERY clock: the cash-hours cells'
        // last candle (the 16:00 slot) never reaches its own end either.
        "ustec" | "us500" => at(16 * 60 + 14, &new_york_offset_seconds),
        "hk50" => at(16 * 60 + 29, &|_| 8 * 3_600),
        _ => return None,
    };
    // NOT clipped to the session close: a feed may print past it inside the
    // session's last candle (US500 16:00 slot, last print 16:14). Past the day's
    // end it is meaningless -- the DAX during the US/EU summer-time mismatch
    // weeks prints to the broker day's close.
    let _ = session_close;
    let minute = seconds.div_euclid(60);
    (minute < 1_440).then_some(minute)
}

impl Instrument {
    /// `AUDJPY` (forex), its cash session as the base clock.
    pub(crate) const FN_AUDJPY: Self = Self {
        session: (0, 840),
        shift_hours: 0,
        per_session: 29,
        calendar: 252.0,
        multiplier: 629.1365729672597,
        contract_size: 100000.0,
        fx_to_usd: 0.006323950698480355,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.9822,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `AUDUSD` (forex), its cash session as the base clock.
    pub(crate) const FN_AUDUSD: Self = Self {
        session: (60, 840),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.4266,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `CADJPY` (forex), its cash session as the base clock.
    pub(crate) const FN_CADJPY: Self = Self {
        session: (120, 810),
        shift_hours: 0,
        per_session: 24,
        calendar: 252.0,
        multiplier: 632.3350870093079,
        contract_size: 100000.0,
        fx_to_usd: 0.006323950698480355,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.8979,
        financing_long: 0.0,
        financing_short: 0.017293,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `CHFJPY` (forex), its cash session as the base clock.
    pub(crate) const FN_CHFJPY: Self = Self {
        session: (0, 870),
        shift_hours: 0,
        per_session: 30,
        calendar: 252.0,
        multiplier: 632.3350870093079,
        contract_size: 100000.0,
        fx_to_usd: 0.006323950698480355,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.7887,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `EURAUD` (forex), its cash session as the base clock.
    pub(crate) const FN_EURAUD: Self = Self {
        session: (60, 840),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 69186.00000000001,
        contract_size: 100000.0,
        fx_to_usd: 0.69186,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.8106,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `EURJPY` (forex), its cash session as the base clock.
    pub(crate) const FN_EURJPY: Self = Self {
        session: (60, 870),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 629.1365729672597,
        contract_size: 100000.0,
        fx_to_usd: 0.006323950698480355,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.5002,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `EURUSD` (forex), its cash session as the base clock.
    pub(crate) const FN_EURUSD: Self = Self {
        session: (120, 780),
        shift_hours: 0,
        per_session: 23,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.0872,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `FRA40` (index), its cash session as the base clock.
    pub(crate) const FN_FR40: Self = Self {
        session: (180, 690),
        shift_hours: 0,
        per_session: 18,
        calendar: 252.0,
        multiplier: 12.0,
        contract_size: 10.0,
        fx_to_usd: 1.12113,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 2.4824,
        financing_long: 2.10213,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `GBPAUD` (forex), its cash session as the base clock.
    pub(crate) const FN_GBPAUD: Self = Self {
        session: (60, 840),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 69186.00000000001,
        contract_size: 100000.0,
        fx_to_usd: 0.69186,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.7906,
        financing_long: 0.0,
        financing_short: 9.512000000000001e-05,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `GBPJPY` (forex), its cash session as the base clock.
    pub(crate) const FN_GBPJPY: Self = Self {
        session: (0, 810),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 629.1365729672597,
        contract_size: 100000.0,
        fx_to_usd: 0.006323950698480355,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.8571,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `GBPUSD` (forex), its cash session as the base clock.
    pub(crate) const FN_GBPUSD: Self = Self {
        session: (30, 840),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.1495,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `HK50` (index), its cash session as the base clock.
    pub(crate) const FN_HK50: Self = Self {
        session: (180, 600),
        shift_hours: 6,
        per_session: 15,
        calendar: 252.0,
        multiplier: 1.0,
        contract_size: 10.0,
        fx_to_usd: 0.12743593795399052,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 3.0679,
        financing_long: 2.82013,
        financing_short: 0.39846,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `NZDUSD` (forex), its cash session as the base clock.
    pub(crate) const FN_NZDUSD: Self = Self {
        session: (90, 810),
        shift_hours: 0,
        per_session: 25,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.7124,
        financing_long: 1.509e-05,
        financing_short: 2.211e-05,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `UK100` (index), its cash session as the base clock.
    pub(crate) const FN_UK100: Self = Self {
        session: (180, 690),
        shift_hours: 0,
        per_session: 18,
        calendar: 252.0,
        multiplier: 14.000000000000002,
        contract_size: 10.0,
        fx_to_usd: 1.32711,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 1.6955,
        financing_long: 1.80428,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `US2000` (index), its cash session as the base clock.
    pub(crate) const FN_US2000: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 252.0,
        multiplier: 10.0,
        contract_size: 10.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 3.5417,
        financing_long: 0.27490000000000003,
        financing_short: 0.0645,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `US30` (index), its cash session as the base clock.
    pub(crate) const FN_US30: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 252.0,
        multiplier: 10.0,
        contract_size: 10.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.3845,
        financing_long: 9.2831,
        financing_short: 1.6601,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `SPX500` (index), its cash session as the base clock.
    pub(crate) const FN_US500: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 252.0,
        multiplier: 10.0,
        contract_size: 10.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.753,
        financing_long: 1.04638,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `USDCAD` (forex), its cash session as the base clock.
    pub(crate) const FN_USDCAD: Self = Self {
        session: (150, 870),
        shift_hours: 0,
        per_session: 25,
        calendar: 252.0,
        multiplier: 70680.8688092394,
        contract_size: 100000.0,
        fx_to_usd: 0.7046719751955465,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.2851,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `USDCHF` (forex), its cash session as the base clock.
    pub(crate) const FN_USDCHF: Self = Self {
        session: (120, 840),
        shift_hours: 0,
        per_session: 25,
        calendar: 252.0,
        multiplier: 120869.29194768777,
        contract_size: 100000.0,
        fx_to_usd: 1.2090290287869812,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 1.4312,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `USDJPY` (forex), its cash session as the base clock.
    pub(crate) const FN_USDJPY: Self = Self {
        session: (0, 780),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 629.1365729672597,
        contract_size: 100000.0,
        fx_to_usd: 0.006323950698480355,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.445,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
    /// `NDX100` (index), its cash session as the base clock.
    pub(crate) const FN_USTEC: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 252.0,
        multiplier: 10.0,
        contract_size: 10.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 40.0,
        spread_bp: 0.5266,
        financing_long: 3.29382,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `XAUUSD` (commodity), its cash session as the base clock.
    pub(crate) const FN_XAUUSD: Self = Self {
        session: (480, 960),
        shift_hours: 0,
        per_session: 17,
        calendar: 252.0,
        multiplier: 100.0,
        contract_size: 100.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 50.0,
        spread_bp: 0.9147,
        financing_long: 0.5794,
        financing_short: 0.157,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(3.0),
    };
}
