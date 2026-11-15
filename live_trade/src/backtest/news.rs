//! The5ers' news rule: no order executes within 2 minutes of a Forex Factory
//! red folder in a related currency (`sandbox/research/the5ers_rules.py`).
//!
//! The calendar is `data/ff_calendar/*.json` in Forex Factory's export format
//! (`[{title, country, date, impact}]`, `country` the currency), written by
//! `py -m sandbox.research.the5ers_rules fetch-history` (2024 on) and
//! `fetch-news` (this week; run it weekly). Only `impact == "High"` counts.
//! Stamps are New York wall clock in seconds, the store's convention.
//!
//! Re-read when the directory changes (checked at most once a minute), so the
//! weekly fetch reaches a running live process.

use std::sync::{Mutex, OnceLock};

/// `the5ers_rules.RULES["news_seconds"]`.
pub const GUARD_SECONDS: f64 = 120.0;
/// A deferred close executes this long after the window ends
/// (`the5ers_combined_strategies.NEWS_DEFER_SECONDS`).
pub const DEFER_SECONDS: f64 = 5.0;

struct Calendar {
    dir: Option<std::path::PathBuf>,
    /// `(ny_stamp, currency)`, sorted, deduplicated.
    events: Vec<(i64, String)>,
    /// Directory signature (file count, newest mtime) at the last read.
    signature: (usize, u64),
    checked_at: u64,
}

fn calendar() -> &'static Mutex<Calendar> {
    static CAL: OnceLock<Mutex<Calendar>> = OnceLock::new();
    CAL.get_or_init(|| {
        Mutex::new(Calendar { dir: None, events: Vec::new(), signature: (0, 0), checked_at: 0 })
    })
}

/// Points the calendar at `dir` (`data/ff_calendar`). Called by whoever knows
/// the store root; the first call wins.
pub fn init(dir: std::path::PathBuf) {
    let mut cal = calendar().lock().expect("news lock");
    if cal.dir.is_none() {
        cal.dir = Some(dir);
        cal.checked_at = 0;
    }
}

fn default_dir() -> Option<std::path::PathBuf> {
    if let Ok(value) = std::env::var("FF_CALENDAR_DIR") {
        return Some(value.into());
    }
    ["data/ff_calendar", "../data/ff_calendar", "../../data/ff_calendar"]
        .iter()
        .map(std::path::PathBuf::from)
        .find(|path| path.is_dir())
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn signature(dir: &std::path::Path) -> (usize, u64) {
    let mut count = 0usize;
    let mut newest = 0u64;
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().is_some_and(|e| e == "json") {
                count += 1;
                if let Ok(meta) = entry.metadata()
                    && let Ok(modified) = meta.modified()
                    && let Ok(age) = modified.duration_since(std::time::UNIX_EPOCH)
                {
                    newest = newest.max(age.as_secs());
                }
            }
        }
    }
    (count, newest)
}

/// Parses `2026-11-06T13:30:00+00:00` (any offset) to a UTC epoch.
fn parse_iso(text: &str) -> Option<i64> {
    let text = text.trim();
    let (date, rest) = text.split_once('T')?;
    let mut d = date.split('-').map(|p| p.parse::<i64>());
    let (y, m, dd) = (d.next()?.ok()?, d.next()?.ok()?, d.next()?.ok()?);
    let (clock, offset) = if let Some(stripped) = rest.strip_suffix('Z') {
        (stripped, 0)
    } else if let Some(pos) = rest.rfind(['+', '-']).filter(|pos| *pos >= 8) {
        let (clock, off) = rest.split_at(pos);
        let sign = if off.starts_with('-') { -1 } else { 1 };
        let mut parts = off[1..].split(':').map(|p| p.parse::<i64>());
        let hours = parts.next()?.ok()?;
        let minutes = parts.next().and_then(|p| p.ok()).unwrap_or(0);
        (clock, sign * (hours * 3600 + minutes * 60))
    } else {
        (rest, 0)
    };
    let mut c = clock.split(':').map(|p| p.split('.').next().unwrap_or("0").parse::<i64>());
    let (hh, mm) = (c.next()?.ok()?, c.next()?.ok()?);
    let ss = c.next().and_then(|p| p.ok()).unwrap_or(0);
    let days = crate::backtest::data::parse_iso_days(&format!("{y:04}-{m:02}-{dd:02}"))?;
    Some(days * 86_400 + hh * 3600 + mm * 60 + ss - offset)
}

fn load(dir: &std::path::Path) -> Vec<(i64, String)> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.extension().is_some_and(|e| e == "json") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Ok(serde_json::Value::Array(rows)) = serde_json::from_str::<serde_json::Value>(&text) else {
            continue;
        };
        for row in rows {
            let impact = row.get("impact").and_then(|v| v.as_str()).unwrap_or("");
            if !impact.eq_ignore_ascii_case("high") {
                continue;
            }
            let (Some(date), Some(country)) =
                (row.get("date").and_then(|v| v.as_str()), row.get("country").and_then(|v| v.as_str()))
            else {
                continue;
            };
            let Some(utc) = parse_iso(date) else {
                continue;
            };
            let ny = utc + crate::backtest::data::new_york_offset_seconds(utc);
            out.push((ny, country.to_ascii_uppercase()));
        }
    }
    out.sort();
    out.dedup();
    out
}

/// Every red folder on disk, `(ny_stamp, currency)`, re-read when the files change.
pub fn events() -> Vec<(i64, String)> {
    let mut cal = calendar().lock().expect("news lock");
    let now = now_secs();
    if cal.checked_at == 0 || now >= cal.checked_at + 60 {
        cal.checked_at = now.max(1);
        if cal.dir.is_none() {
            cal.dir = default_dir();
        }
        if let Some(dir) = cal.dir.clone() {
            let sig = signature(&dir);
            if sig != cal.signature {
                cal.events = load(&dir);
                cal.signature = sig;
            }
        }
    }
    cal.events.clone()
}

/// `the5ers_rules.related_currencies`, for a study market OR a The5ers broker
/// symbol: FX pairs are their two legs; the rest are the currency each is
/// priced in.
pub fn related(symbol: &str) -> Vec<&'static str> {
    let symbol = symbol.to_ascii_lowercase();
    let fixed: &[&'static str] = match symbol.as_str() {
        "xauusd" | "xagusd" | "xptusd" | "xpdusd" | "ukoil" | "usoil" | "xbrusd" | "xtiusd" => &["USD"],
        "xaueur" => &["EUR", "USD"],
        "ustec" | "nas100" | "us500" | "sp500" | "us30" => &["USD"],
        "de40" | "dax40" | "fr40" | "f40eur" | "stoxx50" | "esxeur" => &["EUR"],
        "uk100" => &["GBP"],
        "aus200" | "asxaud" => &["AUD"],
        "hk50" | "hsihkd" => &["CNY", "HKD"],
        "btc" | "btcusd" | "ethusd" | "ltcusd" | "solusd" | "xrpusd" | "dogeusd" | "dogusd" => &["USD"],
        "btceur" | "etheur" => &["EUR"],
        _ => &[],
    };
    if !fixed.is_empty() {
        return fixed.to_vec();
    }
    const CCY: [&str; 9] = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY"];
    if symbol.len() == 6 && symbol.bytes().all(|b| b.is_ascii_alphabetic()) {
        let (a, b) = symbol.split_at(3);
        let up = |s: &str| CCY.iter().copied().find(|c| c.eq_ignore_ascii_case(s));
        return [up(a), up(b)].into_iter().flatten().collect();
    }
    Vec::new()
}

/// The related red-folder stamps for `symbol`, sorted.
pub fn related_events(symbol: &str) -> Vec<i64> {
    let legs = related(symbol);
    let mut out: Vec<i64> = events()
        .into_iter()
        .filter(|(_, ccy)| legs.iter().any(|leg| leg == ccy))
        .map(|(stamp, _)| stamp)
        .collect();
    out.dedup();
    out
}

/// `_defer`: whether an order on a market whose related events are `events`
/// executing at NY-wall `at` falls inside a +-guard window, and when it may
/// execute instead (chained windows push it on).
pub fn defer(at: f64, events: &[i64]) -> (bool, f64) {
    let mut at = at;
    let mut hit = false;
    loop {
        let lo = at - GUARD_SECONDS;
        let i = events.partition_point(|t| (*t as f64) < lo);
        if i >= events.len() || events[i] as f64 > at + GUARD_SECONDS {
            return (hit, at);
        }
        hit = true;
        let hi = at + GUARD_SECONDS;
        let j = events.partition_point(|t| (*t as f64) <= hi) - 1;
        at = events[j] as f64 + GUARD_SECONDS + DEFER_SECONDS;
    }
}

/// Whether an order on `symbol` executing at NY-wall `at` is inside a window.
pub fn blocked(symbol: &str, at: f64) -> bool {
    defer(at, &related_events(symbol)).0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defer_matches_the_python_selftest() {
        let t0 = 1_000_020;
        assert_eq!(defer((t0 + 2) as f64, &[t0]), (true, (t0 + 125) as f64));
        assert!(defer((t0 - 120) as f64, &[t0]).0);
        assert_eq!(defer((t0 + 121) as f64, &[t0]), (false, (t0 + 121) as f64));
        assert_eq!(defer(t0 as f64, &[t0, t0 + 180]), (true, (t0 + 305) as f64));
    }

    #[test]
    fn related_currencies() {
        assert_eq!(related("eurusd"), vec!["EUR", "USD"]);
        assert_eq!(related("sp500"), vec!["USD"]);
        assert_eq!(related("hsihkd"), vec!["CNY", "HKD"]);
        assert_eq!(related("btceur"), vec!["EUR"]);
    }

    #[test]
    fn parses_ff_dates() {
        assert_eq!(parse_iso("1970-01-01T00:00:00+00:00"), Some(0));
        assert_eq!(parse_iso("1970-01-01T08:30:00-05:00"), Some(13 * 3600 + 30 * 60));
        assert_eq!(parse_iso("1970-01-01T13:30:00Z"), Some(13 * 3600 + 30 * 60));
    }
}
