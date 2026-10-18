//! The frozen per-market contract specs.
//!
//! PER MARKET, NOT PER SLEEVE. Eight sleeves trade JP225, four each trade
//! USDJPY and ETHUSD, two each UKOIL and EURJPY, and one each AUDUSD and
//! GBPJPY; all of them size against the same broker spec, so it lives here and
//! each sleeve file names the one it uses.

/// Everything `cfd_families.resolve` assembles for one symbol, frozen from
/// the 2026-08-16 Exness Pro snapshot and the 2024 volume profile.
///
/// `multiplier` is ALREADY in the account currency -- MT5 reports
/// `trade_tick_value` converted -- so it must never be multiplied by
/// `fx_to_usd` again. That double conversion was a silent 159x error on the JPY
/// crosses ([[mt5-tick-value-is-account-currency]]). The margin ceiling keeps
/// its conversion because `entry * contract_size` really is quoted in the profit
/// currency.
#[derive(Clone, Copy)]
pub(crate) struct Instrument {
    /// `(open, close)` in minutes past New York midnight, on the SHIFTED clock.
    pub(crate) session: (i64, i64),
    /// Hours added to every timestamp before the day is cut. The Asian cash
    /// sessions straddle New York midnight, and without the shift one session
    /// lands in two calendar days, which breaks every prior-day anchor.
    pub(crate) shift_hours: i64,
    /// 30-minute buckets in one session: `max(2, (close - open) / 30 + 1)`.
    pub(crate) per_session: usize,
    /// Trading days a year, for annualising realised volatility.
    pub(crate) calendar: f64,
    /// Account currency per full price unit per lot.
    pub(crate) multiplier: f64,
    pub(crate) contract_size: f64,
    pub(crate) fx_to_usd: f64,
    pub(crate) volume_min: f64,
    pub(crate) volume_step: f64,
    pub(crate) volume_max: f64,
    /// The sealed session spread in basis points, WITHOUT the 0.2 bp slippage
    /// allowance that `cost_bp` adds to it. Read by the shadow account only --
    /// the engine bills the live fill through `sleeve_spread_bp`, and billing it
    /// twice would make the admission filter stricter than the Python's.
    pub(crate) spread_bp: f64,
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
    pub(crate) financing_long: f64,
    /// The same for a SHORT lot, from `swap_short`.
    pub(crate) financing_short: f64,
    /// The median 20-session realised volatility over the in-sample window,
    /// which is what `context` records as `vol_target`. Unused by the book's own
    /// sizing -- kept because it is the figure a standalone replay throttles
    /// against, and dropping it would make the two impossible to reconcile.
    #[allow(dead_code)]
    pub(crate) vol_target: f64,
    /// The broker's commission, charged ONCE at the open (`commission_price`).
    /// `None` on Exness Pro, where a real round trip booked 0.0000.
    pub(crate) commission: Commission,
}

/// `cfd_families.BROKERS[...]["commission"]` for one market's asset class.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Commission {
    /// Free: Exness Pro everywhere, FundedNext on indices.
    None,
    /// `("usd_per_lot", x)`: x USD per lot -- FundedNext forex, $7.
    UsdPerLot(f64),
    /// `("notional_pct", x)`: x percent of the open price -- FundedNext
    /// commodities, 0.0016%.
    NotionalPct(f64),
}

impl Instrument {
    /// `cfd_families.commission_price`: the commission for one trade in PRICE
    /// units per lot, so it rides the same `lots * multiplier` the gross move
    /// does.
    pub(crate) fn commission_price(&self, entry: f64) -> f64 {
        match self.commission {
            Commission::None => 0.0,
            Commission::UsdPerLot(usd) => {
                if self.multiplier != 0.0 {
                    usd / self.multiplier
                } else {
                    0.0
                }
            }
            Commission::NotionalPct(percent) => entry * percent / 100.0,
        }
    }

    /// `commission_price` in basis points of `entry`, for the engine's
    /// proportional entry charge. Zero wherever there is no commission.
    pub(crate) fn commission_bp(&self, entry: f64) -> f64 {
        match self.commission {
            Commission::None => 0.0,
            _ if entry > 0.0 => 1e4 * self.commission_price(entry) / entry,
            _ => 0.0,
        }
    }

    /// THE BROKER DAY, for an `off`-window cell (`EXNESS_WINDOW=off`): the
    /// same contract on the +7h clock that puts the 17:00 New York rollover at
    /// midnight, session 01:00-23:59 shifted (`BROKER_DAY`), 46 thirty-minute
    /// buckets. The cash session it must stay out of is the sleeve's
    /// `off_hours`, not a field here.
    pub(crate) const fn broker_day(self) -> Self {
        Self {
            session: (60, 1439),
            shift_hours: 7,
            per_session: 46,
            ..self
        }
    }

    /// A NEW YORK CLOCK WINDOW (`cfd_families.window_clock`): the broker
    /// day's +7h clock with `(open, close)` -- minutes past the 17:00 rollover
    /// -- as the session. The sleeve's own `Timeframe` carries its bucket count.
    pub(crate) const fn window(self, open: i64, close: i64) -> Self {
        Self {
            session: (open, close),
            shift_hours: 7,
            ..self
        }
    }

}
