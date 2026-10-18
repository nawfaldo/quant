//! `CFD_BROKER_HOURS` live: whether FundedNext printed a minute in a candle.
//!
//! The FundedNext book was sealed with every vendor candle the broker did not
//! quote dropped (`cfd_families.all_bars`). The backtest filters its bars before
//! the engine sees them; live has only the vendor feed, so each sleeve asks its
//! market's `fundednext_<broker>_1m` table -- kept current minute by minute by
//! `tools/fundednext_stream_1m.py` -- as each candle closes.
//!
//! ONE INDEX PER TABLE, IN MEMORY. The warm-up replays a year of candles per
//! sleeve, so the stamps are loaded once and only the tail is re-read, when a
//! question reaches past what is already held.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

use crate::api::parquet_store::{self, NANOS_PER_SECOND, ParquetStore};
use crate::strategies::cfd::BrokerQuotes;

pub(super) struct StoreQuotes {
    store: ParquetStore,
    table: String,
    /// Broker minute stamps (New York wall clock seconds), ascending, and the
    /// first second NOT yet read from the store.
    state: Mutex<(Vec<i64>, i64)>,
}

impl StoreQuotes {
    fn read(&self, from: i64) -> Vec<i64> {
        let batches = match self.store.scan(
            &self.table,
            &["open"],
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
            if let Ok(stamps) = parquet_store::timestamp_column(batch) {
                out.extend(stamps.iter().map(|ns| ns.div_euclid(NANOS_PER_SECOND)));
            }
        }
        out.sort_unstable();
        out.dedup();
        out
    }
}

impl BrokerQuotes for StoreQuotes {
    fn quoted_real(&self, start: i64, end: i64) -> bool {
        let mut state = self.state.lock().expect("quote index lock");
        if state.1 == i64::MIN {
            // First question: everything from a day before it.
            state.1 = start - 86_400;
        }
        if end > state.1 {
            let from = state.1;
            let fresh = self.read(from);
            let (stamps, until) = &mut *state;
            stamps.extend(fresh.into_iter().filter(|stamp| *stamp >= from));
            if let Some(last) = stamps.last() {
                *until = (*until).max(*last + 1);
            }
        }
        let stamps = &state.0;
        // Before the table's first minute the vendor candle stays, exactly as
        // `all_bars` keeps the warm-up it has no broker answer for.
        if stamps.first().is_none_or(|first| end <= *first) {
            return true;
        }
        let at = stamps.partition_point(|stamp| *stamp < start);
        at < stamps.len() && stamps[at] < end
    }
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
            state: Mutex::new((Vec::new(), i64::MIN)),
        })
    }));
    Some(quotes as Arc<dyn BrokerQuotes>)
}
