//! Go-live behaviour: what the runtime refuses to send, and when.

use super::tests::test_slot;
use super::*;

/// There is no upper volume cap. A cap on a fraction-of-equity strategy
/// binds only once the account has grown, and then refuses entries while
/// still allowing exits -- a book that quietly stops opening.
#[test]
fn a_large_entry_is_not_refused_for_its_size() {
    let slot = test_slot();
    let huge = Action::Enter {
        side: Side::Long,
        price: 100.0,
        quantity: 25.0,
    };
    assert_eq!(slot.entry_refusal(huge, Some(0)), None);
}

#[test]
fn an_entry_below_the_broker_minimum_is_still_refused() {
    let slot = test_slot();
    let dust = Action::Enter {
        side: Side::Long,
        price: 100.0,
        quantity: 0.001,
    };
    assert!(slot.entry_refusal(dust, Some(0)).is_some());
}

/// A catch-up bar must not open new risk, and must still allow exits.
#[test]
fn a_stale_catch_up_bar_refuses_entries() {
    let slot = test_slot();
    let entry = Action::Enter {
        side: Side::Long,
        price: 100.0,
        quantity: 0.5,
    };
    assert_eq!(slot.entry_refusal(entry, Some(0)), None);
    assert_eq!(
        slot.entry_refusal(entry, Some(3_600)),
        Some("signal belongs to a stale catch-up bar")
    );
    // No clock at all is not evidence of freshness.
    assert_eq!(
        slot.entry_refusal(entry, None),
        Some("signal belongs to a stale catch-up bar")
    );
}

/// Staleness must not swallow exits: refusing to close is never safer.
#[test]
fn a_stale_signal_never_refuses_an_exit() {
    let slot = test_slot();
    let exit = Action::Close {
        price: 100.0,
        fraction: 1.0,
    };
    assert_eq!(slot.entry_refusal(exit, Some(3_600)), None);
}

/// A minute that arrives one poll late is still fresh; a replayed backlog is
/// not. 2026-09-24: `usdjpy_half_life`'s 02:29 minute landed at 02:31:00 with
/// the 02:30 one, 60s after its close, and was refused as "catch-up".
#[test]
fn a_minute_one_poll_late_may_still_enter() {
    let bar = 1_790_000_000 - 1_790_000_000 % 60;
    let close = bar + 60;
    assert_eq!(entry_age_seconds(bar, "usdjpy", close + 60), 60);
    let slot = test_slot();
    let entry = Action::Enter {
        side: Side::Long,
        price: 100.0,
        quantity: 0.5,
    };
    assert_eq!(slot.entry_refusal(entry, Some(60)), None);
    assert_eq!(slot.entry_refusal(entry, Some(ENTRY_FRESH_SECONDS)), None);
}

/// The fresh window is the three-minute floor or a sixth of the sleeve's OWN
/// candle, whichever is longer: a 5-minute sleeve keeps 180s, a 30-minute one
/// 300s and a 120-minute one 1,200s. 2026-10-01: a USTEC 5-minute entry at 183s
/// was refused, which is still right; the same delay on a 30-minute sleeve is
/// a tenth of its candle and no longer refuses.
#[test]
fn the_fresh_window_scales_with_the_sleeve_s_candle() {
    assert_eq!(
        entry_fresh_limit("ustec_lux_body_momentum_5m"),
        ENTRY_FRESH_SECONDS
    );
    assert_eq!(entry_fresh_limit("usdjpy_volume_thrust"), 300);
    assert_eq!(entry_fresh_limit("gbpjpy_luxalgo_manipulation_120m"), 1_200);
    // An unknown id gets only the floor.
    assert_eq!(entry_fresh_limit("not_a_sleeve"), ENTRY_FRESH_SECONDS);
}

/// THE BUG THIS CLOCK EXISTS TO PREVENT. On 2026-10-01 at 08:38 UTC a restart
/// stepped three weeks of catch-up bars through the live loop; the old clock
/// re-learned New York's offset from each of them, so a bar from 2026-09-08
/// read as seconds old and seven stale signals went to the broker. The offset
/// now comes from the US rule and nothing a bar says can move it.
#[test]
fn new_york_time_follows_the_us_daylight_saving_rule() {
    use crate::backtest::data::{iso_day, new_york_offset_seconds};
    let at = |day: &str, hour: i64| iso_day(day).unwrap() * 86_400 + hour * 3_600;
    // 2026: DST from Sunday 8 March 07:00 UTC to Sunday 1 November 06:00 UTC.
    assert_eq!(new_york_offset_seconds(at("2026-03-08", 6)), -5 * 3_600);
    assert_eq!(new_york_offset_seconds(at("2026-03-08", 7)), -4 * 3_600);
    assert_eq!(new_york_offset_seconds(at("2026-10-03", 12)), -4 * 3_600);
    assert_eq!(new_york_offset_seconds(at("2026-11-01", 5)), -4 * 3_600);
    assert_eq!(new_york_offset_seconds(at("2026-11-01", 6)), -5 * 3_600);
    assert_eq!(new_york_offset_seconds(at("2026-12-25", 12)), -5 * 3_600);
    // 2025: 9 March and 2 November.
    assert_eq!(new_york_offset_seconds(at("2025-03-09", 7)), -4 * 3_600);
    assert_eq!(new_york_offset_seconds(at("2025-11-02", 6)), -5 * 3_600);

    // And a three-week-old bar is three weeks old, not fresh.
    let now = at("2026-10-01", 8) + 38 * 60;
    let store_now = now + new_york_offset_seconds(now);
    let old_bar = at("2026-09-08", 10) + 49 * 60;
    assert!(entry_age_seconds(old_bar, "usoil", store_now) > 20 * 86_400);
}
