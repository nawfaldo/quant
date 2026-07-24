use serde::Serialize;

#[derive(Clone, Copy, Default)]
pub(crate) struct OrderFlowFeatures {
    pub(crate) midprice: f64,
    pub(crate) spread: f64,
    pub(crate) microprice: f64,
    pub(crate) top1_imbalance: f64,
    pub(crate) top5_imbalance: f64,
    pub(crate) top10_imbalance: f64,
    pub(crate) bid_add_volume: f64,
    pub(crate) bid_cancel_volume: f64,
    pub(crate) ask_add_volume: f64,
    pub(crate) ask_cancel_volume: f64,
    pub(crate) aggressive_buy_volume: f64,
    pub(crate) aggressive_sell_volume: f64,
    pub(crate) trade_delta: f64,
    pub(crate) price_change: f64,
    pub(crate) delta_per_tick: f64,
    pub(crate) executed_at_bid: f64,
    pub(crate) executed_at_ask: f64,
    pub(crate) bid_replenishment: f64,
    pub(crate) ask_replenishment: f64,
    pub(crate) replenishment_score: f64,
    pub(crate) bid_depth_distance: f64,
    pub(crate) ask_depth_distance: f64,
    pub(crate) depth_weighted_distance: f64,
    pub(crate) trade_count: u64,
    pub(crate) depth_event_count: u64,
    pub(crate) book_valid: bool,
}

#[derive(Clone, Copy)]
pub(crate) struct Bar {
    pub(crate) ts: i64,
    pub(crate) open: f64,
    pub(crate) high: f64,
    pub(crate) low: f64,
    pub(crate) close: f64,
    pub(crate) volume: f64,
    pub(crate) volume_delta: f64,
    pub(crate) depth_events: u64,
    pub(crate) vix: f64,
    pub(crate) order_flow: OrderFlowFeatures,
}
#[derive(Clone, Copy, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Long,
    Short,
}
#[derive(Clone, Serialize)]
pub struct Trade {
    pub side: Side,
    #[serde(rename = "et")]
    pub entry_timestamp: i64,
    #[serde(rename = "xt")]
    pub exit_timestamp: i64,
    #[serde(rename = "ep", serialize_with = "serialize_four_decimals")]
    pub entry_price: f64,
    #[serde(rename = "xp", serialize_with = "serialize_four_decimals")]
    pub exit_price: f64,
    #[serde(serialize_with = "serialize_four_decimals")]
    pub pnl: f64,
    #[serde(rename = "qty", serialize_with = "serialize_four_decimals")]
    pub quantity: f64,
    #[serde(skip_serializing)]
    pub entry_raw: f64,
    #[serde(skip_serializing)]
    pub exit_raw: f64,
}

fn serialize_four_decimals<S>(value: &f64, serializer: S) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    serializer.serialize_f64((value * 10_000.0).round() / 10_000.0)
}
#[derive(Clone, Copy)]
pub(crate) enum Instrument {
    Forex,
    Mini,
    Micro,
}
impl Instrument {
    pub(crate) fn parse(s: &str) -> Option<Self> {
        let s = s.to_ascii_lowercase();
        if s == "forex" {
            Some(Self::Forex)
        } else if s.contains("micro") {
            Some(Self::Micro)
        } else if s.contains("mini") || s == "futures" {
            Some(Self::Mini)
        } else {
            None
        }
    }
    pub(crate) fn name(self, symbol: &str) -> String {
        match self {
            Self::Forex => "forex".into(),
            Self::Mini => format!("{symbol} mini"),
            Self::Micro => format!("{symbol} micro"),
        }
    }
    pub(crate) fn point_value(self, symbol: &str) -> f64 {
        match self {
            Self::Forex => 1.0,
            Self::Mini => {
                if symbol == "es" {
                    50.0
                } else {
                    20.0
                }
            }
            Self::Micro => {
                if symbol == "es" {
                    5.0
                } else {
                    2.0
                }
            }
        }
    }
    pub(crate) fn quantity_step(self) -> f64 {
        match self {
            Self::Forex => 0.01,
            Self::Mini | Self::Micro => 1.0,
        }
    }
    pub(crate) fn size(self, raw: f64) -> f64 {
        match self {
            Self::Forex => (raw / 0.01).round().max(1.0) * 0.01,
            _ => raw.round().max(1.0),
        }
    }

    pub(crate) fn risk_size(self, raw: f64) -> Option<f64> {
        let size = match self {
            Self::Forex => (raw / 0.01).floor() * 0.01,
            Self::Mini | Self::Micro => raw.floor(),
        };
        let minimum = match self {
            Self::Forex => 0.01,
            Self::Mini | Self::Micro => 1.0,
        };
        (size >= minimum).then_some(size)
    }
}

#[derive(Clone, Copy)]
pub(crate) enum Action {
    Hold,
    Enter {
        side: Side,
        price: f64,
        quantity: f64,
    },
    Close {
        price: f64,
        fraction: f64,
    },
    EnterPosition {
        id: u64,
        side: Side,
        price: f64,
        quantity: f64,
    },
    ClosePosition {
        id: u64,
        price: f64,
    },
}

pub(crate) trait Strategy {
    fn update(&mut self, bar: Bar, equity: f64) -> Action;
    fn update_all(&mut self, bar: Bar, equity: f64) -> Vec<Action> {
        vec![self.update(bar, equity)]
    }
    fn discard(&mut self, _action: Action) {}
    fn discard_all(&mut self, actions: Vec<Action>) {
        for action in actions {
            self.discard(action);
        }
    }
    fn session_end_minute(&self) -> Option<usize> {
        None
    }
    fn entry_risk_fraction(&self) -> Option<f64> {
        None
    }
    fn entry_stop_price(&self) -> Option<f64> {
        None
    }
    fn max_drawdown_dollars(&self) -> Option<f64> {
        None
    }
    fn monte_carlo_drawdown_ruin_dollars(&self) -> Option<f64> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trade_uses_the_existing_wire_field_names() {
        let trade = Trade {
            side: Side::Long,
            entry_timestamp: 1,
            exit_timestamp: 2,
            entry_price: 100.0,
            exit_price: 101.0,
            pnl: 1.0,
            quantity: 0.5,
            entry_raw: 100.0,
            exit_raw: 101.0,
        };

        let value = serde_json::to_value(trade).unwrap();
        assert_eq!(value["et"], 1);
        assert_eq!(value["xt"], 2);
        assert_eq!(value["ep"], 100.0);
        assert_eq!(value["xp"], 101.0);
        assert_eq!(value["qty"], 0.5);
        assert!(value.get("entry_timestamp").is_none());
    }

    #[test]
    fn forex_risk_size_supports_centilots_without_rounding_up() {
        assert_eq!(Instrument::Forex.risk_size(0.509), Some(0.5));
        assert_eq!(Instrument::Forex.risk_size(0.009), None);
        assert_eq!(Instrument::Mini.risk_size(0.99), None);
    }
}
