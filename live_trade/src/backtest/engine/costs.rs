//! What a fill costs, per market and per sleeve: the EXNESS PRO model, and the
//! only one.
//!
//! THERE IS NOTHING TO CONFIGURE, and that is the point. Costs used to be three
//! per-environment numbers a user typed in -- spread, slippage and commission --
//! and every one of them was replaced by the figures below the moment the run
//! was an `idk` run, which every run is. What the environment screen showed and
//! what the backtest charged were unrelated numbers.
//!
//! What is charged instead: the EXNESS LIVE-FILL MODEL (`backtest::fills`) --
//! the broker's own spread in the entry minute plus a 0.2 bp slippage allowance,
//! billed once at entry, with the entry and every exit re-priced to where the
//! broker's market was when the order actually landed. The per-market constants
//! below are the FALL-BACK for a minute the broker table does not hold, and the
//! share that fell back is reported with every run (`fill_coverage`). Commission is zero, because a real minimum-lot
//! round trip on this account booked 0.0000 on both deals
//! ([[pro-commission-is-measured-zero]]).

use super::*;
use serde::Serialize;

pub(super) fn market_point_value(market: &str) -> f64 {
    match market {
        // FX majors: 100,000 units of base, one price unit each.
        "audusd" | "gbpusd" => 99_999.999_999_999_99,
        // JPY crosses: the same lot, converted at 0.0062767546667670945.
        "usdjpy" | "gbpjpy" | "eurjpy" => 627.647_889_533_971_6,
        "jp225" => 0.006_276_478_895_339_715,
        "ukoil" => 1_000.0,
        // Spot aluminium and the FTSE CFD both carry a contract size of one; the
        // FTSE is quoted in GBP, so its point value IS its USD rate.
        "xalusd" => 1.0,
        "uk100" => 1.353_35,
        // NQ, ETHUSD, BTC and spot nickel all carry a contract size of one.
        "nq" | "ethusd" | "btc" | "xniusd" => 1.0,
        _ => 1.0,
    }
}

/// Entry cost in BASIS POINTS of the fill price, per MARKET.
///
/// Python charges `cost_bp = spread_bp + SLIPPAGE_BP` once at entry and takes it
/// out of the trade's points, so these are the sealed `exness_pro_specs.json`
/// spreads plus the 0.2 bp conservative slippage allowance, and nothing else --
/// a measured round trip on this account booked 0.0000 commission on both deals
/// ([[pro-commission-is-measured-zero]]).
///
/// PROPORTIONAL AND NOT ABSOLUTE, deliberately. A fixed 0.2-point charge is
/// 80 bp on silver and 0.008 bp on an index, so it ranks price level rather than
/// edge ([[absolute-spread-inverts-the-symbol-ranking]]) -- and on a market that
/// moves an order of magnitude inside a run it charges the early trades many
/// times what it charges the late ones.
///
/// The spreads were sampled from a WEEK OF SESSION-FILTERED TICK HISTORY, not
/// from a live quote: a closed-market quote reads up to 6x the in-session spread
/// and reorders the whole ranking ([[weekend-spreads-are-not-tradeable]]).
pub fn market_spread_bp(market: &str) -> Option<f64> {
    /// The conservative allowance, already folded into every figure returned.
    const SLIPPAGE_BP: f64 = 0.2;
    let spread = match market {
        "nq" => 0.298_9,
        "usdjpy" => 0.439_8,
        "audusd" => 0.849_6,
        "jp225" => 0.349_9,
        "ethusd" => 3.721_1,
        "gbpjpy" => 0.743_7,
        "gbpusd" => 0.518_4,
        "ukoil" => 2.744_5,
        "eurjpy" => 0.598_7,
        // The widest quote in the book by an order of magnitude, and the reason
        // this sleeve had to clear its null on a proportional charge rather than
        // an absolute one.
        "xalusd" => 9.021_4,
        "uk100" => 0.698_8,
        "btc" => 1.103,
        // Spot nickel, the second-widest quote in the book.
        "xniusd" => 7.189_7,
        _ => return None,
    };
    Some(spread + SLIPPAGE_BP)
}

/// The same figure for one SLEEVE.
///
/// Resolved through `Sleeve` rather than `market_symbol`, which falls through to
/// "nq" for an unregistered name. That fall-through would hand an unknown
/// strategy NQ's 0.30 bp quote -- the cheapest in the book -- instead of
/// refusing to price it.
pub(super) fn sleeve_spread_bp(name: &str) -> Option<f64> {
    market_spread_bp(Sleeve::from_display(name)?.market())
}

/// The entry charge for one sleeve: `(absolute points, proportional bp)`.
///
/// Exactly one of the two is ever non-trivial, and since 2026-09-04 it is always
/// the second: every member is an `ef.backtest` cell and pays the market's
/// PROPORTIONAL Pro spread. The absolute leg existed for the two `combined_book`
/// imports, which were repriced at a flat `SLIPPAGE_POINTS = 0.2` and left the
/// book with NQ.
///
/// The pair is returned together rather than as two calls because that is what
/// stops both being applied at once, which would bill a flat sleeve twice.
pub(super) fn sleeve_entry_cost(name: &str) -> (f64, Option<f64>) {
    (0.0, sleeve_spread_bp(name))
}

/// Broker `volume_min` per market, in the MT5 lot units every sleeve emits.
///
/// Deliberately separate from Forex's 0.01 quantity step: a request that lands
/// under this is a REFUSAL and not a rounding, and on a small balance it is the
/// binding constraint rather than the edge
/// ([[four-hundred-dollars-selects-the-sleeves-for-you]]).
///
/// JP225's floor is THREE whole lots, not a hundredth of one. These must equal
/// `Instrument::<MARKET>.volume_min` in `exness_combined`, or the engine and the
/// strategy disagree about what is placeable.
pub fn broker_minimum(market: &str) -> f64 {
    match market {
        "nq" => 0.05,
        "ethusd" => 0.10,
        "jp225" => 3.0,
        "uk100" => 0.05,
        "usdjpy" | "audusd" | "gbpjpy" | "gbpusd" | "ukoil" | "eurjpy" | "xalusd" | "btc"
        | "xniusd" => 0.01,
        _ => 0.0,
    }
}

/// The entry charge for one fill, in whichever of the two shapes applies.
///
/// ENTRY PAYS THE WHOLE ROUND TRIP; THE EXIT PAYS NOTHING. Splitting it half per
/// leg bills the same total, but charging it up front is how the account
/// actually experiences the cost, and it makes the entry price the one really
/// paid -- which matters because strategies measure their stop and target from
/// it.
pub(super) struct EntryCosts {
    /// ABSOLUTE charge, account currency per lot, converted below. Non-zero only
    /// for the two imported sleeves, which bill a flat allowance instead of a
    /// market spread.
    pub(super) spread: f64,
    pub(super) point_value: f64,
    /// `Some(bp)` charges the spread PROPORTIONALLY, in basis points of the fill
    /// price, and needs no rate or point-value conversion because a fraction of
    /// the price is already denominated in the price's own currency. Exactly one
    /// of this and `spread` is ever non-zero.
    pub(super) spread_bp: Option<f64>,
}

/// The entry price after costs, in the instrument's own quote currency.
///
/// `spread` is an ACCOUNT currency amount -- 0.2 means twenty cents -- but it is
/// being added to a price quoted in the instrument's currency, so it is divided
/// by `quote_rate` and `point_value` first. For every dollar-quoted instrument
/// the rate is 1.0 and this is exactly what it always was.
///
/// That conversion mattered entirely on ETHBTC. Its prices are BTC, around
/// 0.035, so charging 0.2 unconverted made an entry cost 5.6x the price itself
/// and produced a NEGATIVE fill of -0.1646 on the first validation run.
pub(super) fn fill_entry(raw: f64, buying: bool, costs: &EntryCosts) -> f64 {
    let EntryCosts {
        spread,
        point_value,
        spread_bp,
    } = *costs;
    let value = if point_value.is_finite() && point_value > 0.0 {
        point_value
    } else {
        1.0
    };
    let cost = match spread_bp {
        Some(bp) => raw.abs() * bp / 10_000.0,
        None => spread / value,
    };
    if buying { raw + cost } else { raw - cost }
}

/// One night of carry for a sleeve, in price units per lot. Zero for anything
/// the registry does not know, which is a refusal to guess rather than a claim
/// that financing is free.
pub(super) fn sleeve_financing_per_night(name: &str, long: bool) -> f64 {
    Sleeve::from_display(name).map_or(0.0, |sleeve| sleeve.financing_per_night(long))
}

/// Calendar nights a position spanned: DATE BOUNDARIES CROSSED.
///
/// `cfd_families.financing_nights`. Counting calendar nights IS the
/// triple-swap rule rather than an approximation of it: a broker charges one
/// swap per trading night and three on one weekday to cover the weekend, so a
/// full week is 3 + 1 + 1 + 1 + 1 = 7 charges and a week has 7 calendar nights.
/// The two conventions agree exactly, which is why no weekday table is needed.
///
/// THE STAMPS MUST BE ON THE SLEEVE'S OWN CLOCK -- see `financing_shift`.
pub(super) fn financing_nights(entry_ts: i64, exit_ts: i64) -> i64 {
    (exit_ts.div_euclid(86_400) - entry_ts.div_euclid(86_400)).max(0)
}

/// Seconds added to a trade's stamps before its nights are counted.
///
/// THE ASIAN SESSIONS CROSS REAL MIDNIGHT AND MUST NOT BE CHARGED FOR IT.
/// `ef.backtest` records `position["ts"]` and the exit stamp from bars
/// `all_bars` has ALREADY shifted, so `financing_nights` there counts boundaries
/// on the SHIFTED clock. HK50's session is 03:00-10:00 shifted, which is
/// 21:00-04:00 real: every one of its trades crosses a real date boundary and
/// none crosses a shifted one.
///
/// Counting on the real clock therefore billed one night of HK50 carry -- 3.36
/// price points, 0.030 dollars at the broker minimum -- on 43 of its 122 trades,
/// which is a tenth of the sleeve's whole contribution. JP225 is on the same
/// clock and quotes both swap legs at exactly 0.0, so it hid the bug.
fn financing_shift(name: &str) -> i64 {
    Sleeve::from_display(name).map_or(0, |sleeve| sleeve.shift_hours() * 3_600)
}

/// Realised P&L for one closed position, in ACCOUNT currency.
///
/// The gross leg is denominated in the instrument's quote currency and is
/// crossed here. Nothing is subtracted: Exness Pro bills the whole round trip
/// through the entry spread ([[pro-commission-is-measured-zero]]), so it has
/// already been paid by the time `pos.entry` was computed.
pub(super) fn net_pnl(pos: &Position, exit: f64) -> f64 {
    let movement = if pos.side == Side::Long {
        exit - pos.entry
    } else {
        pos.entry - exit
    };
    movement * pos.quantity * pos.point_value
}

/// The same, less OVERNIGHT FINANCING for every calendar night the position
/// spanned.
///
/// THE SECOND COST LEG, AND FOR A POSITION THAT SLEEPS IT IS THE LARGER ONE.
/// Until this existed the engine charged the spread once at entry and nothing
/// else, so a trade held over a weekend was priced exactly like a thirty-minute
/// scalp ([[overnight-financing-is-not-charged-at-all]]).
///
/// It bites more often than "swing sleeve" suggests. A session family flattens
/// on the bar whose minute reaches the close -- and on a half day or a holiday
/// that bar does not exist, so the position sleeps and is charged. That is
/// exactly where the port and the Python first disagreed: an NQ trade entered
/// 2025-07-03 and closed 2025-07-04 booked one night at 5.717 points, and the
/// port booked nothing.
pub(super) fn net_pnl_after_financing(pos: &Position, exit: f64, exit_ts: i64, name: &str) -> f64 {
    let shift = financing_shift(name);
    let nights = financing_nights(pos.ts + shift, exit_ts + shift);
    if nights == 0 {
        return net_pnl(pos, exit);
    }
    let per_night = sleeve_financing_per_night(name, pos.side == Side::Long);
    net_pnl(pos, exit) - nights as f64 * per_night * pos.quantity * pos.point_value
}

/// One market's line in the Exness Pro cost model, for the API to report.
#[derive(Serialize)]
pub struct MarketCost {
    pub market: &'static str,
    /// Entry charge in basis points of the fill price, slippage allowance
    /// included.
    pub spread_bp: f64,
    /// Smallest order the broker accepts, in MT5 lots.
    pub minimum_lots: f64,
}

/// THE COST MODEL, as one self-describing rule.
///
/// This is what the environment's "Backtest Rules" used to be three editable
/// numbers for. It is served rather than stored because the figures are compiled
/// into the engine: a rule a user could type would be a number the run then
/// ignored, which is exactly what the spread/slippage/commission rows were.
#[derive(Serialize)]
pub struct CostModel {
    pub id: &'static str,
    pub name: &'static str,
    pub detail: &'static str,
    pub markets: Vec<MarketCost>,
}

/// The rule every backtest is priced under, built from the same tables the
/// engine charges so the two cannot drift.
pub fn cost_model() -> CostModel {
    CostModel {
        id: "exness_pro",
        name: "Exness Pro",
        detail: concat!(
            "Filled the way this account fills: the entry at the broker's price ",
            "one feed lag after the candle opens, every exit as a market order ",
            "one whole candle later (there is no broker-side stop), and the ",
            "broker's own spread in the entry minute plus a 0.2 bp slippage ",
            "allowance, all read off exness_<symbol>_1m. The per-market spread ",
            "below is charged only where that table has no minute. No ",
            "commission: a real round trip booked 0.0000 on both deals.",
        ),
        markets: crate::strategies::known_markets()
            .into_iter()
            .filter_map(|market| {
                Some(MarketCost {
                    market,
                    spread_bp: market_spread_bp(market)?,
                    minimum_lots: broker_minimum(market),
                })
            })
            .collect(),
    }
}
