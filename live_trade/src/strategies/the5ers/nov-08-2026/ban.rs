//! The5ers' prohibited-practices limits, applied to the whole book live
//! (`the5ers_combined_strategies.comply`, the5ers.com/faqs/prohibited-trading-practices,
//! updated 2026-11-03). An entry the layer refuses is held as a GHOST: the
//! strategy keeps the position the backtest holds, nothing is sent, and its
//! exit is swallowed -- exactly as the Python book drops the trade.
//!
//! Checked in this order, as in Python:
//!
//!   rollover         no entry 16:45-18:15 NY ("EAs that scalp during the rollover")
//!   bulk             no entry within 60 s of the account's previous entry
//!   max_open         at most 5 positions open on the account
//!   same_instrument  never two positions on one instrument (no stacking, no hedge)
//!   opposed          no entry against an open position in its correlated group
//!   group_cap        at most 2 same-way positions in one correlated group
//!   news             no order within 2 min of a related red folder (entries
//!                    refused here; exits are deferred by the slot)
//!
//! The 1% per-trade risk cap lives in the sizing (`cfd::book_lots`).

use crate::backtest::types::Side;

pub const BULK_GAP_SECONDS: i64 = 60;
pub const MAX_OPEN: usize = 5;
pub const GROUP_MAX_SAME: usize = 2;
/// NY minutes `[start, end)` in which no entry is taken.
pub const ROLLOVER_BLOCK: (i64, i64) = (16 * 60 + 45, 18 * 60 + 15);

/// The user, 2026-11-08: no weekend trading -- no entry from Friday 17:00 to
/// Sunday 17:00 NY (`the5ers_combined_strategies.in_weekend`).
pub fn in_weekend(ts: i64) -> bool {
    let weekday = (ts.div_euclid(86_400) + 3).rem_euclid(7); // Monday = 0
    let minute = ts.div_euclid(60).rem_euclid(1_440);
    (weekday == 4 && minute >= 17 * 60) || weekday == 5 || (weekday == 6 && minute < 17 * 60)
}

/// `the5ers_combined_strategies.CORRELATED`: groups and how each instrument
/// moves with its group.
const CORRELATED: &[&[(&str, i32)]] = &[
    &[("us500", 1), ("ustec", 1), ("us30", 1), ("us2000", 1)],
    &[("de40", 1), ("fr40", 1), ("stoxx50", 1), ("uk100", 1)],
    &[("hk50", 1), ("aus200", 1), ("jp225", 1)],
    &[("xauusd", 1), ("xagusd", 1), ("xaueur", 1), ("xptusd", 1)],
    &[("ukoil", 1), ("usoil", 1)],
    &[
        ("btc", 1),
        ("ethusd", 1),
        ("ltcusd", 1),
        ("solusd", 1),
        ("xrpusd", 1),
        ("dogeusd", 1),
        ("btceur", 1),
        ("etheur", 1),
    ],
    &[("eurusd", 1), ("gbpusd", 1), ("usdchf", -1)],
    &[("audusd", 1), ("nzdusd", 1)],
    &[
        ("usdjpy", 1),
        ("eurjpy", 1),
        ("gbpjpy", 1),
        ("audjpy", 1),
        ("nzdjpy", 1),
        ("cadjpy", 1),
        ("chfjpy", 1),
    ],
];

fn group_of(market: &str) -> Option<(usize, i32)> {
    CORRELATED
        .iter()
        .enumerate()
        .find_map(|(index, group)| group.iter().find(|(m, _)| *m == market).map(|(_, sign)| (index, *sign)))
}

fn sign(side: Side) -> i32 {
    match side {
        Side::Long => 1,
        Side::Short => -1,
    }
}

/// One open position on the account: its market and side.
#[derive(Clone, Debug)]
pub struct Held {
    pub market: &'static str,
    pub side: Side,
}

/// What the layer knows of one The5ers account at a decision.
#[derive(Clone, Debug, Default)]
pub struct View {
    pub held: Vec<Held>,
    /// NY-wall second of the account's last entry sent.
    pub last_entry: Option<i64>,
}

impl View {
    /// Why an entry on `market` / `side` at NY-wall `now` must not be sent,
    /// or `None` to send it.
    pub fn refuse(&self, market: &str, side: Side, now: i64) -> Option<&'static str> {
        let minute = now.div_euclid(60).rem_euclid(1_440);
        if in_weekend(now) {
            return Some("no weekend trading (Friday 17:00 - Sunday 17:00 NY)");
        }
        if (ROLLOVER_BLOCK.0..ROLLOVER_BLOCK.1).contains(&minute) {
            return Some("the5ers ban layer: rollover window (16:45-18:15 NY)");
        }
        if self.last_entry.is_some_and(|last| now - last < BULK_GAP_SECONDS) {
            return Some("the5ers ban layer: bulk (an entry under 60 s ago)");
        }
        if self.held.len() >= MAX_OPEN {
            return Some("the5ers ban layer: 5 positions already open");
        }
        if self.held.iter().any(|held| held.market == market) {
            return Some("the5ers ban layer: a position on this instrument is open");
        }
        if let Some((group, s)) = group_of(market) {
            let direction = sign(side) * s;
            let same: Vec<&Held> = self
                .held
                .iter()
                .filter(|held| group_of(held.market).is_some_and(|(g, _)| g == group))
                .collect();
            if same.iter().any(|held| sign(held.side) * group_of(held.market).map_or(1, |(_, s)| s) != direction) {
                return Some("the5ers ban layer: opposite position open in a correlated instrument");
            }
            if same.len() >= GROUP_MAX_SAME {
                return Some("the5ers ban layer: 2 same-way positions already open in its group");
            }
        }
        None
    }

    /// Records an entry sent at `now`.
    pub fn record(&mut self, market: &'static str, side: Side, now: i64) {
        self.held.push(Held { market, side });
        self.last_entry = Some(now);
    }
}

/// NY wall clock now, in seconds.
pub fn ny_now() -> i64 {
    let utc = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    utc + crate::backtest::data::new_york_offset_seconds(utc)
}

#[cfg(test)]
mod tests {
    use super::*;

    const H9: i64 = 9 * 3_600 + 30 * 60;

    fn view(held: &[(&'static str, Side)], last: Option<i64>) -> View {
        View {
            held: held.iter().map(|(market, side)| Held { market, side: *side }).collect(),
            last_entry: last,
        }
    }

    #[test]
    fn mirrors_the_python_comply_selftest() {
        // bulk: an entry a second after another
        assert!(view(&[], Some(H9)).refuse("xauusd", Side::Long, H9 + 1).unwrap().contains("bulk"));
        assert!(view(&[], Some(H9)).refuse("xauusd", Side::Long, H9 + 60).is_none());
        // one position per instrument
        assert!(view(&[("xauusd", Side::Long)], None).refuse("xauusd", Side::Long, H9).unwrap().contains("instrument"));
        // opposing in a group
        assert!(view(&[("us500", Side::Long)], None).refuse("ustec", Side::Short, H9).unwrap().contains("opposite"));
        // negative correlation: long EURUSD + short USDCHF is the same bet
        assert!(view(&[("eurusd", Side::Long)], None).refuse("usdchf", Side::Short, H9).is_none());
        assert!(view(&[("eurusd", Side::Long)], None).refuse("usdchf", Side::Long, H9).unwrap().contains("opposite"));
        // group cap
        assert!(view(&[("btc", Side::Long), ("ethusd", Side::Long)], None)
            .refuse("solusd", Side::Long, H9)
            .unwrap()
            .contains("same-way"));
        // max open
        let five = [
            ("eurusd", Side::Long),
            ("xauusd", Side::Long),
            ("us500", Side::Long),
            ("de40", Side::Long),
            ("ukoil", Side::Long),
        ];
        assert!(view(&five, None).refuse("audusd", Side::Long, H9).unwrap().contains("5 positions"));
        // rollover
        assert!(view(&[], None).refuse("eurusd", Side::Long, 17 * 3_600).unwrap().contains("rollover"));
        assert!(view(&[], None).refuse("eurusd", Side::Long, 18 * 3_600 + 15 * 60).is_none());
        // weekend: 1970-01-02 was a Friday, 01-04 a Sunday
        let fri = 86_400;
        assert!(view(&[], None).refuse("btc", Side::Long, fri + 17 * 3_600).unwrap().contains("weekend"));
        assert!(view(&[], None).refuse("btc", Side::Long, fri + 16 * 3_600).is_none());
        assert!(view(&[], None).refuse("btc", Side::Long, fri + 86_400 + 12 * 3_600).unwrap().contains("weekend"));
        assert!(view(&[], None).refuse("btc", Side::Long, fri + 2 * 86_400 + 16 * 3_600).unwrap().contains("weekend"));
        assert!(view(&[], None).refuse("btc", Side::Long, fri + 2 * 86_400 + 18 * 3_600 + 15 * 60).is_none());
    }
}

/// One standalone trade, for `comply`.
#[derive(Clone, Debug)]
pub struct Attempt {
    /// The sleeve's position in the book (`BOOK` order): ties go best first.
    pub member: usize,
    pub sleeve: String,
    pub market: &'static str,
    pub side: Side,
    /// Real New York entry and exit stamps (the exit candle's own stamp).
    pub entry: i64,
    pub exit: i64,
    pub bar_seconds: i64,
}

/// `the5ers_combined_strategies.comply`, exactly: walk every trade in entry
/// order (ties by member order) and keep it unless one of the ban rules
/// refuses it. A kept position counts as open until `exit + bar_seconds + 60`.
/// Returns the kept `(sleeve, entry)` pairs and the per-rule refusal counts.
pub fn comply(mut attempts: Vec<Attempt>) -> (std::collections::HashSet<(String, i64)>, std::collections::BTreeMap<&'static str, usize>) {
    attempts.sort_by_key(|a| (a.entry, a.member));
    let mut held: Vec<(i64, &'static str, Option<usize>, i32)> = Vec::new();
    let mut last: Option<i64> = None;
    let mut kept = std::collections::HashSet::new();
    let mut counts = std::collections::BTreeMap::new();
    for a in attempts {
        let ts = a.entry;
        held.retain(|p| p.0 > ts);
        let minute = ts.div_euclid(60).rem_euclid(1_440);
        let reason = if in_weekend(ts) {
            Some("weekend")
        } else if (ROLLOVER_BLOCK.0..ROLLOVER_BLOCK.1).contains(&minute) {
            Some("rollover")
        } else if last.is_some_and(|l| ts - l < BULK_GAP_SECONDS) {
            Some("bulk")
        } else if held.len() >= MAX_OPEN {
            Some("max_open")
        } else if held.iter().any(|p| p.1 == a.market) {
            Some("same_instrument")
        } else {
            match group_of(a.market) {
                Some((group, s)) => {
                    let direction = sign(a.side) * s;
                    let same: Vec<_> = held.iter().filter(|p| p.2 == Some(group)).collect();
                    if same.iter().any(|p| p.3 != direction) {
                        Some("opposed")
                    } else if same.len() >= GROUP_MAX_SAME {
                        Some("group_cap")
                    } else {
                        None
                    }
                }
                None => None,
            }
        };
        if let Some(reason) = reason {
            *counts.entry(reason).or_default() += 1;
            continue;
        }
        let group = group_of(a.market);
        held.push((
            a.exit + a.bar_seconds + 60,
            a.market,
            group.map(|g| g.0),
            group.map_or(0, |(_, s)| sign(a.side) * s),
        ));
        last = Some(ts);
        kept.insert((a.sleeve, ts));
        *counts.entry("kept").or_default() += 1;
    }
    (kept, counts)
}
