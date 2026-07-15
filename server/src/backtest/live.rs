use super::{
    data::format_day,
    prepare::backtest_bar_from_csv,
    types::{Action, Bar, Side, Strategy},
};
use crate::{error::ApiError, questdb::QuestDb, strategies::idk::night_drift::NightDrift};

#[derive(Clone, Copy, PartialEq)]
pub enum LiveSignal {
    Long,
    Short,
    Flat,
    Close,
}

impl LiveSignal {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Long => "long",
            Self::Short => "short",
            Self::Flat => "flat",
            Self::Close => "close",
        }
    }
}

pub struct LiveNightDrift {
    strategy: NightDrift,
    latest_vix: f64,
}

pub struct LiveBar {
    pub timestamp: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub vix: Option<f64>,
}

impl Default for LiveNightDrift {
    fn default() -> Self {
        Self {
            strategy: NightDrift::default(),
            latest_vix: 0.0,
        }
    }
}

impl LiveNightDrift {
    pub fn update(&mut self, bar: LiveBar) -> LiveSignal {
        if let Some(vix) = bar.vix.filter(|value| *value > 0.0) {
            self.latest_vix = vix;
        }
        let action = self.strategy.update(
            Bar {
                ts: bar.timestamp,
                open: bar.open,
                high: bar.high,
                low: bar.low,
                close: bar.close,
                volume: bar.volume,
                vix: self.latest_vix,
            },
            0.0,
        );

        match action {
            Action::Enter {
                side: Side::Long, ..
            } => LiveSignal::Long,
            Action::Enter {
                side: Side::Short, ..
            } => LiveSignal::Short,
            Action::Close { .. } => LiveSignal::Close,
            Action::Hold if self.strategy.in_position => LiveSignal::Long,
            Action::Hold => LiveSignal::Flat,
        }
    }

    pub fn volume(&self) -> f64 {
        0.01
    }

    pub fn update_tick(&mut self, price: f64, timestamp: i64) -> LiveSignal {
        let action = self.strategy.update_tick(price, timestamp);
        match action {
            Action::Enter { .. } => LiveSignal::Long,
            Action::Close { .. } => LiveSignal::Close,
            Action::Hold if self.strategy.in_position => LiveSignal::Long,
            Action::Hold => LiveSignal::Flat,
        }
    }
}

pub async fn warm_live_night_drift(questdb: &QuestDb) -> Result<LiveNightDrift, ApiError> {
    let latest_rows = questdb
        .csv("SELECT cast(max(timestamp) as long) ts FROM nq_1m")
        .await?;
    let latest_micros = latest_rows
        .first()
        .and_then(|row| row.get(0))
        .and_then(|value| value.parse::<i64>().ok())
        .ok_or_else(|| ApiError::QuestDb("nq_1m has no latest timestamp".into()))?;
    let from = format_day(latest_micros / 1_000_000 / 86_400 - 45);
    let sql = format!(
        concat!(
            "SELECT cast(n.timestamp as long) ts,",
            "n.timestamp,n.open,n.high,n.low,n.close,n.volume,v.close ",
            "FROM nq_1m n ASOF JOIN vix_1d v ",
            "WHERE n.timestamp >= '{from}' ORDER BY n.timestamp",
        ),
        from = from,
    );
    let rows = questdb.csv(&sql).await?;
    let mut live = LiveNightDrift::default();

    for row in rows {
        let bar = backtest_bar_from_csv(&row)?;
        if bar.vix > 0.0 {
            live.latest_vix = bar.vix;
        }
        let action = live.strategy.update(bar, 0.0);
        live.strategy.discard(action);
    }

    Ok(live)
}
