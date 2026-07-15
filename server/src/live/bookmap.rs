use crate::{backtest::LiveBar, state::AppState};
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use std::time::Duration;
use tokio_tungstenite::{connect_async, tungstenite::Message};

const STRATEGY: &str = "night_drift";

#[derive(Clone, Copy, Debug, PartialEq)]
struct Tick {
    timestamp_nanos: i64,
    price: f64,
    size: f64,
}

#[derive(Clone, Copy)]
struct CompletedBar {
    timestamp: i64,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

#[derive(Default)]
struct BarBuilder {
    current_minute: Option<i64>,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

impl BarBuilder {
    fn push(&mut self, tick: Tick) -> Option<CompletedBar> {
        let timestamp = tick.timestamp_nanos.div_euclid(1_000_000_000);
        let minute = timestamp.div_euclid(60) * 60;
        let completed = self
            .current_minute
            .filter(|current| *current != minute)
            .map(|current| CompletedBar {
                timestamp: current,
                open: self.open,
                high: self.high,
                low: self.low,
                close: self.close,
                volume: self.volume,
            });

        if self.current_minute != Some(minute) {
            self.current_minute = Some(minute);
            self.open = tick.price;
            self.high = tick.price;
            self.low = tick.price;
            self.close = tick.price;
            self.volume = tick.size;
        } else {
            self.high = self.high.max(tick.price);
            self.low = self.low.min(tick.price);
            self.close = tick.price;
            self.volume += tick.size;
        }
        completed
    }
}

pub fn spawn(state: AppState) {
    let Some(url) = configured_url() else {
        return;
    };
    actix_web::rt::spawn(async move {
        run(state, url).await;
    });
}

fn configured_url() -> Option<String> {
    match std::env::var("BOOKMAP_WS_URL") {
        Ok(url) if url.trim().is_empty() => None,
        Ok(url) => Some(url),
        Err(_) if cfg!(windows) => Some("ws://127.0.0.1:8765".into()),
        Err(_) => None,
    }
}

async fn run(state: AppState, url: String) {
    loop {
        match connect_async(&url).await {
            Ok((stream, _)) => {
                tracing::info!(%url, "connected to Bookmap");
                consume(&state, stream).await;
                tracing::warn!(%url, "Bookmap disconnected");
            }
            Err(error) => tracing::warn!(%url, %error, "Bookmap connection failed"),
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

async fn consume(
    state: &AppState,
    mut stream: tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
) {
    let mut bars = BarBuilder::default();
    while let Some(message) = stream.next().await {
        match message {
            Ok(Message::Text(payload)) => {
                for tick in parse_ticks(payload.as_ref()) {
                    if let Some(bar) = bars.push(tick) {
                        publish_bar(state, bar).await;
                    }
                    publish_tick(state, tick).await;
                }
            }
            Ok(Message::Ping(payload)) => {
                if stream.send(Message::Pong(payload)).await.is_err() {
                    return;
                }
            }
            Ok(Message::Close(_)) | Err(_) => return,
            _ => {}
        }
    }
}

async fn publish_bar(state: &AppState, bar: CompletedBar) {
    let Some(decision) = state.march.update(LiveBar {
        timestamp: bar.timestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume,
        vix: None,
    }) else {
        return;
    };
    if let Err(error) = state
        .executor
        .publish_unix(&state.db, STRATEGY, bar.timestamp, bar.close, decision)
        .await
    {
        tracing::warn!(%error, "unable to publish completed Bookmap bar");
    }
}

async fn publish_tick(state: &AppState, tick: Tick) {
    let timestamp = tick.timestamp_nanos.div_euclid(1_000_000_000);
    let Some(decision) = state.march.update_tick(tick.price, timestamp) else {
        return;
    };
    if let Err(error) = state
        .executor
        .publish_unix(&state.db, STRATEGY, timestamp, tick.price, decision)
        .await
    {
        tracing::warn!(%error, "unable to publish Bookmap tick");
    }
}

fn parse_ticks(payload: &str) -> Vec<Tick> {
    let Ok(value) = serde_json::from_str::<Value>(payload) else {
        return Vec::new();
    };
    let mut ticks = Vec::new();
    collect_ticks(&value, &mut ticks);
    ticks
}

fn collect_ticks(value: &Value, output: &mut Vec<Tick>) {
    match value {
        Value::Array(values) => {
            for value in values {
                collect_ticks(value, output);
            }
        }
        Value::Object(object) => {
            let is_nq = object.get("sym").and_then(Value::as_str) == Some("NQ");
            let timestamp = object.get("ts").and_then(Value::as_f64);
            let price = object.get("price").and_then(Value::as_f64);
            if let (true, Some(timestamp), Some(price)) = (is_nq, timestamp, price) {
                output.push(Tick {
                    timestamp_nanos: timestamp as i64,
                    price,
                    size: object.get("size").and_then(Value::as_f64).unwrap_or(1.0),
                });
                return;
            }
            for value in object.values() {
                collect_ticks(value, output);
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_only_nq_ticks() {
        let ticks = parse_ticks(
            r#"[{"sym":"ES","ts":1,"price":2},{"sym":"NQ","ts":3,"price":4,"size":5}]"#,
        );
        assert_eq!(ticks.len(), 1);
        assert_eq!(ticks[0].price, 4.0);
        assert_eq!(ticks[0].size, 5.0);
    }

    #[test]
    fn completes_the_previous_minute() {
        let mut builder = BarBuilder::default();
        assert!(builder.push(tick(60, 10.0, 2.0)).is_none());
        assert!(builder.push(tick(61, 12.0, 3.0)).is_none());
        let bar = builder.push(tick(120, 11.0, 1.0)).unwrap();
        assert_eq!(bar.timestamp, 60);
        assert_eq!(
            (bar.open, bar.high, bar.low, bar.close),
            (10.0, 12.0, 10.0, 12.0)
        );
        assert_eq!(bar.volume, 5.0);
    }

    fn tick(seconds: i64, price: f64, size: f64) -> Tick {
        Tick {
            timestamp_nanos: seconds * 1_000_000_000,
            price,
            size,
        }
    }
}
