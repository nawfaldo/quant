use crate::backtest::{LiveBar, LiveNightDrift, LiveSignal};
use std::sync::{Arc, Mutex};

#[derive(Clone, Default)]
pub struct MarchStore(Arc<Mutex<Option<MarchState>>>);

struct MarchState {
    strategy: LiveNightDrift,
    previous: LiveSignal,
}

pub struct MarchDecision {
    pub signal: LiveSignal,
    pub previous: LiveSignal,
    pub volume: f64,
}

impl MarchStore {
    pub fn activate(&self, strategy: LiveNightDrift) {
        *self.0.lock().expect("March state poisoned") = Some(MarchState {
            strategy,
            previous: LiveSignal::Flat,
        });
    }

    pub fn deactivate(&self) {
        *self.0.lock().expect("March state poisoned") = None;
    }

    pub fn update(&self, bar: LiveBar) -> Option<MarchDecision> {
        let mut guard = self.0.lock().expect("March state poisoned");
        let state = guard.as_mut()?;
        let previous = state.previous;
        let signal = state.strategy.update(bar);
        state.previous = signal;

        Some(MarchDecision {
            signal,
            previous,
            volume: state.strategy.volume(),
        })
    }

    pub fn update_tick(&self, price: f64, timestamp: i64) -> Option<MarchDecision> {
        let mut guard = self.0.lock().expect("March state poisoned");
        let state = guard.as_mut()?;
        let previous = state.previous;
        let signal = state.strategy.update_tick(price, timestamp);
        state.previous = signal;

        Some(MarchDecision {
            signal,
            previous,
            volume: state.strategy.volume(),
        })
    }
}
