//! The The5ers contracts this book's sleeves size against, frozen from the
//! `the5ers` broker profile each parity fixture records. Named `T5_<MARKET>`.
//! The struct and its meaning are `cfd::Instrument`'s.

use crate::strategies::cfd::{Commission, Instrument};

/// The vendor feeds' scheduled last print: the same Dukascopy tables signal both
/// prop books, so this is FundedNext's schedule.
pub(crate) fn last_print_minute(market: &str, shift_hours: i64, session_close: i64, day: i64) -> Option<i64> {
    crate::strategies::fundednext::oct_21_2026::contracts::last_print_minute(market, shift_hours, session_close, day)
}

impl Instrument {
    /// `AUDJPY` (forex), its cash session as the base clock.
    pub(crate) const T5_AUDJPY: Self = Self {
        session: (0, 840),
        shift_hours: 0,
        per_session: 29,
        calendar: 252.0,
        multiplier: 630.7755385246161,
        contract_size: 100000.0,
        fx_to_usd: 0.0063077553852461605,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.9188,
        financing_long: 0.0156,
        financing_short: 0.016100000000000003,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `AUDUSD` (forex), its cash session as the base clock.
    pub(crate) const T5_AUDUSD: Self = Self {
        session: (60, 840),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.0,
        financing_long: 9.800000000000001e-05,
        financing_short: 8.2e-05,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `BTCEUR` (crypto), its cash session as the base clock.
    pub(crate) const T5_BTCEUR: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 365.0,
        multiplier: 1.1137,
        contract_size: 1.0,
        fx_to_usd: 1.1137,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 1.9389,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::NotionalPct(0.04),
    };
    /// `CADJPY` (forex), its cash session as the base clock.
    pub(crate) const T5_CADJPY: Self = Self {
        session: (120, 900),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 630.7755385246161,
        contract_size: 100000.0,
        fx_to_usd: 0.0063077553852461605,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.3529,
        financing_long: 0.01798,
        financing_short: 0.01933,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `CHFJPY` (forex), its cash session as the base clock.
    pub(crate) const T5_CHFJPY: Self = Self {
        session: (60, 840),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 630.7755385246161,
        contract_size: 100000.0,
        fx_to_usd: 0.0063077553852461605,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.4666,
        financing_long: 0.008400000000000001,
        financing_short: 0.0097,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `DAX40` (index), its cash session as the base clock.
    pub(crate) const T5_DE40: Self = Self {
        session: (180, 690),
        shift_hours: 0,
        per_session: 18,
        calendar: 252.0,
        multiplier: 1.0,
        contract_size: 1.0,
        fx_to_usd: 1.1137,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 500.0,
        spread_bp: 0.6284,
        financing_long: 6.0,
        financing_short: 6.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `ETHEUR` (crypto), its cash session as the base clock.
    pub(crate) const T5_ETHEUR: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 365.0,
        multiplier: 1.1137,
        contract_size: 1.0,
        fx_to_usd: 1.1137,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 5.8266,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::NotionalPct(0.04),
    };
    /// `EURAUD` (forex), its cash session as the base clock.
    pub(crate) const T5_EURAUD: Self = Self {
        session: (60, 840),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 68640.0,
        contract_size: 100000.0,
        fx_to_usd: 0.6864,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.6241,
        financing_long: 7.7e-05,
        financing_short: 7.000000000000001e-05,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `EURJPY` (forex), its cash session as the base clock.
    pub(crate) const T5_EURJPY: Self = Self {
        session: (60, 870),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 630.7755385246161,
        contract_size: 100000.0,
        fx_to_usd: 0.0063077553852461605,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.6203,
        financing_long: 0.01728,
        financing_short: 0.0193,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `EURUSD` (forex), its cash session as the base clock.
    pub(crate) const T5_EURUSD: Self = Self {
        session: (120, 780),
        shift_hours: 0,
        per_session: 23,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.0,
        financing_long: 9.900000000000001e-05,
        financing_short: 9.800000000000001e-05,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `GBPJPY` (forex), its cash session as the base clock.
    pub(crate) const T5_GBPJPY: Self = Self {
        session: (0, 810),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 630.7755385246161,
        contract_size: 100000.0,
        fx_to_usd: 0.0063077553852461605,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 1.0814,
        financing_long: 0.0317,
        financing_short: 0.0348,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `GBPUSD` (forex), its cash session as the base clock.
    pub(crate) const T5_GBPUSD: Self = Self {
        session: (30, 840),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.0,
        financing_long: 8.400000000000001e-05,
        financing_short: 8e-05,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `HSIHKD` (index), its cash session as the base clock.
    pub(crate) const T5_HK50: Self = Self {
        session: (180, 600),
        shift_hours: 6,
        per_session: 15,
        calendar: 252.0,
        multiplier: 0.1,
        contract_size: 1.0,
        fx_to_usd: 0.1275671290124646,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 500.0,
        spread_bp: 2.8594,
        financing_long: 0.6176,
        financing_short: 0.237,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `NZDUSD` (forex), its cash session as the base clock.
    pub(crate) const T5_NZDUSD: Self = Self {
        session: (120, 900),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 99999.99999999999,
        contract_size: 100000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.7144,
        financing_long: 2.7000000000000002e-05,
        financing_short: 1.1000000000000001e-05,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `SOLUSD` (crypto), its cash session as the base clock.
    pub(crate) const T5_SOLUSD: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 365.0,
        multiplier: 1.0,
        contract_size: 1.0,
        fx_to_usd: 1.0,
        volume_min: 1.0,
        volume_step: 1.0,
        volume_max: 300.0,
        spread_bp: 3.3074,
        financing_long: 0.0,
        financing_short: 0.0,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::NotionalPct(0.04),
    };
    /// `UK100` (index), its cash session as the base clock.
    pub(crate) const T5_UK100: Self = Self {
        session: (180, 690),
        shift_hours: 0,
        per_session: 18,
        calendar: 252.0,
        multiplier: 1.0,
        contract_size: 1.0,
        fx_to_usd: 1.33476,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 500.0,
        spread_bp: 1.4822,
        financing_long: 1.8,
        financing_short: 1.8,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `US30` (index), its cash session as the base clock.
    pub(crate) const T5_US30: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 252.0,
        multiplier: 1.0,
        contract_size: 1.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 500.0,
        spread_bp: 0.5651,
        financing_long: 7.2,
        financing_short: 7.2,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `SP500` (index), its cash session as the base clock.
    pub(crate) const T5_US500: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 252.0,
        multiplier: 1.0,
        contract_size: 1.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 500.0,
        spread_bp: 1.2115,
        financing_long: 1.44,
        financing_short: 1.44,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
    /// `USDCAD` (forex), its cash session as the base clock.
    pub(crate) const T5_USDCAD: Self = Self {
        session: (150, 870),
        shift_hours: 0,
        per_session: 25,
        calendar: 252.0,
        multiplier: 70768.90414351935,
        contract_size: 100000.0,
        fx_to_usd: 0.7077190921379486,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.2863,
        financing_long: 9.2e-05,
        financing_short: 0.000101,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `USDCHF` (forex), its cash session as the base clock.
    pub(crate) const T5_USDCHF: Self = Self {
        session: (30, 750),
        shift_hours: 0,
        per_session: 25,
        calendar: 252.0,
        multiplier: 121839.78068839478,
        contract_size: 100000.0,
        fx_to_usd: 1.2184571895066465,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.4902,
        financing_long: 0.00011300000000000002,
        financing_short: 0.00012800000000000002,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `USDJPY` (forex), its cash session as the base clock.
    pub(crate) const T5_USDJPY: Self = Self {
        session: (0, 780),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 630.7755385246161,
        contract_size: 100000.0,
        fx_to_usd: 0.0063077553852461605,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 0.0,
        financing_long: 0.013800000000000002,
        financing_short: 0.01365,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::UsdPerLot(4.0),
    };
    /// `NAS100` (index), its cash session as the base clock.
    pub(crate) const T5_USTEC: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        calendar: 252.0,
        multiplier: 1.0,
        contract_size: 1.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 500.0,
        spread_bp: 0.7249,
        financing_long: 3.575,
        financing_short: 3.438,
        // Unread: every member carries its own `Timeframe`.
        vol_target: 0.0,
        commission: Commission::None,
    };
}

/// The5ers' name for a market (lower case, its `the5ers_<broker>_1m` table)
/// and its point, the unit the M1 `spread` column counts in.
pub(crate) fn broker_source(market: &str) -> Option<(&'static str, f64)> {
    Some(match market {
        "audjpy" => ("audjpy", 0.001),
        "audusd" => ("audusd", 1e-05),
        "btceur" => ("btceur", 0.001),
        "cadjpy" => ("cadjpy", 0.001),
        "chfjpy" => ("chfjpy", 0.001),
        "de40" => ("dax40", 0.01),
        "etheur" => ("etheur", 0.001),
        "euraud" => ("euraud", 1e-05),
        "eurjpy" => ("eurjpy", 0.001),
        "eurusd" => ("eurusd", 1e-05),
        "gbpjpy" => ("gbpjpy", 0.001),
        "gbpusd" => ("gbpusd", 1e-05),
        "hk50" => ("hsihkd", 0.01),
        "nzdusd" => ("nzdusd", 1e-05),
        "solusd" => ("solusd", 0.01),
        "uk100" => ("uk100", 0.01),
        "us30" => ("us30", 0.01),
        "us500" => ("sp500", 0.01),
        "usdcad" => ("usdcad", 1e-05),
        "usdchf" => ("usdchf", 1e-05),
        "usdjpy" => ("usdjpy", 0.001),
        "ustec" => ("nas100", 0.01),
        _ => return None,
    })
}
