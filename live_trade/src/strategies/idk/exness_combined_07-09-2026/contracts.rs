//! The frozen per-market contract specs.
//!
//! PER MARKET, NOT PER SLEEVE. Eight sleeves trade JP225, four each trade
//! USDJPY and ETHUSD, two each UKOIL and EURJPY, and one each AUDUSD and
//! GBPJPY; all of them size against the same broker spec, so it lives here and
//! each sleeve file names the one it uses.

/// Everything `exness_families.resolve` assembles for one symbol, frozen from
/// the 2026-08-16 Exness Pro snapshot and the 2024 volume profile.
///
/// `multiplier` is ALREADY in the account currency -- MT5 reports
/// `trade_tick_value` converted -- so it must never be multiplied by
/// `fx_to_usd` again. That double conversion was a silent 159x error on the JPY
/// crosses ([[mt5-tick-value-is-account-currency]]). The margin ceiling keeps
/// its conversion because `entry * contract_size` really is quoted in the profit
/// currency.
#[derive(Clone, Copy)]
pub(in crate::strategies::idk) struct Instrument {
    /// `(open, close)` in minutes past New York midnight, on the SHIFTED clock.
    pub(super) session: (i64, i64),
    /// Hours added to every timestamp before the day is cut. The Asian cash
    /// sessions straddle New York midnight, and without the shift one session
    /// lands in two calendar days, which breaks every prior-day anchor.
    pub(super) shift_hours: i64,
    /// 30-minute buckets in one session: `max(2, (close - open) / 30 + 1)`.
    pub(super) per_session: usize,
    /// Trading days a year, for annualising realised volatility.
    pub(super) calendar: f64,
    /// Account currency per full price unit per lot.
    pub(super) multiplier: f64,
    pub(super) contract_size: f64,
    pub(super) fx_to_usd: f64,
    pub(super) volume_min: f64,
    pub(super) volume_step: f64,
    pub(super) volume_max: f64,
    /// The sealed session spread in basis points, WITHOUT the 0.2 bp slippage
    /// allowance that `cost_bp` adds to it. Read by the shadow account only --
    /// the engine bills the live fill through `sleeve_spread_bp`, and billing it
    /// twice would make the admission filter stricter than the Python's.
    pub(super) spread_bp: f64,
    /// `financing_price(symbol, +1)`: one night of carry on a LONG lot, in
    /// PRICE units, always >= 0 and subtracted from the trade.
    ///
    /// `abs(min(0, swap_long)) * point`, frozen from the same snapshot as the
    /// spread. `swap_mode` is 1 (points) on every symbol here, so the charge is
    /// points per lot per night and `points * point` is a price distance --
    /// exactly like `cost_price`, with no basis-point round trip.
    ///
    /// A POSITIVE SWAP IS TREATED AS ZERO. Every reading on this account is
    /// negative or zero, but a credit is not something to bank in a backtest:
    /// it is the first number a broker changes.
    ///
    /// THIS IS THE SECOND COST LEG AND IT IS NOT SMALL. NQ charges 5.717 price
    /// points a night against a 0.299 bp spread -- about 6.5x the spread, per
    /// night ([[overnight-financing-is-not-charged-at-all]]).
    pub(super) financing_long: f64,
    /// The same for a SHORT lot, from `swap_short`.
    pub(super) financing_short: f64,
    /// The median 20-session realised volatility over the in-sample window,
    /// which is what `context` records as `vol_target`. Unused by the book's own
    /// sizing -- kept because it is the figure a standalone replay throttles
    /// against, and dropping it would make the two impossible to reconcile.
    #[allow(dead_code)]
    pub(super) vol_target: f64,
}

impl Instrument {
    pub(super) const USDJPY: Self = Self {
        session: (0, 780),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 627.647_889_533_971_6,
        contract_size: 100_000.0,
        fx_to_usd: 0.006_276_754_666_767_094_5,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 300.0,
        spread_bp: 0.439_8,
        financing_long: 0.0,
        financing_short: 0.0137,
        vol_target: 0.063_042_706_205_010_69,
    };
    pub(super) const AUDUSD: Self = Self {
        session: (60, 840),
        shift_hours: 0,
        per_session: 27,
        calendar: 252.0,
        multiplier: 99_999.999_999_999_99,
        contract_size: 100_000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 200.0,
        spread_bp: 0.849_6,
        financing_long: 0.0,
        financing_short: 1.8e-05,
        vol_target: 0.089_616_619_025_316_94,
    };
    pub(super) const JP225: Self = Self {
        session: (60, 480),
        shift_hours: 6,
        per_session: 15,
        calendar: 252.0,
        multiplier: 0.006_276_478_895_339_715,
        contract_size: 1.0,
        fx_to_usd: 0.006_276_754_666_767_094_5,
        // Three lots, not 0.01: JP225 quotes a 0.01 step and refuses anything
        // under three whole lots.
        volume_min: 3.0,
        volume_step: 0.01,
        volume_max: 5_000.0,
        spread_bp: 0.349_9,
        financing_long: 0.0,
        financing_short: 0.0,
        vol_target: 0.165_435_761_155_651_37,
    };
    pub(super) const ETHUSD: Self = Self {
        session: (570, 960),
        shift_hours: 0,
        per_session: 14,
        // Crypto quotes every calendar day, so its realised volatility is
        // annualised over 365 rather than 252.
        calendar: 365.0,
        multiplier: 1.0,
        contract_size: 1.0,
        fx_to_usd: 1.0,
        volume_min: 0.1,
        volume_step: 0.01,
        volume_max: 2_000.0,
        spread_bp: 3.721_1,
        financing_long: 0.411,
        financing_short: 0.0,
        vol_target: 0.691_193_026_192_656_5,
    };
    pub(super) const GBPJPY: Self = Self {
        session: (0, 810),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 627.647_889_533_971_6,
        contract_size: 100_000.0,
        fx_to_usd: 0.006_276_754_666_767_094_5,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 200.0,
        spread_bp: 0.743_7,
        financing_long: 0.0,
        financing_short: 0.0214,
        vol_target: 0.088_587_750_860_468_38,
    };
    pub(super) const UKOIL: Self = Self {
        session: (540, 870),
        shift_hours: 0,
        per_session: 12,
        calendar: 252.0,
        multiplier: 1_000.0,
        contract_size: 1_000.0,
        fx_to_usd: 1.0,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 100.0,
        spread_bp: 2.744_5,
        financing_long: 0.0,
        financing_short: 0.0753,
        vol_target: 0.286_921_871_081_246_34,
    };
    pub(super) const EURJPY: Self = Self {
        session: (60, 870),
        shift_hours: 0,
        per_session: 28,
        calendar: 252.0,
        multiplier: 627.647_889_533_971_6,
        contract_size: 100_000.0,
        fx_to_usd: 0.006_276_754_666_767_094_5,
        volume_min: 0.01,
        volume_step: 0.01,
        volume_max: 200.0,
        spread_bp: 0.598_7,
        financing_long: 0.0,
        financing_short: 0.0089,
        vol_target: 0.073_098_303_217_424_5,
    };
}
