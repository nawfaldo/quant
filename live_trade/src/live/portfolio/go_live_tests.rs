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
    assert_eq!(slot.entry_refusal(huge, true), None);
}

#[test]
fn an_entry_below_the_broker_minimum_is_still_refused() {
    let slot = test_slot();
    let dust = Action::Enter {
        side: Side::Long,
        price: 100.0,
        quantity: 0.001,
    };
    assert!(slot.entry_refusal(dust, true).is_some());
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
    assert_eq!(slot.entry_refusal(entry, true), None);
    assert_eq!(
        slot.entry_refusal(entry, false),
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
    assert_eq!(slot.entry_refusal(exit, false), None);
}

/// A minute that arrives one poll late is still fresh; a replayed backlog is
/// not. 2026-09-24: `usdjpy_half_life`'s 02:29 minute landed at 02:31:00 with
/// the 02:30 one, 60s after its close, and was refused as "catch-up".
#[test]
fn a_minute_one_poll_late_may_still_enter() {
    let bar = 1_790_000_000 - 1_790_000_000 % 60;
    let close = bar + 60;
    assert!(entry_is_fresh(bar, "usdjpy", close + 60));
    assert!(entry_is_fresh(bar, "usdjpy", close + ENTRY_FRESH_SECONDS));
    assert!(!entry_is_fresh(
        bar,
        "usdjpy",
        close + ENTRY_FRESH_SECONDS + 1
    ));
    assert!(!entry_is_fresh(bar, "usdjpy", close + 3_600));
}
