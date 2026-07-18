use crate::{error::ApiError, questdb::QuestDb};
use serde::Serialize;
use serde_json::{Value, json};
use std::{collections::BTreeMap, time::Duration};

pub const CANDLE_MAGIC: u32 = 0x4544_4C43;
pub const VWAP_MAGIC: u32 = 0x5041_5756;
pub const HEATMAP_MAGIC: u32 = 0x5441_4548;
pub const TIMEFRAMES: &[&str] = &["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
pub const SYMBOLS: &[&str] = &["nq", "es"];

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
    SYMBOLS
        .contains(&value)
        .then_some(value)
        .ok_or_else(|| ApiError::BadRequest("unknown symbol".into()))
}

pub fn validate_timeframe(value: &str) -> Result<&str, ApiError> {
    TIMEFRAMES
        .contains(&value)
        .then_some(value)
        .ok_or_else(|| ApiError::BadRequest("unknown tf".into()))
}

async fn query(questdb: &QuestDb, sql: &str) -> Result<Vec<csv::StringRecord>, ApiError> {
    let mut delay = Duration::from_millis(300);
    let mut latest = None;
    for attempt in 1..=6 {
        match questdb.csv(sql).await {
            Ok(rows) => return Ok(rows),
            Err(error) => {
                latest = Some(error);
                if attempt < 6 {
                    tracing::warn!(attempt, sql, "QuestDB query failed; retrying");
                    actix_web::rt::time::sleep(delay).await;
                    delay = (delay * 2).min(Duration::from_secs(5));
                }
            }
        }
    }
    Err(latest.expect("at least one query attempt"))
}

fn field(row: &csv::StringRecord, index: usize) -> Result<&str, ApiError> {
    row.get(index)
        .ok_or_else(|| ApiError::QuestDb(format!("missing CSV column {index}")))
}

fn parse<T: std::str::FromStr>(row: &csv::StringRecord, index: usize) -> Result<T, ApiError>
where
    T::Err: std::fmt::Display,
{
    field(row, index)?
        .parse()
        .map_err(|e| ApiError::QuestDb(format!("bad CSV field {index}: {e}")))
}

pub async fn candles(
    questdb: &QuestDb,
    symbol: &str,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<u8>, ApiError> {
    validate_symbol(symbol)?;
    validate_timeframe(tf)?;
    if !valid_date(from) || !valid_date(to) {
        return Err(ApiError::BadRequest("from and to must be ISO dates".into()));
    }
    let sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,open,high,low,close ",
            "FROM {symbol}_{tf} ",
            "WHERE timestamp >= '{from}' ",
            "AND timestamp < dateadd('d',1,'{to}') ",
            "ORDER BY timestamp ASC",
        ),
        symbol = symbol,
        tf = tf,
        from = from,
        to = to
    );
    let rows = query(questdb, &sql).await?;
    let mut output = Vec::with_capacity(8 + rows.len() * 20);
    output.extend_from_slice(&CANDLE_MAGIC.to_le_bytes());
    output.extend_from_slice(&(rows.len() as u32).to_le_bytes());
    for row in rows {
        let micros: i64 = parse(&row, 0)?;
        output.extend_from_slice(&((micros / 1_000_000) as u32).to_le_bytes());
        for i in 1..=4 {
            output.extend_from_slice(&parse::<f32>(&row, i)?.to_le_bytes());
        }
    }
    Ok(output)
}

pub async fn vwap(questdb: &QuestDb) -> Result<Vec<u8>, ApiError> {
    let rows = query(
        questdb,
        "SELECT cast(timestamp as long) ts,high,low,close,volume FROM nq_1m ORDER BY timestamp ASC",
    )
    .await?;
    let mut output = Vec::with_capacity(8 + rows.len() * 8);
    output.extend_from_slice(&VWAP_MAGIC.to_le_bytes());
    output.extend_from_slice(&(rows.len() as u32).to_le_bytes());
    let (mut day, mut anchored, mut pv, mut volume) = (-1i64, false, 0.0f64, 0.0f64);
    for row in rows {
        let micros: i64 = parse(&row, 0)?;
        let secs = micros / 1_000_000;
        let row_day = secs.div_euclid(86_400);
        let minute = secs.rem_euclid(86_400) / 60;
        if row_day != day {
            day = row_day;
            anchored = false;
            pv = 0.0;
            volume = 0.0;
        }
        if !anchored && minute >= 570 {
            anchored = true;
            pv = 0.0;
            volume = 0.0;
        }
        let high: f64 = parse(&row, 1)?;
        let low: f64 = parse(&row, 2)?;
        let close: f64 = parse(&row, 3)?;
        let row_volume: f64 = parse(&row, 4)?;
        pv += ((high + low + close) / 3.0) * row_volume;
        volume += row_volume;
        let value = if volume > 0.0 {
            (pv / volume) as f32
        } else {
            0.0
        };
        output.extend_from_slice(&(secs as u32).to_le_bytes());
        output.extend_from_slice(&value.to_le_bytes());
    }
    Ok(output)
}

pub async fn vix(questdb: &QuestDb, from: &str, to: &str) -> Result<Vec<(i64, f64)>, ApiError> {
    let sql = if !from.is_empty() && !to.is_empty() {
        format!(
            concat!(
                "SELECT cast(timestamp as long) ts,close FROM vix_1d ",
                "WHERE timestamp >= '{from}' ",
                "AND timestamp < dateadd('d',1,'{to}') ",
                "ORDER BY timestamp ASC",
            ),
            from = from,
            to = to
        )
    } else {
        "SELECT cast(timestamp as long) ts,close FROM vix_1d ORDER BY timestamp ASC".into()
    };
    query(questdb, &sql)
        .await?
        .into_iter()
        .map(|row| Ok((parse::<i64>(&row, 0)? / 1_000_000, parse(&row, 1)?)))
        .collect()
}

#[derive(Default)]
struct Summary {
    bytes: u64,
    first: String,
    last: String,
}
pub async fn database_summary(questdb: &QuestDb) -> Result<Value, ApiError> {
    let mut segments = Vec::new();
    for prefix in ["es", "nq", "qqq_options"] {
        for tf in TIMEFRAMES {
            segments.push(format!(
                concat!(
                    "SELECT '{prefix}' name,sum(diskSize) bytes,",
                    "min(minTimestamp) first_date,max(maxTimestamp) last_date ",
                    "FROM table_partitions('{prefix}_{tf}')",
                ),
                prefix = prefix,
                tf = tf
            ));
        }
    }
    segments.push(
        concat!(
            "SELECT 'vix' name,sum(diskSize) bytes,",
            "min(minTimestamp) first_date,max(maxTimestamp) last_date ",
            "FROM table_partitions('vix_1d')",
        )
        .into(),
    );
    let mut values: BTreeMap<String, Summary> = BTreeMap::new();
    for row in query(questdb, &segments.join(" UNION ALL ")).await? {
        let name = field(&row, 0)?.to_owned();
        let bytes = field(&row, 1)?.parse().unwrap_or(0);
        let first = field(&row, 2)?.get(0..10).unwrap_or("");
        let last = field(&row, 3)?.get(0..10).unwrap_or("");
        let value = values.entry(name).or_default();
        value.bytes += bytes;
        if value.first.is_empty() || (!first.is_empty() && first < value.first.as_str()) {
            value.first = first.into();
        }
        if last > value.last.as_str() {
            value.last = last.into();
        }
    }
    let item = |key: &str, name: &str| {
        let summary = values.get(key);
        json!({
            "name": name,
            "bytes": summary.map(|value| value.bytes).unwrap_or(0),
            "firstDate": summary.map(|value| value.first.as_str()).unwrap_or(""),
            "lastDate": summary.map(|value| value.last.as_str()).unwrap_or(""),
        })
    };
    Ok(json!([
        item("es", "ES"),
        item("nq", "NQ"),
        item("qqq_options", "QQQ Options"),
        item("vix", "VIX")
    ]))
}

pub async fn march_candles(
    questdb: &QuestDb,
    symbol: &str,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<u8>, ApiError> {
    let mut candles = merged_march_candles(questdb, symbol, tf, from, to).await?;
    if from.is_empty() && to.is_empty() && candles.len() > 1500 {
        candles.drain(..candles.len() - 1500);
    }
    candle_volume_binary(candles)
}

/// Builds the March chart's canonical candle stream.  Bookmap trades take
/// priority for every one-minute interval they cover; regular OHLCV fills the
/// remaining minutes before aggregation to the requested timeframe.
async fn merged_march_candles(
    questdb: &QuestDb,
    symbol: &str,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<Candle>, ApiError> {
    validate_symbol(symbol)?;
    validate_timeframe(tf)?;
    let lookback_days = march_lookback_days(tf);
    let base_filter = march_filter(from, to, lookback_days, false);
    let tick_filter = march_filter(from, to, lookback_days, true);

    // The canonical historical series is one minute.  Bookmap tick bars
    // replace regular OHLCV for any minute where Bookmap has a real trade, then
    // the resulting minute series is aggregated to the selected chart frame.
    // This preserves Bookmap's higher-fidelity data without leaving a partial
    // Bookmap hour/day as a partial candle.
    let ohlcv_sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,open,high,low,close,volume ",
            "FROM {symbol}_1m {base_filter} ORDER BY timestamp ASC",
        ),
        symbol = symbol,
        base_filter = base_filter,
    );
    let bookmap_sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,first(price) open,max(price) high,",
            "min(price) low,last(price) close,sum(size) volume ",
            "FROM bm_{symbol}_ticks {tick_filter} ",
            "SAMPLE BY 1m FILL(NONE) ALIGN TO CALENDAR",
        ),
        symbol = symbol,
        tick_filter = tick_filter,
    );

    // Standard imported OHLCV tables use QuestDB's microsecond timestamp type.
    // Normalize it to nanoseconds before it is merged with Bookmap.
    let mut minutes = parse_candles(optional_table_query(questdb, &ohlcv_sql).await?, 1_000)?;
    // Bookmap is deliberately inserted second: its minute replaces the normal
    // OHLCV minute in the BTreeMap whenever both sources cover that minute.
    // The Bookmap collector writes TIMESTAMP_NS, which is already nanoseconds.
    for (timestamp, candle) in parse_candles(optional_table_query(questdb, &bookmap_sql).await?, 1)?
    {
        minutes.insert(timestamp, candle);
    }

    aggregate_minutes(minutes, tf)
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

/// Returns the timestamp predicate shared by regular one-minute OHLCV and the
/// Bookmap tick table.  When the chart has no selected range, keep the source
/// query bounded before the Bookmap tick aggregation reaches Rust.
fn march_filter(from: &str, to: &str, lookback_days: u32, require_trade: bool) -> String {
    let mut clauses = Vec::new();
    if require_trade {
        // Bookmap emits zero-size execution-boundary markers.  They are useful
        // in the raw tick log but must not turn an otherwise empty minute into a
        // Bookmap candle or influence OHLC.
        clauses.push("size > 0".to_owned());
    }
    if !from.is_empty() {
        clauses.push(format!("timestamp >= '{from}'"));
    }
    if !to.is_empty() {
        clauses.push(format!("timestamp < dateadd('d',1,'{to}')"));
    }
    if from.is_empty() && to.is_empty() {
        clauses.push(format!("timestamp >= dateadd('d',-{lookback_days},now())"));
    }
    format!("WHERE {}", clauses.join(" AND "))
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

/// A Bookmap collector may be running before standard OHLCV is imported, or
/// vice versa.  Either source is valid on its own; only a missing table is
/// optional, while connection and query errors still reach the caller.
async fn optional_table_query(
    questdb: &QuestDb,
    sql: &str,
) -> Result<Vec<csv::StringRecord>, ApiError> {
    match questdb.csv(sql).await {
        Ok(rows) => Ok(rows),
        Err(ApiError::QuestDb(detail)) if detail.contains("table does not exist") => Ok(Vec::new()),
        Err(error) => Err(error),
    }
}

/// Normalize source timestamps to nanoseconds.  Imported OHLCV uses microseconds
/// while the Bookmap collector uses QuestDB's TIMESTAMP_NS type.
fn parse_candles(
    rows: Vec<csv::StringRecord>,
    timestamp_to_nanos: i64,
) -> Result<BTreeMap<i64, Candle>, ApiError> {
    rows.into_iter()
        .map(|row| {
            let candle = Candle {
                timestamp: parse::<i64>(&row, 0)? * timestamp_to_nanos,
                open: parse(&row, 1)?,
                high: parse(&row, 2)?,
                low: parse(&row, 3)?,
                close: parse(&row, 4)?,
                volume: parse(&row, 5)?,
            };
            Ok((candle.timestamp, candle))
        })
        .collect()
}

fn aggregate_minutes(minutes: BTreeMap<i64, Candle>, tf: &str) -> Result<Vec<Candle>, ApiError> {
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
    let bucket_size = seconds * 1_000_000_000;
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
    questdb: &QuestDb,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<u8>, ApiError> {
    validate_timeframe(tf)?;
    let filter = if !from.is_empty() && !to.is_empty() {
        format!(" WHERE timestamp >= '{from}' AND timestamp < dateadd('d',1,'{to}')")
    } else if !from.is_empty() {
        format!(" WHERE timestamp >= '{from}'")
    } else {
        String::new()
    };
    let agg = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,first(mid) open,max(mid) high,",
            "min(mid) low,last(mid) close,count() volume FROM (",
            "SELECT timestamp,(BID+ASK)/2.0 mid FROM fx_nq_ticks{filter}",
            ") timestamp(timestamp) SAMPLE BY {tf} FILL(NONE) ALIGN TO CALENDAR",
        ),
        filter = filter,
        tf = tf
    );
    let sql = if from.is_empty() {
        format!(
            concat!(
                "SELECT ts,open,high,low,close,volume FROM (",
                "{agg} ORDER BY ts DESC LIMIT 1500",
                ") ORDER BY ts ASC",
            ),
            agg = agg
        )
    } else {
        agg
    };
    // FX candles use QuestDB's regular microsecond timestamp type.
    let candles = parse_candles(query(questdb, &sql).await?, 1_000)?
        .into_values()
        .collect();
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
    questdb: &QuestDb,
    symbol: &str,
    since: Option<i64>,
) -> Result<Vec<Tick>, ApiError> {
    validate_symbol(symbol)?;

    // Try to query with best_bid and best_ask columns first
    let sql_with_bbo = if let Some(since) = since {
        format!(
            concat!(
                "SELECT ts,price,size,side,best_bid,best_ask FROM (",
                "SELECT cast(timestamp as long) ts,price,size,side,best_bid,best_ask ",
                "FROM bm_{symbol}_ticks ",
                "WHERE cast(timestamp as long) > {since} ",
                "ORDER BY timestamp DESC LIMIT 10000",
                ") ORDER BY ts ASC",
            ),
            symbol = symbol,
            since = since
        )
    } else {
        format!(
            concat!(
                "SELECT ts,price,size,side,best_bid,best_ask FROM (",
                "SELECT cast(timestamp as long) ts,price,size,side,best_bid,best_ask ",
                "FROM bm_{symbol}_ticks ORDER BY timestamp DESC LIMIT 100",
                ") ORDER BY ts ASC",
            ),
            symbol = symbol
        )
    };

    match query(questdb, &sql_with_bbo).await {
        Ok(rows) => {
            rows.into_iter()
                .map(|row| {
                    let price: f64 = parse(&row, 1)?;
                    Ok(Tick {
                        ts: parse(&row, 0)?, // Stored as TIMESTAMP_NS directly
                        price,
                        size: parse(&row, 2)?,
                        side: field(&row, 3)?.into(),
                        best_bid: parse(&row, 4).unwrap_or(price),
                        best_ask: parse(&row, 5).unwrap_or(price),
                    })
                })
                .collect()
        }
        Err(_) => {
            // Fallback for tables without best_bid/best_ask columns
            let sql_without_bbo = if let Some(since) = since {
                format!(
                    concat!(
                        "SELECT ts,price,size,side FROM (",
                        "SELECT cast(timestamp as long) ts,price,size,side ",
                        "FROM bm_{symbol}_ticks ",
                        "WHERE cast(timestamp as long) > {since} ",
                        "ORDER BY timestamp DESC LIMIT 10000",
                        ") ORDER BY ts ASC",
                    ),
                    symbol = symbol,
                    since = since
                )
            } else {
                format!(
                    concat!(
                        "SELECT ts,price,size,side FROM (",
                        "SELECT cast(timestamp as long) ts,price,size,side ",
                        "FROM bm_{symbol}_ticks ORDER BY timestamp DESC LIMIT 100",
                        ") ORDER BY ts ASC",
                    ),
                    symbol = symbol
                )
            };
            let rows = query(questdb, &sql_without_bbo).await?;
            rows.into_iter()
                .map(|row| {
                    let price: f64 = parse(&row, 1)?;
                    Ok(Tick {
                        ts: parse(&row, 0)?,
                        price,
                        size: parse(&row, 2)?,
                        side: field(&row, 3)?.into(),
                        best_bid: price,
                        best_ask: price,
                    })
                })
                .collect()
        }
    }
}

#[derive(Serialize)]
pub struct VolumeDelta {
    time: i64,
    delta: f64,
}
pub async fn volume_delta(
    questdb: &QuestDb,
    symbol: &str,
    tf: &str,
    from: &str,
    to: &str,
) -> Result<Vec<VolumeDelta>, ApiError> {
    let candles = merged_march_candles(questdb, symbol, tf, from, to).await?;
    Ok(candles
        .into_iter()
        .map(|candle| VolumeDelta {
            time: candle.timestamp / 1_000_000_000,
            // Same close-location estimate used previously, now calculated
            // from the exact candles displayed on the March chart.
            delta: if candle.high > candle.low {
                ((2.0 * candle.close - candle.high - candle.low) / (candle.high - candle.low))
                    * candle.volume
            } else {
                0.0
            },
        })
        .collect())
}

#[derive(Serialize)]
pub struct VolumeProfileDeltaBin {
    pub day: i64,
    pub price_bin: f64,
    pub delta: f64,
}

pub async fn volume_profile_delta(
    questdb: &QuestDb,
    symbol: &str,
    tick_size: f64,
    from: &str,
    to: &str,
) -> Result<Vec<VolumeProfileDeltaBin>, ApiError> {
    validate_symbol(symbol)?;
    let filter = march_filter(from, to, 0, true);
    let sql = format!(
        concat!(
            "SELECT ",
            "cast(date_trunc('day', timestamp) as long) day, ",
            "round(price / {tick_size}) * {tick_size} price_bin, ",
            "side, ",
            "sum(size) total_vol ",
            "FROM bm_{symbol}_ticks {filter} ",
            "GROUP BY day, price_bin, side"
        ),
        symbol = symbol,
        tick_size = tick_size,
        filter = filter
    );

    let rows = match questdb.csv(&sql).await {
        Ok(rows) => rows,
        Err(ApiError::QuestDb(detail)) if detail.contains("table does not exist") => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };

    let mut bins = Vec::new();
    for row in rows {
        let day_nanos: i64 = parse(&row, 0)?;
        let day_secs = day_nanos / 1_000_000_000;
        let price_bin: f64 = parse(&row, 1)?;
        let side = field(&row, 2)?;
        let total_vol: f64 = parse(&row, 3)?;

        let signed_vol = if side.to_uppercase() == "BUY" {
            total_vol
        } else {
            -total_vol
        };

        bins.push(VolumeProfileDeltaBin {
            day: day_secs,
            price_bin,
            delta: signed_vol,
        });
    }

    Ok(bins)
}

/// Returns sampled Bookmap resting-depth state changes. Each row is encoded as
/// u32 fake-UTC seconds, f32 price, f32 current size. Sampling is deliberately
/// finer than a candle while keeping the response bounded enough for canvas.
pub async fn bookmap_heatmap(
    questdb: &QuestDb,
    symbol: &str,
    tf: &str,
    from: &str,
    to: &str,
    since: Option<i64>,
) -> Result<Vec<u8>, ApiError> {
    validate_symbol(symbol)?;
    validate_timeframe(tf)?;
    let sample = match tf {
        "1m" => "5s",
        "5m" => "15s",
        "15m" => "1m",
        "30m" => "2m",
        "1h" => "5m",
        "4h" => "15m",
        "1d" => "1h",
        _ => unreachable!(),
    };
    let mut filter = march_filter(from, to, 2, false);
    if let Some(since) = since {
        let since_nanos = since.saturating_mul(1_000_000_000);
        filter.push_str(&format!(" AND cast(timestamp as long) > {since_nanos}"));
    }
    let sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,price_level,last(size_level) size ",
            "FROM bm_{symbol}_depth {filter} ",
            "SAMPLE BY {sample} FILL(NONE) ALIGN TO CALENDAR",
        ),
        symbol = symbol,
        filter = filter,
        sample = sample,
    );
    let rows = optional_table_query(questdb, &sql).await?;
    let mut output = Vec::with_capacity(8 + rows.len() * 12);
    output.extend_from_slice(&HEATMAP_MAGIC.to_le_bytes());
    output.extend_from_slice(&(rows.len() as u32).to_le_bytes());
    for row in rows {
        let nanos: i64 = parse(&row, 0)?;
        let price_level: i64 = parse(&row, 1)?;
        let size: i64 = parse(&row, 2)?;
        output.extend_from_slice(&((nanos / 1_000_000_000) as u32).to_le_bytes());
        output.extend_from_slice(&((price_level as f32) / 4.0).to_le_bytes());
        output.extend_from_slice(&(size as f32).to_le_bytes());
    }
    Ok(output)
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
