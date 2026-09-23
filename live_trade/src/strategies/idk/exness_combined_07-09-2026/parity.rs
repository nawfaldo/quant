//! Trade-for-trade agreement with `exness_families.backtest`.
//!
//! The unit tests in `tests.rs` pin structure -- that the sessions are right, that the
//! channels exclude the current bar, that the sizing refuses where the
//! broker would. None of them can show that this streaming engine produces
//! the SAME TRADES as the array-at-a-time Python it reimplements, and that is
//! the only property that matters: a sealed cell whose port fires on
//! different bars is a different strategy wearing a validated name.
//!
//! Each fixture is written by `_export_parity.py` and holds the
//! session-filtered 30-minute bars `context` builds, plus every trade the
//! cell produced over them. The bars are fed in as-is rather than
//! re-aggregated from one-minute data, because aggregation is a separate
//! question and mixing the two would make a failure ambiguous.
//!
//! EVERY SLEEVE IN THE BOOK HAS ONE, and
//! `every_sleeve_is_covered_by_a_parity_fixture` is what keeps it that way.
//! Since 2026-09-04 that is the whole book rather than most of it: the two
//! hand-written imports, which were not `exness_families` cells and had no
//! sealed row to reproduce, left with NQ. A few are worth naming for what they
//! cover that nothing else does:
//!
//! ```text
//! jp225:break_retest    the SHIFTED CLOCK -- a session straddling New York
//!                       midnight -- and a lookback cut at the day boundary
//! jp225:vol_regime      a `time_N` exit, counted in SESSION-FILTERED bars, so
//!                       an overnight gap costs the position nothing
//! usdjpy:kendall        a 270-bar pairwise statistic maintained by SLIDING,
//!                       so a drifting accumulator would go wrong slowly
//! jp225:kalman          a covariance carried bar to bar with no window to
//!                       fall out of -- the deepest recursion here
//! usdjpy:fracdiff       a 64-tap power-law kernel, and the INVERTED polarity
//! eurjpy:two_stage      an INCLUSIVE rolling minimum walked backwards
//! ukoil:xma_cross       a TEMA cross, the only trigger reading two bars
//! ```
//!
//! SIZING IS TWO STAGES AND ONLY THE SECOND IS GIVEN AN ENORMOUS BALANCE.
//!
//! Which trades EXIST is decided on a SHADOW account seeded at
//! `ADMISSION_BALANCE * shown_equity`: `ef.quantity` returns 0 for any order
//! under the broker's `volume_min` and the trade then never enters the log at
//! all. That refusal is a real part of what the sealed cell is, so
//! `_export_parity.py` runs Python at the same balance and it is NOT raised
//! here.
//!
//! What IS raised is the BOOK equity handed to `update_all`, which drives the
//! second stage only. Leaving that at $400 would make a test about the SIGNAL
//! fail on the shared balance instead.

use super::*;
use serde_json::Value;

/// An account large enough that no entry is ever refused, so a difference in
/// the trade lists can only be a difference in the SIGNAL.
const UNLIMITED: f64 = 1e9;

struct Fixture {
    sleeve: Sleeve,
    shift_seconds: i64,
    bars: Vec<Bar>,
    trades: Vec<Value>,
    /// The window Python was asked for. The bars reach a year further back,
    /// because a streaming engine has to walk its own warm-up.
    from: i64,
    to: i64,
    /// Carried for diagnosis rather than asserted on: `fills` trails
    /// `signals` whenever a signal lands on the last bar of a session, which
    /// both engines drop, so the counters are not a parity statement.
    #[allow(dead_code)]
    signals: usize,
    #[allow(dead_code)]
    fills: usize,
}

fn load(sleeve: Sleeve, raw: &str) -> Fixture {
    let payload: Value = serde_json::from_str(raw).expect("fixture parses");
    let shift_seconds = payload["shift_hours"].as_i64().unwrap() * 3_600;
    assert_eq!(
        payload["symbol"].as_str().unwrap(),
        sleeve.market(),
        "fixture is for a different market"
    );
    assert_eq!(
        payload["per_session"].as_u64().unwrap() as usize,
        sleeve.contract().per_session,
        "fixture disagrees with the frozen bucket count"
    );

    let bars = payload["bars"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            let row = row.as_array().unwrap();
            let number = |index: usize| row[index].as_f64().unwrap();
            Bar {
                // The fixture carries the SHIFTED timestamp, because
                // `all_bars` shifts as it loads. `update_all` applies the
                // shift itself, so it has to come back off here or JP225
                // would be shifted twice.
                ts: row[0].as_i64().unwrap() - shift_seconds,
                open: number(1),
                high: number(2),
                low: number(3),
                close: number(4),
                volume: number(5),
                volume_delta: 0.0,
                depth_events: 0,
                level_two: false,
                order_flow: Default::default(),
                // The exporter still writes a seventh BENCHMARK column for a
                // cell that reads a second market. No member does since
                // 2026-09-04, so it is dropped here rather than plumbed to a
                // `Candle` field nothing would read.
                benchmark: None,
            }
        })
        .collect();

    Fixture {
        sleeve,
        shift_seconds,
        bars,
        trades: payload["trades"].as_array().unwrap().clone(),
        from: payload["from"].as_i64().unwrap(),
        to: payload["to"].as_i64().unwrap(),
        signals: payload["signals"].as_u64().unwrap() as usize,
        fills: payload["fills"].as_u64().unwrap() as usize,
    }
}

/// One entry the Rust engine produced, in the fixture's own coordinates.
#[derive(Debug, PartialEq)]
struct Entry {
    /// SHIFTED, to match what Python recorded.
    ts: i64,
    /// `1` long, `-1` short, matching the fixture's encoding.
    side: i64,
    price: f64,
    /// `stop_day * daily_risk`, taken from the stop the engine set.
    distance: f64,
}

/// Replays the fixture's bars and returns the closed trades whose ENTRY
/// falls inside the fixture's window.
///
/// The bars start a year before that window because the indicators are
/// streaming and cannot be seeded -- a hundred sessions of `calm` history
/// have to be walked rather than handed over. The engine trades through that
/// warm-up like any other period, and those trades are not part of what
/// Python was asked for, so they are dropped here rather than compared
/// against nothing.
fn replay(fixture: &Fixture) -> Vec<(Entry, i64)> {
    // A candle is only evaluated when the bar that STARTS the next slot
    // arrives, so the last real candle would never be seen. One synthetic
    // bar in the following slot flushes it; the synthetic bar itself is left
    // half-built and never evaluated, so it cannot add a trade of its own.
    let flush = fixture.bars.last().map(|last| Bar {
        ts: last.ts + BAR_SECONDS,
        ..*last
    });
    let stream: Vec<Bar> = fixture.bars.iter().chain(flush.iter()).copied().collect();
    replay_stream(fixture, &stream)
}

/// The fixture's candles cut into the one-minute bars the LIVE runtime feeds.
///
/// The fixtures are 30-minute bars, which is the candle itself, so they leave
/// `update_all`'s early close untested -- it is guarded off any source whose bar
/// IS the slot. Live, every one of these markets arrives as `<symbol>_1m`, so
/// the untested path is the only one that ever runs.
///
/// Three minutes reproduce a candle exactly under the engine's own aggregation
/// (first open, running max/min, last close, summed volume): the opening minute
/// carries the open and all the volume, the second carries both extremes, and
/// the slot's FINAL minute carries the close. The gap between the first two is
/// one minute, which is what teaches `source_step` before the third arrives.
fn minute_stream(fixture: &Fixture) -> Vec<Bar> {
    let mut out = Vec::with_capacity(fixture.bars.len() * 3);
    for bar in &fixture.bars {
        out.push(Bar {
            high: bar.open,
            low: bar.open,
            close: bar.open,
            ..*bar
        });
        out.push(Bar {
            ts: bar.ts + 60,
            close: bar.open,
            volume: 0.0,
            ..*bar
        });
        out.push(Bar {
            ts: bar.ts + BAR_SECONDS - 60,
            open: bar.close,
            high: bar.close,
            low: bar.close,
            volume: 0.0,
            ..*bar
        });
    }
    out
}

fn replay_stream(fixture: &Fixture, bars: &[Bar]) -> Vec<(Entry, i64)> {
    let mut engine = FamilyEngine::new(fixture.sleeve, 0.01);
    let mut closed: Vec<(Entry, i64)> = Vec::new();
    let mut open: Option<Entry> = None;
    let mut started = false;
    for bar in bars.iter() {
        // The engine calls this once, on the first bar of the reported
        // window; the preroll before it only warms indicators. Without it
        // the shadow account would carry a year of warm-up P&L into the
        // admission test.
        if !started && bar.ts + fixture.shift_seconds >= fixture.from {
            started = true;
            // `reset_trading_state` DROPS an open position rather than
            // closing it -- the standalone run simply begins here with a
            // flat book -- so anything the preroll was holding leaves with
            // it and is not a trade in this window.
            engine.reset_trading_state();
            open = None;
        }
        let actions = engine.update_all(*bar, UNLIMITED);
        if actions.is_empty() {
            continue;
        }
        // `action_timestamp` reports the CANDLE the decision belongs to, not
        // the bar that flushed it, and it converts the shifted clock back to
        // real New York on the way out.
        let at = engine.action_timestamp(bar.ts) + fixture.shift_seconds;
        for action in actions {
            match action {
                Action::Enter { side, price, .. } => {
                    // Read off the position rather than through
                    // `entry_stop_price`, which this strategy deliberately
                    // does not implement: exposing it tells the engine to
                    // size the entry itself and throws the sleeve's own
                    // quantity away.
                    let stop = engine
                        .position
                        .as_ref()
                        .expect("an open position has a stop")
                        .stop;
                    assert!(open.is_none(), "two entries without a close between");
                    open = Some(Entry {
                        ts: at,
                        side: match side {
                            Side::Long => 1,
                            Side::Short => -1,
                        },
                        price,
                        distance: (price - stop).abs(),
                    });
                }
                Action::Close { .. } => {
                    let entry = open.take().expect("a close without an open position");
                    closed.push((entry, at));
                }
                _ => {}
            }
        }
    }
    closed
        .into_iter()
        .filter(|(entry, _)| fixture.from <= entry.ts && entry.ts < fixture.to)
        .collect()
}

fn expected(trade: &Value) -> Entry {
    Entry {
        ts: trade["entry_ts"].as_i64().unwrap(),
        side: trade["side"].as_i64().unwrap(),
        price: trade["entry"].as_f64().unwrap(),
        distance: trade["distance"].as_f64().unwrap(),
    }
}

/// Prices come off the same bars, so they should agree to the bit; the
/// distance is a fourteen-day mean of true ranges accumulated in a different
/// order, so it is compared at a tolerance that is still far tighter than a
/// tick on any of these markets.
fn agrees(got: &Entry, want: &Entry) -> bool {
    got.ts == want.ts
        && got.side == want.side
        && (got.price - want.price).abs() < 1e-9
        && (got.distance - want.distance).abs() < 1e-6 * want.distance.max(1.0)
}

fn assert_reproduces(fixture: &Fixture) {
    let replayed = replay(fixture);
    assert!(
        !fixture.trades.is_empty(),
        "the fixture recorded no trades to compare against"
    );

    // Every trade the fixture recorded must appear, in order, with the same
    // entry bar, the same side, the same fill price, the same stop distance
    // AND the same exit bar. Entering correctly and trailing differently is
    // still a different strategy.
    let mut cursor = 0usize;
    for trade in &fixture.trades {
        let want = expected(trade);
        let want_exit = trade["exit_ts"].as_i64().unwrap();
        let found = replayed[cursor..]
            .iter()
            .position(|(got, exit)| agrees(got, &want) && *exit == want_exit)
            .unwrap_or_else(|| {
                panic!(
                    "{}: no matching trade for {want:?} exiting at {want_exit}; \
                     the replay had {:?}",
                    fixture.sleeve.display(),
                    (
                        replayed.len(),
                        fixture.trades.len(),
                        replayed
                            .iter()
                            .filter(|(got, _)| (got.ts - want.ts).abs() < 3 * 86_400)
                            .collect::<Vec<_>>()
                    )
                )
            });
        cursor += found + 1;
    }

    // And the replay must not have invented any. Both sides are run at an
    // account that can afford everything, so every signal became a trade on
    // both and the two lists are the same length -- a subset check would let
    // an extra entry through unnoticed.
    // `fills` can trail `signals` for a reason that is NOT sizing: a signal
    // read on the last bar of a session is dropped rather than filled at
    // tomorrow's open, and both engines drop it. So the trade lists are
    // compared, not the counters.
    assert_eq!(
        replayed.len(),
        fixture.trades.len(),
        "{}: the replay produced a different number of trades",
        fixture.sleeve.display()
    );
}

/// A Donchian break, a VWAP retest, and the SHIFTED CLOCK. If the six-hour
/// shift were applied twice, or not at all, every day boundary would move --
/// and this family cuts its lookback at the day boundary, so nothing would
/// line up.
#[test]
fn jp225_break_retest_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225BreakRetest,
        include_str!("../fixtures/parity_jp225_break_retest.json"),
    );
    assert_eq!(fixture.shift_seconds, 6 * 3_600);
    assert_reproduces(&fixture);
}

/// A Bollinger squeeze arming a later break. The coil is found by walking
/// BACKWARDS over an inclusive rolling minimum, which is the one place in the
/// book where an exclusive channel would silently arm on the wrong bars.
#[test]
fn eurjpy_two_stage_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::EurjpyTwoStage,
        include_str!("../fixtures/parity_eurjpy_two_stage.json"),
    );
    assert_reproduces(&fixture);
}

/// A retracement measured in DAILY RANGE inside a trend, which is the only
/// cell whose trend anchor is its own axis rather than the shared `trend`
/// gate. A port that reused `trend` here would read `none` and fire on both
/// sides of the average.
#[test]
fn usdjpy_pullback_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::UsdjpyPullback,
        include_str!("../fixtures/parity_usdjpy_pullback.json"),
    );
    assert_reproduces(&fixture);
}

/// The same family on crypto at the FASTER trend reference, which is what makes
/// the pair two cells rather than one rule on two markets.
#[test]
fn ethusd_pullback_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::EthusdPullback,
        include_str!("../fixtures/parity_ethusd_pullback.json"),
    );
    assert_reproduces(&fixture);
}

/// An OBV line read INCLUSIVE against a channel on itself read EXCLUSIVE. The
/// two are advanced in different places for that reason, and swapping them
/// agrees on most bars and is wrong on the ones the family trades.
#[test]
fn ethusd_obv_break_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::EthusdObvBreak,
        include_str!("../fixtures/parity_ethusd_obv_break.json"),
    );
    assert_reproduces(&fixture);
}

/// THE SAME TWO SERIES AS `obv_break` PLUS A PRICE CHANNEL, and the INVERTED
/// polarity: a `follow` cell here takes the opposite of the raw side. A port
/// that routed this through `Direction::apply` would produce a full, plausible
/// trade log on the wrong side of every trade.
#[test]
fn jp225_obv_divergence_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225ObvDivergence,
        include_str!("../fixtures/parity_jp225_obv_divergence.json"),
    );
    assert_reproduces(&fixture);
}

/// A TEMA cross, which is the only trigger in the book that reads TWO bars of
/// its own indicator -- the sign of `fast - slow` now and at the previous
/// candle. A port holding only the newest value cannot express it.
#[test]
fn ukoil_xma_cross_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::UkoilXmaCross,
        include_str!("../fixtures/parity_ukoil_xma_cross.json"),
    );
    assert_reproduces(&fixture);
}

/// Yesterday's range against the session VWAP, FOLLOWED. It is one of the two
/// cells that read `PriorDay`, and the only one that reads its RANGE rather
/// than its pivots.
#[test]
fn ukoil_level_confluence_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::UkoilLevelConfluence,
        include_str!("../fixtures/parity_ukoil_level_confluence.json"),
    );
    assert_reproduces(&fixture);
}

/// A thrust bar on twice the 20-session average volume. Volume is the one
/// column the aggregation SUMS rather than takes an extreme of, so a candle
/// builder that dropped a minute would fail here and nowhere else.
#[test]
fn usdjpy_volume_thrust_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::UsdjpyVolumeThrust,
        include_str!("../fixtures/parity_usdjpy_volume_thrust.json"),
    );
    assert_reproduces(&fixture);
}

/// The same family on an index at a 20x shorter volume window and an `rr_2`
/// exit, which is what stops the pair being one cell counted twice.
#[test]
fn jp225_volume_thrust_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225VolumeThrust,
        include_str!("../fixtures/parity_jp225_volume_thrust.json"),
    );
    assert_reproduces(&fixture);
}

/// A rolling mean and sigma over 5 sessions of closes, FADED. `MeanSigma`
/// refuses until it holds a full window, unlike the channels, and this is the
/// cell that would trade a hundred bars early if it did not.
#[test]
fn audusd_zscore_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::AudusdZscore,
        include_str!("../fixtures/parity_audusd_zscore.json"),
    );
    assert_reproduces(&fixture);
}

/// FIVE VOTES, THREE OF WHICH HAVE TO AGREE, and each is a different reading of
/// the same candle. It is the only cell here whose signal is a count rather
/// than a comparison, so one vote implemented backwards changes the trade log
/// without ever producing an impossible number.
#[test]
fn ethusd_confluence_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::EthusdConfluence,
        include_str!("../fixtures/parity_ethusd_confluence.json"),
    );
    assert_reproduces(&fixture);
}

/// Yesterday's RANGE as the breakout unit, on a trailing exit.
#[test]
fn ethusd_volatility_breakout_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::EthusdVolatilityBreakout,
        include_str!("../fixtures/parity_ethusd_volatility_breakout.json"),
    );
    assert_reproduces(&fixture);
}

/// The same shape at a tighter fraction and a FIXED 2R target, which makes it
/// the test that `rr_N` is read from the stop DISTANCE rather than from the
/// daily range. It is also the busiest log in the book at 341 trades.
#[test]
fn jp225_volatility_breakout_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225VolatilityBreakout,
        include_str!("../fixtures/parity_jp225_volatility_breakout.json"),
    );
    assert_reproduces(&fixture);
}

/// A failed break of an 8-bar window's Donchian channel, which walks the level
/// ring BACKWARDS and reads each level AS IT STOOD -- not as it stands now.
#[test]
fn gbpjpy_trap_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::GbpjpyTrap,
        include_str!("../fixtures/parity_gbpjpy_trap.json"),
    );
    assert_reproduces(&fixture);
}

/// THE VOLATILITY RATIO AS A TRIGGER RATHER THAN AS A FILTER, and the only cell
/// that reads both realised-volatility windows for something other than
/// `vol_mode`. `time_4` on top, so it also pins the bar-count exit on a market
/// whose session is fifteen candles long.
#[test]
fn jp225_vol_regime_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225VolRegime,
        include_str!("../fixtures/parity_jp225_vol_regime.json"),
    );
    assert_reproduces(&fixture);
}

/// THREE HORIZONS STANDARDISED OVER FORTY SESSIONS OF CLOSES, read off prefix
/// sums. The deepest history any cell in the book touches, and the one place a
/// variance is recovered by subtraction rather than accumulated -- so a prefix
/// array off by one bar would still produce finite, plausible scores.
#[test]
fn jp225_momentum_stack_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225MomentumStack,
        include_str!("../fixtures/parity_jp225_momentum_stack.json"),
    );
    assert_reproduces(&fixture);
}

/// A 270-BAR PAIRWISE STATISTIC MAINTAINED BY SLIDING. Every bar subtracts the
/// departing element's comparisons and adds the arriving one's, so an error
/// does not show up as a wrong reading on one bar -- it accumulates silently
/// into every reading after it, which is exactly what a 274-trade log catches.
#[test]
fn usdjpy_kendall_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::UsdjpyKendall,
        include_str!("../fixtures/parity_usdjpy_kendall.json"),
    );
    assert_reproduces(&fixture);
}

/// AN EVENT SAMPLER WITH A RESET, so its trades are spaced by market structure
/// rather than by a bar count. A threshold read in the wrong units -- annualised
/// volatility instead of `atr / close` -- is out by about ninety at 30 minutes
/// and fires a couple of dozen times in seven years instead of 211.
#[test]
fn jp225_cusum_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225Cusum,
        include_str!("../fixtures/parity_jp225_cusum.json"),
    );
    assert_reproduces(&fixture);
}

/// A COVARIANCE CARRIED BAR TO BAR WITH NO WINDOW TO FALL OUT OF -- the deepest
/// recursion in the book. The update order matters: `p11` must consume the
/// PRE-update `p01`, and getting it the other way round produces a filter that
/// converges to something plausible and wrong.
#[test]
fn jp225_kalman_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::Jp225Kalman,
        include_str!("../fixtures/parity_jp225_kalman.json"),
    );
    assert_reproduces(&fixture);
}

/// A 64-TAP POWER-LAW KERNEL CONSUMED BACKWARDS, standardised against its own
/// twenty-session window, with the INVERTED `follow` polarity on top. Reading
/// the window the other way round is a smoothing rather than a differencing and
/// still produces a perfectly plausible series.
#[test]
fn usdjpy_fracdiff_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::UsdjpyFracdiff,
        include_str!("../fixtures/parity_usdjpy_fracdiff.json"),
    );
    assert_reproduces(&fixture);
}

/// THE OPENING RANGE READ FROM THE PRECOMPUTED BLOCK, which is the whole
/// difference between this and `orb`: a rule that accumulated its own would
/// measure a short range on every day it was already in a position. The slow
/// RSI gate at `oppose` is the second half.
#[test]
fn eurjpy_gated_orb_reproduces_the_python_trade_log() {
    let fixture = load(
        Sleeve::EurjpyGatedOrb,
        include_str!("../fixtures/parity_eurjpy_gated_orb.json"),
    );
    assert_reproduces(&fixture);
}

/// Prints both trade lists side by side. Ignored by default; run it with
/// `--ignored --nocapture` and edit the index to point at a failing fixture.
#[test]
#[ignore = "a diagnostic, not an assertion"]
fn dump() {
    let (sleeve, raw) = ALL[0];
    let fixture = load(sleeve, raw);
    let replayed = replay(&fixture);
    println!("RUST {}", replayed.len());
    for (entry, exit) in &replayed {
        println!("R {} {} {:.4} {}", entry.ts, entry.side, entry.price, exit);
    }
    println!("PY {}", fixture.trades.len());
    for trade in &fixture.trades {
        println!(
            "P {} {} {:.4} {}",
            trade["entry_ts"],
            trade["side"],
            trade["entry"].as_f64().unwrap(),
            trade["exit_ts"]
        );
    }
}

/// Every fixture, so `dump` can be pointed at any of them.
///
/// One entry per SLEEVE, which since 2026-09-04 is the whole book --
/// `every_sleeve_is_covered_by_a_parity_fixture` is what keeps the two lists
/// from drifting apart.
const ALL: [(Sleeve, &str); 22] = [
    (
        Sleeve::UsdjpyVolumeThrust,
        include_str!("../fixtures/parity_usdjpy_volume_thrust.json"),
    ),
    (
        Sleeve::UsdjpyPullback,
        include_str!("../fixtures/parity_usdjpy_pullback.json"),
    ),
    (
        Sleeve::UsdjpyKendall,
        include_str!("../fixtures/parity_usdjpy_kendall.json"),
    ),
    (
        Sleeve::UsdjpyFracdiff,
        include_str!("../fixtures/parity_usdjpy_fracdiff.json"),
    ),
    (
        Sleeve::AudusdZscore,
        include_str!("../fixtures/parity_audusd_zscore.json"),
    ),
    (
        Sleeve::EthusdConfluence,
        include_str!("../fixtures/parity_ethusd_confluence.json"),
    ),
    (
        Sleeve::EthusdVolatilityBreakout,
        include_str!("../fixtures/parity_ethusd_volatility_breakout.json"),
    ),
    (
        Sleeve::EthusdObvBreak,
        include_str!("../fixtures/parity_ethusd_obv_break.json"),
    ),
    (
        Sleeve::EthusdPullback,
        include_str!("../fixtures/parity_ethusd_pullback.json"),
    ),
    (
        Sleeve::GbpjpyTrap,
        include_str!("../fixtures/parity_gbpjpy_trap.json"),
    ),
    (
        Sleeve::UkoilXmaCross,
        include_str!("../fixtures/parity_ukoil_xma_cross.json"),
    ),
    (
        Sleeve::UkoilLevelConfluence,
        include_str!("../fixtures/parity_ukoil_level_confluence.json"),
    ),
    (
        Sleeve::EurjpyTwoStage,
        include_str!("../fixtures/parity_eurjpy_two_stage.json"),
    ),
    (
        Sleeve::EurjpyGatedOrb,
        include_str!("../fixtures/parity_eurjpy_gated_orb.json"),
    ),
    (
        Sleeve::Jp225VolumeThrust,
        include_str!("../fixtures/parity_jp225_volume_thrust.json"),
    ),
    (
        Sleeve::Jp225VolRegime,
        include_str!("../fixtures/parity_jp225_vol_regime.json"),
    ),
    (
        Sleeve::Jp225MomentumStack,
        include_str!("../fixtures/parity_jp225_momentum_stack.json"),
    ),
    (
        Sleeve::Jp225Cusum,
        include_str!("../fixtures/parity_jp225_cusum.json"),
    ),
    (
        Sleeve::Jp225ObvDivergence,
        include_str!("../fixtures/parity_jp225_obv_divergence.json"),
    ),
    (
        Sleeve::Jp225Kalman,
        include_str!("../fixtures/parity_jp225_kalman.json"),
    ),
    (
        Sleeve::EthusdKalman,
        include_str!("../fixtures/parity_ethusd_kalman.json"),
    ),
    (
        Sleeve::UsdjpyHalfLife,
        include_str!("../fixtures/parity_usdjpy_half_life.json"),
    ),
];

/// Every sleeve in the book has a fixture, and every fixture names a sleeve the
/// book still carries.
#[test]
fn every_sleeve_is_covered_by_a_parity_fixture() {
    // DERIVED FROM `ALL` rather than transcribed beside it: two hand-kept lists
    // of the same twenty-two fall out of step, and the one that goes stale is
    // silently the one nothing asserts against.
    let covered: Vec<Sleeve> = ALL.iter().map(|(sleeve, _)| *sleeve).collect();
    for sleeve in BOOK {
        assert!(
            covered.contains(&sleeve),
            "{} has no parity fixture",
            sleeve.display()
        );
    }
    assert_eq!(
        covered.len(),
        BOOK.len(),
        "a fixture names a sleeve the book does not carry"
    );
}

/// EVERY fixture reproduces, not only the ones with a test of their own above.
///
/// The named tests exist to say what each cell covers; this is what makes the
/// coverage exhaustive, so a sleeve seated without its own paragraph is still
/// held to its Python.
#[test]
fn every_fixture_reproduces_the_python_trade_log() {
    for (sleeve, raw) in ALL {
        assert_reproduces(&load(sleeve, raw));
    }
}

/// Fed minute bars, the engine must reach the SAME trades -- and reach them
/// without ever being handed a bar of the following slot.
///
/// Two properties: the aggregation is right, and the early close only parts
/// from the arrival rule where the feed itself has a hole.
///
/// `update_all` closes a candle on its final minute rather than on the arrival
/// of the next slot's bar, which is worth ~60s of live latency. The one input it
/// then cannot read from a bar is `last_of_day`, so it reads that from the
/// session and the clock instead. Wherever the fixture's bars are contiguous the
/// two derivations are the same statement and the trade logs must match to the
/// digit.
///
/// WHERE THEY PART, THEY PART FOR A REASON THAT IS NOT A REGRESSION. A market
/// that stops mid-session and prints nothing more until the next day reads, to
/// the arrival rule, as a day that ended early -- so Python and the 30-minute
/// replay flatten at the hole. Live cannot: at that moment "not published yet"
/// and "never will" are the same observation. The runtime already answers this
/// with `session_overdue`, which flattens on its own clock half an hour past the
/// session close, and that is what closes such a position today too.
///
/// So the assertion is not blanket equality. Each fixture bar that opens such a
/// hole is identified up front, and the two logs must agree everywhere that is
/// not downstream of one.
fn ambiguous_bars(fixture: &Fixture) -> Vec<i64> {
    let (open, close) = fixture.sleeve.session();
    let in_session = |minute: i64| (open..=close).contains(&minute);
    let minute_of = |ts: i64| ts.rem_euclid(86_400) / 60;
    let day_of = |ts: i64| ts.div_euclid(86_400);
    // ON THE SHIFTED CLOCK, which is the only one the session windows mean
    // anything on: JP225's is stated against a day that starts at 19:00 New
    // York. `load` takes the shift back off the fixture, `update_all` puts it
    // on, and this has to sit where `update_all` does or a shifted market would
    // be tested against the wrong session entirely.
    let shift = fixture.shift_seconds;
    fixture
        .bars
        .windows(2)
        .filter_map(|pair| {
            let (bar, next) = (pair[0], pair[1]);
            // Contiguous: the arrival rule and the clock see the same successor.
            if next.ts == bar.ts + BAR_SECONDS {
                return None;
            }
            let (bar_ts, next_ts) = (bar.ts + shift, next.ts + shift);
            let slot_end = bar_ts + BAR_SECONDS;
            // The clock calls this mid-session; the arrival rule, looking at a
            // bar that is a day away, calls it the end of the day.
            let clock_says_last =
                !in_session(minute_of(slot_end)) || day_of(slot_end) != day_of(bar_ts);
            let arrival_says_last = day_of(next_ts) != day_of(bar_ts);
            (!clock_says_last && arrival_says_last).then_some(bar_ts)
        })
        .collect()
}

#[test]
fn minute_bars_reach_the_same_trades() {
    // What the clock-derived `last_of_day` costs, over 22 sleeves and every bar
    // the fixtures carry. Pinned rather than bounded: these are the trades that
    // fall on a feed hole, and if the number moves, the rule has changed and the
    // change has to be argued rather than absorbed.
    //
    // RE-PINNED 2026-09-19 FROM (3, 0, 9), AND THE RULE DID NOT CHANGE -- the
    // FIXTURE SET did. `jp225:break_retest` and `jp225:volatility_breakout`
    // left the book and `ethusd:kalman` and `usdjpy:half_life` took their
    // places, so the bars being counted are not the same bars. The two jp225
    // cells contributed one missing and two moved trades between them on JP225's
    // feed holes; the two arrivals contribute none, ETHUSD having no session
    // boundary to disagree about and USDJPY's holes falling where this cell does
    // not trade. Anything that moves these numbers WITHOUT a membership change
    // is still the rule changing.
    const MISSING: usize = 2;
    const EXTRA: usize = 0;
    const MOVED: usize = 7;

    let (mut clean, mut missing, mut extra, mut moved) = (0, 0, 0, 0);
    for (sleeve, raw) in ALL {
        let fixture = load(sleeve, raw);
        let want = replay(&fixture);
        // NO FLUSH BAR. The early close is what has to end the last candle; if
        // it did not, this stream would lose it and the counts would say so.
        let got = replay_stream(&fixture, &minute_stream(&fixture));
        let holes = ambiguous_bars(&fixture);

        // A hole-free fixture has nothing the two rules can disagree about, so
        // it must reproduce to the digit. Four of the twenty-two are, which is
        // what keeps the aggregation itself under test rather than only the
        // exceptions.
        if holes.is_empty() {
            clean += 1;
            assert_eq!(got.len(), want.len(), "{sleeve:?}: trade count moved");
            for ((ge, gx), (we, wx)) in got.iter().zip(want.iter()) {
                assert!(
                    agrees(ge, we) && gx == wx,
                    "{sleeve:?}: hole-free fixture diverged at {}",
                    we.ts
                );
            }
        }

        // MATCHED ON THE ENTRY, NOT ON POSITION IN THE LIST. One dropped trade
        // shifts every later index, which made a single difference read as 86.
        let gone: Vec<i64> = want
            .iter()
            .filter(|(we, _)| !got.iter().any(|(ge, _)| ge.ts == we.ts))
            .map(|(we, _)| we.ts)
            .collect();
        let added = got
            .iter()
            .filter(|(ge, _)| !want.iter().any(|(we, _)| we.ts == ge.ts))
            .count();
        let shifted: Vec<i64> = want
            .iter()
            .filter(|(we, wx)| {
                got.iter()
                    .any(|(ge, gx)| ge.ts == we.ts && (gx != wx || !agrees(ge, we)))
            })
            .map(|(we, _)| we.ts)
            .collect();

        // EVERY difference must be downstream of a hole. This is the causal
        // claim -- that the clock rule is exact wherever the feed is, and only
        // gives way where nothing could have known the answer.
        for ts in gone.iter().chain(shifted.iter()) {
            assert!(
                holes.iter().any(|hole| *hole <= *ts),
                "{sleeve:?}: trade at {ts} differs with no feed hole before it"
            );
        }

        missing += gone.len();
        extra += added;
        moved += shifted.len();
    }
    assert!(
        clean > 0,
        "no fixture was hole-free, so the equality went untested"
    );
    assert_eq!(
        (missing, extra, moved),
        (MISSING, EXTRA, MOVED),
        "the cost of the clock-derived last_of_day moved"
    );
}
