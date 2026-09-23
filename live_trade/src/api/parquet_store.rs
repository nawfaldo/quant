//! Read access to `data/parquet/`. The engine's only market-data source.
//!
//! QuestDB used to serve every bar, tick, depth event and L2 feature over HTTP,
//! and this module replaces it. The tables were exported to Parquet and then
//! dropped, so a missing file is a missing table -- there is nothing to fall
//! back to and no SQL to fall back with.
//!
//! WHAT THE SQL DID THAT THIS HAS TO DO INSTEAD.
//!
//! The old loaders leaned on three QuestDB behaviours. `SAMPLE BY 1m` bucketed
//! rows into minutes; `WHERE timestamp >= x AND timestamp < y` restricted the
//! scan; and month-sized query windows kept a multi-year level-two read inside
//! the server's query timeout. Only the first two are still needed. Bucketing
//! moved to the callers, which know whether they want a trade rollup or a
//! feature rollup, and the range restriction happens here by SKIPPING ROW
//! GROUPS: the Parquet footer carries per-group minimum and maximum
//! timestamps, so a one-day read of the five-billion-row depth table decodes a
//! few groups rather than forty gigabytes. There is no query timeout to chunk
//! around any more.
//!
//! TIMESTAMPS ARE TEXT, FOR NOW. The CSV export wrote
//! `2008-12-11T02:38:00.000000Z`, so the timestamp column is `Utf8` and has to
//! be parsed. `parse_iso_nanoseconds` reads the fixed layout byte by byte, which
//! is both faster than a general date parser and stricter. `timestamp_column`
//! also accepts the Int64 and Timestamp forms a normalized file would carry, so
//! rewriting the store later needs no change here.
//!
//! Fixed-width ISO-8601 sorts lexicographically in chronological order, which is
//! what lets `row_group_overlaps` prune a text column by comparing text.
//!
//! TIME. Market timestamps are New York wall-clock encoded as fake UTC (see
//! AGENT.md). Nothing here converts a zone. Callers work in epoch seconds;
//! internally everything is epoch nanoseconds, because the tick and depth tables
//! carry nanosecond timestamps and the bar tables do not.
//!
//! LAYOUT. A table is `data/parquet/<table>.parquet`, or the directory
//! `data/parquet/<table>/*.parquet`, or BOTH -- the bulk history in one file and
//! a live collector appending one shard per day beside it. `table_files` reads
//! the pair as one series, so a running feed never rewrites history.

use crate::error::ApiError;
use arrow_array::{
    Array, BooleanArray, Float64Array, Int64Array, RecordBatch, StringArray,
    TimestampNanosecondArray, cast::AsArray,
};
use parquet::arrow::ProjectionMask;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::file::statistics::Statistics;
use std::fs::File;
use std::path::{Path, PathBuf};

pub const NANOS_PER_SECOND: i64 = 1_000_000_000;
pub const NANOS_PER_MINUTE: i64 = 60 * NANOS_PER_SECOND;

#[derive(Clone)]
pub struct ParquetStore {
    root: PathBuf,
}

impl ParquetStore {
    /// The store under `MARKET_DATA_DIR`, or `data/parquet` beside the server.
    pub fn from_env() -> anyhow::Result<Self> {
        let root = match std::env::var("MARKET_DATA_DIR") {
            Ok(value) => PathBuf::from(value),
            Err(_) => default_root(),
        };
        Ok(Self::new(root))
    }

    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Every file backing `table`, oldest first.
    ///
    /// BOTH LAYOUTS AT ONCE, WHICH IS THE POINT. `<table>.parquet` is the bulk
    /// history exported out of QuestDB and `<table>/<date>.parquet` is what a
    /// live collector appends, so a table normally has one of each and the
    /// reader has to see the pair as one series. Returning only the single file
    /// when it exists -- which this did at first -- makes a running feed
    /// invisible to every reader while looking like it is working.
    ///
    /// Shards sort by name, and their names are dates, so file order is time
    /// order and no caller has to re-sort what it reads.
    pub fn table_files(&self, table: &str) -> Vec<PathBuf> {
        let mut files = Vec::new();
        let single = self.root.join(format!("{table}.parquet"));
        if single.is_file() {
            files.push(single);
        }
        let directory = self.root.join(table);
        if directory.is_dir() {
            let mut shards: Vec<PathBuf> = std::fs::read_dir(&directory)
                .into_iter()
                .flatten()
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| path.extension().is_some_and(|ext| ext == "parquet"))
                .collect();
            shards.sort();
            files.append(&mut shards);
        }
        files
    }

    pub fn has_table(&self, table: &str) -> bool {
        !self.table_files(table).is_empty()
    }

    /// Every table name in the store, both layouts, sorted.
    pub fn tables(&self) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(&self.root)
            .into_iter()
            .flatten()
            .flatten()
            .filter_map(|entry| {
                let path = entry.path();
                if path.is_file() && path.extension().is_some_and(|ext| ext == "parquet") {
                    return path
                        .file_stem()
                        .map(|stem| stem.to_string_lossy().into_owned());
                }
                if path.is_dir()
                    && !self
                        .table_files(&path.file_name()?.to_string_lossy())
                        .is_empty()
                {
                    return path
                        .file_name()
                        .map(|name| name.to_string_lossy().into_owned());
                }
                None
            })
            .collect();
        names.sort();
        names.dedup();
        names
    }

    /// The column names of `table`, or none when the table is absent.
    pub fn column_names(&self, table: &str) -> Result<Vec<String>, ApiError> {
        let Some(path) = self.table_files(table).into_iter().next() else {
            return Ok(Vec::new());
        };
        Ok(open(&path)?
            .schema()
            .fields()
            .iter()
            .map(|field| field.name().clone())
            .collect())
    }

    pub fn has_column(&self, table: &str, column: &str) -> Result<bool, ApiError> {
        Ok(self.column_names(table)?.iter().any(|name| name == column))
    }

    /// `(first, last)` epoch nanoseconds, read from footer statistics alone.
    pub fn bounds(&self, table: &str) -> Result<Option<(i64, i64)>, ApiError> {
        let mut span: Option<(i64, i64)> = None;
        for path in self.table_files(table) {
            let builder = open(&path)?;
            let index = timestamp_index(&builder, &path)?;
            for group in builder.metadata().row_groups() {
                let Some(statistics) = group.column(index).statistics() else {
                    return Err(ApiError::Data(format!(
                        "{} has no timestamp statistics to read bounds from",
                        path.display()
                    )));
                };
                let Some((low, high)) = statistic_bounds(statistics)? else {
                    continue;
                };
                span = Some(match span {
                    None => (low, high),
                    Some((first, last)) => (first.min(low), last.max(high)),
                });
            }
        }
        Ok(span)
    }

    /// The newest timestamp in `table`, in epoch nanoseconds.
    pub fn max_timestamp(&self, table: &str) -> Result<Option<i64>, ApiError> {
        Ok(self.bounds(table)?.map(|(_, last)| last))
    }

    /// Decoded batches for `columns` over `[from, to)`, oldest first.
    ///
    /// `timestamp` is always projected and always the LAST column of the
    /// returned batch, whatever order `columns` asks for, so callers read it by
    /// name rather than by position.
    ///
    /// A missing table yields no batches rather than an error. The order-flow
    /// loaders read four tables of which any may be absent, and the SQL that
    /// preceded this treated "table does not exist" the same way.
    pub fn scan(
        &self,
        table: &str,
        columns: &[&str],
        from: Option<i64>,
        to: Option<i64>,
    ) -> Result<Vec<RecordBatch>, ApiError> {
        let mut wanted: Vec<&str> = Vec::with_capacity(columns.len() + 1);
        for name in columns {
            if !wanted.contains(name) && *name != "timestamp" {
                wanted.push(name);
            }
        }
        wanted.push("timestamp");

        let mut out = Vec::new();
        for path in self.table_files(table) {
            let builder = open(&path)?;
            let index = timestamp_index(&builder, &path)?;
            let keep: Vec<usize> = builder
                .metadata()
                .row_groups()
                .iter()
                .enumerate()
                .filter(|(_, group)| {
                    group
                        .column(index)
                        .statistics()
                        .is_none_or(|statistics| row_group_overlaps(statistics, from, to))
                })
                .map(|(position, _)| position)
                .collect();
            if keep.is_empty() {
                continue;
            }

            let schema = builder.parquet_schema();
            let mut projection = Vec::with_capacity(wanted.len());
            for name in &wanted {
                let position = builder
                    .schema()
                    .index_of(name)
                    .map_err(|_| ApiError::Data(format!("{table} has no column {name}")))?;
                projection.push(position);
            }
            let mask = ProjectionMask::roots(schema, projection);

            let reader = builder
                .with_row_groups(keep)
                .with_projection(mask)
                .with_batch_size(65_536)
                .build()
                .map_err(|error| ApiError::Data(error.to_string()))?;
            for batch in reader {
                let batch = batch.map_err(|error| ApiError::Data(error.to_string()))?;
                if let Some(batch) = restrict(batch, from, to)? {
                    out.push(batch);
                }
            }
        }
        Ok(out)
    }
}

fn default_root() -> PathBuf {
    // The server runs from `live_trade/`, so the store is one level up. An absolute
    // `MARKET_DATA_DIR` is the escape hatch for any other layout.
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap_or(Path::new(".."))
        .join("data")
        .join("parquet")
}

type Builder = ParquetRecordBatchReaderBuilder<File>;

/// Parsed footers, keyed by path and invalidated by the file's own size and
/// modification time.
///
/// EVERY READ USED TO REPARSE THE FOOTER. `try_new` seeks to the end of the
/// file, decodes the Thrift metadata and builds the Arrow schema, and the
/// metadata is per row group: a table with thousands of groups pays for all of
/// them to answer a one-day question. Nothing in a backtest reads one file
/// once -- the book loads seven markets and each sleeve's warm-up reaches back
/// over the same files -- so the same footers were decoded again and again
/// within a single run, and again on the next one.
///
/// The size-and-mtime key is what makes this safe for a live collector: it
/// appends a shard per day beside the bulk file, and a rewritten or extended
/// file changes both, so a stale footer can never be served. A missing stat
/// falls through to an uncached load rather than guessing.
type FooterCache = std::collections::HashMap<PathBuf, (u64, std::time::SystemTime, Footer)>;

static FOOTERS: std::sync::LazyLock<std::sync::Mutex<FooterCache>> =
    std::sync::LazyLock::new(Default::default);

type Footer = parquet::arrow::arrow_reader::ArrowReaderMetadata;

fn footer_key(path: &Path) -> Option<(u64, std::time::SystemTime)> {
    let meta = path.metadata().ok()?;
    Some((meta.len(), meta.modified().ok()?))
}

fn open(path: &Path) -> Result<Builder, ApiError> {
    let file =
        File::open(path).map_err(|error| ApiError::Data(format!("{}: {error}", path.display())))?;
    let key = footer_key(path);
    if let Some(key) = key
        && let Some(footer) = FOOTERS
            .lock()
            .expect("parquet footer cache poisoned")
            .get(path)
            .filter(|(len, modified, _)| (*len, *modified) == key)
            .map(|(_, _, footer)| footer.clone())
    {
        return Ok(ParquetRecordBatchReaderBuilder::new_with_metadata(
            file, footer,
        ));
    }
    let footer = Footer::load(
        &file,
        parquet::arrow::arrow_reader::ArrowReaderOptions::new(),
    )
    .map_err(|error| ApiError::Data(format!("{}: {error}", path.display())))?;
    if let Some((len, modified)) = key {
        FOOTERS
            .lock()
            .expect("parquet footer cache poisoned")
            .insert(path.to_path_buf(), (len, modified, footer.clone()));
    }
    Ok(ParquetRecordBatchReaderBuilder::new_with_metadata(
        file, footer,
    ))
}

fn timestamp_index(builder: &Builder, path: &Path) -> Result<usize, ApiError> {
    builder
        .schema()
        .index_of("timestamp")
        .map_err(|_| ApiError::Data(format!("{} has no timestamp column", path.display())))
}

/// Drop the rows of `batch` outside `[from, to)`, or the batch itself if none remain.
///
/// Row-group pruning is coarse -- a group that straddles a boundary is decoded
/// whole -- so the exact edges are trimmed here.
fn restrict(
    batch: RecordBatch,
    from: Option<i64>,
    to: Option<i64>,
) -> Result<Option<RecordBatch>, ApiError> {
    if from.is_none() && to.is_none() {
        return Ok(Some(batch));
    }
    let stamps = timestamp_column(&batch)?;
    let inside = |value: &i64| {
        from.is_none_or(|lower| *value >= lower) && to.is_none_or(|upper| *value < upper)
    };
    if stamps.iter().all(inside) {
        return Ok(Some(batch));
    }
    let first = stamps.iter().position(inside);
    let Some(first) = first else {
        return Ok(None);
    };
    // The tables are time-ordered, so the surviving rows are one contiguous run
    // and the batch can be sliced rather than gathered.
    let last = stamps.iter().rposition(inside).unwrap_or(first);
    Ok(Some(batch.slice(first, last - first + 1)))
}

/// Epoch nanoseconds for a batch's `timestamp` column, in any of its three forms.
pub fn timestamp_column(batch: &RecordBatch) -> Result<Vec<i64>, ApiError> {
    let column = batch
        .column_by_name("timestamp")
        .ok_or_else(|| ApiError::Data("batch has no timestamp column".into()))?;
    if let Some(text) = column.as_any().downcast_ref::<StringArray>() {
        return text
            .iter()
            .map(|value| {
                value
                    .ok_or_else(|| ApiError::Data("null timestamp".into()))
                    .and_then(parse_iso_nanoseconds)
            })
            .collect();
    }
    if let Some(values) = column.as_any().downcast_ref::<TimestampNanosecondArray>() {
        return Ok(values.values().to_vec());
    }
    if let Some(values) = column.as_any().downcast_ref::<Int64Array>() {
        return Ok(values.values().to_vec());
    }
    Err(ApiError::Data(format!(
        "unsupported timestamp type {}",
        column.data_type()
    )))
}

/// `YYYY-MM-DDTHH:MM:SS[.fraction][Z]` as epoch nanoseconds.
///
/// The layout is fixed, so this reads it positionally instead of going through a
/// general parser. Anything that does not match is an error rather than a
/// silently wrong instant -- the migration's worst bug was an Arrow strptime
/// pattern that quietly returned null for every row it was given.
pub fn parse_iso_nanoseconds(text: &str) -> Result<i64, ApiError> {
    let bytes = text.as_bytes();
    if bytes.len() < 19 || bytes[4] != b'-' || bytes[7] != b'-' {
        return Err(ApiError::Data(format!("unparseable timestamp {text:?}")));
    }
    let digits = |start: usize, len: usize| -> Result<i64, ApiError> {
        let mut value: i64 = 0;
        for offset in start..start + len {
            let byte = *bytes
                .get(offset)
                .ok_or_else(|| ApiError::Data(format!("short timestamp {text:?}")))?;
            if !byte.is_ascii_digit() {
                return Err(ApiError::Data(format!("unparseable timestamp {text:?}")));
            }
            value = value * 10 + i64::from(byte - b'0');
        }
        Ok(value)
    };
    let year = digits(0, 4)?;
    let month = digits(5, 2)?;
    let day = digits(8, 2)?;
    let hour = digits(11, 2)?;
    let minute = digits(14, 2)?;
    let second = digits(17, 2)?;

    let mut fraction = 0i64;
    if bytes.get(19) == Some(&b'.') {
        let mut scale = 100_000_000i64;
        for byte in &bytes[20..] {
            if !byte.is_ascii_digit() {
                break;
            }
            if scale > 0 {
                fraction += i64::from(byte - b'0') * scale;
                scale /= 10;
            }
        }
    }
    let days = days_from_civil(year, month, day);
    let seconds = days * 86_400 + hour * 3_600 + minute * 60 + second;
    Ok(seconds * NANOS_PER_SECOND + fraction)
}

fn days_from_civil(y0: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y0 - 1 } else { y0 };
    let era = if y >= 0 { y } else { y - 399 }.div_euclid(400);
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

/// `(min, max)` of a row group's timestamp statistics, in epoch nanoseconds.
fn statistic_bounds(statistics: &Statistics) -> Result<Option<(i64, i64)>, ApiError> {
    match statistics {
        Statistics::ByteArray(values) => {
            let (Some(low), Some(high)) = (values.min_opt(), values.max_opt()) else {
                return Ok(None);
            };
            let text = |value: &parquet::data_type::ByteArray| -> Result<i64, ApiError> {
                let decoded = std::str::from_utf8(value.data())
                    .map_err(|_| ApiError::Data("non-UTF-8 timestamp statistic".into()))?;
                parse_iso_nanoseconds(decoded)
            };
            Ok(Some((text(low)?, text(high)?)))
        }
        Statistics::Int64(values) => match (values.min_opt(), values.max_opt()) {
            (Some(low), Some(high)) => Ok(Some((*low, *high))),
            _ => Ok(None),
        },
        _ => Ok(None),
    }
}

/// Whether a row group can hold a row in `[from, to)`.
///
/// Compared in the file's OWN domain: a text column is compared as text, which
/// works because fixed-width ISO-8601 sorts chronologically, and an Int64 column
/// as numbers. Neither needs the group's bounds converted per read.
fn row_group_overlaps(statistics: &Statistics, from: Option<i64>, to: Option<i64>) -> bool {
    if from.is_none() && to.is_none() {
        return true;
    }
    match statistic_bounds(statistics) {
        Ok(Some((low, high))) => {
            from.is_none_or(|lower| high >= lower) && to.is_none_or(|upper| low < upper)
        }
        // An unreadable statistic is not evidence of absence; decode the group
        // and let `restrict` decide row by row.
        _ => true,
    }
}

// --------------------------------------------------------------------------- #
// column accessors
// --------------------------------------------------------------------------- #

/// A named `f64` column, whatever numeric type the file stores it as.
pub fn floats(batch: &RecordBatch, name: &str) -> Result<Vec<f64>, ApiError> {
    let column = batch
        .column_by_name(name)
        .ok_or_else(|| ApiError::Data(format!("batch has no column {name}")))?;
    if let Some(values) = column.as_any().downcast_ref::<Float64Array>() {
        return Ok((0..values.len())
            .map(|index| {
                if values.is_null(index) {
                    0.0
                } else {
                    values.value(index)
                }
            })
            .collect());
    }
    if let Some(values) = column.as_any().downcast_ref::<Int64Array>() {
        return Ok((0..values.len())
            .map(|index| {
                if values.is_null(index) {
                    0.0
                } else {
                    values.value(index) as f64
                }
            })
            .collect());
    }
    Err(ApiError::Data(format!(
        "column {name} is {}, which is not numeric",
        column.data_type()
    )))
}

/// A named boolean column; a null reads as false.
pub fn booleans(batch: &RecordBatch, name: &str) -> Result<Vec<bool>, ApiError> {
    let column = batch
        .column_by_name(name)
        .ok_or_else(|| ApiError::Data(format!("batch has no column {name}")))?;
    let values: &BooleanArray = column.as_boolean_opt().ok_or_else(|| {
        ApiError::Data(format!(
            "column {name} is {}, which is not boolean",
            column.data_type()
        ))
    })?;
    Ok((0..values.len())
        .map(|index| !values.is_null(index) && values.value(index))
        .collect())
}

/// Whether each row of a named text column equals `wanted`.
///
/// Returns the comparison rather than the strings: `side` and `source` are the
/// only text columns the loaders read, both are asked a yes/no question, and
/// materialising a hundred million `String`s to answer it is the difference
/// between a scan and an out-of-memory.
pub fn text_equals(batch: &RecordBatch, name: &str, wanted: &str) -> Result<Vec<bool>, ApiError> {
    let column = batch
        .column_by_name(name)
        .ok_or_else(|| ApiError::Data(format!("batch has no column {name}")))?;
    let values = column
        .as_any()
        .downcast_ref::<StringArray>()
        .ok_or_else(|| {
            ApiError::Data(format!(
                "column {name} is {}, which is not text",
                column.data_type()
            ))
        })?;
    Ok((0..values.len())
        .map(|index| !values.is_null(index) && values.value(index) == wanted)
        .collect())
}

/// A named text column as owned strings. Only for small results.
pub fn strings(batch: &RecordBatch, name: &str) -> Result<Vec<String>, ApiError> {
    let column = batch
        .column_by_name(name)
        .ok_or_else(|| ApiError::Data(format!("batch has no column {name}")))?;
    let values = column
        .as_any()
        .downcast_ref::<StringArray>()
        .ok_or_else(|| {
            ApiError::Data(format!(
                "column {name} is {}, which is not text",
                column.data_type()
            ))
        })?;
    Ok((0..values.len())
        .map(|index| {
            if values.is_null(index) {
                String::new()
            } else {
                values.value(index).to_owned()
            }
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_the_exported_timestamp_layouts() {
        // Bar tables were exported with microsecond precision, the tick and
        // depth tables with nanosecond.
        assert_eq!(
            parse_iso_nanoseconds("1970-01-01T00:00:00.000000Z").unwrap(),
            0
        );
        assert_eq!(
            parse_iso_nanoseconds("2008-12-11T02:38:00.000000Z").unwrap(),
            1_228_963_080 * NANOS_PER_SECOND
        );
        assert_eq!(
            parse_iso_nanoseconds("2026-07-17T09:30:00.017974016Z").unwrap(),
            1_784_280_600 * NANOS_PER_SECOND + 17_974_016
        );
        // No fraction and no zone suffix are both accepted.
        assert_eq!(
            parse_iso_nanoseconds("2024-01-01T00:00:00").unwrap(),
            1_704_067_200 * NANOS_PER_SECOND
        );
    }

    #[test]
    fn rejects_rather_than_guesses() {
        assert!(parse_iso_nanoseconds("2024-01-01").is_err());
        assert!(parse_iso_nanoseconds("not a timestamp").is_err());
        assert!(parse_iso_nanoseconds("2024-XX-01T00:00:00Z").is_err());
    }

    /// The reader against the real store. Ignored by default because it needs
    /// `data/parquet` populated, which a checkout does not have.
    ///
    /// ```text
    /// cargo test --lib -- --ignored reads_the_real_store
    /// ```
    #[test]
    #[ignore = "requires a populated data/parquet"]
    fn reads_the_real_store() {
        let store = ParquetStore::from_env().unwrap();
        assert!(store.has_table("nq_1m"), "nq_1m missing from the store");

        let (first, last) = store.bounds("nq_1m").unwrap().expect("nq_1m bounds");
        assert!(first < last);

        // One day, to prove row-group pruning trims to the range asked for.
        let day = parse_iso_nanoseconds("2024-06-03T00:00:00").unwrap();
        let batches = store
            .scan(
                "nq_1m",
                &["open", "close"],
                Some(day),
                Some(day + 86_400 * NANOS_PER_SECOND),
            )
            .unwrap();
        let stamps: Vec<i64> = batches
            .iter()
            .flat_map(|batch| timestamp_column(batch).unwrap())
            .collect();
        assert!(!stamps.is_empty(), "no rows for 2024-06-03");
        assert!(stamps.iter().all(|value| *value >= day));
        assert!(
            stamps
                .iter()
                .all(|value| *value < day + 86_400 * NANOS_PER_SECOND)
        );

        // A table that is not there reads as empty rather than failing, which
        // is what the order-flow loaders rely on.
        assert!(!store.has_table("vix_1d"));
        assert!(
            store
                .scan("vix_1d", &["close"], None, None)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn text_bounds_prune_by_lexicographic_order() {
        // The property the whole pruning path rests on: ISO-8601 at a fixed
        // width sorts in chronological order, so a byte-array statistic can be
        // compared without being parsed per row.
        assert!("2024-01-01T00:00:00.000000Z" < "2024-01-02T00:00:00.000000Z");
        assert!("2009-12-31T23:59:00.000000Z" < "2010-01-01T00:00:00.000000Z");
    }
}
