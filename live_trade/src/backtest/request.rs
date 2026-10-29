use crate::error::ApiError;
use serde::Deserialize;

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunRequest {
    /// The cost model to price the run under. Exness Pro is the only one, so
    /// this may be omitted; anything else is refused rather than ignored.
    #[serde(default)]
    pub model: Option<String>,
    pub strategy: String,
    pub symbol: String,
    pub instrument: String,
    pub initial_balance: String,
    pub from_date: String,
    pub to_date: String,
    /// RESEARCH ONLY: the day each sleeve's standalone admission account starts,
    /// when that is EARLIER than `from_date`. Python's canon windows replay trade
    /// logs generated from an earlier date (`fundednext_combined_strategies`'s
    /// 2026 window reads the 2024-2026 logs), so which trades EXIST was decided
    /// by a shadow account compounding since then. Omitted, it is `from_date`.
    #[serde(default)]
    pub admission_from: Option<String>,
    /// RESEARCH ONLY: the day the data is taken to END (exclusive), as
    /// Python's `CFD_OOS_END` cuts its series. A market whose data ends inside
    /// the run has no successor for its last bar (`ended_markets`). Omitted,
    /// the store's own last bar decides.
    #[serde(default)]
    pub data_end: Option<String>,
    /// RESEARCH ONLY: replay the FundedNext canon exactly as Python scored
    /// it -- the look-ahead day end and the end-of-data drop -- instead of the
    /// live rules every other run uses (scheduled last print, overdue close).
    #[serde(default)]
    pub canon_replay: bool,
    /// FundedNext whole-book runs: the `[from, to)` ISO-day windows the
    /// challenge Monte Carlo grid is reported over (`backtest::challenge`).
    /// Omitted, the run's own window.
    #[serde(default)]
    pub challenge_windows: Option<Vec<(String, String)>>,
    /// Paths per grid cell; omitted, `challenge::REPORT_PATHS`. Zero skips it.
    #[serde(default)]
    pub challenge_paths: Option<usize>,
    /// FundedNext: size every sleeve off `min(balance, cap)`. Omitted, a
    /// live-rules FundedNext run takes the challenge's own $6,000 (what the
    /// account trades and the challenge Monte Carlo assumes) and canon replay
    /// compounds uncapped, as Python's book replay does. 0 switches it off.
    #[serde(default)]
    pub sizing_cap: Option<f64>,
    /// RESEARCH ONLY: show every sleeve the initial balance throughout.
    #[serde(default)]
    pub constant_sizing: bool,
}
impl RunRequest {
    pub fn balance(&self) -> Result<f64, ApiError> {
        self.initial_balance
            .parse()
            .ok()
            .filter(|v: &f64| v.is_finite() && *v > 0.0)
            .ok_or_else(|| ApiError::BadRequest("invalid initial balance".into()))
    }
    /// The id of the cost model the run is priced under: Exness Pro when
    /// omitted, or FundedNext. Anything else is refused rather than ignored.
    pub fn model(&self) -> Result<&'static str, ApiError> {
        let Some(value) = self.model.as_deref().map(str::trim).filter(|v| !v.is_empty()) else {
            return Ok(super::MODEL_ID);
        };
        [super::MODEL_ID, super::FUNDEDNEXT_MODEL_ID]
            .into_iter()
            .find(|known| value.eq_ignore_ascii_case(known))
            .ok_or_else(|| {
                ApiError::BadRequest(format!(
                    "unknown model; the models are {} and {}",
                    super::MODEL_ID,
                    super::FUNDEDNEXT_MODEL_ID
                ))
            })
    }

    /// The book the run's model prices. Every strategy in the run must belong
    /// to it: an Exness sleeve priced at FundedNext, or the reverse, is a
    /// strategy measured under costs it was never sealed against.
    pub fn book(&self) -> Result<crate::strategies::cfd::Book, ApiError> {
        let model = self.model()?;
        super::model_book(model).ok_or_else(|| ApiError::BadRequest("unknown model".into()))
    }
}
