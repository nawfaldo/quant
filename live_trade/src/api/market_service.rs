use crate::api::parquet_store::{self, NANOS_PER_SECOND, ParquetStore};
use crate::backtest::data::iso_day;
use crate::error::ApiError;
use arrow_array::RecordBatch;
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

pub const CANDLE_MAGIC: u32 = 0x4544_4C43;
pub const TIMEFRAMES: &[&str] = &["tick", "1m", "5m", "15m", "30m", "1h", "4h", "1d"];

/// The broker tick table the FX chart draws. It is not in the Parquet store.
const FX_TICKS: &str = "ustec_tick";

/// The `YYYY-MM-DD` an epoch-nanosecond instant falls on.
fn iso_date(moment: i64) -> String {
    crate::backtest::data::format_day(moment.div_euclid(NANOS_PER_SECOND).div_euclid(86_400))
}

fn valid_date(value: &str) -> bool {
    value.len() == 10
        && value.bytes().enumerate().all(|(index, byte)| {
            if index == 4 || index == 7 {
                byte == b'-'
            } else {
                byte.is_ascii_digit()
            }
        })
}

pub fn date(value: Option<&str>) -> &str {
    value.filter(|value| valid_date(value)).unwrap_or("")
}

pub fn validate_symbol(value: &str) -> Result<&str, ApiError> {
    let lower = value.to_lowercase();
    if !lower.is_empty()
        && lower.len() <= 64
        && lower
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-' || b == b'^' || b == b'.')
    {
        Ok(value)
    } else {
        Err(ApiError::BadRequest("unknown symbol".into()))
    }
}

pub fn validate_timeframe(value: &str) -> Result<&str, ApiError> {
    TIMEFRAMES
        .contains(&value)
        .then_some(value)
        .ok_or_else(|| ApiError::BadRequest("unknown tf".into()))
}

/// `[from, to)` in epoch nanoseconds for an inclusive ISO day range.
///
/// The upper bound opens the day AFTER `to`, which is the half-open form of the
/// SQL's `timestamp < dateadd('d',1,'{to}')`. An empty or unparseable string
/// yields no bound on that side, exactly as an omitted clause did.
fn day_range(from: &str, to: &str) -> (Option<i64>, Option<i64>) {
    (
        iso_day(from).map(|day| day * 86_400 * NANOS_PER_SECOND),
        iso_day(to).map(|day| (day + 1) * 86_400 * NANOS_PER_SECOND),
    )
}

/// Real-world now, in epoch nanoseconds.
///
/// Used only for the default chart lookback, which is why it is real UTC and not
/// the New York wall clock the rows are stamped in. That is what `now()` in the
/// SQL did, and the few hours of skew do not matter to a window measured in
/// days -- but it would matter if this were ever used to bound a backtest.
fn now_nanoseconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|since| since.as_nanos() as i64)
        .unwrap_or(0)
}

/// `vix_1d` daily closes as `(epoch seconds, close)`.
///
/// THE TABLE IS NOT IN THE STORE. It was a QuestDB table and was not exported,
/// so this returns nothing and the chart draws no VIX overlay. `scan` on a
/// missing table yields no batches, so no special case is needed here -- and the
/// day a `vix_1d.parquet` lands, this starts working again untouched.
pub async fn vix(store: &ParquetStore, from: &str, to: &str) -> Result<Vec<(i64, f64)>, ApiError> {
    let (lower, upper) = day_range(from, to);
    let mut out = Vec::new();
    for batch in store.scan("vix_1d", &["close"], lower, upper)? {
        let stamps = parquet_store::timestamp_column(&batch)?;
        let closes = parquet_store::floats(&batch, "close")?;
        for (stamp, close) in stamps.iter().zip(&closes) {
            out.push((stamp.div_euclid(NANOS_PER_SECOND), *close));
        }
    }
    Ok(out)
}

#[derive(Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct DatabaseSummaryItem {
    pub symbol: String,
    pub dataset_name: String,
    pub country: String,
    pub r#type: String,
    pub timeframe: String,
    pub available_timeframes: Vec<String>,
    pub bytes: u64,
    pub first_date: String,
    pub last_date: String,
}

fn indonesian_company_name(symbol: &str) -> String {
    match symbol.to_uppercase().as_str() {
        "AADI" => "Adaro Andalan Indonesia Tbk.".into(),
        "ADMR" => "Adaro Minerals Indonesia Tbk.".into(),
        "ADRO" => "Adaro Energy Indonesia Tbk.".into(),
        "AKRA" => "AKR Corporindo Tbk.".into(),
        "ANTM" => "Aneka Tambang Tbk.".into(),
        "ASII" => "Astra International Tbk.".into(),
        "BBCA" => "Bank Central Asia Tbk.".into(),
        "BBNI" => "Bank Negara Indonesia Tbk.".into(),
        "BBRI" => "Bank Rakyat Indonesia Tbk.".into(),
        "BMRI" => "Bank Mandiri Tbk.".into(),
        "BUMI" => "Bumi Resources Tbk.".into(),
        "CPIN" => "Charoen Pokphand Indonesia Tbk.".into(),
        "DEWA" => "Darma Henwa Tbk.".into(),
        "ESSA" => "ESSA Industries Indonesia Tbk.".into(),
        "EXCL" => "XL Axiata Tbk.".into(),
        "HRTA" => "Hartadinata Abadi Tbk.".into(),
        "ICBP" => "Indofood CBP Sukses Makmur Tbk.".into(),
        "INDF" => "Indofood Sukses Makmur Tbk.".into(),
        "INKP" => "Indah Kiat Pulp & Paper Tbk.".into(),
        "ISAT" => "Indosat Ooredoo Hutchison Tbk.".into(),
        "ITMG" => "Indo Tambangraya Megah Tbk.".into(),
        "JPFA" => "Japfa Comfeed Indonesia Tbk.".into(),
        "KLBF" => "Kalbe Farma Tbk.".into(),
        "MAPI" => "Mitra Adiperkasa Tbk.".into(),
        "MBMA" => "Merdeka Battery Materials Tbk.".into(),
        "MDKA" => "Merdeka Copper Gold Tbk.".into(),
        "MEDC" => "Medco Energi Internasional Tbk.".into(),
        "PGAS" => "Perusahaan Gas Negara Tbk.".into(),
        "PTBA" => "Bukit Asam Tbk.".into(),
        "SMGR" => "Semen Indonesia (Persero) Tbk.".into(),
        "TLKM" => "Telkom Indonesia (Persero) Tbk.".into(),
        "UNTR" => "United Tractors Tbk.".into(),
        "UNVR" => "Unilever Indonesia Tbk.".into(),
        "WIFI" => "Solusi Sinergi Digital Tbk.".into(),
        other => format!("{other} Tbk."),
    }
}

/// Split `<symbol>_<timeframe>` into its parts, if the suffix is a timeframe.
fn split_timeframe(table: &str) -> Option<(String, String)> {
    let position = table.rfind('_')?;
    let timeframe = &table[position + 1..];
    TIMEFRAMES
        .contains(&timeframe)
        .then(|| (table[..position].to_lowercase(), timeframe.to_string()))
}

pub async fn database_summary(store: &ParquetStore) -> Result<Value, ApiError> {
    // 1. Every table in the store
    struct Group {
        tables: Vec<(String, String)>, // (timeframe, full_table_name)
    }

    let mut groups: BTreeMap<String, Group> = BTreeMap::new();
    for table_name in store.tables() {
        if table_name.starts_with("bm_")
            || table_name.starts_with("fx_")
            || table_name.contains("tmp")
        {
            continue;
        }
        if let Some((prefix, tf)) = split_timeframe(&table_name) {
            groups
                .entry(prefix)
                .or_insert_with(|| Group { tables: Vec::new() })
                .tables
                .push((tf, table_name));
        }
    }

    if groups.is_empty() {
        return Ok(json!([]));
    }

    // 2. Size and date span per symbol.
    //
    // `table_partitions()` gave `diskSize` and the partition timestamp bounds in
    // one query. Here the size is the file's own bytes on disk and the span
    // comes from the Parquet footer, so neither costs a scan.
    #[derive(Default)]
    struct AggStats {
        bytes: u64,
        first_date: String,
        last_date: String,
    }

    let mut stats: BTreeMap<String, AggStats> = BTreeMap::new();
    for (sym_key, group) in &groups {
        for (_, full_table) in &group.tables {
            let entry = stats.entry(sym_key.clone()).or_default();
            for path in store.table_files(full_table) {
                entry.bytes += path.metadata().map(|meta| meta.len()).unwrap_or(0);
            }
            // A table whose footer cannot be read contributes its bytes and no
            // dates rather than failing the whole screen.
            let Ok(Some((first, last))) = store.bounds(full_table) else {
                continue;
            };
            let first = iso_date(first);
            let last = iso_date(last);
            if entry.first_date.is_empty() || first < entry.first_date {
                entry.first_date = first;
            }
            if last > entry.last_date {
                entry.last_date = last;
            }
        }
    }

    let mut items = Vec::new();
    for (sym_key, group) in groups {
        let agg = stats.get(&sym_key);
        let bytes = agg.map(|a| a.bytes).unwrap_or(0);
        let first_date = agg.map(|a| a.first_date.clone()).unwrap_or_default();
        let last_date = agg.map(|a| a.last_date.clone()).unwrap_or_default();

        let mut available_timeframes: Vec<String> =
            group.tables.into_iter().map(|(tf, _)| tf).collect();
        available_timeframes
            .sort_by_key(|tf| TIMEFRAMES.iter().position(|t| t == tf).unwrap_or(99));

        let timeframe_str = if available_timeframes.len() == TIMEFRAMES.len() {
            "tick...1d".to_string()
        } else if available_timeframes.len() == 1 {
            available_timeframes[0].clone()
        } else if !available_timeframes.is_empty() {
            format!(
                "{}...{}",
                available_timeframes.first().unwrap(),
                available_timeframes.last().unwrap()
            )
        } else {
            "—".to_string()
        };

        let (symbol, dataset_name, country, r#type) = match sym_key.as_str() {
            "es" => (
                "ES".to_string(),
                "S&P 500 Futures".to_string(),
                "United States".to_string(),
                "Futures".to_string(),
            ),
            "nq" => (
                "NQ".to_string(),
                "Nasdaq-100 Futures".to_string(),
                "United States".to_string(),
                "Futures".to_string(),
            ),
            "vix" => (
                "VIX".to_string(),
                "CBOE Volatility Index".to_string(),
                "United States".to_string(),
                "Index".to_string(),
            ),
            "ustec" => (
                "USTEC".to_string(),
                "Nasdaq-100 Index CFD".to_string(),
                "United States".to_string(),
                "Index".to_string(),
            ),
            "jkse" => (
                "JKSE".to_string(),
                "Jakarta Composite Index".to_string(),
                "Indonesia".to_string(),
                "Index".to_string(),
            ),
            _ => (
                sym_key.to_uppercase(),
                indonesian_company_name(&sym_key),
                "Indonesia".to_string(),
                "Stock".to_string(),
            ),
        };

        items.push(DatabaseSummaryItem {
            symbol,
            dataset_name,
            country,
            r#type,
            timeframe: timeframe_str,
            available_timeframes,
            bytes,
            first_date,
            last_date,
        });
    }

    items.sort_by(|a, b| {
        let priority = |sym: &str| match sym {
            "ES" => 0,
            "NQ" => 1,
            "VIX" => 2,
            "USTEC" => 3,
            "JKSE" => 4,
            _ => 5,
        };
        let p_a = priority(&a.symbol);
        let p_b = priority(&b.symbol);
        if p_a != p_b {
            p_a.cmp(&p_b)
        } else {
            a.symbol.cmp(&b.symbol)
        }
    });

    Ok(json!(items))
}

pub async fn database_symbols(store: &ParquetStore) -> Result<Value, ApiError> {
    struct Group {
        tables: Vec<(String, String)>,
    }

    let mut groups: BTreeMap<String, Group> = BTreeMap::new();
    for table_name in store.tables() {
        if table_name.starts_with("fx_") || table_name.contains("tmp") {
            continue;
        }
        // Bookmap tick tables (bm_<sym>_ticks) back the march candles,
        // aggregated on the fly across all timeframes.
        if let Some(sym) = table_name
            .strip_prefix("bm_")
            .and_then(|rest| rest.strip_suffix("_ticks"))
        {
            let entry = groups
                .entry(sym.to_lowercase())
                .or_insert_with(|| Group { tables: Vec::new() });
            for tf in TIMEFRAMES {
                if !entry.tables.iter().any(|(existing, _)| existing == tf) {
                    entry.tables.push((tf.to_string(), table_name.clone()));
                }
            }
            continue;
        }
        if table_name.starts_with("bm_") {
            continue;
        }
        if let Some((prefix, tf)) = split_timeframe(&table_name) {
            groups
                .entry(prefix)
                .or_insert_with(|| Group { tables: Vec::new() })
                .tables
                .push((tf, table_name));
        }
    }

    let mut items = Vec::new();
    for (sym_key, group) in groups {
        let mut available_timeframes: Vec<String> =
            group.tables.into_iter().map(|(tf, _)| tf).collect();
        available_timeframes
            .sort_by_key(|tf| TIMEFRAMES.iter().position(|t| t == tf).unwrap_or(99));

        let (symbol, dataset_name, country, r#type) = match sym_key.as_str() {
            "es" => (
                "ES".to_string(),
                "S&P 500 Futures".to_string(),
                "United States".to_string(),
                "Futures".to_string(),
            ),
            "nq" => (
                "NQ".to_string(),
                "Nasdaq-100 Futures".to_string(),
                "United States".to_string(),
                "Futures".to_string(),
            ),
            "vix" => (
                "VIX".to_string(),
                "CBOE Volatility Index".to_string(),
                "United States".to_string(),
                "Index".to_string(),
            ),
            "ustec" => (
                "USTEC".to_string(),
                "Nasdaq-100 Index CFD".to_string(),
                "United States".to_string(),
                "Index".to_string(),
            ),
            "jkse" => (
                "JKSE".to_string(),
                "Jakarta Composite Index".to_string(),
                "Indonesia".to_string(),
                "Index".to_string(),
            ),
            _ => (
                sym_key.to_uppercase(),
                indonesian_company_name(&sym_key),
                "Indonesia".to_string(),
                "Stock".to_string(),
            ),
        };

        items.push(json!({
            "symbol": symbol,
            "datasetName": dataset_name,
            "country": country,
            "type": r#type,
            "availableTimeframes": available_timeframes,
        }));
    }

    items.sort_by(|a, b| {
        let priority = |sym: &str| match sym {
            "ES" => 0,
            "NQ" => 1,
            "VIX" => 2,
            "USTEC" => 3,
            "JKSE" => 4,
            _ => 5,
        };
        let sym_a = a["symbol"].as_str().unwrap_or("");
        let sym_b = b["symbol"].as_str().unwrap_or("");
        let p_a = priority(sym_a);
        let p_b = priority(sym_b);
        if p_a != p_b {
            p_a.cmp(&p_b)
        } else {
            sym_a.cmp(sym_b)
        }
    });

    Ok(json!(items))
}

pub async fn march_candles(
    store: &ParquetStore,
    symbol: &str,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<u8>, ApiError> {
    let mut candles = merged_march_candles(store, symbol, tf, from, to).await?;
    if from.is_empty() && to.is_empty() && candles.len() > 1500 {
        candles.drain(..candles.len() - 1500);
    }
    candle_volume_binary(candles)
}

/// Builds the March chart's canonical candle stream.  Bookmap trades take
/// priority for every one-minute interval they cover; regular OHLCV fills the
/// remaining minutes before aggregation to the requested timeframe.
async fn merged_march_candles(
    store: &ParquetStore,
    symbol: &str,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<Candle>, ApiError> {
    validate_symbol(symbol)?;
    validate_timeframe(tf)?;
    let symbol_lower = symbol.to_lowercase();
    let (lower, upper) = march_range(from, to, march_lookback_days(tf));

    // 1. Try direct timeframe table first (e.g. jkse_1d, nq_1m, etc.)
    if !matches!(symbol_lower.as_str(), "nq" | "es") {
        let direct = store.scan(
            &format!("{symbol_lower}_{tf}"),
            &["open", "high", "low", "close", "volume"],
            lower,
            upper,
        )?;
        let parsed = ohlcv_candles(&direct)?;
        if !parsed.is_empty() {
            return Ok(parsed.into_values().collect());
        }
    }

    // 2. Merge 1m OHLCV, historical Databento, and live Bookmap. A more
    // granular source replaces the same minute from the source before it. A
    // table that is not in the store contributes nothing, which is what the
    // "table does not exist" arm of `optional_table_query` did.
    let mut minutes = ohlcv_candles(&store.scan(
        &format!("{symbol_lower}_1m"),
        &["open", "high", "low", "close", "volume"],
        lower,
        upper,
    )?)?;
    for prefix in ["dbento", "bm"] {
        let ticks = store.scan(
            &format!("{prefix}_{symbol_lower}_ticks"),
            &["price", "size"],
            lower,
            upper,
        )?;
        for (timestamp, candle) in tick_candles(&ticks)? {
            minutes.insert(timestamp, candle);
        }
    }

    aggregate_minutes(minutes, tf)
}

/// One candle per source row, keyed on the row's timestamp in nanoseconds.
fn ohlcv_candles(batches: &[RecordBatch]) -> Result<BTreeMap<i64, Candle>, ApiError> {
    let mut out = BTreeMap::new();
    for batch in batches {
        let stamps = parquet_store::timestamp_column(batch)?;
        let open = parquet_store::floats(batch, "open")?;
        let high = parquet_store::floats(batch, "high")?;
        let low = parquet_store::floats(batch, "low")?;
        let close = parquet_store::floats(batch, "close")?;
        let volume = parquet_store::floats(batch, "volume")?;
        for index in 0..stamps.len() {
            out.insert(
                stamps[index],
                Candle {
                    timestamp: stamps[index],
                    open: open[index],
                    high: high[index],
                    low: low[index],
                    close: close[index],
                    volume: volume[index],
                },
            );
        }
    }
    Ok(out)
}

/// Trades rolled up into one-minute candles, keyed on the minute in nanoseconds.
///
/// Zero-size rows are dropped: Bookmap emits zero-size execution-boundary
/// markers, which are useful in the raw tick log but must not turn an otherwise
/// empty minute into a candle or influence OHLC.
fn tick_candles(batches: &[RecordBatch]) -> Result<BTreeMap<i64, Candle>, ApiError> {
    const MINUTE: i64 = 60 * NANOS_PER_SECOND;
    let mut out: BTreeMap<i64, Candle> = BTreeMap::new();
    for batch in batches {
        let stamps = parquet_store::timestamp_column(batch)?;
        let prices = parquet_store::floats(batch, "price")?;
        let sizes = parquet_store::floats(batch, "size")?;
        for index in 0..stamps.len() {
            if sizes[index] <= 0.0 {
                continue;
            }
            let minute = stamps[index].div_euclid(MINUTE) * MINUTE;
            let price = prices[index];
            match out.get_mut(&minute) {
                Some(candle) => {
                    candle.high = candle.high.max(price);
                    candle.low = candle.low.min(price);
                    candle.close = price;
                    candle.volume += sizes[index];
                }
                None => {
                    out.insert(
                        minute,
                        Candle {
                            timestamp: minute,
                            open: price,
                            high: price,
                            low: price,
                            close: price,
                            volume: sizes[index],
                        },
                    );
                }
            }
        }
    }
    Ok(out)
}

#[derive(Clone, Copy)]
struct Candle {
    timestamp: i64,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

/// The time range every March source is read over, in epoch nanoseconds.
///
/// ONE RANGE FOR ALL FOUR SOURCES. Databento, Bookmap and the OHLCV tables all
/// store New York wall-clock values encoded as fake UTC, so they share a range
/// -- the SQL needed a separate `databento_march_filter` only because the two
/// timestamp columns had different QuestDB types.
///
/// With no selected range the window is the last `lookback_days`, which keeps a
/// default chart from aggregating a decade of ticks to draw 1,500 candles.
fn march_range(from: &str, to: &str, lookback_days: u32) -> (Option<i64>, Option<i64>) {
    let (lower, upper) = day_range(from, to);
    if lower.is_none() && upper.is_none() {
        let span = i64::from(lookback_days) * 86_400 * NANOS_PER_SECOND;
        return (Some(now_nanoseconds() - span), None);
    }
    (lower, upper)
}

/// Enough one-minute history for the default latest-chart limit at each frame,
/// rounded up to full calendar days.
fn march_lookback_days(tf: &str) -> u32 {
    match tf {
        "1m" => 2,
        "5m" => 6,
        "15m" => 16,
        "30m" => 32,
        "1h" => 63,
        "4h" => 251,
        "1d" => 1501,
        _ => 2,
    }
}

/// One timeframe bucket in nanoseconds.
fn timeframe_nanoseconds(tf: &str) -> Result<i64, ApiError> {
    let seconds = match tf {
        "1m" => 60_i64,
        "5m" => 5 * 60,
        "15m" => 15 * 60,
        "30m" => 30 * 60,
        "1h" => 60 * 60,
        "4h" => 4 * 60 * 60,
        "1d" => 24 * 60 * 60,
        _ => return Err(ApiError::BadRequest("unknown tf".into())),
    };
    Ok(seconds * NANOS_PER_SECOND)
}

fn aggregate_minutes(minutes: BTreeMap<i64, Candle>, tf: &str) -> Result<Vec<Candle>, ApiError> {
    let bucket_size = timeframe_nanoseconds(tf)?;
    let mut output: BTreeMap<i64, Candle> = BTreeMap::new();
    for candle in minutes.into_values() {
        let bucket = candle.timestamp.div_euclid(bucket_size) * bucket_size;
        output
            .entry(bucket)
            .and_modify(|aggregate| {
                aggregate.high = aggregate.high.max(candle.high);
                aggregate.low = aggregate.low.min(candle.low);
                aggregate.close = candle.close;
                aggregate.volume += candle.volume;
            })
            .or_insert(Candle {
                timestamp: bucket,
                ..candle
            });
    }
    Ok(output.into_values().collect())
}

fn candle_volume_binary(candles: Vec<Candle>) -> Result<Vec<u8>, ApiError> {
    let mut output = Vec::with_capacity(8 + candles.len() * 24);
    output.extend_from_slice(&CANDLE_MAGIC.to_le_bytes());
    output.extend_from_slice(&(candles.len() as u32).to_le_bytes());
    for candle in candles {
        output.extend_from_slice(&((candle.timestamp / 1_000_000_000) as u32).to_le_bytes());
        for value in [
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        ] {
            output.extend_from_slice(&(value as f32).to_le_bytes());
        }
    }
    Ok(output)
}

pub async fn fx_candles(
    store: &ParquetStore,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<u8>, ApiError> {
    validate_timeframe(tf)?;
    // `ustec_tick` was a QuestDB table and was not exported, so this draws an
    // empty chart rather than failing. `scan` on a missing table yields no
    // batches, so the code below needs no special case and starts working again
    // if the table ever lands in the store.
    let (lower, upper) = day_range(from, to);
    let mut buckets: BTreeMap<i64, Candle> = BTreeMap::new();
    let width = timeframe_nanoseconds(tf)?;
    for batch in store.scan(FX_TICKS, &["bid", "ask"], lower, upper)? {
        let stamps = parquet_store::timestamp_column(&batch)?;
        let bids = parquet_store::floats(&batch, "bid")?;
        let asks = parquet_store::floats(&batch, "ask")?;
        for index in 0..stamps.len() {
            if bids[index] <= 0.0 || asks[index] <= 0.0 {
                continue;
            }
            let mid = (bids[index] + asks[index]) * 0.5;
            let bucket = stamps[index].div_euclid(width) * width;
            match buckets.get_mut(&bucket) {
                Some(candle) => {
                    candle.high = candle.high.max(mid);
                    candle.low = candle.low.min(mid);
                    candle.close = mid;
                    // `count()`: the volume of an FX candle is its tick count.
                    candle.volume += 1.0;
                }
                None => {
                    buckets.insert(
                        bucket,
                        Candle {
                            timestamp: bucket,
                            open: mid,
                            high: mid,
                            low: mid,
                            close: mid,
                            volume: 1.0,
                        },
                    );
                }
            }
        }
    }
    let mut candles: Vec<Candle> = buckets.into_values().collect();
    // An open-ended request draws the most recent 1,500 candles.
    if from.is_empty() && candles.len() > 1500 {
        candles.drain(..candles.len() - 1500);
    }
    candle_volume_binary(candles)
}

#[derive(Serialize)]
pub struct Tick {
    ts: i64,
    price: f64,
    size: f64,
    side: String,
    best_bid: f64,
    best_ask: f64,
}
pub async fn ticks(
    store: &ParquetStore,
    symbol: &str,
    since: Option<i64>,
    before: Option<i64>,
    limit: Option<i64>,
) -> Result<Vec<Tick>, ApiError> {
    validate_symbol(symbol)?;
    let limit = limit.unwrap_or(10_000).clamp(1, 50_000);
    let mut ticks = read_ticks(
        store,
        &format!("dbento_{symbol}_ticks"),
        since,
        before,
        limit,
    )?;
    ticks.extend(read_ticks(
        store,
        &format!("bm_{symbol}_ticks"),
        since,
        before,
        limit,
    )?);
    ticks.sort_by_key(|tick| tick.ts);
    if ticks.len() > limit as usize {
        ticks.drain(..ticks.len() - limit as usize);
    }
    Ok(ticks)
}

/// The newest `limit` ticks of one table inside `(since, before)`, oldest first.
///
/// `since` and `before` are EXCLUSIVE nanosecond bounds, as the SQL's `>` and
/// `<` were, so `scan`'s half-open range is nudged by one nanosecond on the
/// lower side and left alone on the upper.
///
/// The tail is taken after the scan rather than by `ORDER BY ts DESC LIMIT n`.
/// That reads more rows than the database did, which is why every caller passes
/// a bounded window; the request handler's own limit clamps at 50,000 ticks.
///
/// `best_bid`/`best_ask` are present on the Bookmap table and absent from
/// Databento's, and the price stands in for both where they are missing -- the
/// SQL discovered that by trying the wider select and retrying on failure,
/// whereas here the schema says so directly.
fn read_ticks(
    store: &ParquetStore,
    table: &str,
    since: Option<i64>,
    before: Option<i64>,
    limit: i64,
) -> Result<Vec<Tick>, ApiError> {
    if !store.has_table(table) {
        return Ok(Vec::new());
    }
    let has_bbo = store.has_column(table, "best_bid")?;

    let mut columns = vec!["price", "size", "side"];
    if has_bbo {
        columns.extend(["best_bid", "best_ask"]);
    }
    let mut out = Vec::new();
    for batch in store.scan(table, &columns, since.map(|value| value + 1), before)? {
        let stamps = parquet_store::timestamp_column(&batch)?;
        let prices = parquet_store::floats(&batch, "price")?;
        let sizes = parquet_store::floats(&batch, "size")?;
        let sides = parquet_store::strings(&batch, "side")?;
        let bids = has_bbo
            .then(|| parquet_store::floats(&batch, "best_bid"))
            .transpose()?;
        let asks = has_bbo
            .then(|| parquet_store::floats(&batch, "best_ask"))
            .transpose()?;
        for index in 0..stamps.len() {
            let price = prices[index];
            out.push(Tick {
                ts: stamps[index],
                price,
                size: sizes[index],
                side: sides[index].clone(),
                best_bid: bids.as_ref().map_or(price, |values| values[index]),
                best_ask: asks.as_ref().map_or(price, |values| values[index]),
            });
        }
        // Bounded as it goes: a caller that asks for 10,000 ticks with no
        // `since` must not first materialise ninety-six million of them.
        if out.len() > limit as usize * 2 {
            let excess = out.len() - limit as usize;
            out.drain(..excess);
        }
    }
    if out.len() > limit as usize {
        out.drain(..out.len() - limit as usize);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_inputs() {
        assert!(validate_symbol("nq").is_ok());
        assert!(validate_symbol("nq;drop").is_err());
        assert_eq!(date(Some("2026-07-15")), "2026-07-15");
        assert_eq!(date(Some("not-a-date")), "");
    }

    #[test]
    fn aggregates_bookmap_preferred_minutes_into_higher_timeframe() {
        let minute = 60_000_000_000_i64;
        let mut merged = BTreeMap::new();

        // This is the Bookmap bar after it has replaced the regular 1m OHLCV
        // bar at timestamp zero.
        merged.insert(
            0,
            Candle {
                timestamp: 0,
                open: 11.0,
                high: 14.0,
                low: 9.0,
                close: 12.0,
                volume: 7.0,
            },
        );
        // The next minute has no Bookmap ticks, so normal OHLCV fills it.
        merged.insert(
            minute,
            Candle {
                timestamp: minute,
                open: 13.0,
                high: 16.0,
                low: 12.0,
                close: 15.0,
                volume: 5.0,
            },
        );

        let candles = aggregate_minutes(merged, "5m").unwrap();
        assert_eq!(candles.len(), 1);
        let candle = candles[0];
        assert_eq!(candle.open, 11.0);
        assert_eq!(candle.high, 16.0);
        assert_eq!(candle.low, 9.0);
        assert_eq!(candle.close, 15.0);
        assert_eq!(candle.volume, 12.0);
    }
}
