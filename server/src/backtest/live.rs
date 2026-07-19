use crate::{error::ApiError, questdb::QuestDb};

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

pub struct LiveNightDrift;

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
        Self
    }
}

impl LiveNightDrift {
    pub fn update(&mut self, bar: LiveBar) -> LiveSignal {
        let _ = bar;
        LiveSignal::Flat
    }

    pub fn volume(&self) -> f64 {
        0.01
    }

    pub fn update_tick(&mut self, price: f64, timestamp: i64) -> LiveSignal {
        let _ = (price, timestamp);
        LiveSignal::Flat
    }
}

pub async fn warm_live_night_drift(questdb: &QuestDb) -> Result<LiveNightDrift, ApiError> {
    let _ = questdb;
    Ok(LiveNightDrift)
}
