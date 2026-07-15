use crate::error::ApiError;
use anyhow::Context;
use futures_util::StreamExt;
use reqwest::Client;
use std::time::Duration;

#[derive(Clone)]
pub struct QuestDb {
    base_url: String,
    client: Client,
}

impl QuestDb {
    pub fn from_env() -> anyhow::Result<Self> {
        let base_url =
            std::env::var("QUESTDB_URL").unwrap_or_else(|_| "http://127.0.0.1:9000".to_owned());
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(180))
            .build()
            .context("failed to construct QuestDB HTTP client")?;
        Ok(Self { base_url, client })
    }

    pub fn new(base_url: impl Into<String>) -> anyhow::Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(180))
            .build()?;
        Ok(Self {
            base_url: base_url.into(),
            client,
        })
    }

    /// Executes SQL through QuestDB's streaming CSV export endpoint. Reqwest
    /// handles chunked transfer decoding; callers still parse one result at a
    /// time so the service never eagerly caches every timeframe.
    pub async fn csv(&self, sql: &str) -> Result<Vec<csv::StringRecord>, ApiError> {
        let response = self
            .client
            .get(format!("{}/exp", self.base_url.trim_end_matches('/')))
            .query(&[("query", sql)])
            .send()
            .await
            .map_err(|e| ApiError::QuestDb(e.to_string()))?;
        let status = response.status();
        let body = response
            .bytes()
            .await
            .map_err(|e| ApiError::QuestDb(e.to_string()))?;
        if !status.is_success() {
            let detail = String::from_utf8_lossy(&body);
            return Err(ApiError::QuestDb(format!("HTTP {status}: {detail}")));
        }

        let mut reader = csv::ReaderBuilder::new().from_reader(body.as_ref());
        reader
            .records()
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| ApiError::QuestDb(format!("invalid CSV response: {e}")))
    }

    /// Streams an ordered CSV result without retaining the complete response.
    /// This is used for tick tables that can contain millions of rows.
    pub async fn stream_csv<F>(&self, sql: &str, mut consume: F) -> Result<(), ApiError>
    where
        F: FnMut(csv::StringRecord) -> Result<(), ApiError>,
    {
        let response = self
            .client
            .get(format!("{}/exp", self.base_url.trim_end_matches('/')))
            .query(&[("query", sql)])
            .send()
            .await
            .map_err(|error| ApiError::QuestDb(error.to_string()))?;

        let status = response.status();
        if !status.is_success() {
            let detail = response
                .text()
                .await
                .unwrap_or_else(|_| "unable to read response".to_owned());
            return Err(ApiError::QuestDb(format!("HTTP {status}: {detail}")));
        }

        let mut chunks = response.bytes_stream();
        let mut buffer = Vec::with_capacity(64 * 1024);
        let mut first_row = true;

        while let Some(chunk) = chunks.next().await {
            let chunk = chunk.map_err(|error| ApiError::QuestDb(error.to_string()))?;
            buffer.extend_from_slice(&chunk);
            consume_complete_lines(&mut buffer, &mut first_row, &mut consume)?;
        }

        if !buffer.is_empty() {
            consume_line(&buffer, &mut first_row, &mut consume)?;
        }

        Ok(())
    }
}

fn consume_complete_lines<F>(
    buffer: &mut Vec<u8>,
    first_row: &mut bool,
    consume: &mut F,
) -> Result<(), ApiError>
where
    F: FnMut(csv::StringRecord) -> Result<(), ApiError>,
{
    let mut consumed = 0;

    while let Some(relative_end) = buffer[consumed..].iter().position(|byte| *byte == b'\n') {
        let end = consumed + relative_end;
        consume_line(&buffer[consumed..end], first_row, consume)?;
        consumed = end + 1;
    }

    if consumed > 0 {
        buffer.drain(..consumed);
    }

    Ok(())
}

fn consume_line<F>(line: &[u8], first_row: &mut bool, consume: &mut F) -> Result<(), ApiError>
where
    F: FnMut(csv::StringRecord) -> Result<(), ApiError>,
{
    if line.is_empty() {
        return Ok(());
    }
    if *first_row {
        *first_row = false;
        return Ok(());
    }

    let mut reader = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_reader(line);
    let record = reader
        .records()
        .next()
        .transpose()
        .map_err(|error| ApiError::QuestDb(format!("invalid CSV response: {error}")))?;

    if let Some(record) = record {
        consume(record)?;
    }

    Ok(())
}
