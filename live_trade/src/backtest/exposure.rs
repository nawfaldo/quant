//! Per-sleeve exposure schedules for a shared-account run.
//!
//! A combined run sizes every slot off one balance, so the book carries the sum
//! of its sleeves' risk budgets. Deciding whether a *time-varying* budget --
//! stand down in one regime, size up in another -- is worth anything means
//! running the whole book through the engine with that budget applied, because
//! the quantity that matters is the mark-to-market drawdown of the shared
//! account, and no per-sleeve replay produces it.
//!
//! This is the mechanism that makes such a run possible without a rebuild per
//! candidate. `SLEEVE_EXPOSURE_SCHEDULE` names a JSON file:
//!
//! ```json
//! {
//!   "NQ Deep OFI Momentum":     {"default": 1.0, "2025-05-01": 0.25, "2025-07-01": 1.0},
//!   "NQ Hourly Delta Reversal": {"default": 0.5},
//!   "BTC Maroy Ladder":         {"default": 1.0}
//! }
//! ```
//!
//! Each sleeve maps to a step function over calendar days: the factor in force
//! on a day is the one attached to the latest date key at or before it, or
//! `default` (itself defaulting to 1.0) before the first key. Dates are the
//! run's own day convention -- New York wall clock, the same string
//! `format_ts` prints -- so a schedule fitted from a report lines up with it.
//!
//! HOW THE FACTOR IS APPLIED, and why it is applied there. Every `idk` sleeve
//! sizes as `floor(min(equity * risk / stop, equity / margin / price) * lev /
//! step) * step`. Both legs inside the `min` are linear in equity, so handing a
//! slot `equity * m` instead of `equity` scales its quantity by exactly `m` --
//! including the margin cap, which a multiplier applied to the finished
//! quantity would wrongly leave in place. It also keeps every equity-derived
//! quantity a strategy computes internally consistent with the size it took:
//! BTC Maroy Ladder's risk unit is `0.02 * equity / quantity`, which is
//! unchanged when both scale together and would be silently distorted if only
//! one did.
//!
//! The file is re-read once per run, not once per process, so a sweep can
//! rewrite it between candidates and keep one warm server rather than paying a
//! process start and a fresh QuestDB load per cell. Two runs must not share a
//! schedule file concurrently; a research driver is sequential and does not.
//!
//! Unset in production, where every sleeve runs at 1.0 and this costs one
//! environment lookup per run.

use std::collections::BTreeMap;

/// One sleeve's step function over days, plus the factor in force before the
/// first key.
#[derive(Debug, Default)]
struct Steps {
    default: f64,
    points: BTreeMap<String, f64>,
    /// Optional run-start eligibility. Unlike `min_balance`, failing this gate
    /// is permanent for the run: compounding above it later does not enable a
    /// sleeve whose remaining signal window was never validated for a late
    /// start.
    min_initial_balance: f64,
    /// Balance below which this sleeve stands down entirely. See
    /// `Schedule::factor` for why an all-or-nothing gate is the right shape.
    min_balance: f64,
    /// Balance at which a stood-down sleeve resumes. Always >= `min_balance`;
    /// the band between them is hysteresis, so a book hovering on the threshold
    /// does not switch the sleeve on and off day after day.
    resume_balance: f64,
    /// Upper bound on the equity shown to this strategy for position sizing.
    /// Account P&L and drawdown remain shared and uncapped.
    max_sizing_balance: f64,
}

impl Steps {
    fn factor(&self, day: &str) -> f64 {
        self.points
            .range(..=day.to_owned())
            .next_back()
            .map(|(_, value)| *value)
            .unwrap_or(self.default)
    }
}

#[derive(Debug, Default)]
pub(crate) struct Schedule {
    sleeves: BTreeMap<String, Steps>,
}

impl Schedule {
    /// The exposure multiplier for `strategy` on `day` (`YYYY-MM-DD`).
    ///
    /// A sleeve the schedule does not mention runs untouched at 1.0, so a file
    /// may name only the sleeves it wants to move.
    #[allow(dead_code)]
    pub(crate) fn factor(&self, strategy: &str, day: &str) -> f64 {
        self.sleeves
            .get(strategy)
            .map(|steps| steps.factor(day))
            .unwrap_or(1.0)
    }

    /// Whether this run's starting balance is eligible to trade `strategy`.
    pub(crate) fn initial_balance_allows(&self, strategy: &str, initial: f64) -> bool {
        self.sleeves
            .get(strategy)
            .is_none_or(|steps| initial >= steps.min_initial_balance)
    }

    /// Shared equity, optionally capped only for this sleeve's sizing decision.
    pub(crate) fn sizing_equity(&self, strategy: &str, equity: f64) -> f64 {
        self.sleeves.get(strategy).map_or(equity, |steps| {
            if steps.max_sizing_balance > 0.0 {
                equity.min(steps.max_sizing_balance)
            } else {
                equity
            }
        })
    }

    /// The multiplier for `strategy`, or 0.0 if `equity` cannot support it.
    ///
    /// WHY AN ALL-OR-NOTHING GATE. Without one, an under-funded sleeve does not
    /// stop trading -- it trades a *biased subset*. Quantity floors to the lot
    /// step, so the sleeve fills only on the days its size happens to clear the
    /// minimum, which is exactly the low-volatility days the throttle was
    /// sizing up into. Measured: BTC Maroy Ladder on a 1,000 dollar account at
    /// exposure target 13 took 73 of the 246 trades it wanted, and those 73 were
    /// not a random 30% -- one BTC lot is ~900 dollars, so it filled only when
    /// the multiplier was high. That is worse than being switched off, because
    /// the surviving sample looks like a working strategy with a thin trade
    /// count rather than an unfundable one.
    ///
    /// `was_on` carries the previous state so the gate has hysteresis: a sleeve
    /// stands down below `min_balance` and only resumes above `resume_balance`.
    /// Equal thresholds mean no hysteresis, which is the default.
    pub(crate) fn gated_factor(&self, strategy: &str, day: &str, equity: f64, was_on: bool) -> f64 {
        let Some(steps) = self.sleeves.get(strategy) else {
            return 1.0;
        };
        if steps.min_balance <= 0.0 {
            return steps.factor(day);
        }
        let on = if was_on {
            equity >= steps.min_balance
        } else {
            equity >= steps.resume_balance
        };
        if on { steps.factor(day) } else { 0.0 }
    }

    /// Whether `strategy` is fundable at `equity`, for the caller's state.
    pub(crate) fn is_on(&self, strategy: &str, equity: f64, was_on: bool) -> bool {
        let Some(steps) = self.sleeves.get(strategy) else {
            return true;
        };
        if steps.min_balance <= 0.0 {
            return true;
        }
        if was_on {
            equity >= steps.min_balance
        } else {
            equity >= steps.resume_balance
        }
    }

    fn parse(text: &str) -> Result<Self, String> {
        let raw: BTreeMap<String, BTreeMap<String, f64>> =
            serde_json::from_str(text).map_err(|error| error.to_string())?;
        let mut sleeves = BTreeMap::new();
        for (name, entries) in raw {
            let mut steps = Steps {
                default: 1.0,
                points: BTreeMap::new(),
                min_initial_balance: 0.0,
                min_balance: 0.0,
                resume_balance: 0.0,
                max_sizing_balance: 0.0,
            };
            for (key, value) in entries {
                if !value.is_finite() || value < 0.0 {
                    return Err(format!("{name}: factor {value} for {key} is not a size"));
                }
                if key == "default" {
                    steps.default = value;
                } else if key == "min_initial_balance" {
                    steps.min_initial_balance = value;
                } else if key == "min_balance" {
                    steps.min_balance = value;
                } else if key == "resume_balance" {
                    steps.resume_balance = value;
                } else if key == "max_sizing_balance" {
                    steps.max_sizing_balance = value;
                } else if key.len() == 10 && key.as_bytes()[4] == b'-' && key.as_bytes()[7] == b'-'
                {
                    steps.points.insert(key, value);
                } else {
                    return Err(format!(
                        "{name}: {key:?} is not \"default\", \
                         \"min_initial_balance\", \"min_balance\", \
                         \"resume_balance\", \"max_sizing_balance\" or YYYY-MM-DD"
                    ));
                }
            }
            // An unset or too-low resume threshold means no hysteresis rather
            // than a sleeve that can switch off and never come back.
            steps.resume_balance = steps.resume_balance.max(steps.min_balance);
            sleeves.insert(name, steps);
        }
        Ok(Self { sleeves })
    }
}

/// This run's schedule, or `None` when `SLEEVE_EXPOSURE_SCHEDULE` is unset --
/// the production path, where every sleeve runs at its compiled size.
///
/// A malformed file is a loud no-op rather than a silent one: it logs and the
/// run proceeds at 1.0, because a research sweep that quietly ignored its own
/// schedule would report the control's numbers under the candidate's name.
pub(crate) fn schedule() -> Option<Schedule> {
    let path = std::env::var("SLEEVE_EXPOSURE_SCHEDULE").ok()?;
    match std::fs::read_to_string(&path)
        .map_err(|error| error.to_string())
        .and_then(|text| Schedule::parse(&text))
    {
        Ok(parsed) => Some(parsed),
        Err(error) => {
            eprintln!("SLEEVE_EXPOSURE_SCHEDULE {path}: {error}; running at 1.0");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> Schedule {
        Schedule::parse(text).expect("valid schedule")
    }

    #[test]
    fn unnamed_sleeve_runs_untouched() {
        let schedule = parse(r#"{"A": {"default": 0.5}}"#);
        assert_eq!(schedule.factor("B", "2025-05-01"), 1.0);
    }

    #[test]
    fn default_holds_before_the_first_key() {
        let schedule = parse(r#"{"A": {"default": 0.25, "2025-05-01": 1.0}}"#);
        assert_eq!(schedule.factor("A", "2025-04-30"), 0.25);
        assert_eq!(schedule.factor("A", "2025-05-01"), 1.0);
        assert_eq!(schedule.factor("A", "2026-01-01"), 1.0);
    }

    #[test]
    fn a_key_holds_until_the_next_one() {
        let schedule = parse(r#"{"A": {"2025-05-01": 0.5, "2025-07-01": 0.0}}"#);
        assert_eq!(schedule.factor("A", "2025-06-30"), 0.5);
        assert_eq!(schedule.factor("A", "2025-07-01"), 0.0);
        assert_eq!(schedule.factor("A", "2025-12-31"), 0.0);
    }

    #[test]
    fn absent_default_is_full_size() {
        let schedule = parse(r#"{"A": {"2025-05-01": 0.5}}"#);
        assert_eq!(schedule.factor("A", "2025-01-01"), 1.0);
    }

    #[test]
    fn a_negative_factor_is_rejected() {
        assert!(Schedule::parse(r#"{"A": {"default": -1.0}}"#).is_err());
    }

    #[test]
    fn a_stray_key_is_rejected() {
        assert!(Schedule::parse(r#"{"A": {"2025-5-1": 0.5}}"#).is_err());
    }

    /// A sleeve with no `min_balance` must behave exactly as before, at any
    /// balance. This is the regression that protects every existing run.
    #[test]
    fn an_ungated_sleeve_is_never_stood_down() {
        let s = Schedule::parse(r#"{"A": {"default": 0.5}}"#).unwrap();
        assert_eq!(s.gated_factor("A", "2025-06-01", 1.0, true), 0.5);
        assert_eq!(s.gated_factor("A", "2025-06-01", 0.0, false), 0.5);
        assert!(s.is_on("A", 0.0, false));
        // A sleeve the schedule does not mention is untouched at 1.0.
        assert_eq!(s.gated_factor("B", "2025-06-01", 0.0, false), 1.0);
        assert!(s.is_on("B", 0.0, false));
    }

    /// Below the floor the sleeve sizes to zero -- all of its signals, not the
    /// subset that happens to clear the lot step.
    #[test]
    fn a_gated_sleeve_stands_down_below_its_floor() {
        let s = Schedule::parse(r#"{"A": {"default": 0.8, "min_balance": 3000}}"#).unwrap();
        assert_eq!(s.gated_factor("A", "2025-06-01", 2999.0, true), 0.0);
        assert_eq!(s.gated_factor("A", "2025-06-01", 3000.0, true), 0.8);
    }

    /// With a resume band, a sleeve that has stood down needs the higher
    /// threshold to come back, so a balance oscillating around the floor does
    /// not switch it on and off every day.
    #[test]
    fn hysteresis_needs_the_resume_level_to_restart() {
        let s = Schedule::parse(
            r#"{"A": {"default": 1.0, "min_balance": 3000, "resume_balance": 3500}}"#,
        )
        .unwrap();
        // Running, dips to 3200: still above the floor, stays on.
        assert!(s.is_on("A", 3200.0, true));
        // Drops through the floor: off.
        assert!(!s.is_on("A", 2900.0, true));
        // Recovers to 3200 -- above the floor but below resume, stays off.
        assert!(!s.is_on("A", 3200.0, false));
        // Clears resume: back on.
        assert!(s.is_on("A", 3500.0, false));
    }

    /// A resume level below the floor would let a sleeve switch off and never
    /// return; it is clamped up to the floor instead.
    #[test]
    fn a_resume_below_the_floor_is_clamped() {
        let s = Schedule::parse(r#"{"A": {"min_balance": 3000, "resume_balance": 100}}"#).unwrap();
        assert!(!s.is_on("A", 2999.0, false));
        assert!(s.is_on("A", 3000.0, false));
    }

    #[test]
    fn initial_balance_gate_does_not_unlock_after_late_compounding() {
        let s = Schedule::parse(
            r#"{"A": {"min_initial_balance": 3000, "min_balance": 2700, "resume_balance": 3000}}"#,
        )
        .unwrap();
        assert!(!s.initial_balance_allows("A", 1500.0));
        assert!(s.initial_balance_allows("A", 3000.0));
        assert!(s.initial_balance_allows("B", 1.0));
    }

    #[test]
    fn sizing_balance_cap_leaves_real_equity_and_other_sleeves_alone() {
        let s = Schedule::parse(r#"{"A": {"max_sizing_balance": 400}}"#).unwrap();
        assert_eq!(s.sizing_equity("A", 300.0), 300.0);
        assert_eq!(s.sizing_equity("A", 1_000.0), 400.0);
        assert_eq!(s.sizing_equity("B", 1_000.0), 1_000.0);
    }
}
