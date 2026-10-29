//! `CFD_BROKER_HOURS` and `EXNESS_FILLS_ONLY` live: what FundedNext printed.
//!
//! The FundedNext book was sealed with every vendor candle the broker did not
//! quote dropped (`cfd_families.all_bars`) and every entry refused whose fill
//! minute has no broker price. The backtest answers both from the broker's
//! minute table before the engine sees anything; live has only the vendor
//! feed, so each sleeve asks its market's `fundednext_<broker>_1m` table -- kept
//! current minute by minute by `tools/fundednext_stream_1m.py`.
//!
//! ONE INDEX PER TABLE, IN MEMORY. The warm-up replays a year of candles per
//! sleeve, so the stamps are loaded once and only the tail is re-read, when a
//! question reaches past what is already held.

use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use crate::api::parquet_store::{self, NANOS_PER_SECOND, ParquetStore};
use crate::strategies::cfd::BrokerQuotes;

/// What the table holds, from its first question on.
struct Index {
    /// Every broker minute stamp (New York wall clock seconds), ascending.
    stamps: Vec<i64>,
    /// The minutes the backtest can price an entry at: positive open, close
    /// and spread (`MarketFills::from_rows_at` drops every other row).
    priced: Vec<i64>,
    /// The first second NOT yet read from the store.
    until: i64,
}

pub(super) struct StoreQuotes {
    store: ParquetStore,
    table: String,
    state: Mutex<Index>,
}

impl StoreQuotes {
    fn read(&self, from: i64) -> Vec<(i64, bool)> {
        let batches = match self.store.scan(
            &self.table,
            &["open", "close", "spread"],
            Some(from * NANOS_PER_SECOND),
            None,
        ) {
            Ok(batches) => batches,
            Err(error) => {
                tracing::warn!(table = %self.table, %error, "broker-hours read failed");
                return Vec::new();
            }
        };
        let mut out = Vec::new();
        for batch in &batches {
            let (Ok(stamps), Ok(open), Ok(close), Ok(spread)) = (
                parquet_store::timestamp_column(batch),
                parquet_store::floats(batch, "open"),
                parquet_store::floats(batch, "close"),
                parquet_store::floats(batch, "spread"),
            ) else {
                continue;
            };
            for index in 0..stamps.len() {
                let stamp = stamps[index].div_euclid(NANOS_PER_SECOND);
                out.push((
                    stamp,
                    open[index] > 0.0
                        && close[index] > 0.0
                        && spread[index] > 0.0
                        && crate::backtest::fills::broker_trades_at(&self.table, stamp),
                ));
            }
        }
        // A duplicated stamp is priced if ANY of its rows is: `from_rows_at`
        // skips an unpriced row before it deduplicates, so a later invalid
        // copy never hides a valid one.
        out.sort_by_key(|row| row.0);
        let mut kept: Vec<(i64, bool)> = Vec::with_capacity(out.len());
        for row in out {
            match kept.last_mut() {
                Some(last) if last.0 == row.0 => last.1 |= row.1,
                _ => kept.push(row),
            }
        }
        kept
    }

    /// The index, read up to `end` (exclusive) where the store has it.
    fn index_through(&self, start: i64, end: i64) -> std::sync::MutexGuard<'_, Index> {
        let mut state = self.state.lock().expect("quote index lock");
        if state.until == i64::MIN {
            // First question: everything from a day before it.
            state.until = start - 86_400;
        }
        if end > state.until {
            let from = state.until;
            let fresh = self.read(from);
            for (stamp, priced) in fresh.into_iter().filter(|row| row.0 >= from) {
                if state.stamps.last().is_some_and(|last| *last >= stamp) {
                    continue;
                }
                state.stamps.push(stamp);
                if priced {
                    state.priced.push(stamp);
                }
            }
            if let Some(last) = state.stamps.last().copied() {
                state.until = state.until.max(last + 1);
            }
        }
        state
    }
}

impl BrokerQuotes for StoreQuotes {
    fn quoted_real(&self, start: i64, end: i64) -> bool {
        if end > UNTRUSTED_FROM.load(Ordering::Relaxed) {
            return true;
        }
        let state = self.index_through(start, end);
        let stamps = &state.stamps;
        // Before the table's first minute the vendor candle stays, exactly as
        // `all_bars` keeps the warm-up it has no broker answer for.
        if stamps.first().is_none_or(|first| end <= *first) {
            return true;
        }
        let at = stamps.partition_point(|stamp| *stamp < start);
        at < stamps.len() && stamps[at] < end
    }

    /// `MarketFills::entry`'s question. The fill lands a few seconds into the
    /// candle's first minute, so it is that minute's row: priced -> yes, there
    /// but unpriced (a zero spread) -> no, not there yet -> unknown. A minute
    /// the broker never printed stays unknown; the candle's close settles it
    /// (`FamilyEngine::resolve_awaiting_fill`).
    fn fill_minute(&self, minute: i64) -> Option<bool> {
        if minute + 60 > UNTRUSTED_FROM.load(Ordering::Relaxed) {
            return Some(false);
        }
        let state = self.index_through(minute, minute + 60);
        if state.stamps.first().is_none_or(|first| minute < *first) {
            // Before the table: the backtest has no broker price either.
            return Some(false);
        }
        if state.priced.binary_search(&minute).is_ok() {
            return Some(true);
        }
        if state.stamps.binary_search(&minute).is_ok() {
            return Some(false);
        }
        None
    }
}

/// The first store-clock second the broker tables can no longer be trusted
/// for, or `i64::MAX` while the stream is alive. A question about a candle that
/// ends before it is answered from the table as always (the warm-up, and
/// everything the stream did write); one after it gets the safe answer: the
/// candle counts, so exits and stops keep running, and no entry fills.
static UNTRUSTED_FROM: AtomicI64 = AtomicI64::new(i64::MAX);

/// Called by the runtime every tick with whether the stream is alive.
pub(super) fn set_stream_health(alive: bool, now: Option<i64>) {
    if alive {
        UNTRUSTED_FROM.store(i64::MAX, Ordering::Relaxed);
    } else if UNTRUSTED_FROM.load(Ordering::Relaxed) == i64::MAX {
        // From the stream's last promise, or a few minutes back.
        let from = last_through().unwrap_or_else(|| now.unwrap_or(i64::MAX).saturating_sub(300));
        UNTRUSTED_FROM.store(from, Ordering::Relaxed);
    }
}

static LAST_THROUGH: AtomicI64 = AtomicI64::new(i64::MIN);

fn last_through() -> Option<i64> {
    let value = LAST_THROUGH.load(Ordering::Relaxed);
    (value != i64::MIN).then_some(value + 60)
}

/// How far the broker minutes are written (`data/fundednext_stream.json`,
/// `tools/fundednext_stream_1m.py`), or `None` if the stream is not alive: no
/// file, unreadable, or not rewritten for 30 seconds.
pub(super) fn stream_through(store: &ParquetStore) -> Option<i64> {
    let path = match std::env::var("FUNDEDNEXT_STREAM_FILE") {
        Ok(value) => std::path::PathBuf::from(value),
        Err(_) => store.root().parent()?.join("fundednext_stream.json"),
    };
    let text = std::fs::read_to_string(path).ok()?;
    let body: serde_json::Value = serde_json::from_str(&text).ok()?;
    let wrote_at = body.get("wrote_at")?.as_i64()?;
    let through = body.get("through")?.as_i64()?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs() as i64;
    if now - wrote_at > 30 || wrote_at > now + 30 {
        return None;
    }
    LAST_THROUGH.store(through, Ordering::Relaxed);
    Some(through)
}

static STORE: OnceLock<ParquetStore> = OnceLock::new();

/// Called once by the runtime before any slot is built.
pub(super) fn set_store(store: &ParquetStore) {
    let _ = STORE.set(store.clone());
}

/// The shared index for `table`, or `None` before `set_store` (tests). It loads
/// lazily, from the first question asked -- the oldest warm-up candle.
pub(super) fn for_table(table: &str) -> Option<Arc<dyn BrokerQuotes>> {
    static INDEX: OnceLock<Mutex<HashMap<String, Arc<StoreQuotes>>>> = OnceLock::new();
    let store = STORE.get()?;
    let mut index = INDEX.get_or_init(|| Mutex::new(HashMap::new())).lock().expect("index lock");
    let quotes = Arc::clone(index.entry(table.to_owned()).or_insert_with(|| {
        Arc::new(StoreQuotes {
            store: store.clone(),
            table: table.to_owned(),
            state: Mutex::new(Index { stamps: Vec::new(), priced: Vec::new(), until: i64::MIN }),
        })
    }));
    Some(quotes as Arc<dyn BrokerQuotes>)
}
