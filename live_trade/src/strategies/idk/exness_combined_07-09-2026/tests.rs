use super::*;

fn candle(ts: i64, open: f64, high: f64, low: f64, close: f64, volume: f64) -> Candle {
    Candle {
        ts,
        open,
        high,
        low,
        close,
        volume,
    }
}

/// The book is the twenty-two members `exness_combined_strategies.BOOK` records,
/// in that order. A silent reordering would be invisible in every aggregate number
/// this file produces -- and the ORDER is load-bearing beyond documentation:
/// `replay` offers pending entries in `python_key` order, so a book that offers
/// them differently refuses a different set whenever a cap is on.
#[test]
fn the_book_is_the_twenty_one_sealed_members() {
    let ids: Vec<&str> = BOOK.iter().map(|sleeve| sleeve.id()).collect();
    assert_eq!(
        ids,
        vec![
            "usdjpy_volume_thrust",
            "audusd_zscore",
            "ethusd_confluence",
            "ethusd_volatility_breakout",
            "gbpjpy_trap",
            "eurjpy_two_stage",
            "usdjpy_pullback",
            "ethusd_obv_break",
            "ukoil_level_confluence",
            "eurjpy_gated_orb",
            "usdjpy_aroon",
            "ethusd_break_retest",
            "usdjpy_fracdiff",
            "ethusd_pullback",
            "ethusd_kalman",
            "usdjpy_half_life",
            "ethusd_roofing",
            "ethusd_level_confluence",
            "usdjpy_rvol",
            "ethusd_efficiency",
            "ethusd_cci",
            "ethusd_linreg_trend",
        ]
    );
}

/// A run of ALL TWENTY-TWO is the book and is recorded as the book; anything
/// less is a research selection and must not be filed under its id.
///
/// The distinction is the whole value of the name. A saved row that says
/// `exness_combined_07-09-2026` has to mean this exact selection, or the record
/// stops being evidence about anything -- a twenty-four-sleeve experiment filed
/// under the book's id is indistinguishable from the book a year later.
#[test]
fn only_the_whole_book_is_the_book() {
    let names: Vec<&str> = BOOK.into_iter().map(Sleeve::display).collect();
    assert!(is_whole_book(names.iter().copied()));

    // Order is not part of it: ticking twenty-two boxes selects the book
    // whatever sequence they come back in.
    let mut shuffled = names.clone();
    shuffled.reverse();
    assert!(is_whole_book(shuffled.iter().copied()));

    // The snake_case ids name the same twenty-two.
    let ids: Vec<&str> = BOOK.into_iter().map(Sleeve::id).collect();
    assert!(is_whole_book(ids.iter().copied()));

    // One short, one repeated, one stranger, and nothing at all.
    assert!(!is_whole_book(names[1..].iter().copied()));
    let mut doubled = names[1..].to_vec();
    doubled.push(names[1]);
    assert!(!is_whole_book(doubled.iter().copied()));
    let mut stranger = names.clone();
    stranger.push("BTC Maroy Ladder");
    assert!(!is_whole_book(stranger.iter().copied()));
    assert!(!is_whole_book(std::iter::empty()));
}

/// The book's id matches the directory it is sealed in, so a stored run and the
/// code that produced it carry the same name.
#[test]
fn the_book_is_named_after_its_own_directory() {
    assert_eq!(BOOK_ID, "exness_combined_07-09-2026");
    assert_eq!(BOOK_NAME, "Exness Combined 07-09-2026");
    // It must not collide with a sleeve, in either spelling: the registry lists
    // all twenty-two and a collision would make one unreachable.
    assert!(Sleeve::from_id(BOOK_ID).is_none());
    assert!(Sleeve::from_display(BOOK_NAME).is_none());
}

/// Round-tripping both spellings, because the API keys on the id and the
/// engine keys on the display name.
#[test]
fn every_sleeve_resolves_from_both_of_its_names() {
    for sleeve in BOOK {
        assert_eq!(Sleeve::from_id(sleeve.id()), Some(sleeve));
        assert_eq!(Sleeve::from_display(sleeve.display()), Some(sleeve));
    }
}

/// A sleeve wears three names -- a display name for the API, a snake_case id
/// for the live account, and a stored code for the database -- and all three
/// must be distinct per sleeve and unique across the book. Two sleeves
/// sharing a stored code would merge their saved runs.
#[test]
fn all_three_spellings_are_unique_and_agree() {
    let mut codes: Vec<&str> = BOOK.iter().map(|sleeve| sleeve.code()).collect();
    let total = codes.len();
    codes.sort_unstable();
    codes.dedup();
    assert_eq!(codes.len(), total, "two sleeves share a stored code");

    for sleeve in BOOK {
        assert_eq!(
            sleeve.code(),
            sleeve.id().to_ascii_uppercase(),
            "{} stores under a code its id does not produce",
            sleeve.display()
        );
    }
}

/// A name the book does not carry must resolve to nothing, in every
/// spelling. `market_symbol` still falls through to "nq" for an unregistered
/// display name, so anything that prices or routes a strategy has to ask
/// `Sleeve` rather than that helper.
#[test]
fn a_retired_strategy_resolves_to_nothing() {
    assert_eq!(Sleeve::from_display("NQ Hourly Delta Reversal"), None);
    assert_eq!(Sleeve::from_display("BTC Maroy Ladder"), None);
    assert_eq!(Sleeve::from_id("ofi_momentum"), None);
    assert_eq!(Sleeve::from_id("xalusd_pdr"), None);
}

/// `market_symbol` routes by the display name's FIRST WORD, so a name whose
/// prefix disagrees with `market()` would be stepped with another market's
/// prices -- the failure that cost 800 dollars at an 88% drawdown while the
/// standalone backtest looked fine.
#[test]
fn every_display_name_declares_its_own_market() {
    for sleeve in BOOK {
        let declared = sleeve
            .display()
            .split_whitespace()
            .next()
            .unwrap()
            .to_ascii_lowercase();
        assert_eq!(
            declared,
            sleeve.market(),
            "{} declares the wrong market",
            sleeve.display()
        );
    }
}

/// `per_session` is `max(2, (close - open) / 30 + 1)`, and every lookback in
/// the study is a multiple of it. A wrong value here silently retunes all
/// twenty-two cells at once.
///
/// ONE ROW PER MARKET, which is what the book has seven of.
#[test]
fn sessions_and_bucket_counts_match_the_python() {
    let expected: [(Sleeve, (i64, i64), usize); 7] = [
        (Sleeve::UsdjpyVolumeThrust, (0, 780), 27),
        (Sleeve::AudusdZscore, (60, 840), 27),
        (Sleeve::Jp225Cusum, (60, 480), 15),
        (Sleeve::EthusdConfluence, (570, 960), 14),
        (Sleeve::GbpjpyTrap, (0, 810), 28),
        (Sleeve::UkoilXmaCross, (540, 870), 12),
        (Sleeve::EurjpyTwoStage, (60, 870), 28),
    ];
    for (sleeve, session, per_session) in expected {
        let spec = sleeve.contract();
        assert_eq!(spec.session, session, "{}", sleeve.display());
        assert_eq!(spec.per_session, per_session, "{}", sleeve.display());
        let derived = (2usize).max(
            ((spec.session.1 - spec.session.0) / 30 + 1)
                .try_into()
                .unwrap(),
        );
        assert_eq!(derived, per_session, "{}", sleeve.display());
    }
}

/// The cross-check that these sessions are the ones the cells were fitted
/// on: `axes` can only offer `{close - 120, close - 60}` as a cutoff, so a
/// sealed `last_entry_minute` outside that pair means the window is wrong.
#[test]
fn every_entry_cutoff_is_reachable_from_its_session() {
    for sleeve in BOOK {
        let Some(params) = sleeve.params() else {
            continue;
        };
        // `swing_ma` is the one member whose Python has NO cutoff axis at all
        // -- the swing grid carries none -- so there is nothing to have been
        // reachable. Its signal never reads the field.
        if matches!(params.family, super::family::Family::SwingMa { .. }) {
            continue;
        }
        let close = sleeve.contract().session.1;
        assert!(
            params.last_entry_minute == close - 60 || params.last_entry_minute == close - 120,
            "{} cuts off at {} against a {} close",
            sleeve.display(),
            params.last_entry_minute,
            close
        );
    }
}

/// EVERY MEMBER IS A SESSION CELL WITH AN ENTRY CUTOFF, and none is scaled.
///
/// `family_hold` is "session" for all twenty-two families the book selects, so
/// the flatten in `on_candle` is unconditional and Python's `swing` branch is
/// false whatever `ef.SESSION_ONLY` says. That is what this asserts, via the
/// cutoff: a swing or overnight cell carries no `last_entry_minute` axis at all,
/// so one appearing past its own session close would be the tell.
///
/// The scale went with it. `jp225:swing_donchian` was the only boosted sleeve
/// and it left the book on 2026-08-29 -- its 1.6 was bought for DRAWDOWN, not
/// return ([[swing-donchian-is-load-bearing-for-drawdown]]) -- so a non-unit
/// scale appearing here now would be a transposition rather than a decision.
#[test]
fn every_member_is_a_session_cell_and_none_is_scaled() {
    for sleeve in BOOK {
        let Some(params) = sleeve.params() else {
            continue;
        };
        assert!(
            params.last_entry_minute <= sleeve.contract().session.1,
            "{} has no reachable entry cutoff, so it is not a session cell",
            sleeve.display()
        );
    }
    let scaled: Vec<&str> = BOOK
        .into_iter()
        .filter(|sleeve| sleeve.scale() != 1.0)
        .map(Sleeve::display)
        .collect();
    assert!(scaled.is_empty(), "{scaled:?}");
}

/// `rolling_extreme` excludes the bar it is asked about and answers from bar
/// ONE with a partial window. Both halves matter: a channel that counted the
/// current bar could never be broken, and one that refused during warm-up
/// would change which bars the cells trade.
#[test]
fn the_channel_excludes_the_current_bar_and_warms_partially() {
    let mut channel = RollingExtreme::new(3, true);
    assert_eq!(channel.value(), None, "nothing precedes the first bar");
    channel.push(10.0);
    assert_eq!(
        channel.value(),
        Some(10.0),
        "a partial window still answers"
    );
    channel.push(30.0);
    assert_eq!(channel.value(), Some(30.0));
    channel.push(20.0);
    channel.push(5.0);
    assert_eq!(channel.value(), Some(30.0));
    // 30.0 now falls out of the three-bar window.
    channel.push(1.0);
    assert_eq!(channel.value(), Some(20.0));
}

/// `daily_risk` reads only COMPLETED days, and republishes on the rollover.
/// Folding the day being traded into the size of the risk taken on it is the
/// most flattering bug this file could carry.
#[test]
fn the_daily_range_never_reads_the_day_it_sizes() {
    let mut risk = DailyRisk::new();
    for day in 0..DAILY_RANGE_DAYS as i64 {
        let ts = day * 86_400;
        risk.push(&candle(ts, 100.0, 110.0, 90.0, 100.0, 1.0), day);
        assert_eq!(risk.value(), None, "still short of a full window");
    }
    // The fourteenth day completes only when the fifteenth opens.
    let day = DAILY_RANGE_DAYS as i64;
    risk.push(&candle(day * 86_400, 100.0, 500.0, 0.0, 100.0, 1.0), day);
    assert_eq!(risk.value(), Some(20.0), "fourteen 20-point days");
    // The 500-point day is only visible from the day after it.
    risk.push(
        &candle((day + 1) * 86_400, 100.0, 110.0, 90.0, 100.0, 1.0),
        day + 1,
    );
    assert!(risk.value().is_some_and(|value| value > 20.0));
}

/// The floor pivot is the PRIOR day's `(H+L+C)/3`, with R1 and S1 reflected
/// around it. Reading the current day's would be a level built from the bar
/// it is meant to judge.
#[test]
fn floor_pivots_reflect_the_previous_day() {
    let mut prior = PriorDay::new();
    prior.push(&candle(0, 100.0, 120.0, 80.0, 110.0, 1.0), 0);
    assert!(prior.pivots.is_none(), "no previous day yet");
    prior.push(&candle(86_400, 110.0, 115.0, 105.0, 112.0, 1.0), 1);
    let (pivot, r1, s1, r2, s2) = prior.pivots.unwrap();
    // (120 + 80 + 110) / 3
    assert!((pivot - 103.333_333_333_333_33).abs() < 1e-9);
    assert!((r1 - (2.0 * pivot - 80.0)).abs() < 1e-9);
    assert!((s1 - (2.0 * pivot - 120.0)).abs() < 1e-9);
    assert!((r2 - (pivot + 40.0)).abs() < 1e-9);
    assert!((s2 - (pivot - 40.0)).abs() < 1e-9);
    assert_eq!(prior.range, Some((120.0, 80.0)));
}

/// The throttle is TWO SIDED. The old `min(1, target/realized)` could only
/// cut risk, so in a calm regime every sleeve sat at full size and the book's
/// risk was whatever the raw fraction happened to produce.
#[test]
fn the_volatility_multiplier_can_size_up_as_well_as_down() {
    let mut calm = VolatilityMultiplier::new();
    for day in 0..VOL_MIN_DAYS as i64 + 5 {
        // A hair of movement, so the variance is positive but tiny.
        let close = 100.0 + (day % 2) as f64 * 0.001;
        calm.push(close, true);
    }
    assert_eq!(
        calm.value(),
        VOL_MAX_MULTIPLIER,
        "a dead-calm market is capped, not held at 1.0"
    );

    let mut violent = VolatilityMultiplier::new();
    for day in 0..VOL_MIN_DAYS as i64 + 5 {
        let close = 100.0 * if day % 2 == 0 { 1.0 } else { 1.10 };
        violent.push(close, true);
    }
    assert!(violent.value() < 1.0, "a violent market sizes down");
}

/// The warm-up sits at base size rather than refusing, which is what
/// `daily_multipliers` does and is why an early trade is not silently
/// skipped.
#[test]
fn the_volatility_multiplier_warms_up_at_full_size() {
    let mut throttle = VolatilityMultiplier::new();
    for day in 0..VOL_MIN_DAYS as i64 - 1 {
        throttle.push(100.0 + day as f64, true);
    }
    assert_eq!(throttle.value(), 1.0);
}

/// A MARGIN refusal is the broker's and survives `FORCE_MINIMUM_LOT`.
///
/// This is the mechanism that dropped `de40:floor_pivot` from the book on
/// 2026-08-23: DE40's `volume_min` is 0.07 lots against a ~26,473 index
/// quoted in euros, so one minimum lot needs about $536 of margin and a $400
/// account cannot place it at any risk setting
/// ([[shown-equity-cannot-fix-a-margin-refusal]]).
///
/// ETHUSD is the surviving sleeve that comes closest: its floor is 0.10 lots
/// against a $469-per-lot margin, so one minimum order needs about $47.
#[test]
fn a_margin_refusal_survives_force_minimum_lot() {
    let engine = FamilyEngine::new(Sleeve::EthusdConfluence, 0.01);
    assert_eq!(engine.spec.volume_min, 0.1);
    assert!(
        engine.quantity(60.0, 1_878.0, 5_000.0).is_some(),
        "$60 margins one minimum ETHUSD lot"
    );
    assert_eq!(
        engine.quantity(40.0, 1_878.0, 5_000.0),
        None,
        "$40 does not, and rounding up must not rescue it"
    );
}

/// JP225's floor is THREE whole lots, not a hundredth of one -- and it is
/// still affordable, because the index is quoted in yen: one lot costs
/// `68,720 * 0.006277 * 0.25` = $107.85 of margin, so $400 carries 3.7.
///
/// Worth pinning because "volume_min 3.0" reads like an obvious refusal and
/// is not one; reading it that way is how a real sleeve gets dropped from
/// the book for a constraint that does not bind.
#[test]
fn the_jp225_three_lot_floor_is_affordable_on_four_hundred_dollars() {
    let engine = FamilyEngine::new(Sleeve::Jp225VolumeThrust, 0.01);
    assert_eq!(engine.spec.volume_min, 3.0);
    let lots = engine
        .quantity(CANON_INITIAL, 68_720.0, 500.0)
        .expect("three JP225 lots fit inside a $400 margin ceiling");
    assert!((lots - 3.0).abs() < 1e-12, "{lots} lots");
}

/// Where margin CAN afford the floor, a positive request is rounded up to it
/// rather than dropped -- a dropped trade means the backtest and the live
/// account are running different strategies.
#[test]
fn a_tiny_request_is_rounded_up_to_the_broker_minimum() {
    let engine = FamilyEngine::new(Sleeve::EthusdConfluence, 0.01);
    assert_eq!(engine.spec.volume_min, 0.1);
    // A stop so wide the risk leg asks for a hair of a lot, on an account
    // that can easily margin one minimum ETHUSD lot.
    let lots = engine
        .quantity(10_000.0, 1_878.0, 5_000.0)
        .expect("forced up to the floor");
    assert!((lots - 0.1).abs() < 1e-12, "{lots} lots");
}

/// The margin ceiling reads REAL equity, not the capped sizing figure:
/// margin is a broker constraint on the account and does not shrink because
/// the book chose to bet less.
#[test]
fn the_margin_ceiling_binds_before_the_risk_request_does() {
    let engine = FamilyEngine::new(Sleeve::UkoilXmaCross, 0.01);
    // A stop so tight the risk leg would ask for an absurd size; the ceiling
    // is what actually decides.
    let lots = engine.quantity(10_000.0, 87.22, 0.000_1).unwrap();
    let margin_per_lot = 87.22 * 1_000.0 * 1.0 * MARGIN_FRACTION;
    assert!(
        lots <= 10_000.0 / margin_per_lot + 1e-9,
        "{lots} lots exceeds the margin ceiling"
    );
}

/// The canon run is UNCAPPED, so a sleeve's request grows with the balance.
/// This is the assertion that would fail first if a sizing ceiling were
/// reintroduced without saying so.
#[test]
fn the_canon_book_sizes_against_the_whole_balance() {
    let engine = FamilyEngine::new(Sleeve::AudusdZscore, 0.01);
    let small = engine.quantity(1_500.0, 0.708, 0.005).unwrap();
    let large = engine.quantity(15_000.0, 0.708, 0.005).unwrap();
    assert!(large > small, "{small} did not grow into {large}");
}

/// `SHOWN_EQUITY` scales the risk REQUEST and nothing else. The three
/// sleeves that carry one are the ETHUSD pair and the JP225 thrust.
#[test]
fn only_two_sleeves_are_shown_a_larger_balance() {
    let shown: Vec<(&str, f64)> = BOOK
        .into_iter()
        .filter(|sleeve| sleeve.shown_equity() != 1.0)
        .map(|sleeve| (sleeve.display(), sleeve.shown_equity()))
        .collect();
    assert_eq!(
        shown,
        vec![
            ("ETHUSD Confluence", 3.0),
            ("ETHUSD Volatility Breakout", 3.0),
        ]
    );
}

/// `level_confluence` fires only where the two level systems AGREE, and it is
/// the disagreement case that carries the hypothesis: R1 alone is
/// `floor_pivot`'s trade and yesterday's high alone is `pdr`'s, so a bar that
/// tags one of them and not the other must produce nothing here.
#[test]
fn level_confluence_needs_both_systems_at_the_same_price() {
    // `ukoil` reads `pdr_vwap`, the only pair the book reaches since
    // `hk50:level_confluence` left on 2026-09-07. `pivot_pdr` is exercised here
    // instead because both of ITS legs are STATIC, so the cluster can be placed
    // by hand -- a VWAP leg would have to be accumulated through a session first.
    let mut engine = FamilyEngine::new(Sleeve::UkoilLevelConfluence, 0.01);
    for _ in 0..2 * engine.spec.per_session {
        engine
            .atr
            .push(&candle(0, 20_000.0, 20_010.0, 19_990.0, 20_000.0, 1.0));
    }
    let atr = engine.atr.value().unwrap();
    let edge = 0.5 * atr;

    // R1 at 20,100 and yesterday's high one tenth of an ATR away: a cluster.
    engine.prior.pivots = Some((20_000.0, 20_100.0, 19_900.0, 20_200.0, 19_800.0));
    engine.prior.range = Some((20_100.0 + 0.1 * atr, 19_900.0));
    let cluster = 0.5 * (20_100.0 + 20_100.0 + 0.1 * atr);
    // Tagged the cluster and closed back below it: the `fade` cell sells it.
    let reject = candle(
        0,
        cluster - 1.0,
        cluster + 1.0,
        cluster - 2.0,
        cluster - edge - 1.0,
        1.0,
    );
    // `fade` is the polarity that SELLS a tagged-and-rejected cluster; the
    // sealed cell is `follow`, so it is passed explicitly rather than read off
    // the sleeve.
    assert_eq!(
        engine.params.direction,
        Direction::Follow,
        "ukoil:level_confluence follows its cluster"
    );
    engine.params.direction = Direction::Fade;
    assert_eq!(
        engine.level_confluence(&reject, 300, LevelPair::PivotPdr, 0.5),
        Some(Side::Short)
    );

    // The SAME bar with the two systems a full ATR apart is not a cluster, and
    // `floor_pivot` would still have traded it.
    engine.prior.range = Some((20_100.0 + 2.0 * atr, 19_900.0));
    assert_eq!(
        engine.level_confluence(&reject, 300, LevelPair::PivotPdr, 0.5),
        None
    );
}

/// `zscore` is written the other way round from most families too: its raw
/// side is already the fade, so the sealed `fade` cell takes it unchanged.
#[test]
fn the_zscore_fade_sells_the_stretch() {
    let mut engine = FamilyEngine::new(Sleeve::AudusdZscore, 0.01);
    for _ in 0..2 * engine.spec.per_session {
        engine.atr.push(&candle(0, 0.70, 0.701, 0.699, 0.70, 1.0));
    }
    let stats = engine.zscore.as_mut().unwrap();
    for index in 0..5 * 27 {
        // A flat series with one step, so the last close sits far above the
        // mean.
        stats.push(if index < 5 * 27 - 1 { 0.70 } else { 0.75 });
    }
    let stretched = candle(0, 0.75, 0.75, 0.75, 0.75, 1.0);
    assert_eq!(
        engine.zscore_signal(&stretched, 600, 1.5),
        Some(Side::Short)
    );
}

/// `trap` needs three READABLE ballots' worth of history to say anything,
/// and `confluence` needs three of five. Both refuse rather than voting with
/// what they have.
#[test]
fn confluence_refuses_a_half_degraded_pool() {
    let engine = FamilyEngine::new(Sleeve::EthusdConfluence, 0.01);
    // Nothing is warmed: the RSI, the z-score and the ATR all refuse, which
    // leaves at most two readable ballots.
    let bar = candle(0, 1_800.0, 1_810.0, 1_790.0, 1_800.0, 10.0);
    assert_eq!(engine.confluence(&bar, 600, 3), None);
}

/// A signal read on bar `i` fills at bar `i + 1`'s OPEN, and never at its
/// own close. Consuming the same bar's close manufactures a fake edge of
/// about twelve points a trade.
#[test]
fn a_signal_never_fills_on_its_own_candle() {
    let mut engine = FamilyEngine::new(Sleeve::EthusdObvBreak, 0.01);
    engine.pending = Some(Pending {
        side: Side::Long,
        day: 0,
        distance: 0.005,
        realized: 0.0,
    });
    // The pending slot is consumed by `on_candle`, which is only ever
    // reached from the NEXT candle.
    assert!(engine.position.is_none());
    assert!(engine.pending.is_some());
}

/// The strategy owns its own session flatten, so the engine's generic one
/// must stay out of the way -- running both cuts every target short by one
/// bar at the wrong price.
#[test]
fn family_sleeves_flatten_themselves() {
    for sleeve in BOOK {
        let engine = FamilyEngine::new(sleeve, 0.01);
        assert!(engine.flattens_itself(), "{}", sleeve.display());
    }
}

/// The shifted clock is an internal convenience; the engine's flattener and
/// the live runtime both work in real New York minutes.
#[test]
fn the_shifted_clock_is_converted_back_for_the_engine() {
    let jp225 = FamilyEngine::new(Sleeve::Jp225BreakRetest, 0.01);
    // 08:00 shifted is 02:00 New York.
    assert_eq!(jp225.session_end_minute(), Some(120));
    let unshifted = FamilyEngine::new(Sleeve::GbpjpyTrap, 0.01);
    assert_eq!(unshifted.session_end_minute(), Some(810));
}

/// NO SLEEVE MAY ANSWER `entry_risk_fraction` OR `entry_stop_price`.
///
/// The engine reads that pair as "size this entry for me": when both are
/// present it discards the strategy's own quantity and substitutes
/// `risk_limited_quantity`, which knows nothing about the market's contract
/// spec, the sleeve's scale, its shown equity or the volatility multiplier.
///
/// It fails SILENTLY, which is why this is a test rather than a comment.
/// Implementing them asked for `400 * 0.0031 / 200` = 0.006 JP225 lots,
/// `risk_size` floored that to zero, and the entry was dropped without even
/// reaching the broker-minimum counter -- 204 entries became 1 and the book
/// reported +0.00%, which reads like a strategy with no signals rather than
/// a sizing hook that had eaten them.
#[test]
fn no_sleeve_lets_the_engine_size_its_entries() {
    for sleeve in BOOK {
        let strategy = ExnessCombined::new(sleeve, 0.01);
        assert!(
            strategy.entry_risk_fraction().is_none(),
            "{} would hand its sizing to the engine",
            sleeve.display()
        );
        assert!(
            strategy.entry_stop_price().is_none(),
            "{} would hand its sizing to the engine",
            sleeve.display()
        );
    }
}

/// Every sleeve must build. `params()` panics for a market with no frozen
/// spec and `FamilyEngine::new` panics for a sleeve with no cell, so this is
/// the guard that a new member cannot be half-registered.
#[test]
fn every_member_of_the_book_constructs() {
    for sleeve in BOOK {
        let strategy = ExnessCombined::new(sleeve, 0.01);
        assert_eq!(strategy.sleeve(), sleeve);
    }
}

/// THE TRANSPOSITION MUST NOT HAVE MOVED A NUMBER.
///
/// Each sleeve's definition used to be an arm in ten separate matches and is now
/// one record in `sleeves/<id>.rs`. That rewrite is exactly the kind that
/// silently swaps two neighbours' values, so pin the identity fields against the
/// spelling rules they are meant to follow, and pin every sleeve's contract to
/// the market it declares.
#[test]
fn every_sleeve_spec_is_internally_consistent() {
    for sleeve in BOOK {
        let spec = sleeve.spec();
        // `id` is the Python key with its colon replaced.
        assert_eq!(spec.id, spec.python_key.replace(':', "_"), "{}", spec.id);
        // `code` is the uppercase of `id`.
        assert_eq!(spec.code, spec.id.to_uppercase(), "{}", spec.id);
        // The Python key names the market it trades.
        assert_eq!(
            spec.python_key.split(':').next(),
            Some(spec.market),
            "{} trades a market its key does not name",
            spec.id
        );
        // The display name's first word is the market, upper-cased.
        assert_eq!(
            spec.display.split(' ').next().map(str::to_lowercase),
            Some(spec.market.to_owned()),
            "{} is displayed under another market",
            spec.id
        );
        // Every sleeve on a market sizes against that market's frozen spec.
        // The engine adds a 0.2 bp slippage allowance to the sealed spread, so the
        // two agree to within that constant rather than exactly.
        let charged = crate::backtest::engine::costs::market_spread_bp(spec.market).unwrap();
        assert!(
            (charged - spec.contract.spread_bp - 0.2).abs() < 1e-9,
            "{} carries another market's contract",
            spec.id
        );
        // Every member is a family cell, so every one carries parameters.
        assert!(spec.params().is_some(), "{} is not a family cell", spec.id);
    }
}

/// The values that are NOT 1.0 are the whole risk configuration of the book, and
/// each was chosen for a reason recorded on `SleeveSpec`. Pin them by name so a
/// transposition cannot quietly hand one sleeve another's boost.
#[test]
fn only_the_named_sleeves_carry_a_scale_or_a_shown_equity() {
    for sleeve in BOOK {
        let spec = sleeve.spec();
        let expected_shown = match sleeve {
            Sleeve::EthusdConfluence | Sleeve::EthusdVolatilityBreakout => 3.0,
            Sleeve::Jp225VolumeThrust => 2.0,
            _ => 1.0,
        };
        // `SLEEVE_SCALE` names no current member, so every sleeve is at unit
        // scale and a value here would be a transposition rather than a
        // decision.
        assert_eq!(spec.scale, 1.0, "{} scale", spec.id);
        assert_eq!(
            spec.shown_equity, expected_shown,
            "{} shown equity",
            spec.id
        );
        // `EXTERNAL` reaches no market this book trades, so every sleeve is
        // sized as a cell and pays the broker's `volume_min`.
        assert!(!spec.sized_as_import, "{} import sizing", spec.id);
    }
}

/// A live row's book is DERIVED from the strategy it names, never supplied.
///
/// `create_account_strategy` is reached from a form where the strategy is typed,
/// so a book taken from the request would be a second free-text field to get
/// wrong -- and a row claiming to belong to this book while naming something
/// else is worse than no label at all.
#[test]
fn a_live_row_takes_its_book_from_its_own_strategy() {
    for sleeve in BOOK {
        assert_eq!(book_of(sleeve.id()), BOOK_ID, "{}", sleeve.display());
    }
    // A retired id belongs to no book. Empty means "not recorded", which is
    // also what a row written before the column existed holds -- it must never
    // read as "belongs to the current book".
    assert_eq!(book_of("ofi_momentum"), "");
    assert_eq!(book_of("btc_maroy_ladder"), "");
    assert_eq!(book_of(""), "");
    // The book's own id is not a sleeve and so is not a live row either.
    assert_eq!(book_of(BOOK_ID), "");
}

/// What the 2026-09-19 swap did to warm-up, per market.
///
/// A SEATED SLEEVE THAT DEEPENS ITS MARKET'S REQUIREMENT LENGTHENS EVERY COLD
/// START, because `WarmupPlan` collapses a market's sleeves onto the deepest of
/// them. `usdjpy:half_life` asks for two windows in series and looks expensive;
/// it is not, because `usdjpy:fracdiff` already asks for more. Pinned so a
/// future re-fit that DOES deepen a market has to say so.
#[test]
fn the_swap_did_not_deepen_any_market_s_warmup() {
    let deepest = |market: &str| {
        BOOK.iter()
            .filter(|sleeve| sleeve.market() == market)
            .map(|sleeve| sleeve.warmup_sessions())
            .max()
            .unwrap()
    };
    let usdjpy = Sleeve::from_id("usdjpy_half_life")
        .unwrap()
        .warmup_sessions();
    let ethusd = Sleeve::from_id("ethusd_kalman").unwrap().warmup_sessions();
    assert!(
        usdjpy <= deepest("usdjpy"),
        "usdjpy:half_life at {usdjpy} now sets usdjpy's warm-up ({})",
        deepest("usdjpy")
    );
    assert!(
        ethusd <= deepest("ethusd"),
        "ethusd:kalman at {ethusd} now sets ethusd's warm-up ({})",
        deepest("ethusd")
    );
}
