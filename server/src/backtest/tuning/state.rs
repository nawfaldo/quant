use serde_json::{Value, json};
use std::sync::{Arc, Mutex};

#[derive(Clone)]
pub struct TuneStore(Arc<Mutex<Value>>);

impl Default for TuneStore {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(
            json!({"status":"idle","progress":0,"total":0}),
        )))
    }
}

impl TuneStore {
    pub fn get(&self) -> Value {
        self.0.lock().expect("tune state poisoned").clone()
    }

    pub fn set(&self, value: Value) {
        *self.0.lock().expect("tune state poisoned") = value;
    }
}
