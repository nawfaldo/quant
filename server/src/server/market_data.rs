use crate::{error::ApiError, questdb::QuestDb};
use serde::Serialize;
use serde_json::{Value, json};
use std::{collections::BTreeMap, time::Duration};

pub const CANDLE_MAGIC: u32 = 0x4544_4C43;
pub const VWAP_MAGIC: u32 = 0x5041_5756;
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
    validate_symbol(symbol)?;
    validate_timeframe(tf)?;
    let sql = if !from.is_empty() && !to.is_empty() {
        format!(
            concat!(
                "SELECT cast(timestamp as long) ts,open,high,low,close,volume ",
                "FROM {symbol}_{tf} WHERE timestamp >= '{from}' ",
                "AND timestamp < dateadd('d',1,'{to}') ",
                "ORDER BY timestamp ASC",
            ),
            symbol = symbol,
            tf = tf,
            from = from,
            to = to
        )
    } else if !from.is_empty() {
        format!(
            concat!(
                "SELECT cast(timestamp as long) ts,open,high,low,close,volume ",
                "FROM {symbol}_{tf} WHERE timestamp >= '{from}' ",
                "ORDER BY timestamp ASC",
            ),
            symbol = symbol,
            tf = tf,
            from = from
        )
    } else {
        format!(
            concat!(
                "SELECT ts,open,high,low,close,volume FROM (",
                "SELECT cast(timestamp as long) ts,open,high,low,close,volume ",
                "FROM {symbol}_{tf} ORDER BY timestamp DESC LIMIT 1500",
                ") ORDER BY ts ASC",
            ),
            symbol = symbol,
            tf = tf
        )
    };
    let rows = query(questdb, &sql).await?;
    candle_volume_binary(rows)
}

fn candle_volume_binary(rows: Vec<csv::StringRecord>) -> Result<Vec<u8>, ApiError> {
    let mut output = Vec::with_capacity(8 + rows.len() * 24);
    output.extend_from_slice(&CANDLE_MAGIC.to_le_bytes());
    output.extend_from_slice(&(rows.len() as u32).to_le_bytes());
    for row in rows {
        output.extend_from_slice(&((parse::<i64>(&row, 0)? / 1_000_000) as u32).to_le_bytes());
        for i in 1..=5 {
            output.extend_from_slice(&parse::<f32>(&row, i)?.to_le_bytes());
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
    candle_volume_binary(query(questdb, &sql).await?)
}

#[derive(Serialize)]
pub struct Tick {
    ts: i64,
    price: f64,
    size: f64,
    side: String,
}
pub async fn ticks(
    questdb: &QuestDb,
    symbol: &str,
    since: Option<i64>,
) -> Result<Vec<Tick>, ApiError> {
    validate_symbol(symbol)?;
    let sql = if let Some(since) = since {
        format!(
            concat!(
                "SELECT cast(timestamp as long) ts,price,size,side ",
                "FROM bm_{symbol}_ticks ",
                "WHERE cast(timestamp as long) > {since} ",
                "ORDER BY timestamp ASC LIMIT 10000",
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
    query(questdb, &sql)
        .await?
        .into_iter()
        .map(|row| {
            Ok(Tick {
                ts: parse(&row, 0)?,
                price: parse(&row, 1)?,
                size: parse(&row, 2)?,
                side: field(&row, 3)?.into(),
            })
        })
        .collect()
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
    validate_symbol(symbol)?;
    validate_timeframe(tf)?;
    let filter = if !from.is_empty() && !to.is_empty() {
        format!("WHERE timestamp >= '{from}' AND timestamp < dateadd('d',1,'{to}')")
    } else if !from.is_empty() {
        format!("WHERE timestamp >= '{from}'")
    } else {
        "WHERE timestamp >= dateadd('d',-7,now())".into()
    };
    let sql = format!(
        concat!(
            "SELECT cast(timestamp as long) ts,",
            "sum(CASE WHEN high > low THEN ",
            "((2.0*close-high-low)/(high-low))*volume ELSE 0.0 END) delta ",
            "FROM {symbol}_1m {filter} SAMPLE BY {tf} FILL(NONE) ",
            "ALIGN TO CALENDAR ORDER BY timestamp ASC",
        ),
        symbol = symbol,
        filter = filter,
        tf = tf
    );
    query(questdb, &sql)
        .await?
        .into_iter()
        .map(|r| {
            Ok(VolumeDelta {
                time: parse::<i64>(&r, 0)? / 1_000_000,
                delta: parse(&r, 1)?,
            })
        })
        .collect()
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
}
